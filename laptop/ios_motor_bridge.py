"""Primary runtime: ``meit-ios`` ``/auto/status`` -> haptic mapping -> BLE -> motors.

    iPhone(s)  --Wi-Fi-->  meit-ios laptop bridge + meit-ai
                              |
                              |  GET /auto/status   (direction + AI result)
                              v
                     this process  (laptop/haptic.py)
                              |
                              |  BLE CMD v2
                              v
                       ESP32-S3 "MEIT-BELT"  ->  LEFT / RIGHT motors

No iOS or ``meit-ai`` source is modified, and inference is **not** repeated here:
the iOS bridge has already run the model and publishes the result, so this
process only decides what the wearer should feel and delivers it.

Run ``meit-ios/bridge/server.py`` first, then start the phone(s):

* single wearable iPhone (stereo left/center/right) — start listening in the
  app's Wearable mode and poll ``/wearable/status``::

      python -m laptop.ios_motor_bridge --status-path /wearable/status

* four-iPhone coordination path — enable Auto in the app and poll the default
  ``/auto/status``::

      python -m laptop.ios_motor_bridge

Both paths publish the same ``last_event`` shape, so everything below the status
read — suppression policy, haptic mapping, BLE delivery — is identical.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Optional

from laptop.belt_client import BeltLink, add_belt_arguments, belt_from_arguments
from laptop.haptic import (
    DANGER_LABELS,
    DEFAULT_PROFILE,
    HapticProfile,
    build_command,
    with_intensity_scale,
)
from laptop.protocol import DIR_STOP, direction_name, normalize_direction

try:  # 시연 화면(선택). 없어도 벨트 동작에는 영향 없음
    from display_server import show, start as start_display
except ImportError:
    show = start_display = None

LOGGER = logging.getLogger("meit.ios")

DEFAULT_SERVER = "http://127.0.0.1:8765"


@dataclass(frozen=True)
class Event:
    """One completed automatic event from ``/auto/status``."""

    event_id: str
    raw_direction: str
    direction: int
    label: str
    confidence: float
    danger: bool
    margin_db: Optional[float] = None


def _as_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def extract_event(status: Dict[str, Any]) -> Optional[Event]:
    """Parse the retained ``last_event``, or ``None`` if there is no completed one.

    Parsing and *policy* are deliberately separate: this returns whatever the
    iOS bridge said, including a direction that normalizes to ``DIR_STOP`` and a
    non-danger label, so :func:`suppression_reason` can log **why** an event
    produced no vibration.  Silently dropping events here made the previous
    version hard to debug at a demo.

    ``event.direction`` (the direction at trigger time) is preferred over any
    later live direction, matching ``meit-ios`` event semantics: the wearer must
    be told where the sound *was*, not where the loudest phone is now.
    """
    event = status.get("last_event")
    if not isinstance(event, dict) or event.get("outcome") != "completed":
        return None

    event_id = event.get("event_id")
    result = event.get("result")
    if not isinstance(event_id, str) or not event_id or not isinstance(result, dict):
        return None

    raw_direction = event.get("direction", result.get("direction"))
    return Event(
        event_id=event_id,
        raw_direction=str(raw_direction),
        direction=normalize_direction(raw_direction),
        label=str(result.get("label", "")).strip().lower(),
        confidence=_as_float(result.get("confidence")) or 0.0,
        # `danger` is the meit-ai decision layer's verdict. Absent in older or
        # manual-path responses, so only an explicit False suppresses.
        danger=result.get("danger") is not False,
        margin_db=_as_float(event.get("direction_margin_db")),
    )


def suppression_reason(event: Event) -> Optional[str]:
    """Why this event must not vibrate, or ``None`` if it should."""
    if not event.danger:
        return f"AI decided not dangerous (label={event.label})"
    if event.label not in DANGER_LABELS:
        return f"label {event.label!r} is not an alerting class"
    if event.direction == DIR_STOP:
        # meit-ios reports `unknown` whenever its own role/margin gate is not
        # satisfied, so there is nothing trustworthy to point at.
        return f"direction {event.raw_direction!r} is not usable"
    return None


class IOSMotorBridge:
    """Poll the iOS bridge and deliver one haptic command per new event."""

    def __init__(self, *, server: str = DEFAULT_SERVER,
                 status_path: str = "/auto/status",
                 profile: HapticProfile = DEFAULT_PROFILE,
                 intensity_scale: float = 1.0,
                 poll_interval: float = 0.1,
                 http_timeout: float = 1.0) -> None:
        self.status_url = server.rstrip("/") + "/" + status_path.lstrip("/")
        self.profile = profile
        self.intensity_scale = intensity_scale
        self.poll_interval = poll_interval
        self.http_timeout = http_timeout
        self.seen_event_id: Optional[str] = None
        self.cursor_ready = False

    # ------------------------------------------------------------- transport
    def _fetch(self) -> Dict[str, Any]:
        request = urllib.request.Request(self.status_url,
                                         headers={"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=self.http_timeout) as response:
            if response.status != 200:
                raise RuntimeError(f"HTTP {response.status} from {self.status_url}")
            return json.loads(response.read().decode("utf-8"))

    async def read_status(self) -> Dict[str, Any]:
        return await asyncio.to_thread(self._fetch)

    # ---------------------------------------------------------------- policy
    def _adopt_cursor(self, status: Dict[str, Any]) -> None:
        """Ignore whatever event was already retained when we started.

        ``/auto/status`` keeps the most recent event indefinitely, so without
        this the belt would fire once for a hazard that happened before the
        bridge was even running.
        """
        event = status.get("last_event")
        if isinstance(event, dict) and isinstance(event.get("event_id"), str):
            self.seen_event_id = event["event_id"]
            LOGGER.info("ignoring pre-existing event %s on startup",
                        self.seen_event_id[:8])
        self.cursor_ready = True

    async def _handle(self, link: BeltLink, status: Dict[str, Any]) -> None:
        event = extract_event(status)
        if event is None or event.event_id == self.seen_event_id:
            return

        reason = suppression_reason(event)
        if reason is not None:
            # Consume it: re-evaluating the same retained event every 100 ms
            # would flood the log without ever producing a command.
            self.seen_event_id = event.event_id
            LOGGER.info("event=%s suppressed: %s", event.event_id[:8], reason)
            return

        command = build_command(event.label, event.confidence, event.direction,
                                self.profile)
        if command is None:
            self.seen_event_id = event.event_id
            LOGGER.info("event=%s produced no pattern", event.event_id[:8])
            return
        if self.intensity_scale != 1.0:
            command = with_intensity_scale(command, self.intensity_scale)

        await link.send(command)
        # Only now is the event consumed. If the GATT write raised, the cursor
        # stays put so the same hazard is retried after reconnecting instead of
        # being lost, which is the whole point of doing this after the write.
        self.seen_event_id = event.event_id
        if show is not None:
            # 실제 BLE 전송이 성공한 뒤에만 화면 갱신 (화면이 벨트보다 앞서가지 않게)
            show(event.label, event.direction, None)
        margin = "" if event.margin_db is None else f" margin={event.margin_db:.1f}dB"
        LOGGER.info("ALERT event=%s %s/%s conf=%.3f%s -> %s",
                    event.event_id[:8], direction_name(event.direction),
                    event.label, event.confidence, margin, command.describe())

    # ----------------------------------------------------------- run session
    async def session(self, link: BeltLink) -> None:
        """Poll until the BLE link drops; :class:`BeltClient` reconnects."""
        while link.is_connected:
            try:
                status = await self.read_status()
            except (urllib.error.URLError, TimeoutError, OSError,
                    ValueError, RuntimeError) as exc:
                LOGGER.warning("/auto/status unavailable: %s", exc)
                await asyncio.sleep(max(0.5, self.poll_interval))
                continue

            if not self.cursor_ready:
                self._adopt_cursor(status)
            else:
                await self._handle(link, status)
            await asyncio.sleep(self.poll_interval)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="MEIT: meit-ios /auto/status -> haptic pattern -> BLE belt")
    parser.add_argument("--server", default=DEFAULT_SERVER,
                        help="meit-ios laptop bridge base URL")
    parser.add_argument("--status-path", default="/auto/status",
                        help="status endpoint to poll: /auto/status for the four-iPhone "
                             "coordination path, or /wearable/status for the single "
                             "wearable-iPhone stereo path")
    parser.add_argument("--poll-ms", type=int, default=100,
                        help="/auto/status polling interval in milliseconds")
    parser.add_argument("--http-timeout", type=float, default=1.0)
    parser.add_argument("--profile", default=None,
                        help="haptic profile JSON (default: laptop/haptic_profile.json values)")
    parser.add_argument("--intensity-scale", type=float, default=1.0,
                        help="multiply every pattern's duty, e.g. 0.7 for a quiet room "
                             "or 1.2 through a thick jacket")
    parser.add_argument("--log-level", default="INFO")
    add_belt_arguments(parser)
    return parser


def main(argv: Optional[list[str]] = None) -> None:
    args = build_parser().parse_args(argv)
    if args.poll_ms < 50:
        raise SystemExit("--poll-ms must be >= 50")
    if not 0.1 <= args.intensity_scale <= 2.0:
        raise SystemExit("--intensity-scale must be 0.1..2.0")

    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO),
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")

    bridge = IOSMotorBridge(
        server=args.server,
        status_path=args.status_path,
        profile=HapticProfile.load(args.profile),
        intensity_scale=args.intensity_scale,
        poll_interval=args.poll_ms / 1000.0,
        http_timeout=args.http_timeout,
    )
    belt = belt_from_arguments(args)
    if start_display is not None:
        start_display()
    try:
        asyncio.run(belt.run_forever(bridge.session))
    except KeyboardInterrupt:
        LOGGER.info("stopped")


if __name__ == "__main__":
    main()
