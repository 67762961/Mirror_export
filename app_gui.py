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
from tkinter import filedialog, messagebox, simpledialog, ttk

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
CACHE_VERSION = 2
MAX_PROFILES = 20

# 进度区各控件之间的统一间距，保证间隔视觉均匀
_GAP = 10

# 一个「组合」= 仓库 + 输出目录 + Token + 数量上限 + 各选项开关
PROFILE_FIELDS = (
    "repo",
    "out",
    "token",
    "max_prs",
    "max_issues",
    "max_commits",
    "max_tags",
    "skip_details",
    "include_contents",
    "force",
    "incremental",
)

DEFAULT_PROFILE = {
    "repo": "",
    "out": "",
    "token": "",
    "max_prs": 80,
    "max_issues": 80,
    "max_commits": 100,
    "max_tags": 50,
    "skip_details": False,
    "include_contents": True,
    "force": True,
    "incremental": True,
}


def mask_token(token: str) -> str:
    token = token or ""
    if len(token) <= 12:
        return token[:4] + "••••" if token else ""
    return f"{token[:6]}…{token[-4:]}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _normalize_profile(raw: dict, fallback_name: str = "") -> dict:
    """把任意来源的字典补齐成完整组合，缺失字段取默认值。"""
    p = dict(DEFAULT_PROFILE)
    p["name"] = (raw.get("name") or fallback_name or "").strip()
    for k in PROFILE_FIELDS:
        if k not in raw:
            continue
        v = raw[k]
        if k in ("max_prs", "max_issues", "max_commits", "max_tags"):
            try:
                p[k] = int(v)
            except Exception:
                pass
        elif k in ("skip_details", "include_contents", "force", "incremental"):
            p[k] = bool(v)
        else:
            p[k] = ("" if v is None else str(v)).strip()
    p["last_used"] = str(raw.get("last_used") or "")
    return p


def _migrate_v1(data: dict) -> dict:
    """v1（tokens 列表 + 全局 last_repo/last_out）→ v2 profiles。

    旧文件不删除，只做就地升级；每条 Token 合成一个组合，
    首个 Token 继承全局的 last_repo / last_out。
    """
    profiles = []
    seen_name = set()
    last_repo = (data.get("last_repo") or "").strip()
    last_out = (data.get("last_out") or "").strip()
    incremental = bool(data.get("incremental", True))
    for item in data.get("tokens") or []:
        if not isinstance(item, dict):
            continue
        tok = (item.get("token") or "").strip()
        if not tok:
            continue
        # 注意：用已生成条数判断“首条”，不能用原始下标
        #（前几项可能因非 dict / 空 token 被 continue 跳过）
        first = not profiles
        # 优先用该 Token 自己记录过的 repo；v1 的 last_repo 只是「最近用过」，
        # 仅在该条目没有 repo 时兜底，不能覆盖已有值（否则会丢用户的仓库信息）
        repo = (item.get("repo") or "").strip() or (last_repo if first else "")
        # 同理，last_out 是全局「最近输出目录」，只补给没有输出目录的首条
        out = (item.get("out") or "").strip() or (last_out if first else "")
        name = repo or f"组合 {len(profiles) + 1}"
        base, n = name, 2
        while name in seen_name:
            name = f"{base} ({n})"
            n += 1
        seen_name.add(name)
        raw = {
            "name": name,
            "repo": repo,
            "out": out,
            "token": tok,
            "incremental": incremental,
            "last_used": item.get("last_used") or "",
        }
        # v1 若已存在这些字段（如手工加过），一并继承，不要退回默认值
        for k in (
            "max_prs",
            "max_issues",
            "max_commits",
            "max_tags",
            "skip_details",
            "include_contents",
            "force",
        ):
            if k in data:
                raw[k] = data[k]
            elif k in item:
                raw[k] = item[k]
        profiles.append(_normalize_profile(raw))
    return {
        "version": CACHE_VERSION,
        "profiles": profiles,
        "active": profiles[0]["name"] if profiles else "",
    }


def load_cache() -> dict:
    """读取本机组合缓存；自动把旧版格式迁移为 v2（写回一次）。

    内容解析不出来时把原文件改名为 .json.bad 备份，不静默丢弃也不覆盖。
    """
    empty = {"version": CACHE_VERSION, "profiles": [], "active": ""}
    try:
        if TOKEN_CACHE_FILE.exists():
            data = json.loads(TOKEN_CACHE_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                if isinstance(data.get("profiles"), list):
                    profiles = [
                        _normalize_profile(p)
                        for p in data["profiles"]
                        if isinstance(p, dict)
                    ]
                    profiles = [p for p in profiles if p.get("name")]
                    return {
                        "version": CACHE_VERSION,
                        "profiles": profiles,
                        "active": str(data.get("active") or ""),
                    }
                if isinstance(data.get("tokens"), list):
                    # 旧版：迁移后立刻落盘，保证只迁一次
                    migrated = _migrate_v1(data)
                    save_cache(migrated)
                    return migrated
    except Exception:
        # JSON 损坏 / 读失败：留一份 .json.bad 再重新开始，避免把内容写没了
        _quarantine_cache()
    return empty


def save_cache(data: dict) -> bool:
    """原子写出：先写临时文件再替换，避免写一半断电留下坏 JSON。

    返回是否**真的写成功**。调用方必须据此决定是否提示「已保存」——
    旧实现内部 `except Exception: pass` 吞掉所有写入错误（exe 放在 Program Files、
    只读 U 盘、磁盘满、被杀软锁文件等），GUI 却照样打印「组合已保存」，
    用户以为参数已持久化，重启后全部丢失且全程没有任何错误提示。
    """
    try:
        tmp = TOKEN_CACHE_FILE.with_name(TOKEN_CACHE_FILE.name + ".tmp")
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(tmp, TOKEN_CACHE_FILE)
        return True
    except Exception:
        # 清理可能残留的临时文件，避免下次误读
        try:
            tmp.unlink()
        except Exception:
            pass
        return False


def _quarantine_cache() -> str:
    """缓存读不出来时备份原文件，避免后续写回把可恢复的内容直接覆盖掉。"""
    try:
        if not TOKEN_CACHE_FILE.exists():
            return ""
        backup = TOKEN_CACHE_FILE.with_name(TOKEN_CACHE_FILE.name + ".bad")
        os.replace(TOKEN_CACHE_FILE, backup)
        return backup.name
    except Exception:
        return ""


def list_profiles() -> list[dict]:
    return load_cache().get("profiles", [])


def get_profile(name: str) -> dict | None:
    name = (name or "").strip()
    for p in list_profiles():
        if p.get("name") == name:
            return p
    return None


def active_profile() -> dict | None:
    """上次使用的组合；active 失效时回落到最近使用的一条。"""
    data = load_cache()
    profiles = data.get("profiles", [])
    if not profiles:
        return None
    act = data.get("active") or ""
    for p in profiles:
        if p.get("name") == act:
            return p
    ordered = sorted(profiles, key=lambda p: p.get("last_used") or "", reverse=True)
    return ordered[0]


def upsert_profile(profile: dict, make_active: bool = True) -> dict:
    """按 name 覆盖或新增一条组合，返回规范化后的结果。"""
    p = _normalize_profile(profile, fallback_name=profile.get("name", ""))
    if not p["name"]:
        raise ValueError("组合名称不能为空")
    p["last_used"] = p.get("last_used") or _now_iso()
    data = load_cache()
    profiles = data.get("profiles", [])
    replaced = False
    for i, old in enumerate(profiles):
        if old.get("name") == p["name"]:
            profiles[i] = p
            replaced = True
            break
    if not replaced:
        if len(profiles) >= MAX_PROFILES:
            raise ValueError(f"组合最多 {MAX_PROFILES} 个，请先删除不用的")
        profiles.append(p)
    data["profiles"] = profiles
    data["version"] = CACHE_VERSION
    if make_active:
        data["active"] = p["name"]
    if not save_cache(data):
        # 写盘失败必须让调用方知道，否则 GUI 会打印「已保存」的假成功日志
        raise OSError(f"组合缓存写入失败：{TOKEN_CACHE_FILE}")
    return p


def delete_profile(name: str) -> None:
    name = (name or "").strip()
    data = load_cache()
    data["profiles"] = [p for p in data.get("profiles", []) if p.get("name") != name]
    if data.get("active") == name:
        # 清空 active，交由 active_profile() 按 last_used 回落到最近使用的组合
        data["active"] = ""
    save_cache(data)


def rename_profile(old_name: str, new_name: str) -> None:
    new_name = (new_name or "").strip()
    if not new_name:
        raise ValueError("组合名称不能为空")
    data = load_cache()
    profiles = data.get("profiles", [])
    if any(p.get("name") == new_name for p in profiles if p.get("name") != old_name):
        raise ValueError(f"组合「{new_name}」已存在")
    hit = False
    for p in profiles:
        if p.get("name") == old_name:
            p["name"] = new_name
            hit = True
    if not hit:
        raise ValueError("该组合不存在")
    if data.get("active") == old_name:
        data["active"] = new_name
    save_cache(data)


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
        self._profile_names: dict[str, str] = {}
        self._token_plain: str = ""
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
            background=[("active", "#eaeef2"), ("disabled", bg)],
            foreground=[("disabled", "#8c959f")],
            bordercolor=[("disabled", border_muted)],
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
            bordercolor=[("disabled", "#8c959f")],
        )
        style.configure("TCheckbutton", background=bg, foreground=ink, padding=(2, 4))
        style.map("TCheckbutton", background=[("active", bg)])
        # 方块勾选贴图（供 SquareCheck 使用）；_dim 版用于导出期间禁用态
        self._chk_off = self._make_chk_image(False)
        self._chk_on = self._make_chk_image(True)
        self._chk_off_dim = self._make_chk_image(False, dim=True)
        self._chk_on_dim = self._make_chk_image(True, dim=True)

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

    def _make_chk_image(self, checked: bool, dim: bool = False) -> tk.PhotoImage:
        """14x14 方块：空心 / 绿色实心，不用 ✕。

        dim=True 用于禁用态：勾选态换成中性灰（#8c959f），未勾选态换成更淡的
        边（#e4e8ec）。tk.Checkbutton 的自绘贴图不会随 state="disabled" 变化，
        没有灰版的话锁住了也看不出来。
        """
        img = tk.PhotoImage(width=14, height=14)
        if checked:
            # 绿色实心 + 深一点边
            outer, inner = ("#8c959f", "#a8b1ba") if dim else ("#1a7f37", "#1f883d")
        else:
            outer, inner = ("#e4e8ec", "#ffffff") if dim else ("#d0d7de", "#ffffff")
        img.put(outer, to=(0, 0, 14, 14))
        img.put(inner, to=(1, 1, 13, 13))
        return img

    def _square_check(self, parent, text: str, variable: tk.BooleanVar, command=None) -> tk.Checkbutton:
        """白底、方块指示器，勾选时为填色方。"""
        def _paint():
            """按「是否勾选」+「是否禁用」选贴图；不用 selectimage，
            否则 Tk 会自动换成绿版，盖掉禁用态的灰版。"""
            try:
                if getattr(widget, "_dim", False):
                    img = self._chk_on_dim if variable.get() else self._chk_off_dim
                else:
                    img = self._chk_on if variable.get() else self._chk_off
                widget.configure(image=img)
            except tk.TclError:
                pass  # 控件已销毁（退出过程中变量仍在变）

        def _sync():
            _paint()
            if command:
                command()

        widget = tk.Checkbutton(
            parent,
            text=text,
            variable=variable,
            command=_sync,
            # 不用 selectimage：它会覆盖禁用态的灰版贴图
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
            # 禁用时文字变灰：tk.Checkbutton 不支持 style.map，
            # 不设 disabledforeground 的话锁住了却看不出来
            disabledforeground="#8c959f",
            anchor="w",
            font=("Segoe UI", 10),
        )
        widget._dim = False
        widget._paint = _paint
        _paint()
        # 变量被程序改动时（如切换组合 _apply_profile）也要重画指示器
        variable.trace_add("write", lambda *_: _paint())
        return widget

    def _set_check_state(self, widget: tk.Checkbutton, enabled: bool) -> None:
        """禁用/启用自绘方块勾选：除 state 外还要换灰版方块贴图。"""
        widget.configure(state="normal" if enabled else "disabled")
        widget._dim = not enabled
        widget._paint()

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

    # ---------- UI ----------
    def _build_ui(self):
        outer = ttk.Frame(self, padding=(28, 24, 28, 20))
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        # 仅日志行可伸展
        outer.rowconfigure(10, weight=1)

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

        # 组合（仓库 + 输出目录 + Token + 选项 一起存、一起切）
        prof = ttk.Frame(outer)
        prof.grid(row=1, column=0, sticky="ew", pady=(0, 18))
        prof.columnconfigure(1, weight=1)
        ttk.Label(prof, text="组合", width=12, anchor="e").grid(row=0, column=0, sticky="e", padx=(0, 12))
        self.profile_var = tk.StringVar(value="")
        self.profile_combo = ttk.Combobox(
            prof, textvariable=self.profile_var, state="readonly"
        )
        self.profile_combo.grid(row=0, column=1, sticky="ew", padx=(0, 10))
        self.profile_combo.bind("<<ComboboxSelected>>", self._on_profile_selected)
        pbtns = ttk.Frame(prof)
        pbtns.grid(row=0, column=2, sticky="e")
        # 导出期间要禁用这些按钮：否则用户中途「保存」会被工作线程的旧快照覆盖
        self.profile_btns = [
            ttk.Button(pbtns, text="保存", command=self._save_profile),
            ttk.Button(pbtns, text="另存为…", command=self._save_profile_as),
            ttk.Button(pbtns, text="重命名", command=self._rename_profile),
            ttk.Button(pbtns, text="删除", command=self._delete_profile),
        ]
        for i, b in enumerate(self.profile_btns):
            b.grid(row=0, column=i, padx=(0 if i == 0 else 8, 0))
        ttk.Label(
            prof,
            text="选一个组合即可带出仓库 / 输出目录 / Token / 数量上限与选项；改完点「保存」写回该组合",
            style="Muted.TLabel",
        ).grid(row=1, column=1, columnspan=2, sticky="w", pady=(8, 0))

        # 导出期间需要一并锁定的「导出参数」控件（避免改动被工作线程的旧快照吞掉）
        self.param_widgets: list = []

        # 仓库
        r = field_row(2, "仓库")
        self.repo_var = tk.StringVar(value="owner/repo")
        w = ttk.Entry(r, textvariable=self.repo_var)
        w.grid(row=0, column=1, sticky="ew")
        self.param_widgets.append(w)

        # 输出目录
        r = field_row(3, "输出目录")
        self.out_var = tk.StringVar(value="")
        w = ttk.Entry(r, textvariable=self.out_var)
        w.grid(row=0, column=1, sticky="ew", padx=(0, 10))
        self.param_widgets.append(w)
        w = ttk.Button(r, text="浏览…", command=self._pick_out)
        w.grid(row=0, column=2, sticky="e")
        self.param_widgets.append(w)

        # Token（明文输入；组合切换时自动带出，掩码显示由「显示明文」控制）
        r = field_row(4, "Token")
        self.token_var = tk.StringVar(value="")
        self.token_entry = ttk.Entry(r, textvariable=self.token_var)
        self.token_entry.grid(row=0, column=1, sticky="ew", padx=(0, 10))
        self.param_widgets.append(self.token_entry)
        self.clear_btn = ttk.Button(r, text="清除缓存", command=self._clear_token_cache)
        self.clear_btn.grid(row=0, column=2, sticky="e")

        # Token 附属选项
        tok_sub = ttk.Frame(outer)
        tok_sub.grid(row=5, column=0, sticky="ew", pady=(0, 18))
        ttk.Label(tok_sub, text="", width=12).grid(row=0, column=0)
        self.show_token = tk.BooleanVar(value=False)
        self.remember_var = tk.BooleanVar(value=True)
        w = self._square_check(
            tok_sub, "显示明文", self.show_token, command=self._toggle_token_visibility
        )
        w.grid(row=0, column=1, sticky="w", padx=(0, 20))
        self.param_widgets.append(w)
        w = self._square_check(tok_sub, "记住到本机缓存", self.remember_var)
        w.grid(row=0, column=2, sticky="w")
        self.param_widgets.append(w)

        # 分隔
        ttk.Separator(outer, orient="horizontal").grid(row=6, column=0, sticky="ew", pady=(4, 18))

        # 数量上限
        limits = ttk.LabelFrame(outer, text="  数量上限  ", padding=(16, 12))
        limits.grid(row=7, column=0, sticky="ew", pady=(0, 16))
        for i in range(8):
            limits.columnconfigure(i, weight=1)
        self.max_prs = tk.IntVar(value=80)
        self.max_issues = tk.IntVar(value=80)
        self.max_commits = tk.IntVar(value=100)
        self.max_tags = tk.IntVar(value=50)

        def spin(parent, label, var, col, to=10000):
            ttk.Label(parent, text=label).grid(row=0, column=col, sticky="e", padx=(0, 6))
            w = ttk.Spinbox(parent, from_=1, to=to, textvariable=var, width=7)
            w.grid(row=0, column=col + 1, sticky="w", padx=(0, 24))
            self.param_widgets.append(w)

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
        opts.grid(row=8, column=0, sticky="ew", pady=(0, 20))
        self.skip_details = tk.BooleanVar(value=False)
        self.include_contents = tk.BooleanVar(value=True)
        self.force_var = tk.BooleanVar(value=True)
        self.incremental_var = tk.BooleanVar(value=True)
        ttk.Label(opts, text="选项", width=12, anchor="e").grid(row=0, column=0, sticky="e", padx=(0, 12))
        box = ttk.Frame(opts)
        box.grid(row=0, column=1, sticky="w")
        w = self._square_check(box, "跳过 diff / 评论", self.skip_details)
        w.grid(row=0, column=0, sticky="w", padx=(0, 22))
        self.param_widgets.append(w)
        w = self._square_check(box, "缓存源码", self.include_contents)
        w.grid(row=0, column=1, sticky="w", padx=(0, 22))
        self.param_widgets.append(w)
        w = self._square_check(box, "覆盖输出目录", self.force_var)
        w.grid(row=0, column=2, sticky="w", padx=(0, 22))
        self.param_widgets.append(w)
        w = self._square_check(box, "增量导出", self.incremental_var)
        w.grid(row=0, column=3, sticky="w")
        self.param_widgets.append(w)

        # 操作（始终可见）
        actions = ttk.Frame(outer)
        actions.grid(row=9, column=0, sticky="ew", pady=(4, 14))
        # 从左到右：按钮 / 按钮 / 进度条 / 总任务进度 / 阶段位置 / 阶段内计数 / 说明文本。
        # 各列统一用 _GAP 作为列间距，保证视觉间隔均匀；进度条与所有计数列都定宽，
        # 文本长度变化时不会把进度条挤得左右移动。weight 只给最后的说明列。
        actions.columnconfigure(6, weight=1)
        self.run_btn = ttk.Button(actions, text="  开始导出  ", style="Accent.TButton", command=self._start)
        self.run_btn.grid(row=0, column=0, sticky="w", ipady=4)
        self.open_btn = ttk.Button(actions, text="  打开输出目录  ", command=self._open_out, state="disabled")
        self.open_btn.grid(row=0, column=1, sticky="w", padx=(_GAP, 0), ipady=4)
        self.progress = ttk.Progressbar(actions, mode="determinate", length=200, maximum=100)
        self.progress.grid(row=0, column=2, sticky="w", padx=(_GAP, 0))
        # 全局百分比
        self.progress_pct = ttk.Label(actions, text="0%", style="Muted.TLabel", width=5, anchor="w")
        self.progress_pct.grid(row=0, column=3, sticky="w", padx=(_GAP, 0))
        # 总任务进度：已完成阶段数 / 总阶段数
        self.progress_stages = ttk.Label(actions, text="0/0", style="Muted.TLabel", width=7, anchor="w")
        self.progress_stages.grid(row=0, column=4, sticky="w", padx=(_GAP, 0))
        # 当前阶段内的文件/条目计数
        self.progress_count = ttk.Label(actions, text="0/0", style="Muted.TLabel", width=11, anchor="w")
        self.progress_count.grid(row=0, column=5, sticky="w", padx=(_GAP, 0))
        # 进度说明语句：没任务时置空，不占位
        self.progress_label = ttk.Label(actions, text="", style="Muted.TLabel", anchor="w")
        self.progress_label.grid(row=0, column=6, sticky="ew", padx=(_GAP, 0))

        # 日志
        logwrap = ttk.Frame(outer, style="Card.TFrame")
        logwrap.grid(row=10, column=0, sticky="nsew", pady=(0, 14))
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
        self.status.grid(row=11, column=0, sticky="ew")

        self._append_log("Mirror_export")
        self._append_log("选组合（或直接填字段后「另存为…」）→ 开始导出 → 拷贝输出目录到内网")
        self._append_log("-" * 48)

    # ---------- 组合（仓库 + 输出目录 + Token + 选项） ----------
    def _apply_profile(self, p: dict) -> None:
        """把组合字段刷进控件。"""
        self.repo_var.set(p.get("repo") or "")
        self.out_var.set(p.get("out") or "")
        self.max_prs.set(int(p.get("max_prs", 80)))
        self.max_issues.set(int(p.get("max_issues", 80)))
        self.max_commits.set(int(p.get("max_commits", 100)))
        self.max_tags.set(int(p.get("max_tags", 50)))
        self.skip_details.set(bool(p.get("skip_details", False)))
        self.include_contents.set(bool(p.get("include_contents", True)))
        self.force_var.set(bool(p.get("force", True)))
        self.incremental_var.set(bool(p.get("incremental", True)))
        # Token：按「显示明文」决定显示掩码还是明文
        self._set_token_field(p.get("token") or "")

    def _set_token_field(self, token: str) -> None:
        """把 Token 写进输入框；掩码显示时用 mask_token，真值仍从组合读取。"""
        token = (token or "").strip()
        self._token_plain = token
        self.token_var.set(token if self.show_token.get() else (mask_token(token) if token else ""))

    def _capture_profile(self, name: str) -> dict:
        """把当前控件值收成一个组合。"""
        return {
            "name": name,
            "repo": self.repo_var.get().strip().strip("/"),
            "out": self.out_var.get().strip(),
            "token": self._resolve_token(self.token_var.get()),
            "max_prs": int(self.max_prs.get()),
            "max_issues": int(self.max_issues.get()),
            "max_commits": int(self.max_commits.get()),
            "max_tags": int(self.max_tags.get()),
            "skip_details": bool(self.skip_details.get()),
            "include_contents": bool(self.include_contents.get()),
            "force": bool(self.force_var.get()),
            "incremental": bool(self.incremental_var.get()),
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
        labels = []
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
        self.profile_combo.configure(values=labels)
        selected = ""
        if keep_current:
            for label, name in self._profile_names.items():
                if name == keep_current:
                    self.profile_var.set(label)
                    selected = name
                    break
        if not selected:
            # 没有指定保留项（如删除后）：跟随 active_profile() 的最近使用回落，
            # 与下次启动加载的组合保持一致，而不是简单取文件里第一条
            act = active_profile() or {}
            act_name = act.get("name") or ""
            for label, name in self._profile_names.items():
                if name == act_name:
                    self.profile_var.set(label)
                    selected = name
                    break
        if not selected:
            self.profile_var.set(labels[0] if labels else "")
            selected = self._profile_names.get(self.profile_var.get(), "")
        if apply:
            p = get_profile(selected) if selected else None
            if p:
                self._apply_profile(p)
                self._append_log(f"已切换到组合「{selected}」")
            else:
                # 一个组合都不剩：回到内置默认值
                self._apply_profile(dict(DEFAULT_PROFILE, name=""))

    def _current_profile_name(self) -> str:
        return self._profile_names.get(self.profile_var.get(), "")

    def _on_profile_selected(self, _event=None):
        name = self._current_profile_name()
        if not name:
            return
        p = get_profile(name)
        if not p:
            return
        self._apply_profile(p)
        self._append_log(f"已切换到组合「{name}」→ {p.get('repo') or '(未设仓库)'}")

    def _save_profile(self):
        name = self._current_profile_name()
        if not name:
            self._save_profile_as()
            return
        try:
            upsert_profile(self._profile_for_save(name))
        except Exception as e:
            messagebox.showwarning("保存失败", str(e))
            return
        self._refresh_profile_choices(keep_current=name)
        self._append_log(f"已保存组合「{name}」")

    def _save_profile_as(self):
        name = simpledialog.askstring("另存为组合", "组合名称：", parent=self)
        if name is None:
            return
        name = name.strip()
        if not name:
            return
        if get_profile(name):
            if not messagebox.askyesno("确认", f"组合「{name}」已存在，覆盖？"):
                return
        elif not self.repo_var.get().strip():
            messagebox.showwarning("参数不完整", "仓库格式应为 owner/name，例如 octocat/hello-world")
            return
        try:
            upsert_profile(self._profile_for_save(name))
        except Exception as e:
            messagebox.showwarning("保存失败", str(e))
            return
        self._refresh_profile_choices(keep_current=name)
        self._append_log(f"已新建组合「{name}」")

    def _rename_profile(self):
        name = self._current_profile_name()
        if not name:
            return
        new = simpledialog.askstring("重命名组合", "新名称：", initialvalue=name, parent=self)
        if new is None or not new.strip() or new.strip() == name:
            return
        try:
            rename_profile(name, new.strip())
        except Exception as e:
            messagebox.showwarning("重命名失败", str(e))
            return
        self._refresh_profile_choices(keep_current=new.strip(), apply=True)
        self._append_log(f"组合「{name}」已重命名为「{new.strip()}」")

    def _delete_profile(self):
        name = self._current_profile_name()
        if not name:
            return
        if not messagebox.askyesno("确认", f"删除组合「{name}」？\n（该组合保存的 Token 也会一并删除）"):
            return
        delete_profile(name)
        self._refresh_profile_choices(apply=True)
        self._append_log(f"已删除组合「{name}」")

    def _load_defaults(self):
        profiles = list_profiles()
        p = active_profile()
        if p:
            self._apply_profile(p)
        self._refresh_profile_choices(keep_current=p.get("name") if p else "")
        if not p:
            # 全新使用：没有组合时用环境变量里的 Token 打个底
            env = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
            if env:
                self._set_token_field(env)
        if profiles:
            self._append_log(f"已加载组合「{p.get('name')}」（共 {len(profiles)} 个）")
        out = (p or {}).get("out") or ""
        if out and Path(out).exists():
            self._append_log(f"上次输出目录: {out}")
            self._append_log("默认增量导出：只拉有变化的 Commit / PR / Issue / README")

    def _toggle_token_visibility(self):
        """切换 Token 输入框的掩码/明文显示。"""
        cur = self.token_var.get().strip()
        plain = (self._token_plain or "").strip()
        # 输入框内容与掩码不同 → 用户手改过，以输入框为准
        if plain and cur != mask_token(plain):
            plain = cur
        self._token_plain = plain
        self.token_var.set(plain if self.show_token.get() else (mask_token(plain) if plain else ""))

    def _clear_token_cache(self):
        if not messagebox.askyesno(
            "确认", "清除本机全部组合缓存？\n（仓库、输出目录、Token 与各选项都会被清空）"
        ):
            return
        try:
            if TOKEN_CACHE_FILE.exists():
                TOKEN_CACHE_FILE.unlink()
        except Exception:
            pass
        self._token_plain = ""
        self.token_var.set("")
        self._profile_names = {}
        self.profile_var.set("")
        self.profile_combo.configure(values=[])
        self._append_log("已清除本机组合缓存")

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
        note = str(info.get("note") or "")
        # 全局加权百分比由 exporter 算好；缺失时退回阶段内百分比（兼容旧 exporter）
        overall = info.get("overall")
        stage_idx = int(info.get("stage_idx") or 0)
        stage_n = int(info.get("stage_n") or 0)
        if overall is None:
            overall = int(done * 100 / total) if total else 0
        try:
            overall = max(0, min(100, int(overall)))
            self.progress.configure(mode="determinate", maximum=100, value=overall)
        except Exception:
            pass
        # 四段信息各自独立成列，从左到右：
        #   百分比 | 总任务进度(已完成阶段/总阶段) | 阶段内计数(done/total) | 说明文本
        # 无任务时统一显示 0/0、0% 占位，说明文本留空不占位。
        self.progress_pct.configure(text=f"{overall}%")
        if stage_idx and stage_n:
            # 阶段进行中：最后一段阶段视为已完成，进度条走满时同步到 N/N
            finished = stage_idx if (total and done >= total) else stage_idx - 1
            finished = max(0, min(stage_n, finished))
            self.progress_stages.configure(text=f"{finished}/{stage_n}")
        else:
            self.progress_stages.configure(text="0/0")
        self.progress_count.configure(text=f"{done}/{total}" if total else "0/0")
        if note:
            note = note if len(note) <= 60 else note[:57] + "..."
            detail = f" · {note}"
        else:
            detail = ""
        self.progress_label.configure(text=f"{stage}{detail}" if stage else "")
        self.status.configure(
            text=f"{stage} {done}/{total}（{overall}%）" if total else f"{stage}（{overall}%）"
        )

    def _poll(self):
        try:
            while True:
                line = self.q.get_nowait()
                if line == "__DONE__":
                    self.progress.stop()
                    self.progress.configure(mode="determinate", value=100)
                    self.progress_pct.configure(text="100%")
                    self.progress_label.configure(text="导出完成")
                    self.run_btn.configure(state="normal", text="开始导出")
                    self.open_btn.configure(state="normal")
                    self._set_profile_btns_state(True)
                    # 导出期间组合按钮被禁用，不可能有并发保存，这里再刷一次下拉
                    self._refresh_profile_choices(keep_current=self._current_profile_name())
                    self.status.configure(text="导出完成")
                    self.bell()
                elif line == "__FAIL__":
                    self.progress.stop()
                    self.run_btn.configure(state="normal", text="开始导出")
                    # 失败时也要放开「打开输出目录」：输出目录里往往已有部分产物，
                    # 用户需要进去看日志/半成品；此前只有成功分支恢复它，失败后一直灰着。
                    self.open_btn.configure(state="normal")
                    self._set_profile_btns_state(True)
                    # 保留进度条当前位置，便于定位卡在哪一步
                    self.progress_label.configure(text="失败（进度停在出错处）")
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

    def _set_profile_btns_state(self, enabled: bool) -> None:
        """导出期间锁定组合区与全部导出参数控件。

        工作线程在启动时拍下快照、结束后把快照写回磁盘，因此导出中任何被改动的
        控件，其改动都会在写回时被静默吞掉（界面显示新值、缓存里仍是旧值）。
        这里统一锁住：组合下拉、四个组合按钮、「清除缓存」，以及所有导出参数字段。
        """
        state = "normal" if enabled else "disabled"
        for b in getattr(self, "profile_btns", []):
            try:
                b.configure(state=state)
            except Exception:
                pass
        # 下拉在导出期间保持只读但不可展开
        try:
            self.profile_combo.configure(state="readonly" if enabled else "disabled")
        except Exception:
            pass
        # 「清除缓存」会删掉缓存文件，导出中同样要锁住
        try:
            self.clear_btn.configure(state=state)
        except Exception:
            pass
        # 仓库 / 输出目录 / 浏览… / Token / 显示明文 / 记住到本机缓存
        # / 四个数量上限 / 四个选项开关
        # 自绘方块勾选（tk.Checkbutton）没有 style.map，需要额外换灰版贴图，
        # 否则文字和方块都不变，锁住了却看着像还能点。
        for w in getattr(self, "param_widgets", []):
            try:
                if getattr(w, "_paint", None) is not None:
                    self._set_check_state(w, enabled)
                else:
                    w.configure(state=state)
            except Exception:
                pass

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
        self._set_profile_btns_state(False)
        try:
            self.progress.configure(mode="determinate", maximum=100, value=0)
        except Exception:
            self.progress.start(12)
        # 清掉上一轮的残留值，四段信息各自归零占位
        self.progress_pct.configure(text="0%")
        self.progress_stages.configure(text="0/0")
        self.progress_count.configure(text="0/0")
        self.progress_label.configure(text="启动…")
        self.status.configure(text="正在导出…")
        self._append_log(f"开始导出 {args.repo} → {args.out}")
        # 导出前先记下当前控件对应的组合名与快照（工作线程不再碰 Tk）。
        # Token 解析为空时不取快照：既没得存，也避免把已存的 Token 覆盖成空串。
        profile_name = self._current_profile_name()
        snapshot = self._profile_for_save(profile_name) if profile_name else None
        if snapshot and not (snapshot.get("token") or "").strip():
            snapshot = None
        remember = bool(self.remember_var.get())

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


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
