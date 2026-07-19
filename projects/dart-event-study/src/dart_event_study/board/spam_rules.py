"""스팸(리딩방 홍보·도배·광고) 규칙 기반 베이스라인 (1단계).

설계 원칙:
- 규칙·임계값 전부 파라미터 (5단계 민감도: 필터 on/off 및 강도 조절 가능)
- 각 규칙의 발화가 개별 플래그 컬럼으로 남아 어떤 규칙이 잡았는지 추적 가능
- 성능 수치는 사람 라벨(2단계) 확보 전에는 '커버리지'만 — 정확도 주장 금지

규칙 (제목 + 시각 기반; 작성자 정보 없이 동작):
1. marker  — 홍보성 기호/문구 (정찰 실측 ● 포함)
2. link    — 외부 URL/도메인 흔적
3. contact — 카톡·오픈채팅·텔레그램 등 연락 유도
4. lead    — 리딩방·무료체험·수익보장 등 리딩 영업 문구
5. dup     — 정규화 후 동일 제목의 코퍼스 내 반복 (near-duplicate 재게시)
6. burst   — 정규화 동일 제목이 짧은 시간창 안에 연속 게시 (도배)
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

import pandas as pd

# 홍보성 기호·문구 (정찰 관측 ● 등 + 게시판 통용 마커)
MARKER_RE = re.compile(r"[●▶▷★☆■◆♠]|щ|【|】")
LINK_RE = re.compile(r"https?://|www\.|\w+\.(?:com|co\.kr|kr|net|io|me)\b", re.I)
CONTACT_RE = re.compile(r"카톡|카카오톡|오픈\s?채팅|단톡|텔레그램|텔레\b|문의|검색창|프로필|010[-.\s]?\d{3,4}")
LEAD_RE = re.compile(
    r"리딩|추천주|급등주|무료\s?(?:체험|참여|입장|공개|상담)|수익\s?(?:보장|인증)"
    r"|VIP|족집게|선착순|입장\s?코드|방장|애널리스트\s?추천|종목\s?상담|폭등\s?임박"
)


def normalize(title: str) -> str:
    """near-duplicate 비교용 정규화: NFKC, 소문자, 한글/영숫자만, 숫자 제거.

    (도배꾼이 숫자·특수문자만 바꿔 재게시하는 패턴 대응)
    """
    t = unicodedata.normalize("NFKC", title).lower()
    t = re.sub(r"[^가-힣a-z0-9]", "", t)
    return re.sub(r"\d+", "", t)


@dataclass(frozen=True)
class SpamRuleParams:
    """임계값 파라미터 — 민감도 분석에서 조절."""

    dup_min: int = 3            # 코퍼스 내 동일 정규화 제목 반복 임계 (이상이면 dup)
    burst_window_min: int = 60  # 도배 시간창 (분)
    burst_min: int = 2          # 시간창 내 동일 제목 수 임계 (이상이면 burst)
    min_norm_len: int = 4       # 이보다 짧은 정규화 제목은 dup/burst 판정 제외 ("ㅋㅋ" 등 오탐 방지)
    enabled: bool = True        # 필터 전체 on/off (5단계)


# frozen dataclass라 공유 기본값이 안전 (인자 기본값에서 매번 생성하지 않도록 모듈 싱글턴)
DEFAULT_RULE_PARAMS = SpamRuleParams()


def add_rule_flags(df: pd.DataFrame, params: SpamRuleParams = DEFAULT_RULE_PARAMS) -> pd.DataFrame:
    """df(title, posted_at[, ticker]) → 규칙 플래그 컬럼 추가.

    spam_rule = 개별 규칙 OR. params.enabled=False면 모든 플래그 False (전/후 비교용).
    """
    out = df.copy()
    flags = ["flag_marker", "flag_link", "flag_contact", "flag_lead", "flag_dup", "flag_burst"]
    if not params.enabled:
        for f in flags + ["spam_rule"]:
            out[f] = False
        return out

    out["_norm"] = out["title"].map(normalize)
    out["flag_marker"] = out["title"].str.contains(MARKER_RE)
    out["flag_link"] = out["title"].str.contains(LINK_RE)
    out["flag_contact"] = out["title"].str.contains(CONTACT_RE)
    out["flag_lead"] = out["title"].str.contains(LEAD_RE)

    eligible = out["_norm"].str.len() >= params.min_norm_len
    grp_keys = ["ticker", "_norm"] if "ticker" in out.columns else ["_norm"]

    counts = out[eligible].groupby(grp_keys)["_norm"].transform("size")
    out["flag_dup"] = False
    out.loc[eligible, "flag_dup"] = counts >= params.dup_min

    # 도배: 같은 정규화 제목이 시간창 내 burst_min회 이상
    out["flag_burst"] = False
    sub = out[eligible].sort_values("posted_at")
    win = pd.Timedelta(minutes=params.burst_window_min)
    for _, g in sub.groupby(grp_keys):
        if len(g) < params.burst_min:
            continue
        t = g["posted_at"].reset_index()
        # 슬라이딩: i번째 글 기준 창 안에 burst_min개 이상이면 그 창의 글 전부 burst
        for i in range(len(t) - params.burst_min + 1):
            j = i + params.burst_min - 1
            if t.loc[j, "posted_at"] - t.loc[i, "posted_at"] <= win:
                out.loc[t.loc[i:j, "index"], "flag_burst"] = True

    out["spam_rule"] = out[flags].any(axis=1)
    return out.drop(columns=["_norm"])
