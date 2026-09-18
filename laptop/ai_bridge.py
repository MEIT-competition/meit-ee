from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Sequence

@dataclass(frozen=True)
class AIResult:
    intensity: int
    sound_class: str
    pattern: Sequence[Sequence[int]]

def run_live_ai(audio, sample_rate: int, direction_info) -> Optional[AIResult]:
    raise NotImplementedError(
        "Live AI bridge not wired yet. Resolve classify_clip() vs judge() first."
    )

def run_mock_ai(audio, sample_rate: int, direction_info) -> AIResult:
    # Integration-test only. This is NOT a sound classifier.
    return AIResult(
        intensity=60,
        sound_class="siren",
        pattern=[[100, 50], [100, 0]],
    )
