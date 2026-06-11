"""
WinMagnifier PyInstaller 빌드 스크립트
실행: python build.py [--onefile] [--debug]
"""
import subprocess
import sys
import shutil
import argparse
from pathlib import Path

HERE = Path(__file__).parent


def clean() -> None:
    for d in ["dist", "build"]:
        p = HERE / d
        if p.exists():
            shutil.rmtree(p)
            print(f"  삭제: {p}")
    for f in HERE.glob("*.spec"):
        f.unlink()
    for f in HERE.glob("runtime_hook.py"):
        f.unlink()


def make_runtime_hook() -> None:
    hook = HERE / "runtime_hook.py"
    hook.write_text(
        "import sys, os\n"
        "if getattr(sys, 'frozen', False):\n"
        "    base = sys._MEIPASS\n"
        "    os.environ.setdefault('PATH', base + os.pathsep + os.environ.get('PATH',''))\n"
        "    os.environ.setdefault('QT_AUTO_SCREEN_SCALE_FACTOR', '1')\n",
        encoding="utf-8",
    )


def make_version_info() -> None:
    vi = HERE / "version_info.txt"
    vi.write_text(
        "VSVersionInfo(\n"
        "  ffi=FixedFileInfo(filevers=(2,2,0,0), prodvers=(2,2,0,0),\n"
        "    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0,0)),\n"
        "  kids=[\n"
        "    StringFileInfo([StringTable('041204b0',[\n"
        "      StringStruct('FileDescription', 'WinMagnifier - 실시간 화면 확대기'),\n"
        "      StringStruct('FileVersion',     '2.2.0.0'),\n"
        "      StringStruct('ProductName',     'WinMagnifier'),\n"
        "      StringStruct('ProductVersion',  '2.2.0.0'),\n"
        "      StringStruct('LegalCopyright',  'MIT License'),\n"
        "    ])]),\n"
        "    VarFileInfo([VarStruct('Translation', [0x0412, 0x04b0])])\n"
        "  ]\n"
        ")\n",
        encoding="utf-8",
    )


def build(onefile=False, debug=False) -> None:
    print("=" * 48)
    print("  WinMagnifier v2.2 빌드 시작")
    print("=" * 48)
    clean()
    make_runtime_hook()
    make_version_info()

    ico = HERE / "magnifier.ico"

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name=WinMagnifier",
        "--noconsole",
        "--noupx",
        f"--add-data={HERE / 'magnifier.ico'};.",
        f"--add-data={HERE / 'magnifier_48.png'};.",
        "--hidden-import=PySide6.QtCore",
        "--hidden-import=PySide6.QtGui",
        "--hidden-import=PySide6.QtWidgets",
        "--hidden-import=numpy",
        # WGC 는 순수 ctypes COM 구현이라 winrt 패키지가 필요 없다.
        "--exclude-module=matplotlib",
        "--exclude-module=scipy",
        "--exclude-module=pandas",
        "--exclude-module=tkinter",
        "--exclude-module=unittest",
        "--runtime-hook=runtime_hook.py",
        "--version-file=version_info.txt",
    ]

    if ico.exists():
        cmd.append(f"--icon={ico}")

    if onefile:
        cmd.append("--onefile")
    else:
        cmd.append("--onedir")

    if debug:
        # 디버그 빌드: 콘솔 보이게
        cmd = [c for c in cmd if c != "--noconsole"]
        cmd.append("--console")

    cmd.append(str(HERE / "main.py"))

    result = subprocess.run(cmd, cwd=str(HERE))

    if result.returncode == 0:
        target = (HERE / "dist" / "WinMagnifier.exe") if onefile \
                 else (HERE / "dist" / "WinMagnifier" / "WinMagnifier.exe")
        if target.exists():
            mb = target.stat().st_size / 1024 / 1024
            print(f"\n  빌드 성공: {target}  ({mb:.1f} MB)")
        else:
            print("\n  빌드 완료 (경로 확인 필요)")
    else:
        print("\n  빌드 실패")
        sys.exit(1)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--onefile", action="store_true", help="단일 exe로 패키징")
    ap.add_argument("--debug",   action="store_true", help="콘솔 표시 (디버그)")
    args = ap.parse_args()
    build(onefile=args.onefile, debug=args.debug)
