"""이벤트 시그널 백테스트 엔진 — 가중치 행렬 방식.

모델 (단순·해석 가능 우선):
- 시그널 발생일 종가에 진입, holding_days 거래일 보유 후 종가 청산
- 동시 보유 포지션은 균등가중(일별 리밸런스), 포지션 없으면 현금(수익률 0)
- 비용 = 가중치 변화량(|Δw|)에 매수/매도 비용을 곱해 당일 수익률에서 차감
  (균등가중 일별 리밸런스로 인한 미세 조정 비용까지 포함 — 보수적)
- 숏은 -가중치로 표현. 한국 시장 공매도 제약은 사용하는 쪽에서 판단
  (long_only 필터로 롱만 돌릴 수 있음)
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from quantlab_shared.backtest.costs import CostModel
from quantlab_shared.backtest.metrics import TRADING_DAYS, summary


@dataclass
class BacktestResult:
    daily_gross: pd.Series  # 비용 차감 전 일별 수익률
    daily_net: pd.Series  # 비용 차감 후
    trades: pd.DataFrame  # 트레이드별 (entry, exit, direction, gross_ret, net_ret)
    turnover_ann: float  # 연환산 편도 turnover (Σ|Δw|/2 × 252/일수)

    def metrics(self, net: bool = True) -> dict:
        m = summary(self.daily_net if net else self.daily_gross)
        m["turnover_ann"] = self.turnover_ann
        if len(self.trades):
            m["hit_ratio"] = float((self.trades["net_ret"] > 0).mean())
            m["n_trades"] = len(self.trades)
        else:
            m["hit_ratio"], m["n_trades"] = np.nan, 0
        return m


def run_backtest(
    signals: pd.DataFrame,
    closes: pd.DataFrame,
    holding_days: int,
    cost: CostModel,
    long_only: bool = False,
) -> BacktestResult:
    """signals: (ticker, signal_date, direction) — signal_date에 종가 진입.
    closes: 일별 종가 wide 테이블 (index=date, columns=ticker).
    """
    sig = signals.copy()
    if long_only:
        sig = sig[sig["direction"] > 0]

    dates = closes.index
    rets = closes.pct_change()

    # 포지션 부호 행렬: 진입 익일부터 청산일까지 수익률 귀속
    pos = pd.DataFrame(0.0, index=dates, columns=closes.columns)
    trades = []
    for _, s in sig.iterrows():
        t = s["ticker"]
        if t not in closes.columns:
            continue
        i = dates.searchsorted(pd.Timestamp(s["signal_date"]))
        if i >= len(dates) or dates[i].date() != pd.Timestamp(s["signal_date"]).date():
            continue  # 진입일에 가격 없음
        j = min(i + holding_days, len(dates) - 1)
        if j <= i or pd.isna(closes[t].iloc[i]):
            continue
        pos.iloc[i + 1 : j + 1, pos.columns.get_loc(t)] += s["direction"]
        gross = (closes[t].iloc[j] / closes[t].iloc[i] - 1) * s["direction"]
        trades.append(
            {
                "ticker": t,
                "entry": dates[i].date(),
                "exit": dates[j].date(),
                "direction": s["direction"],
                "gross_ret": gross,
                "net_ret": gross - cost.round_trip,
            }
        )

    # 균등가중: w_i = pos_i / Σ|pos| (gross exposure 1)
    # pos[t]는 "t일 수익률(close[t-1]→close[t])이 귀속되는 포지션" — 진입일 종가 체결이므로
    # 진입 익일부터 값이 있음. 따라서 추가 shift 없이 그대로 곱한다 (look-ahead 아님).
    gross_exp = pos.abs().sum(axis=1)
    weights = pos.div(gross_exp.replace(0, np.nan), axis=0).fillna(0.0)

    daily_gross = (weights * rets).sum(axis=1)

    dw = weights.diff().fillna(weights)
    buys, sells = dw.clip(lower=0), (-dw).clip(lower=0)  # 가중치 증가=매수, 감소=매도
    daily_cost = buys.sum(axis=1) * cost.buy_cost + sells.sum(axis=1) * cost.sell_cost
    daily_net = daily_gross - daily_cost

    n_days = max(len(dates) - 1, 1)
    turnover_ann = float(dw.abs().sum(axis=1).sum() / 2 / n_days * TRADING_DAYS)

    return BacktestResult(
        daily_gross=daily_gross,
        daily_net=daily_net,
        trades=pd.DataFrame(trades),
        turnover_ann=turnover_ann,
    )
