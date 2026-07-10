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
    cost = CostModel(
        transaction_tax=settings["costs"]["transaction_tax"],
        slippage=settings["costs"]["slippage"],
    )

    signals = pd.read_parquet(DATA_DIR / f"signals_{mode}.parquet")
    store = PriceStore(DATA_DIR / "prices", start, end)
    closes = pd.DataFrame({t: store.ohlcv(t)["close"] for t in signals["ticker"].unique()})

    delist_discount = settings["backtest"].get("delist_discount")

    rows = []
    for holding in settings["backtest"]["holding_days"]:
        scopes = [("all", signals)] + [(et, g) for et, g in signals.groupby("event_type")]
        for scope, sig in scopes:
            for variant, lo in [("long_short", False), ("long_only", True)]:
                res = run_backtest(sig, closes, holding, cost, long_only=lo, delist_discount=delist_discount)
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
    print("\n주의: 시총상위 근사 유니버스·현재 스냅샷(survivorship), 표본 얇은 그룹은 결론 유보.")


if __name__ == "__main__":
    main()
