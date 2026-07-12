"""제목 감성 룰 — 금융 뉴스 극성어 사전 (감성 레이어 B안).

모델 없이 투명한 단어 목록으로 제목 극성을 채점한다 (원 스펙: 룰 우선).
제목당 순극성 = sign(호재어 수 − 악재어 수), 이벤트 점수 = 제목 순극성의 평균 ∈ [-1, 1].
사전은 코드에 그대로 공개 — 재현 가능, 사후 조정 시 커밋 이력에 남음.
"""

from __future__ import annotations

POSITIVE = (
    "급등", "상승", "강세", "호재", "개선", "돌파", "신고가", "최고", "매수", "확대",
    "기대", "긍정", "주주가치", "주주환원", "제고", "회복", "반등", "순매수", "상향", "훈풍",
)
NEGATIVE = (
    "급락", "하락", "약세", "악재", "우려", "부진", "적자", "손실", "리스크", "최저",
    "매도", "축소", "부정", "하향", "불안", "급감", "쇼크", "논란", "먹튀", "실망",
)


def title_polarity(title: str) -> int:
    """제목 하나의 순극성: +1 / 0 / -1."""
    pos = sum(1 for w in POSITIVE if w in title)
    neg = sum(1 for w in NEGATIVE if w in title)
    return (pos > neg) - (neg > pos)


def score_titles(titles: list[str]) -> dict:
    """이벤트 단위 감성: 제목 순극성 평균 + 히트 수."""
    if not titles:
        return {"sent_score": None, "n_pos": 0, "n_neg": 0}
    pols = [title_polarity(t) for t in titles]
    return {
        "sent_score": sum(pols) / len(pols),
        "n_pos": sum(1 for p in pols if p > 0),
        "n_neg": sum(1 for p in pols if p < 0),
    }
