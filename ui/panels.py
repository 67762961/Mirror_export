"""导出页：只负责布局与控件暴露，业务逻辑全部在 ui.window 的控制器里。

页面结构（单页表单，信息架构与 Tk 版一一对应）：
    直排[ 标题行 / 组合 / 导出参数 / 数量上限 / 选项 / 操作栏 / 日志 / 状态栏 ]

排版取向：**紧凑、不滚动**。每个分组用无标题卡片（`SectionCard`）而不是带 48px 标题栏
的 `HeaderCardWidget`，分组名走表单左侧的标签列；间距也压到最小。

为什么不套滚动区：卡片按自然高度从上往下排，页面布局开 `SetMinimumSize` 之后，窗口的
最小高度由内容自己推导 —— 于是「把窗口拖小到卡片被压扁」在物理上就不可能发生（拖到
最小尺寸就拖不动了）。反过来，只要有滚动区兜底，窗口就能被拖到远小于内容的尺寸，卡片
被压成一条缝，用户还得靠滚轮才发现下面还有东西。
代价是最小高度变高（实测 620 → 735）：极小屏靠 `setCompact` 收紧到 637。
"""

from __future__ import annotations

from PyQt6.QtCore import QSize
from PyQt6.QtWidgets import QHBoxLayout, QLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    ComboBox,
    FluentIcon,
    LineEdit,
    PushButton,
    SubtitleLabel,
    TransparentToolButton,
)

from .theme import (
    BTN_MIN_WIDTH,
    CARD_PADDING,
    CARD_SPACING,
    COMPACT_CARD_PADDING,
    COMPACT_CARD_SPACING,
    COMPACT_LOG_MIN_HEIGHT,
    FIELD_MIN_WIDTH,
    FIELD_SPACING,
    LOG_MAX_HEIGHT,
    LOG_MIN_HEIGHT,
    PAGE_MARGIN,
    SPIN_WIDTH,
)
from .widgets import (
    ActionBar,
    FocusCheckBox,
    FormRow,
    LogPanel,
    NoWheelSpinBox,
    SectionCard,
)


def _relayout_bottom_up(root: QWidget) -> None:
    """自底向上重算 `root` 子树里每个布局的尺寸缓存。

    Qt 的最小尺寸是自下而上传播的，而 `invalidate()` 只是标脏、真正的重算要等
    下一次事件循环里的 `activate()`。若只调最外层，父布局会拿子控件**旧的**
    `minimumSizeHint` 去算 —— 于是测出来的永远是改之前的值。这里按树深倒序逐层
    `invalidate()` + `activate()`，保证轮到父层时子层已经是新值。
    """

    def depth(widget: QWidget) -> int:
        n = 0
        parent = widget.parentWidget()
        while parent is not None:
            n += 1
            parent = parent.parentWidget()
        return n

    widgets = sorted((root, *root.findChildren(QWidget)), key=depth, reverse=True)
    for widget in widgets:
        layout = widget.layout()
        if layout is not None:
            layout.invalidate()
            layout.activate()
        widget.updateGeometry()


class ExportPage(QWidget):
    """外网导出工具的主页面。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.vBoxLayout = QVBoxLayout(self)
        self.vBoxLayout.setContentsMargins(*PAGE_MARGIN)
        self.vBoxLayout.setSpacing(CARD_SPACING)
        # 最小尺寸 = 内容最小尺寸：它会一路传到 MainWindow.minimumSizeHint()，
        # 窗口因此拖不到「压扁卡片」的尺寸。**这是整个布局的不变量**，删掉就退回
        # 「能拖多小拖多小、卡片挤成一条缝」。
        self.vBoxLayout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)

        # 表单区直接排进页面布局：不再套滚动区，右侧也就不必给滚动条留 8px。
        for build in (
            self._build_header,
            self._build_profile_card,
            self._build_params_card,
            self._build_limits_card,
            self._build_options_card,
        ):
            build(self)

        # 底部：操作栏（按钮 + 进度四段）/ 日志 / 状态栏
        self.action = ActionBar(self)
        self.run_btn = self.action.run_btn
        self.open_btn = self.action.open_btn
        self.progress = self.action.progress
        self.vBoxLayout.addWidget(self.action)

        self._build_log()

        self.status_label = BodyLabel(self.IDLE_TEXT, self)
        self.vBoxLayout.addWidget(self.status_label)

        self._setup_tab_order()

    # 空闲状态文案：没有组合时提示「先建组合」，有组合时提示「导出后拷进内网」。
    # 两段都保持一句话，避免状态栏换行把下面的日志区挤掉。
    IDLE_TEXT = "就绪 · 导出完成后拷入内网，双击 index.html"
    IDLE_NO_PROFILE_TEXT = "就绪 · 还没有组合，填好参数后点「另存为…」保存一套"

    def _setup_tab_order(self) -> None:
        """Tab 顺序按「从上到下、从左到右」的视觉顺序走。

        Qt 默认顺序是**控件创建顺序**，而本页面为排版方便先建表单再建底部操作栏，
        导致 Tab 会先跳到日志框再跳回按钮。这里显式排一遍。
        """
        order = [
            self.profile_combo,
            *self.profile_btns,
            self.repo_edit,
            self.out_edit,
            self.browse_btn,
            self.token_edit,
            self.show_token_check,
            self.remember_check,
            self.clear_btn,
            self.max_prs,
            self.max_issues,
            self.max_commits,
            self.max_tags,
            self.skip_details_check,
            self.include_contents_check,
            self.force_check,
            self.incremental_check,
            self.run_btn,
            self.open_btn,
            self.log.text,
        ]
        for first, second in zip(order, order[1:]):
            QWidget.setTabOrder(first, second)
        # 收尾：最后一个（日志框）回到第一个，形成闭环。不显式设的话 Qt 会按创建顺序
        # 接到别的控件上（实测会跳到页面容器 QWidget），Tab 走一圈后焦点就丢了。
        QWidget.setTabOrder(order[-1], order[0])

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

        self.vBoxLayout.addWidget(row)

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
        for button in self.profile_btns:
            button.setMinimumWidth(BTN_MIN_WIDTH)

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

        self.vBoxLayout.addWidget(card)

    def _build_params_card(self, parent) -> None:
        card = SectionCard(parent)

        self.repo_edit = LineEdit(card)
        self.repo_edit.setPlaceholderText("owner/repo")
        self.repo_edit.setMinimumWidth(FIELD_MIN_WIDTH)
        repo_row = FormRow("仓库", card)
        repo_row.add(self.repo_edit, 1)
        card.bodyLayout.addWidget(repo_row)

        out_row = FormRow("输出目录", card)
        self.out_edit = LineEdit(card)
        self.out_edit.setMinimumWidth(FIELD_MIN_WIDTH)
        self.browse_btn = PushButton("浏览…", card)
        self.browse_btn.setMinimumWidth(BTN_MIN_WIDTH)
        out_row.add(self.out_edit, 1)
        out_row.add(self.browse_btn)
        card.bodyLayout.addWidget(out_row)

        token_row = FormRow("Token", card)
        self.token_edit = LineEdit(card)
        self.token_edit.setPlaceholderText("ghp_…（只在本机使用，不写入导出包）")
        self.token_edit.setMinimumWidth(FIELD_MIN_WIDTH)
        self.clear_btn = PushButton("清除缓存", card)
        self.clear_btn.setMinimumWidth(BTN_MIN_WIDTH)
        token_row.add(self.token_edit, 1)
        token_row.add(self.clear_btn)
        card.bodyLayout.addWidget(token_row)

        extra = FormRow("", card)
        self.show_token_check = FocusCheckBox("显示明文", card)
        self.remember_check = FocusCheckBox("记住到本机缓存", card)
        self.remember_check.setChecked(True)
        extra.add(self.show_token_check)
        extra.add(self.remember_check)
        card.bodyLayout.addWidget(extra)

        self.vBoxLayout.addWidget(card)

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

        self.vBoxLayout.addWidget(card)

    def _build_options_card(self, parent) -> None:
        card = SectionCard(parent)

        self.skip_details_check = FocusCheckBox("跳过 diff / 评论", card)
        self.include_contents_check = FocusCheckBox("缓存源码", card)
        self.include_contents_check.setChecked(True)
        self.force_check = FocusCheckBox("覆盖输出目录", card)
        self.force_check.setChecked(True)
        self.incremental_check = FocusCheckBox("增量导出", card)
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

        self.vBoxLayout.addWidget(card)

    def _build_log(self) -> None:
        card = SectionCard(self)
        self.log = LogPanel(card)
        self.log.text.setMinimumHeight(LOG_MIN_HEIGHT)
        self.log.text.setMaximumHeight(LOG_MAX_HEIGHT)
        card.bodyLayout.addWidget(self.log)
        self.log_card = card
        # 给 stretch=1：窗口拉高时余量优先给日志（日志是唯一「越大越有用」的区域），
        # 但卡片自身也封顶 —— 否则最大化到 4K 时会变成一张大半空白的卡片。
        # 超过上限的余量落到页面底部，视觉上就是状态栏上方多一条留白。
        self._log_card_extra = CARD_PADDING[1] + CARD_PADDING[3]
        card.setMaximumHeight(LOG_MAX_HEIGHT + self._log_card_extra)
        self.vBoxLayout.addWidget(card, 1)

    # ---------- 最小尺寸 ----------
    def contentMinimumSize(self) -> QSize:
        """返回「内容真正的最小尺寸」，供 `ui.window` 推导窗口最小尺寸。

        为什么要这一层：Qt 的布局尺寸是**延迟**重算的 —— 改完内边距 / 行距后，父布局
        拿到的仍是子控件上一轮的 `minimumSizeHint`。只 `invalidate()` 页面布局得到的
        还是旧值（实测切紧凑档高度纹丝不动、切回来也回不去）。可靠的做法是自己从叶子
        往上 `invalidate()` + `activate()` 一遍，让每层都用新的子尺寸重算。
        """
        _relayout_bottom_up(self)
        return self.minimumSizeHint()

    def setCompact(self, compact: bool) -> None:
        """切紧凑密度：极小屏（逻辑高度装不下最小尺寸）上省出几十像素。

        只收紧内边距与行距，**不动字号与控件定宽** —— 那两样一改就会换行、反而
        更高，而且输入框里的长路径会被重新截断。
        """
        self.vBoxLayout.setSpacing(COMPACT_CARD_SPACING if compact else CARD_SPACING)
        self.log.text.setMinimumHeight(
            COMPACT_LOG_MIN_HEIGHT if compact else LOG_MIN_HEIGHT
        )
        for card in self.findChildren(SectionCard):
            card.setCompact(compact)
        # 日志卡的封顶也要跟着内边距走，否则紧凑档下会白白多留一条空白
        self._log_card_extra = (
            COMPACT_CARD_PADDING[1] + COMPACT_CARD_PADDING[3]
            if compact
            else CARD_PADDING[1] + CARD_PADDING[3]
        )
        self.log_card.setMaximumHeight(LOG_MAX_HEIGHT + self._log_card_extra)

    # ---------- 小工具 ----------
    @staticmethod
    def _spin(parent, value: int) -> NoWheelSpinBox:
        # 用 NoWheelSpinBox 而不是 SpinBox：悬停滚滚轮不改数值（避免误触），键盘仍可调
        spin = NoWheelSpinBox(parent)
        spin.setRange(1, 10000)
        spin.setValue(value)
        spin.setFixedWidth(SPIN_WIDTH)
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
