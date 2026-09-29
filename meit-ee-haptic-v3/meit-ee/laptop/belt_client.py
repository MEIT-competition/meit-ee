"""BLE transport to the MEIT-BELT peripheral.

Both bridges (``ios_motor_bridge`` and ``ai_motor_bridge``) talk to the belt
through this module so scanning, reconnection, characteristic verification,
sequence numbering and protocol-version selection exist in exactly one place.

``bleak`` is imported lazily and the scanner/client can be injected, so the
reconnection logic is unit-testable on a machine with no Bluetooth adapter —
which is also what lets CI run these tests.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Optional

from laptop.protocol import (
    CMD_UUID,
    CMD_VERSION_V2,
    CMD_VERSION_V3,
    DEVICE_NAME,
    STOP_COMMAND,
    MotorCommand,
    ProtocolError,
    encode,
    to_v2_command,
)

LOGGER = logging.getLogger("meit.belt")

# Scanner and client factories are injectable purely for tests.
ScannerFn = Callable[[float, str], Awaitable[Optional[Any]]]
ClientFactory = Callable[[Any], Any]


class BeltUnavailable(RuntimeError):
    """The belt could not be found, connected to, or is running old firmware."""


async def _default_scanner(timeout: float, device_name: str) -> Optional[Any]:
    from bleak import BleakScanner

    for device in await BleakScanner.discover(timeout=timeout):
        if device.name == device_name:
            return device
    return None


def _default_client_factory(device: Any) -> Any:
    from bleak import BleakClient

    return BleakClient(device)


class BeltLink:
    """A connected belt: encodes commands and writes them to the CMD handle.

    One instance is valid for the lifetime of a single GATT connection.  It
    does not own reconnection; :class:`BeltClient` does.
    """

    def __init__(self, client: Any, *, protocol_version: int = CMD_VERSION_V3,
                 counter: Optional["SequenceCounter"] = None) -> None:
        if protocol_version not in (CMD_VERSION_V2, CMD_VERSION_V3):
            raise ProtocolError(f"unsupported protocol version: {protocol_version!r}")
        self.client = client
        self.protocol_version = protocol_version
        self.counter = counter or SequenceCounter()

    @property
    def is_connected(self) -> bool:
        return bool(getattr(self.client, "is_connected", False))

    async def send(self, command: MotorCommand) -> int:
        """Write one command and return the sequence number used.

        Downgrading to v2 happens here and is logged once per command, because
        a wearer feeling FRONT when the hazard was behind them needs to be
        explainable from the log rather than a surprise.
        """
        payload = command
        if self.protocol_version == CMD_VERSION_V2:
            payload = to_v2_command(command)
            if payload.direction != command.direction:
                LOGGER.warning("CMD v2 fallback: %s rendered as %s (reflash for 4-way)",
                               command.describe(), payload.describe())
        sequence = self.counter.next()
        packet = encode(sequence, payload, self.protocol_version)
        await self.client.write_gatt_char(CMD_UUID, packet, response=True)
        LOGGER.info("sent seq=%d v%d %s (%d bytes)",
                    sequence, self.protocol_version, payload.describe(), len(packet))
        return sequence

    async def stop(self) -> int:
        """Explicitly cancel any pattern still running on the belt."""
        return await self.send(STOP_COMMAND)


class SequenceCounter:
    """uint8 wrap-around counter, kept across reconnects for log correlation."""

    def __init__(self, start: int = 0) -> None:
        self.value = start & 0xFF

    def next(self) -> int:
        self.value = (self.value + 1) & 0xFF
        return self.value


def verify_cmd_characteristic(client: Any) -> None:
    """Fail fast if the connected device is not running motor-capable firmware.

    A belt still on pre-v2 firmware advertises the same name and service, so
    without this check the bridge would appear to run while every write was
    silently rejected.  Some ``bleak`` backends expose no service list until
    discovery completes; an empty list is treated as "cannot tell" rather than
    as a failure, since the write itself will surface a real mismatch.
    """
    services = getattr(client, "services", None)
    if services is None:
        return
    uuids = {
        str(characteristic.uuid).lower()
        for service in services
        for characteristic in getattr(service, "characteristics", ())
    }
    if not uuids:
        return
    if CMD_UUID.lower() not in uuids:
        raise BeltUnavailable(
            "connected device has no MEIT CMD characteristic; flash the current "
            "firmware from firmware/ before running the bridge"
        )


class BeltClient:
    """Scan, connect, run a session, and reconnect when the link drops."""

    def __init__(self, *, protocol_version: int = CMD_VERSION_V3,
                 device_name: str = DEVICE_NAME,
                 scan_timeout: float = 5.0,
                 reconnect_delay: float = 2.0,
                 scanner: Optional[ScannerFn] = None,
                 client_factory: Optional[ClientFactory] = None) -> None:
        if protocol_version not in (CMD_VERSION_V2, CMD_VERSION_V3):
            raise ProtocolError(f"unsupported protocol version: {protocol_version!r}")
        self.protocol_version = protocol_version
        self.device_name = device_name
        self.scan_timeout = scan_timeout
        self.reconnect_delay = reconnect_delay
        self._scan = scanner or _default_scanner
        self._client_factory = client_factory or _default_client_factory
        self.counter = SequenceCounter()

    async def _connect_once(self, session: Callable[[BeltLink], Awaitable[None]]) -> None:
        LOGGER.info("scanning for %s...", self.device_name)
        device = await self._scan(self.scan_timeout, self.device_name)
        if device is None:
            raise BeltUnavailable(f"{self.device_name} not found")

        async with self._client_factory(device) as client:
            verify_cmd_characteristic(client)
            LOGGER.info("connected to %s (CMD v%d)", self.device_name, self.protocol_version)
            link = BeltLink(client, protocol_version=self.protocol_version,
                            counter=self.counter)
            try:
                await session(link)
            finally:
                # Best-effort: never leave a pattern running because the
                # session raised. The firmware also stops on disconnect, so a
                # failure here is not worth propagating over the real error.
                try:
                    if link.is_connected:
                        await link.stop()
                except Exception as exc:  # noqa: BLE001 - diagnostics only
                    LOGGER.debug("stop-on-exit failed: %s", exc)

    async def run_forever(self, session: Callable[[BeltLink], Awaitable[None]], *,
                          max_attempts: Optional[int] = None) -> None:
        """Keep a session running against the belt, reconnecting as needed.

        ``max_attempts`` exists so tests (and a ``--once`` CLI mode) can bound
        the loop; production callers leave it ``None``.
        """
        attempts = 0
        while max_attempts is None or attempts < max_attempts:
            attempts += 1
            try:
                await self._connect_once(session)
                # A session that returns normally is a clean shutdown request.
                return
            except asyncio.CancelledError:
                raise
            except BeltUnavailable as exc:
                LOGGER.warning("%s; retrying in %.1fs", exc, self.reconnect_delay)
            except Exception as exc:  # noqa: BLE001 - any BLE stack error
                LOGGER.warning("belt link error: %s; reconnecting in %.1fs",
                               exc, self.reconnect_delay)
            if max_attempts is None or attempts < max_attempts:
                await asyncio.sleep(self.reconnect_delay)

    async def send_once(self, command: MotorCommand) -> None:
        """Connect, send a single command, disconnect.

        The belt's pattern sequencer runs independently of the BLE link and the
        firmware only force-stops on *disconnect*, so a short pattern completes
        after this returns.  The caller should wait ``command.duration_ms``
        before exiting if it wants to feel the whole thing.
        """
        async def session(link: BeltLink) -> None:
            await link.send(command)
            await asyncio.sleep(command.duration_ms / 1000.0 + 0.1)

        await self.run_forever(session, max_attempts=1)


def add_belt_arguments(parser: Any) -> None:
    """Shared CLI flags, so both bridges and the test tool stay consistent."""
    parser.add_argument("--protocol", type=int, choices=(2, 3), default=3,
                        help="BLE CMD version. 3 gives 4-way direction (front/right/back/left); "
                             "2 is the pre-reflash fallback and folds BACK into FRONT")
    parser.add_argument("--device-name", default=DEVICE_NAME,
                        help="BLE advertised name to connect to")
    parser.add_argument("--scan-timeout", type=float, default=5.0)
    parser.add_argument("--reconnect-delay", type=float, default=2.0)


def belt_from_arguments(args: Any) -> BeltClient:
    return BeltClient(protocol_version=args.protocol,
                      device_name=args.device_name,
                      scan_timeout=args.scan_timeout,
                      reconnect_delay=args.reconnect_delay)
