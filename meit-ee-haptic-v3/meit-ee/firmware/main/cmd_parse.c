// BLE CMD v2 / v3 packet parser. See cmd_parse.h for why this is its own file.
//
//   v2  [0]=0xA5 [1]=0x02 [2]=seq [3]=dir [4]=intensity [5]=n
//       then n * {on_ms/10, off_ms/10}
//       One motor mask for the whole pattern, derived from the direction byte.
//
//   v3  [0]=0xA5 [1]=0x03 [2]=seq [3]=dir [4]=intensity [5]=n
//       [6..7] = two bits of motor mask per step, little-endian
//       then n * {on_ms/10, off_ms/10}
//       Per-step masks, which is what lets BACK be a left-to-right sweep rather
//       than another both-motors buzz.
//
// Nothing here trusts the sender. A packet is rejected outright rather than
// clamped into something playable: a pattern the wearer cannot interpret is
// worse than no vibration, because it teaches them to ignore the belt.
#include <string.h>
#include "cmd_parse.h"

cmd_parse_result_t cmd_parse(const uint8_t *data, uint16_t len, cmd_packet_t *out)
{
    if (data == NULL || out == NULL) return CMD_PARSE_BAD_FORMAT;
    if (len < CMD_HEADER_V2) return CMD_PARSE_BAD_LENGTH;

    const uint8_t version = data[1];
    if (data[0] != CMD_MAGIC) return CMD_PARSE_BAD_FORMAT;
    if (version != CMD_VERSION_V2 && version != CMD_VERSION_V3)
        return CMD_PARSE_BAD_FORMAT;
    const bool v3 = (version == CMD_VERSION_V3);

    const uint8_t direction = data[3];
    const uint8_t intensity = data[4];
    const uint8_t n = data[5];

    const uint8_t header = v3 ? CMD_HEADER_V3 : CMD_HEADER_V2;
    const uint8_t max_steps = v3 ? PATTERN_MAX_STEPS : PATTERN_MAX_PAIRS_V2;
    // BACK needs per-step masks, so a v2 packet claiming it is malformed rather
    // than merely unsupported.
    const uint8_t max_dir = v3 ? CMD_DIR_MAX : CMD_DIR_RIGHT;

    // The v3 header itself must be present before its mask bytes are read.
    if (len < header) return CMD_PARSE_BAD_LENGTH;
    if (direction > max_dir || intensity > 100 || n > max_steps)
        return CMD_PARSE_BAD_FORMAT;
    if ((uint16_t)(header + 2 * n) != len) return CMD_PARSE_BAD_LENGTH;

    memset(out, 0, sizeof(*out));
    out->version = version;
    out->sequence = data[2];
    out->direction = direction;

    // STOP is the only command allowed to carry no pattern, and it must carry
    // nothing else either, so a truncated or zero-filled buffer cannot be
    // mistaken for a valid stop.
    if (direction == CMD_DIR_STOP) {
        if (n != 0 || intensity != 0) return CMD_PARSE_BAD_FORMAT;
        out->intensity = 0;
        out->n_steps = 0;
        return CMD_PARSE_OK;
    }
    if (n == 0) return CMD_PARSE_BAD_FORMAT;

    if (v3) {
        const uint32_t mask_bits = (uint32_t)data[6] | ((uint32_t)data[7] << 8);
        for (int i = 0; i < n; i++) {
            const uint8_t mask = (uint8_t)((mask_bits >> (2 * i)) & MOTOR_MASK_BOTH);
            // 0b00 selects no motor: a silent hole inside an alert.
            if (mask == 0) return CMD_PARSE_BAD_FORMAT;
            out->masks[i] = mask;
        }
        // Mask bits past the declared step count mean the sender and this
        // firmware disagree about the pattern length.
        if (mask_bits >> (2 * n)) return CMD_PARSE_BAD_FORMAT;
    } else {
        const uint8_t mask = motor_mask_for_cmd_direction(direction);
        if (mask == 0) return CMD_PARSE_BAD_FORMAT;
        for (int i = 0; i < n; i++) out->masks[i] = mask;
    }

    for (int i = 0; i < n; i++) {
        const uint8_t on10 = data[header + 2 * i];
        const uint8_t off10 = data[header + 2 * i + 1];
        // A zero ON time is a step that is never felt; the encoder never emits
        // one, so its presence means a corrupted or hand-rolled packet.
        if (on10 == 0) return CMD_PARSE_BAD_FORMAT;
        out->steps[i].on_ms = (uint16_t)on10 * 10;
        out->steps[i].off_ms = (uint16_t)off10 * 10;
    }

    out->intensity = intensity;
    out->n_steps = n;
    return CMD_PARSE_OK;
}
