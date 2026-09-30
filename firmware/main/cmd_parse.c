// BLE CMD v2 packet parser. See cmd_parse.h for why this is its own file.
//
//   [0]=0xA5 [1]=0x02 [2]=seq [3]=dir [4]=intensity [5]=n
//   then n * {on_ms/10, off_ms/10}
//
// One motor mask applies to the whole pattern and comes from the direction byte.
//
// Nothing here trusts the sender. A packet is rejected outright rather than
// clamped into something playable: a pattern the wearer cannot interpret is
// worse than no vibration, because it teaches them to ignore the belt.
#include <string.h>
#include "cmd_parse.h"

cmd_parse_result_t cmd_parse(const uint8_t *data, uint16_t len, cmd_packet_t *out)
{
    if (data == NULL || out == NULL) return CMD_PARSE_BAD_FORMAT;
    if (len < CMD_HEADER) return CMD_PARSE_BAD_LENGTH;

    if (data[0] != CMD_MAGIC || data[1] != CMD_VERSION)
        return CMD_PARSE_BAD_FORMAT;

    const uint8_t direction = data[3];
    const uint8_t intensity = data[4];
    const uint8_t n = data[5];

    if (direction > CMD_DIR_MAX || intensity > 100 || n > PATTERN_MAX_STEPS)
        return CMD_PARSE_BAD_FORMAT;
    if ((uint16_t)(CMD_HEADER + 2 * n) != len) return CMD_PARSE_BAD_LENGTH;

    memset(out, 0, sizeof(*out));
    out->sequence = data[2];
    out->direction = direction;

    // STOP is the only command allowed to carry no pattern, and it must carry
    // nothing else either, so a truncated or zero-filled buffer cannot be
    // mistaken for a valid stop.
    if (direction == CMD_DIR_STOP) {
        if (n != 0 || intensity != 0) return CMD_PARSE_BAD_FORMAT;
        return CMD_PARSE_OK;
    }
    if (n == 0) return CMD_PARSE_BAD_FORMAT;

    const uint8_t mask = motor_mask_for_cmd_direction(direction);
    if (mask == 0) return CMD_PARSE_BAD_FORMAT;

    for (int i = 0; i < n; i++) {
        const uint8_t on10 = data[CMD_HEADER + 2 * i];
        const uint8_t off10 = data[CMD_HEADER + 2 * i + 1];
        // A zero ON time is a step that is never felt; the encoder never emits
        // one, so its presence means a corrupted or hand-rolled packet.
        if (on10 == 0) return CMD_PARSE_BAD_FORMAT;
        out->steps[i].on_ms = (uint16_t)on10 * 10;
        out->steps[i].off_ms = (uint16_t)off10 * 10;
    }

    out->mask = mask;
    out->intensity = intensity;
    out->n_steps = n;
    return CMD_PARSE_OK;
}
