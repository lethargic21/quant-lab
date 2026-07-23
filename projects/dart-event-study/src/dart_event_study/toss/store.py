"""토스 크롤 스냅샷 저장 + 누적 테이블 (first_seen / is_deleted 판정).

- 스냅샷: data/raw/toss/{code}/{crawl_ts}.parquet (매 크롤 관측 전체)
- 누적:   data/raw/toss/{code}/_cumulative.parquet
  post_id, ticker, first_seen_at, last_seen_at, title, likes, comments,
  relative_time_label, author_hash, is_deleted, deleted_detected_at
  · first_seen_at = 사실상의 타임스탬프 (이 글이 처음 관측된 크롤 시각, KST)
  · is_deleted = 직전 크롤엔 있었는데 이번에 사라진 글 (스팸 삭제 신호)
  · likes/comments = 크롤마다 갱신 (시계열은 스냅샷들에 보존)
"""

from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path

import pandas as pd

from dart_event_study.toss.board import Post

CUM_COLS = [
    "post_id", "ticker", "first_seen_at", "last_seen_at", "title", "likes",
    "comments", "relative_time_label", "author_hash", "is_deleted", "deleted_detected_at",
]


def content_hash(title: str | None) -> str:
    """제목/본문 미리보기의 내용 해시 — 편집 탐지용 (원문은 raw에만, 해시는 파생 테이블 안전용)."""
    return hashlib.sha256((title or "").encode("utf-8")).hexdigest()[:16]


def _cum_path(raw_dir: Path, code: str) -> Path:
    d = raw_dir / code
    d.mkdir(parents=True, exist_ok=True)
    return d / "_cumulative.parquet"


def save_snapshot(raw_dir: Path, code: str, posts: list[Post], crawl_ts: dt.datetime) -> Path:
    """매 크롤 관측 전체를 피드 순서대로 저장.

    posts는 피드 순서(최신 우선)이므로 행 index = feed_rank. 삭제탐지의 브래킷 판정이
    이 순서에 의존하므로 **명시적으로 feed_rank 컬럼**으로 못박는다(파케이 행순서 신뢰 대신).
    content_hash는 편집 탐지용. 둘 다 additive — 기존 스냅샷엔 없어도 파생 단계가 보완한다.
    """
    rows = [
        p.__dict__ | {"feed_rank": rank, "content_hash": content_hash(p.title)}
        for rank, p in enumerate(posts)
    ]
    df = pd.DataFrame(rows)
    df["crawl_ts"] = crawl_ts.isoformat()
    path = raw_dir / code / f"{crawl_ts:%Y%m%dT%H%M%S}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path)
    return path


def update_cumulative(
    raw_dir: Path, code: str, posts: list[Post], crawl_ts: dt.datetime, hit_stop: bool = False
) -> dict:
    """누적 테이블 갱신. 반환: 이번 크롤 요약(신규/삭제/총 관측 수).

    hit_stop: 이번 크롤이 직전 크롤 글까지 다시 관측했는지. **현재 삭제 판정은 비활성화**되어
    이 인자는 사용하지 않는다(미래의 피드-순서 기반 탐지기용 시그니처로만 유지). 아래 삭제
    블록 주석 참조.
    """
    path = _cum_path(raw_dir, code)
    now = crawl_ts.isoformat()
    cur = {p.post_id: p for p in posts}

    if path.exists():
        cum = pd.read_parquet(path)
    else:
        cum = pd.DataFrame(columns=CUM_COLS)
    cum = cum.set_index("post_id") if len(cum) else pd.DataFrame(columns=CUM_COLS[1:]).rename_axis("post_id")

    prev_ids = set(cum.index)
    cur_ids = set(cur)
    new_ids = cur_ids - prev_ids

    rows = {pid: cum.loc[pid].to_dict() for pid in cum.index}
    # 신규/갱신
    for pid, p in cur.items():
        if pid in rows:
            r = rows[pid]
            r.update(last_seen_at=now, likes=p.likes, comments=p.comments,
                     relative_time_label=p.relative_time_label)
            if r.get("is_deleted"):  # 삭제됐다 재등장 → 되살림
                r["is_deleted"], r["deleted_detected_at"] = False, None
        else:
            rows[pid] = dict(
                ticker=p.ticker, first_seen_at=now, last_seen_at=now, title=p.title,
                likes=p.likes, comments=p.comments, relative_time_label=p.relative_time_label,
                author_hash=p.author_hash, is_deleted=False, deleted_detected_at=None,
            )
    # 삭제 판정: **이 라이브 경로에서는 계속 비활성**. is_deleted는 항상 False로 둔다.
    # 전역 id경계 기반 판정은 대량 오탐(실측: 000660 68글에 736건 오탐)이라 여기서 안 한다.
    # 정확한 탐지는 불변 스냅샷에서 오프라인으로 파생하는 별도 모듈이 담당한다 —
    # toss/deletions.py (피드-랭크 지역 인접성 브래킷). 라이브 크롤은 순수 관측만.
    # hit_stop 인자는 하위호환용 시그니처로 유지하되 사용하지 않는다.
    _ = hit_stop
    newly_deleted = 0

    out = pd.DataFrame.from_dict(rows, orient="index").rename_axis("post_id").reset_index()
    for c in CUM_COLS:
        if c not in out.columns:
            out[c] = None
    out[CUM_COLS].to_parquet(path)

    return {
        "observed": len(cur_ids),
        "new": len(new_ids),
        "deleted_this_crawl": newly_deleted,
        "cumulative_total": len(out),
    }
