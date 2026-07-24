"""토스 워치독 결정 로직 검증 (네트워크 불필요)."""

import datetime as dt

from dart_event_study.toss.board import Post
from dart_event_study.toss.store import save_snapshot
from dart_event_study.toss.watchdog import evaluate, parse_last_success


def _status(tmp_path, ts_str):
    p = tmp_path / "STATUS.txt"
    p.write_text(f"LAST_SUCCESS {ts_str}\nLAST_CRAWL {ts_str}\nRESULT OK (exit 0)\n",
                 encoding="utf-8")
    return p


def _snap(raw, code, ts, n=2):
    save_snapshot(raw, code, [Post(str(i), code, f"글{i}", 0, 0, "1분", None) for i in range(n)], ts)


def test_parse_last_success(tmp_path):
    p = _status(tmp_path, "2026-07-21 00:05:04")
    assert parse_last_success(p) == dt.datetime(2026, 7, 21, 0, 5, 4)
    assert parse_last_success(tmp_path / "missing.txt") is None


def test_parse_last_success_with_bom(tmp_path):
    # 래퍼의 PS Out-File -Encoding utf8은 BOM을 붙인다(실측) — 그래도 파싱돼야 한다.
    p = tmp_path / "STATUS.txt"
    p.write_bytes(b"\xef\xbb\xbfLAST_SUCCESS 2026-07-24 21:19:47\nRESULT OK\n")
    assert parse_last_success(p) == dt.datetime(2026, 7, 24, 21, 19, 47)


def test_ok_when_recent(tmp_path):
    codes = ["005930"]
    _snap(tmp_path, "005930", dt.datetime(2026, 7, 21, 9, 0))
    p = _status(tmp_path, "2026-07-21 09:10:00")
    d = evaluate(p, tmp_path, tmp_path / "nolog", codes, now=dt.datetime(2026, 7, 21, 11, 0))
    assert d["action"] == "ok" and d["stale_hours"] < 14


def test_stale_triggers_rerun_alert(tmp_path):
    codes = ["005930"]
    _snap(tmp_path, "005930", dt.datetime(2026, 7, 20, 9, 0))
    p = _status(tmp_path, "2026-07-20 09:10:00")
    # 20h 경과 → 임계(14h) 초과
    d = evaluate(p, tmp_path, tmp_path / "nolog", codes, now=dt.datetime(2026, 7, 21, 5, 10))
    assert d["action"] == "rerun_alert"
    assert d["stale_hours"] > 14


def test_missing_status_file_is_rerun_alert(tmp_path):
    codes = ["005930"]
    _snap(tmp_path, "005930", dt.datetime(2026, 7, 21, 9, 0))
    d = evaluate(tmp_path / "none.txt", tmp_path, tmp_path / "nolog", codes,
                 now=dt.datetime(2026, 7, 21, 9, 30))
    assert d["action"] == "rerun_alert" and d["last_success"] is None


def test_recent_slot_missing_triggers_rerun(tmp_path):
    # 최근 성공은 최근(stale 아님)이지만, 방금 도래한 슬롯이 missing → 현재 슬롯 실패 재시도
    codes = ["005930"]
    _snap(tmp_path, "005930", dt.datetime(2026, 7, 21, 9, 0))     # 09:00만 수집
    p = _status(tmp_path, "2026-07-21 09:10:00")
    # now=12:10 → 12:00 슬롯이 도래했는데 미수집(missing)
    d = evaluate(p, tmp_path, tmp_path / "nolog", codes, now=dt.datetime(2026, 7, 21, 12, 10))
    assert d["action"] == "rerun"
    assert d["recent_slot_status"] == "missing"
