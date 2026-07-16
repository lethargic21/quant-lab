"""토스 누적 테이블 삭제 판정 검증 (네트워크 불필요).

핵심 회귀 방지 두 가지:
1. hit_stop=False(관측 윈도우가 이전 피드에 못 닿음)면 사라진 글을 삭제로 찍지 않는다.
2. hit_stop=True라도, 스크롤이 상단만 봤을 때 하단(과거) 글은 삭제로 오판되지 않는다.
"""

import datetime as dt

import pandas as pd
from dart_event_study.toss.board import Post
from dart_event_study.toss.store import update_cumulative


def _posts(ids: list[int], ticker: str = "005930") -> list[Post]:
    return [
        Post(post_id=str(i), ticker=ticker, title=f"글 {i}", likes=0, comments=0,
             relative_time_label="1분", author_hash=None)
        for i in ids
    ]


def _cum(tmp_path, ticker="005930"):
    return pd.read_parquet(tmp_path / ticker / "_cumulative.parquet").set_index("post_id")


def test_no_hit_stop_marks_nothing_deleted(tmp_path):
    """hit_stop=False면 사라진 글이 있어도 삭제로 찍지 않는다 (윈도우 미도달 → 판단 불가)."""
    ts1 = dt.datetime(2026, 7, 15, 12, 0, 0)
    ts2 = dt.datetime(2026, 7, 15, 15, 30, 0)
    update_cumulative(tmp_path, "005930", _posts([100, 99, 98, 97]), ts1, hit_stop=False)
    # 크롤2: 99·98·97 안 보이지만 hit_stop=False → 삭제 판정 안 함
    summ = update_cumulative(tmp_path, "005930", _posts([100]), ts2, hit_stop=False)
    cum = _cum(tmp_path)
    assert not cum["is_deleted"].fillna(False).any()
    assert summ["deleted_this_crawl"] == 0


def test_below_scan_floor_not_marked_deleted(tmp_path):
    """hit_stop=True라도 스캔 범위 아래(과거) 글은 삭제로 오판되지 않는다 — 핵심 버그 회귀."""
    ts1 = dt.datetime(2026, 7, 15, 12, 0, 0)
    ts2 = dt.datetime(2026, 7, 15, 15, 30, 0)
    update_cumulative(tmp_path, "005930", _posts([100, 99, 98, 97]), ts1, hit_stop=True)
    # 크롤2: 조기종료로 상단 2글(100,99)만 관측 → 스캔 최하단=99. 98·97은 그 아래.
    summ = update_cumulative(tmp_path, "005930", _posts([100, 99]), ts2, hit_stop=True)
    cum = _cum(tmp_path)
    assert not bool(cum.loc["98", "is_deleted"])  # 스캔 범위 밖 → 삭제 아님
    assert not bool(cum.loc["97", "is_deleted"])
    assert not bool(cum.loc["100", "is_deleted"])
    assert not bool(cum.loc["99", "is_deleted"])
    assert summ["deleted_this_crawl"] == 0


def test_in_range_missing_marked_deleted(tmp_path):
    """hit_stop=True + 스캔 범위 안에서 사라진 글은 정상적으로 삭제로 잡힌다 (기능 유지)."""
    ts1 = dt.datetime(2026, 7, 15, 12, 0, 0)
    ts2 = dt.datetime(2026, 7, 15, 15, 30, 0)
    update_cumulative(tmp_path, "005930", _posts([100, 99, 98, 97]), ts1, hit_stop=True)
    # 크롤2: 100·98·97 관측(스캔 최하단=97). 99는 범위 안인데 사라짐 → 삭제.
    summ = update_cumulative(tmp_path, "005930", _posts([100, 98, 97]), ts2, hit_stop=True)
    cum = _cum(tmp_path)
    assert bool(cum.loc["99", "is_deleted"])
    assert cum.loc["99", "deleted_detected_at"] == ts2.isoformat()
    assert not bool(cum.loc["100", "is_deleted"])
    assert summ["deleted_this_crawl"] == 1


def test_deleted_then_reappears_resurrects(tmp_path):
    """삭제 판정됐다가 다시 관측되면 되살아난다."""
    ts1 = dt.datetime(2026, 7, 15, 12, 0, 0)
    ts2 = dt.datetime(2026, 7, 15, 15, 30, 0)
    ts3 = dt.datetime(2026, 7, 15, 21, 0, 0)
    update_cumulative(tmp_path, "005930", _posts([100, 99, 98]), ts1, hit_stop=True)
    update_cumulative(tmp_path, "005930", _posts([100, 98]), ts2, hit_stop=True)  # 99 삭제
    assert bool(_cum(tmp_path).loc["99", "is_deleted"])
    update_cumulative(tmp_path, "005930", _posts([100, 99, 98]), ts3, hit_stop=True)  # 99 재등장
    cum3 = _cum(tmp_path)
    assert not bool(cum3.loc["99", "is_deleted"])
    assert pd.isna(cum3.loc["99", "deleted_detected_at"])  # None → parquet 왕복 시 NaN
