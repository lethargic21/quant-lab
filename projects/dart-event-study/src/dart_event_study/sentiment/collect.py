"""감성 레이어 수집 CLI — 자사주 이벤트(직접+신탁)의 뉴스 반응.

실행:  uv run python -m dart_event_study.sentiment.collect
산출:  data/news_buyback.parquet (rcept_no, ticker, event_type, n_articles, sent_score, ...)
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import requests

from dart_event_study.config import DATA_DIR, get_api_key, load_universe
from dart_event_study.dart.client import DartClient
from dart_event_study.sentiment.lexicon import score_titles
from dart_event_study.sentiment.news import fetch_event_news


def main() -> None:
    mode = load_universe()["mode"]
    events = pd.read_parquet(DATA_DIR / f"events_{mode}.parquet")
    bb = events[events["event_type"].isin(["buyback", "buyback_trust"])]

    cmap = DartClient(get_api_key(), cache_dir=DATA_DIR / "dart").corp_code_map()
    names = {t: v["corp_name"] for t, v in cmap.items()}

    session = requests.Session()
    rows = []
    for i, (_, ev) in enumerate(bb.iterrows(), 1):
        name = names.get(ev["ticker"])
        if not name:
            continue
        d = dt.date(int(ev["rcept_dt"][:4]), int(ev["rcept_dt"][4:6]), int(ev["rcept_dt"][6:]))
        try:
            news = fetch_event_news(name, d, DATA_DIR / "news", session=session)
            metrics = {"n_articles": news["n_articles"], **score_titles(news["titles"])}
        except Exception as e:  # 재시도 후에도 차단 — 결측 기록하고 계속 (재실행 시 캐시 이어받기)
            print(f"  [skip] {name} {d}: {type(e).__name__}")
            metrics = {"n_articles": None, "sent_score": None, "n_pos": None, "n_neg": None}
        rows.append(
            {
                "rcept_no": ev["rcept_no"],
                "ticker": ev["ticker"],
                "event_type": ev["event_type"],
                "rcept_dt": ev["rcept_dt"],
                **metrics,
            }
        )
        if i % 25 == 0:
            print(f"  {i}/{len(bb)} ({name})")

    df = pd.DataFrame(rows)
    out = DATA_DIR / "news_buyback.parquet"
    df.to_parquet(out)
    print(f"\n저장: {out} ({len(df)}건)")
    print("기사 수 분포:")
    print(df["n_articles"].describe().to_string())
    print(f"기사 0건 이벤트: {(df['n_articles'] == 0).sum()}건")
    print("감성 점수 분포 (기사 있는 이벤트):")
    print(df["sent_score"].dropna().describe().to_string())


if __name__ == "__main__":
    main()
