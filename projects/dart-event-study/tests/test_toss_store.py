"""토스 누적 테이블 검증 (네트워크 불필요).

핵심: **삭제 판정은 현재 비활성화(보류)**다. 토스 post_id가 시간순이 아니고 매 크롤이
id-공간에서 성기게 관측하므로, hit_stop 게이트 + 스캔범위 경계로도 대량 오탐이 났다
(실측 4,793건). 정확한 탐지기(피드 순서 기반)는 별도 태스크. 그때까지 is_deleted는 항상 False.
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


def test_deletion_disabled_hit_stop_true(tmp_path):
    """hit_stop=True로 스캔 범위 안에서 글이 사라져도 삭제로 찍지 않는다 (탐지 비활성)."""
    ts1 = dt.datetime(2026, 7, 15, 12, 0, 0)
    ts2 = dt.datetime(2026, 7, 15, 15, 30, 0)
    update_cumulative(tmp_path, "005930", _posts([100, 99, 98, 97]), ts1, hit_stop=True)
    # 99·98이 범위 안인데 사라짐 → 예전 로직이면 삭제. 지금은 비활성 → 미삭제.
    summ = update_cumulative(tmp_path, "005930", _posts([100, 97]), ts2, hit_stop=True)
    cum = _cum(tmp_path)
    assert not cum["is_deleted"].fillna(False).any()
    assert summ["deleted_this_crawl"] == 0


def test_deletion_disabled_hit_stop_false(tmp_path):
    """hit_stop=False에서도 당연히 삭제로 찍지 않는다."""
    ts1 = dt.datetime(2026, 7, 15, 12, 0, 0)
    ts2 = dt.datetime(2026, 7, 15, 15, 30, 0)
    update_cumulative(tmp_path, "005930", _posts([100, 99, 98, 97]), ts1, hit_stop=False)
    summ = update_cumulative(tmp_path, "005930", _posts([100]), ts2, hit_stop=False)
    cum = _cum(tmp_path)
    assert not cum["is_deleted"].fillna(False).any()
    assert summ["deleted_this_crawl"] == 0


def test_new_and_update_tracked(tmp_path):
    """신규 글은 first_seen 확립, 재관측 글은 last_seen/좋아요 갱신 (핵심 기능 유지)."""
    ts1 = dt.datetime(2026, 7, 15, 12, 0, 0)
    ts2 = dt.datetime(2026, 7, 15, 15, 30, 0)
    def _p(pid, title, likes, rel):
        return Post(pid, "005930", title, likes=likes, comments=0,
                    relative_time_label=rel, author_hash=None)

    update_cumulative(tmp_path, "005930", [_p("100", "글", 1, "1분")], ts1, hit_stop=True)
    # 신규 101 추가 + 100 좋아요 갱신
    summ = update_cumulative(
        tmp_path, "005930",
        [_p("100", "글", 5, "3시간"), _p("101", "새글", 0, "방금")],
        ts2, hit_stop=True,
    )
    cum = _cum(tmp_path)
    assert summ["new"] == 1 and summ["observed"] == 2
    assert cum.loc["100", "first_seen_at"] == ts1.isoformat()  # first_seen 불변
    assert cum.loc["100", "last_seen_at"] == ts2.isoformat()   # last_seen 갱신
    assert int(cum.loc["100", "likes"]) == 5                    # 좋아요 갱신
    assert cum.loc["101", "first_seen_at"] == ts2.isoformat()  # 신규 first_seen
