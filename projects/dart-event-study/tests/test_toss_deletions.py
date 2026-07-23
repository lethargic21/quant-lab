"""토스 삭제탐지 검증 (네트워크 불필요).

핵심은 **오탐 방지 가드**다 — absence≠deletion. store.py가 삭제판정을 끈 실측 오탐
시나리오(고정글 아웃라이어, 크롤깊이 밖 밀림)를 재현해 '삭제로 세지 않음'을 못박는다.
"""

import datetime as dt

import pandas as pd
import pytest
from dart_event_study.toss.deletions import build_tables, detect_deletions
from dart_event_study.toss.store import content_hash, save_snapshot


def _polls(*seqs):
    """(poll_id, [ids]) 리스트로 변환. 입력은 최신→오래 순 id 리스트들."""
    return [(f"p{i}", [str(x) for x in s]) for i, s in enumerate(seqs)]


# ── 삭제 확정 (자리 닫힘) ─────────────────────────────────────────────────────


def test_clean_bracket_collapse_is_deletion():
    # X=5가 A=6, B=4 사이. 다음 poll에서 6,4 인접 + 5 없음 → 삭제.
    r = detect_deletions(_polls([7, 6, 5, 4], [7, 6, 4], [7, 6, 4]))
    assert r["5"]["status"] == "deleted"
    assert r["5"]["deleted_at_poll"] == "p1"
    assert r["6"]["status"] == "alive" and r["4"]["status"] == "alive"


def test_deletion_confirmed_at_earliest_collapse_poll():
    r = detect_deletions(_polls([9, 8, 7], [9, 8, 7], [9, 7]))
    assert r["8"]["status"] == "deleted" and r["8"]["deleted_at_poll"] == "p2"


# ── 오탐 방지 (제일 중요) ────────────────────────────────────────────────────


def test_pushed_beyond_depth_is_censored_not_deleted():
    # X=5 관측 후, 다음 poll이 얕게(최신 2개만) 관측 → 5의 이웃 B=4가 깊이 밖.
    # 브래킷 미성립 → censored. 예전 전역경계 로직이면 오탐했을 자리.
    r = detect_deletions(_polls([7, 6, 5, 4], [9, 8], [9, 8]))
    assert r["5"]["status"] == "censored"
    assert r["5"]["deleted_at_poll"] is None


def test_pinned_outlier_does_not_cause_false_positive():
    # 고정글 100(항상 맨 아래, id 아웃라이어). 최근글 50이 얕은 재관측서 깊이 밖으로 밀림.
    # 전역 [min,max] 브래킷이면 50이 100~60 사이라 오탐 → 지역 인접성은 censored.
    p = _polls(
        [62, 61, 60, 50, 100],   # 50이 관측됨(이웃 60, 100)
        [65, 64, 63, 100],       # 최근 3개 + 고정글만; 50과 이웃 60 미관측
        [66, 65, 64, 100],
    )
    r = detect_deletions(p)
    assert r["50"]["status"] == "censored"  # 삭제 아님
    assert r["100"]["status"] == "alive"


def test_neighbor_also_gone_is_conservative_censored():
    # X=5와 위 이웃 A=6이 함께 사라짐 → A로 브래킷 불가 → 보수적 censored(과대계상 금지).
    r = detect_deletions(_polls([7, 6, 5, 4], [7, 4], [7, 4]))
    assert r["5"]["status"] == "censored"


def test_non_adjacent_survivors_not_deletion():
    # 다음 poll에서 A=6, B=4 둘 다 있지만 사이에 다른 글 9(고정 재배치 등)가 낌 → 미확정.
    r = detect_deletions(_polls([7, 6, 5, 4], [7, 6, 9, 4], [7, 6, 9, 4]))
    assert r["5"]["status"] == "censored"


def test_newest_post_no_upper_neighbor_censored():
    # X=7이 최신(위 이웃 없음)인 채 사라짐 → 브래킷 불가 → censored.
    r = detect_deletions(_polls([7, 6, 5], [6, 5], [6, 5]))
    assert r["7"]["status"] == "censored"


def test_alive_in_latest_poll():
    r = detect_deletions(_polls([3, 2, 1], [4, 3, 2, 1]))
    for pid in ("1", "2", "3", "4"):
        assert r[pid]["status"] == "alive"


def test_empty_polls():
    assert detect_deletions([]) == {}


# ── 스냅샷 → 테이블 파생 (feed_rank/content_hash/edit/censoring) ──────────────


def _write_snap(raw, code, ids, ts, titles=None):
    from dart_event_study.toss.board import Post

    titles = titles or {}
    posts = [
        Post(post_id=str(x), ticker=code, title=titles.get(x, f"글{x}"),
             likes=0, comments=0, relative_time_label="1분", author_hash=f"h{x}")
        for x in ids
    ]
    save_snapshot(raw, code, posts, ts)


def test_save_snapshot_writes_feed_rank_and_hash(tmp_path):
    _write_snap(tmp_path, "005930", [30, 20, 10], dt.datetime(2026, 7, 15, 12))
    df = pd.read_parquet(tmp_path / "005930" / "20260715T120000.parquet")
    assert list(df["feed_rank"]) == [0, 1, 2]           # 피드 순서 = 행 순서
    assert df["content_hash"].iloc[0] == content_hash("글30")


def test_build_tables_end_to_end(tmp_path):
    code = "005930"
    _write_snap(tmp_path, code, [7, 6, 5, 4], dt.datetime(2026, 7, 15, 12))
    _write_snap(tmp_path, code, [8, 7, 6, 4], dt.datetime(2026, 7, 15, 15, 30))  # 5 삭제, 8 신규
    t = build_tables(tmp_path, code)
    posts = t["posts"].set_index("post_id")
    assert posts.loc["5", "status"] == "deleted"
    assert posts.loc["8", "status"] == "alive" and posts.loc["7", "status"] == "alive"
    # observations: poll×post, feed_rank 보존
    obs = t["observations"]
    assert len(obs) == 8  # 4 + 4
    assert set(t["polls"]["depth_reached"]) == {4}
    assert t["polls"].iloc[0]["newest_post_seen"] == "7"
    assert t["polls"].iloc[0]["oldest_post_seen"] == "4"


def test_edit_detection_via_content_hash(tmp_path):
    code = "000660"
    _write_snap(tmp_path, code, [5, 4], dt.datetime(2026, 7, 15, 12), titles={5: "원본"})
    _write_snap(tmp_path, code, [5, 4], dt.datetime(2026, 7, 15, 15), titles={5: "수정됨"})
    posts = build_tables(tmp_path, code)["posts"].set_index("post_id")
    assert bool(posts.loc["5", "edited"]) is True
    assert int(posts.loc["5", "n_content_hashes"]) == 2
    assert bool(posts.loc["4", "edited"]) is False


def test_survival_table_event_and_duration(tmp_path):
    code = "005930"
    _write_snap(tmp_path, code, [7, 6, 5, 4], dt.datetime(2026, 7, 15, 12))
    _write_snap(tmp_path, code, [7, 6, 4], dt.datetime(2026, 7, 15, 13))   # 5 삭제
    surv = build_tables(tmp_path, code)["survival"].set_index("post_id")
    assert surv.loc["5", "event"] == 1                 # 삭제 = event
    assert surv.loc["5", "duration_min"] == pytest.approx(60.0)
    assert surv.loc["7", "event"] == 0                 # 생존 = 우측절단
