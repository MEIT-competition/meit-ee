"""MEIT EE laptop <-> belt BLE command protocol.

The iPhone/Windows bridge owns microphone capture, direction estimation and AI.
The ESP32 belt is intentionally a *motor-only* BLE peripheral.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence

DEVICE_NAME = "MEIT-BELT"
SERVICE_UUID = "01000000-1d9e-218f-9a4b-9c4e302a9d11"
CMD_UUID = "04000000-1d9e-218f-9a4b-9c4e302a9d11"

CMD_MAGIC = 0xA5
CMD_VERSION = 0x02

DIR_STOP = 0
DIR_LEFT = 1
DIR_CENTER = 2
DIR_RIGHT = 3
DIRECTION_NAMES = {
    DIR_STOP: "STOP",
    DIR_LEFT: "LEFT",
    DIR_CENTER: "CENTER",
    DIR_RIGHT: "RIGHT",
}

# Kept intentionally small enough for a normal BLE write without fragmentation.
PATTERN_MAX_PAIRS = 4


@dataclass(frozen=True)
class MotorCommand:
    sequence: int
    direction: int
    intensity: int
    pattern: Sequence[Sequence[int]]


def normalize_direction(value: object) -> int:
    """Normalize iOS bridge direction strings to the 3-motor-direction contract.

    Current iOS main can report front/right/back/left/unknown.  The planned
    3-way estimator can report left/center/right.  The belt therefore accepts
    both without requiring an iOS change.

    FRONT and BACK intentionally collapse to CENTER because the wearable has
    only LEFT/RIGHT actuators and the product requirement is 3-way feedback.
    """
    if value is None:
        return DIR_STOP
    s = str(value).strip().lower()
    if s == "left":
        return DIR_LEFT
    if s == "right":
        return DIR_RIGHT
    if s in {"center", "centre", "front", "back"}:
        return DIR_CENTER
    return DIR_STOP


def direction_name(direction: int) -> str:
    try:
        return DIRECTION_NAMES[direction]
    except KeyError as exc:
        raise ValueError(f"invalid direction: {direction}") from exc


def encode_motor_cmd(sequence: int, direction: int, intensity: int,
                     pattern: Sequence[Sequence[int]]) -> bytes:
    """Encode BLE CMD v2.

    Wire format:
      [0] magic      = 0xA5
      [1] version    = 0x02
      [2] sequence   = uint8 trace/correlation value
      [3] direction  = 0 STOP, 1 LEFT, 2 CENTER, 3 RIGHT
      [4] intensity  = 0..100 percent
      [5] n_pairs    = 0..4 (0 is valid only for STOP)
      then n x {on_ms/10, off_ms/10}
    """
    if not 0 <= sequence <= 255:
        raise ValueError("sequence must fit uint8")
    if direction not in DIRECTION_NAMES:
        raise ValueError("direction must be STOP/LEFT/CENTER/RIGHT")
    if not 0 <= intensity <= 100:
        raise ValueError("intensity must be 0..100")

    if direction == DIR_STOP:
        if pattern:
            raise ValueError("STOP command must not include a pattern")
        return bytes([CMD_MAGIC, CMD_VERSION, sequence, DIR_STOP, 0, 0])

    if not 1 <= len(pattern) <= PATTERN_MAX_PAIRS:
        raise ValueError(f"pattern must contain 1..{PATTERN_MAX_PAIRS} pairs")

    out = bytearray([CMD_MAGIC, CMD_VERSION, sequence, direction, intensity, len(pattern)])
    for pair in pattern:
        if len(pair) != 2:
            raise ValueError("each pattern entry must be [on_ms, off_ms]")
        on_ms, off_ms = int(pair[0]), int(pair[1])
        if on_ms < 10 or off_ms < 0:
            raise ValueError("on_ms must be >=10 and off_ms must be >=0")
        if on_ms % 10 or off_ms % 10:
            raise ValueError("pattern times must be multiples of 10 ms")
        if on_ms > 2550 or off_ms > 2550:
            raise ValueError("pattern time exceeds 2550 ms")
        out.extend((on_ms // 10, off_ms // 10))
    return bytes(out)


def decode_motor_cmd(data: bytes) -> MotorCommand:
    if len(data) < 6:
        raise ValueError("CMD packet too short")
    magic, version, sequence, direction, intensity, n_pairs = data[:6]
    if magic != CMD_MAGIC or version != CMD_VERSION:
        raise ValueError("invalid CMD magic/version")
    if direction not in DIRECTION_NAMES:
        raise ValueError("invalid direction")
    if intensity > 100:
        raise ValueError("invalid intensity")
    if n_pairs > PATTERN_MAX_PAIRS or len(data) != 6 + 2 * n_pairs:
        raise ValueError("invalid pattern length")
    if direction == DIR_STOP:
        if intensity != 0 or n_pairs != 0:
            raise ValueError("STOP must have zero intensity and zero pattern pairs")
    elif n_pairs == 0:
        raise ValueError("motor command requires at least one pattern pair")

    pattern: List[List[int]] = []
    for i in range(n_pairs):
        pattern.append([data[6 + 2*i] * 10, data[7 + 2*i] * 10])
    return MotorCommand(sequence, direction, intensity, pattern)
