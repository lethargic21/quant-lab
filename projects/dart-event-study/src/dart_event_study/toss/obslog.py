"""토스 크롤 관측가능성 — 구조화 로그 + 하드kill 포렌식 (Phase 1).

목적: 15:30 하드kill처럼 **잡을 수 없는 강제종료**의 흔적을 남긴다. TerminateProcess는
signal도 atexit도 못 잡지만, 매 단계 직전에 fsync한 **하트비트 파일**이 "어느 종목·어느
단계에서 멈췄는지"를 디스크에 박아둔다. 이것이 다음 발생을 진단할 유일한 증거다.

산출:
- data/toss_logs/runs/run_{ts}.jsonl — 이벤트 스트림(run_start / ticker_* / signal / run_end),
  각 줄 fsync (강제종료 시 버퍼 유실 방지). 오래된 런 로그는 회전.
- data/toss_logs/PROGRESS.txt — 마지막 상태 한 줄(덮어쓰기 + fsync). 하드kill 시 마지막
  성공 단계가 여기 남는다.
- data/toss_logs/runs/last_run_summary.json — 종료 시 요약(정상 종료 경로에서만).

라이브 크롤 동작은 안 바꾼다 — 로깅만 얹는다(관측만, 부작용 없음).
"""

from __future__ import annotations

import atexit
import datetime as dt
import json
import os
import signal
import time
from pathlib import Path


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def slot_of(ts: dt.datetime) -> str:
    """크롤 시각 → 가장 가까운 스케줄 슬롯 라벨(09:00/12:00/15:30/21:00 또는 off)."""
    h = ts.hour + ts.minute / 60
    if 8 <= h < 10.5:
        return "09:00"
    if 10.5 <= h < 13.5:
        return "12:00"
    if 13.5 <= h < 18:
        return "15:30"
    if 18 <= h < 24:
        return "21:00"
    return f"off_{ts:%H%M}"


class RunLogger:
    """한 크롤 실행의 구조화 로그 + 하트비트. 강제종료에 강건(줄마다 fsync)."""

    def __init__(self, log_dir: Path, run_ts: dt.datetime, n_tickers: int, keep: int = 300,
                 install_handlers: bool = True):
        self.runs_dir = log_dir / "runs"
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.runs_dir / f"run_{run_ts:%Y%m%dT%H%M%S}.jsonl"
        self.progress = log_dir / "PROGRESS.txt"
        self.run_ts = run_ts
        self.slot = slot_of(run_ts)
        self.pid = os.getpid()
        self._t0 = time.monotonic()
        self._fh = self.path.open("a", encoding="utf-8")
        self._closed = False
        self._rotate(keep)
        if install_handlers:  # 테스트에선 끔(pytest의 SIGINT 핸들러를 뺏지 않게)
            self._install_handlers()
        self.event("run_start", slot=self.slot, n_tickers=n_tickers, pid=self.pid)
        self.heartbeat("run_start")

    # ── 이벤트 스트림 ────────────────────────────────────────────────────────
    def event(self, kind: str, **fields) -> None:
        rec = {"ts": _now(), "elapsed_s": round(time.monotonic() - self._t0, 1),
               "kind": kind, **fields}
        self._fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self._fh.flush()
        os.fsync(self._fh.fileno())  # 강제종료 대비 — 버퍼 유실 방지

    def heartbeat(self, phase: str, ticker: str | None = None, **extra) -> None:
        """마지막 상태를 PROGRESS.txt에 원자적으로 덮어쓴다(하드kill 시 여기가 증거)."""
        line = (f"{_now()} pid={self.pid} slot={self.slot} "
                f"elapsed={round(time.monotonic() - self._t0, 1)}s phase={phase} ticker={ticker}")
        for k, v in extra.items():
            line += f" {k}={v}"
        tmp = self.progress.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.progress)  # 원자적 교체

    # ── 종목 단위 ────────────────────────────────────────────────────────────
    def ticker_start(self, i: int, code: str, name: str) -> float:
        self.event("ticker_start", i=i, code=code, name=name)
        self.heartbeat("ticker_start", ticker=code, i=i)
        return time.monotonic()

    def ticker_phase(self, code: str, phase: str, **extra) -> None:
        # 종목 내 세부 단계(goto/scroll/parse) — 어느 단계에서 멈췄나 구분(가설 a vs f)
        self.heartbeat(phase, ticker=code, **extra)

    def ticker_end(self, code: str, status: str, t_start: float, **fields) -> None:
        self.event("ticker_end", code=code, status=status,
                   duration_s=round(time.monotonic() - t_start, 1), **fields)

    # ── 종료 ─────────────────────────────────────────────────────────────────
    def close(self, exit_code: int, n_ok: int, n_fail: int) -> None:
        if self._closed:
            return
        self._closed = True
        self.event("run_end", exit_code=exit_code, n_ok=n_ok, n_fail=n_fail,
                   total_s=round(time.monotonic() - self._t0, 1))
        self.heartbeat("run_end", ok=n_ok, fail=n_fail, exit=exit_code)
        (self.runs_dir / "last_run_summary.json").write_text(
            json.dumps({"run_ts": self.run_ts.isoformat(), "slot": self.slot,
                        "exit_code": exit_code, "n_ok": n_ok, "n_fail": n_fail,
                        "total_s": round(time.monotonic() - self._t0, 1)},
                       ensure_ascii=False), encoding="utf-8")
        try:
            self._fh.close()
        except Exception:
            pass

    # ── 강제종료 흔적 ────────────────────────────────────────────────────────
    def _install_handlers(self) -> None:
        def _handler(signum, _frame):
            try:
                self.event("signal", signum=int(signum), name=signal.Signals(signum).name)
                self.heartbeat(f"signal_{signal.Signals(signum).name}")
            finally:
                # 기본 동작으로 되돌려 실제 종료가 일어나게
                signal.signal(signum, signal.SIG_DFL)
                os.kill(self.pid, signum)

        for name in ("SIGINT", "SIGTERM", "SIGBREAK"):  # SIGBREAK는 Windows 전용
            sig = getattr(signal, name, None)
            if sig is not None:
                try:
                    signal.signal(sig, _handler)
                except (ValueError, OSError):
                    pass  # 메인 스레드 아님 등 — 무시
        atexit.register(self._atexit)

    def _atexit(self) -> None:
        # 정상/예외 종료 경로. 이미 close 했으면 no-op. 아니면 비정상 종료 흔적.
        if not self._closed:
            try:
                self.event("atexit_without_close", note="비정상 종료 추정")
                self.heartbeat("atexit_without_close")
                self._fh.close()
            except Exception:
                pass

    # ── 로그 회전 ────────────────────────────────────────────────────────────
    def _rotate(self, keep: int) -> None:
        runs = sorted(self.runs_dir.glob("run_*.jsonl"))
        for old in runs[:-keep] if len(runs) > keep else []:
            try:
                old.unlink()
            except OSError:
                pass
