"""잠정실적 원문 표 파싱 검증 — 실측 구조(셀트리온 20241108800423) 기반 픽스처."""

from dart_event_study.events.earnings import parse_earnings_table

# 실제 문서 구조 축약: 지표 셀(rowspan=2) + 당해실적/누계실적 서브행
FIXTURE = """
<table>
<tr><td>구분</td><td></td><td>당해실적</td><td>전기실적</td><td>전기대비</td><td>전년동기실적</td><td>전년동기대비</td></tr>
<tr>
  <td rowspan="2"><span>매출액</span></td>
  <td><span>당해실적</span></td>
  <td><span>881,933</span></td>
  <td><span>874,741</span></td>
  <td><span>+0.82%</span></td>
  <td><span>672,292</span></td>
  <td><span>+31.18%</span></td>
</tr>
<tr>
  <td><span>누계실적</span></td><td><span>2,493,655</span></td><td><span>-</span></td>
  <td><span>-</span></td><td><span>-</span></td><td><span>-</span></td>
</tr>
<tr>
  <td rowspan="2"><span>영업이익</span></td>
  <td><span>당해실적</span></td>
  <td><span>△1,000</span></td>
  <td><span>500</span></td>
  <td><span>적자전환</span></td>
  <td><span>2,000</span></td>
  <td><span>적자전환</span></td>
</tr>
<tr>
  <td><span>누계실적</span></td><td><span>3,000</span></td><td><span>-</span></td>
  <td><span>-</span></td><td><span>-</span></td><td><span>-</span></td>
</tr>
</table>
"""


def test_parse_earnings_table():
    t = parse_earnings_table(FIXTURE)
    assert t["매출액"] == {"current": 881933, "prev_q": 874741, "prev_yr": 672292}
    # 적자전환: % 열이 텍스트여도 원수치로 파싱됨
    assert t["영업이익"] == {"current": -1000, "prev_q": 500, "prev_yr": 2000}


def test_parse_ignores_summary_rows():
    # 누계실적 서브행이나 헤더는 지표로 잡히지 않아야 함
    t = parse_earnings_table(FIXTURE)
    assert set(t.keys()) == {"매출액", "영업이익"}
