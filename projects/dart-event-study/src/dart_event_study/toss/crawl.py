"""토스 커뮤니티 1회 크롤 CLI (스케줄러가 이걸 호출).

실행:  uv run python -m dart_event_study.toss.crawl
- 유니버스 전 종목을 최신순으로 크롤 → 스냅샷 + 누적 갱신
- 직전 크롤에서 본 post_id에 닿으면 조기 종료 (불필요한 과거 스크롤 방지)
- 종목 간 2~3초 간격. 실패(최신순 검증·미지원 경고·차단)는 종목별로 기록하고 계속.
- 종료 코드: 하나라도 실패면 1 (스케줄러가 알림용으로 사용)
"""

from __future__ import annotations

import datetime as dt
import sys
import time
from pathlib import Path

import pandas as pd
import yaml

from dart_event_study.config import CONFIG_DIR, DATA_DIR
from dart_event_study.toss.board import crawl_community
from dart_event_study.toss.store import save_snapshot, update_cumulative

RAW_DIR = DATA_DIR / "raw" / "toss"
SALT_PATH = RAW_DIR / ".salt"  # gitignore — 작성자 해시용, 커밋 안 함


def load_universe() -> dict[str, str]:
    cfg = yaml.safe_load((CONFIG_DIR / "toss_universe.yaml").read_text(encoding="utf-8"))
    return {**cfg["compare"], **cfg["expand"]}


def get_salt() -> str:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    if not SALT_PATH.exists():
        import secrets
        SALT_PATH.write_text(secrets.token_hex(16), encoding="utf-8")
    return SALT_PATH.read_text(encoding="utf-8").strip()


def prev_ids(code: str) -> set[str]:
    """직전 크롤까지 살아있던 post_id (조기 종료용)."""
    p = RAW_DIR / code / "_cumulative.parquet"
    if not p.exists():
        return set()
    cum = pd.read_parquet(p)
    return set(cum.loc[~cum["is_deleted"].fillna(False), "post_id"])


def main() -> None:
    universe = load_universe()
    salt = get_salt()
    crawl_ts = dt.datetime.now()  # 시스템 로컬(KST 가정) — 장경계 판별 기준
    print(f"=== 토스 크롤 {crawl_ts:%Y-%m-%d %H:%M:%S} | {len(universe)}종목 ===")

    failures = []
    for i, (code, name) in enumerate(universe.items()):
        try:
            posts = crawl_community(code, salt=salt, stop_ids=prev_ids(code))
            save_snapshot(RAW_DIR, code, posts, crawl_ts)
            summ = update_cumulative(RAW_DIR, code, posts, crawl_ts)
            print(f"  {code} {name}: 관측 {summ['observed']}, 신규 {summ['new']}, "
                  f"삭제 {summ['deleted_this_crawl']}, 누적 {summ['cumulative_total']}")
        except Exception as e:  # noqa: BLE001 — 한 종목 실패가 전체를 막지 않게
            failures.append((code, name, str(e)[:120]))
            print(f"  {code} {name}: 실패 — {str(e)[:120]}")
        if i < len(universe) - 1:
            time.sleep(2.5)

    if failures:
        print(f"\n실패 {len(failures)}종목:")
        for code, name, err in failures:
            print(f"  - {code} {name}: {err}")
        sys.exit(1)  # 스케줄러 알림 트리거
    print("\n전 종목 성공.")


if __name__ == "__main__":
    main()
