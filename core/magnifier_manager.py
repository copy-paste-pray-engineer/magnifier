"""
MagnifierManager — 확대기 생성·삭제·관리
"""
from typing import List, Optional, Dict
from PySide6.QtCore import QObject, Signal
from capture.capture_engine import CaptureEngine
from utils.settings import Settings, MagnifierConfig


class MagnifierManager(QObject):
    magnifier_created   = Signal(int)
    magnifier_destroyed = Signal(int)
    all_destroyed       = Signal()

    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.settings       = settings
        self._magnifiers: Dict[int, "_Pair"] = {}
        self._next_id       = 1
        self.capture_engine = CaptureEngine(settings.app.capture_method)

    def create_magnifier(self, target_hwnd: int = 0,
                         config: Optional[MagnifierConfig] = None) -> int:
        from ui.selection_overlay import SelectionOverlay
        from ui.magnifier_window  import MagnifierWindow

        mid = self._next_id
        self._next_id += 1

        if config is None:
            config = MagnifierConfig(id=mid)
            off = (mid - 1) * 30
            config.selection_x += off;  config.selection_y += off
            config.output_x    += off;  config.output_y    += off

        config.id          = mid
        config.target_hwnd = target_hwnd

        out = MagnifierWindow(mid, config, self.capture_engine)
        sel = SelectionOverlay(mid, config, out)

        self._magnifiers[mid] = _Pair(mid, sel, out, config)

        sel.show()
        out.show()
        out.set_selection_overlay(sel)

        self.magnifier_created.emit(mid)
        title = self.capture_engine.get_window_title(target_hwnd) if target_hwnd else "없음"
        print(f"[확대기 #{mid}] 생성됨  (대상: '{title}')")
        return mid

    def destroy_magnifier(self, mid: int):
        pair = self._magnifiers.pop(mid, None)
        if pair:
            pair.selection.close()
            pair.output.close()
            self.magnifier_destroyed.emit(mid)
            print(f"[확대기 #{mid}] 닫혔습니다.")

    def destroy_all(self):
        for mid in list(self._magnifiers):
            self.destroy_magnifier(mid)
        self.all_destroyed.emit()

    def get_configs(self) -> List[MagnifierConfig]:
        return [p.config for p in self._magnifiers.values()]

    def restore_from_settings(self):
        for cfg in self.settings.get_magnifier_configs():
            self.create_magnifier(config=cfg)

    @property
    def magnifier_ids(self) -> List[int]:
        return list(self._magnifiers.keys())

    @property
    def count(self) -> int:
        return len(self._magnifiers)


class _Pair:
    def __init__(self, id, selection, output, config):
        self.id        = id
        self.selection = selection
        self.output    = output
        self.config    = config
