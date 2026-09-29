#pragma once
#include <stdint.h>
#include "tdoa.h"
#include "motor.h"
// NimBLE. Three characteristics. See firmware/PROTOCOL.md for the full
// byte-level spec matched against meit-ai's actual output dict.
//
//   AUDIO notify : 16 kHz mono PCM16, chunked (a single 2.56 s clip is far
//                  larger than one ATT packet -- see ble_svc.c). Each
//                  notify is:
//                    byte0  event_id
//                    byte1  chunk_index (0-based, wraps past 255 -- use to
//                           DETECT LOSS: a chunk can be dropped after 3
//                           failed retries, see ble_svc.c. Receiver should
//                           treat a gap as a signal to discard or flag the
//                           event, not just concatenate what arrived)
//                    byte2  flags, bit0 = last chunk of this event
//                    byte3.. PCM16 samples, little-endian
//                  Sent only when the loudness gate opens (see main.c).
//                  IMPORTANT: chunk count depends entirely on the
//                  negotiated ATT MTU. If MTU negotiation fails and the
//                  connection stays at the BLE default (23 bytes), one
//                  2.56 s/40960-sample clip needs on the order of 4000+
//                  chunks -- likely too slow for a live safety alert.
//                  Confirm MTU actually negotiates above default on real
//                  hardware before treating end-to-end latency as solved.
//
//   DIR   notify : 4 bytes {event_id, dir_byte, confidence_q8, rms_dbfs_i8}
//                  dir_byte is LEFT=6, RIGHT=2, BACK=4, or DIR_UNKNOWN (0xFF) for "-1 판별 불가"
//                  -- confidence_q8 will be 0 exactly when dir_byte is
//                  DIR_UNKNOWN, never a fabricated direction. Sent once per
//                  event, after main.c's multi-frame direction vote
//                  completes (not on the very first frame -- see
//                  TDOA_VOTE_FRAMES in config.h).
//
//   CMD   write  : meit-ai's judge() output dict, minus `direction` as a
//                  literal value (see event_id below) and minus the string
//                  fields (pattern_name/sound_class are redundant with a
//                  numeric id here).
//                    byte0        event_id        (echoes the DIR this
//                                                  command responds to --
//                                                  see PROTOCOL.md)
//                    byte1        intensity_pct   (0..100)
//                    byte2        sound_class     (0=horn,1=siren,2=crash,
//                                                  0xFF=none; for on-device
//                                                  logging only)
//                    byte3        n_pairs         (1..PATTERN_MAX_PAIRS)
//                    byte4..      n_pairs * {on_ms/10 : u8, off_ms/10 : u8}
//                  Max payload 4 + 2*4 = 12 bytes, fits one ATT packet
//                  without MTU negotiation.
void ble_svc_init(void);
bool ble_svc_connected(void);
int  ble_svc_send_audio(uint8_t event_id, const int16_t *pcm, int n);
int  ble_svc_send_direction(uint8_t event_id, uint8_t dir_byte,
                            float confidence, float rms_dbfs);

typedef void (*ble_cmd_cb_t)(uint8_t event_id, uint8_t intensity_pct,
                             uint8_t sound_class, const motor_step_t *steps,
                             int n_steps);
void ble_svc_set_cmd_cb(ble_cmd_cb_t cb);
