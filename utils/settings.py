"""
설정 저장/로드
"""
import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional


@dataclass
class MagnifierConfig:
    id: int = 0
    selection_x: int = 100
    selection_y: int = 100
    selection_w: int = 300
    selection_h: int = 200
    output_x: int = 500
    output_y: int = 100
    output_w: int = 600
    output_h: int = 400
    opacity: float = 1.0
    click_through: bool = False
    show_hud: bool = True
    fps: int = 60
    dwm_mode: bool = False
    preset_name: str = ""
    target_hwnd: int = 0


_DEFAULT_HOTKEYS: Dict[str, Any] = {
    "opacity":      {"mods": 0x0002, "vk": 0x70},   # Ctrl + F1
    "clickthrough": {"mods": 0x0002, "vk": 0x71},   # Ctrl + F2
}


@dataclass
class AppSettings:
    capture_method: str = "auto"  # auto(=printwindow), printwindow, wgc, bitblt
    presets: List[Dict[str, Any]] = field(default_factory=list)
    preset_groups: List[Dict[str, Any]] = field(default_factory=list)
    hotkeys: Dict[str, Any] = field(
        default_factory=lambda: {k: dict(v) for k, v in _DEFAULT_HOTKEYS.items()}
    )


class Settings:
    CONFIG_DIR  = Path(os.getenv("APPDATA", ".")) / "WinMagnifier"
    CONFIG_FILE = CONFIG_DIR / "settings.json"

    def __init__(self) -> None:
        self.app = AppSettings()
        self.CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    def load(self) -> None:
        if not self.CONFIG_FILE.exists():
            return
        try:
            with open(self.CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.app.capture_method = data.get("capture_method", self.app.capture_method)
            self.app.presets        = data.get("presets", [])
            self.app.preset_groups  = data.get("preset_groups", [])
            self.app.hotkeys.update(data.get("hotkeys", {}))
        except Exception:
            logger.exception("설정 로드 실패")

    def save(self) -> None:
        try:
            data = asdict(self.app)
            with open(self.CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception:
            logger.exception("설정 저장 실패")

    # ── 단축키 ──────────────────────────────────────────────

    def get_hotkey_bindings(self) -> Dict[str, Any]:
        result = {k: dict(v) for k, v in _DEFAULT_HOTKEYS.items()}
        result.update(self.app.hotkeys)
        return result

    def set_hotkey_binding(self, action: str, mods: int, vk: int) -> None:
        self.app.hotkeys[action] = {"mods": mods, "vk": vk}
        self.save()

    # ── 프리셋 ──────────────────────────────────────────────

    def save_preset(self, name: str, config: MagnifierConfig, process_name: str) -> None:
        preset = {
            "name":           name,
            "target_process": process_name,
            "selection_x":    config.selection_x,
            "selection_y":    config.selection_y,
            "selection_w":    config.selection_w,
            "selection_h":    config.selection_h,
            "output_x":       config.output_x,
            "output_y":       config.output_y,
            "output_w":       config.output_w,
            "output_h":       config.output_h,
            "opacity":        config.opacity,
            "click_through":  config.click_through,
            "show_hud":       config.show_hud,
            "fps":            config.fps,
            "dwm_mode":       config.dwm_mode,
        }
        self.app.presets = [p for p in self.app.presets if p.get("name") != name]
        self.app.presets.append(preset)
        self.save()

    def get_presets(self) -> List[Dict[str, Any]]:
        return list(self.app.presets)

    def get_preset(self, name: str) -> Optional[Dict[str, Any]]:
        for p in self.app.presets:
            if p.get("name") == name:
                return p
        return None

    def delete_preset(self, name: str) -> None:
        self.app.presets = [p for p in self.app.presets if p.get("name") != name]
        self.save()

    def preset_to_config(self, preset: Dict[str, Any]) -> MagnifierConfig:
        cfg = MagnifierConfig()
        for k in ("selection_x", "selection_y", "selection_w", "selection_h",
                  "output_x", "output_y", "output_w", "output_h",
                  "opacity", "click_through", "show_hud", "fps", "dwm_mode"):
            if k in preset:
                setattr(cfg, k, preset[k])
        return cfg

    # ── 프리셋 그룹 ─────────────────────────────────────────
    # 그룹은 프리셋 데이터를 복사하지 않고 이름만 참조한다.

    def save_preset_group(self, name: str, preset_names: List[str]) -> None:
        group = {"name": name, "presets": list(preset_names)}
        self.app.preset_groups = [g for g in self.app.preset_groups
                                  if g.get("name") != name]
        self.app.preset_groups.append(group)
        self.save()

    def get_preset_groups(self) -> List[Dict[str, Any]]:
        return list(self.app.preset_groups)

    def delete_preset_group(self, name: str) -> None:
        self.app.preset_groups = [g for g in self.app.preset_groups
                                  if g.get("name") != name]
        self.save()
