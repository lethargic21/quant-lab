"""게시판 수집 CLI (2단계 연구 — 사용자 승인 2026-07-13).

실행:  uv run python -m dart_event_study.board.collect
대상: 팍스넷 밀도 프로빙 상위 5종목 × 최근 365일 목록 페이지.
산출: data/raw/paxnet/{code}/page_*.json (캐시) + data/raw/paxnet/{code}_posts.parquet
작성자 정보 미수집. data/는 gitignore — 원문은 커밋되지 않는다.
"""

from __future__ import annotations

import pandas as pd

from dart_event_study.board.paxnet import PaxnetBoard
from dart_event_study.config import DATA_DIR

# 2026-07-13 밀도 프로빙(후보 10종목 1페이지씩) 상위 5 — docs/spam_filter_report.md 참조
TICKERS = {
    "005930": "삼성전자",     # 12.3글/일
    "000660": "SK하이닉스",   # 9.0글/일
    "005380": "현대차",       # 0.9글/일
    "068270": "셀트리온",     # 0.8글/일
    "042660": "한화오션",     # 0.5글/일
}
DAYS = 365


def main() -> None:
    raw_dir = DATA_DIR / "raw" / "paxnet"
    board = PaxnetBoard(cache_dir=raw_dir)
    for code, name in TICKERS.items():
        rows = board.collect(code, days=DAYS)
        df = pd.DataFrame(rows)
        if not df.empty:
            df.insert(0, "ticker", code)
            df = df.drop_duplicates("seq").sort_values("posted_at").reset_index(drop=True)
        out = raw_dir / f"{code}_posts.parquet"
        df.to_parquet(out)
        span = f"{df['posted_at'].min():%Y-%m-%d} ~ {df['posted_at'].max():%Y-%m-%d}" if len(df) else "-"
        print(f"{code} {name}: {len(df)}글 저장 ({span})")


if __name__ == "__main__":
    main()
