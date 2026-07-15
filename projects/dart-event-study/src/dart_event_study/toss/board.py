"""토스 커뮤니티 페이지 크롤 (Playwright + 실제 Chromium).

렌더된 DOM만 읽는다. 최신순 정렬을 강제하고 단조성으로 검증 — 검증 실패 시 예외
(인기순으로 수집하면 신규 글을 놓치므로 그 크롤은 실패 처리해야 한다).
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass

from playwright.sync_api import sync_playwright

COMMUNITY_URL = "https://www.tossinvest.com/stocks/A{code}/community"
_REL = re.compile(r"^(방금|\d+(?:초|분|시간|일|주|개월|년))$")
_UNIT_MIN = {"초": 1 / 60, "분": 1, "시간": 60, "일": 1440, "주": 10080, "개월": 43200, "년": 525600}


def rel_to_minutes(label: str | None) -> float | None:
    """상대시각 라벨 → 대략 분. 정렬 검증·참고용일 뿐 절대시각으로 신뢰하지 않는다."""
    if not label:
        return None
    if label == "방금":
        return 0.0
    m = re.match(r"(\d+)(초|분|시간|일|주|개월|년)", label)
    return int(m.group(1)) * _UNIT_MIN[m.group(2)] if m else None


@dataclass
class Post:
    post_id: str
    ticker: str
    title: str  # 제목/본문 미리보기 (목록 렌더 텍스트)
    likes: int
    comments: int
    relative_time_label: str | None
    author_hash: str | None  # salted hash (원문 닉네임 미저장)


# 게시글 컨테이너: div[data-post-anchor-id].
# 렌더 라인 구조(실측): [배지(주주 등), 닉네임, 상대시각, 팔로우, 본문..., 좋아요, 댓글]
# → '팔로우' 앞 = 헤더(닉네임/시각), '팔로우' 뒤 = 본문 + 말미 숫자.
# 이 위치 규칙으로 닉네임이 본문에 새지 않게 분리한다(원문 닉네임 미저장, 해시만).
_EXTRACT_JS = r"""() => {
  const relRe = /^(방금|\d+(초|분|시간|일|주|개월|년))$/;
  return [...document.querySelectorAll('div[data-post-anchor-id]')].map(d => {
    const lines = d.innerText.split('\n').map(s => s.trim()).filter(Boolean);
    const fi = lines.indexOf('팔로우');
    const header = fi >= 0 ? lines.slice(0, fi) : lines.slice(0, 3);
    const tail = fi >= 0 ? lines.slice(fi + 1) : lines;

    const rel = header.find(s => relRe.test(s)) || null;
    // 닉네임: 헤더에서 상대시각 바로 앞 라인 (없으면 마지막 헤더 라인)
    let author = null;
    const ri = header.findIndex(s => relRe.test(s));
    if (ri > 0) author = header[ri - 1];
    else if (header.length) author = header[header.length - 1];

    // 본문: '팔로우' 뒤에서 순수 숫자(좋아요/댓글)가 아닌 라인
    const bodyLines = tail.filter(s => !/^\d[\d,]*$/.test(s) && s !== '팔로우');
    const nums = tail.filter(s => /^\d[\d,]*$/.test(s)).map(s => parseInt(s.replace(/,/g,'')));
    return {
      id: d.getAttribute('data-post-anchor-id'),
      body: bodyLines.join(' ').slice(0, 500),
      rel, author,
      likes: nums.length ? nums[0] : 0,
      comments: nums.length > 1 ? nums[1] : 0,
    };
  });
}"""


def _hash_author(nick: str | None, salt: str) -> str | None:
    if not nick:
        return None
    return hashlib.sha256((salt + nick).encode("utf-8")).hexdigest()[:16]


def _switch_to_latest(page) -> bool:
    """정렬을 최신순으로 전환하고 상대시각 단조성으로 검증. 최대 3회 시도."""
    for _ in range(3):
        try:
            page.locator("button:has-text('인기순')").first.click(timeout=5000)
            page.wait_for_timeout(1200)
            # 드롭다운의 '최신순' 항목 좌표 클릭 (포털 렌더 대응)
            cand = page.evaluate(
                """() => { const out=[];
                   for (const e of document.querySelectorAll('*')) {
                     if (e.childElementCount===0 && e.textContent.trim()==='최신순') {
                       const r=e.getBoundingClientRect();
                       if (r.width>0&&r.height>0) out.push([Math.round(r.x+r.width/2), Math.round(r.y+r.height/2)]); } }
                   return out; }"""
            )
            if cand:
                page.mouse.click(cand[-1][0], cand[-1][1])
                page.wait_for_timeout(3000)
        except Exception:
            page.wait_for_timeout(1000)
        # 검증: 최신순이면 상위 글들의 상대시각이 단조 증가
        labels = page.evaluate(
            """() => [...document.querySelectorAll('div[data-post-anchor-id]')].map(d => {
                 const t=d.innerText.split('\\n').map(s=>s.trim()).filter(Boolean);
                 return t.find(s=>/^(방금|\\d+(초|분|시간|일|주|개월|년))$/.test(s))||null; })"""
        )
        mins = [rel_to_minutes(x) for x in labels if x is not None]
        if len(mins) >= 4 and all(mins[i] <= mins[i + 1] + 2 for i in range(len(mins) - 1)):
            return True
    return False


def crawl_community(
    code: str, salt: str, stop_ids: set[str] | None = None, max_scrolls: int = 40, headless: bool = True
) -> list[Post]:
    """한 종목 커뮤니티를 최신순으로 크롤. stop_ids(직전 크롤 관측분)에 닿으면 조기 종료.

    최신순 검증 실패 시 RuntimeError — 호출측이 그 종목 크롤을 실패로 기록한다.
    """
    stop_ids = stop_ids or set()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)  # UA 무변조 = 정직한 Chromium
        page = browser.new_page(viewport={"width": 1400, "height": 2200})
        try:
            page.goto(COMMUNITY_URL.format(code=code), timeout=45000)
            page.wait_for_timeout(7000)
            body = page.inner_text("body")
            if "지원하지 않는 브라우저" in body:
                raise RuntimeError(f"{code}: 미지원 브라우저 경고 — 중단(우회 금지)")
            if not _switch_to_latest(page):
                raise RuntimeError(f"{code}: 최신순 정렬 검증 실패 — 설계상 수집 중단")

            seen: dict[str, dict] = {}
            hit_stop = False
            for _ in range(max_scrolls):
                for it in page.evaluate(_EXTRACT_JS):
                    if it["id"] in stop_ids:
                        hit_stop = True
                    seen.setdefault(it["id"], it)
                if hit_stop:
                    break
                page.mouse.wheel(0, 2200)
                page.wait_for_timeout(1400)
        finally:
            browser.close()

    return [
        Post(
            post_id=it["id"], ticker=code, title=it["body"][:500],
            likes=int(it.get("likes") or 0), comments=int(it.get("comments") or 0),
            relative_time_label=it["rel"], author_hash=_hash_author(it.get("author"), salt),
        )
        for it in seen.values()
    ]
