"""
WinMagnifier — 실행 진입점
"""
import sys, os
from pathlib import Path

if sys.platform != "win32":
    print("Windows에서만 동작합니다.")
    sys.exit(1)

os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "1")
os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING",   "1")

if getattr(sys, "frozen", False):
    _BASE = Path(sys._MEIPASS)
else:
    _BASE = Path(__file__).parent

from PySide6.QtWidgets import QApplication
from PySide6.QtCore    import Qt
from core.magnifier_manager import MagnifierManager
from ui.tray_icon           import SystemTrayIcon, load_tray_icon
from utils.settings         import Settings


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

    mgr  = MagnifierManager(settings)
    tray = SystemTrayIcon(mgr, settings)
    tray.show()

    print("━" * 40)
    print("  WinMagnifier 실행 중")
    print("  트레이 아이콘 더블클릭: 새 확대기")
    print("  트레이 아이콘 우클릭: 메뉴")
    print("━" * 40)

    ret = app.exec()
    settings.save()
    sys.exit(ret)


if __name__ == "__main__":
    main()
