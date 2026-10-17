"""可复用的小组件：表单行、进度四段、操作栏、日志区、单行输入对话框。"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QTextCursor
from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    LineEdit,
    MessageBoxBase,
    PlainTextEdit,
    PrimaryPushButton,
    ProgressBar,
    PushButton,
    SubtitleLabel,
)

from .theme import (
    BAR_HEIGHT,
    BAR_WIDTH,
    COUNT_WIDTH,
    FIELD_SPACING,
    GAP,
    LABEL_WIDTH,
    LOG_MAX_LINES,
    PCT_WIDTH,
    STAGES_WIDTH,
    log_font,
)


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
    """日志区：等宽、只读、自动滚到底、超过上限从头裁掉（配合导出长跑）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.vBoxLayout = QVBoxLayout(self)
        self.vBoxLayout.setContentsMargins(0, 0, 0, 0)

        self.text = PlainTextEdit(self)
        self.text.setReadOnly(True)
        self.text.setFont(log_font())
        self.text.setPlaceholderText("导出日志会显示在这里")
        self.vBoxLayout.addWidget(self.text)

    def append_line(self, line: str) -> None:
        self.text.appendPlainText(line)
        self._trim()
        scrollbar = self.text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def clear_log(self) -> None:
        self.text.clear()

    def line_count(self) -> int:
        return int(self.text.document().blockCount())

    def _trim(self) -> None:
        """行数超限时从头部删掉 1000 行，避免长跑导出把内存和重绘拖垮。"""
        doc = self.text.document()
        if doc.blockCount() <= LOG_MAX_LINES:
            return
        cursor = QTextCursor(doc)
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        cursor.movePosition(
            QTextCursor.MoveOperation.NextBlock,
            QTextCursor.MoveMode.KeepAnchor,
            1000,
        )
        cursor.removeSelectedText()


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
