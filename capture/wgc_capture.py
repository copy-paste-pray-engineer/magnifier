"""
Windows Graphics Capture (WGC) — pure ctypes COM.

winrt-runtime Python 패키지 없이 COM vtable 를 직접 호출합니다.
Windows 10 1803 (빌드 17134) 이상에서 동작합니다.

vtable 인덱스 출처: Windows.Graphics.winmd 메타데이터 (ECMA-335 파싱).
IInspectable 상속 인터페이스는 [0-5] 가 IUnknown+IInspectable 이므로
자체 메서드는 [6] 부터 시작합니다.
"""
from __future__ import annotations
import ctypes
import logging
import threading
from ctypes import (
    c_void_p, c_uint, c_int, c_long, c_ulong, c_ushort, c_ubyte, c_int64,
    Structure, POINTER, WINFUNCTYPE, byref,
)
from typing import Optional
import numpy as np

logger = logging.getLogger(__name__)


# ── WGC 가용성 ────────────────────────────────────────────────────
def wgc_available() -> bool:
    """pure ctypes COM 방식 — winrt 패키지 불필요. d3d11.dll + combase.dll 확인."""
    try:
        _ = ctypes.windll.d3d11
        _ = ctypes.windll.combase
        return True
    except OSError:
        return False


# ── D3D11 상수 ────────────────────────────────────────────────────
DXGI_FORMAT_B8G8R8A8_UNORM       = 87
D3D_DRIVER_TYPE_HARDWARE          = 1
D3D_DRIVER_TYPE_WARP              = 5
D3D11_SDK_VERSION                 = 7
D3D11_CREATE_DEVICE_BGRA_SUPPORT  = 0x20
D3D11_USAGE_STAGING               = 3
D3D11_CPU_ACCESS_READ             = 0x20000
D3D11_MAP_READ                    = 1

# ── D3D11 vtable 인덱스 ───────────────────────────────────────────
_VT_QueryInterface   = 0
_VT_AddRef           = 1
_VT_Release          = 2
_VT_GetInterface     = 3   # IDirect3DDxgiInterfaceAccess::GetInterface
_VT_Tex2D_GetDesc    = 10  # ID3D11Texture2D::GetDesc
_VT_Dev_CreateTex2D  = 5   # ID3D11Device::CreateTexture2D
_VT_Dev_GetImmCtx    = 40  # ID3D11Device::GetImmediateContext
_VT_Ctx_Map          = 14  # ID3D11DeviceContext::Map
_VT_Ctx_Unmap        = 15  # ID3D11DeviceContext::Unmap
_VT_Ctx_CopyResource = 47  # ID3D11DeviceContext::CopyResource

# ── WGC vtable 인덱스 (winmd 파싱으로 확정) ──────────────────────
# IGraphicsCaptureItem (IInspectable 상속)
_VT_GCI_get_Size             = 7

# IDirect3D11CaptureFramePool (IInspectable 상속)
_VT_FP_Recreate              = 6
_VT_FP_TryGetNextFrame       = 7
_VT_FP_add_FrameArrived      = 8
_VT_FP_remove_FrameArrived   = 9
_VT_FP_CreateCaptureSession  = 10

# IDirect3D11CaptureFramePoolStatics2 (IInspectable 상속)
_VT_FPS2_CreateFreeThreaded  = 6

# IDirect3D11CaptureFrame (IInspectable 상속)
_VT_Frame_get_Surface        = 6

# IGraphicsCaptureSession (IInspectable 상속)
_VT_Sess_StartCapture        = 6

# IGraphicsCaptureSession3 (IInspectable 직접 상속 — v1 과 별개 인터페이스)
# vtable: [0-2]=IUnknown, [3-5]=IInspectable, [6]=get_IsBorderRequired, [7]=put_IsBorderRequired
# 주의: IsBorderRequired 는 Session2(IsCursorCaptureEnabled)가 아니라 Session3 소속.
_VT_Sess3_get_IsBorderRequired = 6
_VT_Sess3_put_IsBorderRequired = 7

# IClosable::Close (IInspectable 상속)
_VT_Closable_Close           = 6

# IGraphicsCaptureItemInterop (IUnknown 상속, vtable [3])
_VT_Interop_CreateForWindow  = 3


# ── GUID 헬퍼 ─────────────────────────────────────────────────────
class GUID(Structure):
    _fields_ = [
        ("Data1", c_ulong),
        ("Data2", c_ushort),
        ("Data3", c_ushort),
        ("Data4", c_ubyte * 8),
    ]

def _guid_equal(a: GUID, b: GUID) -> bool:
    return bytes(a) == bytes(b)


def _guid(s: str) -> GUID:
    s = s.strip("{}").replace("-", "")
    g = GUID()
    g.Data1 = int(s[0:8],  16)
    g.Data2 = int(s[8:12], 16)
    g.Data3 = int(s[12:16],16)
    raw = bytes.fromhex(s[16:32])
    for i in range(8):
        g.Data4[i] = raw[i]
    return g

# IGraphicsCaptureItemInterop — HWND → IGraphicsCaptureItem (IUnknown 파생)
IID_IGraphicsCaptureItemInterop    = _guid("{3628E81B-3CAC-4C60-B7F4-23CE0E0C3356}")
# IGraphicsCaptureItem
IID_IGraphicsCaptureItem           = _guid("{79C3F95B-31F7-4EC2-A464-632EF5D30760}")
# IGraphicsCaptureSession3 — put_IsBorderRequired (Windows 10 21H1+ / Windows 11)
IID_IGraphicsCaptureSession3       = _guid("{F2CDD966-22AE-5EA1-9596-3A289344C3BE}")
# IDirect3D11CaptureFramePoolStatics2 (CreateFreeThreaded)
IID_IDirect3D11CaptureFramePoolSt2 = _guid("{589B103F-6BBC-5DF5-A991-02E28B3B66D5}")
# IClosable (Windows.Foundation)
IID_IClosable                      = _guid("{30D5A829-7FA4-4026-83BB-D75BAE4EA99E}")
# IDirect3DDxgiInterfaceAccess — IDirect3DSurface → ID3D11Texture2D 언랩
IID_IDirect3DDxgiInterfaceAccess   = _guid("{A9B3D012-3DF2-4EE3-B8D1-8695F457D3C1}")
IID_ID3D11Texture2D                = _guid("{6F15AAF2-D208-4E89-9AB4-489535D34F9C}")
IID_IDXGIDevice                    = _guid("{54EC77FA-1377-44E6-8C32-88FD5F44C84C}")

# 프레임 콜백 핸들러가 QueryInterface 에서 수락하는 IID 들.
IID_IUnknown                       = _guid("{00000000-0000-0000-C000-000000000046}")
# IAgileObject — 마커 인터페이스(메서드 없음). 이걸 수락하면 WinRT 가
# 핸들러를 스레드 간 마샬링 없이 그대로 호출한다 (free-threaded 풀에 적합).
IID_IAgileObject                   = _guid("{94EA2B94-E9CC-49E0-C0FF-EE64CA8F5B90}")
# ITypedEventHandler<Direct3D11CaptureFramePool*, IInspectable*> 의
# parameterized IID. WinRT 표준 알고리즘(UUID v5, SHA-1)으로 계산:
#   uuid5(11f47ad5-7b73-42c0-abae-878b1e16adee,
#         "pinterface({9de1c534-6ae1-11e0-84e1-18a905bcc53f};"
#         "rc(Windows.Graphics.Capture.Direct3D11CaptureFramePool;"
#         "{24b5d8fd-f76a-4eba-9b88-32a3a35b3eaf});cinterface(IInspectable))")
IID_FrameArrivedHandler            = _guid("{F810DB63-93F2-5FE6-A429-4BF67D98E90C}")

E_NOINTERFACE = -2147467262  # 0x80004002


# ── 구조체 ────────────────────────────────────────────────────────
class SizeInt32(Structure):
    _fields_ = [("Width", c_int), ("Height", c_int)]

class EventToken(Structure):
    _fields_ = [("Value", c_int64)]

class _COM_Handler(Structure):
    """ITypedEventHandler를 구현하는 최소 COM 객체."""
    _fields_ = [("lpVtbl", c_void_p)]

class DXGI_SAMPLE_DESC(Structure):
    _fields_ = [("Count", c_uint), ("Quality", c_uint)]

class D3D11_TEXTURE2D_DESC(Structure):
    _fields_ = [
        ("Width",          c_uint),
        ("Height",         c_uint),
        ("MipLevels",      c_uint),
        ("ArraySize",      c_uint),
        ("Format",         c_uint),
        ("SampleDesc",     DXGI_SAMPLE_DESC),
        ("Usage",          c_uint),
        ("BindFlags",      c_uint),
        ("CPUAccessFlags", c_uint),
        ("MiscFlags",      c_uint),
    ]

class D3D11_MAPPED_SUBRESOURCE(Structure):
    _fields_ = [
        ("pData",      c_void_p),
        ("RowPitch",   c_uint),
        ("DepthPitch", c_uint),
    ]


# ── COM vtable 호출 헬퍼 ──────────────────────────────────────────
def _vtbl(p: int) -> ctypes.Array:
    vtbl_addr = ctypes.cast(p, POINTER(c_void_p))[0]
    return ctypes.cast(vtbl_addr, POINTER(c_void_p * 128))[0]

def _call(p: int, idx: int, proto: type) -> object:
    return proto(_vtbl(p)[idx])

_RELEASE_PROTO = WINFUNCTYPE(c_ulong, c_void_p)
_QI_PROTO      = WINFUNCTYPE(c_long,  c_void_p, POINTER(GUID), POINTER(c_void_p))

def _com_release(p: int) -> None:
    if p:
        _call(p, _VT_Release, _RELEASE_PROTO)(p)

def _com_query(p: int, iid: GUID) -> int:
    if not p:
        return 0
    out = c_void_p()
    hr  = _call(p, _VT_QueryInterface, _QI_PROTO)(p, byref(iid), byref(out))
    return out.value if (hr == 0 and out.value) else 0

def _closable_close_and_release(ptr: int) -> None:
    """IClosable::Close 호출 후 Release. 실패해도 Release 는 항상 수행."""
    if not ptr:
        return
    closable = _com_query(ptr, IID_IClosable)
    if closable:
        try:
            proto = WINFUNCTYPE(c_long, c_void_p)
            _call(closable, _VT_Closable_Close, proto)(closable)
        except Exception:
            pass
        _com_release(closable)
    _com_release(ptr)


# ── WinRT COM 헬퍼 ────────────────────────────────────────────────
def _ro_factory(class_name: str, iid: GUID) -> int:
    """RoGetActivationFactory로 활성화 팩토리 획득 → raw COM ptr 반환."""
    cb = ctypes.windll.combase
    cb.WindowsCreateString.restype  = c_long
    cb.WindowsCreateString.argtypes = [ctypes.c_wchar_p, c_uint, POINTER(c_void_p)]
    cb.RoGetActivationFactory.restype = c_long
    hstr = c_void_p()
    cb.WindowsCreateString(class_name, len(class_name), byref(hstr))
    fac = c_void_p()
    hr  = cb.RoGetActivationFactory(hstr, byref(iid), byref(fac))
    cb.WindowsDeleteString(hstr)
    if (hr & 0xFFFFFFFF) or not fac.value:
        raise OSError(f"RoGetActivationFactory('{class_name}') 실패: hr={hr & 0xFFFFFFFF:#010x}")
    return fac.value

def _interop_create_for_window(interop_ptr: int, hwnd: int) -> int:
    """IGraphicsCaptureItemInterop::CreateForWindow → IGraphicsCaptureItem* 반환."""
    out   = c_void_p()
    proto = WINFUNCTYPE(c_long, c_void_p, c_void_p, POINTER(GUID), POINTER(c_void_p))
    hr    = proto(_vtbl(interop_ptr)[_VT_Interop_CreateForWindow])(
                interop_ptr, hwnd, byref(IID_IGraphicsCaptureItem), byref(out))
    if (hr & 0xFFFFFFFF) or not out.value:
        raise OSError(f"CreateForWindow(hwnd={hwnd:#x}) 실패: hr={hr & 0xFFFFFFFF:#010x}")
    return out.value

def _create_winrt_device(dxgi_ptr: int) -> int:
    """IDXGIDevice* → IDirect3DDevice* (WinRT IInspectable 래퍼)."""
    d3d11 = ctypes.windll.d3d11
    d3d11.CreateDirect3D11DeviceFromDXGIDevice.restype  = c_long
    d3d11.CreateDirect3D11DeviceFromDXGIDevice.argtypes = [c_void_p, POINTER(c_void_p)]
    out = c_void_p()
    hr  = d3d11.CreateDirect3D11DeviceFromDXGIDevice(dxgi_ptr, byref(out))
    if hr or not out.value:
        raise OSError(f"CreateDirect3D11DeviceFromDXGIDevice 실패: hr={hr:#010x}")
    return out.value


# ── D3D11 디바이스 생성 ───────────────────────────────────────────
def _create_d3d11_raw() -> tuple[int, int]:
    d3d11 = ctypes.windll.d3d11
    d3d11.D3D11CreateDevice.restype  = c_long
    d3d11.D3D11CreateDevice.argtypes = [
        c_void_p, c_uint, c_void_p, c_uint,
        c_void_p, c_uint, c_uint,
        POINTER(c_void_p), POINTER(c_uint), POINTER(c_void_p),
    ]
    dev = c_void_p(); ctx = c_void_p(); fl = c_uint(0)
    hr  = d3d11.D3D11CreateDevice(
        None, D3D_DRIVER_TYPE_HARDWARE, None,
        D3D11_CREATE_DEVICE_BGRA_SUPPORT, None, 0, D3D11_SDK_VERSION,
        byref(dev), byref(fl), byref(ctx),
    )
    if hr < 0:
        hr = d3d11.D3D11CreateDevice(
            None, D3D_DRIVER_TYPE_WARP, None,
            D3D11_CREATE_DEVICE_BGRA_SUPPORT, None, 0, D3D11_SDK_VERSION,
            byref(dev), byref(fl), byref(ctx),
        )
    if hr < 0 or not dev.value or not ctx.value:
        raise OSError(f"D3D11CreateDevice 실패: hr={hr:#010x}")
    return dev.value, ctx.value


# ── WGC 세션 ──────────────────────────────────────────────────────
class WGCSession:
    """
    단일 HWND 대상 WGC 캡처 세션.
    순수 ctypes COM vtable 호출 — winrt Python 패키지 불필요.
    """

    def __init__(self, hwnd: int) -> None:
        self.hwnd     = hwnd
        self._frame:  Optional[np.ndarray] = None
        self._lock    = threading.Lock()
        self._closed  = False
        self._init_ok = False

        # D3D11 자원
        self._d3d_device  = 0
        self._d3d_context = 0
        self._staging     = 0
        self._stg_w = self._stg_h = self._stg_fmt = 0
        self._pool_size: tuple[int, int] = (0, 0)

        # WGC COM 포인터 (close 에서 Release)
        self._item_ptr    = 0
        self._winrt_dev   = 0
        self._pool_ptr    = 0
        self._session_ptr = 0
        self._frame_token: Optional[EventToken] = None

        # ITypedEventHandler COM 객체 — GC 방지용 레퍼런스 유지
        self._h_qi_fn = self._h_ar_fn = self._h_rl_fn = self._h_inv_fn = None
        self._h_vtbl  = None
        self._h_obj   = None
        self._h_ptr   = 0

        try:
            self._init_com()
        except Exception:
            logger.exception("[WGC] 초기화 실패 (hwnd=%#x)", hwnd)
            self.close()  # 부분 생성된 자원 회수 — 각 포인터를 개별 검사하므로 안전

    # ── 초기화 ────────────────────────────────────────────────────
    def _init_com(self) -> None:
        cb = ctypes.windll.combase
        cb.RoInitialize.restype = c_long
        hr = cb.RoInitialize(1)  # RO_INIT_MULTITHREADED
        # 0=S_OK, 0x80010106=RPC_E_CHANGED_MODE(이미 초기화) 모두 OK
        if (hr & 0xFFFFFFFF) not in (0x00000000, 0x80010106, 0x00000001):
            raise OSError(f"RoInitialize 실패: hr={hr & 0xFFFFFFFF:#010x}")

        # 1) HWND → IGraphicsCaptureItem
        interop_fac = _ro_factory(
            "Windows.Graphics.Capture.GraphicsCaptureItem",
            IID_IGraphicsCaptureItemInterop,
        )
        try:
            self._item_ptr = _interop_create_for_window(interop_fac, self.hwnd)
        finally:
            _com_release(interop_fac)

        # 2) item.get_Size [7] → 캡처 크기
        size = SizeInt32()
        proto = WINFUNCTYPE(c_long, c_void_p, POINTER(SizeInt32))
        hr = proto(_vtbl(self._item_ptr)[_VT_GCI_get_Size])(self._item_ptr, byref(size))
        if (hr & 0xFFFFFFFF) or size.Width <= 0 or size.Height <= 0:
            raise OSError(f"get_Size 실패: hr={hr & 0xFFFFFFFF:#010x} {size.Width}x{size.Height}")

        # 3) D3D11 device + context
        self._d3d_device, self._d3d_context = _create_d3d11_raw()

        # 4) IDXGIDevice QI → IDirect3DDevice 래핑
        dxgi_ptr = _com_query(self._d3d_device, IID_IDXGIDevice)
        if not dxgi_ptr:
            raise OSError("QI(IDXGIDevice) 실패")
        try:
            self._winrt_dev = _create_winrt_device(dxgi_ptr)
        finally:
            _com_release(dxgi_ptr)

        # 5) IDirect3D11CaptureFramePoolStatics2 팩토리
        pool_fac = _ro_factory(
            "Windows.Graphics.Capture.Direct3D11CaptureFramePool",
            IID_IDirect3D11CaptureFramePoolSt2,
        )

        # 6) CreateFreeThreaded [6]: (device, format, count, size) → pool
        pool_out = c_void_p()
        proto2 = WINFUNCTYPE(c_long, c_void_p, c_void_p, c_uint, c_int, SizeInt32, POINTER(c_void_p))
        hr2 = proto2(_vtbl(pool_fac)[_VT_FPS2_CreateFreeThreaded])(
            pool_fac, self._winrt_dev, DXGI_FORMAT_B8G8R8A8_UNORM, 2, size, byref(pool_out)
        )
        _com_release(pool_fac)
        if (hr2 & 0xFFFFFFFF) or not pool_out.value:
            raise OSError(f"CreateFreeThreaded 실패: hr={hr2 & 0xFFFFFFFF:#010x}")
        self._pool_ptr  = pool_out.value
        self._pool_size = (size.Width, size.Height)

        # 7) ITypedEventHandler COM 객체 구성
        self._setup_handler()

        # 8) add_FrameArrived [8] → EventToken
        token = EventToken()
        proto3 = WINFUNCTYPE(c_long, c_void_p, c_void_p, POINTER(EventToken))
        hr3 = proto3(_vtbl(self._pool_ptr)[_VT_FP_add_FrameArrived])(
            self._pool_ptr, self._h_ptr, byref(token)
        )
        if (hr3 & 0xFFFFFFFF):
            raise OSError(f"add_FrameArrived 실패: hr={hr3 & 0xFFFFFFFF:#010x}")
        self._frame_token = token

        # 9) CreateCaptureSession [10]: (item) → session
        sess_out = c_void_p()
        proto4 = WINFUNCTYPE(c_long, c_void_p, c_void_p, POINTER(c_void_p))
        hr4 = proto4(_vtbl(self._pool_ptr)[_VT_FP_CreateCaptureSession])(
            self._pool_ptr, self._item_ptr, byref(sess_out)
        )
        if (hr4 & 0xFFFFFFFF) or not sess_out.value:
            raise OSError(f"CreateCaptureSession 실패: hr={hr4 & 0xFFFFFFFF:#010x}")
        self._session_ptr = sess_out.value

        # 9.5) 노란 테두리 비활성화 (IGraphicsCaptureSession3, Windows 10 21H1+)
        # IGraphicsCaptureSession3 은 IInspectable 을 직접 상속하는 별개 인터페이스.
        # vtable: [6]=get_IsBorderRequired, [7]=put_IsBorderRequired
        _border_ok = False
        sess3 = _com_query(self._session_ptr, IID_IGraphicsCaptureSession3)
        if sess3:
            try:
                proto_b = WINFUNCTYPE(c_long, c_void_p, c_ubyte)
                hr_b = proto_b(_vtbl(sess3)[_VT_Sess3_put_IsBorderRequired])(sess3, 0)
                _border_ok = (hr_b & 0xFFFFFFFF) == 0
                if _border_ok:
                    logger.info("[WGC] 노란 테두리 비활성화 성공")
                else:
                    logger.warning("[WGC] 노란 테두리 비활성화 실패: hr=%#010x",
                                   hr_b & 0xFFFFFFFF)
            except Exception:
                logger.exception("[WGC] 노란 테두리 비활성화 예외")
            finally:
                _com_release(sess3)
        else:
            logger.info("[WGC] IGraphicsCaptureSession3 미지원 (Windows 10 21H1 미만)")
        self.border_suppressed: bool = _border_ok

        # 10) StartCapture [6]
        proto5 = WINFUNCTYPE(c_long, c_void_p)
        hr5 = proto5(_vtbl(self._session_ptr)[_VT_Sess_StartCapture])(self._session_ptr)
        if (hr5 & 0xFFFFFFFF):
            raise OSError(f"StartCapture 실패: hr={hr5 & 0xFFFFFFFF:#010x}")

        self._init_ok = True
        logger.info("[WGC] 캡처 시작: hwnd=%#x, size=%dx%d",
                    self.hwnd, size.Width, size.Height)

    # ── ITypedEventHandler 구현 ────────────────────────────────────
    def _setup_handler(self) -> None:
        """
        ITypedEventHandler<Direct3D11CaptureFramePool*, IInspectable*> 를
        ctypes 로 구현합니다. 콜백 함수 객체는 GC 를 막기 위해 self 에 보관합니다.
        """
        wgc_self = self

        # 수락 IID 외에는 반드시 E_NOINTERFACE 를 돌려줘야 한다.
        # 무조건 성공시키면 런타임이 IMarshal 등으로 착각하고 vtable 4칸
        # 너머의 메서드를 호출해 access violation 이 난다.
        _accepted = (IID_IUnknown, IID_IAgileObject, IID_FrameArrivedHandler)

        def _qi(this, riid, ppv):
            if not ppv:
                return E_NOINTERFACE
            if riid and any(_guid_equal(riid[0], a) for a in _accepted):
                ctypes.cast(ppv, POINTER(c_void_p))[0] = this
                return 0
            ctypes.cast(ppv, POINTER(c_void_p))[0] = None
            return E_NOINTERFACE
        def _ar(this): return 2
        def _rl(this): return 1
        def _invoke(this, sender, args):
            try:
                wgc_self._frame_callback(sender)
            except Exception:
                logger.exception("[WGC] 프레임 콜백 예외")
            return 0

        _QI_P  = WINFUNCTYPE(c_long,  c_void_p, POINTER(GUID), POINTER(c_void_p))
        _UL_P  = WINFUNCTYPE(c_ulong, c_void_p)
        _INV_P = WINFUNCTYPE(c_long,  c_void_p, c_void_p, c_void_p)

        self._h_qi_fn  = _QI_P(_qi)
        self._h_ar_fn  = _UL_P(_ar)
        self._h_rl_fn  = _UL_P(_rl)
        self._h_inv_fn = _INV_P(_invoke)

        self._h_vtbl = (c_void_p * 4)(
            ctypes.cast(self._h_qi_fn,  c_void_p).value,
            ctypes.cast(self._h_ar_fn,  c_void_p).value,
            ctypes.cast(self._h_rl_fn,  c_void_p).value,
            ctypes.cast(self._h_inv_fn, c_void_p).value,
        )
        self._h_obj = _COM_Handler()
        self._h_obj.lpVtbl = ctypes.addressof(self._h_vtbl)
        self._h_ptr = ctypes.addressof(self._h_obj)

    # ── 프레임 콜백 ────────────────────────────────────────────────
    def _frame_callback(self, pool_ptr: int) -> None:
        # 콜백 전체를 _lock 으로 감싼다. close() 가 같은 락 안에서 _closed 를
        # 세우므로, 진행 중인 콜백이 끝나기 전에는 D3D 자원이 해제되지 않는다
        # (use-after-free 방지). remove_FrameArrived 는 진행 중 콜백의 완료를
        # 보장하지 않기 때문에 락 없이는 race 가 남는다.
        with self._lock:
            if self._closed:
                return
            self._recreate_pool_if_resized(pool_ptr)

            # TryGetNextFrame [7]: () → IDirect3D11CaptureFrame*
            frame_out = c_void_p()
            proto = WINFUNCTYPE(c_long, c_void_p, POINTER(c_void_p))
            hr = proto(_vtbl(pool_ptr)[_VT_FP_TryGetNextFrame])(pool_ptr, byref(frame_out))
            if (hr & 0xFFFFFFFF) or not frame_out.value:
                return
            frame_ptr = frame_out.value
            try:
                # get_Surface [6]: () → IDirect3DSurface* (IInspectable)
                surf_out = c_void_p()
                proto2 = WINFUNCTYPE(c_long, c_void_p, POINTER(c_void_p))
                hr2 = proto2(_vtbl(frame_ptr)[_VT_Frame_get_Surface])(frame_ptr, byref(surf_out))
                if (hr2 & 0xFFFFFFFF) == 0 and surf_out.value:
                    try:
                        arr = self._surface_via_vtable(surf_out.value)
                        if arr is not None:
                            self._frame = arr
                    finally:
                        _com_release(surf_out.value)
            finally:
                _closable_close_and_release(frame_ptr)

    def _recreate_pool_if_resized(self, pool_ptr: int) -> None:
        """대상 창 크기가 바뀌면 FramePool 을 새 크기로 Recreate.

        풀 텍스처는 생성 시점 크기로 고정되므로, 리사이즈 후에도 그대로 두면
        프레임에 이전 크기 기준의 잔여 영역이 섞인다.
        """
        size = SizeInt32()
        proto = WINFUNCTYPE(c_long, c_void_p, POINTER(SizeInt32))
        hr = proto(_vtbl(self._item_ptr)[_VT_GCI_get_Size])(self._item_ptr, byref(size))
        if (hr & 0xFFFFFFFF) or size.Width <= 0 or size.Height <= 0:
            return
        if (size.Width, size.Height) == self._pool_size:
            return
        proto_r = WINFUNCTYPE(c_long, c_void_p, c_void_p, c_uint, c_int, SizeInt32)
        hr_r = proto_r(_vtbl(pool_ptr)[_VT_FP_Recreate])(
            pool_ptr, self._winrt_dev, DXGI_FORMAT_B8G8R8A8_UNORM, 2, size)
        if (hr_r & 0xFFFFFFFF) == 0:
            self._pool_size = (size.Width, size.Height)
            logger.info("[WGC] FramePool 재생성: %dx%d", size.Width, size.Height)

    # ── 표면 → numpy (D3D11 staging 텍스처 경유) ─────────────────
    def _surface_via_vtable(self, surf_ptr: int) -> Optional[np.ndarray]:
        """IDirect3DSurface* → IDirect3DDxgiInterfaceAccess → ID3D11Texture2D → numpy."""
        access = _com_query(surf_ptr, IID_IDirect3DDxgiInterfaceAccess)
        if not access:
            return None
        tex_ptr = 0
        try:
            out = c_void_p()
            proto = WINFUNCTYPE(c_long, c_void_p, POINTER(GUID), POINTER(c_void_p))
            hr = _call(access, _VT_GetInterface, proto)(
                access, byref(IID_ID3D11Texture2D), byref(out))
            if hr < 0 or not out.value:
                return None
            tex_ptr = out.value

            desc = D3D11_TEXTURE2D_DESC()
            proto = WINFUNCTYPE(None, c_void_p, POINTER(D3D11_TEXTURE2D_DESC))
            _call(tex_ptr, _VT_Tex2D_GetDesc, proto)(tex_ptr, byref(desc))
            w, h, fmt = desc.Width, desc.Height, desc.Format
            if w == 0 or h == 0:
                return None

            staging = self._ensure_staging(w, h, fmt)
            if not staging:
                return None

            # CopyResource(dst=staging, src=tex)
            proto = WINFUNCTYPE(None, c_void_p, c_void_p, c_void_p)
            _call(self._d3d_context, _VT_Ctx_CopyResource, proto)(
                self._d3d_context, staging, tex_ptr)

            # Map(staging, 0, MAP_READ, 0, &mapped)
            mapped = D3D11_MAPPED_SUBRESOURCE()
            proto  = WINFUNCTYPE(
                c_long, c_void_p, c_void_p, c_uint, c_uint, c_uint,
                POINTER(D3D11_MAPPED_SUBRESOURCE),
            )
            hr = _call(self._d3d_context, _VT_Ctx_Map, proto)(
                self._d3d_context, staging, 0, D3D11_MAP_READ, 0, byref(mapped))
            if hr < 0 or not mapped.pData:
                return None
            try:
                rp = mapped.RowPitch
                buf = (c_ubyte * (rp * h)).from_address(mapped.pData)
                view = np.frombuffer(buf, dtype=np.uint8).reshape(h, rp // 4, 4)
                return view[:, :w, 2::-1].copy()  # BGRA → RGB
            finally:
                proto = WINFUNCTYPE(None, c_void_p, c_void_p, c_uint)
                _call(self._d3d_context, _VT_Ctx_Unmap, proto)(
                    self._d3d_context, staging, 0)
        finally:
            if tex_ptr:
                _com_release(tex_ptr)
            _com_release(access)

    def _ensure_staging(self, w: int, h: int, fmt: int) -> int:
        if (self._staging and self._stg_w == w
                and self._stg_h == h and self._stg_fmt == fmt):
            return self._staging
        if self._staging:
            _com_release(self._staging)
            self._staging = 0

        desc = D3D11_TEXTURE2D_DESC()
        desc.Width            = w
        desc.Height           = h
        desc.MipLevels        = 1
        desc.ArraySize        = 1
        desc.Format           = fmt
        desc.SampleDesc.Count = 1
        desc.Usage            = D3D11_USAGE_STAGING
        desc.BindFlags        = 0
        desc.CPUAccessFlags   = D3D11_CPU_ACCESS_READ
        desc.MiscFlags        = 0

        out   = c_void_p()
        proto = WINFUNCTYPE(c_long, c_void_p,
                            POINTER(D3D11_TEXTURE2D_DESC),
                            c_void_p, POINTER(c_void_p))
        hr = _call(self._d3d_device, _VT_Dev_CreateTex2D, proto)(
            self._d3d_device, byref(desc), None, byref(out))
        if hr < 0 or not out.value:
            logger.error("[WGC] CreateTexture2D(staging) 실패: hr=%#010x", hr & 0xFFFFFFFF)
            return 0
        self._staging = out.value
        self._stg_w, self._stg_h, self._stg_fmt = w, h, fmt
        return self._staging

    # ── 공개 API ──────────────────────────────────────────────────
    def get_latest_frame(self) -> Optional[np.ndarray]:
        if not self._init_ok:
            return None
        with self._lock:
            return self._frame

    @property
    def ok(self) -> bool:
        return self._init_ok

    # ── 정리 ──────────────────────────────────────────────────────
    def close(self) -> None:
        # _closed 플래그는 반드시 락 안에서 세운다. 락 획득이 곧 "진행 중이던
        # 프레임 콜백이 끝났다"는 보장이고, 이후 도착하는 콜백은 _closed 검사로
        # 자원에 손대지 않는다. 그 다음에야 안전하게 Release 할 수 있다.
        with self._lock:
            if self._closed:
                return
            self._closed = True

        # 콜백 해제 — remove_FrameArrived [9]
        if self._pool_ptr and self._frame_token is not None:
            try:
                proto = WINFUNCTYPE(c_long, c_void_p, EventToken)
                proto(_vtbl(self._pool_ptr)[_VT_FP_remove_FrameArrived])(
                    self._pool_ptr, self._frame_token)
            except Exception:
                pass
            self._frame_token = None

        _closable_close_and_release(self._session_ptr); self._session_ptr = 0
        _closable_close_and_release(self._pool_ptr);    self._pool_ptr    = 0
        _com_release(self._winrt_dev);                  self._winrt_dev   = 0
        _com_release(self._item_ptr);                   self._item_ptr    = 0
        self._release_d3d()

    def _release_d3d(self) -> None:
        if self._staging:
            _com_release(self._staging);     self._staging     = 0
        if self._d3d_context:
            _com_release(self._d3d_context); self._d3d_context = 0
        if self._d3d_device:
            _com_release(self._d3d_device);  self._d3d_device  = 0

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass
