"""거래비용 모델 (절대 원칙 4 — 현실적 비용을 파라미터로)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CostModel:
    """한국 주식 거래비용.

    - transaction_tax: 증권거래세 — 매도 시에만 부과
    - slippage: 편도 슬리피지 가정 — 매수/매도 양쪽
    """

    transaction_tax: float = 0.0020
    slippage: float = 0.0010

    @property
    def buy_cost(self) -> float:
        return self.slippage

    @property
    def sell_cost(self) -> float:
        return self.slippage + self.transaction_tax

    @property
    def round_trip(self) -> float:
        return self.buy_cost + self.sell_cost
