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
        self.settings        = settings
        self._magnifiers: Dict[int, "_Pair"] = {}
        self._opacity_hidden = False
        self._saved_opacity: Dict[int, float] = {}
        self.capture_engine  = CaptureEngine(settings.app.capture_method)

    def _next_available_id(self) -> int:
        """사용 중인 ID 중 가장 낮은 빈 번호를 반환합니다."""
        used = set(self._magnifiers.keys())
        i = 1
        while i in used:
            i += 1
        return i

    def create_magnifier(self, target_hwnd: int = 0,
                         config: Optional[MagnifierConfig] = None) -> int:
        from ui.selection_overlay import SelectionOverlay
        from ui.magnifier_window  import MagnifierWindow

        mid = self._next_available_id()

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

        out.closed.connect(lambda mid=mid: self._on_window_closed(mid))
        out.save_preset_requested.connect(
            lambda name, cfg=config: self._handle_save_preset(name, cfg)
        )

        sel.show()
        out.show()
        out.set_selection_overlay(sel)

        if config.dwm_mode and config.target_hwnd:
            out.set_dwm_mode(True)

        self.magnifier_created.emit(mid)
        title = self.capture_engine.get_window_title(target_hwnd) if target_hwnd else "없음"
        print(f"[확대기 #{mid}] 생성됨  (대상: '{title}')")
        return mid

    def destroy_magnifier(self, mid: int):
        pair = self._magnifiers.pop(mid, None)
        if pair:
            pair.selection.close()
            pair.output.close()   # → closeEvent → closed.emit() → _on_window_closed (no-op, 이미 pop)
            self.magnifier_destroyed.emit(mid)
            print(f"[확대기 #{mid}] 닫혔습니다.")

    # ── 전체 단축키 동작 ─────────────────────────────────────

    def toggle_all_opacity(self):
        """전체 확대기 투명도 0% ↔ 이전 값 토글 (단축키용)"""
        if self._opacity_hidden:
            for mid, pair in self._magnifiers.items():
                saved = self._saved_opacity.get(mid, 1.0)
                pair.output.setWindowOpacity(saved)
                pair.output.config.opacity = saved
            self._opacity_hidden = False
        else:
            self._saved_opacity = {}
            for mid, pair in self._magnifiers.items():
                self._saved_opacity[mid] = pair.output.windowOpacity()
                pair.output.setWindowOpacity(0.0)
            self._opacity_hidden = True

    def toggle_all_clickthrough(self):
        """전체 확대기 클릭 투과 토글 (단축키용)"""
        for pair in self._magnifiers.values():
            pair.output.toggle_click_through()

    def _handle_save_preset(self, name: str, config: MagnifierConfig):
        process_name = self.capture_engine.get_process_name(config.target_hwnd)
        self.settings.save_preset(name, config, process_name)
        print(f"프리셋 '{name}' 저장됨  (프로세스: {process_name or '없음'})")

    def _on_window_closed(self, mid: int):
        """Bug 1: 출력창이 직접 닫혔을 때 — _magnifiers 에서 제거하고 트레이를 갱신."""
        if mid not in self._magnifiers:
            return  # destroy_magnifier() 가 이미 pop 했으면 무시
        self._magnifiers.pop(mid)
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
