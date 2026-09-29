"""Standalone EE runtime: audio (or a ready result) in, motor vibration out.

Use :mod:`laptop.ios_motor_bridge` for the normal setup, where ``meit-ios``
already ran ``meit-ai``.  This module is for the two cases that one cannot
cover:

* **Bench replay.**  ``--wav horn.wav --direction back`` runs the real model on
  a file and plays the resulting pattern.  No iPhones, no Wi-Fi, no acoustics.
* **AI-less iOS bridge.**  If the iOS side is run without ``meit-ai`` and hands
  EE raw audio plus a direction, this process owns inference instead.

Ingest API (all paths POST unless noted)::

    POST /ee/audio     body = PCM16LE mono 16 kHz (2.5 s ideally; padded/trimmed)
                       X-MEIT-Direction: front|right|back|left
                       X-MEIT-Event-Id:  optional, for log correlation
                    -> runs meit-ai, then vibrates
    POST /ee/event     body = {"direction": "...", "label": "...",
                               "confidence": 0.0, "danger": true,
                               "event_id": "optional"}
                    -> skips inference, vibrates directly
    POST /ee/stop      cancel any pattern still running
    GET  /ee/health    counters, belt connection state, loaded model

HTTP threads never touch BLE.  They validate, run inference, build the command
and hand it to an :class:`asyncio.Queue`; the single BLE session task is the
only writer to the GATT characteristic.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import threading
import time
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional, Tuple

from laptop.ai_runner import (
    PAYLOAD_BYTES,
    AIError,
    AIResult,
    build_runner,
    load_wav_clip,
)
from laptop.belt_client import BeltLink, add_belt_arguments, belt_from_arguments
from laptop.haptic import (
    DEFAULT_PROFILE,
    HapticProfile,
    build_command,
    with_intensity_scale,
)
from laptop.protocol import (
    DIR_STOP,
    STOP_COMMAND,
    MotorCommand,
    direction_name,
    normalize_direction,
)

LOGGER = logging.getLogger("meit.aibridge")

# Reject anything much larger than one clip so a stray upload cannot exhaust
# memory; the runner pads or trims to exactly one clip anyway.
MAX_BODY_BYTES = PAYLOAD_BYTES * 2
QUEUE_DEPTH = 4


@dataclass
class Counters:
    received: int = 0
    rejected: int = 0
    suppressed: int = 0
    cooldown: int = 0
    dropped: int = 0
    delivered: int = 0
    inference_failed: int = 0

    def as_dict(self) -> Dict[str, int]:
        return dict(vars(self))


class CommandSink:
    """Thread-safe handoff from HTTP worker threads to the BLE session task.

    The queue is bounded and drops the *oldest* pending command when full: if
    several hazards arrive faster than the belt can play them, the newest is the
    one the wearer needs.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop, *, depth: int = QUEUE_DEPTH,
                 cooldown_ms: int = 1500) -> None:
        self.loop = loop
        self.queue: asyncio.Queue[Tuple[MotorCommand, str]] = asyncio.Queue(maxsize=depth)
        self.cooldown = cooldown_ms / 1000.0
        self.counters = Counters()
        self._lock = threading.Lock()
        self._last_accepted = 0.0

    def submit(self, command: MotorCommand, label: str) -> str:
        """Accept a command from any thread. Returns an outcome string."""
        now = time.monotonic()
        with self._lock:
            # A second alert within the cooldown is almost always the same
            # physical event seen again; replaying it would only mask the first
            # pattern part-way through.
            if self.cooldown > 0 and now - self._last_accepted < self.cooldown:
                self.counters.cooldown += 1
                return "cooldown"
            self._last_accepted = now

        def push() -> None:
            if self.queue.full():
                try:
                    self.queue.get_nowait()
                    self.counters.dropped += 1
                except asyncio.QueueEmpty:
                    pass
            self.queue.put_nowait((command, label))

        self.loop.call_soon_threadsafe(push)
        return "queued"

    def submit_stop(self) -> str:
        """Queue an explicit STOP. Never rate-limited: stopping must always work."""
        self.loop.call_soon_threadsafe(
            lambda: self.queue.put_nowait((STOP_COMMAND, "STOP")))
        return "queued"

    async def get(self) -> Tuple[MotorCommand, str]:
        return await self.queue.get()


class Ingest:
    """Validation, inference and haptic mapping, independent of HTTP or BLE."""

    def __init__(self, runner: Any, sink: CommandSink, *,
                 profile: HapticProfile = DEFAULT_PROFILE,
                 intensity_scale: float = 1.0,
                 allow_back: bool = True) -> None:
        self.runner = runner
        self.sink = sink
        self.profile = profile
        self.intensity_scale = intensity_scale
        self.allow_back = allow_back
        # meit-ai holds a TensorFlow session; serialise calls rather than
        # assuming the model is re-entrant across HTTP worker threads.
        self._infer_lock = threading.Lock()

    def _dispatch(self, direction: int, result: AIResult,
                  event_id: Optional[str]) -> Dict[str, Any]:
        tag = (event_id or "-")[:8]
        if not result.danger:
            self.sink.counters.suppressed += 1
            return {"outcome": "suppressed", "reason": "not_dangerous",
                    "label": result.label}

        command = build_command(result.label, result.confidence, direction, self.profile)
        if command is None:
            self.sink.counters.suppressed += 1
            return {"outcome": "suppressed", "reason": "no_pattern_for_result",
                    "label": result.label, "direction": direction_name(direction)}
        if self.intensity_scale != 1.0:
            command = with_intensity_scale(command, self.intensity_scale)

        outcome = self.sink.submit(command, f"{direction_name(direction)}/{result.label}")
        LOGGER.info("event=%s %s/%s conf=%.3f -> %s (%s)", tag,
                    direction_name(direction), result.label, result.confidence,
                    command.describe(), outcome)
        return {"outcome": outcome, "label": result.label,
                "confidence": result.confidence,
                "direction": direction_name(direction),
                "intensity": command.intensity,
                "duration_ms": command.duration_ms}

    def _direction(self, raw: Any) -> int:
        direction = normalize_direction(raw, allow_back=self.allow_back)
        if direction == DIR_STOP:
            raise ValueError(f"direction {raw!r} must be front, right, back or left")
        return direction

    def handle_audio(self, payload: bytes, raw_direction: Any,
                     event_id: Optional[str]) -> Dict[str, Any]:
        direction = self._direction(raw_direction)
        self.sink.counters.received += 1
        with self._infer_lock:
            result = self.runner.infer(payload)
        return self._dispatch(direction, result, event_id)

    def handle_event(self, body: Dict[str, Any]) -> Dict[str, Any]:
        direction = self._direction(body.get("direction"))
        self.sink.counters.received += 1
        try:
            confidence = float(body.get("confidence", 1.0))
        except (TypeError, ValueError):
            confidence = 0.0
        result = AIResult(label=str(body.get("label", "")).strip().lower(),
                          confidence=confidence,
                          danger=body.get("danger") is not False)
        return self._dispatch(direction, result, body.get("event_id"))


class _Handler(BaseHTTPRequestHandler):
    server_version = "meit-ee-ai-bridge"

    @property
    def ingest(self) -> Ingest:
        return self.server.ingest  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:
        LOGGER.debug("%s - %s", self.address_string(), fmt % args)

    def _respond(self, status: HTTPStatus, body: Dict[str, Any]) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _read_body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValueError("Content-Length must be an integer") from None
        if length <= 0:
            raise ValueError("empty request body")
        if length > MAX_BODY_BYTES:
            raise ValueError(f"body exceeds {MAX_BODY_BYTES} bytes")
        return self.rfile.read(length)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path != "/ee/health":
            self._respond(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return
        server = self.server  # type: ignore[assignment]
        self._respond(HTTPStatus.OK, {
            "status": "ok",
            "counters": self.ingest.sink.counters.as_dict(),
            "runner": type(self.ingest.runner).__name__,
            "belt_connected": bool(getattr(server, "belt_connected", False)),
            "cmd_protocol_version": getattr(server, "cmd_protocol_version", None),
        })

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        try:
            if self.path == "/ee/stop":
                outcome = self.ingest.sink.submit_stop()
                self._respond(HTTPStatus.OK, {"outcome": outcome, "command": "stop"})
                return
            if self.path == "/ee/audio":
                body = self._read_body()
                result = self.ingest.handle_audio(
                    body,
                    self.headers.get("X-MEIT-Direction"),
                    self.headers.get("X-MEIT-Event-Id"))
            elif self.path == "/ee/event":
                body = self._read_body()
                result = self.ingest.handle_event(json.loads(body.decode("utf-8")))
            else:
                self._respond(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                return
        except (ValueError, UnicodeDecodeError) as exc:
            self.ingest.sink.counters.rejected += 1
            self._respond(HTTPStatus.BAD_REQUEST, {"error": "invalid_request",
                                                   "detail": str(exc)})
            return
        except AIError as exc:
            self.ingest.sink.counters.inference_failed += 1
            LOGGER.error("inference failed: %s", exc)
            self._respond(HTTPStatus.INTERNAL_SERVER_ERROR,
                          {"error": "inference_failed", "detail": str(exc)})
            return
        self._respond(HTTPStatus.OK, result)


async def _drain(sink: CommandSink, link: BeltLink) -> None:
    """The only writer to the belt: play queued commands one at a time."""
    while link.is_connected:
        try:
            command, label = await asyncio.wait_for(sink.get(), timeout=0.5)
        except asyncio.TimeoutError:
            continue
        await link.send(command)
        sink.counters.delivered += 1
        # Let the pattern finish before starting the next one, otherwise the
        # firmware replaces it mid-play and both alerts read as one stutter.
        if command.duration_ms:
            await asyncio.sleep(command.duration_ms / 1000.0)
        LOGGER.debug("played %s", label)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="MEIT EE: audio or AI result -> haptic pattern -> BLE belt")
    parser.add_argument("--meit-ai", default=None,
                        help="path to the meit-ai checkout (default: $MEIT_AI_PATH)")
    parser.add_argument("--mock-label", default=None,
                        choices=("horn", "siren", "crash", "normal"),
                        help="skip the real model and always report this label; "
                             "for testing the belt without meit-ai")
    parser.add_argument("--mock-confidence", type=float, default=0.85)
    parser.add_argument("--wav", default=None,
                        help="one-shot mode: infer this wav file, vibrate once, exit")
    parser.add_argument("--direction", default=None,
                        choices=("front", "right", "back", "left"),
                        help="direction to use in --wav one-shot mode")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--cooldown-ms", type=int, default=1500,
                        help="ignore further alerts for this long after accepting one")
    parser.add_argument("--profile", default=None, help="haptic profile JSON")
    parser.add_argument("--intensity-scale", type=float, default=1.0)
    parser.add_argument("--log-level", default="INFO")
    add_belt_arguments(parser)
    return parser


async def _run_server_mode(args: argparse.Namespace) -> None:
    loop = asyncio.get_running_loop()
    sink = CommandSink(loop, cooldown_ms=args.cooldown_ms)
    runner = build_runner(args.meit_ai, mock_label=args.mock_label,
                          mock_confidence=args.mock_confidence)
    ingest = Ingest(runner, sink, profile=HapticProfile.load(args.profile),
                    intensity_scale=args.intensity_scale,
                    allow_back=args.protocol >= 3)

    httpd = ThreadingHTTPServer((args.host, args.port), _Handler)
    httpd.ingest = ingest  # type: ignore[attr-defined]
    httpd.cmd_protocol_version = args.protocol  # type: ignore[attr-defined]
    httpd.belt_connected = False  # type: ignore[attr-defined]
    thread = threading.Thread(target=httpd.serve_forever, name="meit-ee-http", daemon=True)
    thread.start()
    LOGGER.info("ingest listening on http://%s:%d (POST /ee/audio, /ee/event)",
                args.host, args.port)

    async def session(link: BeltLink) -> None:
        httpd.belt_connected = True  # type: ignore[attr-defined]
        try:
            await _drain(sink, link)
        finally:
            httpd.belt_connected = False  # type: ignore[attr-defined]

    belt = belt_from_arguments(args)
    try:
        await belt.run_forever(session)
    finally:
        httpd.shutdown()
        httpd.server_close()
        runner.close()


async def _run_wav_mode(args: argparse.Namespace) -> None:
    if not args.direction:
        raise SystemExit("--wav also needs --direction")
    direction = normalize_direction(args.direction, allow_back=args.protocol >= 3)
    runner = build_runner(args.meit_ai, mock_label=args.mock_label,
                          mock_confidence=args.mock_confidence)
    try:
        result = runner.infer(load_wav_clip(args.wav))
    finally:
        runner.close()
    LOGGER.info("%s -> label=%s confidence=%.3f danger=%s (%.0f ms)",
                args.wav, result.label, result.confidence, result.danger,
                result.inference_ms)

    command = build_command(result.label, result.confidence, direction,
                            HapticProfile.load(args.profile))
    if command is None or not result.danger:
        LOGGER.info("no pattern for this result; nothing sent")
        return
    if args.intensity_scale != 1.0:
        command = with_intensity_scale(command, args.intensity_scale)
    LOGGER.info("sending %s", command.describe())
    await belt_from_arguments(args).send_once(command)


def main(argv: Optional[list[str]] = None) -> None:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO),
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    if not 0.1 <= args.intensity_scale <= 2.0:
        raise SystemExit("--intensity-scale must be 0.1..2.0")
    if args.cooldown_ms < 0:
        raise SystemExit("--cooldown-ms must be >= 0")

    runner_mode = _run_wav_mode if args.wav else _run_server_mode
    try:
        asyncio.run(runner_mode(args))
    except KeyboardInterrupt:
        LOGGER.info("stopped")
    except AIError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
