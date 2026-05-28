"""
WinMagnifier — 실행 진입점
"""
import sys, os
from pathlib import Path

if sys.platform != "win32":
    print("Windows에서만 동작합니다.")
    sys.exit(1)


def _ensure_admin():
    """
    관리자 권한 강제.
    하드웨어 가속 게임 창(DirectX/OpenGL)에 PrintWindow/WGC 가 후킹하려면
    동등 이상의 무결성 레벨이 필요합니다. 일반 권한이면 캡처가 빈 화면이
    되거나 대상 창을 잡지 못합니다.

    이미 관리자면 그대로 진행. 아니면 ShellExecuteW("runas") 로 UAC 띄우고
    현재 프로세스는 종료합니다. 사용자가 UAC 거부하면 종료 코드 1.
    """
    import ctypes
    shell32 = ctypes.windll.shell32
    try:
        if shell32.IsUserAnAdmin():
            return
    except Exception:
        return  # API 호출 실패 시 그냥 진행

    # 재실행 명령 구성
    if getattr(sys, "frozen", False):
        exe    = sys.executable
        params = " ".join(f'"{a}"' for a in sys.argv[1:])
    else:
        exe    = sys.executable
        script = os.path.abspath(sys.argv[0])
        params = " ".join(f'"{a}"' for a in [script] + sys.argv[1:])

    cwd = os.path.dirname(os.path.abspath(sys.argv[0]))
    # SW_SHOWNORMAL = 1
    ret = shell32.ShellExecuteW(None, "runas", exe, params, cwd, 1)
    if ret <= 32:
        # 5 = SE_ERR_ACCESSDENIED (사용자가 UAC 거부)
        print(f"관리자 권한이 필요합니다. (ShellExecuteW 코드: {ret})")
        sys.exit(1)
    sys.exit(0)


_ensure_admin()

os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "1")
os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING",   "1")

if getattr(sys, "frozen", False):
    _BASE = Path(sys._MEIPASS)
else:
    _BASE = Path(__file__).parent

# 프로젝트 루트를 sys.path 맨 앞에 추가 — IDE나 다른 디렉토리에서 실행해도
# capture / ui / core / utils 패키지를 찾을 수 있도록 보장합니다.
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore    import Qt
from core.magnifier_manager import MagnifierManager
from ui.tray_icon           import SystemTrayIcon, load_tray_icon
from utils.settings         import Settings
from utils.hotkeys          import HotkeyManager


def main():
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("WinMagnifier")
    app.setApplicationVersion("2.2.0")
    app.setWindowIcon(load_tray_icon())

    settings = Settings()
    settings.load()

    mgr        = MagnifierManager(settings)
    hotkey_mgr = HotkeyManager(settings)
    hotkey_mgr.load_and_register()
    hotkey_mgr.opacity_toggled.connect(mgr.toggle_all_opacity)
    hotkey_mgr.clickthrough_toggled.connect(mgr.toggle_all_clickthrough)

    tray = SystemTrayIcon(mgr, settings, hotkey_mgr)
    tray.show()

    print("━" * 40)
    print("  WinMagnifier 실행 중")
    print("  트레이 아이콘 더블클릭: 새 확대기")
    print("  트레이 아이콘 우클릭: 메뉴")
    print("━" * 40)

    ret = app.exec()
    hotkey_mgr.close()
    settings.save()
    sys.exit(ret)


if __name__ == "__main__":
    main()
