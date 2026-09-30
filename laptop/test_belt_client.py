"""BLE transport tests, run against fakes so no Bluetooth adapter is needed."""
import logging
import unittest

from laptop.belt_client import (
    BeltClient,
    BeltLink,
    BeltUnavailable,
    SequenceCounter,
    verify_cmd_characteristic,
)
from laptop.haptic import build_command
from laptop.protocol import (
    CMD_UUID,
    CMD_VERSION,
    DIR_CENTER,
    DIR_LEFT,
    MASK_BOTH,
    STOP_COMMAND,
    decode,
)


class FakeCharacteristic:
    def __init__(self, uuid):
        self.uuid = uuid


class FakeService:
    def __init__(self, uuids):
        self.characteristics = [FakeCharacteristic(uuid) for uuid in uuids]


class FakeClient:
    """Enough of ``bleak.BleakClient`` for the transport under test."""

    def __init__(self, *, uuids=(CMD_UUID,), services=True,
                 fail_on_write=None, drop_after=None):
        self.is_connected = True
        self.writes = []
        self.services = [FakeService(uuids)] if services else None
        self.fail_on_write = fail_on_write
        self.drop_after = drop_after
        self.entered = 0
        self.exited = 0

    async def __aenter__(self):
        self.entered += 1
        return self

    async def __aexit__(self, *exc_info):
        self.exited += 1
        self.is_connected = False
        return False

    async def write_gatt_char(self, uuid, data, response=True):
        if self.fail_on_write is not None and len(self.writes) == self.fail_on_write:
            raise OSError("simulated GATT failure")
        self.writes.append((uuid, bytes(data)))
        if self.drop_after is not None and len(self.writes) >= self.drop_after:
            self.is_connected = False

    def decoded(self):
        return [decode(payload) for _, payload in self.writes]


def setUpModule():
    # Without a handler, logging falls back to stderr and buries the test
    # results in reconnect warnings these tests deliberately provoke.
    # assertLogs still works: it installs its own handler.
    logging.getLogger("meit.belt").addHandler(logging.NullHandler())


def scanner_returning(device):
    async def scan(timeout, name):
        return device
    return scan


class SequenceCounterTests(unittest.TestCase):
    def test_wraps_at_uint8(self):
        counter = SequenceCounter(start=254)
        self.assertEqual([counter.next() for _ in range(3)], [255, 0, 1])


class CharacteristicVerificationTests(unittest.TestCase):
    def test_missing_characteristic_is_fatal(self):
        # Pre-v2 firmware advertises the same name and service, so without this
        # the bridge would look healthy while every write was rejected.
        with self.assertRaises(BeltUnavailable):
            verify_cmd_characteristic(FakeClient(uuids=("0000-other",)))

    def test_present_characteristic_passes(self):
        verify_cmd_characteristic(FakeClient())

    def test_uuid_case_is_ignored(self):
        verify_cmd_characteristic(FakeClient(uuids=(CMD_UUID.upper(),)))

    def test_unknown_service_list_is_not_a_failure(self):
        # Some bleak backends expose nothing until discovery finishes; the write
        # itself will surface a real mismatch.
        verify_cmd_characteristic(FakeClient(services=False))
        verify_cmd_characteristic(FakeClient(uuids=()))


class BeltLinkTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_encodes_and_numbers_sequences(self):
        client = FakeClient()
        link = BeltLink(client)
        command = build_command("siren", 0.9, DIR_CENTER)

        self.assertEqual(await link.send(command), 1)
        self.assertEqual(await link.send(command), 2)

        uuid, payload = client.writes[0]
        self.assertEqual(uuid, CMD_UUID)
        decoded = decode(payload)
        self.assertEqual(decoded.version, CMD_VERSION)
        self.assertEqual(decoded.sequence, 1)
        self.assertEqual(decoded.command, command)
        self.assertEqual(decoded.command.mask, MASK_BOTH)

    async def test_no_warning_on_a_normal_send(self):
        link = BeltLink(FakeClient())
        with self.assertNoLogs("meit.belt", level=logging.WARNING):
            await link.send(build_command("siren", 0.9, DIR_LEFT))

    async def test_stop(self):
        client = FakeClient()
        link = BeltLink(client)
        await link.stop()
        self.assertEqual(decode(client.writes[0][1]).command, STOP_COMMAND)

    async def test_write_failure_propagates(self):
        # The caller needs the failure so it can avoid advancing its event
        # cursor past an alert the wearer never felt.
        link = BeltLink(FakeClient(fail_on_write=0))
        with self.assertRaises(OSError):
            await link.send(build_command("horn", 0.9, DIR_LEFT))


class BeltClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_session_runs_and_stops_on_exit(self):
        client = FakeClient()
        calls = []

        async def session(link):
            calls.append(link)
            await link.send(build_command("crash", 0.9, DIR_LEFT))

        belt = BeltClient(scanner=scanner_returning(object()),
                          client_factory=lambda device: client)
        await belt.run_forever(session, max_attempts=1)

        self.assertEqual(len(calls), 1)
        # One alert, then the fail-safe STOP on the way out.
        self.assertEqual(len(client.writes), 2)
        self.assertEqual(decode(client.writes[-1][1]).command, STOP_COMMAND)
        self.assertEqual(client.exited, 1)

    async def test_belt_not_found_is_retried(self):
        attempts = []

        async def scan(timeout, name):
            attempts.append(name)
            return None

        belt = BeltClient(scanner=scan, reconnect_delay=0)
        await belt.run_forever(lambda link: None, max_attempts=3)
        self.assertEqual(len(attempts), 3)

    async def test_session_error_triggers_reconnect(self):
        clients = [FakeClient(), FakeClient()]
        created = []

        def factory(device):
            client = clients[len(created)]
            created.append(client)
            return client

        calls = []

        async def session(link):
            calls.append(1)
            if len(calls) == 1:
                raise OSError("link dropped")

        belt = BeltClient(scanner=scanner_returning(object()),
                          client_factory=factory, reconnect_delay=0)
        await belt.run_forever(session, max_attempts=2)
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(created), 2)

    async def test_sequence_numbers_survive_reconnects(self):
        # Sequence numbers are the only way to correlate a laptop log line with
        # an ESP32 log line, so a reconnect must not reset them.
        clients = [FakeClient(), FakeClient()]
        created = []

        def factory(device):
            client = clients[len(created)]
            created.append(client)
            return client

        async def session(link):
            await link.send(build_command("horn", 0.9, DIR_LEFT))
            if len(created) == 1:
                raise OSError("link dropped")

        belt = BeltClient(scanner=scanner_returning(object()),
                          client_factory=factory, reconnect_delay=0)
        await belt.run_forever(session, max_attempts=2)

        first = decode(clients[0].writes[0][1]).sequence
        second = decode(clients[1].writes[0][1]).sequence
        self.assertNotEqual(first, second)

    async def test_send_once_connects_sends_and_leaves(self):
        client = FakeClient()
        belt = BeltClient(scanner=scanner_returning(object()),
                          client_factory=lambda device: client)
        command = build_command("crash", 1.0, DIR_LEFT)
        await belt.send_once(command)
        self.assertEqual(decode(client.writes[0][1]).command, command)
        self.assertEqual(client.exited, 1)


if __name__ == "__main__":
    unittest.main()
