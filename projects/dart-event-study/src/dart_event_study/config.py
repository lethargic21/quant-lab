"""config/*.yaml 로딩 + 경로 상수."""

from __future__ import annotations

import os
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]  # projects/dart-event-study
REPO_ROOT = PROJECT_ROOT.parents[1]  # quant-lab
CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"  # gitignore — API 응답/가격 스냅샷 캐시


def load_settings() -> dict:
    return yaml.safe_load((CONFIG_DIR / "settings.yaml").read_text(encoding="utf-8"))


def load_universe() -> dict:
    return yaml.safe_load((CONFIG_DIR / "universe.yaml").read_text(encoding="utf-8"))


def resolve_tickers(universe: dict | None = None) -> list[str]:
    """mode/selection에 따라 유니버스 티커 반환."""
    universe = universe or load_universe()
    if universe["mode"] == "debug":
        return list(universe["debug_tickers"])

    if universe.get("selection", "snapshot_current") == "proxy_2019":
        return resolve_universe_asof(universe)["tickers"]
    from quantlab_shared.data.universe import get_kospi_top_n

    return get_kospi_top_n(universe["full_size"], cache_dir=DATA_DIR / "universe")


def resolve_universe_asof(universe: dict | None = None) -> dict:
    """proxy_2019 유니버스 전체 메타데이터 (tickers / delisted / delisted_loss).

    delisted_loss는 백테스트 청산 할인 대상 (손실형 상폐만).
    """
    universe = universe or load_universe()
    from quantlab_shared.data.universe import get_kospi_top_n_asof

    asof = load_settings()["period"]["start"]
    return get_kospi_top_n_asof(universe["full_size"], asof=asof, cache_dir=DATA_DIR / "universe")


def get_api_key() -> str:
    """레포 루트 .env에서 OPENDART_API_KEY 로딩."""
    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / ".env")
    key = os.environ.get("OPENDART_API_KEY")
    if not key:
        raise RuntimeError("OPENDART_API_KEY가 없음 — 레포 루트 .env 확인 (.env.example 참조)")
    return key
