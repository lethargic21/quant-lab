"""팍스넷 종목토론실 목록 수집기 (게시판 심리 연구 — 정찰 2회 후 사용자 승인, 2026-07-13).

수집 원칙 (docs/data_feasibility.md):
- robots.txt 허용 경로(/tbbs/)만, 정직한 UA, 요청 간격 2.5초+지터
- 403/차단 시 우회 없이 중단 (재시도 1회 백오프 후 실패면 예외)
- **작성자 닉네임·ID는 파싱 자체를 하지 않음** (div.write 미접근)
- 원문은 data/raw/(gitignore)에만 저장, 페이지 단위 json 캐시로 중단 재개 가능
"""

from __future__ import annotations

import datetime as dt
import json
import random
import re
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

UA = "quant-lab-research-recon/1.0 (academic feasibility check)"
_MON = {"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
        "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12}


def parse_kst(s: str) -> dt.datetime | None:
    """'Mon Jul 13 12:47:09 KST 2026' → naive KST datetime."""
    m = re.match(r"\w{3} (\w{3}) (\d{1,2}) (\d{2}):(\d{2}):(\d{2}) KST (\d{4})", s)
    if not m:
        return None
    mo, d, h, mi, se, y = m.groups()
    return dt.datetime(int(y), _MON[mo], int(d), int(h), int(mi), int(se))


def parse_list_page(html: str) -> list[dict]:
    """목록 페이지 → 게시글 행. 작성자 관련 요소(.write)는 읽지 않는다."""
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for li in soup.find_all("li"):
        typ = li.select_one("div.type[data-seq]")
        tit = li.select_one(".title .tit a")
        date_el = li.select_one(".date .data-date-format")
        if not (typ and tit and date_el):
            continue
        seq = typ["data-seq"]
        posted = parse_kst(date_el.get("data-date-format", ""))
        if not posted:
            continue

        # li를 기본인자로 바인딩 — 현재 루프 안에서만 호출하므로 동작은 같지만,
        # 나중에 이 함수가 루프 밖으로 새도 늦은 바인딩 함정에 걸리지 않는다.
        def _num(sel: str, li=li) -> int:
            el = li.select_one(sel)
            digits = re.sub(r"\D", "", el.get_text()) if el else ""
            return int(digits) if digits else 0

        rows.append(
            {
                "seq": seq,
                "title": tit.get_text(" ", strip=True),
                "posted_at": posted,
                "views": _num("div.viewer"),
                "likes": _num("div.like"),
                "n_comments": _num(f"b#comment-num_{seq}"),
            }
        )
    return rows


class PaxnetBoard:
    def __init__(self, cache_dir: Path | str, throttle: float = 2.5):
        self.cache_dir = Path(cache_dir)
        self.throttle = throttle
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": UA})

    def _fetch(self, code: str, page: int) -> str:
        """403 = 차단 신호 → 1회 백오프 후 지속 시 중단 (우회 금지).

        ConnectionReset(10054) = 장시간 세션의 keep-alive 종료로 실측됨(175페이지 후 발생)
        → 연결 오류에 한해 60초 대기 + 새 세션 1회 재시도. 재발하면 소프트 스로틀
        가능성으로 간주하고 중단·보고 (공격적 재시도 금지).
        """
        last_exc: Exception | None = None
        for backoff in (0, 60):
            if backoff:
                time.sleep(backoff)
                self._session = requests.Session()
                self._session.headers.update({"User-Agent": UA})
            time.sleep(self.throttle + random.uniform(0, 1.0))
            try:
                r = self._session.get(
                    "https://www.paxnet.co.kr/tbbs/list",
                    params={"id": code, "tbbsType": "L", "page": str(page)},
                    timeout=20,
                )
            except (requests.ConnectionError, requests.Timeout) as e:
                last_exc = e
                continue
            if r.status_code != 403:
                r.raise_for_status()
                return r.text
        if last_exc:
            raise RuntimeError(
                f"팍스넷 연결 오류 재발 (code={code}, page={page}) — 소프트 스로틀 가능성, 중단: {last_exc}"
            )
        raise RuntimeError(f"팍스넷 403 지속 (code={code}, page={page}) — 우회하지 않고 중단")

    def list_page(self, code: str, page: int) -> list[dict]:
        """페이지 단위 json 캐시 (posted_at은 ISO 문자열로 저장)."""
        pdir = self.cache_dir / code
        pdir.mkdir(parents=True, exist_ok=True)
        path = pdir / f"page_{page:05d}.json"
        if path.exists():
            rows = json.loads(path.read_text(encoding="utf-8"))
        else:
            rows = parse_list_page(self._fetch(code, page))
            out = [{**r, "posted_at": r["posted_at"].isoformat()} for r in rows]
            path.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
            return rows
        return [{**r, "posted_at": dt.datetime.fromisoformat(r["posted_at"])} for r in rows]

    def collect(self, code: str, days: int = 365, max_pages: int = 600) -> list[dict]:
        """cutoff(오늘-days)까지 페이지를 내려가며 수집. 캐시 페이지는 요청 없이 재사용."""
        cutoff = dt.datetime.now() - dt.timedelta(days=days)
        all_rows: list[dict] = []
        for page in range(1, max_pages + 1):
            rows = self.list_page(code, page)
            if not rows:
                break
            all_rows.extend(rows)
            oldest = min(r["posted_at"] for r in rows)
            if oldest < cutoff:
                break
        return [r for r in all_rows if r["posted_at"] >= cutoff]
