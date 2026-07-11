"""거래비용 모델 (절대 원칙 4 — 현실적 비용을 파라미터로).

v1.2 [3]: 위탁수수료 + 날짜 의존 거래세 스케줄 추가.
증권거래세는 기간 중 계단식 인하(한국)라 상수 가정은 연도별 성과를 왜곡한다.
"""

from __future__ import annotations

import datetime as dt
from bisect import bisect_right
from dataclasses import dataclass, field


@dataclass(frozen=True)
class CostModel:
    """한국 주식 거래비용.

    - transaction_tax: 증권거래세(+농특세) — 매도 시. tax_schedule이 있으면 무시됨
    - slippage: 편도 슬리피지 가정 — 매수/매도 양쪽
    - commission: 위탁수수료 편도 — 매수/매도 양쪽
    - tax_schedule: ((시행일 ISO, 세율), ...) 오름차순 — 날짜 의존 세율.
      매도일이 첫 시행일 이전이면 transaction_tax로 폴백
    """

    transaction_tax: float = 0.0020
    slippage: float = 0.0010
    commission: float = 0.0
    tax_schedule: tuple[tuple[str, float], ...] | None = field(default=None)

    def tax_at(self, date: dt.date | str) -> float:
        if not self.tax_schedule:
            return self.transaction_tax
        d = str(date)[:10]
        idx = bisect_right([eff for eff, _ in self.tax_schedule], d) - 1
        return self.tax_schedule[idx][1] if idx >= 0 else self.transaction_tax

    @property
    def buy_cost(self) -> float:
        return self.slippage + self.commission

    @property
    def sell_cost(self) -> float:
        """고정 세율 기준 매도 비용 (스케줄 모드에서는 sell_cost_at 사용)."""
        return self.slippage + self.commission + self.transaction_tax

    def sell_cost_at(self, date: dt.date | str) -> float:
        return self.slippage + self.commission + self.tax_at(date)

    @property
    def round_trip(self) -> float:
        return self.buy_cost + self.sell_cost

    def round_trip_at(self, exit_date: dt.date | str) -> float:
        return self.buy_cost + self.sell_cost_at(exit_date)


# 한국 KOSPI 증권거래세+농어촌특별세 합계 시행일별 스케줄 (공식 인하 이력)
KOSPI_TAX_SCHEDULE: tuple[tuple[str, float], ...] = (
    ("2000-01-01", 0.0030),  # 0.15% + 0.15%
    ("2019-06-03", 0.0025),  # 0.10% + 0.15%
    ("2021-01-01", 0.0023),  # 0.08% + 0.15%
    ("2023-01-01", 0.0020),  # 0.05% + 0.15%
    ("2024-01-01", 0.0018),  # 0.03% + 0.15%
    ("2025-01-01", 0.0015),  # 0%    + 0.15%
)
