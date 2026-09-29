"""Direct LEFT/CENTER/RIGHT motor test over BLE, no iPhone or AI required."""
from __future__ import annotations
import argparse
import asyncio

from laptop.protocol import (
    CMD_UUID, DEVICE_NAME, DIR_CENTER, DIR_LEFT, DIR_RIGHT, DIR_STOP,
    encode_motor_cmd,
)

DIRECTIONS = {"left": DIR_LEFT, "center": DIR_CENTER, "right": DIR_RIGHT, "stop": DIR_STOP}
PATTERN = ((300, 100), (300, 0))


async def main_async(direction: int, intensity: int, scan_timeout: float) -> None:
    from bleak import BleakClient, BleakScanner
    print(f"[SCAN] looking for {DEVICE_NAME}...")
    devices = await BleakScanner.discover(timeout=scan_timeout)
    dev = next((d for d in devices if d.name == DEVICE_NAME), None)
    if dev is None:
        raise SystemExit("MEIT-BELT not found")
    async with BleakClient(dev) as client:
        pattern = () if direction == DIR_STOP else PATTERN
        command_intensity = 0 if direction == DIR_STOP else intensity
        packet = encode_motor_cmd(1, direction, command_intensity, pattern)
        await client.write_gatt_char(CMD_UUID, packet, response=True)
        print("[OK] motor test command sent")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("direction", choices=sorted(DIRECTIONS))
    p.add_argument("--intensity", type=int, default=60)
    p.add_argument("--scan-timeout", type=float, default=5.0)
    args = p.parse_args()
    if not 1 <= args.intensity <= 100:
        p.error("--intensity must be 1..100")
    asyncio.run(main_async(DIRECTIONS[args.direction], args.intensity, args.scan_timeout))


if __name__ == "__main__":
    main()
