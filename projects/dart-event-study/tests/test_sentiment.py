"""감성 레이어 검증 — 사전 채점·뉴스 파서·삼분위 (네트워크 불필요)."""

import numpy as np
import pandas as pd
import pytest
from dart_event_study.sentiment.lexicon import score_titles, title_polarity
from dart_event_study.sentiment.news import _parse_articles

# 실측 마크업 구조 축약 픽스처 (해시 클래스는 구조 앵커가 아니므로 생략)
FIXTURE = """
<div>
  <a href="https://n.news.naver.com/mnews/article/018/0004618571"><span>1兆 자사주 사들이는 포스코…주가 하방 지지</span></a>
  <a href="https://n.news.naver.com/mnews/article/018/0004618571"><span>포스코가 대규모 자사주 매입에 나선 것은 2007년 이후 13년여 만이다</span></a>
  <a href="https://www.edaily.co.kr/news/read?newsId=123"><span>키움證 "포스코 1조 자사주 매입 주주가치 제고 긍정적"</span></a>
  <a href="https://search.naver.com/search.naver?page=2"><span>다음 페이지로 이동하는 링크입니다</span></a>
  <a href="https://news.example.com/promo"><span>언론사가 선정한 주요기사 혹은 심층기획 기사입니다. 구독하세요.</span></a>
  <a href="https://news.example.com/short"><span>짧은글</span></a>
</div>
"""


def test_parser_dedupes_by_href_and_filters():
    arts = _parse_articles(FIXTURE)
    assert len(arts) == 2  # 네이버뉴스 1건(중복 href 병합) + edaily 1건
    # 같은 href는 더 긴 텍스트로 병합
    assert "13년여 만이다" in arts["https://n.news.naver.com/mnews/article/018/0004618571"]


def test_title_polarity():
    assert title_polarity("포스코 1조 자사주 매입 주주가치 제고 긍정적") == 1
    assert title_polarity("자사주 매입에도 주가 급락…우려 확산") == -1
    assert title_polarity("포스코 자사주 취득 결정") == 0
    # 혼합: 긍/부 동수면 0
    assert title_polarity("상승 기대 속 우려도, 하락 가능성") <= 0


def test_score_titles():
    s = score_titles(["급등 기대", "우려 확산", "중립 제목입니다"])
    assert s["n_pos"] == 1 and s["n_neg"] == 1
    assert s["sent_score"] == pytest.approx(0.0)
    assert score_titles([])["sent_score"] is None


def test_tercile_handles_ties():
    from dart_event_study.analysis.attention import tercile_report

    rng = np.random.default_rng(1)
    df = pd.DataFrame({
        "n_articles": [0] * 30 + list(range(1, 31)),  # 동점(0) 다수
        "car": rng.normal(0.02, 0.05, 60),
        "month": [f"2020-{m:02d}" for m in (list(range(1, 13)) * 5)],
    })
    rep = tercile_report(df, "n_articles")
    assert list(rep["N"][:3]) == [20, 20, 20]  # rank 분리로 균등 삼분위
    assert "상위-하위" in rep.iloc[-1]["그룹"]
