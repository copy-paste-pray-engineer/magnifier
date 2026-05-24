"""
Windows Graphics Capture (WGC) — winrt 기반, ctypes vtable interop.

설치:
    pip install winrt-runtime
    pip install "winrt-Windows.Graphics.Capture"
    pip install "winrt-Windows.Graphics.DirectX"
    pip install "winrt-Windows.Graphics.DirectX.Direct3D11"
    pip install "winrt-Windows.Graphics.Imaging"     # SoftwareBitmap 폴백
    pip install "winrt-Windows.Foundation"

핵심 동작:
    Direct3D11CaptureFramePool 가 새 프레임을 알려 주면
    IDirect3DSurface → ID3D11Texture2D 로 언랩하고
    STAGING 텍스처에 CopyResource + Map 으로 CPU 픽셀을 읽어 numpy 로 반환합니다.

vtable 인덱스는 D3D11 SDK 규약을 따르며 32/64-bit 모두 동일합니다
(포인터 크기는 ctypes.c_void_p 가 흡수합니다).
ID3D11DeviceContext::CopyResource 는 47번 슬롯이며 48이 아닙니다 — 이전 구현의
잘못된 [48] 인덱스가 첫 프레임 이후 캡처가 멈추는 버그의 직접 원인이었습니다.
"""
from __future__ import annotations
import ctypes
import ctypes.wintypes as wintypes
import threading
from ctypes import (
    c_void_p, c_uint, c_int, c_long, c_ulong, c_ushort, c_ubyte,
    Structure, POINTER, WINFUNCTYPE, byref,
)
from typing import Optional
import numpy as np

# ── WGC 가용성 체크 ────────────────────────────────────────────
_WGC_AVAILABLE = False
_WGC_ENGINE    = None   # "winrt" | "winsdk"

def _probe_wgc():
    global _WGC_AVAILABLE, _WGC_ENGINE
    try:
        import winrt.windows.graphics.capture            # noqa
        import winrt.windows.graphics.directx            # noqa
        import winrt.windows.graphics.directx.direct3d11 # noqa
        _WGC_AVAILABLE = True
        _WGC_ENGINE    = "winrt"
        return
    except Exception:
        pass
    try:
        import winsdk.windows.graphics.capture           # noqa
        _WGC_AVAILABLE = True
        _WGC_ENGINE    = "winsdk"
        return
    except Exception:
        pass

_probe_wgc()


def wgc_available() -> bool:
    return _WGC_AVAILABLE


# ── COM/D3D11 상수 ─────────────────────────────────────────────
DXGI_FORMAT_B8G8R8A8_UNORM       = 87

D3D_DRIVER_TYPE_HARDWARE         = 1
D3D_DRIVER_TYPE_WARP             = 5
D3D11_SDK_VERSION                = 7
D3D11_CREATE_DEVICE_BGRA_SUPPORT = 0x20

D3D11_USAGE_STAGING              = 3
D3D11_CPU_ACCESS_READ            = 0x20000
D3D11_MAP_READ                   = 1

# ── vtable 인덱스 (D3D11 SDK 표준, 32/64-bit 동일) ──────────────
# IUnknown
_VT_QueryInterface   = 0
_VT_AddRef           = 1
_VT_Release          = 2

# IDirect3DDxgiInterfaceAccess (IUnknown 다음에 GetInterface 한 개)
_VT_GetInterface     = 3

# ID3D11Texture2D (DeviceChild→Resource→Texture2D 상속 체인)
#   0~2 IUnknown
#   3   ID3D11DeviceChild::GetDevice
#   4~6 GetPrivateData/SetPrivateData/SetPrivateDataInterface
#   7~9 ID3D11Resource: GetType/SetEvictionPriority/GetEvictionPriority
#   10  ID3D11Texture2D::GetDesc
_VT_Tex2D_GetDevice  = 3
_VT_Tex2D_GetDesc    = 10

# ID3D11Device
#   5   CreateTexture2D
#   40  GetImmediateContext
_VT_Dev_CreateTex2D  = 5
_VT_Dev_GetImmCtx    = 40

# ID3D11DeviceContext
#   14  Map
#   15  Unmap
#   47  CopyResource          ← 이전 코드의 [48] 이 버그였음
_VT_Ctx_Map          = 14
_VT_Ctx_Unmap        = 15
_VT_Ctx_CopyResource = 47


# ── GUID 헬퍼 ─────────────────────────────────────────────────
class GUID(Structure):
    _fields_ = [
        ("Data1", c_ulong),
        ("Data2", c_ushort),
        ("Data3", c_ushort),
        ("Data4", c_ubyte * 8),
    ]


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


IID_IDirect3DDxgiInterfaceAccess = _guid("{A9B3D012-3DF2-4EE3-B8D1-8695F457D3C1}")
IID_ID3D11Texture2D              = _guid("{6F15AAF2-D208-4E89-9AB4-489535D34F9C}")
IID_IDXGIDevice                  = _guid("{54EC77FA-1377-44E6-8C32-88FD5F44C84C}")


# ── D3D11 구조체 ──────────────────────────────────────────────
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


# ── COM vtable 호출 헬퍼 ──────────────────────────────────────
def _vtbl(p: int) -> ctypes.Array:
    """`p` 가 가리키는 COM 객체의 vtable 을 충분히 큰 배열로 노출."""
    vtbl_addr = ctypes.cast(p, POINTER(c_void_p))[0]
    return ctypes.cast(vtbl_addr, POINTER(c_void_p * 128))[0]

def _call(p: int, idx: int, proto):
    """vtable 슬롯 idx 의 함수 포인터를 `proto` 시그너처로 호출 가능하게 만듦."""
    return proto(_vtbl(p)[idx])

_RELEASE_PROTO = WINFUNCTYPE(c_ulong, c_void_p)
_QI_PROTO      = WINFUNCTYPE(c_long,  c_void_p, POINTER(GUID), POINTER(c_void_p))

def _com_release(p: int) -> int:
    if not p:
        return 0
    return _call(p, _VT_Release, _RELEASE_PROTO)(p)

def _com_query(p: int, iid: GUID) -> int:
    """QueryInterface — 성공 시 새 포인터(int) 반환, 실패 시 0."""
    if not p:
        return 0
    out = c_void_p()
    hr  = _call(p, _VT_QueryInterface, _QI_PROTO)(p, byref(iid), byref(out))
    if hr < 0 or not out.value:
        return 0
    return out.value


# ── winrt IInspectable 포인터 추출 ────────────────────────────
def _unwrap_iinspectable(obj) -> int:
    """
    winrt-runtime 으로 투영된 객체의 raw IInspectable ABI 주소를 best-effort
    로 얻는다. 패키지 버전(1.x / 2.x)에 따라 노출 방식이 달라 여러 경로를
    순서대로 시도한다. 실패 시 0 — 호출자는 SoftwareBitmap 경로로 폴백.

    여기서 얻은 포인터는 Python 래퍼가 소유한다. _com_release 로 풀지 말 것.
    """
    if obj is None:
        return 0

    for attr in ("_ptr", "_inspectable_ptr", "_iunknown_ptr",
                 "_thisptr", "_default_interface_ptr", "_addr"):
        v = getattr(obj, attr, None)
        if isinstance(v, int) and v:
            return v
        if callable(v):
            try:
                p = int(v())
                if p:
                    return p
            except Exception:
                pass

    try:
        import winrt._winrt as _w
        for fname in ("_iinspectable_address", "_iunknown_address",
                      "iinspectable_address",  "iunknown_address",
                      "get_iunknown_pointer",  "to_abi_int"):
            fn = getattr(_w, fname, None)
            if callable(fn):
                try:
                    p = int(fn(obj))
                    if p:
                        return p
                except Exception:
                    pass
    except ImportError:
        pass

    return 0


# ── D3D11 디바이스 생성 (ctypes) ──────────────────────────────
def _create_d3d11_raw() -> tuple[int, int]:
    """ID3D11Device + immediate context 를 만들어 raw 포인터(int) 둘로 반환."""
    d3d11 = ctypes.windll.d3d11
    d3d11.D3D11CreateDevice.restype  = c_long
    d3d11.D3D11CreateDevice.argtypes = [
        c_void_p,                # pAdapter
        c_uint,                  # DriverType
        c_void_p,                # Software
        c_uint,                  # Flags
        c_void_p,                # pFeatureLevels
        c_uint,                  # FeatureLevels
        c_uint,                  # SDKVersion
        POINTER(c_void_p),       # ppDevice
        POINTER(c_uint),         # pFeatureLevel
        POINTER(c_void_p),       # ppImmediateContext
    ]
    dev = c_void_p()
    ctx = c_void_p()
    fl  = c_uint(0)

    hr = d3d11.D3D11CreateDevice(
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


# ── WGC 세션 ──────────────────────────────────────────────────
class WGCSession:
    """
    단일 HWND 대상 WGC 캡처 세션.
    프레임 콜백에서 staging 텍스처에 복사 → CPU map 으로 numpy 변환.
    staging 텍스처는 크기/포맷 변경 시에만 재할당한다.
    """

    def __init__(self, hwnd: int):
        self.hwnd     = hwnd
        self._frame:   Optional[np.ndarray] = None
        self._lock    = threading.Lock()
        self._closed  = False
        self._init_ok = False

        # raw D3D11 자원 (반드시 close 에서 Release)
        self._d3d_device  = 0     # ID3D11Device*
        self._d3d_context = 0     # ID3D11DeviceContext*
        self._staging     = 0     # ID3D11Texture2D* (staging, BGRA)
        self._stg_w       = 0
        self._stg_h       = 0
        self._stg_fmt     = 0

        # winrt 객체 (참조 유지용)
        self._pool        = None
        self._session_obj = None
        self._d3d_wrapped = None  # IDirect3DDevice (winrt)
        self._frame_token = None  # event token 보관 (해제용)

        # 경로 선택: 첫 프레임에서 vtable 가능 여부 판별
        self._can_vtable  = None  # None=미정, True/False

        if not _WGC_AVAILABLE:
            return

        try:
            if _WGC_ENGINE == "winrt":
                self._init_winrt()
            else:
                # winsdk 경로는 미구현 — winrt 패키지 사용 권장
                print("[WGC] winsdk 경로는 미구현. winrt-runtime 패키지를 설치해 주세요.")
        except Exception as e:
            print(f"[WGC] 세션 초기화 실패: {e}")
            self._release_d3d()

    # ── winrt 초기화 ──────────────────────────────────────────
    def _init_winrt(self):
        import winrt.windows.graphics.capture          as wgc
        import winrt.windows.graphics.directx          as dx
        import winrt.windows.graphics.directx.direct3d11 as d3d11
        from   winrt.windows.graphics.capture import Direct3D11CaptureFramePool

        # 1) HWND → GraphicsCaptureItem
        item = self._hwnd_to_item(wgc)
        if item is None:
            return

        # 2) D3D11 디바이스 + IDXGIDevice 래핑
        self._d3d_device, self._d3d_context = _create_d3d11_raw()
        dxgi_ptr = _com_query(self._d3d_device, IID_IDXGIDevice)
        if not dxgi_ptr:
            raise OSError("QueryInterface(IDXGIDevice) 실패")
        try:
            # winrt 헬퍼는 IDXGIDevice* 의 정수 주소를 받는다
            self._d3d_wrapped = d3d11.create_direct3_d11_device_from_dxgi_device(int(dxgi_ptr))
        finally:
            _com_release(dxgi_ptr)

        # 3) FramePool — BGRA8, 2 프레임 버퍼
        size = item.size
        self._pool = Direct3D11CaptureFramePool.create(
            self._d3d_wrapped,
            dx.DirectXPixelFormat.B8_G8_R8_A8_UINT_NORMALIZED,
            2,
            size,
        )
        self._session_obj = self._pool.create_capture_session(item)

        # 4) 프레임 도착 콜백
        try:
            self._frame_token = self._pool.add_frame_arrived(self._on_frame_arrived)
        except AttributeError:
            # 구버전: += 연산자 사용
            self._pool.frame_arrived += self._on_frame_arrived
            self._frame_token = None

        # 5) 캡처 시작
        self._session_obj.start_capture()
        self._init_ok = True
        print(f"[WGC] 캡처 시작: hwnd={self.hwnd:#x}, size={size.width}x{size.height}")

    def _hwnd_to_item(self, wgc_mod):
        try:
            if hasattr(wgc_mod.GraphicsCaptureItem, 'create_for_window'):
                return wgc_mod.GraphicsCaptureItem.create_for_window(self.hwnd)
            return None
        except Exception as e:
            print(f"[WGC] HWND→Item 변환 실패: {e}")
            return None

    # ── 프레임 도착 콜백 ─────────────────────────────────────
    def _on_frame_arrived(self, pool, _):
        if self._closed:
            return
        try:
            frame = pool.try_get_next_frame()
            if frame is None:
                return
            try:
                size    = frame.content_size
                surface = frame.surface
                arr     = self._surface_to_numpy(surface, size)
                if arr is not None:
                    with self._lock:
                        self._frame = arr
            finally:
                # Direct3D11CaptureFrame 는 Close 가 필수 — 누수 방지
                try: frame.close()
                except Exception: pass
        except Exception:
            pass

    # ── 표면 → numpy (vtable 우선, SoftwareBitmap 폴백) ───────
    def _surface_to_numpy(self, surface, size) -> Optional[np.ndarray]:
        if self._can_vtable is not False:
            arr = self._surface_via_vtable(surface)
            if arr is not None:
                self._can_vtable = True
                return arr
            if self._can_vtable is None:
                # 첫 시도 실패 → 영구 폴백
                print("[WGC] vtable 경로 사용 불가 — SoftwareBitmap 폴백으로 전환")
                self._can_vtable = False
        return self._surface_via_softbmp(surface)

    # ── vtable 경로 ─────────────────────────────────────────
    def _surface_via_vtable(self, surface) -> Optional[np.ndarray]:
        insp = _unwrap_iinspectable(surface)
        if not insp:
            return None

        access = _com_query(insp, IID_IDirect3DDxgiInterfaceAccess)
        if not access:
            return None

        tex_ptr = 0
        try:
            # IDirect3DDxgiInterfaceAccess::GetInterface(__uuidof(ID3D11Texture2D), &tex)
            out   = c_void_p()
            proto = WINFUNCTYPE(c_long, c_void_p, POINTER(GUID), POINTER(c_void_p))
            hr    = _call(access, _VT_GetInterface, proto)(
                        access, byref(IID_ID3D11Texture2D), byref(out))
            if hr < 0 or not out.value:
                return None
            tex_ptr = out.value

            # ID3D11Texture2D::GetDesc
            desc  = D3D11_TEXTURE2D_DESC()
            proto = WINFUNCTYPE(None, c_void_p, POINTER(D3D11_TEXTURE2D_DESC))
            _call(tex_ptr, _VT_Tex2D_GetDesc, proto)(tex_ptr, byref(desc))
            w, h, fmt = desc.Width, desc.Height, desc.Format
            if w == 0 or h == 0:
                return None

            # staging 텍스처 (크기·포맷 변경 시에만 재할당)
            staging = self._ensure_staging(w, h, fmt)
            if not staging:
                return None

            # ID3D11DeviceContext::CopyResource(dst=staging, src=tex)
            proto = WINFUNCTYPE(None, c_void_p, c_void_p, c_void_p)
            _call(self._d3d_context, _VT_Ctx_CopyResource, proto)(
                self._d3d_context, staging, tex_ptr)

            # ID3D11DeviceContext::Map(staging, 0, MAP_READ, 0, &mapped)
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
                row_pitch = mapped.RowPitch
                # RowPitch 는 GPU 정렬로 인해 w*4 보다 클 수 있음 → 안전 슬라이스
                buf  = (c_ubyte * (row_pitch * h)).from_address(mapped.pData)
                view = np.frombuffer(buf, dtype=np.uint8).reshape(h, row_pitch // 4, 4)
                # BGRA → RGB, 실제 폭만 잘라 복사 (Unmap 이후엔 pData 무효)
                return view[:, :w, 2::-1].copy()
            finally:
                proto = WINFUNCTYPE(None, c_void_p, c_void_p, c_uint)
                _call(self._d3d_context, _VT_Ctx_Unmap, proto)(
                    self._d3d_context, staging, 0)
        finally:
            if tex_ptr:
                _com_release(tex_ptr)
            _com_release(access)

    def _ensure_staging(self, w: int, h: int, fmt: int) -> int:
        if (self._staging and self._stg_w == w and
                self._stg_h == h and self._stg_fmt == fmt):
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
            print(f"[WGC] CreateTexture2D(staging) 실패: hr={hr:#010x}")
            return 0

        self._staging = out.value
        self._stg_w, self._stg_h, self._stg_fmt = w, h, fmt
        return self._staging

    # ── SoftwareBitmap 폴백 (winrt 내부 API 노출이 막혔을 때) ──
    def _surface_via_softbmp(self, surface) -> Optional[np.ndarray]:
        try:
            from winrt.windows.graphics.imaging import (
                SoftwareBitmap, BitmapAlphaMode,
            )
        except ImportError:
            return None
        try:
            op  = SoftwareBitmap.create_copy_from_surface_async(
                surface, BitmapAlphaMode.PREMULTIPLIED)

            done   = threading.Event()
            result = [None]

            def _cb(operation, _status):
                try:
                    result[0] = operation.get_results()
                except Exception:
                    result[0] = None
                finally:
                    done.set()

            op.completed = _cb
            if not done.wait(timeout=0.5):
                return None
            softbmp = result[0]
            if softbmp is None:
                return None

            w, h = softbmp.pixel_width, softbmp.pixel_height
            buf  = bytearray(w * h * 4)
            softbmp.copy_to_buffer(buf)
            arr  = np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 4)
            return arr[:, :, 2::-1].copy()
        except Exception:
            return None

    # ── 공개 API ──────────────────────────────────────────────
    def get_latest_frame(self) -> Optional[np.ndarray]:
        if not self._init_ok:
            return None
        with self._lock:
            # numpy 배열은 immutable 처럼 사용 — 호출자가 수정/슬라이스 해도 안전.
            # 매 호출 copy() 는 비용이 크므로 같은 객체 반환.
            return self._frame

    @property
    def ok(self) -> bool:
        return self._init_ok

    def close(self):
        if self._closed:
            return
        self._closed = True

        # 콜백 해제
        try:
            if self._pool and self._frame_token is not None:
                self._pool.remove_frame_arrived(self._frame_token)
        except Exception:
            pass

        for attr in ("_session_obj", "_pool"):
            obj = getattr(self, attr, None)
            if obj is None:
                continue
            try:
                obj.close()
            except Exception:
                pass
            setattr(self, attr, None)

        self._d3d_wrapped = None
        self._release_d3d()
        print(f"[WGC] 세션 종료: hwnd={self.hwnd:#x}")

    def _release_d3d(self):
        if self._staging:
            _com_release(self._staging);     self._staging = 0
        if self._d3d_context:
            _com_release(self._d3d_context); self._d3d_context = 0
        if self._d3d_device:
            _com_release(self._d3d_device);  self._d3d_device = 0

    def __del__(self):
        # 명시적 close 가 안 됐을 때 최후의 안전망
        try:
            self.close()
        except Exception:
            pass


# ── 인스톨 가이드 ──────────────────────────────────────────────
def print_install_guide():
    print("""
=== 고성능 캡처 라이브러리 설치 가이드 ===

1. WGC (Windows Graphics Capture) - GPU 가속, 비활성/게임 창 캡처:
   pip install winrt-runtime
   pip install "winrt-Windows.Graphics.Capture"
   pip install "winrt-Windows.Graphics.DirectX"
   pip install "winrt-Windows.Graphics.DirectX.Direct3D11"
   pip install "winrt-Windows.Graphics.Imaging"     # 호환성 폴백
   → 비활성/게임창 완벽 캡처, 노란 테두리 표시

2. BitBlt - GDI 기본 (추가 설치 불필요):
   → 화면에 보이는 영역만, 하드웨어 가속 창은 검은 화면 가능성
""")


if __name__ == "__main__":
    print(f"WGC 가용: {wgc_available()} (엔진: {_WGC_ENGINE})")
    print_install_guide()
