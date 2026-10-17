"""图标：沿用项目既有的 assets/icon.ico（打包后从 _MEIPASS 取）。"""

from __future__ import annotations

from PyQt6.QtGui import QIcon

from gui_core import app_root


def app_icon() -> QIcon:
    """窗口 / 任务栏 / exe 图标。

    优先 ico（含多尺寸，任务栏更清晰），缺失时退回 256 PNG；都没有则空图标。
    """
    for name in ("icon.ico", "icon-256.png"):
        path = app_root() / "assets" / name
        if path.exists():
            return QIcon(str(path))
    return QIcon()
