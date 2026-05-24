"""
확대기 설정 패널
출력창 우클릭 → "설정 패널" 로 열 수 있습니다.
패널을 닫아도 확대기는 계속 동작합니다.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QSlider,
    QPushButton, QGroupBox, QCheckBox, QFrame, QButtonGroup,
)
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui  import QPainter, QColor

if TYPE_CHECKING:
    from ui.magnifier_window import MagnifierWindow

# ── 팔레트 ───────────────────────────────────────────────────
_BG   = "#1a1a1a"
_CARD = "#242424"
_RED  = "#d43030"
_BLUE = "#3060e0"
_GRN  = "#28a050"
_TEXT = "#e8e8e8"
_DIM  = "#888888"
_LINE = "#3a3a3a"
_SEL  = "#383838"   # 선택된 버튼 배경

_BASE_STYLE = f"""
QWidget {{
    background: {_BG};
    color: {_TEXT};
    font-family: 'Consolas', monospace;
    font-size: 11px;
}}
QGroupBox {{
    border: 1px solid {_LINE};
    border-radius: 4px;
    margin-top: 8px;
    padding-top: 6px;
    font-weight: bold;
    color: {_DIM};
    font-size: 10px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 8px;
}}
QSlider::groove:horizontal {{
    height: 4px;
    background: {_LINE};
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    width: 14px;
    height: 14px;
    background: {_RED};
    border-radius: 7px;
    margin: -5px 0;
}}
QSlider::sub-page:horizontal {{
    background: {_RED};
    border-radius: 2px;
}}
QPushButton {{
    background: {_CARD};
    border: 1px solid {_LINE};
    border-radius: 4px;
    padding: 5px 10px;
    color: {_TEXT};
}}
QPushButton:hover {{
    background: {_SEL};
    border-color: #606060;
}}
QPushButton:pressed {{
    background: {_RED};
}}
QCheckBox::indicator {{
    width: 14px;
    height: 14px;
    border: 1px solid {_LINE};
    border-radius: 3px;
    background: {_CARD};
}}
QCheckBox::indicator:checked {{
    background: {_RED};
    border-color: {_RED};
}}
"""

# 캡처 방법 토글 버튼 스타일
_BTN_ACTIVE = (
    f"QPushButton {{"
    f"  background: {_RED}; border: 1px solid {_RED};"
    f"  border-radius: 4px; padding: 5px 10px; color: white; font-weight: bold;"
    f"}}"
    f"QPushButton:hover {{ background: #c02828; border-color: #c02828; }}"
)
_BTN_INACTIVE = (
    f"QPushButton {{"
    f"  background: {_CARD}; border: 1px solid {_LINE};"
    f"  border-radius: 4px; padding: 5px 10px; color: {_DIM};"
    f"}}"
    f"QPushButton:hover {{ background: {_SEL}; color: {_TEXT}; }}"
)

# HUD 약어
_METHOD_SHORT = {"auto": "AUTO", "wgc": "WGC", "bitblt": "GDI"}


class _Sep(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.HLine)
        self.setStyleSheet(f"color: {_LINE};")


class ControlPanel(QWidget):
    """확대기에 종속된 설정 패널. 닫아도 확대기는 유지됩니다."""

    close_requested = Signal()

    def __init__(self, mag_window: "MagnifierWindow", parent=None):
        super().__init__(
            parent,
            Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.FramelessWindowHint,
        )
        self.w      = mag_window
        self.mag_id = mag_window.mag_id
        self._drag  = False
        self._anchor = None

        self.setStyleSheet(_BASE_STYLE)
        self.setFixedWidth(300)

        self._build_ui()
        self._wire()
        self._sync_from_window()

        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._refresh_stats)
        self._timer.start()

        geo = mag_window.geometry()
        self.move(geo.right() + 8, geo.top())

    # ── UI 구성 ──────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 10)
        root.setSpacing(5)

        # 헤더
        hdr = QHBoxLayout()
        ttl = QLabel(f"확대기  #{self.mag_id}  설정")
        ttl.setStyleSheet(f"font-size: 13px; font-weight: bold; color: {_RED};")
        x_btn = QPushButton("X")
        x_btn.setFixedSize(22, 22)
        x_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none; color: {_DIM}; }}"
            f"QPushButton:hover {{ color: {_RED}; }}"
        )
        x_btn.clicked.connect(self.hide)
        hdr.addWidget(ttl)
        hdr.addStretch()
        hdr.addWidget(x_btn)
        root.addLayout(hdr)
        root.addWidget(_Sep())

        # 현재 상태
        stat = QGroupBox("현재 상태")
        sl   = QHBoxLayout(stat)
        sl.setSpacing(6)
        self._fps_lbl  = QLabel("0 fps")
        self._fps_lbl.setStyleSheet("color: #50d050; font-weight: bold;")
        self._zoom_lbl = QLabel("--")
        self._zoom_lbl.setStyleSheet("color: #50d050; font-weight: bold;")
        self._mode_lbl = QLabel("--")
        self._mode_lbl.setStyleSheet(f"color: {_DIM}; font-size: 10px;")
        self._mode_lbl.setFixedWidth(44)
        self._mode_lbl.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        sl.addWidget(QLabel("FPS"));   sl.addWidget(self._fps_lbl)
        sl.addSpacing(8)
        sl.addWidget(QLabel("배율")); sl.addWidget(self._zoom_lbl)
        sl.addStretch()
        sl.addWidget(self._mode_lbl)
        root.addWidget(stat)

        # 투명도
        op_box = QGroupBox("투명도")
        op_lay = QHBoxLayout(op_box)
        self._op_sl = QSlider(Qt.Orientation.Horizontal)
        self._op_sl.setRange(10, 100)
        self._op_sl.setValue(100)
        self._op_v  = QLabel("100%")
        self._op_v.setStyleSheet(f"color: {_RED}; font-weight: bold;")
        self._op_v.setFixedWidth(38)
        op_lay.addWidget(self._op_sl)
        op_lay.addWidget(self._op_v)
        root.addWidget(op_box)

        # FPS 제한
        fps_box = QGroupBox("FPS 제한")
        fps_lay = QHBoxLayout(fps_box)
        self._fps_sl = QSlider(Qt.Orientation.Horizontal)
        self._fps_sl.setRange(10, 120)
        self._fps_sl.setValue(60)
        self._fps_sl.setTickInterval(10)
        self._fps_v  = QLabel("60")
        self._fps_v.setStyleSheet(f"color: {_RED}; font-weight: bold;")
        self._fps_v.setFixedWidth(38)
        fps_lay.addWidget(self._fps_sl)
        fps_lay.addWidget(self._fps_v)
        root.addWidget(fps_box)

        # ── 캡처 방법 — 고성능 / 저사양 두 버튼 ────────────────
        # 고성능 : WGC (GPU 가속, 비활성 창 지원)
        # 저사양  : DWM 썸네일 (CPU 최소, 픽셀 접근 불가)
        cap_box = QGroupBox("캡처 방법")
        cap_lay = QVBoxLayout(cap_box)
        cap_lay.setSpacing(6)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)

        self._btn_high = QPushButton("고성능")
        self._btn_high.setFixedHeight(46)
        self._btn_high.setToolTip(
            "고성능 모드 (WGC)\n\n"
            "GPU 가속, 비활성/가려진 창 캡처, 게임 화면 캡처."
        )

        self._btn_low = QPushButton("저사양")
        self._btn_low.setFixedHeight(46)
        self._btn_low.setToolTip(
            "저사양 모드 \n\n"
            "뭔가 뭔가 작동 안 할 수 있음"
        )

        btn_row.addWidget(self._btn_high)
        btn_row.addWidget(self._btn_low)
        cap_lay.addLayout(btn_row)

        # 현재 선택 표시 라벨
        self._cap_status = QLabel("")
        self._cap_status.setStyleSheet(f"color: {_DIM}; font-size: 10px;")
        self._cap_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cap_lay.addWidget(self._cap_status)
        root.addWidget(cap_box)

        # 표시 설정
        disp_box = QGroupBox("표시 설정")
        disp_lay = QVBoxLayout(disp_box)
        self._hud_chk = QCheckBox("HUD 오버레이  (번호·배율·FPS)")
        self._top_chk = QCheckBox("항상 맨 위에 표시")
        self._top_chk.setChecked(True)
        disp_lay.addWidget(self._hud_chk)
        disp_lay.addWidget(self._top_chk)
        root.addWidget(disp_box)

        # 동작
        act_box = QGroupBox("동작")
        act_lay = QVBoxLayout(act_box)
        self._ct_btn   = QPushButton("클릭 투과 켜기")
        self._pick_btn = QPushButton("대상 창 지정")
        act_lay.addWidget(self._ct_btn)
        act_lay.addWidget(self._pick_btn)
        root.addWidget(act_box)

        # 확대기 닫기
        self._del_btn = QPushButton("이 확대기 닫기")
        self._del_btn.setStyleSheet(
            f"QPushButton {{ border-color: {_RED}; color: {_RED}; }}"
            f"QPushButton:hover {{ background: {_RED}; color: white; }}"
        )
        root.addWidget(self._del_btn)
        self.adjustSize()

    def _wire(self):
        self._op_sl.valueChanged.connect(self._on_opacity)
        self._fps_sl.valueChanged.connect(self._on_fps)
        self._btn_high.clicked.connect(self._on_high_perf)
        self._btn_low.clicked.connect(self._on_low_spec)
        self._hud_chk.toggled.connect(self._on_hud)
        self._ct_btn.clicked.connect(self._on_ct)
        self._top_chk.toggled.connect(self._on_topmost)
        self._pick_btn.clicked.connect(self.w._pick_target_window)
        self._del_btn.clicked.connect(self.close_requested.emit)

    def _sync_from_window(self):
        w  = self.w
        op = int(w.windowOpacity() * 100)
        self._op_sl.blockSignals(True)
        self._op_sl.setValue(op)
        self._op_sl.blockSignals(False)
        self._op_v.setText(f"{op}%")

        self._hud_chk.blockSignals(True)
        self._hud_chk.setChecked(w._show_hud)
        self._hud_chk.blockSignals(False)

        self._update_cap_buttons()
        self._update_ct_btn()

    # ── 캡처 방법 버튼 ───────────────────────────────────────

    def _on_high_perf(self):
        """고성능(WGC) — DWM 모드 끄고 WGC 캡처 활성화"""
        if self.w._dwm_mode:
            self.w.set_dwm_mode(False)
        self.w.capture_engine._active = "wgc"
        self._update_cap_buttons()
        print(f"[확대기 #{self.mag_id}] 고성능 모드 (WGC)")

    def _on_low_spec(self):
        """저사양(DWM) — DWM 썸네일 모드 활성화"""
        if not self.w._dwm_mode:
            self.w.set_dwm_mode(True)
        self._update_cap_buttons()
        print(f"[확대기 #{self.mag_id}] 저사양 모드 (DWM)")


    def _update_cap_buttons(self):
        """현재 모드에 따라 버튼 외관 및 상태 텍스트 업데이트"""
        w = self.w
        if w._dwm_mode:
            self._btn_high.setStyleSheet(_BTN_INACTIVE)
            self._btn_low.setStyleSheet(_BTN_ACTIVE)
            self._cap_status.setText("DWM 썸네일 렌더링 중  —  CPU 사용 최소")
        else:
            self._btn_high.setStyleSheet(_BTN_ACTIVE)
            self._btn_low.setStyleSheet(_BTN_INACTIVE)
            # _active 문자열이 아닌 실제 패키지 가용 여부로 판단
            try:
                from capture.wgc_capture import wgc_available
                _wgc_ok = wgc_available()
            except Exception:
                _wgc_ok = False
            active = getattr(w.capture_engine, "_active", "bitblt")
            if active == "wgc" and _wgc_ok:
                self._cap_status.setText("WGC 캡처 중  —  GPU 가속, 비활성 창 지원")
            else:
                self._cap_status.setText(
                    "BitBlt 캡처 중  —  화면에 보이는 영역만\n"
                    "고성능 모드: 가상환경에 winrt 패키지 설치 후 재시작"
                )
    # ── 슬롯 ─────────────────────────────────────────────────

    def _on_opacity(self, v: int):
        self._op_v.setText(f"{v}%")
        self.w.setWindowOpacity(v / 100.0)
        self.w.config.opacity = v / 100.0

    def _on_fps(self, v: int):
        self._fps_v.setText(str(v))
        if not self.w._dwm_mode:
            self.w._cap.set_fps(v)

    def _on_hud(self, checked: bool):
        if self.w._show_hud != checked:
            self.w.toggle_hud()

    def _on_ct(self):
        self.w.toggle_click_through()
        self._update_ct_btn()

    def _update_ct_btn(self):
        if self.w._click_through:
            self._ct_btn.setText("클릭 투과 끄기   [현재: 켜짐]")
            self._ct_btn.setStyleSheet(
                f"QPushButton {{ background: {_BLUE}; border-color: {_BLUE}; }}"
                f"QPushButton:hover {{ background: #2050c0; }}"
            )
        else:
            self._ct_btn.setText("클릭 투과 켜기")
            self._ct_btn.setStyleSheet("")

    def _on_topmost(self, checked: bool):
        flags = self.w.windowFlags()
        if checked:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        else:
            flags &= ~Qt.WindowType.WindowStaysOnTopHint
        self.w.setWindowFlags(flags)
        self.w.show()

    # ── 실시간 갱신 ──────────────────────────────────────────

    def _refresh_stats(self):
        if not self.isVisible():
            return
        w = self.w

        self._fps_lbl.setText(f"{w._fps_display} fps")

        sel = w.selection_overlay
        if sel:
            sr = sel.get_region()
            if sr.width() > 0 and sr.height() > 0:
                zm = ((w.width() / sr.width()) + (w.height() / sr.height())) / 2
                self._zoom_lbl.setText(f"{zm:.1f}x")
            else:
                self._zoom_lbl.setText("--")

        raw = getattr(w.capture_engine, "_active", "auto")
        self._mode_lbl.setText(_METHOD_SHORT.get(raw, raw[:4].upper()))

        self._update_ct_btn()
        self._update_cap_buttons()

        op = int(w.windowOpacity() * 100)
        if self._op_sl.value() != op:
            self._op_sl.blockSignals(True)
            self._op_sl.setValue(op)
            self._op_sl.blockSignals(False)
            self._op_v.setText(f"{op}%")

        if self._hud_chk.isChecked() != w._show_hud:
            self._hud_chk.blockSignals(True)
            self._hud_chk.setChecked(w._show_hud)
            self._hud_chk.blockSignals(False)

    # ── 드래그 이동 ──────────────────────────────────────────

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag   = True
            self._anchor = e.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, e):
        if self._drag and self._anchor:
            self.move(e.globalPosition().toPoint() - self._anchor)

    def mouseReleaseEvent(self, e):
        self._drag = False

    def closeEvent(self, e):
        self._timer.stop()
        super().closeEvent(e)

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(26, 26, 26))
        p.setPen(QColor(58, 58, 58))
        p.drawRect(0, 0, self.width() - 1, self.height() - 1)