#!/usr/bin/env python3
"""Mirror_export · 图形界面入口（PyQt6 + PyQt6-Fluent-Widgets）。

外网运行：填好参数点「开始导出」，生成可拷入内网的只读静态站。
导出核心仍是 export.py（纯标准库），界面只通过 `export.export(args)` 与
stdout 里的 `__PROGRESS__{json}` 机器行交互；与界面无关的逻辑在 gui_core。

本文件只做启动：补 sys.path、检查界面依赖、设置显示缩放策略、创建 QApplication
并拉起 ui.window。
"""

from __future__ import annotations

import sys
from pathlib import Path

# 本项目模块与本文件同目录；打包后资源在 _MEIPASS
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from gui_core import app_root  # noqa: E402 - 必须先补 sys.path

ROOT = app_root()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _fatal(message: str) -> int:
    """没有控制台也要让用户看到原因（--windowed 打包出的 exe 里 print 会消失）。"""
    print(message, file=sys.stderr)
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, message, "Mirror_export", 0x10)
        except Exception:
            pass
    return 1


def main() -> int:
    try:
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QGuiApplication
        from PyQt6.QtWidgets import QApplication
        from qfluentwidgets import FluentWindow  # noqa: F401 - 依赖自检
    except Exception as exc:
        # 缺依赖时给出可操作的提示，而不是「窗口一闪而退、什么都没有」
        return _fatal(
            "缺少界面依赖（PyQt6 / PyQt6-Fluent-Widgets）：\n"
            f"{exc}\n\n"
            "源码运行请先安装：pip install PyQt6 PyQt6-Fluent-Widgets"
        )

    # 高倍率显示统一取整到整倍缩放，**必须在构造 QApplication 之前**设。
    #
    # Windows 缩放不是整倍时（笔记本常见的 125% / 150%），Qt 默认按 PassThrough
    # 渲染：界面等比放大 25% / 50%，940×860 的设计尺寸在 1080p 上几乎顶满屏，
    # 而且字体渲染在半倍率下会发虚。RoundPreferFloor 把 125%/150% 归到 100%、
    # 175% 归到 200%，于是：
    #   - 125% / 150% 的 1080p 屏：本应用按 100% 渲染，窗口恢复设计大小；
    #   - 200% 的 4K 屏：本来就是整倍，不受影响（仍按 200%，清晰如常）。
    # 代价：125%/150% 屏上本应用会比系统其它应用小一号；且启动期策略运行中改不了，
    # 换显示器缩放设置后要重启才生效。用户已确认不做可切换开关（见计划 §commit 9）。
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.RoundPreferFloor
    )

    from ui.icons import app_icon
    from ui.window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("Mirror_export")
    app.setWindowIcon(app_icon())

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
