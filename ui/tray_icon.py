"""
시스템 트레이 아이콘
- 더블클릭: 새 확대기
- 우클릭 메뉴: 새 확대기 / 확대기 목록 / 종료
"""
from pathlib import Path
from PySide6.QtWidgets import QSystemTrayIcon, QMenu, QApplication
from PySide6.QtGui     import QIcon, QPixmap, QPainter, QColor, QPen
from PySide6.QtCore    import Qt

_HERE = Path(__file__).parent.parent

_MENU_STYLE = """
QMenu {
    background: #1c1c1c;
    color: #e0e0e0;
    border: 1px solid #444;
    font-family: 'Consolas';
    font-size: 11px;
}
QMenu::item { padding: 5px 20px 5px 12px; }
QMenu::item:selected { background: #2e2e2e; color: #ff6060; }
QMenu::separator { height: 1px; background: #3a3a3a; margin: 3px 0; }
"""


def load_tray_icon() -> QIcon:
    ico = _HERE / "magnifier.ico"
    if ico.exists():
        return QIcon(str(ico))
    return _fallback_icon()


def _fallback_icon() -> QIcon:
    s   = 64
    pix = QPixmap(s, s)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor(22, 22, 26))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(1, 1, s - 2, s - 2)
    p.setPen(QPen(QColor(230, 50, 50), 5))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(4, 4, 36, 36)
    p.setPen(QPen(QColor(210, 48, 48), 7))
    p.drawLine(37, 37, 58, 58)
    p.end()
    return QIcon(pix)


class SystemTrayIcon(QSystemTrayIcon):

    def __init__(self, mgr, settings, parent=None):
        super().__init__(parent)
        self.mgr      = mgr
        self.settings = settings

        self.setIcon(load_tray_icon())
        self.setToolTip("WinMagnifier\n더블클릭: 새 확대기 / 우클릭: 메뉴")

        self._build_menu()
        self.activated.connect(self._on_activate)

        # 확대기 생성/삭제 시 목록 갱신
        mgr.magnifier_created.connect(self._rebuild_menu)
        mgr.magnifier_destroyed.connect(self._rebuild_menu)

    # ── 메뉴 구성 ────────────────────────────────────────────

    def _build_menu(self):
        m = QMenu()
        m.setStyleSheet(_MENU_STYLE)

        m.addAction("새 확대기").triggered.connect(self._new)
        m.addSeparator()

        # 확대기 목록 서브메뉴 (클릭 투과 상태일 때도 제어 가능)
        self._list_menu = m.addMenu("확대기 목록")
        self._fill_list_menu()
        m.addSeparator()

        m.addAction("종료").triggered.connect(self._quit)

        self.setContextMenu(m)

    def _fill_list_menu(self):
        self._list_menu.clear()
        ids = self.mgr.magnifier_ids
        if not ids:
            empty = self._list_menu.addAction("(없음)")
            empty.setEnabled(False)
            return
        for mid in ids:
            sub = self._list_menu.addMenu(f"확대기 #{mid}")
            sub.addAction("설정 패널").triggered.connect(
                lambda _=False, m=mid: self._open_panel(m)
            )
            sub.addAction("클릭 투과 토글").triggered.connect(
                lambda _=False, m=mid: self._toggle_ct(m)
            )
            sub.addAction("닫기").triggered.connect(
                lambda _=False, m=mid: self.mgr.destroy_magnifier(m)
            )

    def _rebuild_menu(self, *_):
        self._fill_list_menu()

    # ── 동작 ─────────────────────────────────────────────────

    def _on_activate(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._new()

    def _new(self):
        import ctypes
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        self.mgr.create_magnifier(target_hwnd=hwnd)

    def _open_panel(self, mid: int):
        pair = self.mgr._magnifiers.get(mid)
        if pair:
            pair.output._show_panel()

    def _toggle_ct(self, mid: int):
        pair = self.mgr._magnifiers.get(mid)
        if pair:
            pair.output.toggle_click_through()

    def _quit(self):
        self.settings.save_magnifier_configs(self.mgr.get_configs())
        self.mgr.destroy_all()
        QApplication.quit()
