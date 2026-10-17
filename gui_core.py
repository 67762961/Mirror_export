#!/usr/bin/env python3
"""Mirror_export · 与界面无关的公共逻辑。

这一层**不 import 任何 GUI 库**：组合缓存（读写 / 原子写 / v1 迁移 / 类型防御）、
日志队列、`__PROGRESS__` 机器行解析都集中在这里，供外网导出工具的界面复用。

为什么单独成文件：这些语义是历史上踩过坑的地方（写盘失败不能报「已保存」、
缓存损坏要留 .bad、进度分子必须是「已处理条数」等）。换界面实现时若把它们
留在窗口类里，很容易在重写中丢掉其中一条；集中一处后，逻辑单点、可脱离界面单测。
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

CACHE_VERSION = 2
MAX_PROFILES = 20

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

DEFAULT_PROFILE: Dict[str, Any] = {
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


def app_root() -> Path:
    """程序资源根目录：打包后是 _MEIPASS，源码运行是项目目录。"""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent


def exe_dir() -> Path:
    """程序所在目录：打包后是 exe 同目录，源码运行是项目目录。

    组合缓存（`gh_token_cache.json`）就落在这里，**永远不进导出包**。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


# 本机组合缓存文件（仓库 + 输出目录 + Token + 数量上限 + 选项）
TOKEN_CACHE_FILE = exe_dir() / "gh_token_cache.json"


def mask_token(token: str) -> str:
    """Token 的掩码显示形式（真值始终另存，掩码只用于界面）。"""
    token = token or ""
    if len(token) <= 12:
        return token[:4] + "••••" if token else ""
    return f"{token[:6]}…{token[-4:]}"


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_profile(raw: dict, fallback_name: str = "") -> dict:
    """把任意来源的字典补齐成完整组合，缺失字段取默认值。

    对**所有**字段都做类型防御：缓存文件可能被手工编辑，任何一个字段类型不对
    都不应让整份缓存读不出来（load_cache 的 except 会把整个文件隔离成 .bad）。
    """
    p = dict(DEFAULT_PROFILE)
    # name 同样要 str() 兜底：直接 .strip() 在 name 是数字/对象时抛 AttributeError，
    # 会让 load_cache 把整份缓存隔离掉，用户看到「0 个组合」且不知原因。
    name = raw.get("name")
    if name is None or name == "":
        name = fallback_name
    p["name"] = str(name or "").strip()
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
    try:
        p["last_used"] = str(raw.get("last_used") or "")
    except Exception:
        p["last_used"] = ""
    return p


def migrate_v1(data: dict) -> dict:
    """v1（tokens 列表 + 全局 last_repo/last_out）→ v2 profiles。

    旧文件不删除，只做就地升级；每条 Token 合成一个组合，
    首个 Token 继承全局的 last_repo / last_out。
    """
    profiles: List[dict] = []
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
        # 注意：用已生成条数判断「首条」，不能用原始下标
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
        profiles.append(normalize_profile(raw))
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
                        normalize_profile(p)
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
                    migrated = migrate_v1(data)
                    save_cache(migrated)
                    return migrated
    except Exception:
        # JSON 损坏 / 读失败：留一份 .json.bad 再重新开始，避免把内容写没了
        quarantine_cache()
    return empty


def save_cache(data: dict) -> bool:
    """原子写出：先写临时文件再替换，避免写一半断电留下坏 JSON。

    返回是否**真的写成功**。调用方必须据此决定是否提示「已保存」——
    旧实现内部 `except Exception: pass` 吞掉所有写入错误（exe 放在 Program Files、
    只读 U 盘、磁盘满、被杀软锁文件等），GUI 却照样打印「组合已保存」，
    用户以为参数已持久化，重启后全部丢失且全程没有任何错误提示。
    """
    tmp = TOKEN_CACHE_FILE.with_name(TOKEN_CACHE_FILE.name + ".tmp")
    try:
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


def quarantine_cache() -> str:
    """缓存读不出来时备份原文件，避免后续写回把可恢复的内容直接覆盖掉。"""
    try:
        if not TOKEN_CACHE_FILE.exists():
            return ""
        backup = TOKEN_CACHE_FILE.with_name(TOKEN_CACHE_FILE.name + ".bad")
        os.replace(TOKEN_CACHE_FILE, backup)
        return backup.name
    except Exception:
        return ""


def list_profiles() -> List[dict]:
    return load_cache().get("profiles", [])


def get_profile(name: str) -> Optional[dict]:
    name = (name or "").strip()
    for p in list_profiles():
        if p.get("name") == name:
            return p
    return None


def active_profile() -> Optional[dict]:
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
    p = normalize_profile(profile, fallback_name=profile.get("name", ""))
    if not p["name"]:
        raise ValueError("组合名称不能为空")
    p["last_used"] = p.get("last_used") or now_iso()
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
        # 写盘失败必须让调用方知道，否则界面会打印「已保存」的假成功日志
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
    """把 print 重定向到队列，供界面消费（导出线程里替换 sys.stdout/stderr）。"""

    def __init__(self, q):
        self.q = q

    def write(self, s: str):
        s = (s or "").rstrip("\n")
        if s:
            self.q.put(s)

    def flush(self):
        pass


# ---------- 进度协议（__PROGRESS__{json}） ----------

def parse_progress(payload: str) -> Optional[dict]:
    """把 `__PROGRESS__` 后面的 JSON 解析成界面要用的四段信息。

    口径与导出侧约定一致（ARCHITECTURE「进度协议」）：
    - `overall` 由 exporter 算好；缺字段的旧 exporter 退回 `done/total`；
    - 「总任务进度」= 已完成阶段数 / 本轮总阶段数，正在执行的阶段算未完成，
      故阶段开始时显示 `idx-1/N`、该阶段跑满后显示 `idx/N`；
    - 说明文本截断到 60 字符。
    """
    try:
        info = json.loads(payload)
    except Exception:
        return None
    if not isinstance(info, dict):
        return None

    stage = str(info.get("stage") or "")
    done = int(info.get("done") or 0)
    total = int(info.get("total") or 0)
    note = str(info.get("note") or "")
    overall = info.get("overall")
    stage_idx = int(info.get("stage_idx") or 0)
    stage_n = int(info.get("stage_n") or 0)

    # 全局加权百分比由 exporter 算好；缺失时退回阶段内百分比（兼容旧 exporter）
    if overall is None:
        overall = int(done * 100 / total) if total else 0
    try:
        overall = max(0, min(100, int(overall)))
    except Exception:
        overall = 0

    if stage_idx and stage_n:
        finished = stage_idx if (total and done >= total) else stage_idx - 1
        finished = max(0, min(stage_n, finished))
        stages_text = f"{finished}/{stage_n}"
    else:
        stages_text = "0/0"

    if note:
        note = note if len(note) <= 60 else note[:57] + "..."

    return {
        "stage": stage,
        "done": done,
        "total": total,
        "overall": overall,
        "pct_text": f"{overall}%",
        "stages_text": stages_text,
        "count_text": f"{done}/{total}" if total else "0/0",
        "detail_text": f"{stage} · {note}" if note else stage,
        "status_text": (
            f"{stage} {done}/{total}（{overall}%）" if total else f"{stage}（{overall}%）"
        ),
    }
