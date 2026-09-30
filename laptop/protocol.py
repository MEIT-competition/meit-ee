"""MEIT EE laptop <-> belt BLE command protocol (CMD v2).

Responsibility split
--------------------
The iPhone and the laptop own microphone capture, the stereo direction estimate
and the ``meit-ai`` danger-sound inference. The ESP32 belt is a *motor-only* BLE
peripheral: it receives a finished haptic command and plays it. This module is
only the wire format between the two.

Three directions
----------------
iOS reports ``left``, ``right``, ``center`` or ``unavailable``
(``StereoDirectionEstimator.swift``), so the belt renders exactly three
sensations: left motor, right motor, or both motors together. There is no rear
cue, because a stereo pair cannot tell front from back and the system has no
rear sensor.

That means one motor mask applies to a whole pattern, which is what CMD v2
carries. A belt already flashed with firmware that also understands the older
4-direction CMD v3 accepts these packets unchanged, so no reflash is needed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Sequence, Tuple

# ---------------------------------------------------------------- BLE identity
# Unchanged across every firmware revision so the laptop needs no
# device-specific discovery logic and older belts stay discoverable.
DEVICE_NAME = "MEIT-BELT"
SERVICE_UUID = "01000000-1d9e-218f-9a4b-9c4e302a9d11"
CMD_UUID = "04000000-1d9e-218f-9a4b-9c4e302a9d11"

# ---------------------------------------------------------------- wire framing
CMD_MAGIC = 0xA5
CMD_VERSION = 0x02

# Direction codes on the wire.
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
ALERT_DIRECTIONS = (DIR_LEFT, DIR_CENTER, DIR_RIGHT)

# Motor mask bits. Bit 0 = LEFT, bit 1 = RIGHT, matching firmware config.h.
MASK_LEFT = 0b01
MASK_RIGHT = 0b10
MASK_BOTH = MASK_LEFT | MASK_RIGHT

DIRECTION_MASKS = {
    DIR_LEFT: MASK_LEFT,
    DIR_CENTER: MASK_BOTH,
    DIR_RIGHT: MASK_RIGHT,
}

# A 6-byte header plus 2 bytes per step. The longest pattern in use is a siren's
# three pulses, so 4 leaves headroom and the worst case is 14 bytes — well
# inside the 20 payload bytes a Write Request carries on the default 23-byte ATT
# MTU, so an alert never depends on MTU negotiation.
PATTERN_MAX_STEPS = 4
HEADER_BYTES = 6
MAX_PACKET_BYTES = HEADER_BYTES + 2 * PATTERN_MAX_STEPS

# Timings are transmitted in units of 10 ms, one unsigned byte each.
STEP_TIME_UNIT_MS = 10
STEP_TIME_MAX_MS = 255 * STEP_TIME_UNIT_MS  # 2550 ms


class ProtocolError(ValueError):
    """Raised for any packet that must not reach the motors."""


# --------------------------------------------------------------- direction I/O
def normalize_direction(value: object) -> int:
    """Map a reported direction onto a wire direction code.

    iOS stereo reports ``left``, ``right``, ``center`` or ``unavailable``.
    ``unavailable`` and anything unrecognised return ``DIR_STOP``, which callers
    must treat as *suppress the alert* rather than as *stop the motors*: telling
    the wearer "danger, somewhere" is worse than staying silent, and iOS has
    already decided the direction is not trustworthy.

    ``front`` and ``centre`` are accepted as spellings of ``center``. ``back`` is
    deliberately **not** accepted: the belt has no way to point behind the wearer,
    so it declines rather than pointing somewhere it does not mean.
    """
    if value is None:
        return DIR_STOP
    text = str(value).strip().lower()
    if text == "left":
        return DIR_LEFT
    if text == "right":
        return DIR_RIGHT
    if text in {"center", "centre", "front"}:
        return DIR_CENTER
    return DIR_STOP


def direction_name(direction: int) -> str:
    try:
        return DIRECTION_NAMES[direction]
    except KeyError:
        raise ProtocolError(f"invalid direction code: {direction!r}") from None


def mask_for_direction(direction: int) -> int:
    """Which motors an alert direction drives."""
    try:
        return DIRECTION_MASKS[direction]
    except KeyError:
        raise ProtocolError(f"{direction!r} drives no motors") from None


# ------------------------------------------------------------------ data model
@dataclass(frozen=True)
class MotorStep:
    """One ON burst followed by an OFF gap, in milliseconds.

    Which motors run is fixed for the whole pattern by the command's direction,
    so a step carries only timing.
    """

    on_ms: int
    off_ms: int

    def validate(self) -> None:
        for name, value, minimum in (("on_ms", self.on_ms, STEP_TIME_UNIT_MS),
                                     ("off_ms", self.off_ms, 0)):
            if not isinstance(value, int):
                raise ProtocolError(f"{name} must be an int (got {value!r})")
            if value < minimum:
                raise ProtocolError(f"{name} must be >= {minimum} (got {value})")
            if value > STEP_TIME_MAX_MS:
                raise ProtocolError(f"{name} must be <= {STEP_TIME_MAX_MS} (got {value})")
            if value % STEP_TIME_UNIT_MS:
                raise ProtocolError(f"{name} must be a multiple of {STEP_TIME_UNIT_MS} ms "
                                    f"(got {value})")

    @property
    def total_ms(self) -> int:
        return self.on_ms + self.off_ms


@dataclass(frozen=True)
class MotorCommand:
    """A complete, already-decided haptic command."""

    direction: int
    intensity: int
    steps: Tuple[MotorStep, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "steps", tuple(self.steps))
        self.validate()

    def validate(self) -> None:
        if self.direction not in DIRECTION_NAMES:
            raise ProtocolError(f"invalid direction code: {self.direction!r}")
        if not isinstance(self.intensity, int):
            raise ProtocolError("intensity must be an int")

        if self.direction == DIR_STOP:
            if self.steps:
                raise ProtocolError("STOP must not carry a pattern")
            if self.intensity != 0:
                raise ProtocolError("STOP must carry intensity 0")
            return

        if not 1 <= self.intensity <= 100:
            raise ProtocolError(f"intensity must be 1..100 (got {self.intensity})")
        if not 1 <= len(self.steps) <= PATTERN_MAX_STEPS:
            raise ProtocolError(f"pattern must hold 1..{PATTERN_MAX_STEPS} steps "
                                f"(got {len(self.steps)})")
        for step in self.steps:
            step.validate()

    @property
    def duration_ms(self) -> int:
        """Wall-clock length of the pattern, trailing gap included."""
        return sum(step.total_ms for step in self.steps)

    @property
    def is_stop(self) -> bool:
        return self.direction == DIR_STOP

    @property
    def mask(self) -> int:
        """Which motors this command drives; 0 for STOP."""
        return 0 if self.is_stop else mask_for_direction(self.direction)

    def describe(self) -> str:
        if self.is_stop:
            return "STOP"
        where = {MASK_LEFT: "L", MASK_RIGHT: "R", MASK_BOTH: "LR"}[self.mask]
        shape = " ".join(f"{s.on_ms}+{s.off_ms}" for s in self.steps)
        return (f"{direction_name(self.direction)} int={self.intensity} "
                f"{self.duration_ms}ms {where}[{shape}]")


STOP_COMMAND = MotorCommand(direction=DIR_STOP, intensity=0, steps=())


@dataclass(frozen=True)
class DecodedCommand:
    version: int
    sequence: int
    command: MotorCommand


# ----------------------------------------------------------------- encoding
def encode(sequence: int, command: MotorCommand) -> bytes:
    """Encode CMD v2.

    ::

        [0]     magic      0xA5
        [1]     version    0x02
        [2]     sequence   uint8, trace/correlation only
        [3]     direction  0 STOP, 1 LEFT, 2 CENTER, 3 RIGHT
        [4]     intensity  0..100 percent (0 only for STOP)
        [5]     n_steps    0..4 (0 only for STOP)
        [6+2i]  on_ms  / 10   (1..255)
        [7+2i]  off_ms / 10   (0..255)
    """
    if not isinstance(sequence, int) or not 0 <= sequence <= 255:
        raise ProtocolError(f"sequence must be a uint8 (got {sequence!r})")
    command.validate()

    if command.is_stop:
        return bytes([CMD_MAGIC, CMD_VERSION, sequence, DIR_STOP, 0, 0])

    out = bytearray([CMD_MAGIC, CMD_VERSION, sequence, command.direction,
                     command.intensity, len(command.steps)])
    for step in command.steps:
        out.append(step.on_ms // STEP_TIME_UNIT_MS)
        out.append(step.off_ms // STEP_TIME_UNIT_MS)
    assert len(out) <= MAX_PACKET_BYTES, "packet exceeded the single-write budget"
    return bytes(out)


# ----------------------------------------------------------------- decoding
def decode(data: bytes) -> DecodedCommand:
    """Decode a packet, applying the same checks as the firmware.

    Kept strict and symmetric with the ESP32 GATT handler so a unit test here is
    evidence about the device, not only about Python.
    """
    if len(data) < HEADER_BYTES:
        raise ProtocolError("CMD packet too short")
    if data[0] != CMD_MAGIC:
        raise ProtocolError(f"bad CMD magic: 0x{data[0]:02x}")
    if data[1] != CMD_VERSION:
        raise ProtocolError(f"unsupported CMD version: 0x{data[1]:02x}")

    sequence, direction, intensity, n_steps = data[2], data[3], data[4], data[5]
    if direction not in DIRECTION_NAMES:
        raise ProtocolError(f"invalid direction code: {direction}")
    if intensity > 100:
        raise ProtocolError(f"invalid intensity: {intensity}")
    if n_steps > PATTERN_MAX_STEPS:
        raise ProtocolError(f"pattern holds more than {PATTERN_MAX_STEPS} steps")
    if len(data) != HEADER_BYTES + 2 * n_steps:
        raise ProtocolError("CMD length disagrees with the step count")

    if direction == DIR_STOP:
        if n_steps or intensity:
            raise ProtocolError("STOP must carry no pattern and zero intensity")
        return DecodedCommand(CMD_VERSION, sequence, STOP_COMMAND)
    if n_steps == 0:
        raise ProtocolError("a motor command needs at least one step")

    steps: List[MotorStep] = []
    for index in range(n_steps):
        on_raw = data[HEADER_BYTES + 2 * index]
        off_raw = data[HEADER_BYTES + 2 * index + 1]
        if on_raw == 0:
            raise ProtocolError("a step cannot have a zero ON time")
        steps.append(MotorStep(on_ms=on_raw * STEP_TIME_UNIT_MS,
                               off_ms=off_raw * STEP_TIME_UNIT_MS))

    return DecodedCommand(CMD_VERSION, sequence,
                          MotorCommand(direction=direction, intensity=intensity,
                                       steps=tuple(steps)))


def steps_from_pairs(pairs: Iterable[Sequence[int]]) -> Tuple[MotorStep, ...]:
    """Convenience builder from ``[[on_ms, off_ms], ...]``."""
    return tuple(MotorStep(on_ms=int(on), off_ms=int(off)) for on, off in pairs)
