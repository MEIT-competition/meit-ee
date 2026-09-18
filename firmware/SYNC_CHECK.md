# I2S sync verification (do this FIRST, before anything else)

Everything in this project depends on one unverified assumption:
**two I2S peripherals sharing one BCLK/WS net start on the same frame.**

I have not verified this on real ESP32-S3 silicon. Verify it yourself before
building anything on top of it.

## Wiring for the test
All four INMP441 bundled together (taped in a stack, <1 cm apart) so the true
acoustic TDoA is under 1 sample at 48 kHz.

    ESP32-S3 GPIO5  --- BCLK out (bus A / I2S0, master)
    ESP32-S3 GPIO6  --- WS   out (bus A / I2S0, master)
    ESP32-S3 GPIO16 --- BCLK in  (bus B / I2S1, slave)  --- jumper to GPIO5
    ESP32-S3 GPIO17 --- WS   in  (bus B / I2S1, slave)  --- jumper to GPIO6
    GPIO7  --- SD of mic0 (L/R=GND) and mic1 (L/R=VDD)   [bus A]
    GPIO15 --- SD of mic2 (L/R=GND) and mic3 (L/R=VDD)   [bus B]

Bus A and bus B's BCLK/WS are deliberately DIFFERENT GPIO numbers, jumpered
together into one electrical net -- not the same GPIO doing double duty as
both an output (master) and input (slave). See `config.h` for why. If you
still want to try the same-GPIO-number wiring instead (fewer jumpers), that
is what this test would need to validate; the separate-GPIO wiring above
avoids needing to test that specific question at all.

## Procedure
1. Flash a build that dumps raw 4-channel frames over USB serial or to SD.
2. Clap once. Capture ~1 s.
3. Run `python tdoa/calibration.py` -> `measure_sync_offsets()`.
4. Repeat across **10 power cycles**.

## Reading the result
| outcome | meaning | action |
|---|---|---|
| offset 0 every boot | peripherals start together | nothing to do |
| same non-zero offset every boot | constant hardware skew | pass to `tdoa_set_bus_skew_samples()` in `app_main()` |
| offset changes per boot, small (<3 samp) | startup race | re-measure at boot, or accept +/-3 samp error (~8 deg) |
| offset changes per boot, large/unstable | inter-peripheral sync unusable | **fall back to plan B** |

## Plan B (if sync fails)
Use ONE I2S peripheral with 2 mics (LEFT/RIGHT) only. That pair is
sample-exact by construction. You get the left/right axis from TDoA and
resolve front/back from the level difference between the two remaining mics
on the second bus (level comparison does not need sample sync). Accuracy
drops, but end-to-end still works, which matters more in 5 days.
