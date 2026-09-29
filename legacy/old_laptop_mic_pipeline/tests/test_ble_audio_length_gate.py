"""Regression: only an exact 40960-sample / 81920-byte AUDIO clip reaches AI.

chunk_index is a uint8 and wraps 255 -> 0 inside one normal clip (342 chunks
at MTU 247). A receiver that subscribes mid-event can therefore see the
clip's SECOND index 0 (chunk 256) as a clean start and assemble chunks
256..341 without any gap: 20480 bytes = 10240 samples = 0.64 s. That
truncated clip must be discarded before AI. A full clip whose index wraps
255 -> 0 is normal and must still reach AI.

Chunk layout mirrors firmware/main/ble_svc.c at MTU 247:
payload budget = 247 - 3 (ATT) - 3 (header) = 241 -> 120 samples/chunk.
"""
import asyncio

import numpy as np

import laptop.ble_receiver as br
from laptop.ai_bridge import AIResult
from laptop.protocol import AUDIO_CLIP_BYTES, AUDIO_CLIP_SAMPLES, DIR_BACK
from laptop.ble_receiver import Receiver

MTU = 247
SAMPLES_PER_CHUNK = (MTU - 3 - 3) // 2


def clip_chunks(event_id):
    pcm = (np.arange(AUDIO_CLIP_SAMPLES) % 2000 - 1000).astype("<i2").tobytes()
    step = SAMPLES_PER_CHUNK * 2
    parts = [pcm[i:i + step] for i in range(0, len(pcm), step)]
    return [bytes([event_id, idx & 0xFF, 1 if idx == len(parts) - 1 else 0]) + part
            for idx, part in enumerate(parts)]


def run_scenario(monkeypatch, feed):
    ai_calls = []

    def recording_mock_ai(audio, sr, dir_info):
        ai_calls.append(len(audio))
        return AIResult(intensity=60, sound_class="siren", pattern=[[100, 0]])

    monkeypatch.setattr(br, "run_mock_ai", recording_mock_ai)

    class Client:
        def __init__(self):
            self.sent = []

        async def write_gatt_char(self, _uuid, data, response=True):
            self.sent.append(bytes(data))

    async def scenario():
        receiver = Receiver(mock_ai=True)
        receiver.client = Client()
        feed(receiver)
        worker = asyncio.create_task(receiver.process_events())
        await asyncio.sleep(0.05)
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass
        return receiver.client.sent

    sent = asyncio.run(scenario())
    return ai_calls, sent


def test_contract_constants():
    assert AUDIO_CLIP_SAMPLES == 40960
    assert AUDIO_CLIP_BYTES == 81920
    assert len(clip_chunks(0)) == 342


def test_full_clip_with_index_wrap_reaches_ai(monkeypatch):
    chunks = clip_chunks(9)
    assert [c[1] for c in chunks[254:258]] == [254, 255, 0, 1]

    def feed(receiver):
        receiver.on_dir(None, bytearray([9, DIR_BACK, 200, (-30) & 0xFF]))
        for c in chunks:
            receiver.on_audio(None, bytearray(c))

    ai_calls, sent = run_scenario(monkeypatch, feed)
    assert ai_calls == [AUDIO_CLIP_SAMPLES]
    assert len(sent) == 1


def test_mid_event_join_at_second_index0_is_not_sent_to_ai(monkeypatch):
    chunks = clip_chunks(11)
    tail = chunks[256:]            # starts at the clip's second chunk_index 0
    assert tail[0][1] == 0
    assert sum(len(c) - 3 for c in tail) // 2 == 10240

    def feed(receiver):
        # Receiver subscribed mid-event: no DIR, first chunk seen is index 0.
        for c in tail:
            receiver.on_audio(None, bytearray(c))

    ai_calls, sent = run_scenario(monkeypatch, feed)
    assert ai_calls == [], "truncated 10240-sample clip must not reach AI"
    assert sent == []
