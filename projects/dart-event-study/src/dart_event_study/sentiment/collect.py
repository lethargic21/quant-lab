"""감성 레이어 수집 CLI — 이벤트별 뉴스 반응 (자사주 + 유상증자).

실행:  uv run python -m dart_event_study.sentiment.collect
산출:  data/news_buyback.parquet, data/news_rights.parquet
       (rcept_no, ticker, event_type, rcept_dt, n_articles, sent_score, n_pos, n_neg)

이미 산출 파일이 있는 그룹은 건너뛴다 (재실행 시 재크롤 없음 — 이어받기).
검색어는 이벤트 성격에 맞춘다: 자사주="자사주", 유증="유상증자".

**earnings(실적)는 의도적으로 제외**: 5,300건 × 스로틀 ~6.6초 ≈ 10시간이고 장시간
소프트-403이 하드 밴으로 번질 위험. 필요하면 별도 장시간 잡으로 돌린다 (아래 GROUPS에
주석으로 남김). 유증 139건은 ~20~40분으로 현실적.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import requests

from dart_event_study.config import DATA_DIR, get_api_key, load_universe
from dart_event_study.dart.client import DartClient
from dart_event_study.sentiment.lexicon import score_titles
from dart_event_study.sentiment.news import fetch_event_news

# (출력파일 stem, 대상 event_type들, 검색어)
GROUPS: list[tuple[str, list[str], str]] = [
    ("news_buyback", ["buyback", "buyback_trust"], "자사주"),
    ("news_rights", ["rights_offering"], "유상증자"),
    # ("news_earnings", ["earnings"], "실적"),  # 5,300건 ~10h — 밴 위험, 별도 잡으로만
]


def collect_group(
    events: pd.DataFrame, event_types: list[str], query_term: str, names: dict[str, str],
    session: requests.Session, out_path,
) -> pd.DataFrame:
    sub = events[events["event_type"].isin(event_types)]
    rows = []
    for i, (_, ev) in enumerate(sub.iterrows(), 1):
        name = names.get(ev["ticker"])
        if not name:
            continue
        d = dt.date(int(ev["rcept_dt"][:4]), int(ev["rcept_dt"][4:6]), int(ev["rcept_dt"][6:]))
        try:
            news = fetch_event_news(name, d, DATA_DIR / "news", session=session, query_term=query_term)
            metrics = {"n_articles": news["n_articles"], **score_titles(news["titles"])}
        except Exception as e:  # 재시도 후에도 차단 — 결측 기록하고 계속 (재실행 시 캐시 이어받기)
            print(f"  [skip] {name} {d}: {type(e).__name__}")
            metrics = {"n_articles": None, "sent_score": None, "n_pos": None, "n_neg": None}
        rows.append({
            "rcept_no": ev["rcept_no"], "ticker": ev["ticker"],
            "event_type": ev["event_type"], "rcept_dt": ev["rcept_dt"], **metrics,
        })
        if i % 25 == 0:
            print(f"  {i}/{len(sub)} ({name})")

    df = pd.DataFrame(rows)
    df.to_parquet(out_path)
    n_art = df["n_articles"].dropna()
    print(f"저장: {out_path} ({len(df)}건, 기사 중앙값 {n_art.median():.0f}, "
          f"0건 {(df['n_articles'] == 0).sum()}건)\n")
    return df


def main() -> None:
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    mode = load_universe()["mode"]
    events = pd.read_parquet(DATA_DIR / f"events_{mode}.parquet")
    cmap = DartClient(get_api_key(), cache_dir=DATA_DIR / "dart").corp_code_map()
    names = {t: v["corp_name"] for t, v in cmap.items()}
    session = requests.Session()

    for stem, types, term in GROUPS:
        out = DATA_DIR / f"{stem}.parquet"
        if out.exists():
            print(f"건너뜀: {stem} (이미 있음 — 재크롤 없음)")
            continue
        n = int(events["event_type"].isin(types).sum())
        print(f"수집 시작: {stem} — '{term}' 검색, 대상 {n}건")
        collect_group(events, types, term, names, session, out)


if __name__ == "__main__":
    main()
