"""BLE transport to the MEIT-BELT peripheral.

Every entry point (``ios_motor_bridge``, ``ai_motor_bridge``, ``send_motor_test``,
``verify_pipeline``) talks to the belt through this module, so scanning,
reconnection, characteristic verification and sequence numbering exist in exactly
one place.

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
    DEVICE_NAME,
    STOP_COMMAND,
    MotorCommand,
    encode,
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


class SequenceCounter:
    """uint8 wrap-around counter, kept across reconnects for log correlation."""

    def __init__(self, start: int = 0) -> None:
        self.value = start & 0xFF

    def next(self) -> int:
        self.value = (self.value + 1) & 0xFF
        return self.value


class BeltLink:
    """A connected belt: encodes commands and writes them to the CMD handle.

    One instance is valid for the lifetime of a single GATT connection. It does
    not own reconnection; :class:`BeltClient` does.
    """

    def __init__(self, client: Any, *, counter: Optional[SequenceCounter] = None) -> None:
        self.client = client
        self.counter = counter or SequenceCounter()

    @property
    def is_connected(self) -> bool:
        return bool(getattr(self.client, "is_connected", False))

    async def send(self, command: MotorCommand) -> int:
        """Write one command and return the sequence number used."""
        sequence = self.counter.next()
        packet = encode(sequence, command)
        await self.client.write_gatt_char(CMD_UUID, packet, response=True)
        LOGGER.info("sent seq=%d %s (%d bytes)", sequence, command.describe(), len(packet))
        return sequence

    async def stop(self) -> int:
        """Explicitly cancel any pattern still running on the belt."""
        return await self.send(STOP_COMMAND)


def verify_cmd_characteristic(client: Any) -> None:
    """Fail fast if the connected device is not running motor-capable firmware.

    A belt on pre-CMD firmware advertises the same name and service, so without
    this check the bridge would appear to run while every write was silently
    rejected. Some ``bleak`` backends expose no service list until discovery
    completes; an empty list is treated as "cannot tell" rather than as a
    failure, since the write itself will surface a real mismatch.
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

    def __init__(self, *, device_name: str = DEVICE_NAME,
                 scan_timeout: float = 5.0,
                 reconnect_delay: float = 2.0,
                 scanner: Optional[ScannerFn] = None,
                 client_factory: Optional[ClientFactory] = None) -> None:
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
            LOGGER.info("connected to %s", self.device_name)
            link = BeltLink(client, counter=self.counter)
            try:
                await session(link)
            finally:
                # Best-effort: never leave a pattern running because the session
                # raised. The firmware also stops on disconnect, so a failure
                # here is not worth propagating over the real error.
                try:
                    if link.is_connected:
                        await link.stop()
                except Exception as exc:  # noqa: BLE001 - diagnostics only
                    LOGGER.debug("stop-on-exit failed: %s", exc)

    async def run_forever(self, session: Callable[[BeltLink], Awaitable[None]], *,
                          max_attempts: Optional[int] = None) -> None:
        """Keep a session running against the belt, reconnecting as needed.

        ``max_attempts`` exists so tests and one-shot CLI modes can bound the
        loop; production callers leave it ``None``.
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
        firmware force-stops on *disconnect*, so wait out the pattern before
        leaving or it would be cut short.
        """
        async def session(link: BeltLink) -> None:
            await link.send(command)
            await asyncio.sleep(command.duration_ms / 1000.0 + 0.1)

        await self.run_forever(session, max_attempts=1)


def add_belt_arguments(parser: Any) -> None:
    """Shared CLI flags, so every entry point stays consistent."""
    parser.add_argument("--device-name", default=DEVICE_NAME,
                        help="BLE advertised name to connect to")
    parser.add_argument("--scan-timeout", type=float, default=5.0)
    parser.add_argument("--reconnect-delay", type=float, default=2.0)


def belt_from_arguments(args: Any) -> BeltClient:
    return BeltClient(device_name=args.device_name,
                      scan_timeout=args.scan_timeout,
                      reconnect_delay=args.reconnect_delay)
