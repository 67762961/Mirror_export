# Build Mirror_export.exe via PyInstaller
# Usage: .build-venv\Scripts\python.exe build_exe.py
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parent
BUILD = ROOT / "build_pyinstaller"
DIST = ROOT / "dist"
EXE_NAME = "Mirror_export"

def main() -> int:
    from PyInstaller.__main__ import run

    # clean previous build artifacts but keep existing token cache in dist
    for p in (BUILD, ROOT / "dist" / EXE_NAME):
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)
    for name in (f"{EXE_NAME}.exe", "github-mirror-export.exe"):
        old_exe = DIST / name
        if old_exe.exists():
            old_exe.unlink()

    DIST.mkdir(exist_ok=True)

    args = [
        str(ROOT / "app_gui.py"),
        "--name", EXE_NAME,
        "--onefile",
        "--windowed",
        "--noconfirm",
        "--clean",
        "--distpath", str(DIST),
        "--workpath", str(BUILD / "work"),
        "--specpath", str(BUILD),
        # icons for window / taskbar
        "--icon", str(ROOT / "assets" / "icon.ico"),
        # runtime data next to _MEIPASS: viewer/* and assets icons
        "--add-data", f"{ROOT / 'viewer'}{';' if sys.platform == 'win32' else ':'}viewer",
        "--add-data", f"{ROOT / 'assets' / 'icon.ico'}{';' if sys.platform == 'win32' else ':'}assets",
        "--add-data", f"{ROOT / 'assets' / 'icon-256.png'}{';' if sys.platform == 'win32' else ':'}assets",
        # ensure export module is bundled
        "--hidden-import", "export",
        "--collect-submodules", "tkinter",
    ]
    print("Running PyInstaller:")
    print(" ", "\n  ".join(args))
    run(args)

    # drop leftover onedir folder if present
    leftover_dir = DIST / EXE_NAME
    if leftover_dir.is_dir():
        shutil.rmtree(leftover_dir, ignore_errors=True)

    exe = DIST / f"{EXE_NAME}.exe"
    if not exe.exists():
        found = list(DIST.rglob(f"{EXE_NAME}.exe"))
        if not found:
            print("ERROR: exe not found", file=sys.stderr)
            return 1
        exe = found[0]
    print(f"OK: {exe}")
    print(f"Size: {exe.stat().st_size / 1024 / 1024:.2f} MB")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
