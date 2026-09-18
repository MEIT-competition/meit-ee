from laptop.protocol import DIRECTION_NAMES, direction_name

def test_direction_convention():
    expected = [
        "FRONT","FRONT_RIGHT","RIGHT","BACK_RIGHT",
        "BACK","BACK_LEFT","LEFT","FRONT_LEFT"
    ]
    assert DIRECTION_NAMES == expected
    for i, name in enumerate(expected):
        assert direction_name(i) == name
    assert direction_name(-1) == "UNKNOWN"
