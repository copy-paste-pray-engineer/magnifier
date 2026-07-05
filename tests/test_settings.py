"""Settings — 프리셋/그룹/단축키 직렬화 round-trip 검증."""
from pathlib import Path

import pytest

from utils.settings import MagnifierConfig, Settings


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    """실제 %APPDATA% 를 건드리지 않도록 임시 디렉토리로 격리."""
    monkeypatch.setattr(Settings, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(Settings, "CONFIG_FILE", tmp_path / "settings.json")
    return Settings()


def _reload(settings: Settings) -> Settings:
    """디스크에 저장된 내용을 새 인스턴스로 다시 읽는다."""
    fresh = Settings()
    fresh.load()
    return fresh


def test_preset_roundtrip(settings: Settings) -> None:
    cfg = MagnifierConfig(selection_w=123, selection_h=45, fps=30, opacity=0.5)
    settings.save_preset("게임용", cfg, "game.exe")

    loaded = _reload(settings)
    preset = loaded.get_preset("게임용")
    assert preset is not None
    assert preset["target_process"] == "game.exe"

    cfg2 = loaded.preset_to_config(preset)
    assert cfg2.selection_w == 123
    assert cfg2.selection_h == 45
    assert cfg2.fps == 30
    assert cfg2.opacity == 0.5


def test_preset_same_name_overwrites(settings: Settings) -> None:
    settings.save_preset("같은이름", MagnifierConfig(fps=30), "a.exe")
    settings.save_preset("같은이름", MagnifierConfig(fps=60), "b.exe")
    presets = settings.get_presets()
    assert len(presets) == 1
    assert presets[0]["fps"] == 60
    assert presets[0]["target_process"] == "b.exe"


def test_preset_to_config_excludes_hwnd(settings: Settings) -> None:
    """hwnd 는 휘발성 — 프리셋에 저장되지도, 복원되지도 않아야 한다."""
    cfg = MagnifierConfig(target_hwnd=0x1234)
    settings.save_preset("p", cfg, "")
    preset = settings.get_preset("p")
    assert preset is not None
    assert "target_hwnd" not in preset
    assert settings.preset_to_config(preset).target_hwnd == 0


def test_preset_group_roundtrip(settings: Settings) -> None:
    settings.save_preset("a", MagnifierConfig(), "")
    settings.save_preset("b", MagnifierConfig(), "")
    settings.save_preset_group("듀얼", ["a", "b"])

    loaded = _reload(settings)
    groups = loaded.get_preset_groups()
    assert len(groups) == 1
    assert groups[0]["presets"] == ["a", "b"]

    loaded.delete_preset_group("듀얼")
    assert loaded.get_preset_groups() == []


def test_hotkey_binding_roundtrip(settings: Settings) -> None:
    settings.set_hotkey_binding("opacity", 0x0002 | 0x0004, 0x41)  # Ctrl+Shift+A

    loaded = _reload(settings)
    bindings = loaded.get_hotkey_bindings()
    assert bindings["opacity"] == {"mods": 0x0006, "vk": 0x41}
    # 건드리지 않은 바인딩은 기본값 유지
    assert bindings["clickthrough"]["vk"] == 0x71


def test_load_missing_file_keeps_defaults(settings: Settings) -> None:
    settings.load()  # 파일 없음 — 예외 없이 기본값 유지
    assert settings.app.capture_method == "auto"
    assert settings.get_presets() == []
