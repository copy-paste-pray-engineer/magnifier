@echo off
echo WinMagnifier 패키지 설치
echo ========================
pip install PySide6 pywin32 numpy Pillow
echo.
echo 설치 완료. 실행:
echo   python main.py
echo.
echo 고성능 모드(WGC)를 사용하려면 추가 설치:
echo   pip install winrt-runtime "winrt-Windows.Graphics.Capture"
pause
