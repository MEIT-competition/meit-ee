from __future__ import annotations
import argparse
import asyncio
from typing import Dict, Optional

from bleak import BleakClient, BleakScanner

from laptop.ai_bridge import run_live_ai, run_mock_ai, warmup_live_ai
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
        # A DIR notify is always sent before its event's own AUDIO chunks
        # (see ble_svc.h), so any assembler state still held under this
        # event_id here can only be a stale, never-completed leftover from
        # an earlier use of the same id. event_id wraps and repeats every
        # 256 events because it's a plain uint8 counter in firmware
        # (main.c's `event_id_ctr`) -- unrelated to EVENT_HISTORY, which
        # only sizes the direction-lookup ring, not this counter.
        #
        # The leftover happens when an event's LAST audio chunk was
        # dropped: the assembler never sees a `last` chunk to trigger its
        # own cleanup, so its partial state (parts + expected chunk index)
        # sits there until the id is reused. Left alone, the new event's
        # first chunk (index 0) will almost always mismatch that stale
        # `expected` count, which AudioAssembler reads as a chunk gap --
        # so the common failure mode is a perfectly good NEW event getting
        # wrongly marked `lost` and discarded in process_events(), not old
        # audio silently reaching the AI (that would additionally need the
        # stale `expected` to coincidentally land back on exactly 0).
        # Reset the state here so a reused event_id always starts clean.
        self.assembler.reset_event(pkt.event_id)
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
            # Pop (not get): dir_by_event is keyed by a uint8 event_id, so
            # it can never hold more than 256 entries either way -- not
            # literally unbounded. The reason to pop here is correctness:
            # without it, an entry that's never claimed (e.g. its own audio
            # gets discarded below for a chunk gap) sits under that
            # event_id until the id wraps back around 256 events later, and
            # a later, unrelated event reusing the same id could then be
            # paired with that stale leftover DIR info instead of its own.
            # Popping on every completion (successful or discarded) keeps
            # this dict scoped to events that are genuinely still in flight.
            dir_info = self.dir_by_event.pop(completed.event_id, None)

            if completed.lost:
                print(
                    f"[AUDIO] event={completed.event_id}: chunk gap detected -> "
                    "discarding before AI"
                )
                continue

            audio = pcm16le_to_float32(completed.pcm_bytes)

            print(
                f"[AUDIO] event={completed.event_id}: "
                f"{len(audio)} samples @ {AI_SAMPLE_RATE} Hz "
                f"({len(audio)/AI_SAMPLE_RATE:.3f} s)"
            )

            try:
                if self.mock_ai:
                    result = run_mock_ai(audio, AI_SAMPLE_RATE, dir_info)
                else:
                    # run_live_ai is expected to eventually call into a real
                    # (synchronous, likely slow) classifier. Calling it
                    # directly here would block this whole event loop --
                    # including BLE notify handling and CMD writes for any
                    # OTHER event already in flight -- for as long as
                    # inference takes. to_thread() keeps that off the loop.
                    result = await asyncio.to_thread(
                        run_live_ai, audio, AI_SAMPLE_RATE, dir_info
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

            try:
                cmd = encode_cmd(
                    event_id=completed.event_id,
                    intensity=result.intensity,
                    sound_class=result.sound_class,
                    pattern=result.pattern,
                )
                await self.client.write_gatt_char(CMD_UUID, cmd, response=True)
            except Exception as exc:
                # A bad AI result shape (encode_cmd) or a BLE write failure/
                # mid-write disconnect (write_gatt_char) must not take the
                # whole worker task down with it -- that would silently stop
                # ALL future events from being processed for the rest of the
                # connection, not just this one.
                print(f"[CMD] event={completed.event_id}: failed to send: {exc}")
                continue
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

async def run_forever(mock_ai: bool, scan_timeout: float, once: bool,
                      retry_delay: float = 2.0):
    # Firmware re-advertises after every disconnect (ble_svc.c), so a dropped
    # link during a demo only needs the laptop to scan and connect again.
    # A connection error is logged and retried rather than killing the script.
    while True:
        try:
            await run(mock_ai, scan_timeout)
        except Exception as exc:
            print(f"[BLE] connection error: {exc}")
        if once:
            return
        print(f"[BLE] disconnected / not found -> retrying in {retry_delay:.0f} s "
              "(Ctrl+C to quit)")
        await asyncio.sleep(retry_delay)

def main():
    p = argparse.ArgumentParser(description="MEIT belt laptop BLE receiver")
    p.add_argument("--mock-ai", action="store_true")
    p.add_argument("--scan-timeout", type=float, default=8.0)
    p.add_argument("--once", action="store_true",
                   help="exit after the first disconnect instead of reconnecting")
    args = p.parse_args()
    if not args.mock_ai:
        print("[AI] loading model (warm-up)...")
        warmup_live_ai(AI_SAMPLE_RATE)
        print("[AI] model ready")
    try:
        asyncio.run(run_forever(args.mock_ai, args.scan_timeout, args.once))
    except KeyboardInterrupt:
        print("\n[BLE] stopped")

if __name__ == "__main__":
    main()
