"""Phase 1 데이터 수집 CLI.

실행:  uv run python -m dart_event_study.collect
- 유니버스 티커의 기간 내 공시 목록 수집 → data/disclosures_{mode}.parquet
- 수정주가 OHLCV + 시가총액 캐싱, 거래정지/상폐 플래그 요약
모든 원천 응답은 디스크 캐시 — 재실행 시 재크롤링하지 않음.
"""

from __future__ import annotations

import pandas as pd

from dart_event_study.config import DATA_DIR, get_api_key, load_settings, load_universe, resolve_tickers
from dart_event_study.dart.client import DartClient


def year_slices(start: str, end: str) -> list[tuple[str, str]]:
    """(bgn_de, end_de) YYYYMMDD 연 단위 구간 — 캐시 파일을 연 단위로 쪼개기 위함."""
    s, e = pd.Timestamp(start), pd.Timestamp(end)
    out = []
    for y in range(s.year, e.year + 1):
        a = max(s, pd.Timestamp(y, 1, 1))
        b = min(e, pd.Timestamp(y, 12, 31))
        out.append((a.strftime("%Y%m%d"), b.strftime("%Y%m%d")))
    return out


def collect_disclosures(tickers: list[str], start: str, end: str, mode: str) -> pd.DataFrame:
    client = DartClient(get_api_key(), cache_dir=DATA_DIR / "dart")
    cmap = client.corp_code_map()

    missing = [t for t in tickers if t not in cmap]
    if missing:
        print(f"[warn] corp_code 매핑 실패 티커 {len(missing)}개: {missing}")

    rows: list[dict] = []
    for t in tickers:
        if t not in cmap:
            continue
        corp = cmap[t]
        for bgn, ende in year_slices(start, end):
            for r in client.list_disclosures(corp["corp_code"], bgn, ende):
                r["ticker"] = t
                rows.append(r)
        print(f"  {t} {corp['corp_name']}: 누적 {len(rows)}건")

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values(["ticker", "rcept_dt", "rcept_no"]).reset_index(drop=True)
    out = DATA_DIR / f"disclosures_{mode}.parquet"
    df.to_parquet(out)
    print(f"공시 목록 저장: {out} ({len(df)}건)")
    return df


def collect_prices(tickers: list[str], start: str, end: str) -> None:
    from quantlab_shared.data.calendar import TradingCalendar
    from quantlab_shared.data.prices import PriceStore

    cal = TradingCalendar.from_krx(start, end, cache_dir=DATA_DIR / "prices")
    store = PriceStore(DATA_DIR / "prices", start, end)
    print(f"거래일 캘린더: {len(cal.days)}일 ({cal.days[0]} ~ {cal.days[-1]})")
    for t in tickers:
        df = store.ohlcv(t)
        flags = store.status_flags(t, cal.days)
        print(f"  {t}: 가격 {len(df)}일, flags={flags}")


def main() -> None:
    settings, universe = load_settings(), load_universe()
    start, end = settings["period"]["start"], settings["period"]["end"]
    tickers = resolve_tickers(universe)
    print(f"mode={universe['mode']}, tickers={len(tickers)}개, 기간 {start} ~ {end}")

    df = collect_disclosures(tickers, start, end, universe["mode"])
    if not df.empty:
        print("\n공시 건수 (티커×연도):")
        summary = df.assign(year=df["rcept_dt"].str[:4]).groupby(["ticker", "year"]).size()
        print(summary.unstack(fill_value=0))

    print("\n가격 데이터:")
    collect_prices(tickers, start, end)


if __name__ == "__main__":
    main()
