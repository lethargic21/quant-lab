"""성과지표 — 일별 수익률 시리즈 기반. rf=0 가정."""

from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def ann_return(daily: pd.Series) -> float:
    if len(daily) == 0:
        return np.nan
    total = float((1 + daily).prod())
    return total ** (TRADING_DAYS / len(daily)) - 1


def ann_vol(daily: pd.Series) -> float:
    return float(daily.std() * np.sqrt(TRADING_DAYS))


def sharpe(daily: pd.Series) -> float:
    vol = ann_vol(daily)
    return ann_return(daily) / vol if vol > 0 else np.nan


def sortino(daily: pd.Series) -> float:
    downside = daily[daily < 0]
    if len(downside) == 0:
        return np.nan
    dvol = float(downside.std() * np.sqrt(TRADING_DAYS))
    return ann_return(daily) / dvol if dvol > 0 else np.nan


def max_drawdown(daily: pd.Series) -> float:
    """최대 낙폭 (음수로 반환)."""
    nav = (1 + daily).cumprod()
    return float((nav / nav.cummax() - 1).min())


def summary(daily: pd.Series) -> dict:
    return {
        "ann_return": ann_return(daily),
        "ann_vol": ann_vol(daily),
        "sharpe": sharpe(daily),
        "sortino": sortino(daily),
        "mdd": max_drawdown(daily),
        "n_days": len(daily),
    }
