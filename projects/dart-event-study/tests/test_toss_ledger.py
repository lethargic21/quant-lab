"""토스 커버리지 원장·갭 감지 검증 (네트워크 불필요)."""

import datetime as dt

import pandas as pd
from dart_event_study.toss.board import Post
from dart_event_study.toss.ledger import (
    build_ledger,
    detect_gaps,
    expected_slots,
    missing_ticker_slots,
    snapshot_coverage,
)
from dart_event_study.toss.store import save_snapshot


def _snap(raw, code, ts, n=2):
    posts = [Post(str(i), code, f"글{i}", 0, 0, "1분", None) for i in range(n)]
    save_snapshot(raw, code, posts, ts)


def test_expected_slots_enumerates_four_per_day():
    slots = expected_slots(dt.date(2026, 7, 15), dt.date(2026, 7, 16))
    assert len(slots) == 8
    assert (dt.date(2026, 7, 15), "09:00") in slots
    assert (dt.date(2026, 7, 16), "21:00") in slots


def test_snapshot_coverage_maps_slot(tmp_path):
    _snap(tmp_path, "005930", dt.datetime(2026, 7, 20, 15, 30, 5))
    _snap(tmp_path, "000660", dt.datetime(2026, 7, 20, 12, 0, 3))
    cov = snapshot_coverage(tmp_path, ["005930", "000660"])
    assert set(cov["slot"]) == {"15:30", "12:00"}


def test_ledger_ok_partial_missing(tmp_path):
    codes = ["005930", "000660", "005380"]
    day = dt.datetime(2026, 7, 20)
    # 09:00 전 종목(ok), 12:00 2/3(partial), 15:30/21:00 없음(missing)
    for c in codes:
        _snap(tmp_path, c, day.replace(hour=9))
    _snap(tmp_path, "005930", day.replace(hour=12))
    _snap(tmp_path, "000660", day.replace(hour=12))

    led = build_ledger(tmp_path, tmp_path / "nolog", codes)
    by = {(r["date"], r["slot"]): r for _, r in led.iterrows()}
    assert by[("2026-07-20", "09:00")]["status"] == "ok"
    assert by[("2026-07-20", "12:00")]["status"] == "partial"
    assert by[("2026-07-20", "12:00")]["n_tickers"] == 2
    assert by[("2026-07-20", "15:30")]["status"] == "missing"
    assert by[("2026-07-20", "21:00")]["status"] == "missing"


def test_ledger_excludes_future_and_prestart(tmp_path):
    codes = ["005930"]
    _snap(tmp_path, "005930", dt.datetime(2026, 7, 20, 15, 30))  # 첫 크롤
    _snap(tmp_path, "005930", dt.datetime(2026, 7, 21, 9, 0))
    led = build_ledger(tmp_path, tmp_path / "nolog", codes, now=dt.datetime(2026, 7, 21, 10, 0))
    slots = {(r["date"], r["slot"]) for _, r in led.iterrows()}
    assert ("2026-07-20", "09:00") not in slots   # 첫 크롤 이전 = 수집전
    assert ("2026-07-20", "12:00") not in slots
    assert ("2026-07-20", "15:30") in slots        # 첫 크롤
    assert ("2026-07-20", "21:00") in slots        # 실제 놓침
    assert ("2026-07-21", "09:00") in slots        # ok
    assert ("2026-07-21", "12:00") not in slots    # now=10:00 → 미래 = 대기
    assert ("2026-07-21", "21:00") not in slots


def test_detect_gaps_excludes_ok(tmp_path):
    codes = ["005930", "000660"]
    day = dt.datetime(2026, 7, 20, 9)
    for c in codes:
        _snap(tmp_path, c, day)
    led = build_ledger(tmp_path, tmp_path / "nolog", codes)
    gaps = detect_gaps(led)
    assert (gaps["status"] == "ok").sum() == 0
    assert (gaps["slot"] == "09:00").sum() == 0  # ok 슬롯은 빠짐
    assert len(gaps) == len(led) - 1             # 09:00만 ok


def test_missing_ticker_slots_lists_absent(tmp_path):
    codes = ["005930", "000660", "005380"]
    day = dt.datetime(2026, 7, 20, 12)
    _snap(tmp_path, "005930", day)
    _snap(tmp_path, "005380", day)  # 000660 빠짐
    m = missing_ticker_slots(tmp_path, codes)
    row = m[m["slot"] == "12:00"].iloc[0]
    assert row["missing_tickers"] == "000660" and row["n_missing"] == 1


def test_ledger_enriched_with_run_log(tmp_path):
    codes = ["005930"]
    ts = dt.datetime(2026, 7, 20, 15, 30, 5)
    _snap(tmp_path, "005930", ts)
    # Phase 1 run 로그 위조 (run_end 있음 = 정상 종료)
    runs = tmp_path / "toss_logs" / "runs"
    runs.mkdir(parents=True)
    import json
    lines = [
        {"kind": "run_start", "slot": "15:30"},
        {"kind": "ticker_end", "code": "005930", "status": "ok",
         "status_counts": {"200": 300, "490": 1}},
        {"kind": "run_end", "exit_code": 0, "total_s": 400.0},
    ]
    (runs / "run_20260720T153005.jsonl").write_text(
        "\n".join(json.dumps(x) for x in lines), encoding="utf-8")

    led = build_ledger(tmp_path, tmp_path / "toss_logs", codes)
    row = led[(led["date"] == "2026-07-20") & (led["slot"] == "15:30")].iloc[0]
    assert row["status"] == "ok"
    assert row["run_exit"] == 0
    assert bool(row["run_clean_end"]) is True
    assert row["http_490_total"] == 1


def test_empty_returns_empty(tmp_path):
    assert build_ledger(tmp_path, tmp_path, ["005930"]).empty
    assert isinstance(snapshot_coverage(tmp_path, ["005930"]), pd.DataFrame)
