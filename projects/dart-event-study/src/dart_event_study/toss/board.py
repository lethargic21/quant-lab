"""토스 커뮤니티 페이지 크롤 (Playwright + 실제 Chromium).

렌더된 DOM만 읽는다. 최신순 정렬을 강제하고 단조성으로 검증 — 검증 실패 시 예외
(인기순으로 수집하면 신규 글을 놓치므로 그 크롤은 실패 처리해야 한다).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from playwright.sync_api import sync_playwright

COMMUNITY_URL = "https://www.tossinvest.com/stocks/A{code}/community"


class SortValidationError(RuntimeError):
    """최신순 정렬 검증 실패 — 그 종목·그 슬롯은 결측 처리(오염 쌓느니 건너뜀).

    n_posts(초기 렌더 글 수)를 실어 호출측이 결측 로그(sort_failures.csv)에 남긴다.
    """

    def __init__(self, code: str, n_posts: int):
        self.code, self.n_posts = code, n_posts
        super().__init__(f"{code}: 최신순 정렬 검증 실패(글 {n_posts}개) — 설계상 수집 중단")
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


def _is_latest_sorted(page) -> bool:
    """현재 렌더된 게시글이 최신순인지 두 독립 증거 중 하나로 검증(저활성 종목 대응)."""
    rows = page.evaluate(
        """() => [...document.querySelectorAll('div[data-post-anchor-id]')].map(d => {
             const t=d.innerText.split('\\n').map(s=>s.trim()).filter(Boolean);
             return {id: d.getAttribute('data-post-anchor-id'),
                     rel: t.find(s=>/^(방금|\\d+(초|분|시간|일|주|개월|년))$/.test(s))||null}; })"""
    )
    if not rows:
        return False
    # (a) 상대시각 단조 증가 (표본 4+)
    mins = [rel_to_minutes(r["rel"]) for r in rows if r["rel"] is not None]
    if len(mins) >= 4 and all(mins[i] <= mins[i + 1] + 2 for i in range(len(mins) - 1)):
        return True
    # (b) post_id 내림차순 (id는 시간순 발급 — 높을수록 최신). 저활성 종목 폴백.
    ids = [int(r["id"]) for r in rows if str(r["id"]).isdigit()]
    if len(ids) >= 3:
        desc = sum(ids[i] >= ids[i + 1] for i in range(len(ids) - 1)) / (len(ids) - 1)
        if desc >= 0.85:  # 고정글 소수 예외 허용
            return True
    return False


def _sort_label(page) -> str | None:
    """정렬 컨트롤 버튼의 현재 라벨('인기순'|'최신순'). 없으면 None."""
    return page.evaluate(
        """() => { const b=[...document.querySelectorAll('button')]
             .find(b=>/^(인기순|최신순)$/.test(b.innerText.trim()));
           return b ? b.innerText.trim() : null; }"""
    )


def _switch_to_latest(page) -> bool:
    """정렬을 최신순으로 전환. 최대 4회 시도.

    토스 정렬 컨트롤은 클릭 시 인기순<->최신순으로 **토글**한다(실측). 예전 구현은 인기순을
    클릭해 드롭다운을 연 뒤 '최신순' 항목 좌표를 클릭하려 했으나, 항목 탐지 필터
    (childElementCount===0)가 실제 요소(childCount=1)와 안 맞아 **한 번도 실행되지 않았다**.
    게다가 그 항목 클릭이 실행됐다면 정렬을 도로 인기순으로 되돌렸을 것이다.
    → 드롭다운 항목 클릭을 제거하고, 현재 라벨을 보고 '인기순'일 때만 토글한다.
    검증은 항상 실제 렌더된 게시글 순서(_is_latest_sorted)로 한다.
    """
    for _ in range(4):
        if _is_latest_sorted(page):
            return True
        try:
            if _sort_label(page) == "인기순":
                page.locator("button:has-text('인기순')").first.click(timeout=5000)
                page.wait_for_timeout(2500)  # 토글 후 재정렬 렌더 대기
            else:
                page.wait_for_timeout(1200)  # 최신순인데 미검증 → 피드 재렌더 대기
        except Exception:
            page.wait_for_timeout(1000)
    return _is_latest_sorted(page)


def crawl_community(
    code: str, salt: str, stop_ids: set[str] | None = None, max_scrolls: int = 40, headless: bool = True
) -> tuple[list[Post], bool]:
    """한 종목 커뮤니티를 최신순으로 크롤. stop_ids(직전 크롤 관측분)에 닿으면 조기 종료.

    반환: (posts, hit_stop). hit_stop=True면 이번 크롤이 직전 크롤 글까지 다시 관측했다는
    뜻(= 관측 윈도우가 이전 피드까지 닿음). 삭제 판정은 hit_stop일 때만 신뢰할 수 있다
    (윈도우가 이전 글에 닿지 못하면 사라짐이 삭제인지 스크롤 미도달인지 구분 불가).

    최신순 검증 실패 시 SortValidationError — 호출측이 그 종목 크롤을 결측으로 기록한다.
    """
    stop_ids = stop_ids or set()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)  # UA 무변조 = 정직한 Chromium
        page = browser.new_page(viewport={"width": 1400, "height": 2200})
        try:
            page.goto(COMMUNITY_URL.format(code=code), timeout=45000)
            page.wait_for_timeout(9000)
            body = page.inner_text("body")
            if "지원하지 않는 브라우저" in body:
                raise RuntimeError(f"{code}: 미지원 브라우저 경고 — 중단(우회 금지)")
            # 빈 커뮤니티(글 0개)는 정상 — 정렬 검증 대상이 없으므로 실패가 아니라 빈 결과.
            # (검증 실패로 처리하면 비활성 종목이 매 크롤 거짓 알림을 낸다)
            n_posts = len(page.query_selector_all("div[data-post-anchor-id]"))
            if n_posts == 0:
                page.wait_for_timeout(3000)  # 느린 렌더 한 번 더 기회
                n_posts = len(page.query_selector_all("div[data-post-anchor-id]"))
            if n_posts == 0:
                return [], False  # 빈 커뮤니티
            # 글이 있으면 반드시 최신순 검증 — 인기순으로 오수집하면 first-seen이 무너진다.
            if not _switch_to_latest(page):
                raise SortValidationError(code, n_posts)

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

    posts = [
        Post(
            post_id=it["id"], ticker=code, title=it["body"][:500],
            likes=int(it.get("likes") or 0), comments=int(it.get("comments") or 0),
            relative_time_label=it["rel"], author_hash=_hash_author(it.get("author"), salt),
        )
        for it in seen.values()
    ]
    return posts, hit_stop
