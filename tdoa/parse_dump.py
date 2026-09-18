"""Convert a MEIT dual-I2S serial dump to a NumPy (4, N) array."""

import argparse
import re
from pathlib import Path

import numpy as np


ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
ROW_MARKER = "MEIT_RAW,"


def parse_dump(path):
    rows = []
    expected_index = 0
    with Path(path).open("r", encoding="utf-8", errors="replace") as stream:
        for line_number, raw_line in enumerate(stream, 1):
            line = ANSI_ESCAPE.sub("", raw_line)
            marker = line.find(ROW_MARKER)
            if marker < 0:
                continue
            fields = line[marker:].strip().split(",")
            if len(fields) != 6:
                raise ValueError(f"line {line_number}: expected 6 CSV fields")
            sample_index = int(fields[1])
            if sample_index != expected_index:
                raise ValueError(
                    f"line {line_number}: expected sample {expected_index}, "
                    f"got {sample_index}"
                )
            rows.append([float(value) for value in fields[2:]])
            expected_index += 1

    if not rows:
        raise ValueError("no MEIT_RAW rows found")
    channels = np.asarray(rows, dtype=np.float32).T
    if channels.shape[0] != 4:
        raise ValueError(f"expected 4 channels, got shape {channels.shape}")
    return channels


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input", help="idf.py monitor output captured as text")
    parser.add_argument("output", help="output .npy path")
    args = parser.parse_args()

    channels = parse_dump(args.input)
    np.save(args.output, channels)
    print(f"saved {args.output}: shape={channels.shape}, dtype={channels.dtype}")


if __name__ == "__main__":
    main()
