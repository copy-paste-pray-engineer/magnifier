"""
CaptureEngine — 통합 캡처 엔진

지원 방법:
  auto        → printwindow 와 동일 (기본값)
  printwindow → PrintWindow(PW_RENDERFULLCONTENT). DWM 합성 프레임 복사.
                DX/GPU 창·비활성 창 지원, 노란 테두리 없음, 추가 설치 불필요.
  wgc         → Windows Graphics Capture (순수 ctypes COM, GPU 가속).
                실패 시 printwindow 로 폴백.
  bitblt      → GDI BitBlt. 항상 동작하지만 DX 창은 검은 화면.

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
import logging
import time
from ctypes import windll, byref, c_int, c_void_p
from typing import Optional, Tuple, Dict
import numpy as np

logger = logging.getLogger(__name__)

# WGC 세션 생성 실패 후 재시도까지 대기 시간 (초).
# 창 최소화 등 일시적 실패가 영구 차단되지 않도록 한다.
_WGC_RETRY_SEC = 5.0

SRCCOPY    = 0x00CC0020
CAPTUREBLT = 0x40000000
DIB_RGB_COLORS = 0
BI_RGB     = 0

# PrintWindow 플래그 (Windows 8.1+)
# DWM 합성 프레임을 그대로 복사 → DX/GPU 창 백그라운드 캡처 가능
PW_RENDERFULLCONTENT = 0x00000002


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

    def __init__(self) -> None:
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

    def capture_window_printwindow(self, hwnd: int, rx: int, ry: int,
                                   rw: int, rh: int) -> Optional[np.ndarray]:
        """
        PrintWindow(PW_RENDERFULLCONTENT) — DWM 합성 프레임을 HDC 로 복사.
        DirectX / OpenGL / GPU 가속 창을 비활성(백그라운드) 상태에서도 캡처.
        노란 테두리(WGC 캡처 표시자) 없음. Windows 8.1+ 에서 동작.
        """
        rect = RECT()
        self.u.GetWindowRect(hwnd, byref(rect))
        ww = rect.right  - rect.left
        wh = rect.bottom - rect.top
        if ww <= 0 or wh <= 0:
            return None

        hdc_scr = self.u.GetDC(None)       # 화면 DC — 색상 형식 기준
        if not hdc_scr:
            return None
        hdc_mem = self.g.CreateCompatibleDC(hdc_scr)
        hbmp    = self.g.CreateCompatibleBitmap(hdc_scr, ww, wh)
        old     = self.g.SelectObject(hdc_mem, hbmp)

        # DWM compositor 에게 창 내용을 hdc_mem 에 그리도록 요청
        ok    = self.u.PrintWindow(hwnd, hdc_mem, PW_RENDERFULLCONTENT)
        frame = self._read(hdc_mem, hbmp, ww, wh) if ok else None

        self.g.SelectObject(hdc_mem, old)
        self.g.DeleteObject(hbmp)
        self.g.DeleteDC(hdc_mem)
        self.u.ReleaseDC(None, hdc_scr)

        if frame is None:
            return None
        cx = rx - rect.left;  cy = ry - rect.top
        fh, fw = frame.shape[:2]
        x1, y1 = max(0, cx), max(0, cy)
        x2, y2 = min(fw, cx + rw), min(fh, cy + rh)
        return frame[y1:y2, x1:x2] if x2 > x1 and y2 > y1 else None

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

    def _read(self, hdc_mem: int, hbmp: int, w: int, h: int) -> Optional[np.ndarray]:
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
      auto        — printwindow 와 동일 (기본값)
      printwindow — PrintWindow(PW_RENDERFULLCONTENT), DX 창·비활성 창 지원
      wgc         — Windows Graphics Capture (순수 ctypes, 실패 시 PW 폴백)
      bitblt      — GDI BitBlt (항상 동작, DX 창 미지원)

    캡처 방법은 엔진 전역 설정이다 — 모든 확대기가 공유한다.
    """

    def __init__(self, method: str = "auto") -> None:
        self.method = method
        self._gdi   = _GDI()
        self._wgc_sessions: Dict[int, object] = {}
        self._wgc_refcount: Dict[int, int]    = {}
        self._wgc_failed_at: Dict[int, float] = {}  # hwnd → 실패 시각 (재시도 판단)

        if method in ("wgc", "printwindow", "bitblt"):
            self._active = method
        else:
            # auto: PrintWindow + PW_RENDERFULLCONTENT 기본.
            # WGC 와 달리 캡처 표시자(노란 테두리)가 없고 DX/GPU 창도 지원.
            self._active = "printwindow"

        logger.info("캡처 방법: %s", self._active.upper())

    # ── WGC 세션 라이프사이클 ────────────────────────────────────
    def acquire_wgc(self, hwnd: int) -> None:
        """확대기가 hwnd 를 사용하기 시작할 때 호출 — 참조 카운트 +1."""
        if not hwnd:
            return
        self._wgc_refcount[hwnd] = self._wgc_refcount.get(hwnd, 0) + 1

    def release_wgc(self, hwnd: int) -> None:
        """확대기가 hwnd 사용을 끝낼 때 호출 — 0 되면 세션 종료."""
        if not hwnd:
            return
        cnt = self._wgc_refcount.get(hwnd, 0) - 1
        if cnt <= 0:
            self._wgc_refcount.pop(hwnd, None)
            self.release_wgc_session(hwnd)
        else:
            self._wgc_refcount[hwnd] = cnt

    # ── 공개 API ────────────────────────────────────────────────

    def capture(self, hwnd: int,
                region_x: int, region_y: int,
                region_w: int, region_h: int) -> Optional[np.ndarray]:
        if region_w <= 0 or region_h <= 0:
            return None
        if not hwnd:
            return None  # 대상 창 없음 — 화면 전체 캡처 금지 (재귀 렌더링 방지)

        if not windll.user32.IsWindow(hwnd):
            return None

        # ── WGC (명시적으로 지정한 경우만) ───────────────────────
        if self._active == "wgc":
            frame = self._capture_wgc(hwnd, region_x, region_y, region_w, region_h)
            if frame is not None:
                return frame
            # WGC 실패 → PrintWindow 로 fallback (DX 창 지원 유지)

        # ── PrintWindow + DWM (기본값 및 WGC fallback) ───────────
        # bitblt 전용 모드가 아닌 경우: DWM compositor 프레임 캡처 시도
        if self._active != "bitblt":
            frame = self._gdi.capture_window_printwindow(
                hwnd, region_x, region_y, region_w, region_h)
            if frame is not None:
                return frame

        # ── BitBlt (최후 수단) ───────────────────────────────────
        # DX 전용 창에서는 빈 화면이 나올 수 있으나 항상 동작함
        return self._gdi.capture_window_region(
            hwnd, region_x, region_y, region_w, region_h)

    def capture_screen_region(self, x: int, y: int, w: int, h: int
                               ) -> Optional[np.ndarray]:
        return self._gdi.capture_screen_region(x, y, w, h)

    # ── WGC 내부 ────────────────────────────────────────────────

    def _capture_wgc(self, hwnd: int, rx: int, ry: int,
                     rw: int, rh: int) -> Optional[np.ndarray]:
        try:
            from capture.wgc_capture import WGCSession
            if hwnd not in self._wgc_sessions:
                # 최근 실패한 hwnd 는 일정 시간 후에만 재시도.
                # 창 최소화 등 일시적 원인일 수 있어 영구 차단하지 않는다.
                failed = self._wgc_failed_at.get(hwnd)
                if failed is not None and time.monotonic() - failed < _WGC_RETRY_SEC:
                    return None
                sess = WGCSession(hwnd)
                if not sess.ok:
                    self._wgc_failed_at[hwnd] = time.monotonic()
                    return None
                self._wgc_failed_at.pop(hwnd, None)
                self._wgc_sessions[hwnd] = sess
            sess = self._wgc_sessions[hwnd]
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
        except Exception:
            logger.exception("WGC 캡처 오류")
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

    def get_process_name(self, hwnd: int) -> str:
        """HWND 에서 프로세스 실행 파일명 반환 (예: 'chrome.exe'). 실패 시 ''."""
        if not hwnd:
            return ""
        pid = wintypes.DWORD(0)
        windll.user32.GetWindowThreadProcessId(hwnd, byref(pid))
        if not pid.value:
            return ""
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        h = windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if not h:
            return ""
        try:
            buf  = ctypes.create_unicode_buffer(260)
            size = wintypes.DWORD(260)
            windll.kernel32.QueryFullProcessImageNameW(h, 0, buf, byref(size))
            path = buf.value
            return path.rsplit("\\", 1)[-1] if path else ""
        finally:
            windll.kernel32.CloseHandle(h)

    def find_window_by_process(self, process_name: str) -> int:
        """실행 파일명과 일치하는 최상위 가시 창의 HWND 반환 (없으면 0)."""
        if not process_name:
            return 0
        found: list = []
        target = process_name.lower()

        _CB = ctypes.WINFUNCTYPE(c_int, c_void_p, c_void_p)

        @_CB
        def _cb(hwnd, _):
            if windll.user32.IsWindowVisible(hwnd):
                if self.get_process_name(hwnd).lower() == target:
                    found.append(hwnd)
                    return 0
            return 1

        windll.user32.EnumWindows(_cb, 0)
        return found[0] if found else 0

    def release_wgc_session(self, hwnd: int) -> None:
        self._wgc_failed_at.pop(hwnd, None)
        sess = self._wgc_sessions.pop(hwnd, None)
        if sess:
            try: sess.close()
            except Exception: pass

    def close(self) -> None:
        for sess in self._wgc_sessions.values():
            try: sess.close()
            except Exception: pass
        self._wgc_sessions.clear()
        self._wgc_refcount.clear()
        self._wgc_failed_at.clear()
