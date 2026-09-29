#pragma once
// ===== LOLIN S3 V1.0.0 / ESP32-S3 =====
// !! Verify every GPIO against the LOLIN S3 pinout before powering up. !!

#define TDOA_FS_HZ        48000
#define AI_FS_HZ          16000          // 48k / 3, integer decimation
#define DECIM             (TDOA_FS_HZ / AI_FS_HZ)
#define FRAME_LEN         1024           // 21.3 ms @48k. FFT size = 2048.
#define FFT_N             2048
#define NUM_MICS          2
#include "direction.h"

// Single I2S0 master, Philips stereo: LEFT slot first, RIGHT slot second.
#define I2S_BCLK          GPIO_NUM_5
#define I2S_WS            GPIO_NUM_6
#define I2S_DIN           GPIO_NUM_7
// Drain > 2^18 startup SCK cycles (4096 stereo frames at 64 clocks/frame).
#define I2S_WARMUP_FRAMES 5

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

// ========================================================================
// MPU6050 I2C is assigned to GPIO41/42 below. ESP32-S3's I2C peripheral is
// GPIO-matrix-routable (not pinned to fixed hardware pins), so any free
// GPIO works electrically; GPIO41/42 avoid every other subsystem's pins
// (see PINMAP.md's GPIO summary/reserved tables). Whether the LOLIN S3
// silkscreen also labels these as "the" I2C pins is a board-labeling
// convenience, not a functional requirement -- confirm against the actual
// board before wiring if that label matters to you.
// microSD SPI pins are reserved here for future SD logging implementation.
// ========================================================================

// --- MPU6050 / GY-521 : I2C ---
#define I2C_SDA_PIN       GPIO_NUM_42
#define I2C_SCL_PIN       GPIO_NUM_41

// --- microSD : SPI ---
#define SD_SCK_PIN        GPIO_NUM_12
#define SD_MOSI_PIN       GPIO_NUM_14
#define SD_MISO_PIN       GPIO_NUM_18
#define SD_CS_PIN         GPIO_NUM_21


// DMA slot order: L/R=GND -> CH_LEFT, L/R=3V3 -> CH_RIGHT.
enum { CH_LEFT = 0, CH_RIGHT = 1 };

// Measure actual lateral microphone spacing and calibrate on the wearer.
#define SPEED_OF_SOUND    343.0f
#define MIC_SPACING_M     0.16f
#define TAU_MARGIN        1.6f
// Inclusive center band [-T,+T] maps to BACK in the restricted domain.
// TODO(calibration): 2 samples is provisional; measure bias and threshold
// on the final geometry using docs/two_mic_bringup.md before acceptance.
#define TDOA_THRESHOLD_SAMPLES 2.0f
// Measured center-axis bias: subtract from LEFT-minus-RIGHT delay.
#define TDOA_LR_BIAS_SAMPLES   0.0f

// --- GCC-PHAT ---
#define PHAT_FMIN_HZ      200.0f
#define PHAT_FMAX_HZ      4000.0f
#define PHAT_BIN_FLOOR    0.0316f        // -30 dB relative to strongest bin
// NOT validated against real hardware, and NOT numerically comparable to
// the Python reference's confidence (tdoa/gcc_phat.py) -- the two use
// different noise-floor statistics (Python: median of the correlation
// window; C: mean of abs(), see tdoa.c). Both direction paths apply a Hann
// window before the FFT. Same underlying idea, different numbers. Do not
// treat 0.15 as validated by the Python synthetic tests passing; once real
// mics are in, log conf_lr/confidence (direction_t, tdoa.h) for
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

// --- Two DRV8833 boards, A channel only, two motors, LEDC PWM ---
enum { MOTOR_LEFT = 0, MOTOR_RIGHT = 1, NUM_MOTORS = 2 };
#define MOTOR_LEFT_GPIO   GPIO_NUM_13
#define MOTOR_RIGHT_GPIO  GPIO_NUM_1
#define MOTOR_MASK_LEFT  (1u << MOTOR_LEFT)
#define MOTOR_MASK_RIGHT (1u << MOTOR_RIGHT)
#define MOTOR_MASK_BOTH  (MOTOR_MASK_LEFT | MOTOR_MASK_RIGHT)
// One input per motor is PWM-driven and the partner input should be held LOW.
// For the Adafruit DRV8833 breakout, SLP must be HIGH for the outputs to run.
// MVP wiring uses SLP strapped to 3V3, so firmware control is disabled here.
#define MOTOR_SLEEP_GPIO  (-1)

// Motor drive limit. MOTOR_SUPPLY_MV is the worst-case MAXIMUM of the motor
// supply rail, not a momentary meter reading. If VM is raw LiPo, keep 4200.
// Only change it when a dedicated regulated motor rail is confirmed. Do not
// power all motors from the ESP32 board's 3V3 rail.
// !! STALE FOR CURRENT BRING-UP HARDWARE: motors now run from a 4xAA pack,
// !! not a 1S LiPo. 4200 is NOT the worst case for 4xAA, so the cap below
// !! would over-drive 3 V motors. Before any motor test, read the cell
// !! chemistry off the label and set this to 4 x that chemistry's fresh-cell
// !! maximum (from the cell datasheet). Deliberately NOT changed here because
// !! the chemistry/voltage is not yet known.
// TODO(power): 4200 mV is PROVISIONAL. Confirm battery maximum VM and
// actual motor rating before powered tests; do not guess a replacement.
#define MOTOR_SUPPLY_MV   4200
#define MOTOR_RATED_MV    3000
#define MOTOR_DUTY_MAX    255

#define MOTOR_DUTY_CAP_RAW ((MOTOR_DUTY_MAX * MOTOR_RATED_MV) / MOTOR_SUPPLY_MV)
#define MOTOR_DUTY_CAP     (MOTOR_DUTY_CAP_RAW > MOTOR_DUTY_MAX \
                            ? MOTOR_DUTY_MAX : MOTOR_DUTY_CAP_RAW)
_Static_assert(MOTOR_SUPPLY_MV > 0,
               "MOTOR_SUPPLY_MV must be > 0 (it divides MOTOR_DUTY_CAP)");
_Static_assert(MOTOR_RATED_MV > 0,
               "MOTOR_RATED_MV must be > 0");
_Static_assert(MOTOR_DUTY_CAP > 0,
               "MOTOR_DUTY_CAP computed to 0 -- motors would never move. "
               "Check MOTOR_SUPPLY_MV / MOTOR_RATED_MV.");
_Static_assert(MOTOR_DUTY_CAP <= MOTOR_DUTY_MAX,
               "MOTOR_DUTY_CAP must fit the uint8_t duty scale");

// Initial PWM carrier. Confirm on hardware by comparing vibration strength,
// audible noise and low-duty startup behavior before finalizing.
#define MOTOR_PWM_FREQ_HZ 20000
#define MOTOR_PWM_RES     LEDC_TIMER_8_BIT
extern const int MOTOR_GPIO[NUM_MOTORS];

// --- AI team interface (meit-ai, README "출력 포맷" / decision/*.py) ---
// Keep these in sync with meit-ai by hand -- there is no shared repo for it.
// See firmware/PROTOCOL.md for the exact byte layout this backs.
#define GATING_MS         250    // meit-ai decision/patterns.py GATING_MS.
                                 // Only affects which pattern length the AI
                                 // side chooses; nothing here reads it, but
                                 // if it changes, PATTERN_MAX_PAIRS below
                                 // may need to grow (siren FULL = 3 pairs).
#define PATTERN_MAX_PAIRS 4
#define UNKNOWN_SWEEP_ON_MS  80   // DIR_UNKNOWN: LEFT then RIGHT, one motor at a time
#define UNKNOWN_SWEEP_OFF_MS 40   // gap between the two motors

// meit-ai classifier/adapter.py CLASSES = ["horn","siren","crash","normal"].
// "normal" never reaches firmware -- decision/judge.py returns None for it.
enum { SOUND_CLASS_HORN = 0, SOUND_CLASS_SIREN = 1, SOUND_CLASS_CRASH = 2,
      SOUND_CLASS_NONE = 0xFF };

// --- Per-event audio clip ---
// TODO(AI contract): current AUDIO is 16 kHz PCM16, 40960 samples = 2.56 s.
// Confirm whether AI requires exactly 2.50 s / 40000 samples and who crops.
// Keep the existing interface until that contract is confirmed.
#define CLIP_FRAMES 120   // 120 x 1024 / 48 kHz = 2.56 s per event
// Computed as (FRAME_LEN * CLIP_FRAMES) / DECIM, NOT (FRAME_LEN/DECIM) *
// CLIP_FRAMES -- the latter truncates 1024/3 to 341 before multiplying,
// undercounting by 24 samples over 24 frames vs. what resample_48k_to_16k
// actually produces once its decimation phase is correctly persistent
// (342 samples on most frames, 341 on others; 40960 total at 120 frames). Verified this
// divides evenly for the current FRAME_LEN/CLIP_FRAMES/DECIM; if you change
// any of the three, check FRAME_LEN*CLIP_FRAMES % DECIM == 0 still holds.
#define CLIP_OUT_SAMPLES  ((FRAME_LEN * CLIP_FRAMES) / DECIM)

// First N frames (~N*21ms) of a new event are used to vote on direction
// instead of trusting a single 21ms frame. A car horn's first frame can be
// ambiguous or reflection-heavy; averaging over ~125ms costs little given
// the AI needs the full 2.56 s clip anyway. See main.c.
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
// of redundant clips instead of one. GATING_MS (250, meit-ai's own pattern-
// length budget) doubles as this cooldown so the belt doesn't re-arm faster
// than meit-ai would want to re-decide anyway.
#define COOLDOWN_FRAMES   ((GATING_MS * TDOA_FS_HZ) / (FRAME_LEN * 1000))

// Conservative cap on one BLE audio chunk's PCM payload, independent of the
// actual negotiated ATT MTU (which can vary 20-512 bytes) -- bounds a
// fixed-size stack buffer in ble_svc.c regardless of what gets negotiated.
#define BLE_AUDIO_MAX_PAYLOAD 240

// ---- Bring-up only: synthetic events (no microphones needed) ----
// 1 = replace the microphone capture task with fake_event_task (main.c): one
// event every MEIT_FAKE_EVENT_PERIOD_MS while BLE is connected, direction
// cycling LEFT / RIGHT / BACK, fixed 1 kHz tone clip. Lets BLE -> laptop -> CMD -> motor be
// tested while the microphone hardware is still dead. MUST be 0 for demo.
#define MEIT_FAKE_EVENTS          0
#define MEIT_FAKE_EVENT_PERIOD_MS 10000
