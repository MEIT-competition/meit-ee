"""One command that verifies the whole back half of the system on the real belt.

    python -m laptop.verify_pipeline --meit-ai C:\\src\\meit-ai

It runs the real ``meit-ai`` model, maps each verdict to a haptic pattern, sends it
over BLE, and tells you — before each one — exactly what you should feel. The cases
that ``meit-ai`` rejects are included on purpose: a belt that stays silent for a
quiet clip is as much a pass as one that buzzes for a loud hazard.

Test audio is synthesised in-process, so nothing has to be downloaded. Point
``--sounds`` at a folder of real recordings to check the model's real-world
accuracy instead; the synthetic clips only prove the pipeline runs.

The direction stage covers ``left``, ``center`` and ``right`` — every direction
iOS stereo reports, and every sensation the belt can produce.

What a full pass demonstrates:

* the SavedModel loads and classifies on this machine,
* ``meit-ai``'s own gates (confidence threshold, dBFS gate, ``normal``) decide
  whether the belt fires at all,
* class becomes pulse count, loudness becomes strength,
* each direction reaches the intended motor(s), and
* the BLE write lands and the motors actually move.

Only the model's accuracy on real sounds is out of scope — that is
``meit-ai``'s own ``eval/`` and ``model/TRAINING_REPORT.md``.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import math
import random
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from laptop.ai_runner import (
    CLIP_SAMPLES,
    EXPECTED_SR,
    AIError,
    AIResult,
    build_runner,
    float_to_pcm16,
    load_wav_clip,
)
from laptop.belt_client import BeltLink, add_belt_arguments, belt_from_arguments
from laptop.haptic import HapticProfile, build_command, with_intensity_scale
from laptop.protocol import MotorCommand, direction_name, normalize_direction

LOGGER = logging.getLogger("meit.verify")

# Feel-descriptions for the wearer, in the project's language.
MOTOR_WORDS = {"LEFT": "왼쪽 모터", "CENTER": "양쪽 모터 동시", "RIGHT": "오른쪽 모터"}


@dataclass
class Clip:
    """One piece of test audio plus why it is in the set."""

    name: str
    pcm: bytes
    intent: str  # what this case is meant to demonstrate


def _synth(fn) -> bytes:
    return float_to_pcm16([fn(i / EXPECTED_SR) for i in range(CLIP_SAMPLES)])


def builtin_clips() -> List[Clip]:
    """Deterministic synthetic clips covering both outcomes.

    These are tones and noise, not recordings, so which danger class the model
    picks is not the point — that it classifies, gates and drives the belt is.
    """
    rng = random.Random(0)

    def sweep(t: float) -> float:
        # A tonal sound whose pitch rises and falls twice a second.
        return 0.45 * math.sin(2 * math.pi * (700 + 400 * math.sin(2 * math.pi * 2 * t)) * t)

    return [
        Clip("큰 소리 (음높이 변하는 톤)", _synth(sweep),
             "위험 판정 → 진동해야 함"),
        Clip("같은 소리, 아주 작게", _synth(lambda t: sweep(t) / 300.0),
             "meit-ai dB 게이트(-50dB) → 진동 없어야 함"),
        Clip("큰 소리 (광대역 노이즈)", _synth(lambda t: 0.30 * (rng.random() * 2 - 1)),
             "위험 판정 → 진동해야 함"),
        Clip("완전 무음", bytes(CLIP_SAMPLES * 2),
             "확신도 미달 → 진동 없어야 함"),
    ]


def clips_from_folder(folder: Path) -> List[Clip]:
    files = sorted(p for p in folder.iterdir() if p.suffix.lower() in {".wav", ".flac"})
    if not files:
        raise SystemExit(f"{folder} 안에 wav 파일이 없습니다")
    return [Clip(p.name, load_wav_clip(p), "실제 녹음") for p in files]


def describe_expectation(command: Optional[MotorCommand], result: AIResult) -> str:
    """The sentence the tester reads before the belt moves."""
    if command is None:
        return "  ▶ 느껴야 할 것: **진동 없음** (meit-ai가 알릴 필요 없다고 판정)"
    where = MOTOR_WORDS[direction_name(command.direction)]
    pulses = len(command.steps)
    return (f"  ▶ 느껴야 할 것: {where} · **{pulses}번** · 세기 {command.intensity}% "
            f"· 총 {command.duration_ms}ms")


@dataclass
class Case:
    clip_name: str
    intent: str
    result: AIResult
    command: Optional[MotorCommand]
    sent: bool = False


async def run_cases(link: BeltLink, cases: List[Case], gap_ms: int,
                    pause: bool) -> None:
    """Play each case in order, announcing it first so it can be felt."""
    for index, case in enumerate(cases, start=1):
        print()
        print(f"[{index}/{len(cases)}] {case.clip_name}")
        print(f"  목적: {case.intent}")
        print(f"  meit-ai: {case.result.label}  확신도 {case.result.confidence:.3f}  "
              f"음량 {case.result.dbfs:.1f}dBFS  위험={case.result.danger}")
        print(describe_expectation(case.command, case.result))

        if case.command is None:
            # Nothing is sent at all. Pause anyway so the tester can confirm the
            # belt really stayed still rather than assuming it did.
            await asyncio.sleep(min(1.2, gap_ms / 1000.0))
            continue

        if pause:
            await asyncio.to_thread(input, "  (Enter를 누르면 진동을 보냅니다) ")
        await link.send(case.command)
        case.sent = True
        await asyncio.sleep(case.command.duration_ms / 1000.0)
        if index < len(cases):
            await asyncio.sleep(gap_ms / 1000.0)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="실제 meit-ai 추론부터 벨트 진동까지 한 번에 검증")
    parser.add_argument("--meit-ai", default=None,
                        help="meit-ai 체크아웃 경로 (기본값: $MEIT_AI_PATH)")
    parser.add_argument("--mock-label", default=None,
                        choices=("horn", "siren", "crash", "normal"),
                        help="모델 없이 배선만 확인할 때. 실제 검증에는 쓰지 마세요")
    parser.add_argument("--sounds", default=None,
                        help="실제 녹음 wav 폴더 (생략하면 합성 클립 사용)")
    parser.add_argument("--direction", default="center",
                        choices=("left", "center", "right"),
                        help="분류 단계에서 쓸 방향 (기본 center)")
    parser.add_argument("--skip-direction-check", action="store_true",
                        help="방향별 모터 확인 단계를 건너뜀")
    parser.add_argument("--gap-ms", type=int, default=1200,
                        help="각 진동 사이 간격")
    parser.add_argument("--pause", action="store_true",
                        help="진동마다 Enter를 기다림 (하나씩 확인할 때)")
    parser.add_argument("--profile", default=None, help="햅틱 프로파일 JSON")
    parser.add_argument("--intensity-scale", type=float, default=1.0)
    parser.add_argument("--dry-run", action="store_true",
                        help="벨트 없이 판정·패턴만 출력")
    add_belt_arguments(parser)
    return parser


def main(argv: Optional[List[str]] = None) -> None:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.WARNING,
                        format="%(levelname)s %(name)s %(message)s")
    profile: HapticProfile = HapticProfile.load(args.profile)

    try:
        runner = build_runner(args.meit_ai, mock_label=args.mock_label)
    except AIError as exc:
        raise SystemExit(f"\n[중단] {exc}\n")

    clips = clips_from_folder(Path(args.sounds).expanduser()) if args.sounds \
        else builtin_clips()

    print("=" * 74)
    print(" MEIT 뒷단 검증 — meit-ai 추론 → 햅틱 매핑 → BLE → 모터")
    print("=" * 74)
    print(f" 추론기        : {type(runner).__name__}"
          f"{'  ⚠ 모형(mock)이라 실제 검증이 아닙니다' if args.mock_label else ''}")
    print(f" 테스트 오디오 : {'실제 녹음 ' + args.sounds if args.sounds else '합성 클립 (내장)'}")
    print(" 벨트를 손에 쥐고, 각 항목의 '느껴야 할 것'과 실제 느낌을 비교하세요.")

    # --- phase 1: does classification become the right sensation? -------------
    base_direction = normalize_direction(args.direction)
    cases: List[Case] = []
    for clip in clips:
        result = runner.infer(clip.pcm)
        command = (build_command(result.label, result.confidence, base_direction,
                                 profile, dbfs=result.dbfs)
                   if result.danger else None)
        if command is not None and args.intensity_scale != 1.0:
            command = with_intensity_scale(command, args.intensity_scale)
        cases.append(Case(clip.name, clip.intent, result, command))

    # --- phase 2: does direction reach the right motors? ----------------------
    # Reuse the loudest clip meit-ai judged dangerous, so this stage tests only
    # direction and nothing else varies between the three.
    if not args.skip_direction_check:
        loud = max((c for c in cases if c.command is not None),
                   key=lambda c: c.result.dbfs, default=None)
        if loud is None:
            print("\n ⚠ 위험 판정된 클립이 없어 방향 확인 단계를 건너뜁니다.")
        else:
            for name in ("left", "center", "right"):
                direction = normalize_direction(name)
                command = build_command(loud.result.label, loud.result.confidence,
                                        direction, profile, dbfs=loud.result.dbfs)
                if command is not None and args.intensity_scale != 1.0:
                    command = with_intensity_scale(command, args.intensity_scale)
                cases.append(Case(f"방향 확인: {name}",
                                  f"{MOTOR_WORDS[direction_name(direction)]}에서만 느껴야 함",
                                  loud.result, command))

    runner.close()

    if args.dry_run:
        for index, case in enumerate(cases, start=1):
            print()
            print(f"[{index}/{len(cases)}] {case.clip_name}")
            print(f"  meit-ai: {case.result.label} {case.result.confidence:.3f} "
                  f"{case.result.dbfs:.1f}dBFS 위험={case.result.danger}")
            print(describe_expectation(case.command, case.result))
        print("\n(--dry-run: 벨트로 아무것도 보내지 않았습니다)")
        return

    belt = belt_from_arguments(args)

    async def session(link: BeltLink) -> None:
        await run_cases(link, cases, args.gap_ms, args.pause)

    try:
        asyncio.run(belt.run_forever(session, max_attempts=1))
    except KeyboardInterrupt:
        print("\n중단됨")
        return

    vibrated = sum(1 for c in cases if c.sent)
    silent = sum(1 for c in cases if c.command is None)
    print()
    print("=" * 74)
    if vibrated == 0:
        print(" 벨트에 아무것도 전송되지 않았습니다.")
        print(" 벨트 전원과 블루투스를 확인하고 다시 실행하세요.")
        print(" (벨트 없이 판정만 보려면 --dry-run)")
        sys.exit(1)
    print(f" 전송한 진동 {vibrated}개, 의도적으로 침묵한 경우 {silent}개.")
    print(" 아래가 모두 맞았다면 뒷단 전 구간이 정상입니다:")
    print("   · 위험한 소리마다 진동이 왔고, 횟수가 안내와 일치했다")
    print("   · 조용한 소리·무음에서는 아무 진동도 오지 않았다")
    print("   · 방향 확인 단계에서 안내한 모터에서만 느껴졌다")
    print(" 하나라도 다르면 그 항목 번호를 알려주세요.")
    print("=" * 74)


if __name__ == "__main__":
    main()
