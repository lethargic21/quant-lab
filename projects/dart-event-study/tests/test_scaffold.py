"""Phase 0 스캐폴드 검증: 패키지 임포트와 config 로딩이 되는지만 확인."""

from pathlib import Path

import yaml

CONFIG_DIR = Path(__file__).parent.parent / "config"


def test_packages_importable():
    import dart_event_study  # noqa: F401
    import quantlab_shared

    assert quantlab_shared.RANDOM_SEED == 42


def test_universe_config():
    cfg = yaml.safe_load((CONFIG_DIR / "universe.yaml").read_text(encoding="utf-8"))
    assert cfg["mode"] in ("debug", "full")
    assert len(cfg["debug_tickers"]) == 5
    assert all(len(t) == 6 for t in cfg["debug_tickers"])


def test_settings_config():
    cfg = yaml.safe_load((CONFIG_DIR / "settings.yaml").read_text(encoding="utf-8"))
    assert cfg["period"]["start"] < cfg["period"]["end"]
    assert 0 < cfg["costs"]["transaction_tax"] < 0.01
    assert cfg["direction_rules"]["buyback"]["direction"] == 1
