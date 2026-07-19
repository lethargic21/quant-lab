"""통합 CLI 검증 (네트워크 불필요).

핵심: 단계 디스패치, --mode/--config-dir 오버라이드가 설정 로더에 실제로 반영되는가,
그리고 기존 진입점을 깨지 않았는가(모든 단계 모듈이 main()을 갖는가).
"""

import importlib

import pytest
import yaml
from dart_event_study.__main__ import COMMANDS, main
from dart_event_study.config import apply_overrides, load_settings, load_universe


@pytest.fixture(autouse=True)
def _reset_overrides():
    """테스트 간 전역 오버라이드 격리 (누수 방지)."""
    from dart_event_study import config

    saved = dict(config._OVERRIDES)
    yield
    config._OVERRIDES.update(saved)


def test_list_exits_zero(capsys):
    assert main(["--list"]) == 0
    out = capsys.readouterr().out
    assert "event-study" in out and "caar-tests" in out and "toss-crawl" in out


def test_no_command_prints_help_and_returns_1(capsys):
    assert main([]) == 1
    assert "파이프라인 단계" in capsys.readouterr().out


def test_unknown_command_rejected():
    with pytest.raises(SystemExit):
        main(["no-such-stage"])


def test_dispatches_to_module_main(monkeypatch):
    called = {}
    module = importlib.import_module("dart_event_study.analysis.event_study")
    monkeypatch.setattr(module, "main", lambda: called.setdefault("ran", True))
    assert main(["event-study"]) == 0
    assert called.get("ran") is True


def test_mode_override_reaches_loader(monkeypatch):
    module = importlib.import_module("dart_event_study.analysis.event_study")
    seen = {}
    monkeypatch.setattr(module, "main", lambda: seen.setdefault("mode", load_universe()["mode"]))
    main(["event-study", "--mode", "debug"])
    assert seen["mode"] == "debug"


def test_config_dir_override_reads_alternate_files(tmp_path):
    (tmp_path / "universe.yaml").write_text(
        yaml.safe_dump({"mode": "debug", "debug_tickers": ["005930"]}), encoding="utf-8"
    )
    (tmp_path / "settings.yaml").write_text(
        yaml.safe_dump({"period": {"start": "2020-01-01", "end": "2020-12-31"}}), encoding="utf-8"
    )
    apply_overrides(config_dir=tmp_path)
    assert load_universe()["debug_tickers"] == ["005930"]
    assert load_settings()["period"]["start"] == "2020-01-01"


def test_missing_config_dir_rejected(tmp_path):
    with pytest.raises(SystemExit):
        apply_overrides(config_dir=tmp_path / "does-not-exist")


def test_mode_override_does_not_touch_yaml_file():
    """오버라이드는 메모리에서만 — universe.yaml 원본은 그대로여야 한다."""
    from dart_event_study.config import CONFIG_DIR

    before = (CONFIG_DIR / "universe.yaml").read_text(encoding="utf-8")
    apply_overrides(mode="debug")
    assert load_universe()["mode"] == "debug"
    assert (CONFIG_DIR / "universe.yaml").read_text(encoding="utf-8") == before


def test_every_command_target_module_has_main():
    """기존 `python -m <module>` 진입점 계약 유지 확인."""
    for name, (path, _) in COMMANDS.items():
        module = importlib.import_module(path)
        assert callable(getattr(module, "main", None)), f"{name} -> {path}에 main() 없음"
