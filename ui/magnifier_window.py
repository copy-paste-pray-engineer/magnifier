"""
확대 출력창 — 실시간 렌더링, DWM 오버레이, 설정 패널 연동
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional
import ctypes
import time

import numpy as np
from PySide6.QtWidgets import QWidget, QMenu, QAction
from PySide6.QtCore import (
    Qt, QRect, QPoint, QSize, QTimer, QThread, QMutex, QMutexLocker, Signal
)
from PySide6.QtGui import (
    QPainter, QColor, QImage,
    QMouseEvent, QFont, QCursor, QResizeEvent,
)

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
MIN_W  = 80
MIN_H  = 60

# HUD 캡처 방법 약어
_METHOD_SHORT = {"auto": "AUTO", "wgc": "WGC", "bitblt": "GDI"}

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

    def __init__(self, engine: CaptureEngine, config: MagnifierConfig):
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

    def set_region(self, x, y, w, h, hwnd=0):
        with QMutexLocker(self._mutex):
            self._x = x;  self._y = y
            self._w = max(1, w);  self._h = max(1, h)
            self._hwnd = hwnd

    def set_fps(self, fps: int):
        self._frame_interval = 1.0 / max(1, min(240, fps))

    def run(self):
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

    def stop(self):
        self._running = False
        self.wait(2000)


# ── 출력창 ────────────────────────────────────────────────────
class MagnifierWindow(QWidget):

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

        self._fps_timer = QTimer(self)
        self._fps_timer.setInterval(1000)
        self._fps_timer.timeout.connect(self._tick_fps)
        self._fps_timer.start()

        self._dwm_timer = QTimer(self)
        self._dwm_timer.setInterval(16)
        self._dwm_timer.timeout.connect(self._update_dwm)

    # ── 초기화 ───────────────────────────────────────────────

    def _setup_window(self):
        flags = (Qt.WindowType.FramelessWindowHint
                 | Qt.WindowType.WindowStaysOnTopHint
                 | Qt.WindowType.Tool)
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setMouseTracking(True)
        self.setMinimumSize(MIN_W, MIN_H)

    def _apply_config(self):
        self.setGeometry(self.config.output_x, self.config.output_y,
                         self.config.output_w, self.config.output_h)
        self.setWindowOpacity(self.config.opacity)
        if self.config.click_through:
            self._enable_click_through()

    def _save_config(self):
        g = self.geometry()
        self.config.output_x      = g.x()
        self.config.output_y      = g.y()
        self.config.output_w      = g.width()
        self.config.output_h      = g.height()
        self.config.opacity       = self.windowOpacity()
        self.config.click_through = self._click_through
        self.config.show_hud      = self._show_hud

    def set_selection_overlay(self, overlay: SelectionOverlay):
        self.selection_overlay = overlay
        overlay.region_changed.connect(self._on_region_changed)
        self._update_region(overlay.get_region())

    # ── 캡처 ─────────────────────────────────────────────────

    def _on_region_changed(self, rect: QRect):
        self._update_region(rect)
        if self._dwm_mode:
            self._update_dwm()

    def _update_region(self, rect: QRect):
        self._cap.set_region(rect.x(), rect.y(),
                             rect.width(), rect.height(),
                             self.config.target_hwnd)

    def _on_frame(self, frame: np.ndarray):
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

    def _tick_fps(self):
        self._fps_display = self._fps_count
        self._fps_count   = 0

    # ── DWM 모드 ─────────────────────────────────────────────

    def set_dwm_mode(self, enabled: bool):
        if enabled == self._dwm_mode:
            return
        self._dwm_mode = enabled
        if enabled:
            self._start_dwm()
        else:
            self._stop_dwm()

    def _start_dwm(self):
        hwnd_src  = self.config.target_hwnd
        hwnd_dest = int(self.winId())
        if not hwnd_src:
            print(f"[확대기 #{self.mag_id}] 대상 창이 지정되지 않아 DWM 모드를 켤 수 없습니다.")
            self._dwm_mode = False
            return
        from ui.dwm_overlay import DWMOverlay
        self._dwm_overlay = DWMOverlay(hwnd_src, hwnd_dest)
        if not self._dwm_overlay.ok:
            print(f"[확대기 #{self.mag_id}] DWM 썸네일 등록 실패 — 캡처 모드를 유지합니다.")
            self._dwm_overlay = None
            self._dwm_mode    = False
            return
        self._cap.set_fps(1)
        self._dwm_timer.start()
        self.update()
        print(f"[확대기 #{self.mag_id}] DWM 모드 활성화")

    def _stop_dwm(self):
        self._dwm_timer.stop()
        if self._dwm_overlay:
            self._dwm_overlay.close()
            self._dwm_overlay = None
        self._cap.set_fps(60)
        self.update()
        print(f"[확대기 #{self.mag_id}] DWM 모드 해제")

    def _update_dwm(self):
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

    def paintEvent(self, event):
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

    def _draw_hud(self, p: QPainter, w: int, h: int):
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

    def toggle_hud(self):
        self._show_hud       = not self._show_hud
        self.config.show_hud = self._show_hud
        self.update()
        state = "표시" if self._show_hud else "숨김"
        print(f"[확대기 #{self.mag_id}] HUD {state}")

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

    def mousePressEvent(self, e: QMouseEvent):
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

    def mouseMoveEvent(self, e: QMouseEvent):
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

    def mouseReleaseEvent(self, e: QMouseEvent):
        self._drag_active = False
        self._save_config()

    def _do_resize_move(self, delta: QPoint):
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

    def wheelEvent(self, e):
        if self._click_through:
            return
        dy = e.angleDelta().y()
        op = max(0.1, min(1.0, self.windowOpacity() + dy / 1200.0))
        self.setWindowOpacity(op)
        self.config.opacity = op
        self.update()

    # ── 우클릭 메뉴 — 3개 항목만 ─────────────────────────────

    def _ctx_menu(self, pos: QPoint):
        menu = QMenu(self)
        menu.setStyleSheet(_CTX_STYLE)

        menu.addAction("설정 패널").triggered.connect(self._show_panel)
        menu.addSeparator()
        menu.addAction("대상 창 지정").triggered.connect(self._pick_target_window)
        menu.addSeparator()
        menu.addAction("이 확대기 닫기").triggered.connect(self.close)

        menu.exec(pos)

    # ── 설정 패널 ────────────────────────────────────────────

    def _show_panel(self):
        if self._panel is None:
            from ui.control_panel import ControlPanel
            self._panel = ControlPanel(self)
            self._panel.close_requested.connect(self.close)
        geo = self.geometry()
        self._panel.move(geo.right() + 6, geo.top())
        self._panel.show()
        self._panel.raise_()

    # ── 클릭 투과 ────────────────────────────────────────────

    def toggle_click_through(self):
        if self._click_through:
            self._disable_click_through()
        else:
            self._enable_click_through()
        self.config.click_through = self._click_through
        self.update()

    def _enable_click_through(self):
        hwnd = int(self.winId())
        ex   = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        ctypes.windll.user32.SetWindowLongW(
            hwnd, GWL_EXSTYLE, ex | WS_EX_TRANSPARENT | WS_EX_LAYERED
        )
        self._click_through = True
        print(f"[확대기 #{self.mag_id}] 클릭 투과 활성화")

    def _disable_click_through(self):
        hwnd = int(self.winId())
        ex   = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        ctypes.windll.user32.SetWindowLongW(
            hwnd, GWL_EXSTYLE, ex & ~WS_EX_TRANSPARENT & ~WS_EX_LAYERED
        )
        self._click_through = False
        print(f"[확대기 #{self.mag_id}] 클릭 투과 해제")

    # ── 대상 창 지정 ─────────────────────────────────────────

    def _pick_target_window(self):
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.information(
            self, "대상 창 지정",
            "확인을 누른 뒤 3초 안에 캡처할 창 위로 마우스를 올려두세요.\n"
            "해당 창이 새 대상으로 지정됩니다."
        )
        QTimer.singleShot(3000, self._capture_cursor_window)

    def _capture_cursor_window(self):
        hwnd  = self.capture_engine.get_window_at_cursor()
        title = self.capture_engine.get_window_title(hwnd)
        # 이전 hwnd 의 참조 해제 → 새 hwnd 등록
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
        print(f"[확대기 #{self.mag_id}] 대상 창 변경 → '{title}'")

    # ── 리사이즈 / 닫기 ──────────────────────────────────────

    def resizeEvent(self, e: QResizeEvent):
        self._save_config()
        if self._dwm_mode:
            self._update_dwm()
        super().resizeEvent(e)

    def closeEvent(self, e):
        self._stop_dwm()
        self._cap.stop()
        self._fps_timer.stop()
        if self._panel:
            self._panel.close()
        if self.selection_overlay:
            self.selection_overlay.close()
        # 캡처 엔진의 hwnd 참조 해제 — 0 이 되면 WGC 세션이 즉시 종료된다.
        if self.config.target_hwnd:
            try:
                self.capture_engine.release_wgc(self.config.target_hwnd)
            except Exception:
                pass
        self._save_config()
        super().closeEvent(e)
