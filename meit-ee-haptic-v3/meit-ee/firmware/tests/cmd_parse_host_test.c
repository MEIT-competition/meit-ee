// Compiles the real firmware/main/cmd_parse.c on the host and exercises it
// against (a) hand-written malformed packets and (b) golden packets produced by
// the laptop encoder in laptop/protocol.py.
//
// (b) is the part that matters most: it makes the Python encoder and the C
// parser prove they agree, so a change to one cannot silently desync the wire
// format. See firmware/tests/run_cmd_parse_tests.py.
#include <stdio.h>
#include <string.h>
#include "cmd_parse.h"
#include "golden.h"

static int failures, checks;

#define CHECK(cond, ...) do { \
    checks++; \
    if (!(cond)) { \
        failures++; \
        printf("  FAIL line %d: ", __LINE__); \
        printf(__VA_ARGS__); \
        printf("\n"); \
    } \
} while (0)

// ------------------------------------------------------------- golden vectors
static void test_golden(void)
{
    printf("golden vectors from laptop/protocol.py (%d)\n", (int)GOLDEN_COUNT);
    for (size_t g = 0; g < GOLDEN_COUNT; g++) {
        const golden_case_t *c = &GOLDEN[g];
        cmd_packet_t packet;
        cmd_parse_result_t rc = cmd_parse(c->data, (uint16_t)c->len, &packet);
        CHECK(rc == CMD_PARSE_OK, "%s: parser rejected a valid packet (rc=%d)",
              c->name, (int)rc);
        if (rc != CMD_PARSE_OK) continue;

        CHECK(packet.version == c->version, "%s: version %u != %d",
              c->name, packet.version, c->version);
        CHECK(packet.direction == c->direction, "%s: direction %u != %d",
              c->name, packet.direction, c->direction);
        CHECK(packet.intensity == c->intensity, "%s: intensity %u != %d",
              c->name, packet.intensity, c->intensity);
        CHECK(packet.n_steps == c->n_steps, "%s: n_steps %d != %d",
              c->name, packet.n_steps, c->n_steps);
        if (packet.n_steps != c->n_steps) continue;

        for (int i = 0; i < c->n_steps; i++) {
            CHECK(packet.masks[i] == c->masks[i],
                  "%s: step %d mask %u != %u", c->name, i,
                  packet.masks[i], c->masks[i]);
            CHECK(packet.steps[i].on_ms == c->on_ms[i],
                  "%s: step %d on_ms %u != %u", c->name, i,
                  packet.steps[i].on_ms, c->on_ms[i]);
            CHECK(packet.steps[i].off_ms == c->off_ms[i],
                  "%s: step %d off_ms %u != %u", c->name, i,
                  packet.steps[i].off_ms, c->off_ms[i]);
        }
    }
}

// ------------------------------------------------------------ malformed input
static void expect_reject(const char *name, const uint8_t *data, int len)
{
    cmd_packet_t packet;
    cmd_parse_result_t rc = cmd_parse(data, (uint16_t)len, &packet);
    CHECK(rc != CMD_PARSE_OK, "%s: parser accepted a packet it must reject", name);
}

static void test_rejects(void)
{
    printf("malformed packets\n");

    // A valid v3 LEFT command, mutated one field at a time below.
    const uint8_t ok[] = {CMD_MAGIC, CMD_VERSION_V3, 7, CMD_DIR_LEFT, 80, 1,
                          MOTOR_MASK_LEFT, 0x00, 30, 0};
    cmd_packet_t packet;
    CHECK(cmd_parse(ok, sizeof(ok), &packet) == CMD_PARSE_OK,
          "baseline v3 packet must parse");

    uint8_t bad[sizeof(ok)];

    memcpy(bad, ok, sizeof(ok)); bad[0] = 0x5A;
    expect_reject("bad magic", bad, sizeof(bad));

    memcpy(bad, ok, sizeof(ok)); bad[1] = 0x04;
    expect_reject("unknown version", bad, sizeof(bad));

    memcpy(bad, ok, sizeof(ok)); bad[3] = CMD_DIR_MAX + 1;
    expect_reject("direction out of range", bad, sizeof(bad));

    memcpy(bad, ok, sizeof(ok)); bad[4] = 101;
    expect_reject("intensity above 100", bad, sizeof(bad));

    memcpy(bad, ok, sizeof(ok)); bad[5] = PATTERN_MAX_STEPS + 1;
    expect_reject("step count above the pattern budget", bad, sizeof(bad));

    memcpy(bad, ok, sizeof(ok)); bad[6] = 0x00;
    expect_reject("step mask selects no motor", bad, sizeof(bad));

    // Declared 1 step, but mask bits set for a second one.
    memcpy(bad, ok, sizeof(ok)); bad[6] = MOTOR_MASK_LEFT | (MOTOR_MASK_RIGHT << 2);
    expect_reject("mask bits beyond the declared step count", bad, sizeof(bad));

    memcpy(bad, ok, sizeof(ok)); bad[8] = 0;
    expect_reject("zero ON time", bad, sizeof(bad));

    // Length disagreements.
    expect_reject("truncated below the v2 header", ok, CMD_HEADER_V2 - 1);
    expect_reject("v3 header truncated", ok, CMD_HEADER_V3 - 1);
    expect_reject("length shorter than the step count claims", ok, sizeof(ok) - 1);
    memcpy(bad, ok, sizeof(ok)); bad[5] = 0;
    expect_reject("non-STOP with zero steps", bad, sizeof(bad));

    // BACK cannot exist in v2: it needs a mask per step.
    const uint8_t v2_back[] = {CMD_MAGIC, CMD_VERSION_V2, 1, CMD_DIR_BACK, 80, 1, 30, 0};
    expect_reject("BACK in a v2 packet", v2_back, sizeof(v2_back));

    // v2's own step budget is smaller than v3's.
    const uint8_t v2_too_many[] = {CMD_MAGIC, CMD_VERSION_V2, 1, CMD_DIR_LEFT, 80,
                                   PATTERN_MAX_PAIRS_V2 + 1,
                                   20, 5, 20, 5, 20, 5, 20, 5, 20, 0};
    expect_reject("v2 with more pairs than v2 allows", v2_too_many, sizeof(v2_too_many));

    // STOP must be exactly STOP.
    const uint8_t stop_with_intensity[] = {CMD_MAGIC, CMD_VERSION_V3, 1,
                                           CMD_DIR_STOP, 50, 0, 0, 0};
    expect_reject("STOP carrying intensity", stop_with_intensity,
                  sizeof(stop_with_intensity));
    const uint8_t stop_with_steps[] = {CMD_MAGIC, CMD_VERSION_V3, 1, CMD_DIR_STOP,
                                       0, 1, MOTOR_MASK_LEFT, 0, 30, 0};
    expect_reject("STOP carrying a pattern", stop_with_steps, sizeof(stop_with_steps));
}

// ------------------------------------------------------------------ behaviour
static void test_stop(void)
{
    printf("STOP\n");
    const uint8_t v3[] = {CMD_MAGIC, CMD_VERSION_V3, 9, CMD_DIR_STOP, 0, 0, 0, 0};
    const uint8_t v2[] = {CMD_MAGIC, CMD_VERSION_V2, 9, CMD_DIR_STOP, 0, 0};
    cmd_packet_t packet;

    CHECK(cmd_parse(v3, sizeof(v3), &packet) == CMD_PARSE_OK, "v3 STOP must parse");
    CHECK(cmd_is_stop(&packet), "v3 STOP must be reported as a stop");
    CHECK(packet.n_steps == 0, "v3 STOP must carry no steps");

    CHECK(cmd_parse(v2, sizeof(v2), &packet) == CMD_PARSE_OK, "v2 STOP must parse");
    CHECK(cmd_is_stop(&packet), "v2 STOP must be reported as a stop");
}

static void test_v2_mask_expansion(void)
{
    printf("v2 direction -> per-step masks\n");
    const struct { uint8_t direction; uint8_t expected; const char *name; } cases[] = {
        {CMD_DIR_LEFT, MOTOR_MASK_LEFT, "LEFT"},
        {CMD_DIR_FRONT, MOTOR_MASK_BOTH, "FRONT/CENTER"},
        {CMD_DIR_RIGHT, MOTOR_MASK_RIGHT, "RIGHT"},
    };
    for (size_t i = 0; i < sizeof(cases) / sizeof(cases[0]); i++) {
        const uint8_t data[] = {CMD_MAGIC, CMD_VERSION_V2, 3, cases[i].direction,
                                70, 2, 24, 14, 24, 0};
        cmd_packet_t packet;
        CHECK(cmd_parse(data, sizeof(data), &packet) == CMD_PARSE_OK,
              "v2 %s must parse", cases[i].name);
        CHECK(packet.n_steps == 2, "v2 %s must yield 2 steps", cases[i].name);
        for (int s = 0; s < packet.n_steps; s++)
            CHECK(packet.masks[s] == cases[i].expected,
                  "v2 %s step %d mask %u != %u", cases[i].name, s,
                  packet.masks[s], cases[i].expected);
        CHECK(packet.steps[0].on_ms == 240 && packet.steps[0].off_ms == 140,
              "v2 %s timings must scale by 10", cases[i].name);
    }
}

static void test_largest_packet(void)
{
    printf("packet budget\n");
    // A BACK siren is the longest pattern the profile can produce: 6 steps.
    // It must still fit the 20-byte single-write budget, which is what keeps the
    // belt independent of ATT MTU negotiation.
    CHECK(CMD_HEADER_V3 + 2 * PATTERN_MAX_STEPS == 20,
          "v3 worst case is %d bytes, expected 20",
          CMD_HEADER_V3 + 2 * PATTERN_MAX_STEPS);

    uint8_t data[CMD_HEADER_V3 + 2 * PATTERN_MAX_STEPS];
    data[0] = CMD_MAGIC; data[1] = CMD_VERSION_V3; data[2] = 1;
    data[3] = CMD_DIR_BACK; data[4] = 90; data[5] = PATTERN_MAX_STEPS;
    uint32_t bits = 0;
    for (int i = 0; i < PATTERN_MAX_STEPS; i++)
        bits |= (uint32_t)((i % 2) ? MOTOR_MASK_RIGHT : MOTOR_MASK_LEFT) << (2 * i);
    data[6] = (uint8_t)(bits & 0xFF);
    data[7] = (uint8_t)((bits >> 8) & 0xFF);
    for (int i = 0; i < PATTERN_MAX_STEPS; i++) {
        data[CMD_HEADER_V3 + 2 * i] = 13;
        data[CMD_HEADER_V3 + 2 * i + 1] = (i % 2 && i != PATTERN_MAX_STEPS - 1) ? 15 : 0;
    }

    cmd_packet_t packet;
    CHECK(cmd_parse(data, sizeof(data), &packet) == CMD_PARSE_OK,
          "the largest legal v3 packet must parse");
    CHECK(packet.n_steps == PATTERN_MAX_STEPS, "all %d steps must survive",
          PATTERN_MAX_STEPS);
    CHECK(packet.masks[0] == MOTOR_MASK_LEFT && packet.masks[1] == MOTOR_MASK_RIGHT,
          "a sweep must alternate left then right");
}

int main(void)
{
    test_golden();
    test_rejects();
    test_stop();
    test_v2_mask_expansion();
    test_largest_packet();
    printf("\n%d checks, %d failures\n", checks, failures);
    return failures == 0 ? 0 : 1;
}
