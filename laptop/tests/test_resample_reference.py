TDOA_FS_HZ = 48000
AI_FS_HZ = 16000
DECIM = TDOA_FS_HZ // AI_FS_HZ
FRAME_LEN = 1024
CLIP_FRAMES = 24

def streaming_output_count(n_in, phase):
    out = 0
    for _ in range(n_in):
        if phase == 0:
            out += 1
        phase = (phase + 1) % DECIM
    return out, phase

def test_current_clip_output_is_8192():
    phase = 0
    total = 0
    per_frame = []
    for _ in range(CLIP_FRAMES):
        n, phase = streaming_output_count(FRAME_LEN, phase)
        total += n
        per_frame.append(n)
    assert total == 8192
    assert total == (FRAME_LEN * CLIP_FRAMES) // DECIM
    assert 341 in per_frame and 342 in per_frame
