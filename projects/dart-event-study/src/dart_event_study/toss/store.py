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
from pathlib import Path

import pandas as pd

from dart_event_study.toss.board import Post

CUM_COLS = [
    "post_id", "ticker", "first_seen_at", "last_seen_at", "title", "likes",
    "comments", "relative_time_label", "author_hash", "is_deleted", "deleted_detected_at",
]


def _cum_path(raw_dir: Path, code: str) -> Path:
    d = raw_dir / code
    d.mkdir(parents=True, exist_ok=True)
    return d / "_cumulative.parquet"


def save_snapshot(raw_dir: Path, code: str, posts: list[Post], crawl_ts: dt.datetime) -> Path:
    df = pd.DataFrame([p.__dict__ for p in posts])
    df["crawl_ts"] = crawl_ts.isoformat()
    path = raw_dir / code / f"{crawl_ts:%Y%m%dT%H%M%S}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path)
    return path


def update_cumulative(
    raw_dir: Path, code: str, posts: list[Post], crawl_ts: dt.datetime, hit_stop: bool = False
) -> dict:
    """누적 테이블 갱신. 반환: 이번 크롤 요약(신규/삭제/총 관측 수).

    hit_stop: 이번 크롤이 직전 크롤 글까지 다시 관측했는지. **삭제 판정은 hit_stop일 때만**
    한다 — 윈도우가 이전 피드에 못 닿으면(신규 글이 많아 스크롤 상한에 걸린 경우 등) 사라짐이
    삭제인지 미도달인지 구분할 수 없기 때문. (토스 post_id는 전역 시간순이 아니라, 관측 최소 id를
    경계로 쓰면 22시간 공백 같은 성긴 크롤에서 대량 오탐이 난다 — 실측 확인.)
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
    # 삭제 판정: **hit_stop일 때만** (윈도우가 이전 피드까지 닿았을 때만 신뢰).
    # hit_stop=False면 관측 윈도우가 이전 글에 못 닿은 것 → 사라짐을 삭제로 볼 근거가 없다.
    # hit_stop=True면 이번 크롤이 실제 스캔한 id 범위(관측 최소 id 이상) 안에서 사라진 글만
    # 삭제로 본다. 순방향 dense 크롤(3h 간격, 신규 소량)에선 윈도우가 이전 창을 촘촘히 덮어
    # 정확하다. 성긴 크롤(예: 22h 공백)의 크로스-크롤 삭제는 신뢰 못 하므로 별도 태스크로 분리
    # (정확한 삭제 탐지기는 순방향 데이터 축적 후: [[toss-deletion-detector]]).
    newly_deleted = 0
    if hit_stop:
        obs_ids_num = [int(pid) for pid in cur_ids if str(pid).isdigit()]
        scan_floor = min(obs_ids_num) if obs_ids_num else None
        if scan_floor is not None:
            for pid in prev_ids - cur_ids:
                if not str(pid).isdigit() or int(pid) < scan_floor:
                    continue  # 스캔 범위 밖(더 과거) → 관측 안 함, 삭제 아님
                r = rows[pid]
                if not r.get("is_deleted"):
                    r["is_deleted"], r["deleted_detected_at"] = True, now
                    newly_deleted += 1

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
