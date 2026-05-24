"""
설정 저장/로드
"""
import json, os
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any


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
    opacity: float = 1.0          # 기본 100%
    click_through: bool = False
    show_hud: bool = True
    target_hwnd: int = 0


@dataclass
class AppSettings:
    capture_method: str = "auto"  # auto, wgc, bitblt
    magnifiers: List[Dict[str, Any]] = field(default_factory=list)


class Settings:
    CONFIG_DIR  = Path(os.getenv("APPDATA", ".")) / "WinMagnifier"
    CONFIG_FILE = CONFIG_DIR / "settings.json"

    def __init__(self):
        self.app = AppSettings()
        self.CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    def load(self):
        if not self.CONFIG_FILE.exists():
            return
        try:
            with open(self.CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.app.capture_method = data.get("capture_method", self.app.capture_method)
            self.app.magnifiers     = data.get("magnifiers", [])
        except Exception as e:
            print(f"설정 로드 실패: {e}")

    def save(self):
        try:
            data = asdict(self.app)
            with open(self.CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"설정 저장 실패: {e}")

    def save_magnifier_configs(self, configs: list):
        self.app.magnifiers = [asdict(c) for c in configs]
        self.save()

    def get_magnifier_configs(self) -> List[MagnifierConfig]:
        result = []
        for d in self.app.magnifiers:
            cfg = MagnifierConfig()
            for k, v in d.items():
                if hasattr(cfg, k) and k != "target_hwnd":
                    setattr(cfg, k, v)
            result.append(cfg)
        return result
