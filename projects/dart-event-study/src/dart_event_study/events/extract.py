"""Phase 2 이벤트 추출 CLI.

실행:  uv run python -m dart_event_study.events.extract
disclosures_{mode}.parquet(Phase 1 산출물) 기반으로 3종 이벤트를 추출해
events_{mode}.parquet 저장. 공통 스키마:
(ticker, corp_code, rcept_no, rcept_dt, event_type, direction, strength, 이벤트별 features...)
"""

from __future__ import annotations

import pandas as pd

from dart_event_study.collect import year_slices
from dart_event_study.config import DATA_DIR, get_api_key, load_settings, load_universe, resolve_tickers
from dart_event_study.dart.client import DartClient
from dart_event_study.events.buyback import extract_buybacks
from dart_event_study.events.earnings import extract_earnings
from dart_event_study.events.rights import extract_rights_offerings


def main() -> None:
    settings, universe = load_settings(), load_universe()
    start, end = settings["period"]["start"], settings["period"]["end"]
    mode = universe["mode"]
    tickers = resolve_tickers(universe)
    ro_rules = settings["direction_rules"]["rights_offering"]

    client = DartClient(get_api_key(), cache_dir=DATA_DIR / "dart")
    cmap = client.corp_code_map()
    disclosures = pd.read_parquet(DATA_DIR / f"disclosures_{mode}.parquet")

    events: list[dict] = []
    for t in tickers:
        if t not in cmap:
            continue
        cc = cmap[t]["corp_code"]
        for bgn, ende in year_slices(start, end):
            events += extract_buybacks(client, t, cc, bgn, ende)
            events += extract_rights_offerings(client, t, cc, bgn, ende, ro_rules)
    events += extract_earnings(client, disclosures)

    df = pd.DataFrame(events).sort_values(["rcept_dt", "ticker"]).reset_index(drop=True)
    out = DATA_DIR / f"events_{mode}.parquet"
    df.to_parquet(out)
    print(f"이벤트 저장: {out} ({len(df)}건)\n")

    print("이벤트 표본 수 (절대 원칙: 얇은 표본은 통계 결론 유보):")
    print(df.groupby("event_type").size().to_string())
    print("\n방향 분포:")
    print(df.groupby(["event_type", "direction"], dropna=False).size().to_string())

    er = df[df["event_type"] == "earnings"]
    if len(er):
        fail = er["direction"].isna().sum()
        print(f"\n실적 방향 판정 불가: {fail}/{len(er)}건 ({fail / len(er):.1%})")
        print("서프라이즈 기준(basis) 분포:")
        print(er["surprise_basis"].value_counts(dropna=False).to_string())

    ro = df[df["event_type"] == "rights_offering"]
    if len(ro):
        print("\n유상증자 배정방식×지배용도:")
        print(ro.groupby(["allocation", "purpose"]).size().to_string())


if __name__ == "__main__":
    main()
