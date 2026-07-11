"""Phase 4b 백테스트 CLI — 시그널 포트폴리오 vs KOSPI.

실행:  uv run python -m dart_event_study.analysis.backtest
설정된 보유기간 전부 × (long-short / long-only) × (전체 / 이벤트타입별)을
빠짐없이 리포트한다 (절대 원칙 5 — 좋은 조합만 골라 자랑 금지).
"""

from __future__ import annotations

import pandas as pd
from quantlab_shared.backtest.costs import CostModel
from quantlab_shared.backtest.engine import run_backtest
from quantlab_shared.backtest.metrics import summary
from quantlab_shared.data.prices import PriceStore

from dart_event_study.config import DATA_DIR, load_settings, load_universe

COLS = ["ann_return", "ann_vol", "sharpe", "sortino", "mdd", "turnover_ann", "hit_ratio", "n_trades"]


def main() -> None:
    settings, universe = load_settings(), load_universe()
    mode = universe["mode"]
    start, end = settings["period"]["start"], settings["period"]["end"]
    c = settings["costs"]
    from quantlab_shared.backtest.costs import KOSPI_TAX_SCHEDULE

    cost = CostModel(
        transaction_tax=c["transaction_tax"],
        slippage=c["slippage"],
        commission=c.get("commission", 0.0),
        tax_schedule=KOSPI_TAX_SCHEDULE if c.get("use_tax_schedule") else None,
    )
    cost_mode = "시행일별 스케줄" if c.get("use_tax_schedule") else f"고정 {c['transaction_tax']:.2%}"
    print(f"비용 모드: 거래세 {cost_mode} + 슬리피지 {c['slippage']:.2%} + 수수료 {c.get('commission', 0):.3%} (편도)")

    signals = pd.read_parquet(DATA_DIR / f"signals_{mode}.parquet")
    store = PriceStore(DATA_DIR / "prices", start, end)
    closes = pd.DataFrame({t: store.ohlcv(t)["close"] for t in signals["ticker"].unique()})
    volumes = (
        pd.DataFrame({t: store.ohlcv(t)["volume"] for t in signals["ticker"].unique()})
        if settings["backtest"].get("roll_suspended")
        else None
    )

    delist_discount = settings["backtest"].get("delist_discount")
    # 손실형 상폐만 청산 할인 (proxy_2019 유니버스일 때 — 합병 상폐는 무할인 청산)
    delist_tickers = None
    if universe["mode"] == "full" and universe.get("selection") == "proxy_2019":
        from dart_event_study.config import resolve_universe_asof

        meta = resolve_universe_asof(universe)
        delist_tickers = set(meta["delisted_loss"])
        print(f"유니버스: proxy_2019 (상폐 {len(meta['delisted'])}종 포함, 손실형 할인 대상 {len(delist_tickers)}종)")

    rows = []
    for holding in settings["backtest"]["holding_days"]:
        scopes = [("all", signals)] + [(et, g) for et, g in signals.groupby("event_type")]
        for scope, sig in scopes:
            for variant, lo in [("long_short", False), ("long_only", True)]:
                res = run_backtest(
                    sig, closes, holding, cost, long_only=lo,
                    delist_discount=delist_discount, delist_tickers=delist_tickers,
                    volumes=volumes,
                )
                m = res.metrics()
                rows.append({"scope": scope, "H": holding, "variant": variant} | {c: m.get(c) for c in COLS})
                if scope == "all":  # 비용 영향 확인용 gross 병기
                    g = res.metrics(net=False)
                    rows.append(
                        {"scope": "all(gross)", "H": holding, "variant": variant}
                        | {c: g.get(c) for c in COLS}
                    )

    result = pd.DataFrame(rows)
    bench = summary(store.ohlcv("KS11")["close"].pct_change().dropna())
    result = pd.concat(
        [result, pd.DataFrame([{"scope": "KOSPI(벤치마크)", "H": "-", "variant": "-"} | bench])],
        ignore_index=True,
    )

    result["H"] = result["H"].astype(str)
    out = DATA_DIR / f"backtest_{mode}.parquet"
    result.to_parquet(out)
    print(f"저장: {out}\n")
    pd.set_option("display.float_format", lambda v: f"{v:.3f}")
    print(result.to_string(index=False))

    # v1.2 [6] 서브기간 안정성 — 헤드라인 전략이 특정 국면에 몰려 있지 않은지
    subperiods = settings["backtest"].get("subperiods") or []
    if subperiods:
        print("\n서브기간 안정성 (v1.2 [6] — 전 기간 결과와 병기, 좋은 쪽만 고르지 않음):")
        sub_rows = []
        sig_dates = pd.to_datetime(signals["signal_date"])
        for a, b in subperiods:
            sub_closes = closes.loc[a:b]
            sub_sig = signals[(sig_dates >= a) & (sig_dates <= b)]
            for scope, sg in [("all", sub_sig), ("buyback", sub_sig[sub_sig.event_type == "buyback"])]:
                for holding in settings["backtest"]["holding_days"]:
                    r = run_backtest(sg, sub_closes, holding, cost,
                                     delist_discount=delist_discount, delist_tickers=delist_tickers,
                                     volumes=volumes.loc[a:b] if volumes is not None else None)
                    m = r.metrics()
                    sub_rows.append({"period": f"{a[:4]}-{b[2:4]}", "scope": scope, "H": holding,
                                     "ann_return": m["ann_return"], "sharpe": m["sharpe"],
                                     "mdd": m["mdd"], "n_trades": m["n_trades"]})
            kospi_sub = store.ohlcv("KS11")["close"].loc[a:b].pct_change().dropna()
            ks = summary(kospi_sub)
            sub_rows.append({"period": f"{a[:4]}-{b[2:4]}", "scope": "KOSPI", "H": "-",
                             "ann_return": ks["ann_return"], "sharpe": ks["sharpe"],
                             "mdd": ks["mdd"], "n_trades": None})
        print(pd.DataFrame(sub_rows).to_string(index=False))

    # v1.2 [3-보완] 슬리피지 민감도 — 시장충격을 모델링하는 대신 범위로 정직하게 제시
    print("\n슬리피지 민감도 (편도, 자사주 H=5 / all H=5 long-short):")
    sens_rows = []
    for slip in (0.001, 0.003, 0.005):
        cost_s = CostModel(
            transaction_tax=c["transaction_tax"], slippage=slip,
            commission=c.get("commission", 0.0),
            tax_schedule=cost.tax_schedule,
        )
        for scope, sg, lo in [("buyback", signals[signals.event_type == "buyback"], False),
                              ("all", signals, False)]:
            r = run_backtest(sg, closes, 5, cost_s, long_only=lo,
                             delist_discount=delist_discount, delist_tickers=delist_tickers,
                             volumes=volumes)
            m = r.metrics()
            sens_rows.append({"slippage": f"{slip:.1%}", "scope": scope,
                              "ann_return": m["ann_return"], "sharpe": m["sharpe"]})
    print(pd.DataFrame(sens_rows).to_string(index=False))


if __name__ == "__main__":
    main()
