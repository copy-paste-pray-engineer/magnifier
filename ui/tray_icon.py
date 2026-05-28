"""
시스템 트레이 아이콘
- 더블클릭: 새 확대기
- 우클릭 메뉴: 새 확대기 / 확대기 목록 / 종료
"""
from pathlib import Path
from PySide6.QtWidgets import QSystemTrayIcon, QMenu, QApplication, QInputDialog, QMessageBox
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

    def __init__(self, mgr, settings, hotkey_mgr=None, parent=None):
        super().__init__(parent)
        self.mgr          = mgr
        self.settings     = settings
        self._hotkey_mgr  = hotkey_mgr

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

        # 프리셋 불러오기 서브메뉴
        self._preset_menu = m.addMenu("프리셋 불러오기")
        self._preset_menu.aboutToShow.connect(self._fill_preset_menu)
        self._fill_preset_menu()
        m.addSeparator()

        # 프리셋 그룹 서브메뉴 — 여러 프리셋을 한 번에 실행
        self._group_menu = m.addMenu("프리셋 그룹")
        self._group_menu.aboutToShow.connect(self._fill_group_menu)
        self._fill_group_menu()
        m.addSeparator()

        # 확대기 목록 서브메뉴 (클릭 투과 상태일 때도 제어 가능)
        self._list_menu = m.addMenu("확대기 목록")
        self._list_menu.aboutToShow.connect(self._fill_list_menu)
        self._fill_list_menu()
        m.addSeparator()

        m.addAction("단축키 설정...").triggered.connect(self._show_hotkey_settings)
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
            pair  = self.mgr._magnifiers.get(mid)
            pname = pair.config.preset_name if (pair and pair.config.preset_name) else ""
            label = f"확대기 #{mid}  [{pname}]" if pname else f"확대기 #{mid}"
            sub = self._list_menu.addMenu(label)
            sub.addAction("설정 패널").triggered.connect(
                lambda _=False, m=mid: self._open_panel(m)
            )
            sub.addAction("클릭 투과 토글").triggered.connect(
                lambda _=False, m=mid: self._toggle_ct(m)
            )
            sub.addAction("프리셋으로 저장...").triggered.connect(
                lambda _=False, m=mid: self._save_preset(m)
            )
            sub.addAction("닫기").triggered.connect(
                lambda _=False, m=mid: self.mgr.destroy_magnifier(m)
            )

    def _fill_preset_menu(self):
        self._preset_menu.clear()
        presets = self.settings.get_presets()
        if not presets:
            empty = self._preset_menu.addAction("(없음)")
            empty.setEnabled(False)
        else:
            for p in presets:
                name = p.get("name", "?")
                proc = p.get("target_process", "")
                label = f"{name}  [{proc}]" if proc else name
                act = self._preset_menu.addAction(label)
                act.triggered.connect(lambda _=False, preset=p: self._load_preset(preset))
            self._preset_menu.addSeparator()
            self._preset_menu.addAction("프리셋 삭제...").triggered.connect(
                self._delete_preset_dialog
            )

    def _fill_group_menu(self):
        self._group_menu.clear()
        groups = self.settings.get_preset_groups()
        if not groups:
            empty = self._group_menu.addAction("(없음)")
            empty.setEnabled(False)
        else:
            for g in groups:
                gname = g.get("name", "?")
                count = len(g.get("presets", []))
                act = self._group_menu.addAction(f"{gname}  ({count}개)")
                act.triggered.connect(
                    lambda _=False, grp=g: self._load_preset_group(grp)
                )
        self._group_menu.addSeparator()
        self._group_menu.addAction("그룹 만들기...").triggered.connect(
            self._create_group_dialog
        )
        del_act = self._group_menu.addAction("그룹 삭제...")
        del_act.triggered.connect(self._delete_group_dialog)
        del_act.setEnabled(bool(groups))

    def _rebuild_menu(self, *_):
        self._fill_list_menu()

    # ── 동작 ─────────────────────────────────────────────────

    def _on_activate(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._new()

    def _new(self):
        self.mgr.create_magnifier(target_hwnd=0)

    def _open_panel(self, mid: int):
        pair = self.mgr._magnifiers.get(mid)
        if pair:
            pair.output._show_panel()

    def _toggle_ct(self, mid: int):
        pair = self.mgr._magnifiers.get(mid)
        if pair:
            pair.output.toggle_click_through()

    # ── 프리셋 ─────────────────────────────────────────────────

    def _save_preset(self, mid: int):
        pair = self.mgr._magnifiers.get(mid)
        if not pair:
            return
        name, ok = QInputDialog.getText(
            None, "프리셋 저장", f"확대기 #{mid} 프리셋 이름을 입력하세요:"
        )
        if not ok or not name.strip():
            return
        name         = name.strip()
        config       = pair.config
        process_name = self.mgr.capture_engine.get_process_name(config.target_hwnd)
        self.settings.save_preset(name, config, process_name)
        print(f"프리셋 '{name}' 저장됨  (프로세스: {process_name or '없음'})")

    def _load_preset(self, preset: dict):
        cfg              = self.settings.preset_to_config(preset)
        cfg.preset_name  = preset.get("name", "")       # 트레이 목록에 이름 표시
        process_name     = preset.get("target_process", "")
        hwnd = (self.mgr.capture_engine.find_window_by_process(process_name)
                if process_name else 0)
        self.mgr.create_magnifier(target_hwnd=hwnd, config=cfg)
        if hwnd:
            title = self.mgr.capture_engine.get_window_title(hwnd)
            print(f"프리셋 '{preset.get('name')}' 불러옴  (창: '{title}')")
        else:
            print(f"프리셋 '{preset.get('name')}' 불러옴  "
                  f"(프로세스 '{process_name}' 미실행 — 대상 창 없음)")

    def _show_hotkey_settings(self):
        if self._hotkey_mgr is None:
            QMessageBox.information(None, "단축키", "단축키 관리자가 초기화되지 않았습니다.")
            return
        from ui.hotkey_settings import HotkeySettingsDialog
        dlg = HotkeySettingsDialog(self._hotkey_mgr)
        dlg.exec()

    def _delete_preset_dialog(self):
        presets = self.settings.get_presets()
        if not presets:
            QMessageBox.information(None, "프리셋 삭제", "저장된 프리셋이 없습니다.")
            return
        names = [p.get("name", "?") for p in presets]
        name, ok = QInputDialog.getItem(
            None, "프리셋 삭제", "삭제할 프리셋을 선택하세요:", names, 0, False
        )
        if ok and name:
            self.settings.delete_preset(name)
            print(f"프리셋 '{name}' 삭제됨")

    # ── 프리셋 그룹 ─────────────────────────────────────────────

    def _load_preset_group(self, group: dict):
        loaded  = 0
        missing = []
        for name in group.get("presets", []):
            preset = self.settings.get_preset(name)
            if preset is None:
                missing.append(name)   # 삭제된 프리셋 — 건너뜀
                continue
            self._load_preset(preset)
            loaded += 1
        print(f"그룹 '{group.get('name')}' 불러옴  (확대기 {loaded}개 생성)")
        if missing:
            print(f"  누락된 프리셋 건너뜀: {', '.join(missing)}")

    def _create_group_dialog(self):
        presets = self.settings.get_presets()
        if not presets:
            QMessageBox.information(
                None, "프리셋 그룹", "먼저 프리셋을 하나 이상 저장하세요."
            )
            return
        from ui.preset_group_dialog import PresetGroupDialog
        dlg = PresetGroupDialog(presets)
        if dlg.exec():
            name, preset_names = dlg.selected()
            self.settings.save_preset_group(name, preset_names)
            print(f"그룹 '{name}' 저장됨  (프리셋 {len(preset_names)}개)")

    def _delete_group_dialog(self):
        groups = self.settings.get_preset_groups()
        if not groups:
            QMessageBox.information(None, "그룹 삭제", "저장된 그룹이 없습니다.")
            return
        names = [g.get("name", "?") for g in groups]
        name, ok = QInputDialog.getItem(
            None, "그룹 삭제", "삭제할 그룹을 선택하세요:", names, 0, False
        )
        if ok and name:
            self.settings.delete_preset_group(name)
            print(f"그룹 '{name}' 삭제됨")

    def _quit(self):
        self.settings.save_magnifier_configs(self.mgr.get_configs())
        self.mgr.destroy_all()
        # 캡처 엔진의 모든 WGC 세션(D3D11 디바이스 포함) 해제
        try:
            self.mgr.capture_engine.close()
        except Exception:
            pass
        QApplication.quit()
