"""主题：默认跟随系统深浅色，可手动切换；选择记在 QSettings。

为什么不用项目目录里的配置文件：exe 可能被拷到任意位置（含只读 U 盘），
主题偏好属于「这台机器」的界面偏好，写进 QSettings（Windows 即注册表
HKCU\\Software\\Mirror_export）不会在程序目录里制造额外文件，也不去动
`gh_token_cache.json`（那是导出参数的组合缓存，格式不动）。
"""

from __future__ import annotations

from PyQt6.QtCore import QSettings
from qfluentwidgets import Theme, setTheme, setThemeColor

from .theme import BRAND_COLOR

ORDER = ("auto", "light", "dark")
LABELS = {"auto": "跟随系统", "light": "浅色", "dark": "深色"}
_THEMES = {"auto": Theme.AUTO, "light": Theme.LIGHT, "dark": Theme.DARK}


def _settings() -> QSettings:
    return QSettings("Mirror_export", "GUI")


def load_theme() -> str:
    """读回上次选择；认不出的值一律退回「跟随系统」。"""
    try:
        value = str(_settings().value("theme", "auto") or "auto").lower()
    except Exception:
        return "auto"
    return value if value in ORDER else "auto"


def save_theme(name: str) -> None:
    try:
        _settings().setValue("theme", name if name in ORDER else "auto")
    except Exception:
        pass  # 偏好写不进去（受限注册表）不该影响程序可用性


def apply_theme(name: str) -> None:
    setThemeColor(BRAND_COLOR)
    setTheme(_THEMES.get(name, Theme.AUTO))


def next_theme(name: str) -> str:
    if name not in ORDER:
        return "auto"
    return ORDER[(ORDER.index(name) + 1) % len(ORDER)]


def theme_label(name: str) -> str:
    return LABELS.get(name, LABELS["auto"])
