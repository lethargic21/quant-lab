"""일별 파생지표 집계 (장중/장후 분리).

실행:  uv run python -m dart_event_study.toss.aggregate

first_seen_at(우리가 찍은 크롤 시각)을 크롤 슬롯에 매핑해 장중/장후를 나눈다.
크롤 스케줄 09:00 / 12:00 / 15:30 / 21:00 기준, 각 슬롯이 커버하는 관측 창:
  09:00 슬롯 → (전일, 장후)  [전일 21:00 → 09:00 야간]
  12:00 슬롯 → (당일, 장중)  [09:00 → 12:00]
  15:30 슬롯 → (당일, 장중)  [12:00 → 15:30]
  21:00 슬롯 → (당일, 장후)  [15:30 → 21:00]
비정규 시각(수동/최초 크롤)도 가장 가까운 슬롯으로 분류. session_date+session 부여.

출력: data/processed/toss_daily.parquet
  ticker, date, posts_intraday, posts_afterhours, posts_total,
  deleted_count, avg_likes, avg_comments
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

from dart_event_study.config import DATA_DIR

RAW_DIR = DATA_DIR / "raw" / "toss"


def classify(first_seen: dt.datetime) -> tuple[dt.date, str]:
    """first_seen → (session_date, 'intraday'|'afterhours'). 슬롯 기반."""
    h = first_seen.hour + first_seen.minute / 60
    if 10.5 <= h < 13.5:      # 12:00 슬롯
        return first_seen.date(), "intraday"
    if 13.5 <= h < 18.0:      # 15:30 슬롯
        return first_seen.date(), "intraday"
    if 18.0 <= h < 24.0:      # 21:00 슬롯
        return first_seen.date(), "afterhours"
    # h < 10.5 → 09:00 슬롯 (전일 밤 → 오늘 아침): 전일 장후로 귀속
    return first_seen.date() - dt.timedelta(days=1), "afterhours"


def main() -> None:
    rows = []
    for cum_path in RAW_DIR.glob("*/_cumulative.parquet"):
        cum = pd.read_parquet(cum_path)
        if cum.empty:
            continue
        cum["first_seen_dt"] = pd.to_datetime(cum["first_seen_at"])
        cls = cum["first_seen_dt"].apply(lambda t: classify(t.to_pydatetime()))
        cum["session_date"] = [c[0] for c in cls]
        cum["session"] = [c[1] for c in cls]

        for (ticker, date), g in cum.groupby(["ticker", "session_date"]):
            rows.append({
                "ticker": ticker,
                "date": date,
                "posts_intraday": int((g["session"] == "intraday").sum()),
                "posts_afterhours": int((g["session"] == "afterhours").sum()),
                "posts_total": len(g),
                "deleted_count": int(g["is_deleted"].fillna(False).sum()),
                "avg_likes": round(float(g["likes"].mean()), 2),
                "avg_comments": round(float(g["comments"].mean()), 2),
            })

    out = pd.DataFrame(rows).sort_values(["ticker", "date"]).reset_index(drop=True)
    proc = DATA_DIR / "processed"
    proc.mkdir(parents=True, exist_ok=True)
    path = proc / "toss_daily.parquet"
    out.to_parquet(path)
    print(f"저장: {path} ({len(out)}행)")
    if len(out):
        print(out.to_string(index=False))


if __name__ == "__main__":
    main()
