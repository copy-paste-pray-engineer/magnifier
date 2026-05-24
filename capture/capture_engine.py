"""
CaptureEngine — 통합 캡처 엔진

지원 방법:
  auto  → WGC(winrt 설치 시) > BitBlt(기본 fallback)
  wgc   → Windows Graphics Capture API  (GPU, 비활성 창 완벽 지원)
  bitblt→ GDI BitBlt  (화면에 보이는 영역, 추가 설치 불필요)

사용하지 않는 방법:
  PrintWindow — DX 전용 창에서 검은 화면이 자주 발생하고
                WGC 대비 이점이 없어 제외.
  MSS/DXGI   — 화면 전체 캡처 후 크롭 방식이라 개별 창 캡처 목적과 맞지 않아 제외.
                BitBlt이 설치 없이도 동작하며 호환성이 더 높음.

DWM 썸네일:
  이 엔진에서는 별도 방법으로 노출하지 않습니다.
  MagnifierWindow 내부의 DWMOverlay 세션이 직접 처리합니다.
  (DwmRegisterThumbnail 은 픽셀 복사 없이 GPU 가 직접 렌더하기 때문에
   캡처→numpy 파이프라인과 구조가 근본적으로 다릅니다.)
"""
import ctypes
import ctypes.wintypes as wintypes
from ctypes import windll, byref, c_int
from typing import Optional, Tuple, Dict
import numpy as np

SRCCOPY    = 0x00CC0020
CAPTUREBLT = 0x40000000
DIB_RGB_COLORS = 0
BI_RGB     = 0


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize",          wintypes.DWORD), ("biWidth",       c_int),
        ("biHeight",        c_int),          ("biPlanes",      wintypes.WORD),
        ("biBitCount",      wintypes.WORD),  ("biCompression", wintypes.DWORD),
        ("biSizeImage",     wintypes.DWORD), ("biXPelsPerMeter", c_int),
        ("biYPelsPerMeter", c_int),          ("biClrUsed",     wintypes.DWORD),
        ("biClrImportant",  wintypes.DWORD),
    ]

class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]

class RECT(ctypes.Structure):
    _fields_ = [("left", c_int), ("top", c_int),
                ("right", c_int), ("bottom", c_int)]


class _GDI:
    """BitBlt 기반 화면 캡처 (내부용)"""

    def __init__(self):
        self.u = windll.user32
        self.g = windll.gdi32

    def capture_screen_region(self, x: int, y: int, w: int, h: int
                               ) -> Optional[np.ndarray]:
        hdc_scr = self.u.GetDC(None)
        if not hdc_scr:
            return None
        hdc_mem = self.g.CreateCompatibleDC(hdc_scr)
        hbmp    = self.g.CreateCompatibleBitmap(hdc_scr, w, h)
        old     = self.g.SelectObject(hdc_mem, hbmp)
        self.g.BitBlt(hdc_mem, 0, 0, w, h, hdc_scr, x, y, SRCCOPY | CAPTUREBLT)
        frame   = self._read(hdc_mem, hbmp, w, h)
        self.g.SelectObject(hdc_mem, old)
        self.g.DeleteObject(hbmp)
        self.g.DeleteDC(hdc_mem)
        self.u.ReleaseDC(None, hdc_scr)
        return frame

    def capture_window_region(self, hwnd: int, rx: int, ry: int,
                               rw: int, rh: int) -> Optional[np.ndarray]:
        """창 전체를 BitBlt 으로 캡처한 뒤 원하는 영역을 크롭"""
        rect = RECT()
        self.u.GetWindowRect(hwnd, byref(rect))
        ww = rect.right - rect.left
        wh = rect.bottom - rect.top
        if ww <= 0 or wh <= 0:
            return None

        hdc_win = self.u.GetDC(hwnd)
        if not hdc_win:
            return None
        hdc_mem = self.g.CreateCompatibleDC(hdc_win)
        hbmp    = self.g.CreateCompatibleBitmap(hdc_win, ww, wh)
        old     = self.g.SelectObject(hdc_mem, hbmp)
        self.g.BitBlt(hdc_mem, 0, 0, ww, wh, hdc_win, 0, 0, SRCCOPY | CAPTUREBLT)
        full = self._read(hdc_mem, hbmp, ww, wh)
        self.g.SelectObject(hdc_mem, old)
        self.g.DeleteObject(hbmp)
        self.g.DeleteDC(hdc_mem)
        self.u.ReleaseDC(hwnd, hdc_win)

        if full is None:
            return None
        cx = rx - rect.left;  cy = ry - rect.top
        fh, fw = full.shape[:2]
        x1, y1 = max(0, cx), max(0, cy)
        x2, y2 = min(fw, cx + rw), min(fh, cy + rh)
        return full[y1:y2, x1:x2] if x2 > x1 and y2 > y1 else None

    def _read(self, hdc_mem, hbmp, w, h) -> Optional[np.ndarray]:
        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize        = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth       = w
        bmi.bmiHeader.biHeight      = -h
        bmi.bmiHeader.biPlanes      = 1
        bmi.bmiHeader.biBitCount    = 32
        bmi.bmiHeader.biCompression = BI_RGB
        buf   = (ctypes.c_char * (w * h * 4))()
        lines = self.g.GetDIBits(hdc_mem, hbmp, 0, h, buf, byref(bmi), DIB_RGB_COLORS)
        if lines <= 0:
            return None
        arr = np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 4)
        return arr[:, :, 2::-1].copy()   # BGRA → RGB


def _check_wgc() -> bool:
    try:
        from capture.wgc_capture import wgc_available
        return wgc_available()
    except Exception:
        return False


class CaptureEngine:
    """
    캡처 방법:
      auto   — WGC 가능하면 WGC, 아니면 BitBlt
      wgc    — Windows Graphics Capture (winrt 패키지 필요)
      bitblt — GDI BitBlt  (추가 설치 불필요, 항상 동작)
    """

    def __init__(self, method: str = "auto"):
        self.method = method
        self._gdi   = _GDI()
        self._wgc_sessions: Dict[int, object] = {}

        if method == "auto":
            self._active = "wgc" if _check_wgc() else "bitblt"
        else:
            self._active = method if method in ("wgc", "bitblt") else "bitblt"

        print(f"캡처 방법: {self._active.upper()}")

    # ── 공개 API ────────────────────────────────────────────────

    def capture(self, hwnd: int,
                region_x: int, region_y: int,
                region_w: int, region_h: int) -> Optional[np.ndarray]:
        if region_w <= 0 or region_h <= 0:
            return None

        if self._active == "wgc" and hwnd:
            frame = self._capture_wgc(hwnd, region_x, region_y, region_w, region_h)
            if frame is not None:
                return frame
            # WGC 실패 시 자동 fallback

        # BitBlt
        if hwnd and windll.user32.IsWindow(hwnd):
            return self._gdi.capture_window_region(
                hwnd, region_x, region_y, region_w, region_h)
        return self._gdi.capture_screen_region(region_x, region_y, region_w, region_h)

    def capture_screen_region(self, x: int, y: int, w: int, h: int
                               ) -> Optional[np.ndarray]:
        return self._gdi.capture_screen_region(x, y, w, h)

    # ── WGC 내부 ────────────────────────────────────────────────

    def _capture_wgc(self, hwnd, rx, ry, rw, rh) -> Optional[np.ndarray]:
        try:
            from capture.wgc_capture import WGCSession
            if hwnd not in self._wgc_sessions:
                sess = WGCSession(hwnd)
                if not sess.ok:
                    return None
                self._wgc_sessions[hwnd] = sess
            sess  = self._wgc_sessions[hwnd]
            frame = sess.get_latest_frame()
            if frame is None:
                return None
            wr = RECT()
            windll.user32.GetWindowRect(hwnd, byref(wr))
            cx, cy = rx - wr.left, ry - wr.top
            fh, fw = frame.shape[:2]
            x1, y1 = max(0, cx), max(0, cy)
            x2, y2 = min(fw, cx + rw), min(fh, cy + rh)
            return frame[y1:y2, x1:x2] if x2 > x1 and y2 > y1 else None
        except Exception as e:
            print(f"WGC 캡처 오류: {e}")
            return None

    # ── 유틸 ────────────────────────────────────────────────────

    def get_window_at_cursor(self) -> int:
        pt = wintypes.POINT()
        windll.user32.GetCursorPos(byref(pt))
        hwnd = windll.user32.WindowFromPoint(pt)
        root = windll.user32.GetAncestor(hwnd, 2)
        return root if root else hwnd

    def get_window_rect(self, hwnd: int) -> Tuple[int, int, int, int]:
        r = RECT()
        windll.user32.GetWindowRect(hwnd, byref(r))
        return r.left, r.top, r.right - r.left, r.bottom - r.top

    def get_window_title(self, hwnd: int) -> str:
        buf = ctypes.create_unicode_buffer(256)
        windll.user32.GetWindowTextW(hwnd, buf, 256)
        return buf.value

    def release_wgc_session(self, hwnd: int):
        sess = self._wgc_sessions.pop(hwnd, None)
        if sess:
            try: sess.close()
            except Exception: pass

    def close(self):
        for sess in self._wgc_sessions.values():
            try: sess.close()
            except Exception: pass
        self._wgc_sessions.clear()
