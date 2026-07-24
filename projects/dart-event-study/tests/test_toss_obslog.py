"""토스 관측가능성 로거 검증 (네트워크 불필요).

핵심: 하드kill 포렌식 — PROGRESS.txt가 항상 마지막 단계를 담고, run 로그가 순서대로
쌓이며, 회전이 동작한다. signal 핸들러는 테스트에서 끈다(pytest 방해 방지).
"""

import datetime as dt
import json

from dart_event_study.toss.obslog import RunLogger, slot_of


def _read_events(logger):
    return [json.loads(ln) for ln in logger.path.read_text(encoding="utf-8").splitlines()]


def test_slot_classification():
    assert slot_of(dt.datetime(2026, 7, 20, 9, 0)) == "09:00"
    assert slot_of(dt.datetime(2026, 7, 20, 12, 0)) == "12:00"
    assert slot_of(dt.datetime(2026, 7, 20, 15, 30)) == "15:30"
    assert slot_of(dt.datetime(2026, 7, 20, 21, 0)) == "21:00"
    assert slot_of(dt.datetime(2026, 7, 20, 3, 0)).startswith("off")


def test_run_lifecycle_events_in_order(tmp_path):
    ts = dt.datetime(2026, 7, 20, 15, 30, 5)
    log = RunLogger(tmp_path, ts, n_tickers=2, install_handlers=False)
    t0 = log.ticker_start(0, "005930", "삼성전자")
    log.ticker_end("005930", "ok", t0, observed=10, new=5)
    t1 = log.ticker_start(1, "000660", "SK하이닉스")
    log.ticker_end("000660", "sort_fail", t1, n_posts=11)
    log.close(exit_code=1, n_ok=1, n_fail=1)

    kinds = [e["kind"] for e in _read_events(log)]
    assert kinds == ["run_start", "ticker_start", "ticker_end",
                     "ticker_start", "ticker_end", "run_end"]
    ev = _read_events(log)
    assert ev[0]["slot"] == "15:30" and ev[0]["n_tickers"] == 2
    assert ev[2]["status"] == "ok" and ev[2]["observed"] == 10 and "duration_s" in ev[2]
    assert ev[4]["status"] == "sort_fail"
    assert ev[-1] == {**ev[-1], "kind": "run_end", "exit_code": 1, "n_ok": 1, "n_fail": 1}


def test_progress_holds_last_phase(tmp_path):
    ts = dt.datetime(2026, 7, 20, 15, 30, 5)
    log = RunLogger(tmp_path, ts, n_tickers=1, install_handlers=False)
    log.ticker_start(0, "005930", "삼성전자")
    log.ticker_phase("005930", "scroll7")
    prog = (tmp_path / "PROGRESS.txt").read_text(encoding="utf-8")
    assert "phase=scroll7" in prog and "ticker=005930" in prog and f"pid={log.pid}" in prog


def test_summary_written_on_close(tmp_path):
    ts = dt.datetime(2026, 7, 20, 21, 0, 0)
    log = RunLogger(tmp_path, ts, n_tickers=1, install_handlers=False)
    log.close(exit_code=0, n_ok=1, n_fail=0)
    summ = json.loads((tmp_path / "runs" / "last_run_summary.json").read_text(encoding="utf-8"))
    assert summ["exit_code"] == 0 and summ["slot"] == "21:00" and summ["n_ok"] == 1


def test_atexit_without_close_leaves_trace(tmp_path):
    ts = dt.datetime(2026, 7, 20, 15, 30, 5)
    log = RunLogger(tmp_path, ts, n_tickers=1, install_handlers=False)
    log.ticker_start(0, "005930", "삼성전자")
    log._atexit()  # close() 없이 종료된 상황 재현
    kinds = [e["kind"] for e in _read_events(log)]
    assert "atexit_without_close" in kinds
    assert "atexit_without_close" in (tmp_path / "PROGRESS.txt").read_text(encoding="utf-8")


def test_close_is_idempotent(tmp_path):
    log = RunLogger(tmp_path, dt.datetime(2026, 7, 20, 9, 0), n_tickers=1, install_handlers=False)
    log.close(exit_code=0, n_ok=1, n_fail=0)
    log.close(exit_code=0, n_ok=1, n_fail=0)  # 두 번째는 no-op
    assert [e["kind"] for e in _read_events(log)].count("run_end") == 1


def test_log_rotation_keeps_last_n(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    for i in range(5):  # 오래된 런 로그 5개 미리 생성
        (runs / f"run_2026010{i}T090000.jsonl").write_text("{}", encoding="utf-8")
    # keep=3 → 새 로그 생성 시 오래된 것 정리해 총 3개 유지
    RunLogger(tmp_path, dt.datetime(2026, 2, 1, 9, 0), n_tickers=1, keep=3, install_handlers=False)
    remaining = sorted(runs.glob("run_*.jsonl"))
    assert len(remaining) == 3
    assert remaining[-1].name == "run_20260201T090000.jsonl"  # 새 것은 남음
