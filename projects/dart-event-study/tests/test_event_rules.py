"""이벤트 방향/강도 룰 + 숫자 파싱 검증 (네트워크 불필요)."""

import datetime as dt

from dart_event_study.events.parse import growth, num
from dart_event_study.events.rights import classify_allocation, direction_score, dominant_purpose

RULES = {
    "by_allocation": {
        "주주배정": -1,
        "주주배정후실권주일반공모": -1,
        "일반공모": -1,
        "제3자배정": 0,
    },
    "purpose_adjustment": {"채무상환": -0.5, "시설자금": 0.5, "타법인증권취득": 0.5, "운영자금": 0},
}


def test_num_parsing():
    assert num("1,362,800") == 1362800
    assert num("-") is None
    assert num("") is None
    assert num(None) is None
    assert num("△1,234") == -1234
    assert num("(500)") == -500


def test_growth_sign_conventions():
    assert growth(120, 100) == 0.2
    assert growth(-50, -100) == 0.5  # 적자 축소 = +
    assert growth(50, -100) == 1.5  # 흑자 전환 = +
    assert growth(-150, -100) == -0.5  # 적자 확대 = −
    assert growth(100, 0) is None
    assert growth(None, 100) is None


def test_classify_allocation():
    # 실측 문자열: "주주배정후 실권주 일반공모"
    assert classify_allocation("주주배정후 실권주 일반공모") == "주주배정후실권주일반공모"
    assert classify_allocation("주주배정증자") == "주주배정"
    assert classify_allocation("제3자배정증자") == "제3자배정"
    assert classify_allocation("일반공모증자") == "일반공모"


def test_dominant_purpose_from_real_fields():
    # 한화솔루션 2021 유증 실측 응답: 시설 6,356억 > 타법인 4,105억 > 운영 3,000억
    row = {
        "fdpp_fclt": "635,601,300,000",
        "fdpp_op": "300,014,800,000",
        "fdpp_ocsa": "410,473,800,000",
        "fdpp_dtrp": "-",
        "fdpp_bsninh": "-",
        "fdpp_etc": "-",
    }
    purpose, amounts = dominant_purpose(row)
    assert purpose == "시설자금"
    assert len(amounts) == 3


def test_direction_score():
    # 주주배정후실권주일반공모 + 시설자금 = -1 + 0.5 = -0.5 → 악재
    assert direction_score("주주배정후실권주일반공모", "시설자금", RULES) == -0.5
    # 제3자배정 + 타법인증권취득 = 0 + 0.5 → 약호재
    assert direction_score("제3자배정", "타법인증권취득", RULES) == 0.5
    # 주주배정 + 채무상환 = -1.5 → 강한 악재
    assert direction_score("주주배정", "채무상환", RULES) == -1.5


def test_shares_report_selection_rule():
    """이벤트일 이전 공개분만 후보가 되는지 — 후보 생성 로직만 검사."""
    from dart_event_study.events import shares

    calls = []

    class FakeClient:
        def stock_totals(self, corp_code, bsns_year, reprt_code):
            calls.append((bsns_year, reprt_code))
            return [{"se": "보통주", "istc_totqy": "88,761,861"}]

    # 2021-05-01 이벤트: 1분기보고서(3/31+45d=5/15)는 아직 미공개 → 2020 사업보고서(12/31+90d=3/31)가 최신
    result = shares.total_shares_before(FakeClient(), "X", dt.date(2021, 5, 1))
    assert result == 88761861
    assert calls[0] == ("2020", "11011")
