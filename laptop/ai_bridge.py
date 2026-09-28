from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Sequence


@dataclass(frozen=True)
class AIResult:
    intensity: int
    sound_class: str
    pattern: Sequence[Sequence[int]]


def run_live_ai(audio, sample_rate: int, direction_info) -> Optional[AIResult]:
    """BLE로 받은 한 이벤트의 오디오 → 진동 명령. 알릴 필요 없으면 None.
    """
    try:
        from classifier.adapter import SR, predict_array
        from decision.judge import judge
    except ImportError as e:
        raise ImportError(
            "meit-ai를 찾을 수 없습니다. meit-ai를 PYTHONPATH에 추가하거나 "
            "설치한 뒤 다시 실행하세요."
        ) from e

    if sample_rate != SR:
        raise ValueError(f"sample_rate must be {SR}, got {sample_rate}")

    direction = getattr(direction_info, "direction", -1)

    probs, db = predict_array(audio)
    cmd = judge(probs, direction, db)
    if cmd is None:
        return None

    return AIResult(
        intensity=cmd["intensity"],
        sound_class=cmd["sound_class"],
        pattern=cmd["pattern"],
    )


def run_mock_ai(audio, sample_rate: int, direction_info) -> AIResult:
    # Integration-test only. This is NOT a sound classifier.
    return AIResult(
        intensity=60,
        sound_class="siren",
        pattern=[[100, 50], [100, 0]],
    )

def warmup() -> None:
    """모델을 미리 로드해둔다.

    predict_array()의 첫 호출에서 TensorFlow SavedModel을 디스크에서
    읽어오느라 30초 안팎이 걸린다(실측 28~37초). BLE 연결 직후 미리
    한 번 돌려두면 실제 이벤트는 처음부터 30ms 안에 처리된다.
    """
    import numpy as np

    from classifier.adapter import CLIP_SEC, SR

    run_live_ai(np.zeros(int(SR * CLIP_SEC), dtype=np.float32), SR, None)