"""导出页：只负责布局与控件暴露，业务逻辑全部在 ui.window 的控制器里。

页面结构（单页表单，信息架构与 Tk 版一一对应）：
    滚动区[ 标题 / 组合 / 导出参数 / 数量上限 / 选项 ] + 固定底部[ 操作栏 / 日志 / 状态栏 ]

表单放在纵向滚动区里：窗口矮或系统缩放放大时，卡片不会被压扁成一条（实测 800px
高的窗口装不下约 1250px 的表单内容，不滚动的话控件会被挤到 0 高度直接看不见）。
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
    HeaderCardWidget,
    LineEdit,
    PushButton,
    SingleDirectionScrollArea,
    SpinBox,
    SubtitleLabel,
    TransparentToolButton,
)

from .theme import CARD_SPACING, PAGE_MARGIN, ROW_SPACING
from .widgets import ActionBar, FormRow, LogPanel


class ExportPage(QWidget):
    """外网导出工具的主页面。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.vBoxLayout = QVBoxLayout(self)
        self.vBoxLayout.setContentsMargins(*PAGE_MARGIN)
        self.vBoxLayout.setSpacing(CARD_SPACING)

        # 表单区：纵向滚动，空间不够时滚动而不是压缩
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
        head = QWidget(parent)
        layout = QVBoxLayout(head)
        layout.setContentsMargins(0, 0, 0, 2)
        layout.setSpacing(4)
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.addWidget(SubtitleLabel("Mirror_export", head))
        title_row.addStretch(1)
        # 主题切换（跟随系统 / 浅色 / 深色），当前状态见 tooltip
        self.theme_btn = TransparentToolButton(FluentIcon.CONSTRACT, head)
        self.theme_btn.setFixedSize(32, 32)
        title_row.addWidget(self.theme_btn)
        layout.addLayout(title_row)

        layout.addWidget(
            CaptionLabel("在外网导出自包含静态站，拷入内网后双击 index.html 浏览", head)
        )
        self.formLayout.addWidget(head)

    def _build_profile_card(self, parent) -> None:
        card = HeaderCardWidget("组合", parent)
        box = QVBoxLayout()
        box.setSpacing(ROW_SPACING)

        self.profile_combo = ComboBox(card)
        self.profile_combo.setMinimumWidth(320)
        self.profile_combo.setPlaceholderText("（还没有组合）")

        # 导出期间要禁用这四个按钮：否则用户中途「保存」会被工作线程的旧快照覆盖
        self.profile_btns = [
            PushButton("保存", card),
            PushButton("另存为…", card),
            PushButton("重命名", card),
            PushButton("删除", card),
        ]

        profile_row = FormRow("组合", card)
        profile_row.add(self.profile_combo, 1)
        for button in self.profile_btns:
            profile_row.add(button)
        box.addWidget(profile_row)

        box.addWidget(
            CaptionLabel(
                "选一个组合即可带出仓库 / 输出目录 / Token / 数量上限与选项；改完点「保存」写回该组合",
                card,
            )
        )

        card.viewLayout.addLayout(box)
        self.formLayout.addWidget(card)

    def _build_params_card(self, parent) -> None:
        card = HeaderCardWidget("导出参数", parent)
        box = QVBoxLayout()
        box.setSpacing(ROW_SPACING)

        self.repo_edit = LineEdit(card)
        self.repo_edit.setPlaceholderText("owner/repo")
        repo_row = FormRow("仓库", card)
        repo_row.add(self.repo_edit, 1)
        box.addWidget(repo_row)

        out_row = FormRow("输出目录", card)
        self.out_edit = LineEdit(card)
        self.browse_btn = PushButton("浏览…", card)
        out_row.add(self.out_edit, 1)
        out_row.add(self.browse_btn)
        box.addWidget(out_row)

        token_row = FormRow("Token", card)
        self.token_edit = LineEdit(card)
        self.token_edit.setPlaceholderText("ghp_…（只在本机使用，不写入导出包）")
        self.clear_btn = PushButton("清除缓存", card)
        token_row.add(self.token_edit, 1)
        token_row.add(self.clear_btn)
        box.addWidget(token_row)

        extra = FormRow("", card)
        self.show_token_check = CheckBox("显示明文", card)
        self.remember_check = CheckBox("记住到本机缓存", card)
        self.remember_check.setChecked(True)
        extra.add(self.show_token_check)
        extra.add(self.remember_check)
        box.addWidget(extra)

        card.viewLayout.addLayout(box)
        self.formLayout.addWidget(card)

    def _build_limits_card(self, parent) -> None:
        card = HeaderCardWidget("数量上限", parent)
        box = QVBoxLayout()
        box.setSpacing(ROW_SPACING)

        self.max_prs = self._spin(card, 80)
        self.max_issues = self._spin(card, 80)
        self.max_commits = self._spin(card, 100)
        self.max_tags = self._spin(card, 50)

        row = QHBoxLayout()
        row.setSpacing(24)
        row.addWidget(self._labeled(card, "PR", self.max_prs))
        row.addWidget(self._labeled(card, "Issue", self.max_issues))
        row.addWidget(self._labeled(card, "每轮 Commit", self.max_commits))
        row.addWidget(self._labeled(card, "Tag", self.max_tags))
        row.addStretch(1)
        box.addLayout(row)

        box.addWidget(
            CaptionLabel(
                "Commit/diff 缓存无限累积；每轮从 HEAD 往回拉这么多条，旧的不删", card
            )
        )

        card.viewLayout.addLayout(box)
        self.formLayout.addWidget(card)

    def _build_options_card(self, parent) -> None:
        card = HeaderCardWidget("选项", parent)
        box = QVBoxLayout()
        box.setSpacing(ROW_SPACING)

        self.skip_details_check = CheckBox("跳过 diff / 评论", card)
        self.include_contents_check = CheckBox("缓存源码", card)
        self.include_contents_check.setChecked(True)
        self.force_check = CheckBox("覆盖输出目录", card)
        self.force_check.setChecked(True)
        self.incremental_check = CheckBox("增量导出", card)
        self.incremental_check.setChecked(True)

        row = QHBoxLayout()
        row.setSpacing(22)
        for widget in (
            self.skip_details_check,
            self.include_contents_check,
            self.force_check,
            self.incremental_check,
        ):
            row.addWidget(widget)
        row.addStretch(1)
        box.addLayout(row)

        card.viewLayout.addLayout(box)
        self.formLayout.addWidget(card)

    def _build_log(self) -> None:
        card = HeaderCardWidget("日志", self)
        self.log = LogPanel(card)
        self.log.text.setMinimumHeight(140)
        self.log.text.setMaximumHeight(300)
        card.viewLayout.addWidget(self.log)
        self.log_card = card
        self.vBoxLayout.addWidget(card)

    # ---------- 小工具 ----------
    @staticmethod
    def _spin(parent, value: int) -> SpinBox:
        spin = SpinBox(parent)
        spin.setRange(1, 10000)
        spin.setValue(value)
        spin.setFixedWidth(110)
        return spin

    @staticmethod
    def _labeled(parent, label: str, widget) -> QWidget:
        box = QWidget(parent)
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(CaptionLabel(label, box))
        layout.addWidget(widget)
        return box
