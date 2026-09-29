import re
from pathlib import Path
import pytest
from laptop.protocol import (DIRECTION_NAMES, direction_name, decode_dir_packet,
                             DIR_LEFT, DIR_RIGHT, DIR_BACK)

def test_direction_convention():
    assert DIRECTION_NAMES == {6: "LEFT", 2: "RIGHT", 4: "BACK"}
    for wire, name in DIRECTION_NAMES.items():
        assert direction_name(wire) == name
        assert decode_dir_packet(bytes([1, wire, 200, 216])).direction == wire
    assert direction_name(-1) == "UNKNOWN"

@pytest.mark.parametrize('wire', [0, 1, 3, 5, 7, 8, 254])
def test_retired_direction_rejected(wire):
    with pytest.raises(ValueError):
        decode_dir_packet(bytes([1, wire, 200, 216]))
    with pytest.raises(ValueError):
        direction_name(wire)

def test_firmware_wire_contract():
    text = (Path(__file__).resolve().parents[2] / 'firmware/main/direction.h').read_text()
    for name, value in [('LEFT', DIR_LEFT), ('RIGHT', DIR_RIGHT), ('BACK', DIR_BACK)]:
        assert int(re.search(r'DIR_WIRE_' + name + r'\s*=\s*(\d+)', text)[1]) == value

def test_display_unknown_is_not_back():
    import display_server as ds
    for wire, name in [(-1, "UNKNOWN"), (0, "UNKNOWN"), (DIR_BACK, "BACK"),
                       (DIR_LEFT, "LEFT"), (DIR_RIGHT, "RIGHT")]:
        ds.show('horn', wire, 60)
        assert ds._state['direction_name'] == name
