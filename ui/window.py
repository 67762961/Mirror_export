"""主窗口与控制器。

窗口只做三件事：把 Fluent 控件摆好（ui.panels）、把组合/Token/进度这些状态
搬进搬出、以及用后台线程跑导出核心。导出核心的调用方式保持不变：
`export.export(args)` + stdout 的 `__PROGRESS__{json}` 机器行。

历史契约（换 UI 不改语义，改这里之前先读 ARCHITECTURE §4.2）：
- 组合缓存读写全部走 gui_core，写盘失败必须让用户看见；
- Token 真值存 `_token_plain`，输入框可能只是掩码；
- 导出期间锁定**全部**参数控件（工作线程结束时会把启动时的快照写回磁盘）；
- 工作线程异常必须兜住 `BaseException`（`export()` 参数校验失败抛 SystemExit），
  且 `__DONE__` / `__FAIL__` 必达，两条分支恢复同一组控件。
"""

from __future__ import annotations

import os
import queue
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import QApplication, QFileDialog
from qfluentwidgets import (
    InfoBar,
    InfoBarPosition,
    MessageBox,
    PushButton,
    qconfig,
)
from qfluentwidgets.window.fluent_window import FluentWindowBase

from gui_core import (
    DEFAULT_PROFILE,
    LogWriter,
    TOKEN_CACHE_FILE,
    active_profile,
    app_root,
    delete_profile,
    exe_dir,
    get_profile,
    list_profiles,
    mask_token,
    parse_progress,
    rename_profile,
    summarize_failure,
    upsert_profile,
)

from . import theme_manager
from .icons import app_icon
from .panels import ExportPage
from .widgets import TextInputDialog

ROOT = app_root()
EXE_DIR = exe_dir()

try:
    import export as export_mod
except Exception:
    export_mod = None


class MainWindow(FluentWindowBase):
    """单页 Fluent 窗口：左侧无导航栏，标题栏以下是导出表单。"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Mirror_export")
        self.resize(940, 860)
        self.setMinimumSize(880, 620)
        self.setWindowIcon(app_icon())

        self._profile_names: dict[str, str] = {}
        self._token_plain: str = ""
        self._selected_profile: str = ""
        self._loading_profile: bool = False
        self.q: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        # 本轮导出期间产生的人类可读日志（不含 __PROGRESS__ 机器行），
        # 用于失败时挑一句摘要放到状态栏，不必让用户翻整段 traceback
        self._run_log: list[str] = []

        # 内容从标题栏下方开始（高度取自 Fluent 标题栏自身的定高，不写死）
        self.hBoxLayout.setContentsMargins(0, self.titleBar.height(), 0, 0)
        self.page = ExportPage(self)
        self.stackedWidget.addWidget(self.page)
        self.stackedWidget.setCurrentWidget(self.page)
        self.hBoxLayout.addWidget(self.stackedWidget, 1)

        # 导出期间需要一并锁定的「导出参数」控件（新增可编辑控件必须登记到这里）
        self.param_widgets = [
            self.page.repo_edit,
            self.page.out_edit,
            self.page.browse_btn,
            self.page.token_edit,
            self.page.show_token_check,
            self.page.remember_check,
            self.page.max_prs,
            self.page.max_issues,
            self.page.max_commits,
            self.page.max_tags,
            self.page.skip_details_check,
            self.page.include_contents_check,
            self.page.force_check,
            self.page.incremental_check,
        ]

        self._wire_signals()
        self._append_log("Mirror_export")
        self._append_log("选组合（或直接填字段后「另存为…」）→ 开始导出 → 拷贝输出目录到内网")
        self._append_log("-" * 48)
        self._load_defaults()

        # 主题：默认跟随系统，可在右上角按钮里切 浅色 / 深色 / 跟随系统
        self._theme_name = theme_manager.load_theme()
        theme_manager.apply_theme(self._theme_name)
        self._update_theme_tooltip()

        self.timer = QTimer(self)
        self.timer.setInterval(80)
        self.timer.timeout.connect(self._poll)
        self.timer.start()

        # 系统主题变化（auto 模式下由系统切深浅色）时也要重刷日志颜色：
        # 日志是逐行设色的，不重刷会留下「深色底 + 浅色字」或反之的错配色。
        try:
            qconfig.themeChangedFinished.connect(self._on_theme_changed)
        except Exception:
            pass  # 旧版 qfluentwidgets 没有这个信号时静默跳过，不影响功能

    # ---------- 信号 ----------
    def _wire_signals(self) -> None:
        page = self.page
        page.profile_combo.currentIndexChanged.connect(self._on_profile_selected)
        for button, slot in zip(
            page.profile_btns,
            (self._save_profile, self._save_profile_as, self._rename_profile, self._delete_profile),
        ):
            button.clicked.connect(slot)
        page.browse_btn.clicked.connect(self._pick_out)
        page.clear_btn.clicked.connect(self._clear_token_cache)
        page.show_token_check.stateChanged.connect(self._toggle_token_visibility)
        page.theme_btn.clicked.connect(self._toggle_theme)
        page.run_btn.clicked.connect(self._start)
        page.open_btn.clicked.connect(self._open_out)
        # 日志右键菜单：面板只抛信号，动作在这里接线（面板不碰文件系统，便于单测）
        page.log.copyRequested.connect(self._copy_log)
        page.log.saveRequested.connect(self._save_log)
        page.log.clearRequested.connect(self._clear_log)
        # 键盘：Enter 开始导出（输入框里按 Enter 也算），Ctrl+L 清空日志
        self._install_shortcuts()

    def _install_shortcuts(self) -> None:
        """键盘快捷方式：Enter 导出 / Ctrl+Shift+L 清日志。

        用 QShortcut + WidgetWithChildrenShortcut 绑在本窗口上，避免全局热键影响其他程序。
        Enter 只在**没有打开对话框**时生效：QShortcut 天然只在窗口激活时触发，够用。
        """
        enter = QShortcut(QKeySequence(Qt.Key.Key_Return), self)
        enter.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        enter.activated.connect(self._start)
        self._shortcut_enter = enter

        # Ctrl+Shift+L 而不是 Ctrl+L：后者在输入框里是「选中行」的常见语义
        clear_log = QShortcut(QKeySequence("Ctrl+Shift+L"), self)
        clear_log.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        clear_log.activated.connect(self._clear_log)
        self._shortcut_clear_log = clear_log

    # ---------- 日志右键动作 ----------
    def _copy_log(self) -> None:
        text = self.page.log.to_plain_text()
        if not text.strip():
            InfoBar.info("日志为空", "还没有可复制的内容", parent=self,
                         position=InfoBarPosition.TOP, duration=2000)
            return
        QApplication.clipboard().setText(text)
        InfoBar.success("已复制", f"共 {len(text.splitlines())} 行日志已复制到剪贴板",
                        parent=self, position=InfoBarPosition.TOP, duration=2000)

    def _save_log(self) -> None:
        text = self.page.log.to_plain_text()
        if not text.strip():
            InfoBar.info("日志为空", "还没有可保存的内容", parent=self,
                         position=InfoBarPosition.TOP, duration=2000)
            return
        default_name = f"mirror-export-{datetime.now().strftime('%Y%m%d-%H%M%S')}.log"
        path, _ = QFileDialog.getSaveFileName(
            self, "保存日志", str(EXE_DIR / default_name), "日志文件 (*.log);;文本文件 (*.txt);;所有文件 (*)"
        )
        if not path:
            return
        try:
            Path(path).write_text(text, encoding="utf-8")
        except Exception as e:
            self._warn("保存失败", f"日志写入失败：{e}")
            return
        self._append_log(f"[缓存] 日志已保存 → {path}")

    def _clear_log(self) -> None:
        """清空日志：机器行本来就不进日志框，这里只清人类可读日志。"""
        self.page.log.clear_log()
        self.page.status_label.setText("日志已清空")
    # ---------- 主题 ----------
    def _on_theme_changed(self) -> None:
        """主题（含跟随系统时的自动切换）变化后重刷日志着色。"""
        try:
            self.page.log.refresh_colors()
        except Exception:
            pass

    def _update_theme_tooltip(self) -> None:
        self.page.theme_btn.setToolTip(
            f"主题：{theme_manager.theme_label(self._theme_name)}（点击切换）"
        )

    def _toggle_theme(self) -> None:
        """在 跟随系统 → 浅色 → 深色 之间循环，并记住选择。"""
        self._theme_name = theme_manager.next_theme(self._theme_name)
        theme_manager.apply_theme(self._theme_name)
        theme_manager.save_theme(self._theme_name)
        self._update_theme_tooltip()
        # 已写进日志的颜色是逐行设置的，主题一变要整体重刷，否则深色底上留着浅色系颜色
        self.page.log.refresh_colors()
        self._append_log(f"界面主题已切换为「{theme_manager.theme_label(self._theme_name)}」")

    # ---------- 组合（仓库 + 输出目录 + Token + 选项） ----------
    def _apply_profile(self, p: dict) -> None:
        """把组合字段刷进控件。"""
        page = self.page
        page.repo_edit.setText(p.get("repo") or "")
        page.out_edit.setText(p.get("out") or "")
        page.max_prs.setValue(int(p.get("max_prs", 80)))
        page.max_issues.setValue(int(p.get("max_issues", 80)))
        page.max_commits.setValue(int(p.get("max_commits", 100)))
        page.max_tags.setValue(int(p.get("max_tags", 50)))
        page.skip_details_check.setChecked(bool(p.get("skip_details", False)))
        page.include_contents_check.setChecked(bool(p.get("include_contents", True)))
        page.force_check.setChecked(bool(p.get("force", True)))
        page.incremental_check.setChecked(bool(p.get("incremental", True)))
        # Token：按「显示明文」决定显示掩码还是明文
        self._set_token_field(p.get("token") or "")

    def _set_token_field(self, token: str) -> None:
        """把 Token 写进输入框；掩码显示时用 mask_token，真值仍保存在 _token_plain。"""
        token = (token or "").strip()
        self._token_plain = token
        show = self.page.show_token_check.isChecked()
        self.page.token_edit.setText(token if show else (mask_token(token) if token else ""))

    def _token_field_text(self) -> str:
        return self.page.token_edit.text().strip()

    def _resolve_token(self, raw: str) -> str:
        """解析 Token 输入框内容。

        掩码显示时输入框里是 mask_token 的结果，真值存在 _token_plain；
        只要输入框内容与掩码一致，就返回真值（用户没改过）。
        """
        raw = (raw or "").strip()
        plain = (self._token_plain or "").strip()
        if plain and raw == mask_token(plain):
            return plain
        return raw

    def _capture_profile(self, name: str) -> dict:
        """把当前控件值收成一个组合。"""
        page = self.page
        return {
            "name": name,
            "repo": page.repo_edit.text().strip().strip("/"),
            "out": page.out_edit.text().strip(),
            "token": self._resolve_token(self._token_field_text()),
            "max_prs": int(page.max_prs.value()),
            "max_issues": int(page.max_issues.value()),
            "max_commits": int(page.max_commits.value()),
            "max_tags": int(page.max_tags.value()),
            "skip_details": bool(page.skip_details_check.isChecked()),
            "include_contents": bool(page.include_contents_check.isChecked()),
            "force": bool(page.force_check.isChecked()),
            "incremental": bool(page.incremental_check.isChecked()),
        }

    def _profile_for_save(self, name: str) -> dict:
        """收当前控件为组合。

        Token 栏解析为空时保留该组合已存的 Token：掩码显示下用户不动它就是「没改」，
        若因此把真值覆盖成空串会丢 Token。要彻底清掉本机 Token 请用「清除缓存」。
        """
        snap = self._capture_profile(name)
        if not snap["token"]:
            old = get_profile(name) or {}
            snap["token"] = (old.get("token") or "").strip()
        return snap

    def _refresh_profile_choices(self, keep_current: str | None = None, apply: bool = False) -> None:
        """下拉列出全部组合：名称 · 仓库。

        apply=True 时把最终选中的组合刷进控件（删除/重命名后保持界面一致）。
        """
        labels: list[str] = []
        self._profile_names = {}
        for p in list_profiles():
            name = p.get("name") or ""
            repo = p.get("repo") or "(未设仓库)"
            label = f"{name}  ·  {repo}"
            base, n = label, 2
            while label in self._profile_names:
                label = f"{base} ({n})"
                n += 1
            self._profile_names[label] = name
            labels.append(label)

        combo = self.page.profile_combo
        selected = ""
        self._loading_profile = True
        try:
            combo.clear()
            combo.addItems(labels)
            if keep_current:
                for label, name in self._profile_names.items():
                    if name == keep_current:
                        combo.setCurrentText(label)
                        selected = name
                        break
            if not selected:
                # 没有指定保留项（如删除后）：跟随 active_profile() 的最近使用回落，
                # 与下次启动加载的组合保持一致，而不是简单取文件里第一条
                act = active_profile() or {}
                act_name = act.get("name") or ""
                for label, name in self._profile_names.items():
                    if name == act_name:
                        combo.setCurrentText(label)
                        selected = name
                        break
            if not selected and labels:
                combo.setCurrentIndex(0)
                selected = self._profile_names.get(combo.currentText(), "")
        finally:
            self._loading_profile = False

        self._selected_profile = selected
        if apply:
            p = get_profile(selected) if selected else None
            if p:
                self._apply_profile(p)
                self._append_log(f"已切换到组合「{selected}」")
            else:
                # 一个组合都不剩：回到内置默认值
                self._apply_profile(dict(DEFAULT_PROFILE, name=""))

    def _current_profile_name(self) -> str:
        return self._profile_names.get(self.page.profile_combo.currentText(), "")

    def _on_profile_selected(self, _index: int = 0) -> None:
        if self._loading_profile:
            return
        name = self._current_profile_name()
        if not name:
            return
        self._selected_profile = name
        p = get_profile(name)
        if not p:
            return
        self._apply_profile(p)
        self._append_log(f"已切换到组合「{name}」→ {p.get('repo') or '(未设仓库)'}")

    def _save_profile(self) -> None:
        name = self._current_profile_name()
        if not name:
            self._save_profile_as()
            return
        try:
            upsert_profile(self._profile_for_save(name))
        except Exception as e:
            self._warn("保存失败", str(e))
            return
        self._refresh_profile_choices(keep_current=name)
        self._append_log(f"已保存组合「{name}」")

    def _save_profile_as(self) -> None:
        name = self._ask_text("另存为组合", "组合名称：", placeholder="例如 A项目-产品库")
        if name is None or not name:
            return
        if get_profile(name):
            if not self._confirm("确认", f"组合「{name}」已存在，覆盖？"):
                return
        elif not self.page.repo_edit.text().strip():
            self._warn("参数不完整", "仓库格式应为 owner/name，例如 octocat/hello-world")
            return
        try:
            upsert_profile(self._profile_for_save(name))
        except Exception as e:
            self._warn("保存失败", str(e))
            return
        self._refresh_profile_choices(keep_current=name)
        self._append_log(f"已新建组合「{name}」")

    def _rename_profile(self) -> None:
        name = self._current_profile_name()
        if not name:
            return
        new = self._ask_text("重命名组合", "新名称：", text=name)
        if new is None or not new or new == name:
            return
        try:
            rename_profile(name, new)
        except Exception as e:
            self._warn("重命名失败", str(e))
            return
        self._refresh_profile_choices(keep_current=new, apply=True)
        self._append_log(f"组合「{name}」已重命名为「{new}」")

    def _delete_profile(self) -> None:
        name = self._current_profile_name()
        if not name:
            return
        if not self._confirm("确认", f"删除组合「{name}」？\n（该组合保存的 Token 也会一并删除）"):
            return
        delete_profile(name)
        self._refresh_profile_choices(apply=True)
        if not list_profiles():
            self.page.status_label.setText(ExportPage.IDLE_NO_PROFILE_TEXT)
        self._append_log(f"已删除组合「{name}」")

    def _load_defaults(self) -> None:
        profiles = list_profiles()
        p = active_profile()
        if p:
            self._apply_profile(p)
        self._refresh_profile_choices(keep_current=p.get("name") if p else "")
        self.page.status_label.setText(
            ExportPage.IDLE_TEXT if p else ExportPage.IDLE_NO_PROFILE_TEXT
        )
        if not p:
            # 全新使用：没有组合时用环境变量里的 Token 打个底
            env = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
            if env:
                self._set_token_field(env)
        if profiles:
            self._append_log(f"已加载组合「{(p or {}).get('name')}」（共 {len(profiles)} 个）")
        out = (p or {}).get("out") or ""
        if out and Path(out).exists():
            self._append_log(f"上次输出目录: {out}")
            self._append_log("默认增量导出：只拉有变化的 Commit / PR / Issue / README")

    def _toggle_token_visibility(self) -> None:
        """切换 Token 输入框的掩码/明文显示。"""
        cur = self._token_field_text()
        plain = (self._token_plain or "").strip()
        # 输入框内容与掩码不同 → 用户手改过，以输入框为准
        if plain and cur != mask_token(plain):
            plain = cur
        self._token_plain = plain
        show = self.page.show_token_check.isChecked()
        self.page.token_edit.setText(plain if show else (mask_token(plain) if plain else ""))

    def _clear_token_cache(self) -> None:
        if not self._confirm(
            "确认", "清除本机全部组合缓存？\n（仓库、输出目录、Token 与各选项都会被清空）"
        ):
            return
        try:
            if TOKEN_CACHE_FILE.exists():
                TOKEN_CACHE_FILE.unlink()
        except Exception:
            pass
        self._token_plain = ""
        self.page.token_edit.setText("")
        self._profile_names = {}
        self._selected_profile = ""
        self.page.profile_combo.clear()
        # 提示语承诺「仓库、输出目录、Token 与各选项都会被清空」，因此控件也要真的归位，
        # 否则用户以为清空了、实际界面仍留着旧值（此时点导出会拿残留参数跑一次）。
        try:
            self._apply_profile(dict(DEFAULT_PROFILE, name=""))
        except Exception:
            pass
        self._append_log("已清除本机组合缓存（仓库 / 输出目录 / Token / 数量上限 / 选项均已恢复默认）")
        self.page.status_label.setText(ExportPage.IDLE_NO_PROFILE_TEXT)

    # ---------- 输出目录 ----------
    def _pick_out(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "选择输出目录")
        if directory:
            self.page.out_edit.setText(directory)

    def _open_out(self) -> None:
        out = self.page.out_edit.text().strip()
        if out and Path(out).exists():
            os.startfile(out)  # type: ignore[attr-defined]

    # ---------- 日志 / 进度 ----------
    def _append_log(self, s: str) -> None:
        self.page.log.append_line(s)
        self._run_log.append(s)

    def _apply_progress(self, payload: str) -> None:
        info = parse_progress(payload)
        if not info:
            return
        self.page.progress.apply_info(info)
        self.page.status_label.setText(info["status_text"])

    def _poll(self) -> None:
        try:
            while True:
                line = self.q.get_nowait()
                if line == "__DONE__":
                    self.page.progress.set_done()
                    self.page.run_btn.setEnabled(True)
                    self.page.run_btn.setText("开始导出")
                    self.page.open_btn.setEnabled(True)
                    self._set_profile_btns_state(True)
                    # 导出期间组合按钮被禁用，不可能有并发保存，这里再刷一次下拉
                    self._refresh_profile_choices(keep_current=self._current_profile_name())
                    self.page.status_label.setText("导出完成")
                    QApplication.beep()
                    self._notify_success()
                elif line == "__FAIL__":
                    self.page.run_btn.setEnabled(True)
                    self.page.run_btn.setText("开始导出")
                    # 失败时也要放开「打开输出目录」：输出目录里往往已有部分产物，
                    # 用户需要进去看日志/半成品
                    self.page.open_btn.setEnabled(True)
                    self._set_profile_btns_state(True)
                    # 保留进度条当前位置，便于定位卡在哪一步
                    self.page.progress.set_failed()
                    reason = summarize_failure(self._run_log)
                    # 状态栏只放一句摘要（过长会挤掉后面的说明），完整信息在日志里
                    self.page.status_label.setText(f"导出失败：{reason}")
                    self._notify_failure(reason)
                elif line.startswith("__PROGRESS__"):
                    self._apply_progress(line[len("__PROGRESS__"):])
                else:
                    self._append_log(line)
        except queue.Empty:
            pass

    # ---------- 完成/失败提示 ----------
    def _notify_success(self) -> None:
        """成功提示条：带「打开输出目录」按钮，省得再去操作栏点一次。"""
        bar = InfoBar.success(
            "导出完成",
            "可以把整个输出目录拷进内网，双击 index.html 浏览",
            parent=self,
            position=InfoBarPosition.TOP,
            duration=6000,
            isClosable=True,
        )
        open_btn = PushButton("打开输出目录", bar)
        open_btn.clicked.connect(self._open_out)
        bar.addWidget(open_btn)

    def _notify_failure(self, reason: str) -> None:
        """失败提示条：常驻到用户关掉（duration=-1），因为失败往往需要立刻处理。

        失败时也带「打开输出目录」：输出目录里通常已有半成品与日志，用户要进去看。
        """
        bar = InfoBar.error(
            "导出失败",
            reason,
            parent=self,
            position=InfoBarPosition.TOP,
            duration=-1,
            isClosable=True,
        )
        open_btn = PushButton("打开输出目录", bar)
        open_btn.clicked.connect(self._open_out)
        bar.addWidget(open_btn)

    # ---------- 参数 / 导出 ----------
    def _collect_args(self):
        page = self.page
        repo = page.repo_edit.text().strip().strip("/")
        if not repo or "/" not in repo:
            raise ValueError("仓库格式应为 owner/name，例如 octocat/hello-world")
        token = self._resolve_token(self._token_field_text())
        if not token:
            raise ValueError("请填写或选择 GitHub Token")
        out = page.out_edit.text().strip()
        if not out:
            out = str(EXE_DIR / f"gh-mirror-{repo.replace('/', '-')}")
            page.out_edit.setText(out)

        class NS:
            pass

        a = NS()
        a.repo = repo
        a.out = out
        a.token = token
        a.force = bool(page.force_check.isChecked())
        a.max_prs = int(page.max_prs.value())
        a.max_issues = int(page.max_issues.value())
        a.max_commits = int(page.max_commits.value())
        a.max_tags = int(page.max_tags.value())
        a.max_file_bytes = 400_000
        a.skip_details = bool(page.skip_details_check.isChecked())
        a.include_contents = bool(page.include_contents_check.isChecked())
        # diff 条数与本轮 Commit 窗口一致；缓存无限累积
        a.max_commit_diffs = 0 if a.skip_details else int(page.max_commits.value())
        a.no_assets = False
        a.incremental = bool(page.incremental_check.isChecked())
        return a

    def _set_profile_btns_state(self, enabled: bool) -> None:
        """导出期间锁定组合区与全部导出参数控件。

        工作线程在启动时拍下快照、结束后把快照写回磁盘，因此导出中任何被改动的
        控件，其改动都会在写回时被静默吞掉（界面显示新值、缓存里仍是旧值）。
        这里统一锁住：组合下拉、四个组合按钮、「清除缓存」，以及所有导出参数字段。
        """
        for button in self.page.profile_btns:
            button.setEnabled(enabled)
        # 下拉在导出期间同样不可展开
        self.page.profile_combo.setEnabled(enabled)
        # 「清除缓存」会删掉缓存文件，导出中同样要锁住
        self.page.clear_btn.setEnabled(enabled)
        # 仓库 / 输出目录 / 浏览… / Token / 显示明文 / 记住到本机缓存
        # / 四个数量上限 / 四个选项开关
        for widget in self.param_widgets:
            widget.setEnabled(enabled)

    def _start(self) -> None:
        if export_mod is None:
            self._warn("错误", "未找到 export 模块，exe 打包可能不完整")
            return
        try:
            args = self._collect_args()
        except Exception as e:
            self._warn("参数不完整", str(e))
            return

        self.page.run_btn.setEnabled(False)
        self.page.run_btn.setText("导出中…")
        self.page.open_btn.setEnabled(False)
        self._set_profile_btns_state(False)
        # 清掉上一轮的残留值，四段信息各自归零占位
        self.page.progress.set_starting()
        self.page.status_label.setText("正在导出…")
        self._run_log = []  # 每轮重置，失败摘要只从本轮日志里挑
        self._append_log(f"开始导出 {args.repo} → {args.out}")

        # 导出前先记下当前控件对应的组合名与快照（工作线程不再碰界面）。
        # Token 解析为空时不取快照：既没得存，也避免把已存的 Token 覆盖成空串。
        profile_name = self._current_profile_name()
        snapshot = self._profile_for_save(profile_name) if profile_name else None
        if snapshot and not (snapshot.get("token") or "").strip():
            snapshot = None
        remember = bool(self.page.remember_check.isChecked())

        def run():
            old_out, old_err = sys.stdout, sys.stderr
            sys.stdout = sys.stderr = LogWriter(self.q)
            # 终止信号必须「必达」：export() 在参数校验失败时会 sys.exit()，
            # 抛出的是 SystemExit（BaseException 子类），`except Exception` 接不住，
            # 会让 __DONE__ / __FAIL__ 都不入队 → 界面永远停在「导出中…」、
            # 所有按钮与参数控件永久禁用，只能重启程序。
            # 因此这里用 BaseException 兜底，并在 finally 里再补一次（防重复入队）。
            finished = False
            try:
                export_mod.export(args)
                # 导出成功：仓库/输出目录/Token/选项整体写回该组合。
                # 此时组合按钮已被禁用，不存在并发保存，快照不会被别人的修改顶掉。
                if remember and snapshot:
                    try:
                        upsert_profile(snapshot)
                        self.q.put(f"[缓存] 组合「{profile_name}」已保存 → {TOKEN_CACHE_FILE.name}")
                    except Exception as e:
                        # 写盘失败（目录只读 / 磁盘满 / 被杀软锁）必须明确告知，
                        # 不能让用户以为参数已持久化
                        self.q.put(f"[缓存] 组合「{profile_name}」保存失败：{e}")
                        self.q.put("[缓存] 参数仅本次有效，重启后会丢失；请检查程序所在目录是否可写")
                elif remember:
                    self.q.put("[缓存] 当前未选组合，未保存；可点「另存为…」建一个")
                self.q.put("__DONE__")
                finished = True
            except BaseException as e:  # noqa: BLE001 - 必须兜住 SystemExit
                # SystemExit 的 str() 为空，补一句可读说明，便于定位
                if isinstance(e, SystemExit):
                    self.q.put(f"[错误] 导出中止（exit code {e.code}），详见上方日志")
                else:
                    self.q.put(f"[错误] {e}")
                self.q.put(traceback.format_exc())
                self.q.put("__FAIL__")
                finished = True
            finally:
                sys.stdout, sys.stderr = old_out, old_err
                # 极端兜底：若上面的分支因故没发终止信号，这里补发，保证界面能恢复
                if not finished:
                    try:
                        self.q.put("__FAIL__")
                    except Exception:
                        pass

        self.worker = threading.Thread(target=run, daemon=True)
        self.worker.start()

    # ---------- 对话框 ----------
    def _ask_text(self, title: str, label: str, text: str = "", placeholder: str = "") -> str | None:
        dialog = TextInputDialog(title, label, text, placeholder, self)
        if not dialog.exec():
            return None
        return dialog.value()

    def _confirm(self, title: str, content: str) -> bool:
        box = MessageBox(title, content, self)
        box.yesButton.setText("确定")
        box.cancelButton.setText("取消")
        return bool(box.exec())

    def _warn(self, title: str, content: str) -> None:
        """提示框：与 Tk 版 `messagebox.showwarning` 等价（模态、必须点掉）。"""
        box = MessageBox(title, content, self)
        box.yesButton.setText("知道了")
        box.hideCancelButton()
        box.exec()
