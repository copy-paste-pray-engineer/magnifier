"""
WinMagnifier — 컴포넌트 테스트
실행: python test_capture.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))


def test_screen_capture() -> None:
    print("=== 화면 캡처 테스트 ===")
    from capture.capture_engine import CaptureEngine
    engine = CaptureEngine("auto")
    frame  = engine.capture_screen_region(100, 100, 300, 200)
    if frame is not None:
        print(f"  성공: shape={frame.shape}, dtype={frame.dtype}")
        try:
            from PIL import Image
            Image.fromarray(frame).save("test_capture.png")
            print("  test_capture.png 저장 완료")
        except ImportError:
            print(f"  배열 샘플: {frame[:2, :2, :]}")
    else:
        print("  실패")


def test_window_capture() -> None:
    print("\n=== 창 캡처 테스트 ===")
    import ctypes
    from capture.capture_engine import CaptureEngine
    engine = CaptureEngine("auto")
    hwnd   = ctypes.windll.user32.GetForegroundWindow()
    title  = engine.get_window_title(hwnd)
    x, y, w, h = engine.get_window_rect(hwnd)
    print(f"  대상 창: '{title}'  위치/크기: ({x},{y}) {w}x{h}")
    if w > 0 and h > 0:
        frame = engine.capture(hwnd, x, y, min(300, w), min(200, h))
        if frame is not None:
            print(f"  성공: shape={frame.shape}")
            try:
                from PIL import Image
                Image.fromarray(frame).save("test_window.png")
                print("  test_window.png 저장 완료")
            except ImportError:
                pass
        else:
            print("  실패 (창 캡처 불가)")


def test_fps() -> None:
    print("\n=== FPS 테스트 (1초) ===")
    import time
    import ctypes
    from capture.capture_engine import CaptureEngine
    engine   = CaptureEngine("auto")
    hwnd     = ctypes.windll.user32.GetForegroundWindow()
    x, y, w, h = engine.get_window_rect(hwnd)
    count    = 0
    deadline = time.perf_counter() + 1.0
    while time.perf_counter() < deadline:
        frame = engine.capture(hwnd, x, y, min(300, w), min(200, h))
        if frame is not None:
            count += 1
    print(f"  {count} fps")


if __name__ == "__main__":
    print("WinMagnifier 컴포넌트 테스트\n")
    try:
        test_screen_capture()
        test_window_capture()
        test_fps()
    except Exception:
        import traceback
        traceback.print_exc()
    print("\n완료")
