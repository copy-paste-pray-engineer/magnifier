# CLAUDE.md — WinMagnifier 기술 노트

WinMagnifier v2.2 의 캡처 파이프라인과 주요 설계 결정을 정리한다.
(이전 버전 문서는 winrt-runtime 기반 구현을 설명했으나, 현재 WGC 는
**winrt 패키지 없이 순수 ctypes COM** 으로 재작성되었다.)

---

## 1. 캡처 경로 결정 로직

```
CaptureEngine.capture(hwnd, x, y, w, h):
  ├─ hwnd 없음 → None (화면 전체 캡처 금지 — 재귀 렌더링 방지)
  ├─ active == "wgc"      → WGCSession.get_latest_frame()
  │                          실패 시 PrintWindow 로 폴백
  ├─ active != "bitblt"   → PrintWindow(PW_RENDERFULLCONTENT)
  └─ BitBlt (최후 수단 — DX 창은 검은 화면)
```

* 기본값 `auto` = `printwindow`. DWM 합성 프레임을 복사하므로
  DX/OpenGL 창과 비활성 창을 지원하고, WGC 의 노란 테두리도 없다.
* 캡처 방법은 **엔진 전역 설정** — 모든 확대기가 공유한다.
* WGC 세션 생성 실패는 hwnd 별로 5초간만 캐시(`_wgc_failed_at`) —
  창 최소화 같은 일시적 실패가 영구 차단되지 않는다.
* DWM 썸네일(`ui/dwm_overlay.py`)은 픽셀 복사 없는 GPU 직접 렌더링이라
  이 파이프라인과 별개. `MagnifierWindow` 의 "저사양" 모드가 사용.

## 2. WGC — 순수 ctypes COM (`capture/wgc_capture.py`)

`RoGetActivationFactory` → `IGraphicsCaptureItemInterop::CreateForWindow`
→ `Direct3D11CaptureFramePool.CreateFreeThreaded` → `StartCapture` 전 과정을
vtable 직접 호출로 수행한다. winrt 패키지 의존성 없음.

### 표면 → numpy

```
IDirect3DSurface ──QI──▶ IDirect3DDxgiInterfaceAccess
                          └─ GetInterface(IID_ID3D11Texture2D) → GPU 텍스처
                             └─ CopyResource → staging(재사용) → Map → numpy
```

* staging 텍스처는 크기/포맷 변경 시에만 재할당 (`_ensure_staging`).
* `RowPitch` 는 GPU 정렬로 `width*4` 보다 클 수 있다 —
  `reshape(h, rp//4, 4)[:, :w, 2::-1].copy()`. Unmap 후 pData 무효화되므로
  `.copy()` 필수.

### 프레임 콜백 핸들러 (ITypedEventHandler)

ctypes 로 만든 4칸 vtable COM 객체. **QueryInterface 는 화이트리스트 방식**:
`IUnknown` / `IAgileObject` / handler PIID 만 수락, 그 외 `E_NOINTERFACE`.
무조건 성공시키면 런타임이 IMarshal 등으로 착각해 vtable 범위 밖을 호출
→ access violation. handler PIID 는 WinRT 표준 알고리즘(UUID v5)으로 계산:

```
uuid5(11f47ad5-7b73-42c0-abae-878b1e16adee,
      "pinterface({9de1c534-...};rc(...Direct3D11CaptureFramePool;{24b5d8fd-...});cinterface(IInspectable))")
= f810db63-93f2-5fe6-a429-4bf67d98e90c
```

`IAgileObject` 수락 → free-threaded 풀에서 마샬링 없이 직접 호출됨.

### close() ↔ 콜백 동기화

콜백 본체 전체와 `close()` 의 `_closed` 설정이 같은 `_lock` 을 잡는다.
`close()` 가 락을 얻으면 진행 중이던 콜백이 끝난 것이고, 이후 콜백은
`_closed` 검사로 즉시 반환 → 그 다음에야 D3D 자원을 Release 한다.
`remove_FrameArrived` 는 진행 중 콜백 완료를 보장하지 않으므로 락이 필수.

### 창 리사이즈 대응

콜백마다 `item.get_Size` 를 풀 크기와 비교, 다르면 `FramePool.Recreate`.
풀 텍스처는 생성 시점 크기로 고정되므로 방치하면 잔여 영역이 섞인다.

### 노란 테두리 비활성화

`IsBorderRequired` 는 **IGraphicsCaptureSession3**
(`{F2CDD966-22AE-5EA1-9596-3A289344C3BE}`, Windows 10 21H1+) 소속.
Session2 는 `IsCursorCaptureEnabled` 용 — 혼동 주의 (과거 버그 원인).

### vtable 인덱스 (검증 완료)

| 인터페이스 | 메서드 | 슬롯 |
|---|---|---|
| IUnknown | QI / AddRef / Release | 0 / 1 / 2 |
| IDirect3DDxgiInterfaceAccess | GetInterface | 3 |
| ID3D11Texture2D | GetDesc | 10 |
| ID3D11Device | CreateTexture2D | 5 |
| ID3D11DeviceContext | Map / Unmap / CopyResource | 14 / 15 / 47 |
| IGraphicsCaptureItem | get_Size | 7 |
| IDirect3D11CaptureFramePool | Recreate / TryGetNextFrame / add / remove / CreateCaptureSession | 6 / 7 / 8 / 9 / 10 |
| IGraphicsCaptureSession | StartCapture | 6 |
| IGraphicsCaptureSession3 | get/put_IsBorderRequired | 6 / 7 |
| IClosable | Close | 6 |

IInspectable 상속 인터페이스는 [0-5]=IUnknown+IInspectable, 자체 메서드 [6]부터.
vtable 인덱스는 32/64-bit 동일 (포인터 폭만 다름).

## 3. 리소스 라이프사이클

| 포인터 | 출처 | 해제 책임 |
|---|---|---|
| `_d3d_device`, `_d3d_context` | `D3D11CreateDevice` | 세션 close |
| `_staging` | `CreateTexture2D` | 세션 close 또는 재할당 직전 |
| QI 로 얻은 `access`, `tex_ptr` | 프레임마다 신규 | 프레임 처리 직후 `_com_release` |
| `dxgi_ptr` | `IDXGIDevice` QI | WinRT 디바이스 래핑 직후 |
| 프레임/세션/풀 | WGC | `IClosable::Close` 후 Release |

* `CaptureEngine.acquire_wgc(hwnd)` / `release_wgc(hwnd)` 참조 카운트 —
  같은 hwnd 를 공유하는 마지막 확대기가 닫힐 때 세션 종료.
* 트레이 종료 시 `capture_engine.close()` 가 전 세션 + D3D 디바이스 회수.
* `__init__` 실패 시에도 `close()` 가 부분 생성 자원을 회수 (포인터 개별 검사).

## 4. UI 주의사항

* **`setWindowFlags()` 호출 금지** (네이티브 창 재생성 → `winId` 변경 →
  raw `SetWindowLongW` 클릭 투과 스타일 소실 + DWM 썸네일 대상 무효화).
  topmost 토글은 `SetWindowPos(HWND_TOPMOST/NOTOPMOST)` 사용.
* DWM 모드 해제 시 FPS 는 `config.fps` 로 복원 (60 하드코딩 금지).
* 새 확대기(프리셋 아님)는 생성 직후 자동으로 창 선택 모드 진입 —
  대상 창 없으면 캡처가 시작되지 않아 검은 화면이기 때문.
* `_on_frame`: `frame.tobytes()` 한 번만 복사, `QImage.copy()` 금지.
  QImage 는 소유권이 없으므로 `_frame_buf` 로 참조 유지.

## 5. 설계 결정 기록

* **확대기 자동 저장/복원 제거** — 프리셋("미리 세팅, 필요 시 활성화")과
  역할 중복. 종료 시점 상태 통째 보존은 사용자 요구가 아니었음.
* **관리자 권한 강제 제거** — 관리자 권한은 대상 창이 관리자 프로세스일 때만
  필요 (UIPI). 그 경우 사용자가 직접 "관리자로 실행".
* **PrintWindow 기본** — WGC 대비 노란 테두리 없음 + 의존성 없음.
  단, 매 프레임 창 전체 캡처 후 크롭이라 큰 창에서는 WGC 가 유리.
* **로깅** — `print()` 금지. `%APPDATA%/WinMagnifier/winmagnifier.log`
  (RotatingFileHandler 1MB×2) + 콘솔(stderr 존재 시). `--noconsole` 빌드에서
  파일 로그가 유일한 진단 수단.

## 6. 도구

* `ruff check .` — E701/E702(한 줄 스타일), E402(의도적 지연 import) 제외.
* `mypy` 설정은 pyproject.toml 에 있으나 ctypes 경계 타입 정리는 미완.
* `python test_capture.py` — 캡처 엔진 수동 스모크 테스트.
* `python build.py [--onefile] [--debug]` — PyInstaller 패키징.
  winrt/pywin32 의존성 없음 (requirements.txt 참고).
