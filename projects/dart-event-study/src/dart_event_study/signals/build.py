"""Phase 3 시그널 생성 CLI.

실행:  uv run python -m dart_event_study.signals.build
events_{mode}.parquet → 체결가능일 매핑 + 거래 가능 이벤트 필터 → signals_{mode}.parquet
스키마: (ticker, rcept_no, event_type, rcept_dt, signal_date, direction, strength, surprise_basis)
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
from quantlab_shared.data.calendar import TradingCalendar

from dart_event_study.config import DATA_DIR, load_settings, load_universe
from dart_event_study.signals.timing import execution_date


def build_signals(events: pd.DataFrame, calendar: TradingCalendar) -> tuple[pd.DataFrame, dict]:
    """이벤트 → 시그널. (시그널 df, 제외 사유 카운트) 반환."""
    dropped = {"direction_없음": 0, "direction_중립": 0, "기간말_체결불가": 0}
    rows = []
    for _, ev in events.iterrows():
        if pd.isna(ev["direction"]):
            dropped["direction_없음"] += 1
            continue
        if ev["direction"] == 0:
            dropped["direction_중립"] += 1
            continue
        rcept_date = dt.datetime.strptime(ev["rcept_dt"], "%Y%m%d").date()
        try:
            sig_date = execution_date(rcept_date, calendar)  # 시각 미상 → 익영업일 (보수적)
        except ValueError:
            dropped["기간말_체결불가"] += 1
            continue
        rows.append(
            {
                "ticker": ev["ticker"],
                "rcept_no": ev["rcept_no"],
                "event_type": ev["event_type"],
                "rcept_dt": ev["rcept_dt"],
                "signal_date": sig_date,
                "direction": int(ev["direction"]),
                "strength": ev["strength"],
                "surprise_basis": ev.get("surprise_basis"),
            }
        )
    df = pd.DataFrame(rows).sort_values(["signal_date", "ticker"]).reset_index(drop=True)
    return df, dropped


def main() -> None:
    settings, universe = load_settings(), load_universe()
    mode = universe["mode"]
    start, end = settings["period"]["start"], settings["period"]["end"]

    events = pd.read_parquet(DATA_DIR / f"events_{mode}.parquet")
    calendar = TradingCalendar.from_krx(start, end, cache_dir=DATA_DIR / "prices")

    signals, dropped = build_signals(events, calendar)
    out = DATA_DIR / f"signals_{mode}.parquet"
    signals.to_parquet(out)

    print(f"시그널 저장: {out} ({len(signals)}건 / 이벤트 {len(events)}건)")
    print(f"제외: {dropped}")
    print("\n이벤트타입 × 방향:")
    print(signals.groupby(["event_type", "direction"]).size().to_string())
    lag = (pd.to_datetime(signals["signal_date"]) - pd.to_datetime(signals["rcept_dt"])).dt.days
    print(f"\n접수→체결 지연(달력일): 평균 {lag.mean():.2f}일, 최대 {lag.max()}일")


if __name__ == "__main__":
    main()
