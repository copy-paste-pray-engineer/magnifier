"""
WinMagnifier — 실행 진입점
"""
import logging
import logging.handlers
import os
import sys
from pathlib import Path

if sys.platform != "win32":
    print("Windows에서만 동작합니다.")
    sys.exit(1)

logger = logging.getLogger(__name__)

# 참고: 관리자 권한 프로세스의 창은 일반 권한에서는 캡처할 수 없다 (UIPI 제약).
# 그런 창을 캡처해야 할 때만 사용자가 직접 "관리자로 실행" 하면 된다.
# 일반 게임·브라우저 캡처에는 관리자 권한이 필요하지 않으므로 강제하지 않는다.

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


def _setup_logging() -> None:
    """콘솔 + 회전 파일 로깅.

    --noconsole 빌드에서는 stderr 가 없어 콘솔 출력이 증발하므로
    파일 핸들러가 실질적인 진단 수단이다.
    """
    log_dir = Path(os.getenv("APPDATA", ".")) / "WinMagnifier"
    log_dir.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [
        logging.handlers.RotatingFileHandler(
            log_dir / "winmagnifier.log",
            maxBytes=1_000_000, backupCount=2, encoding="utf-8",
        )
    ]
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=handlers,
    )


def main() -> None:
    _setup_logging()

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

    logger.info("WinMagnifier 실행 중 — 트레이 더블클릭: 새 확대기 / 우클릭: 메뉴")

    ret = app.exec()
    hotkey_mgr.close()
    settings.save()
    sys.exit(ret)


if __name__ == "__main__":
    main()
