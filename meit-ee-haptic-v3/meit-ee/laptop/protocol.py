"""MEIT EE laptop <-> belt BLE command protocol (CMD v2 and CMD v3).

Responsibility split
--------------------
The iPhones and the ``meit-ios`` laptop bridge own microphone capture, the
4-role direction selection and the ``meit-ai`` danger-sound inference.  The
ESP32 belt is a *motor-only* BLE peripheral: it receives a finished haptic
command and plays it.  This module is only the wire format between the two.

Why a v3 exists
---------------
``meit-ios`` reports one of four directions (``front``, ``right``, ``back``,
``left``) or ``unknown``.  CMD v2 carries a single motor mask for the whole
pattern, so with two actuators it can only express LEFT / BOTH / RIGHT and
``front``/``back`` had to collapse into the same sensation.  CMD v3 carries a
motor mask **per pattern step**, which lets ``back`` be rendered as a
left-to-right sweep and keeps all four directions distinguishable.  See
``docs/HAPTIC_DESIGN.md``.

v2 remains encodable and the firmware still accepts it, so a belt that has not
been reflashed keeps working in 3-direction mode.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Sequence, Tuple

# ---------------------------------------------------------------- BLE identity
# Unchanged from the previous firmware so the laptop needs no device-specific
# discovery logic and older belts stay discoverable.
DEVICE_NAME = "MEIT-BELT"
SERVICE_UUID = "01000000-1d9e-218f-9a4b-9c4e302a9d11"
CMD_UUID = "04000000-1d9e-218f-9a4b-9c4e302a9d11"

# ---------------------------------------------------------------- wire framing
CMD_MAGIC = 0xA5
CMD_VERSION_V2 = 0x02
CMD_VERSION_V3 = 0x03
SUPPORTED_VERSIONS = (CMD_VERSION_V2, CMD_VERSION_V3)

# Direction codes on the wire.  1/2/3 keep their v2 meaning so a v2 decoder
# reading a v3 direction byte still sees a sane value; 4 (BACK) is v3-only.
DIR_STOP = 0
DIR_LEFT = 1
DIR_FRONT = 2
DIR_RIGHT = 3
DIR_BACK = 4
# v2 called "both motors simultaneously" CENTER.  Same code, same sensation.
DIR_CENTER = DIR_FRONT

DIRECTION_NAMES = {
    DIR_STOP: "STOP",
    DIR_LEFT: "LEFT",
    DIR_FRONT: "FRONT",
    DIR_RIGHT: "RIGHT",
    DIR_BACK: "BACK",
}
# Directions a v2 belt can render.  BACK is deliberately absent.
V2_DIRECTIONS = (DIR_STOP, DIR_LEFT, DIR_FRONT, DIR_RIGHT)

# Motor mask bits.  Bit 0 = LEFT, bit 1 = RIGHT, matching firmware config.h.
MASK_LEFT = 0b01
MASK_RIGHT = 0b10
MASK_BOTH = MASK_LEFT | MASK_RIGHT
VALID_MASKS = (MASK_LEFT, MASK_RIGHT, MASK_BOTH)

# v3 header is 8 bytes and each step is 2 bytes, so 6 steps is exactly 20
# payload bytes: the most a Write Request carries on the default 23-byte ATT
# MTU.  Staying inside that means the belt never depends on MTU negotiation.
PATTERN_MAX_STEPS = 6
V3_HEADER_BYTES = 8
V3_MAX_BYTES = V3_HEADER_BYTES + 2 * PATTERN_MAX_STEPS

# v2 had no per-step mask, so its header is 6 bytes and it allowed 4 pairs.
PATTERN_MAX_PAIRS_V2 = 4
V2_HEADER_BYTES = 6

# Timings are transmitted in units of 10 ms, one unsigned byte each.
STEP_TIME_UNIT_MS = 10
STEP_TIME_MAX_MS = 255 * STEP_TIME_UNIT_MS  # 2550 ms


class ProtocolError(ValueError):
    """Raised for any packet that must not reach the motors."""


# --------------------------------------------------------------- direction I/O
def normalize_direction(value: object, *, allow_back: bool = True) -> int:
    """Map a ``meit-ios`` direction string onto a wire direction code.

    ``meit-ios`` reports the loudest registered role, one of ``front``,
    ``right``, ``back`` or ``left``, and ``unknown`` whenever its own margin
    gate is not satisfied.  ``unknown`` and anything unrecognised return
    ``DIR_STOP``, which callers must treat as *suppress the alert* rather than
    as *stop the motors*: telling the wearer "danger, somewhere" is worse than
    staying silent, and the iOS side has already decided the direction is not
    trustworthy.

    ``allow_back=False`` folds ``back`` into ``FRONT`` for a belt still running
    v2 firmware, which cannot render a sweep.
    """
    if value is None:
        return DIR_STOP
    text = str(value).strip().lower()
    if text == "left":
        return DIR_LEFT
    if text == "right":
        return DIR_RIGHT
    if text in {"front", "center", "centre"}:
        return DIR_FRONT
    if text == "back":
        return DIR_BACK if allow_back else DIR_FRONT
    return DIR_STOP


def direction_name(direction: int) -> str:
    try:
        return DIRECTION_NAMES[direction]
    except KeyError:
        raise ProtocolError(f"invalid direction code: {direction!r}") from None


# ------------------------------------------------------------------ data model
@dataclass(frozen=True)
class MotorStep:
    """One ON burst followed by an OFF gap.

    ``mask`` selects which motors run during ``on_ms``.  ``off_ms`` may be 0,
    which is how a sweep chains two bursts with no perceptible seam.
    """

    mask: int
    on_ms: int
    off_ms: int

    def validate(self) -> None:
        if self.mask not in VALID_MASKS:
            raise ProtocolError(f"step mask must be 1, 2 or 3 (got {self.mask})")
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
    """A complete, already-decided haptic command.

    ``direction`` is carried for logging and for v2 compatibility; in v3 the
    per-step masks are authoritative and the firmware drives the motors from
    them, not from this field.
    """

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
    def uniform_mask(self) -> int | None:
        """The single mask shared by every step, or ``None`` if they differ."""
        masks = {step.mask for step in self.steps}
        return masks.pop() if len(masks) == 1 else None

    def describe(self) -> str:
        shape = " ".join(
            f"{'L' if s.mask == MASK_LEFT else 'R' if s.mask == MASK_RIGHT else 'LR'}"
            f":{s.on_ms}+{s.off_ms}" for s in self.steps
        )
        return (f"{direction_name(self.direction)} int={self.intensity} "
                f"{self.duration_ms}ms [{shape}]" if shape
                else f"{direction_name(self.direction)}")


STOP_COMMAND = MotorCommand(direction=DIR_STOP, intensity=0, steps=())


@dataclass(frozen=True)
class DecodedCommand:
    version: int
    sequence: int
    command: MotorCommand


# ------------------------------------------------------------- v2 downgrading
def to_v2_command(command: MotorCommand) -> MotorCommand:
    """Rewrite a v3 command so a v2 belt can play it.

    Two lossy steps, in this order:

    1. Adjacent steps separated by no gap (``off_ms == 0``) are merged and their
       masks unioned.  That is exactly how a sweep is built, so a BACK pattern
       collapses into the equivalent both-motor pulses instead of turning into
       twice as many half-length buzzes.
    2. Any remaining mask difference is unioned into one mask for the whole
       pattern, because v2 has a single mask field.

    The pulse count — which is what encodes the danger class — is preserved.
    The direction distinction between FRONT and BACK is not; that is the cost
    of not reflashing, and callers should log it.
    """
    if command.is_stop:
        return command

    merged: List[MotorStep] = []
    for step in command.steps:
        if merged and merged[-1].off_ms == 0:
            previous = merged[-1]
            merged[-1] = MotorStep(mask=previous.mask | step.mask,
                                   on_ms=previous.on_ms + step.on_ms,
                                   off_ms=step.off_ms)
        else:
            merged.append(step)

    mask = 0
    for step in merged:
        mask |= step.mask
    merged = [MotorStep(mask=mask, on_ms=s.on_ms, off_ms=s.off_ms) for s in merged]

    if len(merged) > PATTERN_MAX_PAIRS_V2:
        # Keep the leading pulses: the onset is what the wearer reacts to.
        merged = merged[:PATTERN_MAX_PAIRS_V2]
    # A truncated or merged tail must not leave the motors waiting on a gap.
    merged[-1] = MotorStep(mask=merged[-1].mask, on_ms=merged[-1].on_ms, off_ms=0)

    direction = {MASK_LEFT: DIR_LEFT, MASK_RIGHT: DIR_RIGHT, MASK_BOTH: DIR_FRONT}[mask]
    return MotorCommand(direction=direction, intensity=command.intensity,
                        steps=tuple(merged))


# ----------------------------------------------------------------- encoding
def _check_sequence(sequence: int) -> None:
    if not isinstance(sequence, int) or not 0 <= sequence <= 255:
        raise ProtocolError(f"sequence must be a uint8 (got {sequence!r})")


def encode_v3(sequence: int, command: MotorCommand) -> bytes:
    """Encode CMD v3.

    ::

        [0]     magic      0xA5
        [1]     version    0x03
        [2]     sequence   uint8, trace/correlation only
        [3]     direction  0 STOP, 1 LEFT, 2 FRONT, 3 RIGHT, 4 BACK
        [4]     intensity  0..100 percent (0 only for STOP)
        [5]     n_steps    0..6 (0 only for STOP)
        [6]     masks low   step0=bits0-1, step1=bits2-3, step2=bits4-5, step3=bits6-7
        [7]     masks high  step4=bits0-1, step5=bits2-3
        [8+2i]  on_ms  / 10   (1..255)
        [9+2i]  off_ms / 10   (0..255)

    A mask nibble of ``0b00`` is invalid by construction, so a truncated or
    zero-padded packet cannot be mistaken for a valid one.
    """
    _check_sequence(sequence)
    command.validate()

    if command.is_stop:
        return bytes([CMD_MAGIC, CMD_VERSION_V3, sequence, DIR_STOP, 0, 0, 0, 0])

    mask_bits = 0
    for index, step in enumerate(command.steps):
        mask_bits |= (step.mask & 0b11) << (2 * index)

    out = bytearray([CMD_MAGIC, CMD_VERSION_V3, sequence, command.direction,
                     command.intensity, len(command.steps),
                     mask_bits & 0xFF, (mask_bits >> 8) & 0xFF])
    for step in command.steps:
        out.append(step.on_ms // STEP_TIME_UNIT_MS)
        out.append(step.off_ms // STEP_TIME_UNIT_MS)
    assert len(out) <= V3_MAX_BYTES, "v3 packet exceeded the single-write budget"
    return bytes(out)


def encode_v2(sequence: int, command: MotorCommand) -> bytes:
    """Encode CMD v2, the pre-v3 format a non-reflashed belt understands.

    ::

        [0]     magic 0xA5
        [1]     version 0x02
        [2]     sequence
        [3]     direction  0 STOP, 1 LEFT, 2 CENTER(both), 3 RIGHT
        [4]     intensity  0..100
        [5]     n_pairs    0..4 (0 only for STOP)
        [6+2i]  on_ms / 10, off_ms / 10

    The command must already be v2-shaped; pass it through :func:`to_v2_command`
    first.  Failing loudly here rather than silently downgrading keeps the
    fallback decision at the call site, where it can be logged.
    """
    _check_sequence(sequence)
    command.validate()

    if command.is_stop:
        return bytes([CMD_MAGIC, CMD_VERSION_V2, sequence, DIR_STOP, 0, 0])

    if command.direction not in V2_DIRECTIONS:
        raise ProtocolError(f"{direction_name(command.direction)} cannot be expressed in "
                            "CMD v2; call to_v2_command() first")
    mask = command.uniform_mask
    if mask is None:
        raise ProtocolError("CMD v2 carries one mask for the whole pattern; "
                            "call to_v2_command() first")
    expected = {MASK_LEFT: DIR_LEFT, MASK_RIGHT: DIR_RIGHT, MASK_BOTH: DIR_FRONT}[mask]
    if command.direction != expected:
        raise ProtocolError("v2 direction byte disagrees with the step masks")
    if len(command.steps) > PATTERN_MAX_PAIRS_V2:
        raise ProtocolError(f"CMD v2 allows at most {PATTERN_MAX_PAIRS_V2} pairs")

    out = bytearray([CMD_MAGIC, CMD_VERSION_V2, sequence, command.direction,
                     command.intensity, len(command.steps)])
    for step in command.steps:
        out.append(step.on_ms // STEP_TIME_UNIT_MS)
        out.append(step.off_ms // STEP_TIME_UNIT_MS)
    return bytes(out)


def encode(sequence: int, command: MotorCommand, version: int = CMD_VERSION_V3) -> bytes:
    """Encode for ``version``, downgrading the command shape when needed."""
    if version == CMD_VERSION_V3:
        return encode_v3(sequence, command)
    if version == CMD_VERSION_V2:
        return encode_v2(sequence, to_v2_command(command))
    raise ProtocolError(f"unsupported CMD version: {version!r}")


# ----------------------------------------------------------------- decoding
def decode(data: bytes) -> DecodedCommand:
    """Decode a v2 or v3 packet, applying the same checks as the firmware.

    Kept strict and symmetric with the ESP32 GATT handler so a unit test here
    is evidence about the device, not only about Python.
    """
    if len(data) < V2_HEADER_BYTES:
        raise ProtocolError("CMD packet too short")
    if data[0] != CMD_MAGIC:
        raise ProtocolError(f"bad CMD magic: 0x{data[0]:02x}")
    version = data[1]
    if version not in SUPPORTED_VERSIONS:
        raise ProtocolError(f"unsupported CMD version: 0x{version:02x}")

    sequence, direction, intensity = data[2], data[3], data[4]
    if direction not in DIRECTION_NAMES:
        raise ProtocolError(f"invalid direction code: {direction}")
    if version == CMD_VERSION_V2 and direction not in V2_DIRECTIONS:
        raise ProtocolError("BACK is not a CMD v2 direction")
    if intensity > 100:
        raise ProtocolError(f"invalid intensity: {intensity}")

    n_steps = data[5]
    header = V3_HEADER_BYTES if version == CMD_VERSION_V3 else V2_HEADER_BYTES
    limit = PATTERN_MAX_STEPS if version == CMD_VERSION_V3 else PATTERN_MAX_PAIRS_V2
    if n_steps > limit:
        raise ProtocolError(f"pattern holds more than {limit} steps")
    if len(data) != header + 2 * n_steps:
        raise ProtocolError("CMD length disagrees with the step count")

    if direction == DIR_STOP:
        if n_steps or intensity:
            raise ProtocolError("STOP must carry no pattern and zero intensity")
        return DecodedCommand(version, sequence, STOP_COMMAND)
    if n_steps == 0:
        raise ProtocolError("a motor command needs at least one step")

    if version == CMD_VERSION_V3:
        mask_bits = data[6] | (data[7] << 8)
        masks = [(mask_bits >> (2 * i)) & 0b11 for i in range(n_steps)]
        if any(mask not in VALID_MASKS for mask in masks):
            raise ProtocolError("invalid per-step motor mask")
        if mask_bits >> (2 * n_steps):
            raise ProtocolError("mask bits set beyond the declared step count")
    else:
        masks = [{DIR_LEFT: MASK_LEFT, DIR_FRONT: MASK_BOTH,
                  DIR_RIGHT: MASK_RIGHT}[direction]] * n_steps

    steps: List[MotorStep] = []
    for index in range(n_steps):
        on_raw = data[header + 2 * index]
        off_raw = data[header + 2 * index + 1]
        if on_raw == 0:
            raise ProtocolError("a step cannot have a zero ON time")
        steps.append(MotorStep(mask=masks[index],
                               on_ms=on_raw * STEP_TIME_UNIT_MS,
                               off_ms=off_raw * STEP_TIME_UNIT_MS))

    return DecodedCommand(version, sequence,
                          MotorCommand(direction=direction, intensity=intensity,
                                       steps=tuple(steps)))


def steps_from_pairs(mask: int, pairs: Iterable[Sequence[int]]) -> Tuple[MotorStep, ...]:
    """Convenience builder: one mask applied to ``[[on_ms, off_ms], ...]``."""
    return tuple(MotorStep(mask=mask, on_ms=int(on), off_ms=int(off)) for on, off in pairs)
