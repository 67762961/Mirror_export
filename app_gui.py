#!/usr/bin/env python3
"""Mirror_export · 图形界面。外网运行，生成可拷入内网的只读静态站。"""

from __future__ import annotations

import json
import os
import queue
import sys
import threading
import traceback
from datetime import datetime, timezone
from pathlib import Path

# 高 DPI：避免 Windows 缩放导致界面/图标发糊
if sys.platform == "win32":
    try:
        import ctypes

        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_AWARE_V2
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass
    except Exception:
        pass

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

# 与 export.py 同目录；打包后 viewer 在 _MEIPASS
if getattr(sys, "frozen", False):
    ROOT = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    EXE_DIR = Path(sys.executable).resolve().parent
else:
    ROOT = Path(__file__).resolve().parent
    EXE_DIR = ROOT

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import export as export_mod
except Exception:
    export_mod = None

TOKEN_CACHE_FILE = EXE_DIR / "gh_token_cache.json"
MAX_TOKEN_HISTORY = 8


def mask_token(token: str) -> str:
    token = token or ""
    if len(token) <= 12:
        return token[:4] + "••••" if token else ""
    return f"{token[:6]}…{token[-4:]}"


def load_token_cache() -> dict:
    try:
        if TOKEN_CACHE_FILE.exists():
            data = json.loads(TOKEN_CACHE_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("tokens"), list):
                return data
    except Exception:
        pass
    return {"tokens": [], "last_repo": ""}


def save_token_cache(data: dict) -> None:
    try:
        TOKEN_CACHE_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass


def remember_token(token: str, repo: str, out: str = "", incremental: bool = True) -> None:
    token = (token or "").strip()
    if not token:
        return
    data = load_token_cache()
    items = [t for t in data.get("tokens", []) if t.get("token") != token]
    items.insert(
        0,
        {
            "token": token,
            "repo": repo,
            "last_used": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    )
    data["tokens"] = items[:MAX_TOKEN_HISTORY]
    data["last_repo"] = repo
    if out:
        data["last_out"] = out
    data["incremental"] = bool(incremental)
    save_token_cache(data)


class LogWriter:
    """把 print 重定向到队列，供 UI 消费。"""

    def __init__(self, q: queue.Queue):
        self.q = q

    def write(self, s: str):
        s = (s or "").rstrip("\n")
        if s:
            self.q.put(s)

    def flush(self):
        pass


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Mirror_export")
        self.geometry("900x720")
        self.minsize(820, 640)
        self._token_map: dict[str, str] = {}
        self._setup_style()
        self._set_icon()
        self._build_ui()
        self._load_defaults()
        self.q: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        self.after(120, self._poll)

    def _setup_style(self):
        """与导出网页 viewer 同一套 GitHub Light 令牌。"""
        try:
            style = ttk.Style(self)
            style.theme_use("clam")
        except Exception:
            return
        bg = "#ffffff"
        subtle = "#f6f8fa"
        border = "#d0d7de"
        border_muted = "#d8dee4"
        ink = "#1f2328"
        muted = "#656d76"
        accent = "#0969da"
        accent_soft = "#ddf4ff"
        green = "#1f883d"
        green_hover = "#1a7f37"

        self.configure(bg=bg)
        style.configure(".", font=("Segoe UI", 10), background=bg, foreground=ink, borderwidth=0)
        style.configure("TFrame", background=bg)
        style.configure("Card.TFrame", background=bg)
        style.configure("TLabel", background=bg, foreground=ink)
        style.configure("Muted.TLabel", background=bg, foreground=muted, font=("Segoe UI", 9))
        style.configure("Title.TLabel", background=bg, foreground=ink, font=("Segoe UI", 18, "bold"))
        style.configure("Sub.TLabel", background=bg, foreground=muted, font=("Segoe UI", 10))
        style.configure("Section.TLabel", background=bg, foreground=ink, font=("Segoe UI", 11, "bold"))
        style.configure("TLabelframe", background=bg, foreground=ink, bordercolor=border, relief="solid")
        style.configure("TLabelframe.Label", background=bg, foreground=muted, font=("Segoe UI", 9))
        style.configure(
            "TButton",
            background=subtle,
            foreground=ink,
            bordercolor=border,
            padding=(12, 6),
            focusthickness=0,
        )
        style.map(
            "TButton",
            background=[("active", "#eaeef2"), ("disabled", subtle)],
            foreground=[("disabled", muted)],
        )
        style.configure(
            "Accent.TButton",
            background=green,
            foreground="#ffffff",
            bordercolor=green,
            padding=(20, 9),
            font=("Segoe UI", 10, "bold"),
        )
        style.map(
            "Accent.TButton",
            background=[("active", green_hover), ("disabled", "#8c959f")],
            foreground=[("disabled", "#ffffff")],
        )
        style.configure("TCheckbutton", background=bg, foreground=ink, padding=(2, 4))
        style.map("TCheckbutton", background=[("active", bg)])
        # 方块勾选贴图（供 SquareCheck 使用）
        self._chk_off = self._make_chk_image(False)
        self._chk_on = self._make_chk_image(True)

        style.configure(
            "TEntry",
            fieldbackground="#ffffff",
            foreground=ink,
            insertcolor=ink,
            bordercolor=border,
            padding=7,
            lightcolor=border,
            darkcolor=border,
        )
        style.configure(
            "TCombobox",
            fieldbackground="#ffffff",
            foreground=ink,
            bordercolor=border,
            padding=7,
            arrowcolor=muted,
            background="#ffffff",
        )
        style.map("TCombobox", fieldbackground=[("readonly", "#ffffff")], foreground=[("readonly", ink)])
        style.configure(
            "TSpinbox",
            fieldbackground="#ffffff",
            foreground=ink,
            arrowcolor=muted,
            bordercolor=border,
            padding=4,
            background="#ffffff",
        )
        style.configure(
            "Horizontal.TProgressbar",
            background=accent,
            troughcolor=border_muted,
            bordercolor=border_muted,
            lightcolor=accent,
            darkcolor=accent,
        )
        style.configure(
            "Status.TLabel",
            background=subtle,
            foreground=muted,
            padding=(10, 8),
            relief="flat",
        )
        style.configure("TScrollbar", background=subtle, troughcolor=bg, bordercolor=bg, arrowcolor=muted)

    def _set_icon(self):
        ico = ROOT / "assets" / "icon.ico"
        png = ROOT / "assets" / "icon-256.png"
        try:
            # 窗口内优先用 256 PNG（PhotoImage 支持 PNG，比缩放 ICO 清晰）
            if png.exists():
                img = tk.PhotoImage(file=str(png))
                self.iconphoto(True, img)
                self._png_ref = img
            if ico.exists():
                try:
                    self.iconbitmap(str(ico))
                except Exception:
                    pass
        except Exception:
            pass

        # 任务栏/标题栏：用系统 API 从 ICO 加载 256 大图标
        self.after(50, lambda: self._apply_win32_icon(ico))

    def _apply_win32_icon(self, ico: Path) -> None:
        if sys.platform != "win32" or not ico.exists():
            return
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.WinDLL("user32", use_last_error=True)
            IMAGE_ICON = 1
            LR_LOADFROMFILE = 0x0010
            WM_SETICON = 0x0080
            ICON_SMALL = 0
            ICON_BIG = 1

            self.update_idletasks()
            hwnd = self.winfo_id()
            # Tk 子窗口 → 顶层窗口句柄
            parent = user32.GetParent(hwnd)
            if parent:
                hwnd = parent

            path = str(ico)
            big = user32.LoadImageW(None, path, IMAGE_ICON, 256, 256, LR_LOADFROMFILE)
            small = user32.LoadImageW(None, path, IMAGE_ICON, 32, 32, LR_LOADFROMFILE)
            if big:
                user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, big)
            if small:
                user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, small)
        except Exception:
            pass

    def _make_chk_image(self, checked: bool) -> tk.PhotoImage:
        """14x14 方块：空心 / 绿色实心，不用 ✕。"""
        img = tk.PhotoImage(width=14, height=14)
        if checked:
            # 绿色实心 + 深一点边
            img.put("#1a7f37", to=(0, 0, 14, 14))
            img.put("#1f883d", to=(1, 1, 13, 13))
        else:
            img.put("#d0d7de", to=(0, 0, 14, 14))
            img.put("#ffffff", to=(1, 1, 13, 13))
        return img

    def _square_check(self, parent, text: str, variable: tk.BooleanVar, command=None) -> tk.Checkbutton:
        """白底、方块指示器，勾选时为填色方。"""
        def _sync():
            img = self._chk_on if variable.get() else self._chk_off
            widget.configure(image=img)
            if command:
                command()

        widget = tk.Checkbutton(
            parent,
            text=text,
            variable=variable,
            command=_sync,
            image=self._chk_on if variable.get() else self._chk_off,
            selectimage=self._chk_on,
            indicatoron=False,  # 关掉系统默认 ✕/对勾
            compound="left",
            padx=6,
            pady=2,
            bd=0,
            highlightthickness=0,
            bg="#ffffff",
            activebackground="#ffffff",
            fg="#1f2328",
            activeforeground="#1f2328",
            anchor="w",
            font=("Segoe UI", 10),
        )
        return widget

    def _refresh_token_choices(self, keep_current: str | None = None):
        data = load_token_cache()
        labels = []
        self._token_map = {}
        for item in data.get("tokens", []):
            tok = item.get("token") or ""
            if not tok:
                continue
            when = (item.get("last_used") or "")[:10]
            label = f"{mask_token(tok)}"
            if when:
                label += f"  ·  {when}"
            # 去重 label
            base = label
            n = 2
            while label in self._token_map:
                label = f"{base} ({n})"
                n += 1
            self._token_map[label] = tok
            labels.append(label)

        self.token_combo.configure(values=labels)
        if keep_current:
            self.token_var.set(keep_current)
        elif labels:
            # 默认选最近一条
            self.token_var.set(labels[0])
        else:
            env = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
            self.token_var.set(env)

    def _resolve_token(self, raw: str) -> str:
        raw = (raw or "").strip()
        if raw in self._token_map:
            return self._token_map[raw]
        # 若用户直接粘贴完整 token
        return raw

    # ---------- UI ----------
    def _build_ui(self):
        outer = ttk.Frame(self, padding=(28, 24, 28, 20))
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        # 仅日志行可伸展
        outer.rowconfigure(9, weight=1)

        def field_row(row, label):
            r = ttk.Frame(outer)
            r.grid(row=row, column=0, sticky="ew", pady=(0, 14))
            r.columnconfigure(1, weight=1)
            ttk.Label(r, text=label, width=12, anchor="e").grid(row=0, column=0, sticky="e", padx=(0, 12))
            return r

        # 页头
        head = ttk.Frame(outer)
        head.grid(row=0, column=0, sticky="ew", pady=(0, 22))
        head.columnconfigure(0, weight=1)
        ttk.Label(head, text="Mirror_export", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            head,
            text="在外网导出自包含静态站，拷入内网后双击 index.html 浏览",
            style="Sub.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(6, 0))

        # 仓库
        r = field_row(1, "仓库")
        self.repo_var = tk.StringVar(value="owner/repo")
        ttk.Entry(r, textvariable=self.repo_var).grid(row=0, column=1, sticky="ew")

        # 输出目录
        r = field_row(2, "输出目录")
        self.out_var = tk.StringVar(value="")
        ttk.Entry(r, textvariable=self.out_var).grid(row=0, column=1, sticky="ew", padx=(0, 10))
        ttk.Button(r, text="浏览…", command=self._pick_out).grid(row=0, column=2, sticky="e")

        # Token
        r = field_row(3, "Token")
        self.token_var = tk.StringVar(value="")
        self.token_combo = ttk.Combobox(r, textvariable=self.token_var, postcommand=self._refresh_token_choices)
        self.token_combo.grid(row=0, column=1, sticky="ew", padx=(0, 10))
        self.token_combo.bind("<<ComboboxSelected>>", self._on_token_selected)
        ttk.Button(r, text="清除缓存", command=self._clear_token_cache).grid(row=0, column=2, sticky="e")

        # Token 附属选项
        tok_sub = ttk.Frame(outer)
        tok_sub.grid(row=4, column=0, sticky="ew", pady=(0, 18))
        ttk.Label(tok_sub, text="", width=12).grid(row=0, column=0)
        self.show_token = tk.BooleanVar(value=False)
        self.remember_var = tk.BooleanVar(value=True)
        self._square_check(
            tok_sub, "显示明文", self.show_token, command=self._toggle_token_visibility
        ).grid(row=0, column=1, sticky="w", padx=(0, 20))
        self._square_check(tok_sub, "记住到本机缓存", self.remember_var).grid(row=0, column=2, sticky="w")

        # 分隔
        ttk.Separator(outer, orient="horizontal").grid(row=5, column=0, sticky="ew", pady=(4, 18))

        # 数量上限
        limits = ttk.LabelFrame(outer, text="  数量上限  ", padding=(16, 12))
        limits.grid(row=6, column=0, sticky="ew", pady=(0, 16))
        for i in range(8):
            limits.columnconfigure(i, weight=1)
        self.max_prs = tk.IntVar(value=80)
        self.max_issues = tk.IntVar(value=80)
        self.max_commits = tk.IntVar(value=100)
        self.max_tags = tk.IntVar(value=50)

        def spin(parent, label, var, col, to=10000):
            ttk.Label(parent, text=label).grid(row=0, column=col, sticky="e", padx=(0, 6))
            ttk.Spinbox(parent, from_=1, to=to, textvariable=var, width=7).grid(
                row=0, column=col + 1, sticky="w", padx=(0, 24)
            )

        spin(limits, "PR", self.max_prs, 0)
        spin(limits, "Issue", self.max_issues, 2)
        spin(limits, "每轮 Commit", self.max_commits, 4)
        spin(limits, "Tag", self.max_tags, 6)
        ttk.Label(
            limits,
            text="Commit/diff 缓存无限累积；每轮从 HEAD 往回拉这么多条，旧的不删",
            style="Muted.TLabel",
        ).grid(row=1, column=0, columnspan=8, sticky="w", pady=(12, 0))

        # 选项
        opts = ttk.Frame(outer)
        opts.grid(row=7, column=0, sticky="ew", pady=(0, 20))
        self.skip_details = tk.BooleanVar(value=False)
        self.include_contents = tk.BooleanVar(value=True)
        self.force_var = tk.BooleanVar(value=True)
        self.incremental_var = tk.BooleanVar(value=True)
        ttk.Label(opts, text="选项", width=12, anchor="e").grid(row=0, column=0, sticky="e", padx=(0, 12))
        box = ttk.Frame(opts)
        box.grid(row=0, column=1, sticky="w")
        self._square_check(box, "跳过 diff / 评论", self.skip_details).grid(row=0, column=0, sticky="w", padx=(0, 22))
        self._square_check(box, "缓存源码", self.include_contents).grid(row=0, column=1, sticky="w", padx=(0, 22))
        self._square_check(box, "覆盖输出目录", self.force_var).grid(row=0, column=2, sticky="w", padx=(0, 22))
        self._square_check(box, "增量导出", self.incremental_var).grid(row=0, column=3, sticky="w")

        # 操作（始终可见）
        actions = ttk.Frame(outer)
        actions.grid(row=8, column=0, sticky="ew", pady=(4, 14))
        actions.columnconfigure(5, weight=1)
        self.run_btn = ttk.Button(actions, text="  开始导出  ", style="Accent.TButton", command=self._start)
        self.run_btn.grid(row=0, column=0, sticky="w", ipady=4)
        self.open_btn = ttk.Button(actions, text="  打开输出目录  ", command=self._open_out, state="disabled")
        self.open_btn.grid(row=0, column=1, sticky="w", padx=(12, 0), ipady=4)
        self.progress_label = ttk.Label(actions, text="", style="Muted.TLabel")
        self.progress_label.grid(row=0, column=3, sticky="e", padx=(12, 0))
        self.progress = ttk.Progressbar(actions, mode="determinate", length=200, maximum=100)
        self.progress.grid(row=0, column=4, sticky="e", padx=(8, 0))

        # 日志
        logwrap = ttk.Frame(outer, style="Card.TFrame")
        logwrap.grid(row=9, column=0, sticky="nsew", pady=(0, 14))
        logwrap.rowconfigure(0, weight=1)
        logwrap.columnconfigure(0, weight=1)
        self.log = tk.Text(
            logwrap,
            wrap="word",
            height=12,
            font=("Consolas", 10),
            bg="#f6f8fa",
            fg="#1f2328",
            insertbackground="#1f2328",
            selectbackground="#0969da",
            selectforeground="#ffffff",
            relief="solid",
            borderwidth=1,
            highlightthickness=1,
            highlightbackground="#d0d7de",
            padx=14,
            pady=12,
        )
        self.log.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(logwrap, command=self.log.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=scroll.set)

        self.status = ttk.Label(outer, text="就绪 · 导出完成后拷入内网，双击 index.html", style="Status.TLabel", anchor="w")
        self.status.grid(row=10, column=0, sticky="ew")

        self._append_log("Mirror_export")
        self._append_log("填 Token 与仓库 → 开始导出 → 拷贝输出目录到内网")
        self._append_log("-" * 48)

    def _load_defaults(self):
        data = load_token_cache()
        last_repo = data.get("last_repo") or ""
        self.repo_var.set(last_repo)
        last_out = data.get("last_out") or ""
        if last_out:
            self.out_var.set(last_out)
        if "incremental" in data:
            self.incremental_var.set(bool(data.get("incremental")))
        self._refresh_token_choices()
        if last_out and Path(last_out).exists():
            self._append_log(f"已加载上次输出目录: {last_out}")
            self._append_log("默认增量导出：只拉有变化的 Commit / PR / Issue / README")

    def _on_token_selected(self, _event=None):
        # combobox 选中的是 label，_collect_args 时再解析成真 token
        pass

    def _toggle_token_visibility(self):
        # Combobox 无 show 属性，改为：显示时把 label 换成明文 token
        if self.show_token.get():
            cur = self.token_var.get()
            real = self._resolve_token(cur)
            if real:
                self.token_var.set(real)
        else:
            # 隐藏：若当前是完整 token，变回 mask label
            cur = self.token_var.get().strip()
            for label, tok in self._token_map.items():
                if tok == cur:
                    self.token_var.set(label)
                    return

    def _clear_token_cache(self):
        if not messagebox.askyesno("确认", "清除本机 Token 缓存？"):
            return
        try:
            if TOKEN_CACHE_FILE.exists():
                TOKEN_CACHE_FILE.unlink()
        except Exception:
            pass
        self.token_var.set("")
        self.token_combo.configure(values=[])
        self._token_map = {}
        self._append_log("已清除 Token 缓存")

    def _pick_out(self):
        d = filedialog.askdirectory(title="选择输出目录")
        if d:
            self.out_var.set(d)

    def _open_out(self):
        out = self.out_var.get().strip()
        if out and Path(out).exists():
            os.startfile(out)  # type: ignore[attr-defined]

    def _append_log(self, s: str):
        self.log.insert("end", s + "\n")
        # 日志过多时截断，避免卡顿
        try:
            line_count = int(self.log.index("end-1c").split(".")[0])
            if line_count > 4000:
                self.log.delete("1.0", "1000.0")
        except Exception:
            pass
        self.log.see("end")

    def _apply_progress(self, payload: str):
        try:
            info = json.loads(payload)
        except Exception:
            return
        stage = str(info.get("stage") or "")
        done = int(info.get("done") or 0)
        total = int(info.get("total") or 0)
        if total > 0:
            pct = max(0, min(100, int(done * 100 / total)))
            try:
                self.progress.configure(mode="determinate", maximum=100, value=pct)
            except Exception:
                pass
            self.progress_label.configure(text=f"{stage} {done}/{total} {pct}%")
            self.status.configure(text=f"{stage} {done}/{total}（{pct}%）")
        else:
            self.progress_label.configure(text=stage)
            self.status.configure(text=stage)

    def _poll(self):
        try:
            while True:
                line = self.q.get_nowait()
                if line == "__DONE__":
                    self.progress.stop()
                    self.progress.configure(mode="determinate", value=100)
                    self.progress_label.configure(text="100%")
                    self.run_btn.configure(state="normal", text="开始导出")
                    self.open_btn.configure(state="normal")
                    self.status.configure(text="导出完成")
                    self.bell()
                elif line == "__FAIL__":
                    self.progress.stop()
                    self.run_btn.configure(state="normal", text="开始导出")
                    self.progress_label.configure(text="失败")
                    self.status.configure(text="导出失败，请看日志")
                elif line.startswith("__PROGRESS__"):
                    self._apply_progress(line[len("__PROGRESS__"):])
                else:
                    self._append_log(line)
        except queue.Empty:
            pass
        self.after(80, self._poll)

    def _collect_args(self):
        repo = self.repo_var.get().strip().strip("/")
        if not repo or "/" not in repo:
            raise ValueError("仓库格式应为 owner/name，例如 octocat/hello-world")
        token = self._resolve_token(self.token_var.get())
        if not token:
            raise ValueError("请填写或选择 GitHub Token")
        out = self.out_var.get().strip()
        if not out:
            out = str(EXE_DIR / f"gh-mirror-{repo.replace('/', '-')}")
            self.out_var.set(out)

        class NS:
            pass

        a = NS()
        a.repo = repo
        a.out = out
        a.token = token
        a.force = bool(self.force_var.get())
        a.max_prs = int(self.max_prs.get())
        a.max_issues = int(self.max_issues.get())
        a.max_commits = int(self.max_commits.get())
        a.max_tags = int(self.max_tags.get())
        a.max_file_bytes = 400_000
        a.skip_details = bool(self.skip_details.get())
        a.include_contents = bool(self.include_contents.get())
        # diff 条数与本轮 Commit 窗口一致；缓存无限累积
        a.max_commit_diffs = 0 if self.skip_details.get() else int(self.max_commits.get())
        a.no_assets = False
        a.incremental = bool(self.incremental_var.get())
        return a

    def _start(self):
        if export_mod is None:
            messagebox.showerror("错误", "未找到 export 模块，exe 打包可能不完整")
            return
        try:
            args = self._collect_args()
        except Exception as e:
            messagebox.showwarning("参数不完整", str(e))
            return

        self.run_btn.configure(state="disabled", text="导出中…")
        self.open_btn.configure(state="disabled")
        try:
            self.progress.configure(mode="determinate", maximum=100, value=0)
        except Exception:
            self.progress.start(12)
        self.progress_label.configure(text="启动…")
        self.status.configure(text="正在导出…")
        self._append_log(f"开始导出 {args.repo} → {args.out}")

        def run():
            old_out, old_err = sys.stdout, sys.stderr
            sys.stdout = sys.stderr = LogWriter(self.q)
            try:
                export_mod.export(args)
                if self.remember_var.get():
                    remember_token(args.token, args.repo, out=args.out, incremental=args.incremental)
                    self.q.put(f"[缓存] Token/路径已保存 → {TOKEN_CACHE_FILE.name}")
                self.q.put("__DONE__")
            except Exception as e:
                self.q.put(f"[错误] {e}")
                self.q.put(traceback.format_exc())
                self.q.put("__FAIL__")
            finally:
                sys.stdout, sys.stderr = old_out, old_err

        self.worker = threading.Thread(target=run, daemon=True)
        self.worker.start()


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
