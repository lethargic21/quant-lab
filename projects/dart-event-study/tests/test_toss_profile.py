"""토스 데이터 프로파일 검증 (네트워크 불필요)."""

import datetime as dt

import pandas as pd
from dart_event_study.toss.profile import (
    activity_by_stock,
    deletion_diagnostics,
    observation_quality,
    session_of,
    session_split,
)


def _posts(rows):
    return pd.DataFrame([
        {"post_id": str(i), "stock": s, "first_seen": ts, "last_seen": ts,
         "status": st, "edited": ed}
        for i, (s, ts, st, ed) in enumerate(rows)
    ])


def test_session_of_boundaries():
    assert session_of(dt.datetime(2026, 7, 20, 9, 0)) == "intraday"    # 월 09:00
    assert session_of(dt.datetime(2026, 7, 20, 15, 29)) == "intraday"  # 마감 직전
    assert session_of(dt.datetime(2026, 7, 20, 15, 30)) == "afterhours"  # 마감
    assert session_of(dt.datetime(2026, 7, 20, 21, 0)) == "afterhours"
    assert session_of(dt.datetime(2026, 7, 20, 3, 0)) == "afterhours"  # 새벽
    assert session_of(dt.datetime(2026, 7, 18, 12, 0)) == "weekend"    # 토요일


def test_activity_by_stock_rate():
    rows = [("005930", "2026-07-15T09:00:00", "alive", False)] * 3
    rows += [("000660", "2026-07-16T09:00:00", "alive", True)]
    rows += [("005930", "2026-07-17T09:00:00", "alive", False)]
    act = activity_by_stock(_posts(rows))
    a = act.set_index("stock")
    assert a.loc["005930", "posts"] == 4
    assert a.loc["000660", "edited"] == 1
    assert (act["posts_per_day"] > 0).all()


def test_session_split_shares():
    rows = [
        ("005930", "2026-07-20T10:00:00", "alive", False),  # 장중(월)
        ("005930", "2026-07-20T21:00:00", "alive", False),  # 장후
        ("005930", "2026-07-18T12:00:00", "alive", False),  # 주말(토)
    ]
    s = session_split(_posts(rows)).set_index("stock")
    assert s.loc["005930", "intraday"] == 1
    assert s.loc["005930", "afterhours"] == 1
    assert s.loc["005930", "weekend"] == 1
    assert s.loc["005930", "intraday_share"] == round(1 / 3, 3)


def test_deletion_diagnostics_counts():
    rows = [
        ("005930", "2026-07-20T10:00:00", "deleted", False),
        ("005930", "2026-07-20T11:00:00", "censored", False),
        ("005930", "2026-07-20T12:00:00", "censored", False),
        ("005930", "2026-07-20T13:00:00", "alive", False),
    ]
    polls = pd.DataFrame([{"poll_id": "p1", "stock": "005930", "depth_reached": 50}])
    d = deletion_diagnostics(_posts(rows), polls).set_index("stock")
    assert d.loc["005930", "deleted"] == 1
    assert d.loc["005930", "censored"] == 2
    assert d.loc["005930", "censored_share"] == 0.5
    assert d.loc["005930", "depth_mean"] == 50


def test_observation_quality():
    obs = pd.DataFrame([
        {"poll_id": "p1", "post_id": "a", "observed_at": "t"},
        {"poll_id": "p2", "post_id": "a", "observed_at": "t"},
        {"poll_id": "p1", "post_id": "b", "observed_at": "t"},
    ])
    polls = pd.DataFrame([{"poll_id": "p1"}, {"poll_id": "p2"}])
    q = observation_quality(obs, polls)
    assert q["polls"] == 2 and q["unique_posts"] == 2
    assert q["reobs_mean"] == 1.5
    assert q["reobs_once_share"] == 0.5  # b는 1회만


def test_empty_inputs_safe():
    assert activity_by_stock(pd.DataFrame()).empty
    assert observation_quality(pd.DataFrame(), pd.DataFrame()) == {}
