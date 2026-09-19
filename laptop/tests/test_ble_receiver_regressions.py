"""Regression tests for laptop/ble_receiver.py's Receiver class.

These guard the three fixes made to Receiver:
  1. A new DIR notify resets any stale AudioAssembler state left under the
     same (wrapping, uint8) event_id, instead of letting a reused id inherit
     a previous, never-completed event's partial state.
  2. encode_cmd()/write_gatt_char() failing for one event does not kill the
     process_events() worker task -- the next queued event still gets
     processed.
  3. The live AI path is offloaded via asyncio.to_thread() so a slow,
     synchronous run_live_ai() call cannot block the asyncio event loop
     (the mock AI path is unaffected and stays a direct call).

No real BLE hardware is used anywhere here -- BleakClient is never
constructed; small fakes stand in for it wherever a `client` is needed.
"""
import asyncio
import time

import laptop.ble_receiver as br
from laptop.ai_bridge import AIResult
from laptop.protocol import AudioAssembler, decode_audio_chunk
from laptop.ble_receiver import Receiver


def make_dir(event_id, direction=0, confidence_byte=200, rms_dbfs=-40):
    return bytes([event_id, direction, confidence_byte, rms_dbfs & 0xFF])


def make_chunk(event_id, idx, last, pcm_bytes=b"\x01\x02"):
    return bytes([event_id, idx & 0xFF, 1 if last else 0]) + pcm_bytes


async def _drain(task):
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


class RecordingClient:
    """Fake BLE client: records every CMD payload it's asked to write."""

    def __init__(self):
        self.sent = []

    async def write_gatt_char(self, _uuid, data, response=True):
        self.sent.append(bytes(data))


# --- 1. stale AudioAssembler state reset on a new DIR for a reused id -----

def test_new_dir_resets_stale_audio_assembler_state():
    receiver = Receiver(mock_ai=True)

    # Event 7 (first use): a chunk arrives, but the LAST chunk never does
    # (dropped after retries in firmware) -- state is stuck incomplete.
    receiver.on_audio(None, bytearray(make_chunk(7, 0, last=False, pcm_bytes=b"\xDE\xAD")))

    # 256 events later, event_id 7 is reused for an unrelated new event.
    # Its DIR arrives first, as it always does (see ble_svc.h).
    receiver.on_dir(None, bytearray(make_dir(7)))

    # The new event's own (single, complete) chunk must assemble cleanly:
    # none of the old event's leftover bytes, and no false "lost" flag from
    # the old event's stale expected-chunk-index counter.
    receiver.on_audio(None, bytearray(make_chunk(7, 0, last=True, pcm_bytes=b"\xBE\xEF")))

    assert not receiver.complete_q.empty()
    completed = receiver.complete_q.get_nowait()
    assert completed.pcm_bytes == b"\xBE\xEF"
    assert not completed.lost


def test_without_dir_reset_stale_state_would_taint_the_next_event():
    # Sanity check for the test above: confirms AudioAssembler really does
    # carry stale state forward on its own when nothing resets it (i.e. the
    # underlying issue is the assembler's reuse behavior, and on_dir's
    # reset_event() call is what actually fixes it, not incidental luck).
    a = AudioAssembler()
    a.push(decode_audio_chunk(make_chunk(7, 0, last=False, pcm_bytes=b"\xDE\xAD")))
    completed = a.push(decode_audio_chunk(make_chunk(7, 0, last=True, pcm_bytes=b"\xBE\xEF")))

    assert completed is not None
    # Without an explicit reset, either the old bytes are still mixed in, or
    # the event is wrongly flagged lost -- either way, not the clean "just
    # the new bytes, not lost" result on_dir's reset produces above.
    assert completed.pcm_bytes != b"\xBE\xEF" or completed.lost


# --- 2. a CMD-path failure must not kill the worker task -------------------

def test_write_gatt_char_failure_does_not_kill_worker():
    class FailFirstThenRecordClient(RecordingClient):
        def __init__(self):
            super().__init__()
            self.calls = 0

        async def write_gatt_char(self, uuid, data, response=True):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("simulated BLE write failure")
            await super().write_gatt_char(uuid, data, response)

    async def scenario():
        receiver = Receiver(mock_ai=True)
        client = FailFirstThenRecordClient()
        receiver.client = client

        # Event 1: completes normally, but its CMD write fails.
        receiver.on_dir(None, bytearray(make_dir(1)))
        receiver.on_audio(None, bytearray(make_chunk(1, 0, last=True)))

        # Event 2: an independent event queued right behind it.
        receiver.on_dir(None, bytearray(make_dir(2)))
        receiver.on_audio(None, bytearray(make_chunk(2, 0, last=True)))

        worker = asyncio.create_task(receiver.process_events())
        await asyncio.sleep(0.05)

        assert not worker.done(), "worker task must survive a write_gatt_char failure"
        assert client.calls == 2, "event 2 must still reach write_gatt_char"
        assert len(client.sent) == 1, "event 2's CMD must have gone out despite event 1's failure"

        await _drain(worker)

    asyncio.run(scenario())


def test_encode_cmd_failure_also_does_not_kill_worker(monkeypatch):
    # A bad AI result (an unencodable pattern) fails inside encode_cmd(),
    # never reaching write_gatt_char() -- must be contained the same way.
    calls = {"n": 0}

    def flaky_mock_ai(audio, sr, dir_info):
        calls["n"] += 1
        if calls["n"] == 1:
            return AIResult(intensity=60, sound_class="horn", pattern=[[105, 50]])  # non-10ms -> raises
        return AIResult(intensity=60, sound_class="siren", pattern=[[100, 50]])

    monkeypatch.setattr(br, "run_mock_ai", flaky_mock_ai)

    async def scenario():
        receiver = Receiver(mock_ai=True)
        receiver.client = RecordingClient()

        receiver.on_dir(None, bytearray(make_dir(3)))
        receiver.on_audio(None, bytearray(make_chunk(3, 0, last=True)))
        receiver.on_dir(None, bytearray(make_dir(4)))
        receiver.on_audio(None, bytearray(make_chunk(4, 0, last=True)))

        worker = asyncio.create_task(receiver.process_events())
        await asyncio.sleep(0.05)

        assert not worker.done(), "worker task must survive an encode_cmd() failure"
        assert len(receiver.client.sent) == 1, "event 4's CMD must still have been sent"

        await _drain(worker)

    asyncio.run(scenario())


# --- 3. live AI path is offloaded via asyncio.to_thread ---------------------

def test_live_ai_runs_via_asyncio_to_thread_without_blocking(monkeypatch):
    def slow_live_ai(audio, sr, dir_info):
        time.sleep(0.2)  # simulate a slow, synchronous model call
        return None

    monkeypatch.setattr(br, "run_live_ai", slow_live_ai)

    to_thread_calls = []
    real_to_thread = asyncio.to_thread

    async def spying_to_thread(func, *args, **kwargs):
        to_thread_calls.append(func)
        return await real_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", spying_to_thread)

    async def scenario():
        receiver = Receiver(mock_ai=False)
        receiver.client = object()  # unused: slow_live_ai returns None -> no CMD write

        receiver.on_dir(None, bytearray(make_dir(5)))
        receiver.on_audio(None, bytearray(make_chunk(5, 0, last=True)))

        ticks = 0

        async def ticker():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.01)
                ticks += 1

        worker = asyncio.create_task(receiver.process_events())
        tick_task = asyncio.create_task(ticker())

        # slow_live_ai blocks for 0.2s. If it ran directly on this loop
        # instead of via to_thread, ticker() would be stalled for that
        # whole window and make ~0 progress. Give it a comfortable margin.
        await asyncio.sleep(0.25)

        await _drain(tick_task)
        await _drain(worker)

        assert to_thread_calls == [slow_live_ai], (
            "run_live_ai must be dispatched through asyncio.to_thread()"
        )
        assert ticks >= 5, f"event loop appears blocked: only {ticks} ticks in 0.25s"

    asyncio.run(scenario())


def test_mock_ai_path_does_not_use_to_thread(monkeypatch):
    # The mock path is fast/deterministic and must stay a direct call --
    # confirms the to_thread fix was scoped to the live path only.
    to_thread_calls = []
    real_to_thread = asyncio.to_thread

    async def spying_to_thread(func, *args, **kwargs):
        to_thread_calls.append(func)
        return await real_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", spying_to_thread)

    async def scenario():
        receiver = Receiver(mock_ai=True)
        receiver.client = RecordingClient()

        receiver.on_dir(None, bytearray(make_dir(6)))
        receiver.on_audio(None, bytearray(make_chunk(6, 0, last=True)))

        worker = asyncio.create_task(receiver.process_events())
        await asyncio.sleep(0.05)
        await _drain(worker)

        assert to_thread_calls == []
        assert len(receiver.client.sent) == 1

    asyncio.run(scenario())
