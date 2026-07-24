"""토스 수집 커버리지 원장 + 갭 감지 (Phase 2).

실행:  uv run python -m dart_event_study.toss.ledger

계획된 슬롯(09:00/12:00/15:30/21:00 매일)을 **실제 스냅샷**과 대조해 (종목×슬롯) 커버리지를
원장으로 만든다. 순방향 수집이라 놓친 슬롯은 영구 손실 — 무엇을·언제 놓쳤는지 눈에 보이게.

원장은 스냅샷(불변·전 기간 존재)을 1차 근거로, Phase 1 run 로그(실측 시작/종료·종료코드·
HTTP 분포)가 있으면 보강한다. 상태: ok(전 종목) / partial(일부) / missing(전무).

라이브 크롤 무관 — 관측 산물에서 파생만. 재실행 idempotent.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pandas as pd
import yaml

from dart_event_study.config import CONFIG_DIR, DATA_DIR
from dart_event_study.toss.obslog import slot_of

RAW_DIR = DATA_DIR / "raw" / "toss"
LOG_DIR = DATA_DIR / "toss_logs"
OUT_DIR = DATA_DIR / "processed"
SLOTS = ["09:00", "12:00", "15:30", "21:00"]
SLOT_TIME = {"09:00": (9, 0), "12:00": (12, 0), "15:30": (15, 30), "21:00": (21, 0)}


def load_universe() -> dict[str, str]:
    cfg = yaml.safe_load((CONFIG_DIR / "toss_universe.yaml").read_text(encoding="utf-8"))
    return {**cfg["compare"], **cfg["expand"]}


def snapshot_coverage(raw_dir: Path, codes: list[str]) -> pd.DataFrame:
    """스냅샷에서 (ticker, crawl_ts, date, slot) 관측 목록. 빈 스냅샷도 '관측함'으로 센다."""
    rows = []
    for code in codes:
        d = raw_dir / code
        if not d.exists():
            continue
        for f in d.glob("*.parquet"):
            if f.stem == "_cumulative":
                continue
            try:
                ts = dt.datetime.strptime(f.stem, "%Y%m%dT%H%M%S")
            except ValueError:
                continue
            rows.append({"ticker": code, "crawl_ts": ts,
                         "date": ts.date(), "slot": slot_of(ts)})
    return pd.DataFrame(rows)


def expected_slots(start: dt.date, end: dt.date) -> list[tuple[dt.date, str]]:
    """수집 시작일~종료일의 모든 (날짜, 슬롯). 시작일은 첫 관측 슬롯부터."""
    out = []
    day = start
    while day <= end:
        for s in SLOTS:
            out.append((day, s))
        day += dt.timedelta(days=1)
    return out


def run_log_index(log_dir: Path) -> dict[tuple[dt.date, str], dict]:
    """Phase 1 run 로그를 (date, slot)→요약으로. 실측 시작/종료·exit·HTTP490 보강용."""
    idx: dict[tuple[dt.date, str], dict] = {}
    runs = log_dir / "runs"
    if not runs.exists():
        return idx
    for f in runs.glob("run_*.jsonl"):
        try:
            events = [json.loads(ln) for ln in f.read_text(encoding="utf-8").splitlines()]
        except (OSError, json.JSONDecodeError):
            continue
        start = next((e for e in events if e["kind"] == "run_start"), None)
        end = next((e for e in events if e["kind"] == "run_end"), None)
        if not start:
            continue
        ts = dt.datetime.strptime(f.stem[len("run_"):], "%Y%m%dT%H%M%S")
        key = (ts.date(), slot_of(ts))
        http490 = sum(te.get("status_counts", {}).get("490", 0)
                      for te in events if te["kind"] == "ticker_end")
        idx[key] = {
            "run_start_ts": ts.isoformat(),
            "run_exit": end["exit_code"] if end else None,
            "run_total_s": end.get("total_s") if end else None,
            "run_clean_end": end is not None,   # run_end 없으면 비정상 종료(하드kill 흔적)
            "http_490_total": http490,
        }
    return idx


def build_ledger(
    raw_dir: Path, log_dir: Path, codes: list[str], now: dt.datetime | None = None
) -> pd.DataFrame:
    """(날짜×슬롯) 원장: 계획시각·실제·수집종목수·상태 + run 로그 보강.

    수집 시작 전(첫 크롤 이전) 슬롯과 아직 도래하지 않은 미래 슬롯은 제외한다 —
    '놓친 것'이 아니라 각각 수집전·대기 상태이므로 missing으로 세면 안 된다.
    """
    now = now or dt.datetime.now()
    cov = snapshot_coverage(raw_dir, codes)
    total = len(codes)
    if cov.empty:
        return pd.DataFrame()
    start, end = cov["date"].min(), cov["date"].max()
    grp = cov.groupby(["date", "slot"]).agg(
        n_tickers=("ticker", "nunique"),
        n_crawls=("crawl_ts", "nunique"),
        first_ts=("crawl_ts", "min"),
        last_ts=("crawl_ts", "max"),
    ).reset_index()
    key = {(r["date"], r["slot"]): r for _, r in grp.iterrows()}
    rlog = run_log_index(log_dir)

    # 수집 개시 기준점 = 실제 커버된 **정규 슬롯** 중 가장 이른 계획시각
    # (off_* = 수동/지각 크롤은 정규 슬롯이 아니라 anchor·기대격자에서 제외)
    def _planned(day: dt.date, slot: str) -> dt.datetime:
        hh, mm = SLOT_TIME[slot]
        return dt.datetime.combine(day, dt.time(hh, mm))

    canonical = [_planned(d, s) for (d, s) in key if s in SLOT_TIME]
    if not canonical:
        return pd.DataFrame()  # 정규 슬롯 커버리지 전무 (수동 크롤만 있음)
    anchor = min(canonical)

    rows = []
    for day, slot in expected_slots(start, end):
        planned = _planned(day, slot)
        if planned < anchor or planned > now:  # 수집전·미래 슬롯 제외
            continue
        a = key.get((day, slot))
        n = int(a["n_tickers"]) if a is not None else 0
        status = "missing" if n == 0 else ("ok" if n >= total else "partial")
        row = {
            "date": day.isoformat(), "slot": slot, "planned": planned.isoformat(),
            "n_tickers": n, "total": total, "status": status,
            "n_crawls": int(a["n_crawls"]) if a is not None else 0,
            "actual_first": a["first_ts"].isoformat() if a is not None else None,
        }
        row |= {k: None for k in ("run_exit", "run_total_s", "run_clean_end", "http_490_total")}
        row |= rlog.get((day, slot), {})
        rows.append(row)
    return pd.DataFrame(rows)


def detect_gaps(ledger: pd.DataFrame) -> pd.DataFrame:
    """missing/partial 슬롯만. 순방향 손실 구간 목록 (오래된 것부터)."""
    if ledger.empty:
        return ledger
    return ledger[ledger["status"] != "ok"].copy().reset_index(drop=True)


def missing_ticker_slots(raw_dir: Path, codes: list[str]) -> pd.DataFrame:
    """부분수집 슬롯에서 어느 종목이 빠졌는지 (세밀 갭). 빈 커뮤니티는 관측으로 침."""
    cov = snapshot_coverage(raw_dir, codes)
    if cov.empty:
        return pd.DataFrame(columns=["date", "slot", "missing_tickers"])
    rows = []
    for (day, slot), g in cov.groupby(["date", "slot"]):
        seen = set(g["ticker"])
        missing = [c for c in codes if c not in seen]
        if missing:
            rows.append({"date": day.isoformat(), "slot": slot,
                         "n_missing": len(missing), "missing_tickers": ",".join(missing)})
    return pd.DataFrame(rows)


def main() -> None:
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    codes = list(load_universe())
    ledger = build_ledger(RAW_DIR, LOG_DIR, codes)
    if ledger.empty:
        print("스냅샷 없음 — 원장 생성 불가")
        return
    gaps = detect_gaps(ledger)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ledger.to_parquet(OUT_DIR / "toss_coverage_ledger.parquet")
    gaps.to_parquet(OUT_DIR / "toss_coverage_gaps.parquet")

    vc = ledger["status"].value_counts().to_dict()
    print(f"커버리지 원장: 계획 슬롯 {len(ledger)}개 "
          f"(ok {vc.get('ok', 0)} / partial {vc.get('partial', 0)} / missing {vc.get('missing', 0)})")
    print(f"저장: {OUT_DIR / 'toss_coverage_ledger.parquet'}\n")
    print("갭(누락·부분수집) 슬롯:")
    cols = ["date", "slot", "n_tickers", "total", "status", "run_clean_end", "http_490_total"]
    print(gaps[cols].to_string(index=False) if not gaps.empty else "  없음 — 전 슬롯 완전 수집")
    # 최근 상태 요약(워치독 참고)
    latest = ledger.iloc[-1]
    print(f"\n최근 슬롯: {latest['date']} {latest['slot']} → {latest['status']} "
          f"({latest['n_tickers']}/{latest['total']})")


if __name__ == "__main__":
    main()
