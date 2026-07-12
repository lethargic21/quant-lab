"""신탁계약 자사주 추출 검증 (실측 POSCO 응답 구조 기반, 네트워크 불필요)."""

import pytest
from dart_event_study.events.buyback import extract_trust_buybacks

# 실측 필드 구조 (POSCO홀딩스 20200410002632 축약)
TRUST_ROW = {
    "rcept_no": "20200410002632",
    "ctr_prc": "1,000,000,000,000",
    "ctr_pp": "주가 안정관리 및 주주가치 제고",
    "ctr_pd_bgd": "2020년 04월 13일",
    "ctr_pd_edd": "2021년 04월 12일",
}


class FakeClient:
    def structured(self, endpoint, corp_code, bgn_de, end_de):
        assert endpoint == "tsstkAqTrctrCnsDecsn.json"
        return [TRUST_ROW]

    def stock_totals(self, corp_code, bsns_year, reprt_code):
        return [{"se": "보통주", "istc_totqy": "87,186,835"}]  # POSCO 당시 발행총수


def test_trust_strength_amount_over_proxy_mcap():
    ev = extract_trust_buybacks(
        FakeClient(), "005490", "00155319", "20200101", "20201231",
        prev_close=lambda t, d: 200000.0,  # 접수 전일 종가 가정
    )[0]
    assert ev["event_type"] == "buyback_trust"
    assert ev["direction"] == 1
    assert ev["rcept_dt"] == "20200410"
    # 1조 / (20만원 × 87,186,835주 ≈ 17.4조) ≈ 5.7%
    assert ev["strength"] == pytest.approx(1e12 / (200000.0 * 87186835), rel=1e-9)
    assert ev["purpose"] == "주가 안정관리 및 주주가치 제고"


def test_trust_strength_none_when_price_missing():
    ev = extract_trust_buybacks(
        FakeClient(), "005490", "00155319", "20200101", "20201231",
        prev_close=lambda t, d: None,
    )[0]
    assert ev["strength"] is None
    assert ev["direction"] == 1  # 방향은 유지 (강도만 결측)
