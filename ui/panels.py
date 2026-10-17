"""导出页：只负责布局与控件暴露，业务逻辑全部在 ui.window 的控制器里。

页面结构（单页表单，信息架构与 Tk 版一一对应）：
    滚动区[ 标题行 / 组合 / 导出参数 / 数量上限 / 选项 ] + 固定底部[ 操作栏 / 日志 / 状态栏 ]

排版取向：**紧凑**。每个分组用无标题卡片（`SectionCard`）而不是带 48px 标题栏的
`HeaderCardWidget`，分组名走表单左侧的标签列；间距也压到最小。这样即使 800px 高的
窗口也能一屏看到全部导出参数，不必靠滚轮翻。滚动区仍然保留 —— 小窗口 / 高 DPI 放大时
它才是兜底，而不是主要浏览方式。
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CheckBox,
    ComboBox,
    FluentIcon,
    LineEdit,
    PushButton,
    SingleDirectionScrollArea,
    SpinBox,
    SubtitleLabel,
    TransparentToolButton,
)

from .theme import CARD_SPACING, FIELD_SPACING, PAGE_MARGIN
from .widgets import ActionBar, FormRow, LogPanel, SectionCard


class ExportPage(QWidget):
    """外网导出工具的主页面。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.vBoxLayout = QVBoxLayout(self)
        self.vBoxLayout.setContentsMargins(*PAGE_MARGIN)
        self.vBoxLayout.setSpacing(CARD_SPACING)

        # 表单区：空间足够时全部可见，不够时才滚动（而不是把卡片压扁）
        self.scroll = SingleDirectionScrollArea(orient=Qt.Orientation.Vertical)
        self.scroll.setWidgetResizable(True)
        body = QWidget()
        self.formLayout = QVBoxLayout(body)
        self.formLayout.setContentsMargins(0, 0, 8, 0)  # 右侧给滚动条留空
        self.formLayout.setSpacing(CARD_SPACING)
        self.scroll.setWidget(body)
        # 透明必须**在 setWidget 之后**做：qfluentwidgets 的实现只会给「当前已有」的内容
        # 部件上透明样式。先调后 setWidget 的话内容体与视口都留着浅色底，
        # 切到深色主题时滚动区里会是一大块白底 + 白字（实测就是这个 bug）。
        self.scroll.enableTransparentBackground()
        self.scroll.viewport().setAutoFillBackground(False)
        self.vBoxLayout.addWidget(self.scroll, 1)

        self._build_header(body)
        self._build_profile_card(body)
        self._build_params_card(body)
        self._build_limits_card(body)
        self._build_options_card(body)
        self.formLayout.addStretch(1)

        # 底部固定：操作栏（按钮 + 进度四段）/ 日志 / 状态栏
        self.action = ActionBar(self)
        self.run_btn = self.action.run_btn
        self.open_btn = self.action.open_btn
        self.progress = self.action.progress
        self.vBoxLayout.addWidget(self.action)

        self._build_log()

        self.status_label = BodyLabel(
            "就绪 · 导出完成后拷入内网，双击 index.html", self
        )
        self.vBoxLayout.addWidget(self.status_label)

    # ---------- 各区块 ----------
    def _build_header(self, parent) -> None:
        """标题行：应用名 + 一句说明 + 主题切换，只占一行。"""
        row = QWidget(parent)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(FIELD_SPACING)

        layout.addWidget(SubtitleLabel("Mirror_export", row))
        layout.addWidget(
            CaptionLabel("在外网导出自包含静态站，拷入内网双击 index.html 浏览", row)
        )
        layout.addStretch(1)

        # 主题切换（跟随系统 / 浅色 / 深色），当前状态见 tooltip
        self.theme_btn = TransparentToolButton(FluentIcon.CONSTRACT, row)
        self.theme_btn.setFixedSize(30, 30)
        layout.addWidget(self.theme_btn)

        self.formLayout.addWidget(row)

    def _build_profile_card(self, parent) -> None:
        card = SectionCard(parent)

        self.profile_combo = ComboBox(card)
        self.profile_combo.setMinimumWidth(300)
        self.profile_combo.setPlaceholderText("（还没有组合）")

        # 导出期间要禁用这四个按钮：否则用户中途「保存」会被工作线程的旧快照覆盖
        self.profile_btns = [
            PushButton("保存", card),
            PushButton("另存为…", card),
            PushButton("重命名", card),
            PushButton("删除", card),
        ]

        row = FormRow("组合", card)
        row.add(self.profile_combo, 1)
        for button in self.profile_btns:
            row.add(button)
        card.bodyLayout.addWidget(row)
        card.bodyLayout.addWidget(
            CaptionLabel(
                "选一个组合即可带出仓库 / 输出目录 / Token / 数量上限与选项；改完点「保存」写回该组合",
                card,
            )
        )

        self.formLayout.addWidget(card)

    def _build_params_card(self, parent) -> None:
        card = SectionCard(parent)

        self.repo_edit = LineEdit(card)
        self.repo_edit.setPlaceholderText("owner/repo")
        repo_row = FormRow("仓库", card)
        repo_row.add(self.repo_edit, 1)
        card.bodyLayout.addWidget(repo_row)

        out_row = FormRow("输出目录", card)
        self.out_edit = LineEdit(card)
        self.browse_btn = PushButton("浏览…", card)
        out_row.add(self.out_edit, 1)
        out_row.add(self.browse_btn)
        card.bodyLayout.addWidget(out_row)

        token_row = FormRow("Token", card)
        self.token_edit = LineEdit(card)
        self.token_edit.setPlaceholderText("ghp_…（只在本机使用，不写入导出包）")
        self.clear_btn = PushButton("清除缓存", card)
        token_row.add(self.token_edit, 1)
        token_row.add(self.clear_btn)
        card.bodyLayout.addWidget(token_row)

        extra = FormRow("", card)
        self.show_token_check = CheckBox("显示明文", card)
        self.remember_check = CheckBox("记住到本机缓存", card)
        self.remember_check.setChecked(True)
        extra.add(self.show_token_check)
        extra.add(self.remember_check)
        card.bodyLayout.addWidget(extra)

        self.formLayout.addWidget(card)

    def _build_limits_card(self, parent) -> None:
        card = SectionCard(parent)

        self.max_prs = self._spin(card, 80)
        self.max_issues = self._spin(card, 80)
        self.max_commits = self._spin(card, 100)
        self.max_tags = self._spin(card, 50)

        row = FormRow("数量上限", card)
        row.contentLayout.setSpacing(18)
        row.add(self._labeled(card, "PR", self.max_prs))
        row.add(self._labeled(card, "Issue", self.max_issues))
        row.add(self._labeled(card, "每轮 Commit", self.max_commits))
        row.add(self._labeled(card, "Tag", self.max_tags))
        card.bodyLayout.addWidget(row)
        card.bodyLayout.addWidget(
            CaptionLabel(
                "Commit/diff 缓存无限累积；每轮从 HEAD 往回拉这么多条，旧的不删", card
            )
        )

        self.formLayout.addWidget(card)

    def _build_options_card(self, parent) -> None:
        card = SectionCard(parent)

        self.skip_details_check = CheckBox("跳过 diff / 评论", card)
        self.include_contents_check = CheckBox("缓存源码", card)
        self.include_contents_check.setChecked(True)
        self.force_check = CheckBox("覆盖输出目录", card)
        self.force_check.setChecked(True)
        self.incremental_check = CheckBox("增量导出", card)
        self.incremental_check.setChecked(True)

        row = FormRow("选项", card)
        row.contentLayout.setSpacing(18)
        for widget in (
            self.skip_details_check,
            self.include_contents_check,
            self.force_check,
            self.incremental_check,
        ):
            row.add(widget)
        card.bodyLayout.addWidget(row)

        self.formLayout.addWidget(card)

    def _build_log(self) -> None:
        card = SectionCard(self)
        self.log = LogPanel(card)
        self.log.text.setMinimumHeight(130)
        self.log.text.setMaximumHeight(300)
        card.bodyLayout.addWidget(self.log)
        self.log_card = card
        self.vBoxLayout.addWidget(card)

    # ---------- 小工具 ----------
    @staticmethod
    def _spin(parent, value: int) -> SpinBox:
        spin = SpinBox(parent)
        spin.setRange(1, 10000)
        spin.setValue(value)
        spin.setFixedWidth(116)  # 再窄就会把数字挤掉（实测 104 时显示成 "8("）
        return spin

    @staticmethod
    def _labeled(parent, label: str, widget) -> QWidget:
        """「说明 + 控件」横排成一组（比上下两行省一个行高）。"""
        box = QWidget(parent)
        layout = QHBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(CaptionLabel(label, box))
        layout.addWidget(widget)
        return box
