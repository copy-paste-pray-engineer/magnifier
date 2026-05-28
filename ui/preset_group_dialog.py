"""
프리셋 그룹 만들기 대화상자
저장된 프리셋 여러 개를 골라 하나의 그룹으로 묶습니다.
"""
from __future__ import annotations
from typing import List, Dict, Any, Tuple

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QLineEdit, QCheckBox,
    QDialogButtonBox, QScrollArea, QWidget, QMessageBox,
)
from PySide6.QtCore import Qt

_STYLE = """
QDialog, QWidget {
    background: #1e1e1e; color: #e0e0e0;
    font-family: Consolas; font-size: 11px;
}
QLabel  { color: #e0e0e0; }
QLineEdit {
    background: #2a2a2a; border: 1px solid #484848;
    border-radius: 4px; padding: 4px 6px; color: #e0e0e0;
}
QScrollArea { border: 1px solid #444; border-radius: 4px; }
QCheckBox { padding: 3px 0; }
QCheckBox::indicator {
    width: 14px; height: 14px;
    border: 1px solid #484848; border-radius: 3px; background: #2a2a2a;
}
QCheckBox::indicator:checked { background: #d43030; border-color: #d43030; }
QPushButton {
    background: #2a2a2a; border: 1px solid #484848;
    border-radius: 4px; padding: 5px 12px; color: #e0e0e0;
}
QPushButton:hover   { background: #383838; }
QPushButton:pressed { background: #d43030; color: white; }
"""


class PresetGroupDialog(QDialog):
    """트레이 → '프리셋 그룹' → '그룹 만들기...' 에서 열리는 다이얼로그."""

    def __init__(self, presets: List[Dict[str, Any]], parent=None):
        super().__init__(parent)
        self._checks: List[Tuple[QCheckBox, str]] = []
        self.setWindowTitle("프리셋 그룹 만들기")
        self.setStyleSheet(_STYLE)
        self.setWindowFlags(
            self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint
        )
        self._build_ui(presets)

    def _build_ui(self, presets: List[Dict[str, Any]]):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        layout.addWidget(QLabel("그룹 이름"))
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("예: 코딩 작업셋")
        layout.addWidget(self._name_edit)

        layout.addWidget(QLabel("포함할 프리셋 선택"))

        # 프리셋 목록 — 개수가 많아질 수 있으므로 스크롤 영역에 담는다.
        inner = QWidget()
        inner_lay = QVBoxLayout(inner)
        inner_lay.setContentsMargins(8, 6, 8, 6)
        inner_lay.setSpacing(2)
        for p in presets:
            name = p.get("name", "?")
            proc = p.get("target_process", "")
            chk = QCheckBox(f"{name}  [{proc}]" if proc else name)
            inner_lay.addWidget(chk)
            self._checks.append((chk, name))
        inner_lay.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(inner)
        scroll.setMinimumHeight(160)
        layout.addWidget(scroll)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

        self.resize(320, 360)

    def selected(self) -> Tuple[str, List[str]]:
        """(그룹 이름, 선택된 프리셋 이름 목록) 반환."""
        name = self._name_edit.text().strip()
        names = [pname for chk, pname in self._checks if chk.isChecked()]
        return name, names

    def _on_accept(self):
        name, names = self.selected()
        if not name:
            QMessageBox.warning(self, "그룹 만들기", "그룹 이름을 입력하세요.")
            return
        if not names:
            QMessageBox.warning(self, "그룹 만들기", "프리셋을 하나 이상 선택하세요.")
            return
        self.accept()
