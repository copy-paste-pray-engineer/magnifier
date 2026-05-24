# CLAUDE.md — WinMagnifier 기술 노트

이 문서는 WinMagnifier v2.2 의 캡처 파이프라인 수정·최적화 결정을 정리한다.
주된 변경은 `capture/wgc_capture.py` 의 전면 재작성과,
`capture/capture_engine.py` / `ui/magnifier_window.py` / `ui/tray_icon.py` 의
리소스 라이프사이클 정리다.

---

## 1. 수정된 버그

### Bug 1 — 게임 캡처 첫 프레임 이후 멈춤
### Bug 2 — Chrome 등 하드웨어 가속 창 검은 화면

두 버그는 같은 뿌리에서 나온다. `_surface_to_numpy()` 가
`surface.as_bytearray()` 라는 **존재하지 않는 메서드**를 호출했기 때문이다.
`IDirect3DSurface` 에는 그런 헬퍼가 없다. 호출이 매 프레임 예외를 던지고
`get_latest_frame()` 은 영구히 `None` 을 반환했다. 그러면 상위
`CaptureEngine.capture()` 가 `BitBlt` 폴백으로 떨어지는데,
BitBlt 는 GDI 기반이라

* Chrome 처럼 하드웨어 가속을 쓰는 창은 → 검은 화면
* 게임의 D3D 백버퍼는 → 첫 화면 캐시 또는 검은 화면

이라는 증상으로 나타났다.

### 진짜 해결책: WinRT 표면을 D3D11 텍스처로 언랩하여 직접 읽기

C++/WinRT 표준 패턴을 ctypes 로 재현했다.

```
IDirect3DSurface  ──QI──▶  IDirect3DDxgiInterfaceAccess
                            │
                            ▼  GetInterface(IID_ID3D11Texture2D)
                          ID3D11Texture2D (GPU side)
                            │
                            ▼  ID3D11DeviceContext::CopyResource
                          ID3D11Texture2D (staging, CPU readable)
                            │
                            ▼  Map(D3D11_MAP_READ)
                          픽셀 바이트 → numpy
```

### 수정된 vtable 인덱스 (D3D11 SDK 표준, 32/64-bit 동일)

| 인터페이스 | 메서드 | 슬롯 | 비고 |
|---|---|---|---|
| `IUnknown` | QueryInterface / AddRef / Release | 0 / 1 / 2 | — |
| `IDirect3DDxgiInterfaceAccess` | GetInterface | **3** | IUnknown 직후 |
| `ID3D11DeviceChild` (Texture2D 상속체인) | GetDevice | **3** | — |
| `ID3D11Texture2D` | GetDesc | **10** | DeviceChild(3-6) + Resource(7-9) + Texture2D(10) |
| `ID3D11Device` | CreateTexture2D | **5** | — |
| `ID3D11Device` | GetImmediateContext | **40** | — |
| `ID3D11DeviceContext` | Map | **14** | — |
| `ID3D11DeviceContext` | Unmap | **15** | — |
| `ID3D11DeviceContext` | **CopyResource** | **47** | 이전 코드의 `[48]` 은 오기 (= UpdateSubresource) |

### "32/64-bit 으로 슬롯이 어긋난다" 는 사실인가?

아니다. **vtable 인덱스는 비트와 무관하게 동일**하다.
달라지는 것은 함수 포인터의 폭(4B/8B)이며, 이는 `ctypes.c_void_p` 가
호스트 워드 폭으로 자동 정렬되므로 인덱싱이 깨질 일은 없다.
실제 원인은 `[48]` 이 그냥 잘못된 인덱스였던 것이다.
"여러 SDK 버전" 도 마찬가지 — `ID3D11DeviceContext` 의 메서드 순서는
D3D11 헤더에서 고정이며, `ID3D11DeviceContext1/2/3` 는 별개 IID 의
파생 인터페이스라 기존 슬롯을 침범하지 않는다.

### vtable 호출 코드

```python
def _vtbl(p: int) -> ctypes.Array:
    vtbl_addr = ctypes.cast(p, POINTER(c_void_p))[0]
    return ctypes.cast(vtbl_addr, POINTER(c_void_p * 128))[0]

def _call(p: int, idx: int, proto):
    return proto(_vtbl(p)[idx])
```

`WINFUNCTYPE` 으로 `stdcall` ABI 를 명시적으로 선언해 두면 32/64-bit
모두에서 정확히 호출된다.

---

## 2. WinRT 객체로부터 IInspectable 포인터 얻기

`winrt-runtime` 은 내부 ABI 포인터를 공식 API 로 노출하지 않는다.
패키지 버전(1.x / 2.x, winsdk) 마다 사용 가능한 접근자가 다르다.
따라서 `_unwrap_iinspectable()` 은 다음 순서로 best-effort 탐색한다.

1. 인스턴스 속성: `_ptr`, `_inspectable_ptr`, `_iunknown_ptr`,
   `_thisptr`, `_default_interface_ptr`, `_addr`
2. `winrt._winrt` 모듈의 헬퍼: `_iinspectable_address`,
   `_iunknown_address`, `to_abi_int` 등

이 모두가 실패하면 **SoftwareBitmap 폴백**으로 전환한다
(자세한 내용은 §3). 첫 프레임에서 vtable 경로가 한 번 성공하면
`_can_vtable = True` 로 고정해 이후 분기 비용을 0 으로 만든다.

> 추출된 IInspectable 포인터는 Python 래퍼가 소유한 것이다.
> `Release` 를 호출해서는 안 된다. QI/GetInterface 로 새로 얻은
> 포인터(`access`, `tex_ptr`)는 우리가 소유하므로 반드시 `_com_release`.

---

## 3. SoftwareBitmap 폴백

WinRT 의 `Windows.Graphics.Imaging.SoftwareBitmap.CreateCopyFromSurfaceAsync`
는 IInspectable 언랩 없이도 동작한다. 비동기 API 이지만
`asyncio` 의존을 만들지 않기 위해 `IAsyncOperation.completed` 콜백 +
`threading.Event` 조합으로 동기 대기한다 (500ms 타임아웃).

```python
op = SoftwareBitmap.create_copy_from_surface_async(surface, ...)
op.completed = lambda op, _: (done.set(), result.append(op.get_results()))
done.wait(0.5)
```

이 경로는 GPU↔CPU 왕복이 한 번 더 발생해서 vtable 경로보다 느리지만,
**모든 winrt-runtime 버전에서 안정 동작**한다는 점이 핵심이다.

---

## 4. ID3D11Device 생성의 추가 버그

기존 코드는 `D3D11CreateDevice` 의 `argtypes/restype` 를 설정하지 않아
64-bit 에서 포인터 인자가 잘림 가능성이 있었다. 또한
`create_direct3_d11_device_from_dxgi_device()` 에 **`ID3D11Device*`** 를
바로 넘기고 있었는데, 이 헬퍼는 **`IDXGIDevice*`** 를 요구한다 →
인터페이스 불일치로 WGC 초기화가 실패하거나 잘못된 디바이스로 진행되었다.

수정:

```python
self._d3d_device, self._d3d_context = _create_d3d11_raw()
dxgi_ptr = _com_query(self._d3d_device, IID_IDXGIDevice)
self._d3d_wrapped = d3d11.create_direct3_d11_device_from_dxgi_device(int(dxgi_ptr))
_com_release(dxgi_ptr)
```

`D3D11CreateDevice` 도 모든 인자 타입을 명시했다.

---

## 5. 성능 최적화

### Staging 텍스처 재사용

이전 코드는 그 자체로 staging 텍스처를 만들지 않았지만, 일반적인
잘못은 매 프레임 `CreateTexture2D(staging)` → `Release` 다. 새 구현은
크기·포맷이 바뀔 때만 재할당한다 (`_ensure_staging`).
대상 창이 리사이즈되지 않는 한 60fps 에서 GPU 자원 할당은 0회/sec.

### RowPitch 처리

`Map` 이 돌려주는 `RowPitch` 는 GPU 정렬로 인해 `width*4` 보다 클 수
있다. 안전한 변환:

```python
view = np.frombuffer(buf, dtype=np.uint8).reshape(h, row_pitch // 4, 4)
return view[:, :w, 2::-1].copy()   # BGRA → RGB, 실제 폭만 남기고 복사
```

`Unmap` 이후 `pData` 가 무효화되므로 `.copy()` 는 필수다 (옵션 아님).

### QImage 복사 제거

`magnifier_window._on_frame` 에서 매 프레임:

```python
self._frame = QImage(frame.tobytes(), ...).copy()    # ← 두 번 복사
```

`frame.tobytes()` 가 이미 새 바이트열을 만들므로 `QImage.copy()` 는
같은 메모리를 한 번 더 복제하는 낭비였다. 수정:

```python
self._frame_buf = frame.tobytes()                    # ← 한 번만
self._frame = QImage(self._frame_buf, ..., Format_RGB888)
```

`QImage` 가 데이터 소유권을 갖지 않으므로 `self._frame_buf` 로
참조를 유지한다 (GC 안전).
1080p 60fps 기준 약 370MB/s 의 메모리 대역폭 절약.

### `get_latest_frame()` 의 불필요한 `.copy()` 제거

콜백이 매번 새 ndarray 를 `self._frame` 에 대입하므로
이전 참조는 유효하게 살아 있다. 호출자는 곧장 `tobytes()` 로 복사하므로
`get_latest_frame()` 단계에서 또 복사할 이유가 없다.

---

## 6. 리소스 라이프사이클

### COM 객체 Release

`WGCSession.close()` 가 모든 raw COM 포인터를 풀고, `__del__` 도
같은 경로를 한 번 더 호출해 안전망을 둔다.

| 포인터 | 출처 | 해제 책임 |
|---|---|---|
| `_d3d_device`, `_d3d_context` | `D3D11CreateDevice` | 세션 close |
| `_staging` | `CreateTexture2D` | 세션 close 또는 재할당 직전 |
| QI 로 얻은 `access`, `tex_ptr` | 프레임마다 신규 | 프레임 처리 직후 `_com_release` |
| `dxgi_ptr` | `IDXGIDevice` QI | `create_direct3_d11_device_from_dxgi_device` 직후 |
| IInspectable 주소 | winrt 래퍼 소유 | **건드리지 않음** |

### WGC 세션 참조 카운트

`CaptureEngine` 에 `acquire_wgc(hwnd)` / `release_wgc(hwnd)` 추가.
같은 hwnd 를 여러 확대기가 공유할 때 마지막 확대기가 닫혀야만
세션이 종료되도록 한다. `MagnifierWindow` 가

* 생성 시 `acquire_wgc(target_hwnd)`
* `_capture_cursor_window` 에서 hwnd 가 바뀌면 old release / new acquire
* `closeEvent` 에서 `release_wgc`

순서로 호출한다. 트레이의 `_quit` 가 `CaptureEngine.close()` 를 호출해
앱 종료 시 잔여 세션과 D3D11 디바이스를 모두 회수한다.

### Direct3D11CaptureFrame.close()

WGC 프레임 객체는 `Close` 가 필수 (내부적으로 텍스처와 펜스 보유).
프레임 콜백에서 `try/finally` 로 보장한다 — 누수 시 GPU 메모리가
프레임 풀 한 개 분량씩 누적된다.

### frame_arrived 콜백 해제

`Direct3D11CaptureFramePool.add_frame_arrived` 가 반환한 토큰을 보관해
`close()` 에서 `remove_frame_arrived(token)` 로 명시 해제한다.
구버전 API (`+=` 패턴) 도 폴백으로 처리.

---

## 7. 제거한 죽은 코드

`wgc_capture.py` 의 `DXGICapture`, 같은 파일의 `DWMThumbnail` 클래스는
어디서도 참조되지 않았다 (DWM 은 `ui/dwm_overlay.py` 의 `DWMOverlay`
가 담당). 유지보수 부담만 늘었으므로 제거.

---

## 8. 캡처 경로 결정 로직 (최종)

```
CaptureEngine.capture(hwnd, x, y, w, h):
  ├─ active=="wgc" and hwnd?
  │    └─ WGCSession.get_latest_frame()  → 성공 시 종료
  │       (실패 시 BitBlt 로 폴백)
  └─ BitBlt (GetDC(hwnd) 또는 GetDC(NULL))
```

```
WGCSession._surface_to_numpy:
  ├─ vtable 경로 시도
  │    ├─ IInspectable 추출 성공 + GetInterface 성공
  │    │   → CopyResource + Map → numpy   (영구 사용)
  │    └─ 첫 시도 실패 → _can_vtable = False
  └─ SoftwareBitmap 폴백 (영구)
```

선택은 **첫 프레임에서 단 한 번** 결정되며 이후 분기 오버헤드는 없다.

---

## 9. 빌드/실행 메모

* `winrt-runtime` 미설치 환경에서는 자동으로 BitBlt 만 사용한다.
* WGC 노란 테두리는 Windows 10 2004 부터 기본 활성. 비활성 옵션은
  Windows 11 22H2 에서 `GraphicsCaptureSession.is_border_required` 로
  토글 가능 — 필요 시 추가 구현 여지가 있다.
* SoftwareBitmap 폴백을 강제 활성화하려면 `_can_vtable` 의 초기값을
  `False` 로 두면 된다 (디버깅용).

---

## 10. 변경된 파일 요약

| 파일 | 변경 |
|---|---|
| `capture/wgc_capture.py` | 전면 재작성 (vtable+SoftwareBitmap, 자원 관리, 죽은 코드 제거) |
| `capture/capture_engine.py` | `acquire_wgc` / `release_wgc` 추가, refcount 관리 |
| `ui/magnifier_window.py` | acquire/release 호출, `_frame_buf` 도입으로 QImage 복사 제거 |
| `ui/tray_icon.py` | 종료 시 `capture_engine.close()` 호출 |
