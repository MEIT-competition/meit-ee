import pytest
from laptop.protocol import encode_cmd, decode_cmd_packet

def test_cmd_packet_matches_protocol_example():
    pkt = encode_cmd(
        event_id=7,
        intensity=85,
        sound_class="siren",
        pattern=[[100, 50], [100, 0]],
    )
    assert list(pkt) == [7, 85, 1, 2, 10, 5, 10, 0]
    decoded = decode_cmd_packet(pkt)
    assert decoded["event_id"] == 7
    assert decoded["intensity"] == 85
    assert decoded["sound_class"] == "siren"
    assert decoded["pattern"] == [[100, 50], [100, 0]]

def test_non_10ms_pattern_rejected():
    with pytest.raises(ValueError):
        encode_cmd(1, 60, "horn", [[105, 50]])
