#pragma once
// ===== LOLIN S3 V1.0.0 / ESP32-S3 =====
// !! Verify every GPIO against the LOLIN S3 pinout before powering up. !!

#define TDOA_FS_HZ        48000
#define AI_FS_HZ          16000          // 48k / 3, integer decimation
#define DECIM             (TDOA_FS_HZ / AI_FS_HZ)
#define FRAME_LEN         1024           // 21.3 ms @48k. FFT size = 2048.
#define FFT_N             2048
#define NUM_MICS          4

// --- I2S bus A (master, generates BCLK/WS) ---
#define I2S_A_BCLK        GPIO_NUM_5
#define I2S_A_WS          GPIO_NUM_6
#define I2S_A_DIN         GPIO_NUM_7     // mic FRONT (L/R=GND) + RIGHT (L/R=VDD)
// --- I2S bus B (slave, BCLK/WS wired to bus A's net via a jumper) ---
#define I2S_B_BCLK        GPIO_NUM_16
#define I2S_B_WS          GPIO_NUM_17
#define I2S_B_DIN         GPIO_NUM_15    // mic BACK (L/R=GND) + LEFT (L/R=VDD)
// GPIO_NUM_16/17 are placeholders, same VERIFY-against-the-real-board
// caveat as every other GPIO in this file.
//
// DELIBERATELY NOT the same GPIO numbers as bus A's BCLK/WS. An earlier
// draft declared I2S_B_BCLK/WS as GPIO5/6 too, i.e. one ESP32 pin
// configured as output for bus A (master) and input for bus B (slave) at
// the same time. Whether two different I2S peripherals can share one
// physical GPIO that way isn't something the API reference actually
// promises either way, so it's a real unknown to carry into hardware
// bring-up for no real benefit -- wiring is functionally the same either
// way. Wire it instead as:
//   ESP32 GPIO5  --(I2S0/bus A, output)--+
//                                        +-- physical jumper --+
//   ESP32 GPIO16 --(I2S1/bus B, input) --+                     |
//                                            (same for WS: 6 <-> 17)
// Same electrical net (BCLK/WS truly shared, so no sample clock drift
// between the two peripherals -- that part of the design is unchanged),
// but ESP32 sees two distinct GPIO numbers, one output-only and one
// input-only, instead of one pin doing double duty. LOLIN S3 has enough
// free GPIOs that this costs nothing but two jumper wires.

// ESP-IDF's I2S DMA requires each individual DMA buffer/descriptor to stay
// at or under 4092 bytes -- a hardware descriptor length-field limit, not
// something the driver silently works around. At 32-bit stereo (2 slots x
// 4 bytes = 8 bytes/frame), that caps a descriptor at floor(4092/8) = 511
// frames. This is a SEPARATE thing from FRAME_LEN (the TDoA/FFT frame
// size, 1024): i2s_channel_read() pulls from as many of these smaller
// descriptors as it needs to fill the caller's larger request, so
// FRAME_LEN does not need to be a multiple of I2S_DMA_FRAMES. An earlier
// draft set cc.dma_frame_num = FRAME_LEN directly, making every descriptor
// 1024*2*4 = 8192 bytes -- double the hardware limit.
#define I2S_DMA_FRAMES    480   // 480*8 = 3840 bytes/descriptor, under 4092

// Channel order used everywhere: FRONT, RIGHT, BACK, LEFT
enum { CH_FRONT = 0, CH_RIGHT = 1, CH_BACK = 2, CH_LEFT = 3 };

// --- geometry (METRES, +x right, +y front). MEASURE THE REAL BELT. ---
#define SPEED_OF_SOUND    343.0f
#define MIC_RADIUS_M      0.08f
#define TAU_MARGIN        1.6f           // torso diffraction path is longer

// --- GCC-PHAT ---
#define PHAT_FMIN_HZ      200.0f
#define PHAT_FMAX_HZ      4000.0f
#define PHAT_BIN_FLOOR    0.0316f        // -30 dB relative to strongest bin
// NOT validated against real hardware, and NOT numerically comparable to
// the Python reference's confidence (tdoa/gcc_phat.py) -- the two use
// different noise-floor statistics (Python: median of the correlation
// window; C: mean of abs(), see tdoa.c) and only the C path applies a Hann
// window before the FFT. Same underlying idea, different numbers. Do not
// treat 0.15 as validated by the Python synthetic tests passing; once real
// mics are in, log conf_lr/conf_fb/confidence (direction_t, tdoa.h) for
// horn/siren/crash/normal samples and pick a real threshold from that --
// see main.c's EV_VOTING logging.
#define MIN_CONFIDENCE    0.15f

// This is a PRE-gate only, to cut BLE traffic during real silence -- the
// actual "is this worth alerting on" call belongs to meit-ai's own
// DB_GATE (decision/judge.py, -50.0 dBFS as of the snapshot this was
// checked against). If this MCU-side gate were stricter (higher/less
// negative) than meit-ai's, a sound between the two thresholds would be
// dropped here before the AI ever saw it -- silently lowering Recall
// exactly the way this project's whole design says not to. Keep this
// LOOSER (more negative) than whatever meit-ai's DB_GATE currently is, and
// re-check both sides stay related if either changes. -45 was a placeholder
// carried over from an earlier draft and was NOT actually looser than
// meit-ai's -50 -- fixed to -60 here, but confirm the real relationship
// once meit-ai's DB_GATE is measured against real hardware, not guessed.
#define RMS_GATE_DBFS     -60.0f

// --- DRV8833 x4 -> 8 motors, LEDC PWM ---
// Each DRV8833 half-bridge has AIN1/AIN2; this design PWMs one input and
// ties the other LOW for single-direction (vibration-only) drive -- document
// that tie explicitly on the schematic, it's not automatic.
// SLP (sleep, active HIGH) defaults LOW on Adafruit's DRV8833 breakout: tie
// all 4 SLP pins to 3.3V so the drivers are enabled, or the motors simply
// won't turn no matter what this firmware does. UNVERIFIED against your
// specific breakout -- check its schematic, this is the Adafruit one.
#define MOTOR_PWM_FREQ_HZ 20000
#define MOTOR_PWM_RES     LEDC_TIMER_8_BIT
extern const int MOTOR_GPIO[8];   // GPIO3 avoided deliberately -- see main.c

// --- AI team interface (meit-ai, README "출력 포맷" / decision/*.py) ---
// Keep these in sync with meit-ai by hand -- there is no shared repo for it.
// See firmware/PROTOCOL.md for the exact byte layout this backs.
#define DIR_UNKNOWN       0xFF   // wire value for "direction: -1" (판별 불가)
#define GATING_MS         300    // meit-ai decision/patterns.py GATING_MS.
                                 // Only affects which pattern length the AI
                                 // side chooses; nothing here reads it, but
                                 // if it changes, PATTERN_MAX_PAIRS below
                                 // may need to grow (siren FULL = 3 pairs).
#define PATTERN_MAX_PAIRS 4

// meit-ai classifier/adapter.py CLASSES = ["horn","siren","crash","normal"].
// "normal" never reaches firmware -- decision/judge.py returns None for it.
enum { SOUND_CLASS_HORN = 0, SOUND_CLASS_SIREN = 1, SOUND_CLASS_CRASH = 2,
      SOUND_CLASS_NONE = 0xFF };

// --- Per-event audio clip ---
#define CLIP_FRAMES       24   // ~0.5 s of audio per event
// Computed as (FRAME_LEN * CLIP_FRAMES) / DECIM, NOT (FRAME_LEN/DECIM) *
// CLIP_FRAMES -- the latter truncates 1024/3 to 341 before multiplying,
// undercounting by 24 samples over 24 frames vs. what resample_48k_to_16k
// actually produces once its decimation phase is correctly persistent
// (342 samples on most frames, 341 on others, 8192 total). Verified this
// divides evenly for the current FRAME_LEN/CLIP_FRAMES/DECIM; if you change
// any of the three, check FRAME_LEN*CLIP_FRAMES % DECIM == 0 still holds.
#define CLIP_OUT_SAMPLES  ((FRAME_LEN * CLIP_FRAMES) / DECIM)

// First N frames (~N*21ms) of a new event are used to vote on direction
// instead of trusting a single 21ms frame. A car horn's first frame can be
// ambiguous or reflection-heavy; averaging over ~125ms costs little given
// the AI needs the full ~0.5s clip anyway. See main.c.
#define TDOA_VOTE_FRAMES  6

// A single confident-looking frame must not win the vote outright just
// because a couple of OTHER frames also happened to read some (different)
// direction. This gates on the WINNING bin's own frame count
// (dir_counts[best] in main.c), not the total number of valid frames
// across all bins -- checking the total instead of the per-bin count was
// an earlier bug that let e.g. RIGHT-once + LEFT-once + 4-unknown confirm
// RIGHT. See main.c.
#define TDOA_MIN_VALID_VOTES 2

// How many recent {event_id -> direction} pairs to remember so a CMD
// arriving after a newer event has already started still fires the right
// motor. See main.c / PROTOCOL.md ("event_id" section).
#define EVENT_HISTORY     8

// Cooldown after an event's clip finishes, before a new one can start.
// Without this, a sustained loud sound (a real siren, for instance) would
// re-trigger a brand new event on literally the next 21ms frame, back to
// back, indefinitely -- flooding the BLE queue and giving the AI a stream
// of redundant clips instead of one. GATING_MS (300, meit-ai's own pattern-
// length budget) doubles as this cooldown so the belt doesn't re-arm faster
// than meit-ai would want to re-decide anyway.
#define COOLDOWN_FRAMES   ((GATING_MS * TDOA_FS_HZ) / (FRAME_LEN * 1000))

// Conservative cap on one BLE audio chunk's PCM payload, independent of the
// actual negotiated ATT MTU (which can vary 20-512 bytes) -- bounds a
// fixed-size stack buffer in ble_svc.c regardless of what gets negotiated.
#define BLE_AUDIO_MAX_PAYLOAD 240
