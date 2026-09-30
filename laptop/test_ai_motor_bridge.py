"""Tests for the standalone EE ingest path.

Covers the three things that are easy to get wrong when HTTP threads, a
TensorFlow session and one BLE writer share a process: the cooldown, the bounded
queue's drop policy, and the fact that only the BLE task ever writes.
"""
import asyncio
import logging
import unittest

from laptop.ai_motor_bridge import CommandSink, Ingest, _drain
from laptop.ai_runner import PAYLOAD_BYTES, AIError, MockAIRunner
from laptop.belt_client import BeltLink
from laptop.haptic import build_command
from laptop.protocol import (
    DIR_CENTER,
    DIR_LEFT,
    MASK_BOTH,
    STOP_COMMAND,
    decode,
)
from laptop.test_belt_client import FakeClient


def setUpModule():
    logging.getLogger("meit.aibridge").addHandler(logging.NullHandler())
    logging.getLogger("meit.ai").addHandler(logging.NullHandler())


def make_ingest(*, label="siren", cooldown_ms=0, intensity_scale=1.0,
                confidence=0.85):
    sink = CommandSink(asyncio.get_running_loop(), cooldown_ms=cooldown_ms)
    runner = MockAIRunner(label=label, confidence=confidence)
    return Ingest(runner, sink, intensity_scale=intensity_scale), sink


class EventIngestTests(unittest.IsolatedAsyncioTestCase):
    async def test_ready_result_skips_inference(self):
        ingest, sink = make_ingest()
        outcome = ingest.handle_event({"direction": "center", "label": "horn",
                                       "confidence": 0.9})
        self.assertEqual(outcome["outcome"], "queued")
        self.assertEqual(outcome["direction"], "CENTER")
        command, _ = await sink.get()
        self.assertEqual(command.direction, DIR_CENTER)
        self.assertEqual(command.mask, MASK_BOTH)
        self.assertEqual(len(command.steps), 2)

    async def test_not_dangerous_is_suppressed(self):
        ingest, sink = make_ingest()
        outcome = ingest.handle_event({"direction": "left", "label": "horn",
                                       "danger": False})
        self.assertEqual(outcome["reason"], "not_dangerous")
        self.assertTrue(sink.queue.empty())
        self.assertEqual(sink.counters.suppressed, 1)

    async def test_non_alerting_label_is_suppressed(self):
        ingest, sink = make_ingest()
        outcome = ingest.handle_event({"direction": "left", "label": "normal"})
        self.assertEqual(outcome["reason"], "no_pattern_for_result")
        self.assertTrue(sink.queue.empty())

    async def test_unusable_direction_is_a_client_error(self):
        # The caller has to supply a direction; guessing one would point the
        # wearer at a hazard that may be on the other side.
        ingest, _ = make_ingest()
        for direction in (None, "unavailable", "sideways", ""):
            with self.assertRaises(ValueError):
                ingest.handle_event({"direction": direction, "label": "horn"})

    async def test_malformed_confidence_still_alerts(self):
        ingest, sink = make_ingest()
        outcome = ingest.handle_event({"direction": "left", "label": "crash",
                                       "confidence": "very"})
        self.assertEqual(outcome["outcome"], "queued")
        command, _ = await sink.get()
        self.assertGreaterEqual(command.intensity, 1)

    async def test_intensity_scale_is_applied(self):
        loud, loud_sink = make_ingest()
        quiet, quiet_sink = make_ingest(intensity_scale=0.5)
        loud.handle_event({"direction": "left", "label": "siren", "confidence": 0.9})
        quiet.handle_event({"direction": "left", "label": "siren", "confidence": 0.9})
        first, _ = await loud_sink.get()
        second, _ = await quiet_sink.get()
        self.assertLess(second.intensity, first.intensity)


class AudioIngestTests(unittest.IsolatedAsyncioTestCase):
    async def test_audio_runs_inference_then_maps(self):
        ingest, sink = make_ingest(label="crash", confidence=0.95)
        outcome = ingest.handle_audio(bytes(PAYLOAD_BYTES), "center", "evt-1")
        self.assertEqual(outcome["label"], "crash")
        self.assertEqual(outcome["direction"], "CENTER")
        command, _ = await sink.get()
        self.assertEqual(len(command.steps), 1)

    async def test_direction_is_validated_before_inference(self):
        ingest, sink = make_ingest()
        with self.assertRaises(ValueError):
            ingest.handle_audio(bytes(PAYLOAD_BYTES), "unavailable", None)
        # Nothing was counted as received, because nothing was processed.
        self.assertEqual(sink.counters.received, 0)

    async def test_short_payloads_are_fitted(self):
        ingest, sink = make_ingest()
        outcome = ingest.handle_audio(b"\x00\x01" * 10, "left", None)
        self.assertEqual(outcome["outcome"], "queued")

    async def test_inference_errors_propagate_as_ai_errors(self):
        class BrokenRunner:
            def infer(self, payload):
                raise AIError("model exploded")

            def close(self):
                pass

        sink = CommandSink(asyncio.get_running_loop())
        ingest = Ingest(BrokenRunner(), sink)
        with self.assertRaises(AIError):
            ingest.handle_audio(bytes(PAYLOAD_BYTES), "left", None)


class CooldownTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_second_alert_inside_the_cooldown_is_dropped(self):
        # Two alerts 200 ms apart are almost always the same physical event;
        # replaying it would only cut the first pattern short.
        ingest, sink = make_ingest(cooldown_ms=60_000)
        first = ingest.handle_event({"direction": "left", "label": "horn"})
        second = ingest.handle_event({"direction": "right", "label": "siren"})
        self.assertEqual(first["outcome"], "queued")
        self.assertEqual(second["outcome"], "cooldown")
        self.assertEqual(sink.counters.cooldown, 1)
        # submit() hands the command to the loop, so let it run before counting.
        await asyncio.sleep(0)
        self.assertEqual(sink.queue.qsize(), 1)

    async def test_zero_cooldown_accepts_everything(self):
        ingest, sink = make_ingest(cooldown_ms=0)
        for _ in range(3):
            self.assertEqual(
                ingest.handle_event({"direction": "left", "label": "horn"})["outcome"],
                "queued")
        await asyncio.sleep(0)
        self.assertEqual(sink.queue.qsize(), 3)

    async def test_stop_ignores_the_cooldown(self):
        # Stopping the motors must always work, whatever the alert rate.
        ingest, sink = make_ingest(cooldown_ms=60_000)
        ingest.handle_event({"direction": "left", "label": "horn"})
        self.assertEqual(sink.submit_stop(), "queued")
        await asyncio.sleep(0)
        self.assertEqual(sink.queue.qsize(), 2)


class QueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_oldest_command_is_dropped_when_full(self):
        # When hazards arrive faster than the belt can play them, the newest is
        # the one the wearer needs.
        sink = CommandSink(asyncio.get_running_loop(), depth=2, cooldown_ms=0)
        for index, label in enumerate(("horn", "siren", "crash")):
            sink.submit(build_command(label, 0.9, DIR_LEFT), label)
            await asyncio.sleep(0)
        self.assertEqual(sink.queue.qsize(), 2)
        self.assertEqual(sink.counters.dropped, 1)
        _, first = await sink.get()
        _, second = await sink.get()
        self.assertEqual([first, second], ["siren", "crash"])


class DrainTests(unittest.IsolatedAsyncioTestCase):
    async def test_queued_commands_reach_the_belt(self):
        sink = CommandSink(asyncio.get_running_loop(), cooldown_ms=0)
        client = FakeClient(drop_after=2)
        sink.submit(build_command("horn", 0.9, DIR_LEFT), "a")
        sink.submit(build_command("crash", 0.9, DIR_CENTER), "b")
        await asyncio.sleep(0)

        await asyncio.wait_for(_drain(sink, BeltLink(client)), timeout=5)
        self.assertEqual(len(client.writes), 2)
        self.assertEqual(sink.counters.delivered, 2)
        self.assertEqual(decode(client.writes[1][1]).command.direction, DIR_CENTER)

    async def test_drain_exits_when_the_link_drops(self):
        sink = CommandSink(asyncio.get_running_loop(), cooldown_ms=0)
        client = FakeClient()
        client.is_connected = False
        await asyncio.wait_for(_drain(sink, BeltLink(client)), timeout=5)
        self.assertEqual(client.writes, [])

    async def test_stop_is_delivered(self):
        sink = CommandSink(asyncio.get_running_loop(), cooldown_ms=0)
        client = FakeClient(drop_after=1)
        sink.submit_stop()
        await asyncio.sleep(0)
        await asyncio.wait_for(_drain(sink, BeltLink(client)), timeout=5)
        self.assertEqual(decode(client.writes[0][1]).command, STOP_COMMAND)


class HttpTests(unittest.IsolatedAsyncioTestCase):
    """The HTTP surface itself, since a 400 at a demo is indistinguishable from
    a dead belt unless the endpoints are known to work."""

    async def asyncSetUp(self):
        import json
        import threading
        from http.server import ThreadingHTTPServer

        from laptop.ai_motor_bridge import _Handler

        self.json = json
        self.ingest, self.sink = make_ingest(label="siren", cooldown_ms=0)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.httpd.ingest = self.ingest
        self.httpd.belt_connected = True
        # A short poll interval keeps shutdown() from adding half a second
        # per test; production uses serve_forever()'s default.
        self.thread = threading.Thread(
            target=lambda: self.httpd.serve_forever(poll_interval=0.02), daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    async def asyncTearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)

    def request(self, path, data=None, headers=None, method=None):
        import urllib.error
        import urllib.request

        req = urllib.request.Request(self.base + path, data=data,
                                     headers=headers or {}, method=method)
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                return response.status, self.json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            return exc.code, self.json.loads(exc.read().decode())

    async def test_health(self):
        status, body = self.request("/ee/health")
        self.assertEqual(status, 200)
        self.assertEqual(body["runner"], "MockAIRunner")
        self.assertTrue(body["belt_connected"])

    async def test_event_endpoint(self):
        payload = self.json.dumps({"direction": "center", "label": "crash",
                                   "confidence": 0.9}).encode()
        status, body = self.request("/ee/event", payload,
                                   {"Content-Type": "application/json"})
        self.assertEqual(status, 200)
        self.assertEqual(body["outcome"], "queued")
        self.assertEqual(body["direction"], "CENTER")
        command, _ = await self.sink.get()
        self.assertEqual(command.direction, DIR_CENTER)

    async def test_audio_endpoint(self):
        status, body = self.request("/ee/audio", bytes(PAYLOAD_BYTES),
                                   {"X-MEIT-Direction": "left",
                                    "X-MEIT-Event-Id": "evt-7",
                                    "Content-Type": "application/octet-stream"})
        self.assertEqual(status, 200)
        self.assertEqual(body["label"], "siren")
        command, _ = await self.sink.get()
        self.assertEqual(command.direction, DIR_LEFT)

    async def test_audio_without_a_direction_header_is_rejected(self):
        status, body = self.request("/ee/audio", bytes(PAYLOAD_BYTES), {})
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], "invalid_request")

    async def test_oversized_body_is_rejected(self):
        status, _ = self.request("/ee/audio", bytes(PAYLOAD_BYTES * 3),
                                 {"X-MEIT-Direction": "left"})
        self.assertEqual(status, 400)

    async def test_malformed_json_is_rejected(self):
        status, body = self.request("/ee/event", b"{not json",
                                    {"Content-Type": "application/json"})
        self.assertEqual(status, 400)

    async def test_empty_body_is_rejected(self):
        status, _ = self.request("/ee/event", b"", method="POST")
        self.assertEqual(status, 400)

    async def test_stop_endpoint(self):
        status, body = self.request("/ee/stop", b"", method="POST")
        self.assertEqual(status, 200)
        command, _ = await self.sink.get()
        self.assertEqual(command, STOP_COMMAND)

    async def test_unknown_paths(self):
        self.assertEqual(self.request("/nope")[0], 404)
        self.assertEqual(self.request("/nope", b"x", method="POST")[0], 404)


if __name__ == "__main__":
    unittest.main()
