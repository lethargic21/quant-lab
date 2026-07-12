"""네이버 뉴스 검색 크롤러 — 이벤트 창 기사 수집 (감성 레이어).

실측(PLAN §11): 날짜필터(ds/de)가 2019~24 과거 기사를 반환, 봇차단 없음.
쿼리 = "회사명 자사주", 창 = 접수일 ±1 달력일. 스로틀 + 이벤트 단위 json 캐시.

파싱 전략: 신형 마크업의 클래스명은 해시라 불안정 → 구조 앵커만 사용:
외부 기사 링크(<a href>)를 href로 중복 제거하고 가장 긴 한글 텍스트를 제목으로.
제목/요약이 같은 href를 공유하므로 href 수 ≈ 기사 카드 수 (근사 — 문서화).
"""

from __future__ import annotations

import datetime as dt
import json
import re
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
_PROMO = ("언론사가 선정한", "구독하세요", "네이버 메인에서")


def _parse_articles(html: str) -> dict[str, str]:
    """href → 대표 텍스트(가장 긴 한글 텍스트). 검색 내부/프로모 링크 제외."""
    soup = BeautifulSoup(html, "html.parser")
    out: dict[str, str] = {}
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not href.startswith("http") or any(
            k in href for k in ("search.naver", "help.naver", "keep.naver", "nid.naver")
        ):
            continue
        text = a.get_text(" ", strip=True).replace("새 창 열림", "").strip()
        if len(text) < 12 or not re.search(r"[가-힣]", text):
            continue
        if any(p in text for p in _PROMO):
            continue
        if len(text) > len(out.get(href, "")):
            out[href] = text
    return out


def fetch_event_news(
    corp_name: str,
    rcept_date: dt.date,
    cache_dir: Path,
    session: requests.Session | None = None,
    max_pages: int = 3,
    throttle: float = 0.8,
) -> dict:
    """이벤트 창([-1,+1] 달력일) '회사명 자사주' 기사 수집. 캐시 우선."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = f"{corp_name}_{rcept_date.isoformat()}".replace(" ", "")
    path = cache_dir / f"{key}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))

    session = session or requests.Session()
    ds = (rcept_date - dt.timedelta(days=1)).strftime("%Y.%m.%d")
    de = (rcept_date + dt.timedelta(days=1)).strftime("%Y.%m.%d")
    articles: dict[str, str] = {}
    for page in range(max_pages):
        time.sleep(throttle)
        r = session.get(
            "https://search.naver.com/search.naver",
            params={
                "where": "news", "query": f"{corp_name} 자사주", "sm": "tab_opt",
                "sort": "0", "pd": "3", "ds": ds, "de": de, "start": str(page * 10 + 1),
            },
            headers={"User-Agent": UA},
            timeout=20,
        )
        r.raise_for_status()
        before = len(articles)
        articles.update(_parse_articles(r.text))
        if len(articles) == before:  # 새 결과 없음 — 마지막 페이지
            break

    result = {
        "corp_name": corp_name,
        "date": rcept_date.isoformat(),
        "n_articles": len(articles),
        "titles": list(articles.values()),
    }
    path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return result
