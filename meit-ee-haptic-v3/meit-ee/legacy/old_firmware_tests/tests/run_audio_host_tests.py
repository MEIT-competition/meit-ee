"""Run real tdoa.c, audio_samples.c and resample.c with a host FFT adapter.

The adapter implements the esp-dsp forward FFT contract using a portable FFT;
it does not validate the ESP32 optimized FFT or physical I2S wiring.
Run: python firmware/tests/run_audio_host_tests.py --cc gcc
Or:  python firmware/tests/run_audio_host_tests.py --cc /path/to/zig --zig
"""
import argparse
from pathlib import Path
import subprocess
import tempfile
import os

DSP = r'''
#pragma once
#include <math.h>
#include <stdlib.h>
#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif
static inline void dsps_fft2r_init_fc32(void *p, int n) { (void)p; (void)n; }
static inline void dsps_wind_hann_f32(float *w, int n) {
    for (int i=0; i<n; i++) w[i]=0.5f*(1-cosf(2*M_PI*i/(n-1)));
}
static inline void dsps_fft2r_fc32(float *x, int n) {
    for (int i=1,j=0; i<n; i++) {
        int bit=n>>1;
        for (; j&bit; bit>>=1) j^=bit;
        j^=bit;
        if (i<j) {
            float a=x[2*i],b=x[2*i+1];
            x[2*i]=x[2*j]; x[2*i+1]=x[2*j+1]; x[2*j]=a; x[2*j+1]=b;
        }
    }
    for (int size=2; size<=n; size*=2) {
        for (int k=0; k<n; k+=size) {
            for (int j=0; j<size/2; j++) {
                double a=-2*M_PI*j/size, c=cos(a), s=sin(a);
                int p=2*(k+j), q=2*(k+j+size/2);
                float re=(float)(x[q]*c-x[q+1]*s), im=(float)(x[q]*s+x[q+1]*c);
                x[q]=x[p]-re; x[q+1]=x[p+1]-im;
                x[p]+=re; x[p+1]+=im;
            }
        }
    }
}
// Adapter already returns natural order, matching FFT + bit-reversal as a pair.
static inline void dsps_bit_rev_fc32(float *x, int n) { (void)x; (void)n; }
'''

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cc', default='cc')
    p.add_argument('--zig', action='store_true')
    args = p.parse_args()
    here = Path(__file__).resolve().parent
    main_dir = here.parent / 'main'
    with tempfile.TemporaryDirectory(prefix='meit-audio-') as tmp:
        d = Path(tmp)
        (d/'esp_err.h').write_text('typedef int esp_err_t;\n')
        (d/'esp_dsp.h').write_text(DSP)
        exe = d / ('audio_tests.exe' if os.name == 'nt' else 'audio_tests')
        cmd = [args.cc] + (['cc'] if args.zig else [])
        cmd += ['-std=c11', '-D_GNU_SOURCE', '-Wall', '-Wextra', '-Werror',
                '-I', str(d), '-I', str(main_dir),
                str(here/'audio_host_test.c'),
                *[str(main_dir/f) for f in ['tdoa.c', 'audio_samples.c', 'resample.c']],
                '-o', str(exe), '-lm']
        subprocess.run(cmd, check=True)
        subprocess.run([str(exe)], check=True, timeout=30)

if __name__ == '__main__':
    main()
