"""토스 삭제탐지 — 스냅샷에서 posts/observations/polls 파생 + 브래킷 판정 (Phase 3).

실행:  uv run python -m dart_event_study.toss.deletions

**라이브 크롤은 건드리지 않는다.** 불변 스냅샷(data/raw/toss/{code}/{ts}.parquet)에서
오프라인으로 파생하므로 크롤 부하 증가 0, 재실행 idempotent. store.py의 삭제판정은 계속
비활성 — 정확한 탐지는 여기서만 한다(store.py 주석의 [[toss-deletion-detector]]가 이것).

핵심 규칙 — **absence ≠ deletion (제일 중요)**:
토스 피드는 고정글(pinned)이 섞여 id-내림차순이 완벽하지 않고(실측 0.88~0.95), 고정
old글 하나가 id범위를 수백만 벌려 **전역 [min,max] 브래킷은 대량 오탐**한다(store.py가 끈 이유).
그래서 전역 경계 대신 **피드-랭크 지역 인접성**으로 판정한다:

  글 X가 마지막 관측된 poll에서 바로 위(더 최신) 이웃 A, 바로 아래(더 오래) 이웃 B를 가졌다면,
  이후 어느 poll에서 A와 B가 **둘 다 관측되고 피드상 인접(rank 차 1)** 하면서 X가 없으면
  → X가 있던 자리가 닫힌 것 → **삭제 확정**.
  A 또는 B가 그 poll의 크롤 깊이 밖이면 → **확정 불가 = censored**(절대 삭제로 세지 않음).
  X가 최신(위 이웃 없음)이거나 최말단(아래 이웃 없음)이면 → 브래킷 불가 = censored.

고정글 아웃라이어에 강건: 전역 id경계를 안 쓰고 X의 실제 이웃만 보므로, 287M짜리 고정글이
있어도 오탐하지 않는다. 다중 삭제(이웃도 같이 삭제)는 보수적으로 censored 처리(과소계상 허용,
과대계상 금지).

우측절단: 최신 poll에 살아있으면 alive, 삭제 확정이면 deleted, 그 외 사라졌지만 미확정이면
censored. survival 테이블(event=1 삭제 / 0 절단)로 Kaplan-Meier 등에 바로 태울 수 있게 낸다.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd

from dart_event_study.config import DATA_DIR
from dart_event_study.toss.store import content_hash

RAW_DIR = DATA_DIR / "raw" / "toss"
OUT_DIR = DATA_DIR / "processed"


# ─────────────────────────────────────────────────────────────────────────────
# 순수 판정 로직 (parquet 무관 — 테스트가 여기 집중)
# ─────────────────────────────────────────────────────────────────────────────


def detect_deletions(polls: list[tuple[str, list[str]]]) -> dict[str, dict]:
    """피드-랭크 지역 인접성 브래킷 판정.

    polls: 시간순. 각 원소 = (poll_id, 피드순 post_id 리스트[0=최신]).
    반환: post_id -> {"status": alive|deleted|censored, "deleted_at_poll": poll_id|None}.
    """
    order = [ids for _, ids in polls]
    poll_ids = [pid for pid, _ in polls]
    rank = [{pid: i for i, pid in enumerate(ids)} for ids in order]
    n = len(polls)
    if n == 0:
        return {}

    last_idx: dict[str, int] = {}
    for k, ids in enumerate(order):
        for pid in ids:
            last_idx[pid] = k

    result: dict[str, dict] = {}
    for pid, lk in last_idx.items():
        if lk == n - 1:  # 최신 poll에 살아있음
            result[pid] = {"status": "alive", "deleted_at_poll": None}
            continue
        ids_lk = order[lk]
        i = rank[lk][pid]
        a = ids_lk[i - 1] if i > 0 else None          # 더 최신 이웃
        b = ids_lk[i + 1] if i < len(ids_lk) - 1 else None  # 더 오래 이웃
        deleted_poll = None
        if a is not None and b is not None:
            for p in range(lk + 1, n):
                ra, rb = rank[p].get(a), rank[p].get(b)
                if ra is not None and rb is not None and rb - ra == 1:
                    deleted_poll = poll_ids[p]  # 자리가 닫힘 = 삭제 확정
                    break
        result[pid] = (
            {"status": "deleted", "deleted_at_poll": deleted_poll}
            if deleted_poll is not None
            else {"status": "censored", "deleted_at_poll": None}
        )
    return result


# ─────────────────────────────────────────────────────────────────────────────
# 스냅샷 → 정규화 테이블
# ─────────────────────────────────────────────────────────────────────────────


def load_snapshots(raw_dir: Path, code: str) -> list[tuple[dt.datetime, pd.DataFrame]]:
    """한 종목의 스냅샷을 crawl_ts 순으로. feed_rank/content_hash 없으면 파생(구 스냅샷 보완)."""
    d = raw_dir / code
    out = []
    for f in sorted(f for f in d.glob("*.parquet") if f.stem != "_cumulative"):
        df = pd.read_parquet(f)
        if df.empty:
            ts = dt.datetime.strptime(f.stem, "%Y%m%dT%H%M%S")
            out.append((ts, df))
            continue
        if "feed_rank" not in df.columns:  # 구 스냅샷: 행순서 = 피드순서
            df = df.reset_index(drop=True)
            df["feed_rank"] = range(len(df))
        if "content_hash" not in df.columns:
            df["content_hash"] = df["title"].map(content_hash)
        ts = pd.to_datetime(df["crawl_ts"].iloc[0]).to_pydatetime()
        out.append((ts, df.sort_values("feed_rank").reset_index(drop=True)))
    out.sort(key=lambda x: x[0])
    return out


def build_tables(raw_dir: Path, code: str) -> dict[str, pd.DataFrame]:
    """posts / observations / polls / survival 4테이블을 파생."""
    snaps = load_snapshots(raw_dir, code)
    polls_seq: list[tuple[str, list[str]]] = []
    obs_rows, poll_rows = [], []

    for ts, df in snaps:
        poll_id = ts.isoformat()
        ids = [] if df.empty else df["post_id"].astype(str).tolist()
        polls_seq.append((poll_id, ids))
        poll_rows.append({
            "poll_id": poll_id, "stock": code,
            "started_at": ts.isoformat(), "ended_at": ts.isoformat(),  # Phase 1이 실측시각으로 대체
            "depth_reached": len(ids), "page_count": None,             # scroll수 미기록(Phase 1)
            "newest_post_seen": ids[0] if ids else None,
            "oldest_post_seen": ids[-1] if ids else None,
            "status": "empty" if not ids else "ok",
        })
        if not df.empty:
            for _, r in df.iterrows():
                obs_rows.append({
                    "poll_id": poll_id, "post_id": str(r["post_id"]),
                    "feed_rank": int(r["feed_rank"]), "observed_at": ts.isoformat(),
                    "content_hash": r.get("content_hash"),
                })

    observations = pd.DataFrame(obs_rows)
    polls = pd.DataFrame(poll_rows)
    status = detect_deletions(polls_seq)

    # posts 집계
    post_rows = []
    if not observations.empty:
        for pid, g in observations.sort_values("observed_at").groupby("post_id"):
            first = g.iloc[0]
            last = g.iloc[-1]
            # 원본 스냅샷에서 메타(작성자·created 라벨)를 첫 관측 시점 기준으로
            hashes = g["content_hash"].dropna().tolist()
            st = status.get(pid, {"status": "censored", "deleted_at_poll": None})
            post_rows.append({
                "post_id": pid, "stock": code,
                "first_seen": first["observed_at"], "last_seen": last["observed_at"],
                "n_observations": len(g),
                "content_hash": hashes[-1] if hashes else None,
                "n_content_hashes": len(set(hashes)),
                "edited": len(set(hashes)) > 1,
                "status": st["status"], "deleted_at_inferred": st["deleted_at_poll"],
            })
    posts = pd.DataFrame(post_rows)

    # 원본에서 작성자 해시·created 라벨 붙이기 (첫 관측 스냅샷 값)
    if not posts.empty:
        meta = {}
        for _, df in snaps:
            if df.empty:
                continue
            for _, r in df.iterrows():
                meta.setdefault(str(r["post_id"]), {
                    "author_hash": r.get("author_hash"),
                    "created_at_reported": r.get("relative_time_label"),
                })
        posts["author_hash"] = posts["post_id"].map(lambda p: meta.get(p, {}).get("author_hash"))
        posts["created_at_reported"] = posts["post_id"].map(
            lambda p: meta.get(p, {}).get("created_at_reported"))

    survival = _survival_table(posts)
    return {"posts": posts, "observations": observations, "polls": polls, "survival": survival}


def _survival_table(posts: pd.DataFrame) -> pd.DataFrame:
    """생존분석용: event=1(삭제)/0(절단), duration = first_seen→(삭제확정시각 or last_seen).

    좌측절단 주의(문서화): 첫 크롤에 이미 존재하던 글은 first_seen이 실제 작성보다 늦다
    → 그 글들의 duration은 하한. 순방향으로 '방금/N분'에 처음 잡힌 글만 정밀.
    """
    if posts.empty:
        return pd.DataFrame(columns=["post_id", "stock", "first_seen", "end_time",
                                     "event", "duration_min"])
    rows = []
    for _, r in posts.iterrows():
        end = r["deleted_at_inferred"] if r["status"] == "deleted" else r["last_seen"]
        t0 = pd.to_datetime(r["first_seen"])
        t1 = pd.to_datetime(end)
        rows.append({
            "post_id": r["post_id"], "stock": r["stock"],
            "first_seen": r["first_seen"], "end_time": end,
            "event": 1 if r["status"] == "deleted" else 0,  # 0 = 우측절단(alive/censored)
            "duration_min": round((t1 - t0).total_seconds() / 60, 1),
        })
    return pd.DataFrame(rows)


def main() -> None:
    import sys

    import yaml

    from dart_event_study.config import CONFIG_DIR

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    cfg = yaml.safe_load((CONFIG_DIR / "toss_universe.yaml").read_text(encoding="utf-8"))
    universe = {**cfg["compare"], **cfg["expand"]}
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    all_t = {k: [] for k in ("posts", "observations", "polls", "survival")}
    summary = []
    for code in universe:
        if not (RAW_DIR / code).exists():
            continue
        t = build_tables(RAW_DIR, code)
        for k, df in t.items():
            if not df.empty:
                all_t[k].append(df)
        p = t["posts"]
        if not p.empty:
            vc = p["status"].value_counts().to_dict()
            summary.append((code, len(p), vc.get("deleted", 0), vc.get("censored", 0),
                            vc.get("alive", 0), int(p["edited"].sum())))

    for k, parts in all_t.items():
        df = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
        df.to_parquet(OUT_DIR / f"toss_{k}.parquet")

    print("삭제탐지 파생 완료 (라이브 크롤 무관·오프라인). 저장:", OUT_DIR)
    print(f"\n{'종목':8} {'글수':>6} {'삭제':>6} {'절단':>6} {'생존':>6} {'편집':>6}")
    for code, n, d, c, a, e in summary:
        print(f"{code:8} {n:>6} {d:>6} {c:>6} {a:>6} {e:>6}")
    tot_del = sum(r[2] for r in summary)
    tot = sum(r[1] for r in summary)
    print(f"\n합계 글 {tot}, 삭제확정 {tot_del} ({tot_del / tot:.1%} — 참고용, 아직 raw 관측 단계)"
          if tot else "\n(데이터 없음)")
    print("주의: 삭제율은 지표가 아니라 raw 관측 산물. 좌측절단·크롤깊이 편향 미보정.")


if __name__ == "__main__":
    main()
