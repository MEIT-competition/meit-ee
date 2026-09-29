# Stereo channel and delay validation

The current design uses one stereo I2S peripheral and two INMP441 microphones.
There is no inter-peripheral skew correction or second acquisition task.

1. Verify GPIO5=BCLK, GPIO6=WS, GPIO7=shared SD. LEFT L/R=GND and RIGHT L/R=3V3.
2. Build/flash `hardware_tests/stereo_i2s`. Capture waits by draining five 1024-sample
   stereo frames, longer than the startup interval. It then records 8192 samples
   per channel before printing, so serial output cannot interrupt that recording.
3. Use `python tdoa/parse_dump.py capture.txt capture.npy` from the repo root.
   The array shape is `(2,8192)`, rows LEFT then RIGHT.
4. Make sound close to each microphone separately and check the corresponding RMS
   and dump channel. A synthetic slot test proves indexing, not actual strap wiring.
5. Use a broadband impulse from the physical LEFT: measured LEFT-minus-RIGHT delay
   should be negative. From RIGHT it should be positive. A narrow pure tone is not
   sufficient to validate correlation peak selection.
6. Repeat center-axis recordings with the final spacing and worn geometry.
   `python tdoa/calibration.py capture.npy` reports a center bias and spread.
   Set `TDOA_LR_BIAS_SAMPLES` only from repeatable data, and choose
   `TDOA_THRESHOLD_SAMPLES` to encompass acceptable center jitter without erasing
   LEFT/RIGHT separation. The initial values 0 and 2 samples are uncalibrated.
7. Repeat with horn/siren/noise/reflections and motors on/off. Firmware and Python
   confidence statistics differ (mean versus median correlation floor); calibrate
   `MIN_CONFIDENCE` from firmware logs rather than the reference score.

Boundary policy is inclusive: -T and +T both map to BACK; below -T is LEFT;
above +T is RIGHT. Silence or unreliable correlation is UNKNOWN, not BACK.

With two lateral microphones, front/back cannot be physically disambiguated by
TDoA. FRONT is excluded by the prototype's operating condition and valid
center-axis delays are labeled BACK.

If all samples are zero, DMA reads alone do not prove external microphone clocks,
power or SD drive. Check startup, CHIPEN/VDD/GND and the actual mic-side
BCLK/WS/SD signals. The historical `../mic_bringup_log.txt` is from retired hardware.
