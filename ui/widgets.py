"""可复用的小组件：表单行、进度四段、操作栏、日志区、单行输入对话框。"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    Action,
    BodyLabel,
    CaptionLabel,
    FluentIcon,
    LineEdit,
    MessageBoxBase,
    PlainTextEdit,
    PrimaryPushButton,
    ProgressBar,
    PushButton,
    RoundMenu,
    SimpleCardWidget,
    SubtitleLabel,
    isDarkTheme,
)

from gui_core import classify_log_line

from .theme import (
    BAR_HEIGHT,
    BAR_WIDTH,
    CARD_PADDING,
    COUNT_WIDTH,
    FIELD_SPACING,
    GAP,
    LABEL_WIDTH,
    LOG_MAX_LINES,
    PCT_WIDTH,
    ROW_SPACING,
    STAGES_WIDTH,
    log_color,
    log_font,
)


class SectionCard(SimpleCardWidget):
    """无标题卡片：省掉 `HeaderCardWidget` 那 48px 标题栏。

    分组靠卡片自身的圆角底色区分，标题空间让给内容 —— 窗口不高时首屏能直接
    看到全部导出参数，而不是大半藏在滚动区里。内容加到 `bodyLayout`。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setBorderRadius(6)
        self.bodyLayout = QVBoxLayout(self)
        self.bodyLayout.setContentsMargins(*CARD_PADDING)
        self.bodyLayout.setSpacing(ROW_SPACING)


class FormRow(QWidget):
    """一行表单：左侧定宽标签（右对齐）+ 右侧内容区。"""

    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        self.hBoxLayout = QHBoxLayout(self)
        self.hBoxLayout.setContentsMargins(0, 0, 0, 0)
        self.hBoxLayout.setSpacing(FIELD_SPACING)

        self.label = CaptionLabel(label, self)
        self.label.setFixedWidth(LABEL_WIDTH)
        self.label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.hBoxLayout.addWidget(self.label)

        self.contentLayout = QHBoxLayout()
        self.contentLayout.setContentsMargins(0, 0, 0, 0)
        self.contentLayout.setSpacing(FIELD_SPACING)
        self.hBoxLayout.addLayout(self.contentLayout, 1)

    def add(self, widget: QWidget, stretch: int = 0) -> "FormRow":
        """把控件加到本行右侧内容区（返回自身，便于链式写法）。"""
        self.contentLayout.addWidget(widget, stretch)
        return self


class ProgressPanel(QWidget):
    """进度四段：进度条 | 百分比 | 已完成阶段/总阶段 | 阶段内计数 | 说明文本。

    硬约束（Tk 版踩过）：进度条与三个计数列都**定宽**，只有最后的说明列吃
    stretch。否则说明文本一长一短就把进度条推来推去，导出时整条栏在跳。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.hBoxLayout = QHBoxLayout(self)
        self.hBoxLayout.setContentsMargins(0, 0, 0, 0)
        self.hBoxLayout.setSpacing(GAP)

        self.bar = ProgressBar(self)
        self.bar.setFixedWidth(BAR_WIDTH)
        # Fluent 的进度条默认只有 4px 高，200px 宽的横条太细，给到 8px 更好认
        self.bar.setFixedHeight(BAR_HEIGHT)
        self.bar.setRange(0, 100)
        self.bar.setValue(0)

        self.pct = BodyLabel("0%", self)
        self.pct.setFixedWidth(PCT_WIDTH)
        self.stages = BodyLabel("0/0", self)
        self.stages.setFixedWidth(STAGES_WIDTH)
        self.count = BodyLabel("0/0", self)
        self.count.setFixedWidth(COUNT_WIDTH)
        self.detail = BodyLabel("", self)

        for widget in (self.bar, self.pct, self.stages, self.count, self.detail):
            self.hBoxLayout.addWidget(widget)
        self.hBoxLayout.setStretchFactor(self.detail, 1)

    def apply_info(self, info: dict) -> None:
        """贴上 gui_core.parse_progress 的解析结果（四段口径唯一来源）。"""
        self.bar.setValue(int(info.get("overall") or 0))
        self.pct.setText(info.get("pct_text") or "0%")
        self.stages.setText(info.get("stages_text") or "0/0")
        self.count.setText(info.get("count_text") or "0/0")
        self.detail.setText(info.get("detail_text") or "")

    def reset(self) -> None:
        self.bar.setValue(0)
        self.pct.setText("0%")
        self.stages.setText("0/0")
        self.count.setText("0/0")
        self.detail.setText("")

    def set_starting(self) -> None:
        self.reset()
        self.detail.setText("启动…")

    def set_done(self) -> None:
        self.bar.setValue(100)
        self.pct.setText("100%")
        self.detail.setText("导出完成")

    def set_failed(self) -> None:
        # 失败时**故意不清零**：进度停在出错处，便于定位卡在哪一步
        self.detail.setText("失败（进度停在出错处）")


class ActionBar(QWidget):
    """操作栏：开始导出 | 打开输出目录 | 进度四段（顺序与 Tk 版一致）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.hBoxLayout = QHBoxLayout(self)
        self.hBoxLayout.setContentsMargins(0, 0, 0, 0)
        self.hBoxLayout.setSpacing(GAP)

        self.run_btn = PrimaryPushButton("开始导出", self)
        self.open_btn = PushButton("打开输出目录", self)
        self.open_btn.setEnabled(False)
        self.progress = ProgressPanel(self)

        self.hBoxLayout.addWidget(self.run_btn)
        self.hBoxLayout.addWidget(self.open_btn)
        self.hBoxLayout.addWidget(self.progress, 1)


class LogPanel(QWidget):
    """日志区：等宽、只读、自动滚到底、按语义着色、右键菜单、超过上限从头裁掉。

    着色只影响观感：级别由 `gui_core.classify_log_line` 判定，取不到颜色就跟随默认前景色。
    主题切换后调用 `refresh_colors()` 重新上色（已写进文档的日志不会自己变色）。

    右键菜单（复制全部 / 另存为文件 / 清空日志）只在这里弹，具体动作由控制器接线 ——
    面板不碰文件系统，方便单测。
    """

    copyRequested = pyqtSignal()
    saveRequested = pyqtSignal()
    clearRequested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.vBoxLayout = QVBoxLayout(self)
        self.vBoxLayout.setContentsMargins(0, 0, 0, 0)

        self.text = PlainTextEdit(self)
        self.text.setReadOnly(True)
        self.text.setFont(log_font())
        self.text.setPlaceholderText("导出日志会显示在这里（右键可复制 / 另存为 / 清空）")
        self.text.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.text.customContextMenuRequested.connect(self._show_menu)
        self.vBoxLayout.addWidget(self.text)

        # (起始位置, 结束位置, 级别)，主题切换时按这个表重刷颜色
        self._spans: list[tuple[int, int, str]] = []

    def _show_menu(self, pos) -> None:
        menu = RoundMenu(parent=self.text)

        copy_action = Action(FluentIcon.COPY, "复制全部", menu)
        copy_action.triggered.connect(self.copyRequested.emit)
        menu.addAction(copy_action)

        save_action = Action(FluentIcon.SAVE, "另存为文件…", menu)
        save_action.triggered.connect(self.saveRequested.emit)
        menu.addAction(save_action)

        menu.addSeparator()

        clear_action = Action(FluentIcon.DELETE, "清空日志", menu)
        clear_action.triggered.connect(self.clearRequested.emit)
        menu.addAction(clear_action)

        menu.exec(self.text.mapToGlobal(pos))

    def to_plain_text(self) -> str:
        return self.text.toPlainText()

    def append_line(self, line: str) -> None:
        level = classify_log_line(line)
        cursor = QTextCursor(self.text.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        # 必须先清掉字符格式：新插入的文本会**继承前一个字符的格式**，
        # 否则上一行的错误红会一路串到后面所有行（实测过）。
        cursor.setCharFormat(QTextCharFormat())
        start = cursor.position()
        cursor.insertText(line + "\n")
        end = cursor.position()
        self._spans.append((start, end, level))
        self._apply_span_color(start, end, level)
        self._trim()
        scrollbar = self.text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def clear_log(self) -> None:
        self.text.clear()
        self._spans.clear()

    def line_count(self) -> int:
        return int(self.text.document().blockCount())

    def refresh_colors(self) -> None:
        """主题切换后重刷所有已写日志的颜色。"""
        for start, end, level in self._spans:
            self._apply_span_color(start, end, level)

    def _apply_span_color(self, start: int, end: int, level: str) -> None:
        """给 [start, end) 这一段上色。

        每一行都显式设色（含 info）：QTextDocument 里新插入的文本会继承前一个字符的
        字符格式，若普通行不上色，错误红会串到后面所有行；若用空格式重置又会变成纯黑，
        深色主题下看不见。所以颜色表里连 info 一起定义，插入与重刷都走这里。
        """
        color = log_color(level, isDarkTheme())
        if color is None:
            return
        cursor = QTextCursor(self.text.document())
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        fmt = QTextCharFormat()
        fmt.setForeground(color)
        cursor.mergeCharFormat(fmt)

    def _trim(self) -> None:
        """行数超限时从头部删掉 1000 行，避免长跑导出把内存和重绘拖垮。"""
        doc = self.text.document()
        if doc.blockCount() <= LOG_MAX_LINES:
            return
        # 记录被删掉的行，免得 _spans 越积越多、也免得重刷颜色时位置错位
        removed_lines = 1000
        cursor = QTextCursor(doc)
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        cursor.movePosition(
            QTextCursor.MoveOperation.NextBlock,
            QTextCursor.MoveMode.KeepAnchor,
            removed_lines,
        )
        cut_pos = cursor.selectionEnd()
        cursor.removeSelectedText()

        kept = []
        for start, end, level in self._spans:
            if end <= cut_pos:
                continue  # 整行已被裁掉
            kept.append((max(start - cut_pos, 0), end - cut_pos, level))
        self._spans = kept


class TextInputDialog(MessageBoxBase):
    """带单行输入的 Fluent 对话框（「另存为…」「重命名」用）。"""

    def __init__(
        self,
        title: str,
        label: str = "",
        text: str = "",
        placeholder: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self.titleLabel = SubtitleLabel(title, self)
        self.hintLabel = CaptionLabel(label, self) if label else None

        self.lineEdit = LineEdit(self)
        self.lineEdit.setPlaceholderText(placeholder)
        self.lineEdit.setText(text)
        self.lineEdit.setClearButtonEnabled(True)

        self.viewLayout.addWidget(self.titleLabel)
        if self.hintLabel is not None:
            self.viewLayout.addWidget(self.hintLabel)
        self.viewLayout.addWidget(self.lineEdit)

        self.widget.setMinimumWidth(380)
        self.yesButton.setText("确定")
        self.cancelButton.setText("取消")
        self.lineEdit.setFocus()

    def value(self) -> str:
        return self.lineEdit.text().strip()
