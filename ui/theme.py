"""界面设计令牌：间距、字号、定宽、日志参数。

颜色不在这里堆：品牌色由 `ui.theme_manager` 通过 `setThemeColor` 设置，
深浅色由 qfluentwidgets 的主题系统统一驱动；这里只放布局数值与等宽字体族，
避免同一类数值散落在各个控件里（改一处要满文件找）。
"""

from __future__ import annotations

from PyQt6.QtGui import QFont

# 页面与卡片（整体偏紧凑：去掉卡片标题栏后，首屏要能直接看到全部参数）
PAGE_MARGIN = (24, 10, 24, 12)   # 内容区外边距（上边留给标题栏）
CARD_PADDING = (16, 12, 16, 12)  # 卡片内边距
CARD_SPACING = 10                # 卡片之间的垂直间距
ROW_SPACING = 8                  # 卡片内各行之间
LABEL_WIDTH = 72                 # 表单左侧标签列宽（各行对齐）
FIELD_SPACING = 8                # 同一行内控件之间

# 进度区（沿用 Tk 版教训：进度条与计数列必须定宽，否则说明文本会把它们挤走）
GAP = 10
BAR_WIDTH = 200
BAR_HEIGHT = 8                  # Fluent 默认 4px，200px 宽的横条太细
PCT_WIDTH = 52
STAGES_WIDTH = 64
COUNT_WIDTH = 96

# 日志区
LOG_MAX_LINES = 4000             # 超过这么多行就从头裁掉 1000 行（与 Tk 版一致）
LOG_VISIBLE_LINES = 12
LOG_FONT_FAMILIES = ("Cascadia Mono", "Consolas", "Courier New")
LOG_FONT_SIZE = 10

# 品牌色（用于 Fluent 的强调色）
BRAND_COLOR = "#0969da"


def log_font() -> QFont:
    """日志区等宽字体：首选项缺失时自动回落到系统等宽字体。"""
    font = QFont()
    try:
        font.setFamilies(list(LOG_FONT_FAMILIES))
    except AttributeError:  # 老 Qt 只有单字体族
        font.setFamily(LOG_FONT_FAMILIES[0])
    font.setPointSize(LOG_FONT_SIZE)
    font.setStyleHint(QFont.StyleHint.Monospace)
    return font
