"""Tests for the primary runtime: parsing, suppression policy and event cursor.

The cursor rules are the ones worth guarding. ``/auto/status`` retains its most
recent event forever, so getting them wrong means either a hazard replayed
every 100 ms or one silently dropped.
"""
import logging
import unittest

from laptop.belt_client import BeltLink
from laptop.ios_motor_bridge import (
    Event,
    IOSMotorBridge,
    extract_event,
    suppression_reason,
)
from laptop.protocol import (
    DIR_CENTER,
    DIR_LEFT,
    DIR_RIGHT,
    DIR_STOP,
    MASK_BOTH,
    MASK_LEFT,
    MASK_RIGHT,
    decode,
)
from laptop.test_belt_client import FakeClient


def setUpModule():
    logging.getLogger("meit.ios").addHandler(logging.NullHandler())


def status(direction="left", label="siren", confidence=0.88, danger=True,
           outcome="completed", event_id="event-1", **extra):
    result = {"label": label, "confidence": confidence}
    if danger is not None:
        result["danger"] = danger
    event = {"event_id": event_id, "outcome": outcome,
             "direction": direction, "result": result}
    event.update(extra)
    return {"last_event": event}


class ExtractEventTests(unittest.TestCase):
    def test_completed_event(self):
        event = extract_event(status(direction="center", label="horn", confidence=0.7))
        self.assertEqual(event.event_id, "event-1")
        self.assertEqual(event.direction, DIR_CENTER)
        self.assertEqual(event.label, "horn")
        self.assertAlmostEqual(event.confidence, 0.7)
        self.assertTrue(event.danger)

    def test_no_event(self):
        self.assertIsNone(extract_event({}))
        self.assertIsNone(extract_event({"last_event": None}))
        self.assertIsNone(extract_event({"last_event": "nope"}))

    def test_incomplete_outcomes_are_ignored(self):
        for outcome in ("audio_timeout", "stopped", "inference_failed",
                        "WAITING_FOR_AUDIO"):
            self.assertIsNone(extract_event(status(outcome=outcome)), outcome)

    def test_missing_identity_or_result(self):
        self.assertIsNone(extract_event(status(event_id="")))
        broken = status()
        broken["last_event"]["result"] = None
        self.assertIsNone(extract_event(broken))

    def test_trigger_time_direction_wins_over_a_nested_one(self):
        # meit-ios semantics: tell the wearer where the sound *was*, not where
        # the loudest phone is now.
        payload = status(direction="right")
        payload["last_event"]["result"]["direction"] = "left"
        self.assertEqual(extract_event(payload).direction, DIR_RIGHT)

    def test_nested_direction_is_the_fallback(self):
        payload = status()
        del payload["last_event"]["direction"]
        payload["last_event"]["result"]["direction"] = "center"
        self.assertEqual(extract_event(payload).direction, DIR_CENTER)

    def test_missing_danger_is_treated_as_dangerous(self):
        # Older and manual-path responses omit the field; only an explicit
        # False means the decision layer said no.
        self.assertTrue(extract_event(status(danger=None)).danger)
        self.assertFalse(extract_event(status(danger=False)).danger)

    def test_malformed_confidence_becomes_zero(self):
        self.assertEqual(extract_event(status(confidence="high")).confidence, 0.0)

    def test_margin_is_captured_when_present(self):
        event = extract_event(status(direction_margin_db=4.5))
        self.assertAlmostEqual(event.margin_db, 4.5)
        self.assertIsNone(extract_event(status()).margin_db)

    def test_unavailable_direction_is_suppressed(self):
        self.assertEqual(extract_event(status(direction="unavailable")).direction,
                         DIR_STOP)


class SuppressionTests(unittest.TestCase):
    def reason(self, **kwargs):
        return suppression_reason(extract_event(status(**kwargs)))

    def test_dangerous_event_is_not_suppressed(self):
        self.assertIsNone(self.reason())

    def test_ai_verdict_is_respected(self):
        self.assertIn("not dangerous", self.reason(danger=False))

    def test_non_alerting_labels(self):
        self.assertIn("not an alerting class", self.reason(label="normal"))

    def test_unusable_direction(self):
        self.assertIn("not usable", self.reason(direction="unavailable"))
        self.assertIn("not usable", self.reason(direction="sideways"))

    def test_reason_names_the_offending_value(self):
        self.assertIn("normal", self.reason(label="normal"))
        self.assertIn("unavailable", self.reason(direction="unavailable"))


class CursorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = FakeClient()
        self.link = BeltLink(self.client)
        self.bridge = IOSMotorBridge()

    async def test_first_contact_ignores_a_retained_event(self):
        # Starting the bridge must not vibrate for a hazard that happened before
        # it was running.
        self.bridge._adopt_cursor(status(event_id="old"))
        await self.bridge._handle(self.link, status(event_id="old"))
        self.assertEqual(self.client.writes, [])

    async def test_new_event_is_delivered(self):
        self.bridge._adopt_cursor(status(event_id="old"))
        await self.bridge._handle(self.link, status(event_id="new", direction="center",
                                                   label="horn"))
        self.assertEqual(len(self.client.writes), 1)
        command = decode(self.client.writes[0][1]).command
        self.assertEqual(command.direction, DIR_CENTER)
        self.assertEqual(command.mask, MASK_BOTH)
        self.assertEqual(len(command.steps), 2)
        self.assertEqual(self.bridge.seen_event_id, "new")

    async def test_the_same_event_is_delivered_only_once(self):
        self.bridge.cursor_ready = True
        payload = status(event_id="e1")
        await self.bridge._handle(self.link, payload)
        await self.bridge._handle(self.link, payload)
        await self.bridge._handle(self.link, payload)
        self.assertEqual(len(self.client.writes), 1)

    async def test_a_failed_write_keeps_the_event_eligible_for_retry(self):
        # This is why the cursor advances after the write and not before: a
        # hazard the wearer never felt must be retried, not dropped.
        failing = FakeClient(fail_on_write=0)
        link = BeltLink(failing)
        self.bridge.cursor_ready = True
        with self.assertRaises(OSError):
            await self.bridge._handle(link, status(event_id="e1"))
        self.assertIsNone(self.bridge.seen_event_id)

        await self.bridge._handle(self.link, status(event_id="e1"))
        self.assertEqual(len(self.client.writes), 1)
        self.assertEqual(self.bridge.seen_event_id, "e1")

    async def test_a_suppressed_event_is_consumed_without_a_write(self):
        # Otherwise a 100 ms poll loop re-evaluates and re-logs the same
        # retained event forever.
        self.bridge.cursor_ready = True
        await self.bridge._handle(self.link, status(event_id="e1", label="normal",
                                                   danger=False))
        self.assertEqual(self.client.writes, [])
        self.assertEqual(self.bridge.seen_event_id, "e1")

    async def test_adopt_cursor_tolerates_an_empty_status(self):
        self.bridge._adopt_cursor({})
        self.assertTrue(self.bridge.cursor_ready)
        self.assertIsNone(self.bridge.seen_event_id)
        await self.bridge._handle(self.link, status(event_id="e1"))
        self.assertEqual(len(self.client.writes), 1)


class MappingTests(unittest.IsolatedAsyncioTestCase):
    async def deliver(self, **kwargs):
        client = FakeClient()
        bridge = IOSMotorBridge(**kwargs.pop("bridge_kwargs", {}))
        bridge.cursor_ready = True
        await bridge._handle(BeltLink(client), status(**kwargs))
        return decode(client.writes[0][1]).command if client.writes else None

    async def test_confidence_reaches_the_intensity(self):
        low = await self.deliver(confidence=0.5, event_id="a")
        high = await self.deliver(confidence=1.0, event_id="b")
        self.assertLess(low.intensity, high.intensity)

    async def test_class_reaches_the_pulse_count(self):
        counts = {}
        for label in ("crash", "horn", "siren"):
            command = await self.deliver(label=label, event_id=label)
            counts[label] = len(command.steps)
        self.assertEqual(len(set(counts.values())), 3, counts)

    async def test_intensity_scale_is_applied(self):
        plain = await self.deliver(event_id="a")
        scaled = await self.deliver(event_id="b",
                                    bridge_kwargs={"intensity_scale": 0.5})
        self.assertLess(scaled.intensity, plain.intensity)

    async def test_left_and_right_reach_the_right_motor(self):
        left = await self.deliver(direction="left", event_id="l")
        right = await self.deliver(direction="right", event_id="r")
        self.assertEqual(left.mask, MASK_LEFT)
        self.assertEqual(right.mask, MASK_RIGHT)
        self.assertEqual(left.direction, DIR_LEFT)
        self.assertEqual(right.direction, DIR_RIGHT)


class SessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_session_polls_until_the_link_drops(self):
        statuses = [status(event_id="old"), status(event_id="e1"),
                    status(event_id="e2", label="crash")]
        index = {"value": 0}

        client = FakeClient(drop_after=2)
        bridge = IOSMotorBridge(poll_interval=0)

        async def read_status():
            position = min(index["value"], len(statuses) - 1)
            index["value"] += 1
            return statuses[position]

        bridge.read_status = read_status
        await bridge.session(BeltLink(client))

        # The first poll only adopts the cursor, so the two later events are the
        # ones delivered.
        self.assertEqual(len(client.writes), 2)

    async def test_status_errors_do_not_kill_the_session(self):
        client = FakeClient(drop_after=1)
        bridge = IOSMotorBridge(poll_interval=0)
        calls = {"value": 0}

        async def read_status():
            calls["value"] += 1
            if calls["value"] <= 2:
                raise OSError("connection refused")
            return status(event_id=f"e{calls['value']}")

        bridge.read_status = read_status
        await bridge.session(BeltLink(client))
        self.assertGreaterEqual(calls["value"], 3)
        self.assertEqual(len(client.writes), 1)


class WearableStatusPathTests(unittest.IsolatedAsyncioTestCase):
    """The single-wearable-iPhone path: /wearable/status carries the same shape.

    The dicts here are byte-for-byte what ``meit-ios`` ``WearableEvents.status()``
    publishes, so these tests are the EE end of that cross-repo contract.
    """

    def test_status_path_selects_the_endpoint(self):
        default = IOSMotorBridge()
        self.assertTrue(default.status_url.endswith("/auto/status"))
        wearable = IOSMotorBridge(status_path="/wearable/status")
        self.assertTrue(wearable.status_url.endswith("/wearable/status"))
        # A missing or extra leading slash must not double up or drop the path.
        self.assertEqual(IOSMotorBridge(server="http://h:8765/",
                                        status_path="wearable/status").status_url,
                         "http://h:8765/wearable/status")

    @staticmethod
    def wearable_status(direction="left", label="siren", confidence=0.9,
                        danger=True, event_id="w1"):
        # Exactly the record meit-ios/bridge/wearable.py emits.
        return {"last_event": {
            "event_id": event_id, "outcome": "completed", "direction": direction,
            "result": {"label": label, "confidence": confidence,
                       "inference_ms": 2.5, "danger": danger,
                       "direction": None if direction == "unavailable" else direction}}}

    async def test_wearable_event_drives_the_matching_motor(self):
        for direction, mask in (("left", MASK_LEFT), ("right", MASK_RIGHT),
                                ("center", MASK_BOTH)):
            client = FakeClient()
            bridge = IOSMotorBridge(status_path="/wearable/status")
            bridge.cursor_ready = True
            await bridge._handle(BeltLink(client),
                                 self.wearable_status(direction=direction, event_id=direction))
            self.assertEqual(len(client.writes), 1, direction)
            self.assertEqual(decode(client.writes[0][1]).command.mask, mask, direction)

    async def test_wearable_unavailable_is_suppressed(self):
        client = FakeClient()
        bridge = IOSMotorBridge(status_path="/wearable/status")
        bridge.cursor_ready = True
        await bridge._handle(BeltLink(client), self.wearable_status(direction="unavailable"))
        self.assertEqual(client.writes, [])

    async def test_wearable_non_danger_is_suppressed(self):
        client = FakeClient()
        bridge = IOSMotorBridge(status_path="/wearable/status")
        bridge.cursor_ready = True
        await bridge._handle(BeltLink(client),
                             self.wearable_status(label="normal", danger=False))
        self.assertEqual(client.writes, [])


class EventDataclassTests(unittest.TestCase):
    def test_event_is_hashable_and_comparable(self):
        first = Event("a", "left", DIR_LEFT, "siren", 0.9, True)
        second = Event("a", "left", DIR_LEFT, "siren", 0.9, True)
        self.assertEqual(first, second)
        self.assertEqual({first, second}, {first})

    def test_stop_direction_is_a_suppression_not_a_command(self):
        event = Event("a", "unavailable", DIR_STOP, "siren", 0.9, True)
        self.assertIsNotNone(suppression_reason(event))


if __name__ == "__main__":
    unittest.main()
