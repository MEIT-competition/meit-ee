"""Print the whole haptic encoding — no belt, no BLE, no Bluetooth adapter.

Run this to check a profile edit before flashing or connecting anything::

    python -m laptop.haptic_preview
    python -m laptop.haptic_preview --confidence 0.55
    python -m laptop.haptic_preview --profile my_profile.json --bytes

It is also the quickest way to explain the encoding to the rest of the team:
the timeline rows are what the wearer feels, drawn to scale.
"""
from __future__ import annotations

import argparse
from typing import Optional

from laptop.haptic import (
    DANGER_LABELS,
    HapticProfile,
    build_command,
    explain,
    render_timeline,
    with_intensity_scale,
)
from laptop.protocol import (
    DIR_CENTER,
    DIR_LEFT,
    DIR_RIGHT,
    MAX_PACKET_BYTES,
    direction_name,
    encode,
)

DIRECTIONS = (DIR_LEFT, DIR_CENTER, DIR_RIGHT)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Preview MEIT haptic patterns offline")
    parser.add_argument("--profile", default=None, help="haptic profile JSON")
    parser.add_argument("--confidence", type=float, default=0.85)
    parser.add_argument("--intensity-scale", type=float, default=1.0)
    parser.add_argument("--bytes", action="store_true",
                        help="also print the encoded BLE packet")
    parser.add_argument("--columns-per-100ms", type=int, default=2)
    return parser


def main(argv: Optional[list[str]] = None) -> None:
    args = build_parser().parse_args(argv)
    profile = HapticProfile.load(args.profile)

    print(f"confidence={args.confidence}  intensity_scale={args.intensity_scale}")
    print(f"packet budget: {MAX_PACKET_BYTES} bytes max, which fits one BLE write "
          f"on the default 23-byte ATT MTU\n")

    widest = 0
    for direction in DIRECTIONS:
        for label in DANGER_LABELS:
            command = build_command(label, args.confidence, direction, profile)
            if command is None:
                continue
            if args.intensity_scale != 1.0:
                command = with_intensity_scale(command, args.intensity_scale)

            print(f"=== {direction_name(direction)} / {label} ===")
            print(explain(label, args.confidence, direction, profile))
            print(render_timeline(command, columns_per_100ms=args.columns_per_100ms))

            packet = encode(1, command)
            widest = max(widest, len(packet))
            if args.bytes:
                print(f"  {len(packet)} bytes: {packet.hex(' ')}")
            print()

    print(f"largest packet in this profile: {widest} bytes")


if __name__ == "__main__":
    main()
