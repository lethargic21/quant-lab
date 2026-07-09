"""실적공시(영업(잠정)실적 공정공시) 이벤트 추출 — 공시 원문 표 파싱.

잠정실적 공시 표에 당해실적·전기실적·전년동기실적이 함께 실리므로
YoY/QoQ 서프라이즈 대용치를 공시 원문만으로 계산한다 (외부 컨센서스 불필요,
look-ahead 없음). 표의 % 열 대신 원수치로 직접 계산해 적자전환 케이스를 다룬다.

방향 = sign(영업이익 YoY), 강도 = |영업이익 YoY| (config: surprise_metric).
단위(억원/백만원)는 회사마다 다르지만 성장률은 단위 불변.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

from dart_event_study.dart.client import DartClient, DartError
from dart_event_study.events.parse import growth, num

METRICS = ("매출액", "영업이익", "당기순이익")


def parse_earnings_table(html: str) -> dict[str, dict[str, float | None]]:
    """잠정실적 표 → {지표: {current, prev_q, prev_yr}}.

    실측 구조: 지표 셀(rowspan=2) 행이 '당해실적' 서브행을 겸함.
    셀 순서: [지표, '당해실적', 당해, 전기, 전기대비%, 전년동기, 전년동기대비%]
    """
    soup = BeautifulSoup(html, "html.parser")
    out: dict[str, dict[str, float | None]] = {}
    for tr in soup.find_all("tr"):
        texts = [c.get_text(strip=True) for c in tr.find_all("td")]
        if len(texts) < 7 or texts[1] != "당해실적":
            continue
        metric = re.sub(r"[\s()]", "", texts[0])
        for m in METRICS:
            if metric.startswith(m) and m not in out:
                out[m] = {
                    "current": num(texts[2]),
                    "prev_q": num(texts[3]),
                    "prev_yr": num(texts[5]),
                }
    return out


def extract_earnings(client: DartClient, disclosures) -> list[dict]:
    """disclosures(DataFrame)에서 잠정실적 공시를 골라 원문 파싱.

    연결/별도 둘 다 수집하되 consolidated 플래그로 구분.
    파싱 실패 건은 direction=None으로 남겨 실패율을 리포트할 수 있게 한다.
    """
    mask = disclosures["report_nm"].str.contains(r"영업\(잠정\)실적", regex=True)
    events = []
    for _, row in disclosures[mask].iterrows():
        rec: dict = {
            "ticker": row["ticker"],
            "corp_code": row["corp_code"],
            "rcept_no": row["rcept_no"],
            "rcept_dt": row["rcept_dt"],
            "event_type": "earnings",
            "consolidated": "연결" in row["report_nm"],
        }
        try:
            table = parse_earnings_table(client.document_html(row["rcept_no"]))
        except DartError as e:
            rec.update(direction=None, strength=None, parse_error=str(e)[:100])
            events.append(rec)
            continue

        for m, key in [("매출액", "sales"), ("영업이익", "op"), ("당기순이익", "np")]:
            vals = table.get(m, {})
            rec[f"{key}_yoy"] = growth(vals.get("current"), vals.get("prev_yr"))
            rec[f"{key}_qoq"] = growth(vals.get("current"), vals.get("prev_q"))

        # 시그널 기준: 영업이익 YoY (config). 구형 공시(주로 2019~20)는 전년동기실적
        # 칸이 비어 있어 YoY 계산 불가 → QoQ로 폴백하고 surprise_basis에 기록
        # (계절성 노이즈 있음 — 리포트에 basis 비중 명시)
        surprise, basis = rec.get("op_yoy"), "yoy"
        if surprise is None:
            surprise, basis = rec.get("op_qoq"), "qoq"
        rec["surprise_basis"] = None if surprise is None else basis
        rec["direction"] = None if surprise is None else (1 if surprise > 0 else -1 if surprise < 0 else 0)
        rec["strength"] = None if surprise is None else abs(surprise)
        rec["parse_error"] = None if table else "표 파싱 실패"
        events.append(rec)
    return events
