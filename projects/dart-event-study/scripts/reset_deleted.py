"""누적 테이블의 is_deleted를 전부 False로 리셋 (일회성 오탐 정정).

배경: 초기 store.py가 'prev-cur 전체'를 삭제로 찍어, 크롤러가 스크롤로 닿지 않은 과거 글을
전부 오탐했다(첫 크롤1→2 전환에서 6,993건). 토스 post_id가 전역 시간순이 아니라 관측 최소 id를
경계로 쓰는 방식도 성긴 크롤(22h 공백)에선 대량 오탐이 난다(실측 확인).

→ 방침(2026-07-16): **삭제 탐지를 보류**하고 is_deleted를 False로 리셋한다. 앞으로 store.py는
hit_stop(윈도우가 이전 피드까지 닿음)일 때만 삭제 판정하며, 순방향 dense 크롤에선 정확하다.
정확한 크로스-크롤 삭제 탐지기는 순방향 깨끗한 데이터가 쌓인 뒤 별도 태스크로 만든다.

**is_deleted와 deleted_detected_at 두 컬럼만 덮어쓴다.** first_seen_at 등 나머지는 불변.

실행(projects/dart-event-study 에서):
    uv run python scripts/reset_deleted.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from dart_event_study.config import DATA_DIR  # noqa: E402

RAW_DIR = DATA_DIR / "raw" / "toss"


def main() -> None:
    if not RAW_DIR.exists():
        print(f"토스 raw 디렉터리 없음: {RAW_DIR}")
        return
    total_before = 0
    print(f"{'ticker':<8} {'before':>7} {'after':>7}")
    for tdir in sorted(d for d in RAW_DIR.iterdir() if d.is_dir()):
        cum_path = tdir / "_cumulative.parquet"
        if not cum_path.exists():
            continue
        cum = pd.read_parquet(cum_path)
        if len(cum) == 0:
            print(f"{tdir.name:<8} {'0':>7} {'0':>7}  (빈 종목)")
            continue
        before = int(cum["is_deleted"].fillna(False).astype(bool).sum())
        # 두 컬럼만 리셋 — 나머지 컬럼은 그대로.
        cum["is_deleted"] = False
        cum["deleted_detected_at"] = None
        cum.to_parquet(cum_path)
        total_before += before
        print(f"{tdir.name:<8} {before:>7} {0:>7}")

    print("-" * 24)
    print(f"{'합계':<8} {total_before:>7} {0:>7}")
    print(f"\nis_deleted=True: {total_before} → 0 (오탐 {total_before}건 제거)")


if __name__ == "__main__":
    main()
