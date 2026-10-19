# Build Mirror_export.exe via PyInstaller
# Usage: python build_exe.py            （也可用 .build-venv\Scripts\python.exe）
#
# 打包要点（改之前先读 ARCHITECTURE.md「打包与运行」）：
# 1. 界面已改用 PyQt6 + PyQt6-Fluent-Widgets，**必须先做依赖自检**：缺包时直接报错，
#    否则会打出一个「双击就闪退」的 exe，用户完全看不出原因。
# 2. 必须排除用不到的重依赖（numpy / scipy / PIL / cv2 / QtQuick / QtQml 等）：
#    qfluentwidgets 的毛玻璃模块 `common/image_utils.py` 顶层 import numpy + PIL + scipy，
#    PyInstaller 静态分析会顺着把它全打进去 —— 实测多出约 59 MB，而我们默认用 Mica、
#    根本不用毛玻璃（缺包时它自己有 try/except 兜底退化）。
# 3. **必须保留 pywin32（win32com）**：qframelesswindow 的无边框窗口要 win32comext.shell，
#    排掉会启动即崩（实测 ModuleNotFoundError: No module named 'win32com'）。
# 4. tkinter 已彻底不用（界面换成 Qt），排掉。
from pathlib import Path
import importlib.util
import os
import shutil
import sys

ROOT = Path(__file__).resolve().parent
# 构建产物默认落在项目内（正常机器）；可用环境变量覆盖，便于在受限环境（比如
# 某些沙箱里 Python 无权在项目目录建子目录）把中间产物挪到可写位置。
#   MIRROR_BUILD_DIR   中间产物（work / spec）
#   MIRROR_DIST_DIR    最终 exe
BUILD = Path(os.environ.get("MIRROR_BUILD_DIR") or (ROOT / "build_pyinstaller"))
DIST = Path(os.environ.get("MIRROR_DIST_DIR") or (ROOT / "dist"))
EXE_NAME = "Mirror_export"

# 界面运行时依赖：缺任何一个都不该继续打包
UI_REQUIRED = ("PyQt6", "qfluentwidgets", "qframelesswindow")

# 明确排除的重依赖（体积大头，本程序一行都不用）
EXCLUDE_MODULES = (
    # qfluentwidgets 毛玻璃间接引入的科学计算栈（默认走 Mica，不用毛玻璃）
    "numpy", "scipy", "pandas", "matplotlib", "PIL", "cv2",
    # 已废弃的 Tk 界面
    "tkinter",
    # Qt 的 QML/Quick 全家桶：本项目只用 Widgets
    "PyQt6.QtQuick", "PyQt6.QtQml", "PyQt6.QtQuickWidgets",
    "PyQt6.QtMultimedia", "PyQt6.QtWebEngineCore", "PyQt6.QtWebEngineWidgets",
    "PyQt6.QtPdf", "PyQt6.QtPdfWidgets", "PyQt6.QtDesigner",
)


def check_ui_dependencies() -> bool:
    """界面依赖自检：缺包时给出可操作提示，而不是打出启动即崩的 exe。"""
    missing = [m for m in UI_REQUIRED if importlib.util.find_spec(m) is None]
    if not missing:
        return True
    print("错误: 缺少界面依赖：" + ", ".join(missing), file=sys.stderr)
    print("  处理: pip install PyQt6 PyQt6-Fluent-Widgets", file=sys.stderr)
    print("  （打包必须在装有这些包的解释器里跑；源码运行同样需要它们）", file=sys.stderr)
    return False


def remove_locked_artifact(path: Path) -> bool:
    """删除旧的 exe；被占用时先改名再删。

    为什么不能直接 unlink：Windows 上旧 exe 若还有进程句柄残留（哪怕 Get-Process 已经
    看不到进程），`os.unlink` 会抛 PermissionError，但 **rename 通常能成功**。
    先改名再删能把「明明没在运行却删不掉」这种情况也覆盖掉。
    """
    if not path.exists():
        return True
    try:
        path.unlink()
        return True
    except PermissionError:
        pass
    except OSError:
        pass

    backup = path.with_name(path.name + ".old")
    try:
        if backup.exists():
            backup.unlink()
        path.rename(backup)
    except OSError as e:
        print(f"错误: 无法删除 {path}", file=sys.stderr)
        print(f"  原因: 该 exe 正被占用（{e}）。Windows 会锁住正在执行的程序文件。", file=sys.stderr)
        print("  处理: 请先关闭正在运行的 Mirror_export 窗口，再重新打包。", file=sys.stderr)
        return False
    try:
        backup.unlink()
    except OSError:
        # 删不掉改名后的残留也无所谓，不挡打包
        print(f"提示: 残留 {backup.name} 未能删除，可稍后手动清理", file=sys.stderr)
    return True


def main() -> int:
    if not check_ui_dependencies():
        return 1

    from PyInstaller.__main__ import run

    # clean previous build artifacts but keep existing token cache in dist
    for p in (BUILD, DIST / EXE_NAME):
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)
    for name in (f"{EXE_NAME}.exe", "github-mirror-export.exe"):
        if not remove_locked_artifact(DIST / name):
            return 1

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
        # 本项目的模块：export / gui_core / ui.* 都是运行时动态导入，显式声明更稳
        "--hidden-import", "export",
        "--hidden-import", "gui_core",
    ]
    for mod in EXCLUDE_MODULES:
        args += ["--exclude-module", mod]

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
