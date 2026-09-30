"""Turn one AI result into one motor command.

This module is the whole point of the EE side: everything upstream produces
*information* (which direction, which danger class, how loud) and the belt can
only produce *sensation*. This is where the translation happens, and it is
deliberately pure — no BLE, no HTTP, no clock — so the mapping can be reasoned
about and unit-tested on its own.

The encoding, in one sentence
----------------------------
**Where you feel it says where the sound is; how many times you feel it says
what the sound was; how hard you feel it says how loud it was.**

Direction -> which motors
    ========== ============================ =========================
    ``left``     left motor only              one-sided
    ``right``    right motor only             one-sided
    ``center``   both motors together         not to either side
    ========== ============================ =========================

    Those are the three directions iOS stereo reports
    (``StereoDirectionEstimator.swift``). There is no rear cue: a stereo pair
    cannot separate front from back, and the system has no rear sensor, so
    inventing a fourth sensation would claim resolution the input does not have.

    ``unavailable`` means iOS's own margin gate was not satisfied. The belt stays
    **silent** rather than buzzing vaguely — a directionless alert teaches the
    wearer to ignore the belt, which is worse than missing one event.

Danger class -> how many pulses
    ``crash`` one long pulse, ``horn`` two, ``siren`` three. Pulse count is the
    most robust haptic channel there is: it survives a loose belt, thick clothing
    and a wearer who is walking. Waveform subtleties do not.

    ``meit-ai``'s own ``decision/patterns.py`` independently uses the same
    counts, so this matches what the AI team designed; only the timings are
    stretched to what a coin motor can actually render.

Loudness -> PWM duty
    Following ``meit-ai``'s deliberate rule that confidence decides *whether* to
    alert and dBFS decides *how hard*. A low-confidence alert still fires —
    suppressing a real hazard is the worse error — but a faint one is felt as
    less urgent than a close one.

Because the cues use different channels (which motor vs. how many pulses vs. how
hard) they compose without interfering: "siren from the left" is three left-only
pulses.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

from laptop.protocol import (
    DIR_CENTER,
    DIR_LEFT,
    DIR_RIGHT,
    DIR_STOP,
    MASK_LEFT,
    MASK_RIGHT,
    PATTERN_MAX_STEPS,
    STEP_TIME_UNIT_MS,
    MotorCommand,
    MotorStep,
    ProtocolError,
    direction_name,
)

# The three classes the belt alerts on. meit-ai's CLASSES is larger — it also
# reports `normal` — and anything outside this set must stay silent.
DANGER_LABELS: Tuple[str, ...] = ("horn", "siren", "crash")

# An ERM/coin vibration motor needs roughly 50-80 ms to spin up to a perceptible
# amplitude, so a burst shorter than this is felt as nothing at all rather than
# as a short pulse. Every emitted step is checked against this floor.
MIN_STEP_ON_MS = 100

DEFAULT_PROFILE_PATH = Path(__file__).with_name("haptic_profile.json")


class HapticError(ValueError):
    """Raised when a profile cannot produce a playable pattern."""


@dataclass(frozen=True)
class ClassProfile:
    """How one danger class is rendered."""

    pulses: int
    on_ms: int
    gap_ms: int
    min_intensity: int
    max_intensity: int

    def validate(self, label: str) -> None:
        def fail(message: str) -> None:
            raise HapticError(f"class {label!r}: {message}")

        if not 1 <= self.pulses <= PATTERN_MAX_STEPS:
            fail(f"pulses must be 1..{PATTERN_MAX_STEPS} to fit one BLE packet "
                 f"(got {self.pulses})")
        if self.on_ms % STEP_TIME_UNIT_MS or self.gap_ms % STEP_TIME_UNIT_MS:
            fail(f"on_ms and gap_ms must be multiples of {STEP_TIME_UNIT_MS} ms "
                 f"(got {self.on_ms}/{self.gap_ms})")
        if self.gap_ms < 0:
            fail("gap_ms must not be negative")
        if self.on_ms < MIN_STEP_ON_MS:
            fail(f"on_ms must be >= {MIN_STEP_ON_MS} ms to be perceptible on a coin "
                 f"motor (got {self.on_ms})")
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
    confidence is mapped onto the class intensity range, for the paths that have
    no measured loudness. The floor is not zero because a small softmax rarely
    goes below chance level, and treating chance-level output as "barely vibrate"
    would waste most of the dynamic range on values that never occur.
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
            raise HapticError("confidence bounds must satisfy 0 <= floor < ceiling <= 1 "
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


# Timings come from the two constraints that actually bite: a coin motor's
# spin-up time (hence no pulse under 100 ms) and the packet budget (hence at most
# 4 pulses). Intensity floors are high because the firmware caps duty at ~47 % to
# keep a 3 V motor safe on a 6.4 V rail, and bench testing on the real belt found
# ~28 % duty cannot be felt while ~47 % can — leaving only ~37-46 % usable.
#
#   crash  one 500 ms pulse                     -> "bang", the most urgent
#   horn   two 240 ms pulses, 140 ms apart      -> "beep-beep"
#   siren  three 260 ms pulses, 150 ms apart    -> "wee-oo-wee"
DEFAULT_PROFILE = HapticProfile(
    classes={
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
# Mirrors the bounds in meit-ai's `decision/intensity.py`, whose deliberate
# choice is that loudness — not confidence — sets how hard the belt buzzes
# ("세기는 dBFS만으로 결정. 확신도는 알릴지 말지만 판단한다").
#
# meit-ai maps this dB range onto 40..100 percent. That is not usable here: its
# 40 % would land at ~18 % duty, which bench testing showed is not felt at all.
# The range is mapped onto each class's belt-tested band instead, keeping
# meit-ai's loudness response while staying perceptible.
DBFS_QUIET = -40.0
DBFS_LOUD = -10.0


def _fraction(value: float, low: float, high: float) -> float:
    """Position of ``value`` in ``low..high``, clamped, 0.0 for unusable input."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(number) or high <= low:
        return 0.0
    return min(1.0, max(0.0, (number - low) / (high - low)))


def _scale_into(entry: ClassProfile, fraction: float) -> int:
    reach = entry.max_intensity - entry.min_intensity
    return int(round(entry.min_intensity + fraction * reach))


def intensity_from_dbfs(entry: ClassProfile, dbfs: float,
                        quiet_dbfs: float = DBFS_QUIET,
                        loud_dbfs: float = DBFS_LOUD) -> int:
    """Map measured loudness onto the class's intensity band.

    Unusable input (``None``, ``NaN``, ``-inf`` for digital silence) degrades to
    the class minimum rather than raising: meit-ai's ``judge()`` has already
    decided this is worth alerting about, so it must still be felt.
    """
    return _scale_into(entry, _fraction(dbfs, quiet_dbfs, loud_dbfs))


def intensity_for(entry: ClassProfile, confidence: float,
                  profile: HapticProfile = DEFAULT_PROFILE) -> int:
    """Map confidence onto the class's PWM-percent range.

    Used where no loudness is available. Malformed confidence degrades to the
    class minimum rather than raising, for the same reason as above.
    """
    return _scale_into(entry, _fraction(confidence, profile.confidence_floor,
                                        profile.confidence_ceiling))


# ------------------------------------------------------------- pattern builder
def _steps_for(entry: ClassProfile) -> Tuple[MotorStep, ...]:
    """The class rhythm: ``pulses`` bursts with a gap between, none after."""
    return tuple(
        MotorStep(on_ms=entry.on_ms,
                  off_ms=0 if index == entry.pulses - 1 else entry.gap_ms)
        for index in range(entry.pulses)
    )


def build_command(label: str, confidence: float, direction: int,
                  profile: HapticProfile = DEFAULT_PROFILE, *,
                  dbfs: Optional[float] = None) -> Optional[MotorCommand]:
    """Build the motor command for one AI result, or ``None`` to stay silent.

    ``None`` is returned — meaning *send nothing at all*, not *send STOP* — for a
    non-danger label, an unknown class, or ``DIR_STOP``. ``DIR_STOP`` here means
    iOS could not settle on a direction.

    ``dbfs`` selects which channel drives intensity. Pass the loudness meit-ai
    measured and the belt follows meit-ai's own rule; leave it ``None`` — as the
    ``/auto/status`` path must, since it publishes no dB — and confidence drives
    it instead. Either way the value lands inside the class's belt-tested band.
    """
    if direction == DIR_STOP:
        return None
    key = str(label).strip().lower()
    if key not in DANGER_LABELS:
        return None
    entry = profile.classes.get(key)
    if entry is None:
        return None

    steps = _steps_for(entry)
    for step in steps:
        if step.on_ms < MIN_STEP_ON_MS:
            raise HapticError(f"class {key!r} would emit a {step.on_ms} ms burst, below the "
                              f"{MIN_STEP_ON_MS} ms perceptibility floor")
    intensity = (intensity_from_dbfs(entry, dbfs) if dbfs is not None
                 else intensity_for(entry, confidence, profile))
    return MotorCommand(direction=direction, intensity=intensity, steps=steps)


def with_intensity_scale(command: MotorCommand, scale: float) -> MotorCommand:
    """Scale a command's intensity, clamped to the legal 1..100 range.

    Used by ``--intensity-scale`` so a demo can be turned down for a quiet room,
    or up through a thick jacket, without editing the profile.
    """
    if command.is_stop:
        return command
    scaled = int(round(command.intensity * float(scale)))
    return replace(command, intensity=min(100, max(1, scaled)))


# --------------------------------------------------------------- presentation
def render_timeline(command: MotorCommand, *, columns_per_100ms: int = 2) -> str:
    """ASCII timeline of a pattern, for bench-testing without hardware.

    ``#`` marks a motor running. Two rows, left motor above right, so a CENTER
    pulse visibly lines up and a one-sided one visibly does not.
    """
    if command.is_stop:
        return "STOP (both motors off)"

    unit_ms = 100 // columns_per_100ms if columns_per_100ms else 50
    mask = command.mask
    rows = {MASK_LEFT: ["L |"], MASK_RIGHT: ["R |"]}
    for step in command.steps:
        on_cols = max(1, round(step.on_ms / unit_ms))
        off_cols = round(step.off_ms / unit_ms)
        for bit, row in rows.items():
            row.append(("#" if mask & bit else ".") * on_cols)
            row.append("." * off_cols)
    header = f"{command.describe()}  ({unit_ms} ms per column)"
    return "\n".join([header, "".join(rows[MASK_LEFT]), "".join(rows[MASK_RIGHT])])


def explain(label: str, confidence: float, direction: int,
            profile: HapticProfile = DEFAULT_PROFILE, *,
            dbfs: Optional[float] = None) -> str:
    """One human-readable line describing what the wearer will feel."""
    command = build_command(label, confidence, direction, profile, dbfs=dbfs)
    if command is None:
        return f"suppressed (label={label!r} direction={direction})"
    entry = profile.classes[str(label).strip().lower()]
    where = {DIR_LEFT: "on the left motor", DIR_RIGHT: "on the right motor",
             DIR_CENTER: "on both motors together"}[direction]
    return (f"{direction_name(direction)}/{label}: {entry.pulses} pulse(s) {where}, "
            f"{command.intensity}% duty, {command.duration_ms} ms total")


__all__ = [
    "DANGER_LABELS",
    "DBFS_LOUD",
    "DBFS_QUIET",
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
    "intensity_from_dbfs",
    "render_timeline",
    "with_intensity_scale",
]
