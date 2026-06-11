"""
확대 출력창 — 실시간 렌더링, DWM 오버레이, 설정 패널 연동
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional
import ctypes
import ctypes.wintypes
import logging
import time

logger = logging.getLogger(__name__)

import numpy as np
from PySide6.QtWidgets import QWidget, QMenu, QHBoxLayout, QLabel, QPushButton
from PySide6.QtCore import (
    Qt, QRect, QPoint, QSize, QTimer, QThread, QMutex, QMutexLocker, Signal
)
from PySide6.QtGui import (
    QPainter, QColor, QImage,
    QMouseEvent, QFont, QCursor, QResizeEvent,
)

# ── 창 선택 안내 배너 ─────────────────────────────────────────────
class _PickerBanner(QWidget):
    """
    창 선택 모드 진입 시 화면 상단 중앙에 표시되는 작은 안내 위젯.
    WA_ShowWithoutActivating 으로 포커스를 빼앗지 않으므로
    사용자가 다른 창을 그대로 클릭할 수 있다.
    """
    from PySide6.QtCore import Signal as _Signal
    cancel_requested = _Signal()

    def __init__(self) -> None:
        super().__init__(None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setStyleSheet(
            "QWidget { background: #1e1e1e; border: 1px solid #555; border-radius: 6px; }"
            "QLabel  { color: #e0e0e0; font-family: Consolas; font-size: 12px;"
            "          border: none; background: transparent; padding: 0; }"
            "QPushButton { background: #6b1c1c; border: 1px solid #aa3030;"
            "              border-radius: 4px; padding: 4px 14px;"
            "              color: #ffaaaa; font-family: Consolas; font-size: 11px; }"
            "QPushButton:hover { background: #992222; }"
        )
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(14)
        lbl = QLabel("클릭할 창을 선택하세요  ·  ESC 또는 취소 버튼으로 중단")
        btn = QPushButton("취소")
        btn.setFixedWidth(60)
        btn.clicked.connect(self.cancel_requested)
        lay.addWidget(lbl)
        lay.addWidget(btn)
        self.adjustSize()

        from PySide6.QtWidgets import QApplication
        sr = QApplication.primaryScreen().geometry()
        self.move(sr.center().x() - self.width() // 2, sr.top() + 12)

from capture.capture_engine import CaptureEngine
from utils.settings         import MagnifierConfig

if TYPE_CHECKING:
    from ui.selection_overlay import SelectionOverlay
    from ui.dwm_overlay       import DWMOverlay

# Win32 창 스타일
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED     = 0x00080000
GWL_EXSTYLE       = -20

# 리사이즈 방향
R_NONE = 0
R_N=1; R_S=2; R_W=3; R_E=4; R_NW=5; R_NE=6; R_SW=7; R_SE=8

HANDLE = 14     # 리사이즈 감지 너비
MIN_W  = 20   # Feature 5: 줌아웃 허용을 위해 최솟값 축소
MIN_H  = 15

# HUD 캡처 방법 약어
_METHOD_SHORT = {"auto": "AUTO", "wgc": "WGC", "bitblt": "GDI", "printwindow": "PW"}

# 우클릭 메뉴 스타일 — 항목 가운데 정렬
_CTX_STYLE = """
QMenu {
    background: #1e1e1e;
    color: #e0e0e0;
    border: 1px solid #484848;
    font-family: 'Consolas';
    font-size: 11px;
}
QMenu::item {
    padding: 7px 28px;
    text-align: center;
}
QMenu::item:selected {
    background: #2a2a2a;
    color: #ffffff;
}
QMenu::separator {
    height: 1px;
    background: #3a3a3a;
    margin: 2px 0;
}
"""


# ── 캡처 스레드 ───────────────────────────────────────────────
class CaptureThread(QThread):
    frame_ready = Signal(object)

    def __init__(self, engine: CaptureEngine, config: MagnifierConfig) -> None:
        super().__init__()
        self._engine         = engine
        self._mutex          = QMutex()
        self._running        = False
        self._frame_interval = 1.0 / 60
        self._x    = config.selection_x
        self._y    = config.selection_y
        self._w    = max(1, config.selection_w)
        self._h    = max(1, config.selection_h)
        self._hwnd = config.target_hwnd

    def set_region(self, x, y, w, h, hwnd=0) -> None:
        with QMutexLocker(self._mutex):
            self._x = x;  self._y = y
            self._w = max(1, w);  self._h = max(1, h)
            self._hwnd = hwnd

    def set_fps(self, fps: int) -> None:
        self._frame_interval = 1.0 / max(1, min(240, fps))

    def run(self) -> None:
        self._running = True
        last = time.perf_counter()
        while self._running:
            now     = time.perf_counter()
            elapsed = now - last
            if elapsed < self._frame_interval:
                ms = int((self._frame_interval - elapsed) * 1000 - 1)
                if ms > 0:
                    self.msleep(ms)
                continue
            last = now
            with QMutexLocker(self._mutex):
                x, y, w, h, hwnd = self._x, self._y, self._w, self._h, self._hwnd
            if w <= 0 or h <= 0:
                continue
            try:
                frame = self._engine.capture(hwnd, x, y, w, h)
                if frame is not None:
                    self.frame_ready.emit(frame)
            except Exception:
                pass

    def stop(self) -> None:
        self._running = False
        self.wait(2000)


# ── 출력창 ────────────────────────────────────────────────────
class MagnifierWindow(QWidget):

    closed                 = Signal()   # 창이 닫힐 때 MagnifierManager 에 알림
    save_preset_requested  = Signal(str)  # 프리셋 이름 → Manager 가 저장 처리

    def __init__(self, mag_id: int, config: MagnifierConfig,
                 capture_engine: CaptureEngine, parent=None):
        super().__init__(parent)
        self.mag_id         = mag_id
        self.config         = config
        self.capture_engine = capture_engine
        self.selection_overlay: Optional[SelectionOverlay] = None

        self._frame         : Optional[QImage] = None
        self._frame_buf     : Optional[bytes]  = None  # QImage 가 참조하는 픽셀 버퍼 유지
        self._click_through = config.click_through
        self._show_hud      = config.show_hud
        self._dwm_mode      = False
        self._dwm_overlay   : Optional[DWMOverlay] = None

        self._drag_active = False
        self._drag_start  = QPoint()
        self._drag_origin = QPoint()
        self._drag_size   = QSize()
        self._resize_dir  = R_NONE

        self._fps_count   = 0
        self._fps_display = 0
        self._panel       = None

        self._setup_window()
        self._apply_config()

        # 캡처 엔진에 대상 hwnd 사용 등록(참조 카운트). closeEvent 에서 release.
        if config.target_hwnd:
            self.capture_engine.acquire_wgc(config.target_hwnd)

        self._cap = CaptureThread(capture_engine, config)
        self._cap.frame_ready.connect(self._on_frame)
        self._cap.start()
        self._cap.set_fps(config.fps)

        self._fps_timer = QTimer(self)
        self._fps_timer.setInterval(1000)
        self._fps_timer.timeout.connect(self._tick_fps)
        self._fps_timer.start()

        self._dwm_timer = QTimer(self)
        self._dwm_timer.setInterval(16)
        self._dwm_timer.timeout.connect(self._update_dwm)

    # ── 초기화 ───────────────────────────────────────────────

    def _setup_window(self) -> None:
        flags = (Qt.WindowType.FramelessWindowHint
                 | Qt.WindowType.WindowStaysOnTopHint
                 | Qt.WindowType.Tool)
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setMouseTracking(True)
        self.setMinimumSize(MIN_W, MIN_H)

    def _apply_config(self) -> None:
        self.setGeometry(self.config.output_x, self.config.output_y,
                         self.config.output_w, self.config.output_h)
        self.setWindowOpacity(self.config.opacity)
        if self.config.click_through:
            self._enable_click_through()

    def _save_config(self) -> None:
        g = self.geometry()
        self.config.output_x      = g.x()
        self.config.output_y      = g.y()
        self.config.output_w      = g.width()
        self.config.output_h      = g.height()
        self.config.opacity       = self.windowOpacity()
        self.config.click_through = self._click_through
        self.config.show_hud      = self._show_hud
        self.config.dwm_mode      = self._dwm_mode

    def set_selection_overlay(self, overlay: SelectionOverlay) -> None:
        self.selection_overlay = overlay
        overlay.region_changed.connect(self._on_region_changed)
        self._update_region(overlay.get_region())

    # ── 캡처 ─────────────────────────────────────────────────

    def _on_region_changed(self, rect: QRect) -> None:
        self._update_region(rect)
        if self._dwm_mode:
            self._update_dwm()

    def _update_region(self, rect: QRect) -> None:
        self._cap.set_region(rect.x(), rect.y(),
                             rect.width(), rect.height(),
                             self.config.target_hwnd)

    def _on_frame(self, frame: np.ndarray) -> None:
        if frame is None or frame.size == 0:
            return
        h, w, c = frame.shape
        # tobytes() 가 이미 새 복사본을 만들어 두므로 QImage.copy() 는 불필요.
        # 다만 QImage 는 데이터 소유권을 갖지 않으니 self 에서 참조를 유지해야 한다.
        self._frame_buf = frame.tobytes()
        self._frame = QImage(
            self._frame_buf, w, h, c * w, QImage.Format.Format_RGB888
        )
        self.update()
        self._fps_count += 1

    def _tick_fps(self) -> None:
        self._fps_display = self._fps_count
        self._fps_count   = 0

    # ── DWM 모드 ─────────────────────────────────────────────

    def set_dwm_mode(self, enabled: bool) -> None:
        if enabled == self._dwm_mode:
            return
        self._dwm_mode = enabled
        if enabled:
            self._start_dwm()
        else:
            self._stop_dwm()

    def _start_dwm(self) -> None:
        hwnd_src  = self.config.target_hwnd
        hwnd_dest = int(self.winId())
        if not hwnd_src:
            logger.info(f"[확대기 #{self.mag_id}] 대상 창이 지정되지 않아 DWM 모드를 켤 수 없습니다.")
            self._dwm_mode = False
            return
        from ui.dwm_overlay import DWMOverlay
        self._dwm_overlay = DWMOverlay(hwnd_src, hwnd_dest)
        if not self._dwm_overlay.ok:
            logger.warning(f"[확대기 #{self.mag_id}] DWM 썸네일 등록 실패 — 캡처 모드를 유지합니다.")
            self._dwm_overlay = None
            self._dwm_mode    = False
            return
        self._cap.set_fps(1)
        self._dwm_timer.start()
        self.update()
        logger.info(f"[확대기 #{self.mag_id}] DWM 모드 활성화")

    def _stop_dwm(self) -> None:
        self._dwm_timer.stop()
        if self._dwm_overlay:
            self._dwm_overlay.close()
            self._dwm_overlay = None
        # 60 하드코딩 금지 — 사용자가 설정한 FPS 로 복원
        self._cap.set_fps(self.config.fps)
        self.update()
        logger.info("[확대기 #%d] DWM 모드 해제", self.mag_id)

    def _update_dwm(self) -> None:
        if not self._dwm_overlay or not self._dwm_mode:
            return
        sel      = self.selection_overlay
        src_rect = None
        if sel:
            sr = sel.get_region()
            wr = self.capture_engine.get_window_rect(self.config.target_hwnd)
            if wr[2] > 0:
                src_rect = (sr.x() - wr[0], sr.y() - wr[1],
                            sr.width(), sr.height())
        self._dwm_overlay.update(
            dest=(0, 0, self.width(), self.height()),
            src=src_rect,
            opacity=int(self.windowOpacity() * 255),
        )

    # ── 그리기 ───────────────────────────────────────────────

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        w, h = self.width(), self.height()

        # 배경 및 이미지
        if self._dwm_mode:
            p.fillRect(self.rect(), QColor(10, 10, 10))
        else:
            p.fillRect(self.rect(), QColor(18, 18, 18))
            if self._frame:
                p.drawImage(QRect(0, 0, w, h), self._frame)

        # HUD — 숨김 상태면 아무것도 그리지 않음
        if self._show_hud:
            self._draw_hud(p, w, h)

        # 테두리 없음 — 리사이즈 힌트(우하단 작은 삼각형)만 표시
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 40))
        p.drawPolygon([
            QPoint(w, h - HANDLE), QPoint(w, h), QPoint(w - HANDLE, h)
        ])

    def _draw_hud(self, p: QPainter, w: int, h: int) -> None:
        """좌상단 HUD: 확대기 번호, 배율, FPS, 캡처 방법"""
        method = _METHOD_SHORT.get(
            getattr(self.capture_engine, "_active", "auto"), "GDI"
        )
        if self._dwm_mode:
            txt = f"#{self.mag_id}  DWM"
        else:
            sel = self.selection_overlay
            if sel:
                sr = sel.get_region()
                if sr.width() > 0 and sr.height() > 0:
                    zm  = ((w / sr.width()) + (h / sr.height())) / 2
                    txt = f"#{self.mag_id}  {zm:.1f}x  {self._fps_display}fps  {method}"
                else:
                    txt = f"#{self.mag_id}  {method}"
            else:
                txt = f"#{self.mag_id}  {self._fps_display}fps  {method}"

        font = QFont("Consolas", 8)
        p.setFont(font)
        fm = p.fontMetrics()
        bx = 4;  by = 2
        tw = fm.horizontalAdvance(txt)
        th = fm.height()

        p.fillRect(bx - 2, by - 1, tw + 6, th + 2, QColor(0, 0, 0, 150))
        p.setPen(QColor(220, 220, 220, 210))
        p.drawText(bx, by + th - 2, txt)

    # ── HUD 토글 ─────────────────────────────────────────────

    def toggle_hud(self) -> None:
        self._show_hud       = not self._show_hud
        self.config.show_hud = self._show_hud
        self.update()
        state = "표시" if self._show_hud else "숨김"
        logger.info(f"[확대기 #{self.mag_id}] HUD {state}")

    # ── 리사이즈 방향 ────────────────────────────────────────

    def _get_resize_dir(self, pos: QPoint) -> int:
        x, y = pos.x(), pos.y()
        w, h = self.width(), self.height()
        L = x < HANDLE;  R = x > w - HANDLE
        T = y < HANDLE;  B = y > h - HANDLE
        if T and L: return R_NW
        if T and R: return R_NE
        if B and L: return R_SW
        if B and R: return R_SE
        if T: return R_N
        if B: return R_S
        if L: return R_W
        if R: return R_E
        return R_NONE

    _CURSORS = {
        R_NONE: Qt.CursorShape.SizeAllCursor,
        R_N:    Qt.CursorShape.SizeVerCursor,
        R_S:    Qt.CursorShape.SizeVerCursor,
        R_W:    Qt.CursorShape.SizeHorCursor,
        R_E:    Qt.CursorShape.SizeHorCursor,
        R_NW:   Qt.CursorShape.SizeFDiagCursor,
        R_SE:   Qt.CursorShape.SizeFDiagCursor,
        R_NE:   Qt.CursorShape.SizeBDiagCursor,
        R_SW:   Qt.CursorShape.SizeBDiagCursor,
    }

    # ── 마우스 ───────────────────────────────────────────────

    def mousePressEvent(self, e: QMouseEvent) -> None:
        if self._click_through:
            return
        if e.button() == Qt.MouseButton.LeftButton:
            pos = e.position().toPoint()
            self._resize_dir  = self._get_resize_dir(pos)
            self._drag_start  = e.globalPosition().toPoint()
            self._drag_origin = self.geometry().topLeft()
            self._drag_size   = self.size()
            self._drag_active = True
        elif e.button() == Qt.MouseButton.RightButton:
            self._ctx_menu(e.globalPosition().toPoint())

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        if self._click_through:
            return
        pos = e.position().toPoint()
        if not self._drag_active:
            d = self._get_resize_dir(pos)
            self.setCursor(QCursor(self._CURSORS.get(d, Qt.CursorShape.ArrowCursor)))
            return
        delta = e.globalPosition().toPoint() - self._drag_start
        self._do_resize_move(delta)
        self._save_config()

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        self._drag_active = False
        self._save_config()

    def _do_resize_move(self, delta: QPoint) -> None:
        dx, dy = delta.x(), delta.y()
        r = QRect(self._drag_origin, self._drag_size)
        d = self._resize_dir
        if d == R_NONE:
            self.move(self._drag_origin + delta)
            return
        if d in (R_W, R_NW, R_SW):
            nl = r.left() + dx
            if r.right() - nl >= MIN_W: r.setLeft(nl)
        if d in (R_E, R_NE, R_SE):
            nr = r.right() + dx
            if nr - r.left() >= MIN_W:  r.setRight(nr)
        if d in (R_N, R_NW, R_NE):
            nt = r.top() + dy
            if r.bottom() - nt >= MIN_H: r.setTop(nt)
        if d in (R_S, R_SW, R_SE):
            nb = r.bottom() + dy
            if nb - r.top() >= MIN_H:   r.setBottom(nb)
        self.setGeometry(r)

    def wheelEvent(self, e) -> None:
        if self._click_through:
            return
        shift = e.modifiers() & Qt.KeyboardModifier.ShiftModifier
        step  = 20 if shift else 5
        delta = step if e.angleDelta().y() > 0 else -step
        g     = self.geometry()
        new_w = max(MIN_W, g.width()  + delta)
        new_h = max(MIN_H, g.height() + delta)
        cx    = g.x() + g.width()  // 2
        cy    = g.y() + g.height() // 2
        self.setGeometry(cx - new_w // 2, cy - new_h // 2, new_w, new_h)
        self._save_config()

    # ── 우클릭 메뉴 — 3개 항목만 ─────────────────────────────

    def _ctx_menu(self, pos: QPoint) -> None:
        menu = QMenu(self)
        menu.setStyleSheet(_CTX_STYLE)

        menu.addAction("설정 패널").triggered.connect(self._show_panel)
        menu.addSeparator()
        menu.addAction("대상 창 지정").triggered.connect(self._pick_target_window)
        menu.addAction("크기 직접 입력...").triggered.connect(self._size_input_dialog)
        menu.addSeparator()
        menu.addAction("프리셋으로 저장...").triggered.connect(self._save_preset_from_menu)
        menu.addSeparator()
        menu.addAction("이 확대기 닫기").triggered.connect(self.close)

        menu.exec(pos)

    # ── 설정 패널 ────────────────────────────────────────────

    def _show_panel(self) -> None:
        if self._panel is None:
            from ui.control_panel import ControlPanel
            self._panel = ControlPanel(self)
            self._panel.close_requested.connect(self.close)
        geo = self.geometry()
        self._panel.move(geo.right() + 6, geo.top())
        self._panel.show()
        self._panel.raise_()

    # ── 클릭 투과 ────────────────────────────────────────────

    def toggle_click_through(self) -> None:
        if self._click_through:
            self._disable_click_through()
        else:
            self._enable_click_through()
        self.config.click_through = self._click_through
        self.update()

    def _enable_click_through(self) -> None:
        hwnd = int(self.winId())
        ex   = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        ctypes.windll.user32.SetWindowLongW(
            hwnd, GWL_EXSTYLE, ex | WS_EX_TRANSPARENT | WS_EX_LAYERED
        )
        self._click_through = True
        # Feature 6: 선택 오버레이에도 동기화
        if self.selection_overlay:
            self.selection_overlay.set_click_through(True)
        logger.info(f"[확대기 #{self.mag_id}] 클릭 투과 활성화")

    def _disable_click_through(self) -> None:
        hwnd = int(self.winId())
        ex   = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        ctypes.windll.user32.SetWindowLongW(
            hwnd, GWL_EXSTYLE, ex & ~WS_EX_TRANSPARENT & ~WS_EX_LAYERED
        )
        self._click_through = False
        # Feature 6: 선택 오버레이에도 동기화
        if self.selection_overlay:
            self.selection_overlay.set_click_through(False)
        logger.info(f"[확대기 #{self.mag_id}] 클릭 투과 해제")

    # ── 프리셋 저장 ──────────────────────────────────────────

    def _save_preset_from_menu(self) -> None:
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "프리셋 저장", "프리셋 이름을 입력하세요:")
        if ok and name.strip():
            self.save_preset_requested.emit(name.strip())

    # ── 크기 직접 입력 ────────────────────────────────────────

    def _size_input_dialog(self) -> None:
        from PySide6.QtWidgets import (
            QDialog, QDialogButtonBox, QFormLayout,
            QSpinBox, QGroupBox, QVBoxLayout,
        )
        _DLG_STYLE = """
            QDialog, QWidget { background: #1e1e1e; color: #e0e0e0;
                               font-family: Consolas; font-size: 11px; }
            QGroupBox { border: 1px solid #444; border-radius: 4px;
                        margin-top: 8px; padding-top: 6px; color: #888;
                        font-size: 10px; }
            QSpinBox  { background: #2a2a2a; color: #e0e0e0;
                        border: 1px solid #484848; border-radius: 3px; padding: 3px; }
            QPushButton { background: #2a2a2a; border: 1px solid #484848;
                          border-radius: 4px; padding: 5px 14px; color: #e0e0e0; }
            QPushButton:hover { background: #383838; }
        """
        dlg = QDialog(self)
        dlg.setWindowTitle(f"크기 직접 입력  —  확대기 #{self.mag_id}")
        dlg.setStyleSheet(_DLG_STYLE)
        layout = QVBoxLayout(dlg)
        layout.setSpacing(8)

        # 캡처 영역
        sel_box  = QGroupBox("캡처 영역 (Selection Overlay)")
        sel_form = QFormLayout(sel_box)
        sel_w = QSpinBox(); sel_w.setRange(4, 7680); sel_w.setSuffix(" px")
        sel_h = QSpinBox(); sel_h.setRange(4, 4320); sel_h.setSuffix(" px")
        if self.selection_overlay:
            sel_w.setValue(self.selection_overlay.width())
            sel_h.setValue(self.selection_overlay.height())
        sel_form.addRow("너비:", sel_w)
        sel_form.addRow("높이:", sel_h)
        layout.addWidget(sel_box)

        # 확대기 창
        out_box  = QGroupBox("확대기 창 (Magnifier Window)")
        out_form = QFormLayout(out_box)
        out_w = QSpinBox(); out_w.setRange(20, 7680); out_w.setSuffix(" px")
        out_h = QSpinBox(); out_h.setRange(15, 4320); out_h.setSuffix(" px")
        out_w.setValue(self.width())
        out_h.setValue(self.height())
        out_form.addRow("너비:", out_w)
        out_form.addRow("높이:", out_h)
        layout.addWidget(out_box)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        layout.addWidget(btns)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        if self.selection_overlay:
            g = self.selection_overlay.geometry()
            self.selection_overlay.setGeometry(g.x(), g.y(), sel_w.value(), sel_h.value())
            self.selection_overlay._save_config()
            self.selection_overlay.region_changed.emit(self.selection_overlay.geometry())

        self.resize(out_w.value(), out_h.value())
        self._save_config()

    # ── 대상 창 지정 ─────────────────────────────────────────

    def _pick_target_window(self) -> None:
        """
        전체화면 Qt 오버레이 대신 WH_MOUSE_LL 저수준 마우스 훅을 설치한다.
        훅은 외부 프로세스의 클릭도 수신할 수 있으며, 클릭을 소비하지 않으므로
        대상 창도 정상적으로 클릭 이벤트를 받는다.
        """
        if getattr(self, '_picking_active', False):
            return
        self._picking_active = True

        banner = _PickerBanner()
        banner.cancel_requested.connect(self._cancel_pick)
        banner.show()
        self._picker_banner = banner

        # ESC 키 폴링 타이머 (80 ms 간격)
        self._esc_timer = QTimer(self)
        self._esc_timer.setInterval(80)
        self._esc_timer.timeout.connect(self._check_pick_esc)
        self._esc_timer.start()

        self._install_pick_hook()

    def _install_pick_hook(self) -> None:
        """SetWindowsHookExW(WH_MOUSE_LL) 로 전역 마우스 클릭을 감지한다."""
        WH_MOUSE_LL    = 14
        WM_LBUTTONDOWN = 0x0201
        user32 = ctypes.windll.user32

        # HOOKPROC 서명: (nCode: int, wParam: WPARAM, lParam: LPARAM) → LRESULT
        # 64-bit Windows 에서 LPARAM = LONG_PTR = c_longlong.
        # c_void_p 를 쓰면 Python 이 큰 양수 int 로 받아서 CallNextHookEx 에서
        # OverflowError 가 발생하므로 반드시 c_longlong 을 사용해야 한다.
        HOOKPROC = ctypes.WINFUNCTYPE(
            ctypes.c_long,
            ctypes.c_int, ctypes.c_ulong, ctypes.c_longlong,
        )

        # CallNextHookEx 가 받는 4번째 인자도 c_longlong 으로 명시
        user32.CallNextHookEx.restype  = ctypes.c_long
        user32.CallNextHookEx.argtypes = [
            ctypes.c_void_p,    # hhk
            ctypes.c_int,       # nCode
            ctypes.c_ulong,     # wParam
            ctypes.c_longlong,  # lParam
        ]

        def _hook_proc(nCode, wParam, lParam):
            if nCode >= 0 and wParam == WM_LBUTTONDOWN:
                # 훅 핸들을 먼저 꺼내고 None 으로 초기화한 뒤 해제
                h = self._pick_hook
                self._pick_hook     = None
                self._pick_hookproc = None
                user32.UnhookWindowsHookEx(h)

                # 현재 커서 위치의 루트 창 HWND 획득
                pt = ctypes.wintypes.POINT()
                user32.GetCursorPos(ctypes.byref(pt))
                hwnd   = user32.WindowFromPoint(pt)
                root   = user32.GetAncestor(hwnd, 2)  # GA_ROOT = 2
                target = root if root else hwnd

                # Qt 이벤트 루프에서 안전하게 마무리 (훅 콜백 스택 탈출)
                QTimer.singleShot(0, lambda t=target: self._finish_pick(t))

                # 클릭을 소비하지 않고 다음 훅으로 전달
                return user32.CallNextHookEx(0, nCode, wParam, lParam)
            return user32.CallNextHookEx(
                self._pick_hook or 0, nCode, wParam, lParam
            )

        proc = HOOKPROC(_hook_proc)
        self._pick_hookproc = proc          # GC 방지 — 반드시 인스턴스 변수로 유지
        self._pick_hook = user32.SetWindowsHookExW(WH_MOUSE_LL, proc, None, 0)
        if not self._pick_hook:
            err = ctypes.windll.kernel32.GetLastError()
            logger.error(f"[확대기 #{self.mag_id}] WH_MOUSE_LL 훅 설치 실패 (오류 {err})")
            self._cleanup_pick()

    def _finish_pick(self, hwnd: int) -> None:
        """훅이 클릭을 감지했을 때 호출 — 정리 후 창 등록."""
        self._cleanup_pick()
        self._on_window_picked(hwnd)

    def _cancel_pick(self) -> None:
        """배너의 취소 버튼 또는 ESC 키로 중단."""
        if getattr(self, '_pick_hook', None):
            ctypes.windll.user32.UnhookWindowsHookEx(self._pick_hook)
            self._pick_hook     = None
            self._pick_hookproc = None
        self._cleanup_pick()

    def _check_pick_esc(self) -> None:
        """ESC 키 상태를 폴링하여 창 선택을 취소한다."""
        VK_ESCAPE = 0x1B
        if ctypes.windll.user32.GetAsyncKeyState(VK_ESCAPE) & 0x8000:
            self._cancel_pick()

    def _cleanup_pick(self) -> None:
        """배너 위젯과 타이머를 안전하게 해제한다."""
        self._picking_active = False
        timer = getattr(self, '_esc_timer', None)
        if timer:
            timer.stop()
            timer.deleteLater()
            self._esc_timer = None
        banner = getattr(self, '_picker_banner', None)
        if banner:
            banner.close()
            banner.deleteLater()
            self._picker_banner = None

    def _on_window_picked(self, hwnd: int) -> None:
        if not hwnd:
            return
        # 자기 자신의 출력창이나 선택 오버레이는 무시
        own_hwnds = {int(self.winId())}
        if self.selection_overlay:
            own_hwnds.add(int(self.selection_overlay.winId()))
        if hwnd in own_hwnds:
            return
        title    = self.capture_engine.get_window_title(hwnd)
        old_hwnd = self.config.target_hwnd
        if old_hwnd != hwnd:
            if old_hwnd:
                self.capture_engine.release_wgc(old_hwnd)
            if hwnd:
                self.capture_engine.acquire_wgc(hwnd)
        self.config.target_hwnd = hwnd
        if self._dwm_mode:
            self._stop_dwm()
            self._start_dwm()
        if self.selection_overlay:
            self._update_region(self.selection_overlay.get_region())
        logger.info(f"[확대기 #{self.mag_id}] 대상 창 변경 → '{title}'")

    # ── 리사이즈 / 닫기 ──────────────────────────────────────

    def resizeEvent(self, e: QResizeEvent) -> None:
        self._save_config()
        if self._dwm_mode:
            self._update_dwm()
        super().resizeEvent(e)

    def closeEvent(self, e) -> None:
        # 창 선택 도중 닫힐 경우 훅과 배너를 정리
        if getattr(self, '_picking_active', False):
            self._cancel_pick()
        self._stop_dwm()
        self._cap.stop()
        self._fps_timer.stop()
        if self._panel:
            self._panel.close()
        # Bug 2: hide() 로 즉시 숨기고 deleteLater() 로 Qt 이벤트 루프에서 안전하게 삭제
        if self.selection_overlay:
            self.selection_overlay.hide()
            self.selection_overlay.deleteLater()
            self.selection_overlay = None
        # 캡처 엔진의 hwnd 참조 해제 — 0 이 되면 WGC 세션이 즉시 종료된다.
        if self.config.target_hwnd:
            try:
                self.capture_engine.release_wgc(self.config.target_hwnd)
            except Exception:
                pass
        self._save_config()
        # Bug 1: Manager 에 알려 _magnifiers 에서 제거하고 트레이 목록을 갱신
        self.closed.emit()
        super().closeEvent(e)
