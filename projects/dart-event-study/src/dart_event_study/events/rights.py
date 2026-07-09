"""유상증자 이벤트 추출 — piicDecsn 구조화 API.

방향 = 배정방식 기본점수 + 자금목적 보정 (config direction_rules.rights_offering),
강도 = 희석률 = 신주수(보통주) / 증자전 발행주식총수 — 둘 다 공시 필드에서 직접.
"""

from __future__ import annotations

from dart_event_study.dart.client import DartClient
from dart_event_study.events.parse import num

# fdpp_* 필드 → settings.yaml purpose_adjustment 키
PURPOSE_FIELDS = {
    "fdpp_fclt": "시설자금",
    "fdpp_op": "운영자금",
    "fdpp_dtrp": "채무상환",
    "fdpp_ocsa": "타법인증권취득",
    "fdpp_bsninh": "영업양수",
    "fdpp_etc": "기타",
}


def classify_allocation(ic_mthn: str) -> str:
    """증자방식 문자열 → 표준 배정방식. 매칭 순서 중요 (제3자 우선)."""
    s = (ic_mthn or "").replace(" ", "")
    if "제3자" in s or "제삼자" in s:
        return "제3자배정"
    if "주주배정" in s and "일반공모" in s:
        return "주주배정후실권주일반공모"
    if "주주배정" in s:
        return "주주배정"
    if "일반공모" in s:
        return "일반공모"
    return "기타"


def dominant_purpose(row: dict) -> tuple[str, dict[str, float]]:
    """용도별 금액 중 최대 항목. (지배 용도, 용도별 금액 dict) 반환."""
    amounts = {}
    for field, name in PURPOSE_FIELDS.items():
        v = num(row.get(field))
        if v:
            amounts[name] = v
    if not amounts:
        return "기타", {}
    return max(amounts, key=amounts.get), amounts


def direction_score(allocation: str, purpose: str, rules: dict) -> float:
    """배정방식 기본점수 + 목적 보정. rules = settings direction_rules.rights_offering."""
    base = rules["by_allocation"].get(allocation, 0)
    adj = rules["purpose_adjustment"].get(purpose, 0)
    return base + adj


def extract_rights_offerings(
    client: DartClient, ticker: str, corp_code: str, bgn_de: str, end_de: str, rules: dict
) -> list[dict]:
    events = []
    for r in client.structured("piicDecsn.json", corp_code, bgn_de, end_de):
        new_shares = num(r.get("nstk_ostk_cnt"))
        pre_shares = num(r.get("bfic_tisstk_ostk"))
        dilution = new_shares / pre_shares if (new_shares and pre_shares) else None
        allocation = classify_allocation(r.get("ic_mthn", ""))
        purpose, amounts = dominant_purpose(r)
        score = direction_score(allocation, purpose, rules)
        events.append(
            {
                "ticker": ticker,
                "corp_code": corp_code,
                "rcept_no": r["rcept_no"],
                "rcept_dt": r["rcept_no"][:8],
                "event_type": "rights_offering",
                "direction": 0 if score == 0 else (1 if score > 0 else -1),
                "strength": dilution,
                "score": score,
                "allocation": allocation,
                "purpose": purpose,
                "new_shares": new_shares,
                "pre_shares": pre_shares,
                "ic_mthn_raw": (r.get("ic_mthn") or "").strip(),
                "purpose_amounts": str(amounts),
            }
        )
    return events
