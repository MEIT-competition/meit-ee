import pytest
from laptop.protocol import decode_dir_packet, direction_name

def test_dir_packet_normal():
    pkt = bytes([5, 2, 255, (-40) & 0xFF])
    out = decode_dir_packet(pkt)
    assert out.event_id == 5
    assert out.direction == 2
    assert out.confidence == 1.0
    assert out.rms_dbfs == -40
    assert direction_name(out.direction) == "RIGHT"

def test_dir_packet_unknown():
    pkt = bytes([9, 0xFF, 0, (-60) & 0xFF])
    out = decode_dir_packet(pkt)
    assert out.direction == -1
    assert out.confidence == 0.0
    assert direction_name(out.direction) == "UNKNOWN"

def test_unknown_requires_zero_confidence():
    with pytest.raises(ValueError):
        decode_dir_packet(bytes([1, 0xFF, 10, (-50) & 0xFF]))
