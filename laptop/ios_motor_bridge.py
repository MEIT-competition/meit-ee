"""Bridge the existing meit-ios Windows HTTP server to the ESP32 belt.

No iOS source changes are required.

Pipeline:
    iPhone(s) -> meit-ios bridge/AI -> GET /auto/status
    -> this process -> BLE CMD v2 -> ESP32 -> LEFT/RIGHT vibration motors

Run the meit-ios bridge first, enable Auto in the app, then run this script.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Optional

from laptop.protocol import (
    CMD_UUID,
    DEVICE_NAME,
    DIR_CENTER,
    DIR_LEFT,
    DIR_RIGHT,
    DIR_STOP,
    direction_name,
    encode_motor_cmd,
    normalize_direction,
)

DANGER_LABELS = {"horn", "siren", "crash"}
DEFAULT_PATTERN = ((220, 120), (220, 120), (220, 0))


@dataclass(frozen=True)
class Alert:
    event_id: str
    direction: int
    label: str
    confidence: float


def _http_json(url: str, timeout: float) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        if response.status != 200:
            raise RuntimeError(f"HTTP {response.status}")
        return json.loads(response.read().decode("utf-8"))


def extract_alert(status: dict[str, Any]) -> Optional[Alert]:
    """Return only a completed dangerous event with a usable 3-way direction."""
    event = status.get("last_event")
    if not isinstance(event, dict) or event.get("outcome") != "completed":
        return None

    event_id = event.get("event_id")
    result = event.get("result")
    if not isinstance(event_id, str) or not event_id:
        return None
    if not isinstance(result, dict):
        return None

    label = str(result.get("label", "")).strip().lower()
    # Current meit-ios auto path exposes `danger`; older/manual-compatible
    # responses may omit it.  Never alert a known non-danger class.
    danger = result.get("danger")
    if label not in DANGER_LABELS or danger is False:
        return None

    # Event.direction is the trigger-time direction and is deliberately
    # preferred over a later live direction. This matches meit-ios semantics.
    direction = normalize_direction(event.get("direction", result.get("direction")))
    if direction == DIR_STOP:
        return None

    try:
        confidence = float(result.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    return Alert(event_id, direction, label, confidence)


async def find_belt(timeout: float):
    from bleak import BleakScanner
    devices = await BleakScanner.discover(timeout=timeout)
    for dev in devices:
        if dev.name == DEVICE_NAME:
            return dev
    return None


class IOSMotorBridge:
    def __init__(self, server: str, intensity: int, poll_interval: float,
                 http_timeout: float, scan_timeout: float) -> None:
        self.status_url = server.rstrip("/") + "/auto/status"
        self.intensity = intensity
        self.poll_interval = poll_interval
        self.http_timeout = http_timeout
        self.scan_timeout = scan_timeout
        self.sequence = 0
        self.seen_event_id: Optional[str] = None
        self.initialized = False

    async def read_status(self) -> dict[str, Any]:
        return await asyncio.to_thread(_http_json, self.status_url, self.http_timeout)

    async def initialize_event_cursor(self) -> None:
        """Do not replay the old `last_event` merely because this program started."""
        try:
            status = await self.read_status()
        except Exception as exc:
            print(f"[iOS] initial /auto/status unavailable: {exc}")
            return
        event = status.get("last_event")
        if isinstance(event, dict) and isinstance(event.get("event_id"), str):
            self.seen_event_id = event["event_id"]
            print(f"[iOS] existing event ignored on startup: {self.seen_event_id[:8]}")
        self.initialized = True

    def next_sequence(self) -> int:
        self.sequence = (self.sequence + 1) & 0xFF
        return self.sequence

    async def run_connection(self, client) -> None:
        uuids = {
            char.uuid.lower()
            for service in client.services
            for char in service.characteristics
        }
        if CMD_UUID.lower() not in uuids:
            raise RuntimeError("ESP32 CMD characteristic not found; flash the new motor-only firmware")

        print("[BLE] connected to MEIT-BELT")
        if not self.initialized:
            await self.initialize_event_cursor()

        while client.is_connected:
            try:
                status = await self.read_status()
            except (urllib.error.URLError, TimeoutError, OSError, ValueError, RuntimeError) as exc:
                print(f"[iOS] status error: {exc}")
                await asyncio.sleep(max(0.5, self.poll_interval))
                continue

            if not self.initialized:
                # First successfully observed status establishes the cursor.
                # This prevents replaying an event that finished before this
                # bridge process came online.
                event = status.get("last_event")
                if isinstance(event, dict) and isinstance(event.get("event_id"), str):
                    self.seen_event_id = event["event_id"]
                    print(f"[iOS] existing event ignored on first contact: {self.seen_event_id[:8]}")
                self.initialized = True
                await asyncio.sleep(self.poll_interval)
                continue

            alert = extract_alert(status)
            if alert is not None and alert.event_id != self.seen_event_id:
                seq = self.next_sequence()
                packet = encode_motor_cmd(
                    sequence=seq,
                    direction=alert.direction,
                    intensity=self.intensity,
                    pattern=DEFAULT_PATTERN,
                )
                try:
                    await client.write_gatt_char(CMD_UUID, packet, response=True)
                except Exception as exc:
                    print(f"[BLE] event={alert.event_id[:8]} write failed: {exc}")
                    raise

                # Only consume the retained event after the ESP32 accepts the
                # GATT write. If BLE drops during the write, reconnect and retry
                # this same /auto/status event instead of silently losing it.
                self.seen_event_id = alert.event_id
                print(
                    f"[ALERT] event={alert.event_id[:8]} class={alert.label} "
                    f"conf={alert.confidence:.3f} direction={direction_name(alert.direction)} "
                    f"seq={seq}"
                )

            await asyncio.sleep(self.poll_interval)

    async def run_forever(self) -> None:
        while True:
            print(f"[SCAN] looking for {DEVICE_NAME}...")
            device = await find_belt(self.scan_timeout)
            if device is None:
                print("[SCAN] belt not found; retrying")
                await asyncio.sleep(2.0)
                continue
            try:
                from bleak import BleakClient
                async with BleakClient(device) as client:
                    await self.run_connection(client)
            except Exception as exc:
                print(f"[BLE] disconnected/error: {exc}; reconnecting")
                await asyncio.sleep(2.0)


def main() -> None:
    p = argparse.ArgumentParser(description="MEIT iOS bridge -> 2-motor ESP32 belt")
    p.add_argument("--server", default="http://127.0.0.1:8765",
                   help="meit-ios Windows bridge base URL")
    p.add_argument("--intensity", type=int, default=75)
    p.add_argument("--poll-ms", type=int, default=100,
                   help="/auto/status polling interval")
    p.add_argument("--http-timeout", type=float, default=1.0)
    p.add_argument("--scan-timeout", type=float, default=5.0)
    args = p.parse_args()

    if not 1 <= args.intensity <= 100:
        p.error("--intensity must be 1..100")
    if args.poll_ms < 50:
        p.error("--poll-ms must be >= 50")

    bridge = IOSMotorBridge(
        server=args.server,
        intensity=args.intensity,
        poll_interval=args.poll_ms / 1000.0,
        http_timeout=args.http_timeout,
        scan_timeout=args.scan_timeout,
    )
    try:
        asyncio.run(bridge.run_forever())
    except KeyboardInterrupt:
        print("\n[STOP] bridge stopped")


if __name__ == "__main__":
    main()
