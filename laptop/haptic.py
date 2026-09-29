"""Turn one AI result into one motor command.

This module is the whole point of the EE side: everything upstream produces
*information* (which direction, which danger class, how confident) and the belt
can only produce *sensation*.  This is where the translation happens, and it is
deliberately pure — no BLE, no HTTP, no clock — so the mapping can be reasoned
about and unit-tested on its own.

The encoding, in one sentence
----------------------------
**Where you feel it says where the sound is; how many times you feel it says
what the sound was; how hard you feel it says how sure the AI is.**

Direction -> which motors, in what order
    ======== ====================================== =========================
    ``left``   left motor only                        one-sided
    ``right``  right motor only                       one-sided
    ``front``  both motors together                   symmetric, instantaneous
    ``back``   left then right, seamlessly            a sweep passing by
    ======== ====================================== =========================

    ``front`` and ``back`` are both "not to one side", so they cannot be told
    apart spatially with two actuators.  They are told apart *temporally*
    instead: ``front`` arrives as one symmetric event, ``back`` travels across
    the body.  A sweep away from the wearer's facing direction is the natural
    reading of "it came from behind you".

Danger class -> how many pulses
    ``crash`` one long pulse, ``horn`` two, ``siren`` three.  Pulse count is
    the most robust haptic channel there is: it survives a loose belt, thick
    clothing and a wearer who is walking.  Waveform subtleties do not.

Confidence -> PWM duty
    A low-confidence alert still fires — suppressing a real hazard is the worse
    error — but it is felt as less urgent.

Because the two axes use different channels (spatial/ordering vs. count) they
compose without interfering: "siren from the left" is three left-only pulses,
"siren from behind" is three sweeps.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from laptop.protocol import (
    DIR_BACK,
    DIR_FRONT,
    DIR_LEFT,
    DIR_RIGHT,
    DIR_STOP,
    MASK_BOTH,
    MASK_LEFT,
    MASK_RIGHT,
    PATTERN_MAX_STEPS,
    STEP_TIME_UNIT_MS,
    MotorCommand,
    MotorStep,
    ProtocolError,
    direction_name,
)

# The three classes meit-ios alerts on.  meit-ai's CLASSES may be larger (it
# also reports e.g. `normal`); anything outside this set must stay silent.
DANGER_LABELS: Tuple[str, ...] = ("horn", "siren", "crash")

# An ERM/coin vibration motor needs roughly 50-80 ms to spin up to a
# perceptible amplitude, so a burst shorter than this is felt as nothing at all
# rather than as a short pulse.  Every emitted step, including the halves of a
# BACK sweep, is checked against this floor.
MIN_STEP_ON_MS = 100

DEFAULT_PROFILE_PATH = Path(__file__).with_name("haptic_profile.json")


class HapticError(ValueError):
    """Raised when a profile cannot produce a playable pattern."""


@dataclass(frozen=True)
class ClassProfile:
    """How one danger class is rendered.

    ``on_ms`` is the length of a whole pulse.  For a BACK sweep the pulse is
    split in half between the two motors, so ``on_ms`` must be a multiple of
    ``2 * STEP_TIME_UNIT_MS`` for both halves to stay on the 10 ms wire grid,
    and each half must still clear :data:`MIN_STEP_ON_MS`.
    """

    pulses: int
    on_ms: int
    gap_ms: int
    min_intensity: int
    max_intensity: int

    def validate(self, label: str) -> None:
        def fail(message: str) -> None:
            raise HapticError(f"class {label!r}: {message}")

        if not 1 <= self.pulses <= PATTERN_MAX_STEPS:
            fail(f"pulses must be 1..{PATTERN_MAX_STEPS} (got {self.pulses})")
        # A BACK sweep spends two steps per pulse, and that is the widest case,
        # so validating it here means no direction can overflow the pattern.
        if self.pulses * 2 > PATTERN_MAX_STEPS:
            fail(f"pulses must be <= {PATTERN_MAX_STEPS // 2} so a BACK sweep still fits "
                 f"in {PATTERN_MAX_STEPS} steps (got {self.pulses})")
        if self.on_ms % (2 * STEP_TIME_UNIT_MS):
            fail(f"on_ms must be a multiple of {2 * STEP_TIME_UNIT_MS} ms so a BACK sweep "
                 f"splits evenly (got {self.on_ms})")
        if self.gap_ms % STEP_TIME_UNIT_MS:
            fail(f"gap_ms must be a multiple of {STEP_TIME_UNIT_MS} ms (got {self.gap_ms})")
        if self.gap_ms < 0:
            fail("gap_ms must not be negative")
        if self.on_ms // 2 < MIN_STEP_ON_MS:
            fail(f"on_ms must be >= {2 * MIN_STEP_ON_MS} ms so each half of a BACK sweep "
                 f"is still perceptible (got {self.on_ms})")
        if not 1 <= self.min_intensity <= self.max_intensity <= 100:
            fail("intensities must satisfy 1 <= min <= max <= 100 "
                 f"(got {self.min_intensity}..{self.max_intensity})")

    def to_dict(self) -> Dict[str, int]:
        return {"pulses": self.pulses, "on_ms": self.on_ms, "gap_ms": self.gap_ms,
                "min_intensity": self.min_intensity, "max_intensity": self.max_intensity}


@dataclass(frozen=True)
class HapticProfile:
    """The complete, tunable mapping.

    ``confidence_floor``/``confidence_ceiling`` bracket the range over which
    confidence is mapped onto the class intensity range.  The floor is not zero
    because a 3-to-5-class softmax rarely goes below chance level, and treating
    chance-level output as "barely vibrate" would waste most of the dynamic
    range on values that never occur.
    """

    classes: Mapping[str, ClassProfile]
    confidence_floor: float = 0.50
    confidence_ceiling: float = 0.90

    def validate(self) -> None:
        if not self.classes:
            raise HapticError("profile defines no classes")
        for label in DANGER_LABELS:
            if label not in self.classes:
                raise HapticError(f"profile is missing danger class {label!r}")
        for label, entry in self.classes.items():
            entry.validate(label)
        if not 0.0 <= self.confidence_floor < self.confidence_ceiling <= 1.0:
            raise HapticError("confidence bounds must satisfy "
                              "0 <= floor < ceiling <= 1 "
                              f"(got {self.confidence_floor}..{self.confidence_ceiling})")

    def to_dict(self) -> Dict[str, Any]:
        return {"confidence_floor": self.confidence_floor,
                "confidence_ceiling": self.confidence_ceiling,
                "classes": {label: entry.to_dict()
                            for label, entry in self.classes.items()}}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "HapticProfile":
        try:
            classes = {str(label): ClassProfile(
                pulses=int(entry["pulses"]),
                on_ms=int(entry["on_ms"]),
                gap_ms=int(entry["gap_ms"]),
                min_intensity=int(entry["min_intensity"]),
                max_intensity=int(entry["max_intensity"]),
            ) for label, entry in dict(data["classes"]).items()}
        except (KeyError, TypeError, ValueError) as exc:
            raise HapticError(f"malformed haptic profile: {exc}") from exc

        profile = cls(
            classes=classes,
            confidence_floor=float(data.get("confidence_floor", 0.50)),
            confidence_ceiling=float(data.get("confidence_ceiling", 0.90)),
        )
        profile.validate()
        return profile

    @classmethod
    def load(cls, path: Optional[Path | str] = None) -> "HapticProfile":
        """Load a profile from JSON, or return the built-in default.

        The shipped ``haptic_profile.json`` is byte-for-byte the default, so a
        team can tune the belt by editing JSON with no code change, and a unit
        test asserts the two never drift apart.
        """
        if path is None:
            return DEFAULT_PROFILE
        text = Path(path).expanduser().read_text(encoding="utf-8")
        return cls.from_dict(json.loads(text))


# Timings below were chosen against the two constraints that actually bite:
# a coin motor's spin-up time (hence no pulse under 100 ms, and 250/120/130 ms
# sweep halves) and the 6-step packet budget (hence at most 3 pulses).
#
#   crash  one 500 ms pulse                     -> "bang", the most urgent
#   horn   two 240 ms pulses, 140 ms apart      -> "beep-beep"
#   siren  three 260 ms pulses, 150 ms apart    -> "wee-oo-wee"
DEFAULT_PROFILE = HapticProfile(
    classes={
        # Floors raised so even a low-confidence alert clears the belt's
        # perceptibility threshold on the bench: duty is capped at ~47 % by the
        # firmware (3 V motor on a 6.4 V rail), and testing showed ~28 % duty is
        # not felt while ~47 % is. Keeping every class's minimum near the top of
        # the allowed range means no pattern comes out too weak to notice.
        # Class is carried by the pulse count, not by intensity, so raising all
        # three does not blur which sound it is.
        "crash": ClassProfile(pulses=1, on_ms=500, gap_ms=0,
                              min_intensity=88, max_intensity=100),
        "horn": ClassProfile(pulses=2, on_ms=240, gap_ms=140,
                             min_intensity=80, max_intensity=100),
        "siren": ClassProfile(pulses=3, on_ms=260, gap_ms=150,
                              min_intensity=84, max_intensity=100),
    },
    confidence_floor=0.50,
    confidence_ceiling=0.90,
)
DEFAULT_PROFILE.validate()


# --------------------------------------------------------------- intensity map
def intensity_for(entry: ClassProfile, confidence: float,
                  profile: HapticProfile = DEFAULT_PROFILE) -> int:
    """Map confidence onto the class's PWM-percent range.

    Non-finite or missing confidence degrades to the class minimum rather than
    raising: a hazard that the AI reported is still worth feeling even if its
    confidence field arrived malformed.
    """
    try:
        value = float(confidence)
    except (TypeError, ValueError):
        value = 0.0
    if value != value or value in (float("inf"), float("-inf")):  # NaN / +-inf
        value = 0.0

    span = profile.confidence_ceiling - profile.confidence_floor
    fraction = (value - profile.confidence_floor) / span
    fraction = min(1.0, max(0.0, fraction))
    reach = entry.max_intensity - entry.min_intensity
    return int(round(entry.min_intensity + fraction * reach))


# ------------------------------------------------------------- pattern builder
def _steps_for(direction: int, entry: ClassProfile) -> Tuple[MotorStep, ...]:
    """Lay the class rhythm out over the motors the direction selects."""
    if direction == DIR_BACK:
        # Each pulse becomes left-half then right-half with no gap between
        # them, so one pulse is felt as a single burst travelling across the
        # body rather than as two separate taps.
        half = entry.on_ms // 2
        steps: List[MotorStep] = []
        for index in range(entry.pulses):
            last = index == entry.pulses - 1
            steps.append(MotorStep(mask=MASK_LEFT, on_ms=half, off_ms=0))
            steps.append(MotorStep(mask=MASK_RIGHT, on_ms=half,
                                   off_ms=0 if last else entry.gap_ms))
        return tuple(steps)

    mask = {DIR_LEFT: MASK_LEFT, DIR_RIGHT: MASK_RIGHT, DIR_FRONT: MASK_BOTH}.get(direction)
    if mask is None:
        raise HapticError(f"no pattern for direction {direction!r}")
    return tuple(
        MotorStep(mask=mask, on_ms=entry.on_ms,
                  off_ms=0 if index == entry.pulses - 1 else entry.gap_ms)
        for index in range(entry.pulses)
    )


def build_command(label: str, confidence: float, direction: int,
                  profile: HapticProfile = DEFAULT_PROFILE) -> Optional[MotorCommand]:
    """Build the motor command for one AI result, or ``None`` to stay silent.

    ``None`` is returned — meaning *send nothing at all*, not *send STOP* — for
    a non-danger label, an unknown class, or ``DIR_STOP``.  ``DIR_STOP`` here
    means iOS could not settle on a direction, and a directionless buzz would
    train the wearer to ignore the belt.
    """
    if direction == DIR_STOP:
        return None
    key = str(label).strip().lower()
    if key not in DANGER_LABELS:
        return None
    entry = profile.classes.get(key)
    if entry is None:
        return None

    steps = _steps_for(direction, entry)
    for step in steps:
        if step.on_ms < MIN_STEP_ON_MS:
            raise HapticError(f"class {key!r} would emit a {step.on_ms} ms burst, below the "
                              f"{MIN_STEP_ON_MS} ms perceptibility floor")
    return MotorCommand(direction=direction,
                        intensity=intensity_for(entry, confidence, profile),
                        steps=steps)


def with_intensity_scale(command: MotorCommand, scale: float) -> MotorCommand:
    """Scale a command's intensity, clamped to the legal 1..100 range.

    Used by the ``--intensity-scale`` knob so a demo can be turned down for a
    quiet room, or up for a thick jacket, without editing the profile.
    """
    if command.is_stop:
        return command
    scaled = int(round(command.intensity * float(scale)))
    return replace(command, intensity=min(100, max(1, scaled)))


# --------------------------------------------------------------- presentation
def render_timeline(command: MotorCommand, *, columns_per_100ms: int = 2) -> str:
    """ASCII timeline of a pattern, for bench-testing without hardware.

    ``#`` marks a motor running.  Two rows, left motor above right, so a BACK
    sweep visibly staircases and a FRONT pulse visibly lines up.
    """
    if command.is_stop:
        return "STOP (both motors off)"

    unit_ms = 100 // columns_per_100ms if columns_per_100ms else 50
    rows = {MASK_LEFT: ["L |"], MASK_RIGHT: ["R |"]}
    for step in command.steps:
        on_cols = max(1, round(step.on_ms / unit_ms))
        off_cols = round(step.off_ms / unit_ms)
        for bit, row in rows.items():
            row.append(("#" if step.mask & bit else ".") * on_cols)
            row.append("." * off_cols)
    header = f"{command.describe()}  ({unit_ms} ms per column)"
    return "\n".join([header, "".join(rows[MASK_LEFT]), "".join(rows[MASK_RIGHT])])


def explain(label: str, confidence: float, direction: int,
            profile: HapticProfile = DEFAULT_PROFILE) -> str:
    """One human-readable line describing what the wearer will feel."""
    command = build_command(label, confidence, direction, profile)
    if command is None:
        return f"suppressed (label={label!r} direction={direction})"
    entry = profile.classes[str(label).strip().lower()]
    where = {DIR_LEFT: "on the left motor", DIR_RIGHT: "on the right motor",
             DIR_FRONT: "on both motors together",
             DIR_BACK: "as a left-to-right sweep"}[direction]
    return (f"{direction_name(direction)}/{label}: {entry.pulses} pulse(s) {where}, "
            f"{command.intensity}% duty, {command.duration_ms} ms total")


__all__ = [
    "DANGER_LABELS",
    "DEFAULT_PROFILE",
    "DEFAULT_PROFILE_PATH",
    "MIN_STEP_ON_MS",
    "ClassProfile",
    "HapticError",
    "HapticProfile",
    "ProtocolError",
    "build_command",
    "explain",
    "intensity_for",
    "render_timeline",
    "with_intensity_scale",
]
