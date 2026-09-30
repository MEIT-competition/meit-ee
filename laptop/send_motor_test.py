"""Drive the belt directly over BLE, with no iPhone and no AI involved.

Two purposes, in the order you should use them during bring-up:

1. ``--raw`` — one plain pulse in one direction, to prove the wiring, the PWM
   channels, the motor supply and the BLE link.  Nothing from the haptic profile
   is involved, so a failure here is hardware or firmware, never mapping.
2. the real alert patterns — exactly what a wearer will feel for a given class
   and direction, so the encoding can be judged on the body rather than on
   paper.

Examples::

    python -m laptop.send_motor_test left --raw        # left motor, 400 ms
    python -m laptop.send_motor_test center --label siren
    python -m laptop.send_motor_test all              # every direction x class
    python -m laptop.send_motor_test stop
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from typing import List, Optional, Tuple

from laptop.belt_client import BeltClient, BeltLink, add_belt_arguments, belt_from_arguments
from laptop.haptic import (
    DANGER_LABELS,
    HapticProfile,
    build_command,
    render_timeline,
    with_intensity_scale,
)
from laptop.protocol import (
    DIR_CENTER,
    DIR_LEFT,
    DIR_RIGHT,
    DIR_STOP,
    STOP_COMMAND,
    MotorCommand,
    MotorStep,
    direction_name,
)

LOGGER = logging.getLogger("meit.test")

TARGETS = {"left": DIR_LEFT, "center": DIR_CENTER, "right": DIR_RIGHT,
           "stop": DIR_STOP}
RAW_PULSE_MS = 400


def raw_command(direction: int, intensity: int) -> MotorCommand:
    """A single plain pulse: the smallest thing that proves the hardware works."""
    if direction == DIR_STOP:
        return STOP_COMMAND
    return MotorCommand(direction=direction, intensity=intensity,
                        steps=(MotorStep(on_ms=RAW_PULSE_MS, off_ms=0),))


def plan(args: argparse.Namespace, profile: HapticProfile) -> List[Tuple[str, MotorCommand]]:
    """Build the labelled list of commands this invocation will send."""
    def decorate(command: MotorCommand) -> MotorCommand:
        if args.intensity_scale != 1.0 and not command.is_stop:
            return with_intensity_scale(command, args.intensity_scale)
        return command

    if args.target == "all":
        directions = [DIR_LEFT, DIR_CENTER, DIR_RIGHT]
        out: List[Tuple[str, MotorCommand]] = []
        for direction in directions:
            for label in DANGER_LABELS:
                command = build_command(label, args.confidence, direction, profile)
                if command is not None:
                    out.append((f"{direction_name(direction)}/{label}", decorate(command)))
        return out

    direction = TARGETS[args.target]
    if direction == DIR_STOP:
        return [("STOP", STOP_COMMAND)]
    if args.raw or args.label is None:
        return [(f"{direction_name(direction)}/raw", decorate(raw_command(direction,
                                                                         args.intensity)))]
    command = build_command(args.label, args.confidence, direction, profile)
    if command is None:
        raise SystemExit(f"no pattern for label {args.label!r}")
    return [(f"{direction_name(direction)}/{args.label}", decorate(command))]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Direct BLE motor test for MEIT-BELT")
    parser.add_argument("target", choices=sorted(TARGETS) + ["all"])
    parser.add_argument("--raw", action="store_true",
                        help=f"send one plain {RAW_PULSE_MS} ms pulse instead of an "
                             "alert pattern (wiring check)")
    parser.add_argument("--label", choices=sorted(DANGER_LABELS), default=None,
                        help="alert class to render; omit for a raw pulse")
    parser.add_argument("--confidence", type=float, default=0.85,
                        help="confidence to map onto intensity")
    parser.add_argument("--intensity", type=int, default=60,
                        help="duty percent for --raw pulses only")
    parser.add_argument("--intensity-scale", type=float, default=1.0)
    parser.add_argument("--gap-ms", type=int, default=900,
                        help="pause between patterns in 'all' mode")
    parser.add_argument("--profile", default=None, help="haptic profile JSON")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the patterns and exit without touching BLE")
    parser.add_argument("--log-level", default="INFO")
    add_belt_arguments(parser)
    return parser


async def run(belt: BeltClient, items: List[Tuple[str, MotorCommand]], gap_ms: int) -> None:
    async def session(link: BeltLink) -> None:
        for index, (name, command) in enumerate(items):
            LOGGER.info("[%d/%d] %s -> %s", index + 1, len(items), name, command.describe())
            await link.send(command)
            # Wait out the pattern, then pause, so consecutive patterns are felt
            # as separate alerts rather than one long buzz.
            await asyncio.sleep(command.duration_ms / 1000.0)
            if index + 1 < len(items):
                await asyncio.sleep(gap_ms / 1000.0)

    await belt.run_forever(session, max_attempts=1)


def main(argv: Optional[list[str]] = None) -> None:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO),
                        format="%(asctime)s %(levelname)s %(message)s")
    if not 1 <= args.intensity <= 100:
        raise SystemExit("--intensity must be 1..100")
    if not 0.1 <= args.intensity_scale <= 2.0:
        raise SystemExit("--intensity-scale must be 0.1..2.0")

    items = plan(args, HapticProfile.load(args.profile))
    if args.dry_run:
        for name, command in items:
            print(f"\n=== {name} ===")
            print(render_timeline(command))
        return

    try:
        asyncio.run(run(belt_from_arguments(args), items, args.gap_ms))
    except KeyboardInterrupt:
        LOGGER.info("stopped")


if __name__ == "__main__":
    main()
