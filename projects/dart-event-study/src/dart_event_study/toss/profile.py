"""토스 수집 데이터 기술통계 — 지금 데이터로 정직하게 답할 수 있는 것만 (Phase 5).

실행:  uv run python -m dart_event_study.toss.profile

**왜 이벤트 스터디를 안 하는가 (핵심 제약)**: 토스 수집은 2026-07-15부터 순방향이고,
DART 이벤트·가격 데이터는 2019~2024다. **시간이 전혀 겹치지 않으므로** 공시 이벤트와
어텐션을 접합하는 분석은 지금 불가능하다(수개월 축적 후에나 가능). 겹치지 않는 데이터를
억지로 이어붙이는 것은 이 프로젝트 규율 위반이라 하지 않는다.

대신 여기서는 **수집 데이터 자체**를 프로파일링한다. 이건 향후 분석의 전제조건을 정량화하는
작업이다 — 종목별 활동량이 신호를 만들 만큼 되는지, 장중/장후 분리가 실제로 작동하는지,
삭제탐지가 어느 조건에서 판정 가능한지.

산출: data/processed/toss_profile_*.parquet + 콘솔 리포트
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

from dart_event_study.config import DATA_DIR

PROC = DATA_DIR / "processed"


def load() -> dict[str, pd.DataFrame]:
    out = {}
    for name in ("posts", "observations", "polls", "survival"):
        p = PROC / f"toss_{name}.parquet"
        out[name] = pd.read_parquet(p) if p.exists() else pd.DataFrame()
    return out


def session_of(ts: dt.datetime) -> str:
    """장중/장후 분류 (KRX 09:00~15:30 기준). first_seen은 크롤 시각이라 슬롯 해상도."""
    if ts.weekday() >= 5:
        return "weekend"
    h = ts.hour + ts.minute / 60
    return "intraday" if 9 <= h < 15.5 else "afterhours"


def activity_by_stock(posts: pd.DataFrame) -> pd.DataFrame:
    """종목별 활동량 — 신호 생성 가능성의 1차 판단 근거."""
    if posts.empty:
        return posts
    p = posts.copy()
    p["first_dt"] = pd.to_datetime(p["first_seen"])
    span_days = (p["first_dt"].max() - p["first_dt"].min()).total_seconds() / 86400
    g = p.groupby("stock").agg(
        posts=("post_id", "count"),
        first_obs=("first_dt", "min"),
        last_obs=("first_dt", "max"),
        edited=("edited", "sum"),
    ).reset_index()
    g["posts_per_day"] = (g["posts"] / max(span_days, 1)).round(1)
    return g.sort_values("posts_per_day", ascending=False)


def session_split(posts: pd.DataFrame) -> pd.DataFrame:
    """장중/장후/주말 분포 — 크롤 슬롯 설계(15:30 경계)가 실제로 갈라내는지 확인."""
    if posts.empty:
        return posts
    p = posts.copy()
    p["session"] = pd.to_datetime(p["first_seen"]).map(lambda t: session_of(t.to_pydatetime()))
    tab = p.groupby(["stock", "session"]).size().unstack(fill_value=0)
    for c in ("intraday", "afterhours", "weekend"):
        if c not in tab.columns:
            tab[c] = 0
    tab["total"] = tab.sum(axis=1)
    tab["intraday_share"] = (tab["intraday"] / tab["total"]).round(3)
    return tab.reset_index()


def deletion_diagnostics(posts: pd.DataFrame, polls: pd.DataFrame) -> pd.DataFrame:
    """삭제탐지 판정가능성 진단 — 왜 대부분 censored인가를 정량화.

    핵심: censored는 '삭제 안 됨'이 아니라 '판정 불가'다. 재관측 깊이(poll의 depth_reached)
    대비 유입량이 많으면 글이 관측창 밖으로 밀려 브래킷이 성립하지 않는다.
    """
    if posts.empty:
        return posts
    st = posts.groupby(["stock", "status"]).size().unstack(fill_value=0)
    for c in ("alive", "deleted", "censored"):
        if c not in st.columns:
            st[c] = 0
    st["total"] = st.sum(axis=1)
    st["censored_share"] = (st["censored"] / st["total"]).round(3)
    st = st.reset_index()
    if not polls.empty:
        d = polls.groupby("stock")["depth_reached"].agg(["mean", "max"]).round(1)
        d.columns = ["depth_mean", "depth_max"]
        st = st.merge(d.reset_index(), on="stock", how="left")
    return st.sort_values("censored_share", ascending=False)


def observation_quality(observations: pd.DataFrame, polls: pd.DataFrame) -> dict:
    """관측 품질 요약 — poll 수, 종목당 재관측 횟수 분포."""
    if observations.empty:
        return {}
    reobs = observations.groupby("post_id").size()
    return {
        "polls": int(polls["poll_id"].nunique()) if not polls.empty else 0,
        "observations": len(observations),
        "unique_posts": int(observations["post_id"].nunique()),
        "reobs_mean": round(float(reobs.mean()), 2),
        "reobs_once_share": round(float((reobs == 1).mean()), 3),  # 1회만 관측된 글 비율
        "reobs_max": int(reobs.max()),
    }


def main() -> None:
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    t = load()
    posts, obs, polls = t["posts"], t["observations"], t["polls"]
    if posts.empty:
        print("파생 테이블 없음 — 먼저 `python -m dart_event_study.toss.deletions` 실행")
        return

    p = posts.copy()
    p["first_dt"] = pd.to_datetime(p["first_seen"])
    start, end = p["first_dt"].min(), p["first_dt"].max()
    print("=" * 78)
    print("토스 커뮤니티 수집 데이터 프로파일")
    print("=" * 78)
    print(f"기간: {start:%Y-%m-%d %H:%M} ~ {end:%Y-%m-%d %H:%M} "
          f"({(end - start).days + 1}일) | 종목 {posts['stock'].nunique()} | 글 {len(posts):,}")
    print("\n⚠️ 공시 이벤트 스터디는 하지 않는다 — 토스(2026-07~)와 DART 이벤트(2019~2024)가")
    print("   시간상 전혀 겹치지 않는다. 접합 분석은 수개월 축적 후에나 가능.\n")

    q = observation_quality(obs, polls)
    print(f"[관측 품질] poll {q['polls']}회, 관측 {q['observations']:,}건, "
          f"글당 재관측 평균 {q['reobs_mean']}회 (최대 {q['reobs_max']})")
    print(f"  1회만 관측된 글: {q['reobs_once_share']:.1%} — 이 비율이 높을수록 삭제 판정 불가")

    act = activity_by_stock(posts)
    print("\n[종목별 활동량] — 향후 신호 생성 가능성의 1차 근거")
    show = act[["stock", "posts", "posts_per_day", "edited"]].head(20)
    print(show.to_string(index=False))

    sess = session_split(posts)
    print("\n[장중/장후 분리] — 15:30 경계 크롤 설계가 실제로 갈라내는가")
    print(sess[["stock", "intraday", "afterhours", "weekend", "intraday_share"]]
          .sort_values("intraday_share", ascending=False).to_string(index=False))
    tot_i, tot_a = int(sess["intraday"].sum()), int(sess["afterhours"].sum())
    tot_w = int(sess["weekend"].sum())
    print(f"  합계: 장중 {tot_i:,} / 장후 {tot_a:,} / 주말 {tot_w:,} "
          f"→ 장중 비중 {tot_i / max(tot_i + tot_a + tot_w, 1):.1%}")

    diag = deletion_diagnostics(posts, polls)
    print("\n[삭제탐지 판정가능성] — censored는 '삭제 안 됨'이 아니라 '판정 불가'")
    cols = [c for c in ("stock", "deleted", "alive", "censored", "censored_share",
                        "depth_mean", "depth_max") if c in diag.columns]
    print(diag[cols].to_string(index=False))
    tot_del = int(diag["deleted"].sum())
    tot_cen = int(diag["censored"].sum())
    print(f"  합계: 삭제확정 {tot_del} / 판정불가(censored) {tot_cen:,} "
          f"({tot_cen / len(posts):.1%})")

    PROC.mkdir(parents=True, exist_ok=True)
    act.to_parquet(PROC / "toss_profile_activity.parquet")
    sess.to_parquet(PROC / "toss_profile_session.parquet")
    diag.to_parquet(PROC / "toss_profile_deletion.parquet")
    print(f"\n저장: {PROC}/toss_profile_*.parquet")
    print("주의: 전부 raw 관측의 기술통계다. 지표·신호 정의나 인과 해석은 범위 밖.")


if __name__ == "__main__":
    main()
