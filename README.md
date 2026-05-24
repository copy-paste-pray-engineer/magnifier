# WinMagnifier v2.2

Windows 전용 실시간 화면 확대기 — Python + PySide6

---

## 설치

```bash
pip install PySide6 pywin32 numpy Pillow
python main.py
```

고성능 모드(WGC) 사용 시 추가 설치:
```bash
pip install winrt-runtime "winrt-Windows.Graphics.Capture"
```

---

## 사용법

**트레이 아이콘 더블클릭** → 새 확대기 생성  
**트레이 아이콘 우클릭** → 메뉴 (새 확대기 / 확대기 목록 / 종료)

### 확대기 조작

| 조작 | 동작 |
|------|------|
| 빨간 박스 드래그 | 캡처 영역 이동 |
| 빨간 박스 테두리 드래그 | 캡처 영역 크기 조절 |
| 방향키 | 캡처 영역 1px 이동 |
| Shift + 방향키 | 캡처 영역 10px 이동 |
| 출력창 드래그 | 출력창 이동 |
| 출력창 테두리 드래그 | 출력창 크기 조절 (배율 자동 변경) |
| 마우스 휠 | 투명도 조절 |
| 우클릭 | 메뉴 (설정 패널 / 대상 창 지정 / 닫기) |

### 설정 패널 (우클릭 → 설정 패널)

- **고성능 (WGC)** : GPU 가속, 비활성/가려진 창 캡처 지원
- **저사양 (DWM)** : CPU 사용 최소, 추가 설치 불필요
- 투명도 슬라이더
- FPS 제한 슬라이더
- HUD 오버레이 ON/OFF
- 클릭 투과 켜기/끄기 (투과 상태에서는 트레이 목록으로 제어)
- 대상 창 지정

### 클릭 투과 상태 제어

출력창이 클릭 투과 상태이면 우클릭 메뉴에 접근할 수 없습니다.  
이때는 **트레이 아이콘 → 확대기 목록 → 해당 확대기 → 클릭 투과 토글** 로 해제하세요.

---

## 캡처 방법

| 모드 | 방법 | 특징 |
|------|------|------|
| 고성능 | WGC (Windows Graphics Capture) | GPU 가속, 비활성 창 완벽 지원, winrt 패키지 필요 |
| 고성능 fallback | BitBlt | winrt 없을 때 자동 전환, 화면에 보이는 영역만 |
| 저사양 | DWM 썸네일 | CPU 거의 0%, 픽셀 접근 불가, 추가 설치 불필요 |

---

## exe 빌드

```bash
pip install pyinstaller
python build.py              # onedir (빠른 시작)
python build.py --onefile    # 단일 exe
python build.py --debug      # 콘솔 표시
```

---

## 파일 구조

```
magnifier/
├── main.py
├── requirements.txt
├── install.bat
├── build.py
├── magnifier.ico
│
├── core/
│   └── magnifier_manager.py
│
├── capture/
│   ├── capture_engine.py    (WGC / BitBlt 통합)
│   └── wgc_capture.py       (WGC winrt 연동)
│
├── ui/
│   ├── selection_overlay.py (캡처 영역 지정 박스)
│   ├── magnifier_window.py  (출력창)
│   ├── control_panel.py     (설정 패널)
│   ├── dwm_overlay.py       (DWM 썸네일 래퍼)
│   └── tray_icon.py         (트레이 아이콘)
│
└── utils/
    └── settings.py
```

---

MIT License
