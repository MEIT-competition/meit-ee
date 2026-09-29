import numpy as np
from laptop.protocol import AudioAssembler, decode_audio_chunk, pcm16le_to_float32

def make_chunk(event_id, idx, last, pcm_bytes):
    return bytes([event_id, idx & 0xFF, 1 if last else 0]) + pcm_bytes

def test_audio_reassembly_exact():
    pcm = (np.arange(1000, dtype=np.int16) - 500).astype("<i2").tobytes()
    parts = [pcm[:600], pcm[600:1200], pcm[1200:]]
    a = AudioAssembler()
    result = None
    for i, part in enumerate(parts):
        result = a.push(decode_audio_chunk(
            make_chunk(3, i, i == len(parts)-1, part)
        ))
    assert result is not None
    assert not result.lost
    assert result.pcm_bytes == pcm

def test_gap_detected():
    a = AudioAssembler()
    p0 = (np.arange(50, dtype=np.int16)).astype("<i2").tobytes()
    p2 = (np.arange(50,100, dtype=np.int16)).astype("<i2").tobytes()
    assert a.push(decode_audio_chunk(make_chunk(5,0,False,p0))) is None
    result = a.push(decode_audio_chunk(make_chunk(5,2,True,p2)))
    assert result is not None
    assert result.lost

def test_chunk_index_wraparound():
    a = AudioAssembler()
    result = None
    for i in range(258):
        one = int(i % 32767).to_bytes(2, "little", signed=True)
        result = a.push(decode_audio_chunk(
            make_chunk(8, i & 0xFF, i == 257, one)
        ))
    assert result is not None
    assert not result.lost
    assert len(result.pcm_bytes) == 516

def test_pcm16_conversion():
    raw = np.array([-32768, 0, 32767], dtype="<i2").tobytes()
    x = pcm16le_to_float32(raw)
    assert np.isclose(x[0], -1.0)
    assert np.isclose(x[1], 0.0)
    assert 0.999 < x[2] < 1.0
