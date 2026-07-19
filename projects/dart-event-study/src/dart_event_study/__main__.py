"""파이프라인 단계별 통합 CLI (Phase C-2).

    uv run --project projects/dart-event-study python -m dart_event_study <단계> [옵션]

예:
    python -m dart_event_study event-study --mode debug
    python -m dart_event_study caar-tests --config-dir path/to/config
    python -m dart_event_study --list

**기존 진입점은 그대로 둔다.** `python -m dart_event_study.analysis.event_study` 같은
모듈 직접 실행은 계속 동작한다 (토스 스케줄러가 `python -m dart_event_study.toss.crawl`을
직접 부르고 있어 깨뜨리면 안 됨). 이 CLI는 그 위에 얹는 **추가** 계층이다.

전역 옵션:
  --mode {debug,full}   universe.yaml을 고치지 않고 유니버스 모드 전환
  --config-dir DIR      settings.yaml/universe.yaml을 다른 디렉터리에서 로드
"""

from __future__ import annotations

import argparse
import importlib

# 단계 이름 → (모듈 경로, 설명). 실행 순서대로.
COMMANDS: dict[str, tuple[str, str]] = {
    # 수집 → 추출 → 시그널
    "collect": ("dart_event_study.collect", "OpenDART 공시 수집 + 캐싱"),
    "extract-events": ("dart_event_study.events.extract", "이벤트 3종 감지·구조화 추출"),
    "build-signals": ("dart_event_study.signals.build", "이벤트 → 방향/강도 시그널 + 체결시점 매핑"),
    # 분석
    "event-study": ("dart_event_study.analysis.event_study", "시장모형 AAR/CAR + 클러스터 t + FDR"),
    "caar-tests": ("dart_event_study.analysis.caar_tests", "CAAR 검정 배터리 (BMP·Corrado·부호·placebo)"),
    "backtest": ("dart_event_study.analysis.backtest", "시그널 포트폴리오 백테스트 vs KOSPI"),
    "significance": ("dart_event_study.analysis.significance", "백테스트 Sharpe 부트스트랩 + Deflated Sharpe"),
    # 어텐션(감성) 레이어
    "news-collect": ("dart_event_study.sentiment.collect", "이벤트별 뉴스 수집 (네이버, 캐시)"),
    "attention": ("dart_event_study.analysis.attention", "어텐션 삼분위별 드리프트 비교"),
    "interaction": ("dart_event_study.analysis.interaction", "이벤트 × 어텐션 상호작용 회귀"),
    # 종목토론방
    "board-collect": ("dart_event_study.board.collect", "팍스넷 게시판 수집"),
    "board-baseline": ("dart_event_study.board.baseline", "스팸 규칙 베이스라인 리포트"),
    "toss-crawl": ("dart_event_study.toss.crawl", "토스 커뮤니티 1회 크롤 (first-seen)"),
    "toss-aggregate": ("dart_event_study.toss.aggregate", "토스 일별 파생지표 집계"),
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m dart_event_study",
        description="DART 이벤트 스터디 파이프라인 — 단계별 실행",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="단계 목록은 --list, 각 단계의 상세는 모듈 docstring 참조.",
    )
    parser.add_argument("--list", action="store_true", help="실행 가능한 단계 목록 출력")
    parser.add_argument(
        "--mode", choices=["debug", "full"],
        help="유니버스 모드 오버라이드 (universe.yaml 수정 없이 전환)",
    )
    parser.add_argument("--config-dir", help="설정 디렉터리 오버라이드 (기본: config/)")
    parser.add_argument(
        "command", nargs="?", choices=list(COMMANDS),
        help="실행할 파이프라인 단계",
    )
    return parser


def print_commands() -> None:
    width = max(len(c) for c in COMMANDS)
    print("파이프라인 단계 (실행 순서):")
    for name, (_, desc) in COMMANDS.items():
        print(f"  {name:<{width}}  {desc}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.list:
        print_commands()
        return 0
    if not args.command:
        parser.print_help()
        return 1

    # 전역 오버라이드는 단계 모듈을 import/실행하기 **전에** 적용해야 한다
    # (모듈들이 main() 안에서 load_settings/load_universe를 호출).
    from dart_event_study.config import apply_overrides

    apply_overrides(config_dir=args.config_dir, mode=args.mode)

    module_path, _ = COMMANDS[args.command]
    module = importlib.import_module(module_path)  # 지연 import — 무거운 의존성 회피
    module.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
