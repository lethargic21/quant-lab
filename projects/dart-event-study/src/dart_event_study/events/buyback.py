"""자사주매입(직접취득) 이벤트 추출 — tsstkAqDecsn 구조화 API.

방향 = +1 고정 (호재), 강도 = 취득예정주식수(보통주) / 발행주식총수.
신탁계약 방식(tsstkAqTrctrCnsDecsn)은 디버그 셋에 표본이 없어 미구현 —
full 실행에서 표본이 나오면 추가한다 (PLAN §7-2 참고).
"""

from __future__ import annotations

import datetime as dt

from dart_event_study.dart.client import DartClient
from dart_event_study.events.parse import num
from dart_event_study.events.shares import total_shares_before


def extract_buybacks(
    client: DartClient, ticker: str, corp_code: str, bgn_de: str, end_de: str
) -> list[dict]:
    events = []
    for r in client.structured("tsstkAqDecsn.json", corp_code, bgn_de, end_de):
        rcept_no = r["rcept_no"]
        rcept_dt = rcept_no[:8]  # rcept_no 앞 8자리 = 접수일자 (실측 일치 확인)
        plan_shares = num(r.get("aqpln_stk_ostk"))
        total = total_shares_before(client, corp_code, dt.date.fromisoformat(f"{rcept_dt[:4]}-{rcept_dt[4:6]}-{rcept_dt[6:]}"))
        strength = plan_shares / total if (plan_shares and total) else None
        events.append(
            {
                "ticker": ticker,
                "corp_code": corp_code,
                "rcept_no": rcept_no,
                "rcept_dt": rcept_dt,
                "event_type": "buyback",
                "direction": 1,
                "strength": strength,
                "plan_shares": plan_shares,
                "plan_amount": num(r.get("aqpln_prc_ostk")),
                "total_shares": total,
                "method": (r.get("aq_mth") or "").strip(),
                "purpose": (r.get("aq_pp") or "").strip(),
            }
        )
    return events
