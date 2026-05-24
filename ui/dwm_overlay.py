"""
DWM Thumbnail 오버레이 모드
GPU→GPU 직접 렌더링으로 CPU 부하 최소화.
비활성/가려진 창 실시간 확대에 이상적.

DwmRegisterThumbnail → DwmUpdateThumbnailProperties 루프로
원본 창 내용을 출력창에 직접 렌더링.
픽셀 데이터 CPU 복사 없음 → 초저지연.
"""
from __future__ import annotations
import ctypes
import ctypes.wintypes as wintypes
from typing import Optional, Tuple

# ── dwmapi 구조체 ──────────────────────────────────────────────

class _RECT(ctypes.Structure):
    _fields_ = [("left",   ctypes.c_int), ("top",    ctypes.c_int),
                ("right",  ctypes.c_int), ("bottom", ctypes.c_int)]

class _DWM_THUMBNAIL_PROPERTIES(ctypes.Structure):
    # 플래그
    DWM_TNP_RECTDESTINATION  = 0x00000001
    DWM_TNP_RECTSOURCE       = 0x00000002
    DWM_TNP_OPACITY          = 0x00000004
    DWM_TNP_VISIBLE          = 0x00000008
    DWM_TNP_SOURCECLIENTONLY = 0x00000010

    _fields_ = [
        ("dwFlags",               wintypes.DWORD),
        ("rcDestination",         _RECT),
        ("rcSource",              _RECT),
        ("opacity",               ctypes.c_byte),
        ("fVisible",              wintypes.BOOL),
        ("fSourceClientAreaOnly", wintypes.BOOL),
    ]


class DWMOverlay:
    """
    단일 DWM 썸네일 세션 관리.
    
    사용법:
        overlay = DWMOverlay(src_hwnd, dst_hwnd)
        overlay.update(dest=(0,0,600,400), src=(100,100,300,200))
        # ... 매 프레임 update() 호출
        overlay.close()
    """

    def __init__(self, hwnd_src: int, hwnd_dest: int):
        self.hwnd_src  = hwnd_src
        self.hwnd_dest = hwnd_dest
        self._thumb    = ctypes.c_void_p(0)
        self._dwmapi   = ctypes.windll.dwmapi
        self._ok       = False
        self._visible  = False
        self._register()

    def _register(self):
        """DwmRegisterThumbnail 호출"""
        if not self.hwnd_src or not self.hwnd_dest:
            return
        try:
            hr = self._dwmapi.DwmRegisterThumbnail(
                self.hwnd_dest,
                self.hwnd_src,
                ctypes.byref(self._thumb),
            )
            self._ok = (hr == 0 and bool(self._thumb))
            if self._ok:
                print(f"[DWM] 등록 성공: src={self.hwnd_src:#x} → dst={self.hwnd_dest:#x}")
            else:
                print(f"[DWM] DwmRegisterThumbnail 실패: hr={hr:#010x}")
        except Exception as e:
            print(f"[DWM] 등록 예외: {e}")

    def update(
        self,
        dest: Tuple[int, int, int, int],                    # x,y,w,h (출력창 내 좌표)
        src:  Optional[Tuple[int, int, int, int]] = None,   # x,y,w,h (원본 창 내 좌표)
        opacity: int = 255,
        client_only: bool = False,
    ) -> bool:
        """
        썸네일 속성 갱신. 매 프레임 호출 필요.
        
        dest: 출력창 내 표시 영역 (픽셀, 창 클라이언트 좌표)
        src:  원본 창에서 가져올 영역 (None = 전체)
        """
        if not self._ok:
            return False

        dx, dy, dw, dh = dest
        if dw <= 0 or dh <= 0:
            return False

        P = _DWM_THUMBNAIL_PROPERTIES
        props = _DWM_THUMBNAIL_PROPERTIES()
        props.dwFlags = (
            P.DWM_TNP_RECTDESTINATION |
            P.DWM_TNP_OPACITY         |
            P.DWM_TNP_VISIBLE
        )

        props.rcDestination.left   = dx
        props.rcDestination.top    = dy
        props.rcDestination.right  = dx + dw
        props.rcDestination.bottom = dy + dh

        props.opacity  = max(0, min(255, opacity))
        props.fVisible = True

        if src is not None:
            sx, sy, sw, sh = src
            props.dwFlags |= P.DWM_TNP_RECTSOURCE
            props.rcSource.left   = sx
            props.rcSource.top    = sy
            props.rcSource.right  = sx + sw
            props.rcSource.bottom = sy + sh

        if client_only:
            props.dwFlags |= P.DWM_TNP_SOURCECLIENTONLY
            props.fSourceClientAreaOnly = True

        try:
            hr = self._dwmapi.DwmUpdateThumbnailProperties(
                self._thumb,
                ctypes.byref(props),
            )
            self._visible = (hr == 0)
            return self._visible
        except Exception:
            return False

    def hide(self):
        """썸네일 숨기기"""
        if not self._ok:
            return
        props = _DWM_THUMBNAIL_PROPERTIES()
        props.dwFlags  = _DWM_THUMBNAIL_PROPERTIES.DWM_TNP_VISIBLE
        props.fVisible = False
        try:
            self._dwmapi.DwmUpdateThumbnailProperties(
                self._thumb, ctypes.byref(props)
            )
        except Exception:
            pass
        self._visible = False

    def get_source_size(self) -> Tuple[int, int]:
        """원본 창 크기 (DWM 기준)"""
        sz = wintypes.SIZE()
        if self._ok:
            try:
                self._dwmapi.DwmQueryThumbnailSourceSize(
                    self._thumb, ctypes.byref(sz)
                )
            except Exception:
                pass
        return sz.cx, sz.cy

    @property
    def ok(self) -> bool:
        return self._ok

    @property
    def visible(self) -> bool:
        return self._visible

    def close(self):
        """DwmUnregisterThumbnail"""
        if self._ok and self._thumb:
            try:
                self._dwmapi.DwmUnregisterThumbnail(self._thumb)
            except Exception:
                pass
        self._ok      = False
        self._visible = False
        self._thumb   = ctypes.c_void_p(0)

    def __del__(self):
        self.close()


def is_dwm_enabled() -> bool:
    """DWM 활성화 여부 확인 (Windows 8+는 항상 True)"""
    try:
        enabled = wintypes.BOOL(0)
        ctypes.windll.dwmapi.DwmIsCompositionEnabled(ctypes.byref(enabled))
        return bool(enabled)
    except Exception:
        return False
