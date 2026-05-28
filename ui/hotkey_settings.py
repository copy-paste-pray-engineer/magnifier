"""
단축키 재설정 대화상자
"""
from __future__ import annotations
from typing import Optional, TYPE_CHECKING

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QGroupBox, QDialogButtonBox, QMessageBox,
)
from PySide6.QtCore import Qt, Signal

if TYPE_CHECKING:
    from utils.hotkeys import HotkeyManager

_STYLE = """
QDialog, QWidget {
    background: #1e1e1e; color: #e0e0e0;
    font-family: Consolas; font-size: 11px;
}
QGroupBox {
    border: 1px solid #444; border-radius: 4px;
    margin-top: 8px; padding-top: 6px;
    color: #888; font-size: 10px;
}
QLabel  { color: #e0e0e0; }
QPushButton {
    background: #2a2a2a; border: 1px solid #484848;
    border-radius: 4px; padding: 5px 12px; color: #e0e0e0;
}
QPushButton:hover   { background: #383838; }
QPushButton:pressed { background: #d43030; color: white; }
"""

_CAPTURE_STYLE = """
QPushButton {
    background: #1a3a6a; border: 1px solid #2060c0;
    border-radius: 4px; padding: 5px 12px; color: #80c0ff;
    font-weight: bold;
}
"""


class _KeyCaptureButton(QPushButton):
    """
    클릭하면 다음 키 조합을 캡처하는 버튼.
    key_captured(mods, vk) 시그널 발생 후 자동으로 원래 상태로 복귀.
    """
    key_captured = Signal(int, int)  # win32 mods, vk

    def __init__(self, label: str, parent=None):
        super().__init__(label, parent)
        self._idle_label  = label
        self._capturing   = False
        self.setMinimumWidth(160)
        self.clicked.connect(self._start_capture)

    def _start_capture(self):
        self._capturing = True
        self.setText("키 입력 중...  (ESC: 취소)")
        self.setStyleSheet(_CAPTURE_STYLE)
        self.setFocus()

    def _stop_capture(self, label: Optional[str] = None):
        self._capturing = False
        self.setText(label or self._idle_label)
        self.setStyleSheet("")

    def keyPressEvent(self, e):
        if not self._capturing:
            super().keyPressEvent(e)
            return

        from PySide6.QtCore import Qt
        key = e.key()

        # 수식키 단독 → 아직 대기
        if key in (Qt.Key.Key_Shift, Qt.Key.Key_Control,
                   Qt.Key.Key_Alt, Qt.Key.Key_Meta):
            return

        if key == Qt.Key.Key_Escape:
            self._stop_capture()
            return

        from utils.hotkeys import qt_event_to_vkmods, binding_to_label
        mods, vk = qt_event_to_vkmods(key, e.modifiers())
        if vk == 0:
            return  # 지원하지 않는 키

        label = binding_to_label(mods, vk)
        self._stop_capture(label)
        self.key_captured.emit(mods, vk)


class HotkeySettingsDialog(QDialog):
    """트레이 아이콘 → '단축키 설정...' 에서 열리는 다이얼로그"""

    def __init__(self, hotkey_mgr: "HotkeyManager", parent=None):
        super().__init__(parent)
        self._mgr = hotkey_mgr
        self.setWindowTitle("전역 단축키 설정")
        self.setStyleSheet(_STYLE)
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self._build_ui()

    def _build_ui(self):
        from utils.hotkeys import binding_to_label
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        info = QLabel(
            "단축키는 앱 포커스 없이도 동작합니다.\n"
            "'변경' 버튼 클릭 후 원하는 키 조합을 누르세요."
        )
        info.setStyleSheet("color: #888; font-size: 10px;")
        layout.addWidget(info)

        # ── 투명도 토글 ──
        op_mods, op_vk = self._mgr.current_binding("opacity")
        op_box  = QGroupBox("투명도 0% 토글  (전체 확대기)")
        op_lay  = QHBoxLayout(op_box)
        self._op_lbl = QLabel(binding_to_label(op_mods, op_vk))
        self._op_lbl.setStyleSheet("color: #50d050; font-weight: bold;")
        self._op_btn = _KeyCaptureButton("변경")
        self._op_btn.key_captured.connect(
            lambda m, v: self._apply("opacity", m, v)
        )
        op_lay.addWidget(self._op_lbl)
        op_lay.addStretch()
        op_lay.addWidget(self._op_btn)
        layout.addWidget(op_box)

        # ── 클릭 투과 토글 ──
        ct_mods, ct_vk = self._mgr.current_binding("clickthrough")
        ct_box  = QGroupBox("클릭 투과 토글  (전체 확대기)")
        ct_lay  = QHBoxLayout(ct_box)
        self._ct_lbl = QLabel(binding_to_label(ct_mods, ct_vk))
        self._ct_lbl.setStyleSheet("color: #50d050; font-weight: bold;")
        self._ct_btn = _KeyCaptureButton("변경")
        self._ct_btn.key_captured.connect(
            lambda m, v: self._apply("clickthrough", m, v)
        )
        ct_lay.addWidget(self._ct_lbl)
        ct_lay.addStretch()
        ct_lay.addWidget(self._ct_btn)
        layout.addWidget(ct_box)

        # ── 닫기 버튼 ──
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(self.accept)
        layout.addWidget(btns)

        self.adjustSize()

    def _apply(self, action: str, mods: int, vk: int):
        from utils.hotkeys import binding_to_label
        ok = self._mgr.rebind(action, mods, vk)
        label = binding_to_label(mods, vk)
        if ok:
            if action == "opacity":
                self._op_lbl.setText(label)
            else:
                self._ct_lbl.setText(label)
        else:
            QMessageBox.warning(
                self, "단축키 등록 실패",
                f"'{label}' 을(를) 등록할 수 없습니다.\n"
                "다른 프로그램이 이미 사용 중인 키 조합일 수 있습니다.\n"
                "다른 키 조합을 시도해 보세요."
            )
