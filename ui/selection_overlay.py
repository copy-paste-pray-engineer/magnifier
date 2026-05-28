"""
확대 영역 지정 박스 (SelectionOverlay)
- 빨간 반투명 테두리
- 드래그 이동
- 8방향 리사이즈 핸들
- 키보드 미세조정 (←↑↓→, Shift+방향키)
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional
import ctypes

from PySide6.QtWidgets import QWidget, QSizeGrip
from PySide6.QtCore import (
    Qt, QRect, QPoint, QSize, QTimer, Signal, QRectF
)
from PySide6.QtGui import (
    QPainter, QColor, QPen, QBrush, QFont, QPainterPath,
    QKeyEvent, QMouseEvent, QCursor
)

from utils.settings import MagnifierConfig

if TYPE_CHECKING:
    from ui.magnifier_window import MagnifierWindow

# Win32 리사이즈 방향 상수
RESIZE_NONE = 0
RESIZE_N = 1
RESIZE_S = 2
RESIZE_W = 3
RESIZE_E = 4
RESIZE_NW = 5
RESIZE_NE = 6
RESIZE_SW = 7
RESIZE_SE = 8

HANDLE_SIZE = 10   # 리사이즈 핸들 크기
BORDER_WIDTH = 3   # 테두리 두께
MIN_SIZE = 4       # 최소 크기 (Feature 3: 4px 까지 허용)

# Win32 클릭 투과 스타일 (Feature 6)
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED     = 0x00080000
GWL_EXSTYLE       = -20


class SelectionOverlay(QWidget):
    """
    확대할 원본 영역을 지정하는 반투명 오버레이 창.
    드래그로 이동, 테두리 드래그로 리사이즈.
    """

    region_changed = Signal(QRect)  # 영역 변경 시그널

    def __init__(
        self,
        mag_id: int,
        config: MagnifierConfig,
        output_window: "MagnifierWindow",
        parent=None,
    ):
        super().__init__(parent)
        self.mag_id = mag_id
        self.config = config
        self.output_window = output_window

        self._drag_start: Optional[QPoint] = None
        self._drag_origin: Optional[QRect] = None
        self._resize_dir = RESIZE_NONE
        self._is_dragging = False
        self._click_through = False  # Feature 6

        self._setup_window()
        self._apply_config()

    def _setup_window(self):
        """윈도우 플래그 설정"""
        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool  # 작업표시줄에 안 나타남
            | Qt.WindowType.X11BypassWindowManagerHint
        )
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)

        # 최소 크기
        self.setMinimumSize(MIN_SIZE, MIN_SIZE)

    def _apply_config(self):
        """설정에서 위치/크기 복원"""
        self.setGeometry(
            self.config.selection_x,
            self.config.selection_y,
            self.config.selection_w,
            self.config.selection_h,
        )

    def _save_config(self):
        """현재 위치/크기를 설정에 저장"""
        geo = self.geometry()
        self.config.selection_x = geo.x()
        self.config.selection_y = geo.y()
        self.config.selection_w = geo.width()
        self.config.selection_h = geo.height()

    # ─── 그리기 ──────────────────────────────────────────

    def paintEvent(self, event):
        # Feature 6: 클릭 투과 모드일 때는 아무것도 그리지 않음 → WA_TranslucentBackground 덕분에 완전 투명
        if self._click_through:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()
        rect = QRectF(
            BORDER_WIDTH / 2,
            BORDER_WIDTH / 2,
            w - BORDER_WIDTH,
            h - BORDER_WIDTH,
        )

        # 반투명 배경 (약하게)
        painter.fillRect(self.rect(), QColor(255, 80, 80, 25))

        # 빨간 테두리
        pen = QPen(QColor(255, 60, 60, 220), BORDER_WIDTH)
        pen.setStyle(Qt.PenStyle.SolidLine)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect)

        # 리사이즈 핸들 (8방향)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 60, 60, 200))
        for hx, hy in self._handle_positions():
            painter.drawRect(
                hx - HANDLE_SIZE // 2,
                hy - HANDLE_SIZE // 2,
                HANDLE_SIZE,
                HANDLE_SIZE,
            )

        # 레이블
        painter.setPen(QColor(255, 220, 220, 200))
        font = QFont("Consolas", 9)
        painter.setFont(font)
        label = f"#{self.mag_id}  {w}×{h}"
        painter.drawText(BORDER_WIDTH + 4, BORDER_WIDTH + 13, label)

    def _handle_positions(self):
        """8방향 핸들 중심 좌표"""
        w, h = self.width(), self.height()
        cx, cy = w // 2, h // 2
        return [
            (0, 0), (cx, 0), (w, 0),
            (0, cy),         (w, cy),
            (0, h), (cx, h), (w, h),
        ]

    def _get_resize_dir(self, pos: QPoint) -> int:
        """마우스 위치로 리사이즈 방향 결정"""
        w, h = self.width(), self.height()
        x, y = pos.x(), pos.y()
        hs = HANDLE_SIZE + 4

        near_left   = x < hs
        near_right  = x > w - hs
        near_top    = y < hs
        near_bottom = y > h - hs

        if near_top and near_left:   return RESIZE_NW
        if near_top and near_right:  return RESIZE_NE
        if near_bottom and near_left: return RESIZE_SW
        if near_bottom and near_right: return RESIZE_SE
        if near_top:    return RESIZE_N
        if near_bottom: return RESIZE_S
        if near_left:   return RESIZE_W
        if near_right:  return RESIZE_E
        return RESIZE_NONE

    def _cursor_for_dir(self, direction: int) -> Qt.CursorShape:
        return {
            RESIZE_N:  Qt.CursorShape.SizeVerCursor,
            RESIZE_S:  Qt.CursorShape.SizeVerCursor,
            RESIZE_W:  Qt.CursorShape.SizeHorCursor,
            RESIZE_E:  Qt.CursorShape.SizeHorCursor,
            RESIZE_NW: Qt.CursorShape.SizeFDiagCursor,
            RESIZE_SE: Qt.CursorShape.SizeFDiagCursor,
            RESIZE_NE: Qt.CursorShape.SizeBDiagCursor,
            RESIZE_SW: Qt.CursorShape.SizeBDiagCursor,
            RESIZE_NONE: Qt.CursorShape.SizeAllCursor,
        }.get(direction, Qt.CursorShape.ArrowCursor)

    # ─── 마우스 이벤트 ────────────────────────────────────

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = event.globalPosition().toPoint()
            self._drag_origin = self.geometry()
            self._resize_dir = self._get_resize_dir(event.position().toPoint())
            self._is_dragging = True
            self.setFocus()

    def mouseMoveEvent(self, event: QMouseEvent):
        pos = event.position().toPoint()

        if not self._is_dragging:
            # 커서 업데이트
            direction = self._get_resize_dir(pos)
            self.setCursor(QCursor(self._cursor_for_dir(direction)))
            return

        global_pos = event.globalPosition().toPoint()
        delta = global_pos - self._drag_start
        origin = self._drag_origin

        if self._resize_dir == RESIZE_NONE:
            # 이동
            new_x = origin.x() + delta.x()
            new_y = origin.y() + delta.y()
            self.move(new_x, new_y)
        else:
            # 리사이즈
            r = QRect(origin)
            self._apply_resize(r, delta, self._resize_dir)
            self.setGeometry(r)

        self._save_config()
        self.update()
        self.region_changed.emit(self.geometry())

    def mouseReleaseEvent(self, event: QMouseEvent):
        self._is_dragging = False
        self._drag_start = None
        self._drag_origin = None
        self._save_config()

    def _apply_resize(self, rect: QRect, delta: QPoint, direction: int):
        """리사이즈 방향에 따라 rect 수정"""
        dx, dy = delta.x(), delta.y()
        d = direction

        if d in (RESIZE_W, RESIZE_NW, RESIZE_SW):
            new_left = rect.left() + dx
            if rect.right() - new_left >= MIN_SIZE:
                rect.setLeft(new_left)

        if d in (RESIZE_E, RESIZE_NE, RESIZE_SE):
            new_right = rect.right() + dx
            if new_right - rect.left() >= MIN_SIZE:
                rect.setRight(new_right)

        if d in (RESIZE_N, RESIZE_NW, RESIZE_NE):
            new_top = rect.top() + dy
            if rect.bottom() - new_top >= MIN_SIZE:
                rect.setTop(new_top)

        if d in (RESIZE_S, RESIZE_SW, RESIZE_SE):
            new_bottom = rect.bottom() + dy
            if new_bottom - rect.top() >= MIN_SIZE:
                rect.setBottom(new_bottom)

    # ─── 키보드 이벤트 ────────────────────────────────────

    def keyPressEvent(self, event: QKeyEvent):
        key = event.key()
        shift = event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        step = 10 if shift else 1

        geo = self.geometry()

        if key == Qt.Key.Key_Left:
            self.move(geo.x() - step, geo.y())
        elif key == Qt.Key.Key_Right:
            self.move(geo.x() + step, geo.y())
        elif key == Qt.Key.Key_Up:
            self.move(geo.x(), geo.y() - step)
        elif key == Qt.Key.Key_Down:
            self.move(geo.x(), geo.y() + step)
        elif key == Qt.Key.Key_Escape:
            # 포커스 해제
            self.clearFocus()
        else:
            super().keyPressEvent(event)

        self._save_config()
        self.region_changed.emit(self.geometry())

    # ─── 더블클릭 ──────────────────────────────────────────

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        """더블클릭: 출력창 포커스"""
        if self.output_window:
            self.output_window.activateWindow()
            self.output_window.raise_()

    # ─── 휠 리사이즈 (Feature 4) ──────────────────────────
    def wheelEvent(self, event):
        """스크롤: ±1px / Shift+스크롤: ±10px  (중심 고정 리사이즈)"""
        shift = event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        step  = 10 if shift else 1
        delta = step if event.angleDelta().y() > 0 else -step

        geo   = self.geometry()
        new_w = max(MIN_SIZE, geo.width()  + delta)
        new_h = max(MIN_SIZE, geo.height() + delta)
        # 중심을 기준으로 양쪽으로 같이 늘이거나 줄임
        cx    = geo.x() + geo.width()  // 2
        cy    = geo.y() + geo.height() // 2
        self.setGeometry(cx - new_w // 2, cy - new_h // 2, new_w, new_h)
        self._save_config()
        self.region_changed.emit(self.geometry())

    # ─── 클릭 투과 (Feature 6) ───────────────────────────
    def set_click_through(self, enabled: bool):
        """출력창 클릭 투과 토글에 맞춰 선택 오버레이도 동기화."""
        self._click_through = enabled
        hwnd = int(self.winId())
        ex   = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        if enabled:
            ctypes.windll.user32.SetWindowLongW(
                hwnd, GWL_EXSTYLE, ex | WS_EX_TRANSPARENT | WS_EX_LAYERED
            )
        else:
            ctypes.windll.user32.SetWindowLongW(
                hwnd, GWL_EXSTYLE, ex & ~WS_EX_TRANSPARENT & ~WS_EX_LAYERED
            )
        self.update()  # paintEvent 재호출 → 테두리 표시/숨김 반영

    def get_region(self) -> QRect:
        """현재 선택 영역 (화면 절대 좌표)"""
        return self.geometry()
