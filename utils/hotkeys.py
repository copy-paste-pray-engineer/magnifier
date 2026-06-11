"""
전역 단축키 관리 — Windows RegisterHotKey API + QAbstractNativeEventFilter
외부 의존성 없음. ctypes + PySide6 만 사용.

기본 바인딩:
  Ctrl + F1  →  전체 확대기 투명도 0% ↔ 복원
  Ctrl + F2  →  전체 확대기 클릭 투과 토글
"""
from __future__ import annotations
import ctypes
import ctypes.wintypes
import logging
from typing import Dict, TYPE_CHECKING

logger = logging.getLogger(__name__)

from PySide6.QtCore import QObject, QAbstractNativeEventFilter, Signal

if TYPE_CHECKING:
    from utils.settings import Settings

WM_HOTKEY    = 0x0312
MOD_NOREPEAT = 0x4000  # 키를 누르고 있어도 반복 발생 안 함

HOTKEY_OPACITY_ID      = 1
HOTKEY_CLICKTHROUGH_ID = 2


# ── 키 변환 유틸 ────────────────────────────────────────────────
def binding_to_label(mods: int, vk: int) -> str:
    """Win32 mods + VK 코드 → 사람이 읽을 수 있는 문자열"""
    parts = []
    if mods & 0x0002: parts.append("Ctrl")
    if mods & 0x0004: parts.append("Shift")
    if mods & 0x0001: parts.append("Alt")
    if mods & 0x0008: parts.append("Win")
    parts.append(_vk_to_name(vk))
    return " + ".join(parts) if parts else "(없음)"


def _vk_to_name(vk: int) -> str:
    if 0x70 <= vk <= 0x7B:
        return f"F{vk - 0x70 + 1}"
    if 0x41 <= vk <= 0x5A:
        return chr(vk)
    if 0x30 <= vk <= 0x39:
        return chr(vk)
    _names = {
        0x20: "Space",  0x0D: "Enter",  0x1B: "Esc",    0x09: "Tab",
        0x2E: "Del",    0x2D: "Ins",    0x24: "Home",   0x23: "End",
        0x21: "PgUp",   0x22: "PgDn",
        0x26: "↑",      0x28: "↓",      0x25: "←",      0x27: "→",
        0x6A: "Num *",  0x6D: "Num -",  0x6B: "Num +",  0x6F: "Num /",
    }
    return _names.get(vk, f"VK{vk:02X}")


def qt_event_to_vkmods(qt_key: int, qt_mods) -> tuple[int, int]:
    """Qt.Key + Qt.KeyboardModifiers → (win32_mods, vk_code)"""
    from PySide6.QtCore import Qt
    vk = 0
    if Qt.Key.Key_F1 <= qt_key <= Qt.Key.Key_F12:
        vk = 0x70 + (qt_key - Qt.Key.Key_F1)
    elif Qt.Key.Key_A <= qt_key <= Qt.Key.Key_Z:
        vk = ord('A') + (qt_key - Qt.Key.Key_A)
    elif Qt.Key.Key_0 <= qt_key <= Qt.Key.Key_9:
        vk = ord('0') + (qt_key - Qt.Key.Key_0)
    else:
        _map = {
            Qt.Key.Key_Space:    0x20, Qt.Key.Key_Return:   0x0D,
            Qt.Key.Key_Escape:   0x1B, Qt.Key.Key_Tab:      0x09,
            Qt.Key.Key_Delete:   0x2E, Qt.Key.Key_Insert:   0x2D,
            Qt.Key.Key_Home:     0x24, Qt.Key.Key_End:      0x23,
            Qt.Key.Key_PageUp:   0x21, Qt.Key.Key_PageDown: 0x22,
            Qt.Key.Key_Up:       0x26, Qt.Key.Key_Down:     0x28,
            Qt.Key.Key_Left:     0x25, Qt.Key.Key_Right:    0x27,
            Qt.Key.Key_Asterisk: 0x6A, Qt.Key.Key_Minus:    0x6D,
            Qt.Key.Key_Plus:     0x6B, Qt.Key.Key_Slash:    0x6F,
        }
        vk = _map.get(qt_key, 0)

    mods = 0
    if qt_mods & Qt.KeyboardModifier.ControlModifier: mods |= 0x0002
    if qt_mods & Qt.KeyboardModifier.ShiftModifier:   mods |= 0x0004
    if qt_mods & Qt.KeyboardModifier.AltModifier:     mods |= 0x0001
    if qt_mods & Qt.KeyboardModifier.MetaModifier:    mods |= 0x0008
    return mods, vk


# ── 네이티브 이벤트 필터 ────────────────────────────────────────
class _HotkeyFilter(QAbstractNativeEventFilter):
    """QApplication 의 네이티브 이벤트 큐에서 WM_HOTKEY 를 가로챕니다."""

    def __init__(self, manager: "HotkeyManager") -> None:
        super().__init__()
        self._mgr = manager

    def nativeEventFilter(self, event_type, message) -> tuple[bool, int]:
        if event_type == b"windows_generic_MSG":
            try:
                msg = ctypes.cast(
                    int(message), ctypes.POINTER(ctypes.wintypes.MSG)
                )
                if msg[0].message == WM_HOTKEY:
                    self._mgr._dispatch(int(msg[0].wParam))
                    return True, 0
            except Exception:
                pass
        return False, 0


# ── 단축키 관리자 ───────────────────────────────────────────────
class HotkeyManager(QObject):
    """
    전역 단축키 등록·해제·재설정.
    RegisterHotKey(NULL, ...) 로 스레드 메시지 큐에 등록 →
    _HotkeyFilter 가 WM_HOTKEY 를 수신 → 시그널 발생.
    """
    opacity_toggled       = Signal()
    clickthrough_toggled  = Signal()

    def __init__(self, settings: "Settings", parent=None) -> None:
        super().__init__(parent)
        self.settings    = settings
        self._registered: Dict[int, tuple[int, int]] = {}  # id → (mods, vk)

        from PySide6.QtWidgets import QApplication
        self._filter = _HotkeyFilter(self)
        QApplication.instance().installNativeEventFilter(self._filter)

    def load_and_register(self) -> None:
        """설정에서 바인딩을 읽어 등록합니다."""
        bindings = self.settings.get_hotkey_bindings()
        self._register(HOTKEY_OPACITY_ID,
                       bindings["opacity"]["mods"],
                       bindings["opacity"]["vk"])
        self._register(HOTKEY_CLICKTHROUGH_ID,
                       bindings["clickthrough"]["mods"],
                       bindings["clickthrough"]["vk"])

    def current_binding(self, action: str) -> tuple[int, int]:
        """현재 등록된 (mods, vk) 반환"""
        hid = (HOTKEY_OPACITY_ID if action == "opacity"
               else HOTKEY_CLICKTHROUGH_ID)
        return self._registered.get(hid, (0, 0))

    def rebind(self, action: str, mods: int, vk: int) -> bool:
        """단축키를 재설정하고 설정 파일에 저장합니다. 성공 여부 반환."""
        hid = (HOTKEY_OPACITY_ID if action == "opacity"
               else HOTKEY_CLICKTHROUGH_ID)
        self._unregister(hid)
        ok = self._register(hid, mods, vk)
        if ok:
            self.settings.set_hotkey_binding(action, mods, vk)
        else:
            # 등록 실패 시 이전 바인딩 복원
            prev = self.settings.get_hotkey_bindings().get(action, {})
            if prev:
                self._register(hid, prev.get("mods", 0), prev.get("vk", 0))
        return ok

    def close(self) -> None:
        """모든 단축키를 해제합니다. 앱 종료 전에 호출해야 합니다."""
        for hid in list(self._registered):
            self._unregister(hid)
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app:
            app.removeNativeEventFilter(self._filter)

    # ── 내부 ────────────────────────────────────────────────────

    def _register(self, hotkey_id: int, mods: int, vk: int) -> bool:
        if not vk:
            return False
        self._unregister(hotkey_id)
        ok = bool(ctypes.windll.user32.RegisterHotKey(
            None, hotkey_id, mods | MOD_NOREPEAT, vk
        ))
        if ok:
            self._registered[hotkey_id] = (mods, vk)
            logger.info(f"[단축키] 등록됨  id={hotkey_id}  {binding_to_label(mods, vk)}")
        else:
            err = ctypes.windll.kernel32.GetLastError()
            logger.warning(f"[단축키] 등록 실패  id={hotkey_id}  {binding_to_label(mods, vk)}  "
                  f"(오류 {err} — 다른 앱이 이미 사용 중일 수 있습니다)")
        return ok

    def _unregister(self, hotkey_id: int) -> None:
        if hotkey_id in self._registered:
            ctypes.windll.user32.UnregisterHotKey(None, hotkey_id)
            del self._registered[hotkey_id]

    def _dispatch(self, hotkey_id: int) -> None:
        if hotkey_id == HOTKEY_OPACITY_ID:
            self.opacity_toggled.emit()
        elif hotkey_id == HOTKEY_CLICKTHROUGH_ID:
            self.clickthrough_toggled.emit()
