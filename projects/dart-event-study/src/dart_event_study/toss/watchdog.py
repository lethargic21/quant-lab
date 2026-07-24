"""토스 수집 워치독 — 원장/하트비트 점검 → 재실행·알림 결정 (Phase 2).

실행:  uv run python -m dart_event_study.toss.watchdog

별도 스케줄 태스크가 주기적으로 이걸 돌려, 수집이 조용히 멈췄는지 판단한다. 판단은
데이터 도착 기준(STATUS.txt의 LAST_SUCCESS) + 원장의 최근 슬롯 상태. 결정을 JSON으로
내보내고, 재실행이 필요하면 WATCHDOG_ALERT.txt를 남긴다. **실제 재실행·토스트는 PS
래퍼(scripts/toss_watchdog_run.ps1)가 이 결정을 읽고 수행** — 재실행은 idempotent
(update_cumulative가 post_id upsert, save_snapshot은 새 타임스탬프 파일).

임계값: LAST_SUCCESS가 max_gap_hours(기본 14h — 21:00→09:00 야간 12h + 여유)보다 오래면
'수집 멈춤 의심' → rerun_alert. 최근 도래 슬롯이 missing이면(현재 슬롯 실패) → rerun.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

from dart_event_study.config import CONFIG_DIR, DATA_DIR

LOG_DIR = DATA_DIR / "toss_logs"
RAW_DIR = DATA_DIR / "raw" / "toss"
STATUS_PATH = LOG_DIR / "STATUS.txt"
ALERT_PATH = LOG_DIR / "WATCHDOG_ALERT.txt"
MAX_GAP_HOURS = 14.0


def parse_last_success(status_path: Path) -> dt.datetime | None:
    """STATUS.txt의 'LAST_SUCCESS YYYY-MM-DD HH:MM:SS' 파싱.

    래퍼가 PS `Out-File -Encoding utf8`로 쓰면 **BOM**이 붙는다(실측). utf-8-sig로 읽어
    BOM을 벗겨야 첫 줄의 `^LAST_SUCCESS`가 매칭된다 — 안 그러면 항상 '없음'으로 오판.
    """
    if not status_path.exists():
        return None
    m = re.search(r"^LAST_SUCCESS\s+(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)",
                  status_path.read_text(encoding="utf-8-sig"), re.MULTILINE)
    return dt.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S") if m else None


def evaluate(
    status_path: Path, raw_dir: Path, log_dir: Path, codes: list[str],
    now: dt.datetime, max_gap_hours: float = MAX_GAP_HOURS,
) -> dict:
    """수집 상태 평가 → 결정 dict. 부작용 없음(테스트 용이)."""
    last = parse_last_success(status_path)
    stale_hours = None if last is None else round((now - last).total_seconds() / 3600, 1)

    # 최근 도래 슬롯이 missing인지 (원장 기반)
    from dart_event_study.toss.ledger import build_ledger

    ledger = build_ledger(raw_dir, log_dir, codes, now=now)
    recent_status = None
    if not ledger.empty:
        recent_status = ledger.iloc[-1]["status"]

    if last is None or (stale_hours is not None and stale_hours > max_gap_hours):
        action, reason = "rerun_alert", (
            "LAST_SUCCESS 없음" if last is None
            else f"마지막 성공 {stale_hours}h 전 (임계 {max_gap_hours}h 초과) — 수집 멈춤 의심")
    elif recent_status == "missing":
        action, reason = "rerun", "최근 도래 슬롯 missing — 현재 슬롯 실패 추정, 재시도"
    else:
        action, reason = "ok", f"정상 (마지막 성공 {stale_hours}h 전, 최근 슬롯 {recent_status})"

    return {
        "checked_at": now.isoformat(),
        "last_success": last.isoformat() if last else None,
        "stale_hours": stale_hours,
        "recent_slot_status": recent_status,
        "action": action,
        "reason": reason,
    }


def main() -> None:
    import sys

    import yaml

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    cfg = yaml.safe_load((CONFIG_DIR / "toss_universe.yaml").read_text(encoding="utf-8"))
    codes = list({**cfg["compare"], **cfg["expand"]})
    decision = evaluate(STATUS_PATH, RAW_DIR, LOG_DIR, codes, dt.datetime.now())

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if decision["action"] != "ok":
        ALERT_PATH.write_text(
            f"WATCHDOG {decision['checked_at']} action={decision['action']}\n{decision['reason']}\n",
            encoding="utf-8")
    elif ALERT_PATH.exists():
        ALERT_PATH.unlink()  # 회복됨 → stale 마커 제거

    print(json.dumps(decision, ensure_ascii=False))  # PS 래퍼가 파싱


if __name__ == "__main__":
    main()
