from __future__ import annotations
import argparse
import asyncio
from typing import Dict, Optional

from bleak import BleakClient, BleakScanner

from laptop.ai_bridge import run_live_ai, run_mock_ai
from laptop.protocol import (
    AUDIO_UUID, CMD_UUID, DEVICE_NAME, DIR_UUID,
    AudioAssembler, decode_audio_chunk, decode_dir_packet,
    direction_name, encode_cmd, pcm16le_to_float32,
)

AI_SAMPLE_RATE = 16000

class Receiver:
    def __init__(self, mock_ai: bool = False) -> None:
        self.mock_ai = mock_ai
        self.assembler = AudioAssembler()
        self.dir_by_event: Dict[int, object] = {}
        self.complete_q: asyncio.Queue = asyncio.Queue()
        self.client: Optional[BleakClient] = None

    def on_dir(self, _sender, data: bytearray) -> None:
        try:
            pkt = decode_dir_packet(bytes(data))
        except Exception as exc:
            print(f"[DIR] decode error: {exc}")
            return
        self.dir_by_event[pkt.event_id] = pkt
        print(
            f"[DIR] event={pkt.event_id} "
            f"dir={direction_name(pkt.direction)}({pkt.direction}) "
            f"conf={pkt.confidence:.3f} rms={pkt.rms_dbfs} dBFS"
        )

    def on_audio(self, _sender, data: bytearray) -> None:
        try:
            chunk = decode_audio_chunk(bytes(data))
            completed = self.assembler.push(chunk)
        except Exception as exc:
            print(f"[AUDIO] decode/reassembly error: {exc}")
            return
        if completed is not None:
            self.complete_q.put_nowait(completed)

    async def process_events(self) -> None:
        assert self.client is not None
        while True:
            completed = await self.complete_q.get()

            if completed.lost:
                print(
                    f"[AUDIO] event={completed.event_id}: chunk gap detected -> "
                    "discarding before AI"
                )
                continue

            audio = pcm16le_to_float32(completed.pcm_bytes)
            dir_info = self.dir_by_event.get(completed.event_id, None)

            print(
                f"[AUDIO] event={completed.event_id}: "
                f"{len(audio)} samples @ {AI_SAMPLE_RATE} Hz "
                f"({len(audio)/AI_SAMPLE_RATE:.3f} s)"
            )

            try:
                result = (
                    run_mock_ai(audio, AI_SAMPLE_RATE, dir_info)
                    if self.mock_ai
                    else run_live_ai(audio, AI_SAMPLE_RATE, dir_info)
                )
            except NotImplementedError as exc:
                print(f"[AI] {exc}")
                print("[AI] no CMD sent")
                continue
            except Exception as exc:
                print(f"[AI] failure: {exc}")
                continue

            if result is None:
                print("[AI] normal/below gate -> no CMD")
                continue

            cmd = encode_cmd(
                event_id=completed.event_id,
                intensity=result.intensity,
                sound_class=result.sound_class,
                pattern=result.pattern,
            )
            await self.client.write_gatt_char(CMD_UUID, cmd, response=True)
            print(f"[CMD] event={completed.event_id} bytes={list(cmd)}")

async def find_belt(timeout: float):
    print(f"[SCAN] looking for {DEVICE_NAME!r}...")
    devices = await BleakScanner.discover(timeout=timeout)
    for dev in devices:
        if dev.name == DEVICE_NAME:
            print(f"[SCAN] found {dev.name} @ {dev.address}")
            return dev
    print("[SCAN] device not found")
    return None

def list_gatt(client: BleakClient) -> None:
    print("[GATT] services / characteristics")
    for service in client.services:
        print(f"  service {service.uuid}")
        for char in service.characteristics:
            print(f"    char {char.uuid} props={list(char.properties)}")

async def run(mock_ai: bool, scan_timeout: float):
    device = await find_belt(scan_timeout)
    if device is None:
        return

    async with BleakClient(device) as client:
        print(f"[BLE] connected={client.is_connected}")
        list_gatt(client)

        available = {
            char.uuid.lower()
            for service in client.services
            for char in service.characteristics
        }
        required = {AUDIO_UUID.lower(), DIR_UUID.lower(), CMD_UUID.lower()}
        missing = required - available
        if missing:
            print("[BLE] expected characteristic UUID(s) missing:")
            for uuid in sorted(missing):
                print("  ", uuid)
            print("Verify UUID byte order against firmware/main/ble_svc.c.")
            return

        receiver = Receiver(mock_ai=mock_ai)
        receiver.client = client

        await client.start_notify(DIR_UUID, receiver.on_dir)
        await client.start_notify(AUDIO_UUID, receiver.on_audio)
        print("[BLE] subscribed to DIR + AUDIO")

        worker = asyncio.create_task(receiver.process_events())
        try:
            while client.is_connected:
                await asyncio.sleep(1.0)
        finally:
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass

def main():
    p = argparse.ArgumentParser(description="MEIT belt laptop BLE receiver")
    p.add_argument("--mock-ai", action="store_true")
    p.add_argument("--scan-timeout", type=float, default=8.0)
    args = p.parse_args()
    asyncio.run(run(args.mock_ai, args.scan_timeout))

if __name__ == "__main__":
    main()
