"""Run tdoa_tonal_diagnostic.c against the real, unmodified tdoa.c.

Uses the same portable esp-dsp FFT adapter as run_audio_host_tests.py.
Exit status fails only on a wrong delay; confidence is printed, not gated.
Run: python firmware/tests/run_tdoa_tonal_diagnostic.py --cc gcc
Or:  python firmware/tests/run_tdoa_tonal_diagnostic.py --cc /path/to/zig --zig
"""
import argparse
from pathlib import Path
import subprocess
import tempfile
import os

from run_audio_host_tests import DSP


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cc', default='cc')
    p.add_argument('--zig', action='store_true')
    args = p.parse_args()
    here = Path(__file__).resolve().parent
    main_dir = here.parent / 'main'
    with tempfile.TemporaryDirectory(prefix='meit-tonal-') as tmp:
        d = Path(tmp)
        (d/'esp_err.h').write_text('typedef int esp_err_t;\n')
        (d/'esp_dsp.h').write_text(DSP)
        exe = d / ('tonal_diag.exe' if os.name == 'nt' else 'tonal_diag')
        cmd = [args.cc] + (['cc'] if args.zig else [])
        cmd += ['-std=c11', '-D_GNU_SOURCE', '-Wall', '-Wextra', '-Werror',
                '-I', str(d), '-I', str(main_dir),
                str(here/'tdoa_tonal_diagnostic.c'), str(main_dir/'tdoa.c'),
                '-o', str(exe), '-lm']
        subprocess.run(cmd, check=True)
        return subprocess.run([str(exe)], timeout=30).returncode


if __name__ == '__main__':
    raise SystemExit(main())
