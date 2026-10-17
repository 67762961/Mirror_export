#!/usr/bin/env python3
"""
GitHub 只读镜像导出器
在外网机器上运行：拉取 PR / Issue / Commit / Tag / 文件树，生成可拷贝到内网的静态站点。

用法（外网）:
  1. 安装 Python 3.8+（无需 pip 依赖）
  2. 设置 Token:
       Windows PowerShell:  $env:GITHUB_TOKEN = "ghp_xxx"
       CMD:                 set GITHUB_TOKEN=ghp_xxx
     Token 需要 repo 权限（私有库）或 public_repo（公开库）
  3. 运行:
       python export.py
       python export.py --repo owner/name
       python export.py --repo owner/name --out gh-mirror
  4. 把输出目录整个拷到 U 盘，进内网后双击 index.html
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

API = "https://api.github.com"
MAX_RETRIES = 3
BACKOFF = 2.0

# release compare 单轮最多保留多少条 commit（与分支 compare 的裁剪口径保持独立可调）
RELEASE_COMPARE_MAX_COMMITS = 100

BINARY_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp", ".tif", ".tiff",
    ".pdf", ".zip", ".7z", ".rar", ".tar", ".gz", ".bz2", ".xz",
    ".exe", ".dll", ".so", ".dylib", ".bin", ".dat", ".mat", ".fig",
    ".mp3", ".mp4", ".wav", ".avi", ".mov", ".wmv",
    ".xlsx", ".xls", ".xlsm", ".docx", ".doc", ".pptx", ".ppt",
    ".ttf", ".otf", ".woff", ".woff2", ".eot",
    ".class", ".jar", ".pyc", ".pyo", ".o", ".a", ".lib",
}

SKIP_DIR_PREFIX = (".git/", "node_modules/", ".venv/", "__pycache__/")


def log(msg: str) -> None:
    print(msg, flush=True)


# --- 阶段表：顺序即执行顺序，权重只影响进度条平滑度，不影响导出行为 ---
# 新增阶段时必须在此登记，否则进度条会缺失该段（见 AGENTS.md）。
STAGES = [
    ("仓库信息", 1),
    ("Tags/Releases", 2),
    ("Release 对比", 3),
    ("Commits", 4),
    ("Commit diff", 12),
    ("Pull Requests", 10),
    ("PR 详情", 18),
    ("Issues", 6),
    ("Issue 关联", 2),
    ("文件树", 3),
    ("文件内容", 20),
    ("仓库图片", 12),
    ("正文图片", 5),
    ("Branches", 1),
    ("分支对比", 3),
    ("分支 diff", 3),
    ("写出数据", 1),
]

_STAGE_ORDER = [name for name, _ in STAGES]
_STAGE_WEIGHT = dict(STAGES)
_STAGE_INDEX = {name: i for i, name in enumerate(_STAGE_ORDER, 1)}
# 全局进度按「本轮实际会执行的阶段」折算：跳过某阶段（--no-assets / --skip-details）时，
# 其权重在分子与分母中一并剔除，保证最终仍能到 100%，且不会因分母变动而忽高忽低。
_STAGE_DECLARED = {"max_idx": 0}
# 本轮实际出现过的阶段名，用于判断哪些阶段被跳过
_SEEN_STAGES: set = set()
# 单调保护：全局百分比只增不减，阶段切换时不会倒退
_LAST_OVERALL = {"value": 0}


def _mark_stage(stage: str) -> None:
    """记录阶段已执行：用于判断跳过项，并维护实际执行到的最大下标。"""
    if stage in _STAGE_WEIGHT:
        _SEEN_STAGES.add(stage)
    idx = _STAGE_INDEX.get(stage, 0)
    if idx > _STAGE_DECLARED["max_idx"]:
        _STAGE_DECLARED["max_idx"] = idx


def reset_progress() -> None:
    """重置本轮的进度状态，必须在每轮 export() 开头调用一次。

    这三个状态是模块级全局，只在进程启动时初始化一次。GUI 里切换组合后再点导出，
    是在**同一个进程**内第二次调用 export()，若不清零：
    - `_LAST_OVERALL` 仍是上一轮的 100，单调保护 max() 会把新一轮首帧直接顶到 100%，
      表现为「进度条从 0 一下冲到 100，导出其实在正常跑」；
    - `_SEEN_STAGES` / `_STAGE_DECLARED` 仍是上一轮的值，跳过阶段（--no-assets /
      --skip-details）的折算失效，`stage_n` 与实际执行阶段数对不上。
    """
    _SEEN_STAGES.clear()
    _STAGE_DECLARED["max_idx"] = 0
    _LAST_OVERALL["value"] = 0


def progress(stage: str, done: int, total: int, note: str = "") -> None:
    """人类可读进度 + 供 GUI 解析的机器行。

    ``overall`` 为全局加权百分比（0-100），由本函数统一算好，GUI 直接使用，
    保证整个导出过程单调递增、阶段切换不倒退。
    """
    _mark_stage(stage)
    total = max(0, int(total or 0))
    done = max(0, int(done or 0))
    done = min(done, total) if total else done
    pct = int(done * 100 / total) if total else 0

    # 全局进度：把「实际会执行的阶段」重新编号，权重按比例缩放后再累加。
    # 关键点：分子与分母必须用同一套权重口径 —— 跳过阶段的权重既要从分母扣除，
    # 也不能再出现在 before 里，否则后面阶段的百分比会被挤到 100% 附近。
    max_idx = _STAGE_DECLARED["max_idx"]
    idx = _STAGE_INDEX.get(stage, 0)
    # 视为跳过的阶段：出现在当前阶段之前、但本轮从未出现过的阶段
    skipped_names = {
        n for i, n in enumerate(_STAGE_ORDER, 1)
        if i < max_idx and n not in _SEEN_STAGES
    }
    live_weight = {n: w for n, w in _STAGE_WEIGHT.items() if n not in skipped_names}
    denom = max(1, sum(live_weight.values()))
    before = sum(live_weight[n] for n in _STAGE_ORDER[: idx - 1] if n in live_weight) if idx else 0
    stage_w = live_weight.get(stage, 0)
    raw = int((before + stage_w * (done / total if total else 0)) * 100 / denom)
    # 单调保护：分母变化或阶段切换都可能让 raw 回落，这里取历史最大值
    overall = max(_LAST_OVERALL["value"], max(0, min(100, raw)))
    _LAST_OVERALL["value"] = overall

    bar = f"[{done}/{total} {pct:3d}%]"
    suffix = f" · {note}" if note else ""
    log(f"  {stage} {bar}{suffix}")
    # 显示用序号：跳过阶段不占编号，避免出现 [7/15] 却只有 14 个阶段在跑
    live_order = [n for n in _STAGE_ORDER if n in live_weight]
    shown_idx = (live_order.index(stage) + 1) if stage in live_weight else idx
    payload = {
        "stage": stage,
        "done": done,
        "total": total,
        "note": note,
        "stage_idx": shown_idx,
        "stage_n": len(live_weight),
        "overall": overall,
    }
    print("__PROGRESS__" + json.dumps(payload, ensure_ascii=False), flush=True)


def stage_begin(stage: str, total: int = 0) -> None:
    """标记阶段开始（total=0 表示总数暂未知），让进度条至少显示当前阶段名。"""
    progress(stage, 0, total)


def make_throttle(step_pct: int = 1):
    """返回「每跨过 step_pct% 才发一次」的节流判定函数，用于高频循环。

    节流状态放在闭包里，progress() 保持无状态，便于测试。
    ``step_pct`` 必须真正生效：按「跨过的档位」判定（档位 = pct // step_pct），
    否则传 5 / 50 会静默退化为 1% 刷屏。阶段首尾（done<=0 / done>=total）始终放行。
    """
    step = max(1, int(step_pct or 1))

    def should(stage: str, done: int, total: int) -> bool:
        if done <= 0 or done >= total:
            return True
        pct = int(done * 100 / total) if total else 0
        slot = pct // step            # 跨过的 step 档位号（step=1 时等价于 pct）
        if slot != should.last_slot:
            should.last_slot = slot
            return True
        return False

    should.last_slot = -1
    return should


def short_sha(sha: str | None) -> str:
    return (sha or "")[:7]


def http_get(url: str, token: str | None, accept: str = "application/vnd.github+json"):
    headers = {
        "Accept": accept,
        "User-Agent": "Mirror_export/1.0",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    last_err = None
    for attempt in range(MAX_RETRIES):
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                remaining = resp.headers.get("X-RateLimit-Remaining")
                if remaining is not None and int(remaining) < 5:
                    reset = int(resp.headers.get("X-RateLimit-Reset", "0") or 0)
                    wait = max(0, reset - int(time.time())) + 1
                    if wait > 0:
                        log(f"  [rate-limit] 剩余 {remaining}，等待 {wait}s ...")
                        time.sleep(min(wait, 90))
                body = resp.read()
                ctype = resp.headers.get("Content-Type", "")
                if "application/json" in ctype:
                    return json.loads(body.decode("utf-8")), dict(resp.headers)
                return body, dict(resp.headers)
        except urllib.error.HTTPError as e:
            if e.code in (403, 429):
                reset = e.headers.get("X-RateLimit-Reset")
                if reset:
                    wait = max(0, int(reset) - int(time.time())) + 1
                    log(f"  [rate-limit] HTTP {e.code}，等待 {wait}s ...")
                    time.sleep(min(wait, 90))
                    continue
            if e.code >= 500:
                time.sleep(BACKOFF ** attempt)
                last_err = e
                continue
            detail = e.read().decode("utf-8", errors="replace")[:300]
            raise RuntimeError(f"HTTP {e.code} for {url}: {detail}") from e
        except urllib.error.URLError as e:
            time.sleep(BACKOFF ** attempt)
            last_err = e
    raise RuntimeError(f"请求失败 {url}: {last_err}")


def paginate(url: str, token: str | None, max_items: int, label: str):
    """max_items<=0 或 None → 一直翻页直到没有数据。"""
    unlimited = max_items is None or int(max_items) <= 0
    items = []
    page = 1
    sep = "&" if "?" in url else "?"
    while unlimited or len(items) < max_items:
        u = f"{url}{sep}per_page=100&page={page}"
        batch, _ = http_get(u, token)
        if not batch:
            break
        if not isinstance(batch, list):
            items.append(batch)
            break
        items.extend(batch)
        log(f"  {label}: 已获取 {len(items)}")
        if len(batch) < 100:
            break
        page += 1
        # 防御：单次最多 10000 条，避免异常翻页
        if page > 100:
            log(f"  [warn] {label}: 已达 10000 条保护上限，停止翻页")
            break
    if unlimited:
        return items
    return items[:max_items]


def paginate_skipping(url: str, token: str | None, max_items: int, label: str, skip=None):
    """按页拉取，**只统计被保留的条目**，凑满 max_items 即停。

    与 ``paginate`` 的区别：``paginate`` 的 ``max_items`` 统计的是原始条目数；
    这里 ``skip(item)`` 为真的条目会被丢弃且不计数。

    GitHub 的 ``/issues`` 接口会把 PR 也混在列表里，调用方需要按「真正的 issue 数」
    控制上限。旧实现用 ``max_issues * 2`` 这种倍数启发式，一旦 PR 占比超过一半就会
    取不够数；这里改为边翻页边过滤，凑满即停（``max_items<=0`` 表示翻到没有为止）。
    """
    unlimited = max_items is None or int(max_items) <= 0
    items = []
    page = 1
    sep = "&" if "?" in url else "?"
    while True:
        u = f"{url}{sep}per_page=100&page={page}"
        batch, _ = http_get(u, token)
        if not batch:
            break
        if not isinstance(batch, list):
            break
        for it in batch:
            if skip is not None and skip(it):
                continue
            items.append(it)
            if not unlimited and len(items) >= max_items:
                return items
        log(f"  {label}: 已获取 {len(items)}（原始 {page} 页）")
        if len(batch) < 100:
            break
        page += 1
        # 防御：单次最多 10000 条，避免异常翻页
        if page > 100:
            log(f"  [warn] {label}: 已达 10000 条保护上限，停止翻页")
            break
    return items


def slim_user(u: dict | None) -> dict:
    if not u:
        return {}
    return {
        "login": u.get("login"),
        "avatar_url": u.get("avatar_url"),
        "html_url": u.get("html_url"),
    }


def slim_label(l: dict) -> dict:
    return {"name": l.get("name"), "color": l.get("color") or "6e7681"}


def slim_milestone(m: dict | None):
    if not m:
        return None
    return {"title": m.get("title"), "state": m.get("state"), "due_on": m.get("due_on")}


def map_pr(item: dict, files: list | None = None, comments: list | None = None) -> dict:
    return {
        "number": item.get("number"),
        "title": item.get("title"),
        "state": item.get("state"),
        "draft": item.get("draft") or False,
        "merged_at": item.get("merged_at"),
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
        "closed_at": item.get("closed_at"),
        "user": slim_user(item.get("user")),
        "assignees": [slim_user(a) for a in (item.get("assignees") or [])],
        "labels": [slim_label(l) for l in (item.get("labels") or [])],
        "milestone": slim_milestone(item.get("milestone")),
        "comments": item.get("comments") or 0,
        "review_comments": item.get("review_comments") or 0,
        "commits": item.get("commits") or 0,
        "changed_files": item.get("changed_files") or 0,
        "additions": item.get("additions") or 0,
        "deletions": item.get("deletions") or 0,
        "body": item.get("body") or "",
        "html_url": item.get("html_url"),
        "head": {
            "ref": (item.get("head") or {}).get("ref"),
            "sha": (item.get("head") or {}).get("sha"),
            "label": (item.get("head") or {}).get("label"),
        },
        "base": {
            "ref": (item.get("base") or {}).get("ref"),
            "sha": (item.get("base") or {}).get("sha"),
            "label": (item.get("base") or {}).get("label"),
        },
        "merged": bool(item.get("merged_at")) or item.get("state") == "closed" and False,
        "files": files or [],
        "issue_comments": comments or [],
    }


def map_issue(item: dict, comments: list | None = None) -> dict:
    pr = item.get("pull_request") or {}
    return {
        "number": item.get("number"),
        "title": item.get("title"),
        "state": item.get("state"),
        "state_reason": item.get("state_reason"),
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
        "closed_at": item.get("closed_at"),
        "user": slim_user(item.get("user")),
        "assignees": [slim_user(a) for a in (item.get("assignees") or [])],
        "labels": [slim_label(l) for l in (item.get("labels") or [])],
        "milestone": slim_milestone(item.get("milestone")),
        "comments": item.get("comments") or 0,
        "body": item.get("body") or "",
        "html_url": item.get("html_url"),
        "is_pr": bool(pr.get("url")),
        "issue_comments": comments or [],
    }


def map_commit(item: dict, files: list | None = None) -> dict:
    c = item.get("commit") or {}
    author = c.get("author") or {}
    committer = c.get("committer") or {}
    return {
        "sha": item.get("sha"),
        "html_url": item.get("html_url"),
        "message": c.get("message") or "",
        "author_name": author.get("name"),
        "author_email": author.get("email"),
        "author_date": author.get("date"),
        "committer_name": committer.get("name"),
        "committer_date": committer.get("date"),
        "author": slim_user(item.get("author")),
        "committer": slim_user(item.get("committer")),
        "parents": [p.get("sha") for p in (item.get("parents") or [])],
        "verified": (c.get("verification") or {}).get("verified"),
        "files": files if files is not None else [],
        "stats": item.get("stats") or {},
        "has_diff": files is not None,
    }


def map_file(f: dict) -> dict:
    patch = f.get("patch") or ""
    # 截断超大 patch，避免 mirror 过大
    if len(patch) > 80_000:
        patch = patch[:80_000] + "\n... [patch 已截断] ..."
    return {
        "filename": f.get("filename"),
        "status": f.get("status"),
        "additions": f.get("additions") or 0,
        "deletions": f.get("deletions") or 0,
        "changes": f.get("changes") or 0,
        "sha": f.get("sha"),
        "patch": patch,
    }


def is_textish(path: str) -> bool:
    ext = Path(path).suffix.lower()
    if ext in BINARY_EXT:
        return False
    return True


def js_assign(name: str, payload) -> str:
    """生成 file:// 下可加载的 data/*.js"""
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    # 防止 </script> 注入
    body = body.replace("</", "<\\/")
    return f"window.GH_DATA=window.GH_DATA||{{}};window.GH_DATA.{name}={body};\n"


def parse_data_js(path: Path, name: str):
    """从 data/xxx.js 解析出 Python 对象（增量导出用）。"""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return None
    marker = f"window.GH_DATA.{name}="
    i = text.find(marker)
    if i < 0:
        return None
    body = text[i + len(marker):].strip()
    if body.endswith(";"):
        body = body[:-1].rstrip()
    body = body.replace("<\\/", "</")
    try:
        return json.loads(body)
    except Exception:
        return None


def load_previous_export(out: Path) -> dict:
    """读取已有导出包里的 data/*.js，用于增量合并。"""
    data_dir = out / "data"
    prev: dict = {}
    if not data_dir.is_dir():
        return prev
    for name in ("meta", "prs", "issues", "commits", "tags", "releases", "files", "branches", "graph"):
        p = data_dir / f"{name}.js"
        if p.exists():
            val = parse_data_js(p, name)
            if val is not None:
                prev[name] = val
    return prev


def load_asset_index(out: Path) -> dict:
    """assets/.index.json: path -> blob sha"""
    p = out / "assets" / ".index.json"
    try:
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def save_asset_index(out: Path, index: dict) -> None:
    try:
        p = out / "assets" / ".index.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(index, ensure_ascii=False, indent=0), encoding="utf-8")
    except Exception:
        pass


def download_repo_images(
    repo: str,
    token: str,
    tree: list,
    out: Path,
    max_bytes: int = 2_000_000,
    prev_index: dict | None = None,
    stage: str = "",
) -> tuple[int, int]:
    """把仓库里的图片落到 out/assets。返回 (新下载, 跳过复用)。

    ``stage`` 非空时按该阶段名上报进度；total 为筛选后的图片总数，
    命中复用的也计入 done，保证纯增量全命中时进度条照常走完。
    """
    assets_dir = out / "assets"
    assets_dir.mkdir(exist_ok=True)
    index = dict(prev_index or {})
    n_new = 0
    n_skip = 0
    # 先筛出候选，拿到分母
    candidates = []
    for t in tree:
        path = t.get("path") or ""
        if t.get("type") != "blob":
            continue
        if Path(path).suffix.lower() not in IMAGE_EXT or (t.get("size") or 0) > max_bytes:
            continue
        if any(path.startswith(p) for p in SKIP_DIR_PREFIX):
            continue
        candidates.append(t)
    if stage:
        stage_begin(stage, len(candidates))
    throttle = make_throttle(1)
    for i, t in enumerate(candidates, 1):
        path = t.get("path") or ""
        sha = t.get("sha") or ""
        rel = asset_rel(path)
        dst = assets_dir / rel
        if (
            index.get(path) == sha
            and dst.exists()
            and dst.stat().st_size > 0
        ):
            n_skip += 1
            if stage and throttle(stage, i, len(candidates)):
                progress(stage, i, len(candidates), path)
            continue
        try:
            raw, _ = http_get(t["url"], token, accept="application/vnd.github.raw")
            data = raw if isinstance(raw, bytes) else str(raw).encode("utf-8", errors="replace")
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(data)
            index[path] = sha
            n_new += 1
            if stage and throttle(stage, i, len(candidates)):
                progress(stage, i, len(candidates), path)
            elif not stage and n_new % 20 == 0:
                log(f"  图片资源: {n_new}")
        except Exception as e:
            log(f"  [warn] 图片 {path} 失败: {e}")
            if stage and throttle(stage, i, len(candidates)):
                progress(stage, i, len(candidates), path)
    save_asset_index(out, index)
    log(f"  图片资源: 新下 {n_new}, 复用 {n_skip}")
    return n_new, n_skip


# 正文外链图片（PR/Issue/Release）下载上限，与仓库树图片一致
BODY_IMAGE_MAX_BYTES = 2_000_000

# Content-Type → 扩展名（user-attachments 等 URL 往往无后缀）
CTYPE_EXT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
    "image/bmp": ".bmp",
    "image/x-icon": ".ico",
    "image/vnd.microsoft.icon": ".ico",
}

# 仅对这些域名附带 Token（禁止把 GitHub Token 发给第三方 CDN）
GITHUB_ASSET_HOSTS = (
    "github.com",
    "api.github.com",
    "raw.githubusercontent.com",
    "user-images.githubusercontent.com",
    "objects.githubusercontent.com",
    "githubusercontent.com",
    "githubassets.com",
)

MD_IMAGE_RE = re.compile(
    r"!\[[^\]]*\]\(\s*(?:<([^>]+)>|([^)\s]+))\s*\)",
    re.I,
)
HTML_IMG_SRC_RE = re.compile(
    r"<img\b[^>]*\bsrc\s*=\s*[\"']([^\"']+)[\"']",
    re.I,
)

# GitHub 粘贴附件：markdown 里是 github.com/user-attachments/assets/<uuid>
# 私有库对该直链即使带 PAT 也常返回 404，需经 GraphQL bodyHTML 解析成
# private-user-images.githubusercontent.com/...?jwt=... 再下载
USER_ATTACHMENTS_RE = re.compile(
    r"^https?://github\.com/user-attachments/assets/",
    re.I,
)
HTML_SRC_ANY_RE = re.compile(r"src=[\"'](https?://[^\"']+)[\"']", re.I)


def _is_github_asset_host(url: str) -> bool:
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in GITHUB_ASSET_HOSTS)


def is_user_attachments_url(url: str) -> bool:
    return bool(url) and bool(USER_ATTACHMENTS_RE.match(url.strip()))


def _url_match_keys(url: str) -> list[str]:
    """用于把 markdown 原始 URL 与 bodyHTML 解析后的 CDN URL 对齐。"""
    bare = (url or "").split("?")[0].split("#")[0].rstrip("/")
    last = bare.rsplit("/", 1)[-1].lower()
    if not last:
        return []
    keys = [last]
    stem = last.split(".")[0]
    if len(stem) >= 8:
        keys.append(stem)
    return keys


def _html_unescape(s: str) -> str:
    return (
        (s or "")
        .replace("&amp;", "&")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
    )


def graphql_post(token: str, query: str, variables: dict | None = None) -> dict:
    """调用 GitHub GraphQL；失败抛异常。"""
    payload: dict = {"query": query}
    if variables:
        payload["variables"] = variables
    data = json.dumps(payload).encode("utf-8")
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "Mirror_export/1.0",
        "Content-Type": "application/json",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{API}/graphql", data=data, headers=headers, method="POST")
    last_err = None
    for attempt in range(MAX_RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = resp.read()
                obj = json.loads(body.decode("utf-8"))
                if obj.get("errors"):
                    msgs = "; ".join(str(e.get("message") or e) for e in obj["errors"][:3])
                    raise RuntimeError(f"GraphQL errors: {msgs}")
                return obj
        except urllib.error.HTTPError as e:
            if e.code in (403, 429):
                reset = e.headers.get("X-RateLimit-Reset")
                if reset:
                    wait = max(0, int(reset) - int(time.time())) + 1
                    if wait > 0:
                        log(f"  [rate-limit] GraphQL HTTP {e.code}，等待 {wait}s ...")
                        time.sleep(min(wait, 90))
                        continue
            if e.code >= 500:
                time.sleep(BACKOFF ** attempt)
                last_err = e
                continue
            detail = e.read().decode("utf-8", errors="replace")[:300]
            raise RuntimeError(f"GraphQL HTTP {e.code}: {detail}") from e
        except urllib.error.URLError as e:
            time.sleep(BACKOFF ** attempt)
            last_err = e
    raise RuntimeError(f"GraphQL 请求失败: {last_err}")


def map_html_to_aliases(htmls: list, pending: set, aliases: dict) -> None:
    """用 bodyHTML 中的 <img src> 解析 pending 里的原始 URL → 可下载 CDN URL。"""
    if not pending:
        return
    srcs = []
    for html in htmls:
        if not html:
            continue
        for m in HTML_SRC_ANY_RE.finditer(html):
            src = _html_unescape(m.group(1))
            if src:
                srcs.append(src)
    if not srcs:
        return
    for orig in list(pending):
        keys = _url_match_keys(orig)
        for src in srcs:
            low = src.lower()
            if any(k in low for k in keys):
                aliases[orig] = src
                pending.discard(orig)
                break


def _batched(seq: list, n: int):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def resolve_attachment_aliases(repo: str, prs: list, issues: list, releases: list, token: str) -> dict:
    """GraphQL bodyHTML：把 user-attachments 等 404 直链解析成可下载 URL。

    返回 {markdown原始URL: 实际下载URL}。image_map 的键仍使用原始 URL。
    """
    aliases: dict[str, str] = {}
    if not token:
        return aliases
    owner, _, name = repo.partition("/")
    if not owner or not name:
        return aliases

    pending: set[str] = set()

    def texts_attachment_urls(texts: list) -> set:
        own = set()
        for t in texts:
            for u in extract_md_image_urls(t or ""):
                if is_user_attachments_url(u):
                    own.add(u)
        return own

    pr_need = []
    for p in prs or []:
        texts = [p.get("body") or ""] + [c.get("body") or "" for c in (p.get("issue_comments") or [])]
        own = texts_attachment_urls(texts)
        if own:
            pr_need.append(p.get("number"))
            pending |= own

    iss_need = []
    for i in issues or []:
        texts = [i.get("body") or ""] + [c.get("body") or "" for c in (i.get("issue_comments") or [])]
        own = texts_attachment_urls(texts)
        if own:
            iss_need.append(i.get("number"))
            pending |= own

    rel_need = []
    for r in releases or []:
        own = texts_attachment_urls([r.get("body") or ""])
        if own:
            tag = r.get("tag_name") or r.get("name") or ""
            if tag:
                rel_need.append(tag)
                pending |= own

    if not pending:
        return aliases
    log(f"  GraphQL 解析附件直链: pending={len(pending)}, prs={len(pr_need)}, issues={len(iss_need)}, releases={len(rel_need)}")

    # PR：bodyHTML + issue comments bodyHTML
    for batch in _batched([n for n in pr_need if n is not None], 8):
        parts = [
            f"p{n}: pullRequest(number: {int(n)}) "
            f"{{ bodyHTML comments(first: 100) {{ nodes {{ bodyHTML }} }} }}"
            for n in batch
        ]
        q = f'query {{ repository(owner: {json.dumps(owner)}, name: {json.dumps(name)}) {{ {" ".join(parts)} }} }}'
        try:
            obj = graphql_post(token, q)
        except Exception as e:
            log(f"  [warn] GraphQL PR 附件解析失败: {e}")
            continue
        repo_data = ((obj.get("data") or {}).get("repository") or {})
        for n in batch:
            node = repo_data.get(f"p{n}") or {}
            htmls = [node.get("bodyHTML") or ""]
            for c in ((node.get("comments") or {}).get("nodes") or []):
                htmls.append(c.get("bodyHTML") or "")
            map_html_to_aliases(htmls, pending, aliases)

    for batch in _batched([n for n in iss_need if n is not None], 8):
        parts = [
            f"i{n}: issue(number: {int(n)}) "
            f"{{ bodyHTML comments(first: 100) {{ nodes {{ bodyHTML }} }} }}"
            for n in batch
        ]
        q = f'query {{ repository(owner: {json.dumps(owner)}, name: {json.dumps(name)}) {{ {" ".join(parts)} }} }}'
        try:
            obj = graphql_post(token, q)
        except Exception as e:
            log(f"  [warn] GraphQL Issue 附件解析失败: {e}")
            continue
        repo_data = ((obj.get("data") or {}).get("repository") or {})
        for n in batch:
            node = repo_data.get(f"i{n}") or {}
            htmls = [node.get("bodyHTML") or ""]
            for c in ((node.get("comments") or {}).get("nodes") or []):
                htmls.append(c.get("bodyHTML") or "")
            map_html_to_aliases(htmls, pending, aliases)

    if rel_need:
        q = (
            f'query {{ repository(owner: {json.dumps(owner)}, name: {json.dumps(name)}) '
            f'{{ releases(first: 50, orderBy: {{field: CREATED_AT, direction: DESC}}) '
            f'{{ nodes {{ tagName descriptionHTML }} }} }} }}'
        )
        try:
            obj = graphql_post(token, q)
            nodes = (((obj.get("data") or {}).get("repository") or {}).get("releases") or {}).get("nodes") or []
            by_tag = {n.get("tagName"): n.get("descriptionHTML") or "" for n in nodes}
            for tag in rel_need:
                map_html_to_aliases([by_tag.get(tag) or ""], pending, aliases)
        except Exception as e:
            log(f"  [warn] GraphQL Release 附件解析失败: {e}")

    log(f"  GraphQL 附件别名: 已解析 {len(aliases)}，仍未解析 {len(pending)}")
    return aliases


def extract_md_image_urls(text: str) -> list[str]:
    """从 markdown / HTML 正文抽取图片 URL（保持顺序、去重）。

    先去掉 fenced code 与 inline code，避免下载示例代码里的假 URL。
    """
    if not text:
        return []
    cleaned = re.sub(r"```[\s\S]*?```", "\n", text)
    cleaned = re.sub(r"~~~[\s\S]*?~~~", "\n", cleaned)
    cleaned = re.sub(r"`[^`\n]+`", "\n", cleaned)
    found = []
    for m in MD_IMAGE_RE.finditer(cleaned):
        u = (m.group(1) or m.group(2) or "").strip()
        if u:
            found.append(u)
    for m in HTML_IMG_SRC_RE.finditer(cleaned):
        u = (m.group(1) or "").strip()
        if u:
            found.append(u)
    seen = set()
    out = []
    for u in found:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def collect_body_image_urls(prs: list, issues: list, releases: list) -> list[str]:
    """汇总 PR/Issue 评论与 Release 正文中的图片 URL。"""
    texts = []
    for p in prs or []:
        texts.append(p.get("body") or "")
        for c in p.get("issue_comments") or []:
            texts.append(c.get("body") or "")
    for i in issues or []:
        texts.append(i.get("body") or "")
        for c in i.get("issue_comments") or []:
            texts.append(c.get("body") or "")
    for r in releases or []:
        texts.append(r.get("body") or "")
    urls: list[str] = []
    seen = set()
    for t in texts:
        for u in extract_md_image_urls(t):
            if u not in seen:
                seen.add(u)
                urls.append(u)
    return urls


def resolve_repo_image_url(url: str, repo: str, tree_image_paths: list[str]) -> str | None:
    """绝对 raw/blob URL 若指向仓库树内图片，返回 assets 相对路径，避免重复下载。"""
    if not url or url.startswith("data:"):
        return None
    if not _is_github_asset_host(url):
        return None
    bare = url.split("?")[0].split("#")[0]
    bare = urllib.parse.unquote(bare)
    repo_l = repo.lower()
    if repo_l not in bare.lower():
        return None
    for path in sorted(tree_image_paths, key=len, reverse=True):
        if bare.endswith("/" + path) or bare.endswith("/" + path.replace(" ", "%20")):
            return asset_rel(path)
    return None


def sniff_image_ext(data: bytes) -> str:
    """按文件头猜测图片扩展名。"""
    if not data:
        return ""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return ".gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp"
    if data.startswith(b"BM"):
        return ".bmp"
    head = data[:200].lstrip().lower()
    if head.startswith(b"<?xml") or head.startswith(b"<svg"):
        return ".svg"
    return ""


def guess_ext_from_url_ct(url: str, ctype: str) -> str:
    path = urllib.parse.urlparse(url).path
    ext = Path(path).suffix.lower()
    if ext in IMAGE_EXT:
        return ext
    ctype = (ctype or "").split(";")[0].strip().lower()
    return CTYPE_EXT.get(ctype, "")


def body_image_rel(url: str, ext: str) -> str:
    h = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
    if not ext.startswith("."):
        ext = ("." + ext) if ext else ".img"
    return f"externals/{h}{ext}"


def fetch_image_bytes(url: str, token: str, max_bytes: int, timeout: int = 30) -> tuple[bytes, str]:
    """下载图片字节；仅 GitHub 系域名带 Token（URL 已含 jwt 时不附带）。

    返回 (data, content-type)。失败时短暂退避重试。
    """
    headers = {
        "User-Agent": "Mirror_export/1.0",
        "Accept": "image/*,*/*;q=0.8",
    }
    if token and _is_github_asset_host(url) and "jwt=" not in url.lower():
        headers["Authorization"] = f"Bearer {token}"
    last_err = None
    for attempt in range(3):
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                ctype = resp.headers.get("Content-Type", "") or ""
                cl = resp.headers.get("Content-Length")
                if cl:
                    try:
                        if int(cl) > max_bytes:
                            raise RuntimeError(f"超过大小上限 {max_bytes}")
                    except ValueError:
                        pass
                chunks = []
                got = 0
                while True:
                    chunk = resp.read(64 * 1024)
                    if not chunk:
                        break
                    got += len(chunk)
                    if got > max_bytes:
                        raise RuntimeError(f"超过大小上限 {max_bytes}")
                    chunks.append(chunk)
                return b"".join(chunks), ctype
        except urllib.error.HTTPError as e:
            if e.code >= 500 and attempt < 2:
                time.sleep(BACKOFF ** attempt)
                last_err = e
                continue
            raise
        except Exception as e:
            if attempt < 2:
                time.sleep(1.0 * (attempt + 1))
                last_err = e
                continue
            raise RuntimeError(f"下载失败 {url}: {e}") from e
    raise RuntimeError(f"下载失败 {url}: {last_err}")


def download_body_images(
    urls: list[str],
    token: str,
    out: Path,
    repo: str,
    tree_image_paths: list[str],
    prev_index: dict | None = None,
    reuse: bool = True,
    max_bytes: int = BODY_IMAGE_MAX_BYTES,
    aliases: dict | None = None,
    stage: str = "",
) -> tuple[dict, int, int, int]:
    """下载 PR/Issue/Release 正文中的外链图片。

    返回 (image_map 原始URL→assets相对路径, 新下载, 复用, 失败/跳过)。
    增量键：assets/.index.json 中的 ``ext:<url>``（键为 markdown 原始 URL）。
    ``aliases``：原始 URL → 实际可下载地址（如 GraphQL 解析出的 private-user-images）。
    保存时合并磁盘上已有键，避免覆盖树图片的 path→sha。
    """
    assets_dir = out / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    index = load_asset_index(out)
    if prev_index:
        index.update(prev_index)
    image_map: dict[str, str] = {}
    aliases = aliases or {}
    n_new = 0
    n_skip = 0
    n_fail = 0

    # 进度分母：可下载的绝对 URL（跳过 data: 与相对路径）
    targets = [
        u for u in urls
        if u and not u.startswith("data:") and re.match(r"^https?://", u, re.I)
    ]
    if stage:
        stage_begin(stage, len(targets))
    throttle = make_throttle(1)
    for i, url in enumerate(targets, 1):
        # 进度放在循环前部，命中复用 / 失败的也计入，避免停在 99% 不动
        if stage and throttle(stage, i, len(targets)):
            progress(stage, i, len(targets), url[:80])
        # 仓库树内已有 → 直接映射，不重复下载
        local = resolve_repo_image_url(url, repo, tree_image_paths)
        if local:
            image_map[url] = local
            n_skip += 1
            continue
        key = f"ext:{url}"
        rel = index.get(key) if reuse else None
        if isinstance(rel, str) and rel:
            dst = assets_dir / rel
            if dst.exists() and dst.stat().st_size > 0:
                image_map[url] = rel
                n_skip += 1
                continue
        fetch_url = aliases.get(url) or url
        try:
            data, ctype = fetch_image_bytes(fetch_url, token, max_bytes)
            if not data:
                n_fail += 1
                continue
            ext = guess_ext_from_url_ct(fetch_url, ctype) or guess_ext_from_url_ct(url, ctype) or sniff_image_ext(data) or ".png"
            rel = body_image_rel(url, ext)
            dst = assets_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(data)
            index[key] = rel
            image_map[url] = rel
            n_new += 1
            if not stage and n_new % 20 == 0:
                log(f"  正文图片: {n_new}")
        except Exception as e:
            n_fail += 1
            hint = "（已尝试 GraphQL 别名）" if fetch_url != url else ""
            log(f"  [warn] 正文图片失败 {url[:100]}{hint}: {e}")

    save_asset_index(out, index)
    log(f"  正文图片: 新下 {n_new}, 复用 {n_skip}, 失败/跳过 {n_fail}, 映射 {len(image_map)}")
    return image_map, n_new, n_skip, n_fail


def app_root() -> Path:
    """脚本运行时为源码目录；打包为 exe 时为解包资源目录或 exe 旁目录。"""
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass and (Path(meipass) / "viewer").is_dir():
            return Path(meipass)
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".bmp"}


def copy_viewer(viewer_src: Path, out: Path) -> None:
    # 新增 viewer 文件时必须登记到这里，否则导出包会缺文件、内网打开报错
    for rel in ("index.html", "css/app.css", "js/app.js", "js/gitgraph.js"):
        src = viewer_src / rel
        if not src.exists():
            raise FileNotFoundError(f"缺少 viewer 文件: {src}")
        dst = out / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(src.read_bytes())


def asset_rel(path: str) -> str:
    """把仓库内路径转成 assets 下的相对路径（统一正斜杠）。

    注意**不能用** ``lstrip("./")``：它按字符集剥离，会把 ``.github/banner.png``
    开头的 ``.`` 也吃掉，得到 ``github/banner.png``；而查看器 mdSrc 的兜底只去掉
    真正的 ``./`` 前缀，仍去找 ``assets/.github/banner.png`` → 内网裂图。
    这里只剥真正的 ``./`` 前缀与开头的 ``/``，保留点目录。
    """
    rel = path.replace("\\", "/")
    while rel.startswith("./"):
        rel = rel[2:]
    rel = rel.lstrip("/")
    return rel


def merge_image_map(prev_map: dict | None, new_map: dict | None, out: Path) -> dict:
    """把本轮正文图片映射与上一轮合并（新结果优先），避免整块覆盖丢历史。

    ``meta.image_map`` 是「原始 URL → assets 相对路径」。若每轮只写本轮结果，
    那么第二轮带 ``--no-assets``、或本轮收集到的 URL 集合为空（例如数量上限调小）时，
    映射会被清成 ``{}``，而 ``assets/externals`` 里的文件其实还在 —— 内网查看器
    拿不到映射就退回原始外链，正文图片全部裂图。

    合并时校验目标文件确实存在：缺失的旧映射不保留，避免指向已被清理的文件。
    """
    merged: dict = {}
    for url, rel in (prev_map or {}).items():
        if not isinstance(rel, str) or not rel:
            continue
        try:
            if (out / "assets" / rel).is_file():
                merged[url] = rel
        except OSError:
            continue
    for url, rel in (new_map or {}).items():
        if isinstance(rel, str) and rel:
            merged[url] = rel
    return merged


def export(args) -> Path:
    # 每轮导出都必须重置进度状态：GUI 在同一进程内连续导出多个组合时，
    # 上一轮遗留的 overall=100 会让新一轮首帧直接显示 100%（见 reset_progress）。
    reset_progress()

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    if args.token:
        token = args.token
    if not token:
        log("错误: 未设置 GITHUB_TOKEN")
        log("  PowerShell: $env:GITHUB_TOKEN = \"ghp_xxx\"")
        log("  CMD:        set GITHUB_TOKEN=ghp_xxx")
        sys.exit(1)

    repo = args.repo.strip("/")
    if not repo or "/" not in repo:
        log("错误: 请指定仓库，格式 owner/name（例如 --repo myorg/myrepo）")
        sys.exit(1)
    out = Path(args.out)
    incremental = bool(getattr(args, "incremental", True))

    if out.exists() and any(out.iterdir()) and not args.force and not incremental:
        log(f"错误: 输出目录已存在且非空: {out}（可加 --force 覆盖，或开启增量导出）")
        sys.exit(1)

    prev = {}
    prev_asset_index = {}
    if incremental and out.exists() and (out / "data").is_dir():
        prev = load_previous_export(out)
        prev_asset_index = load_asset_index(out)
        n_commits = len(prev.get("commits") or [])
        n_prs = len(prev.get("prs") or [])
        n_diff = sum(1 for c in (prev.get("commits") or []) if c.get("has_diff") or (c.get("files")))
        log(f"增量模式: 缓存命中 commits={n_commits} (含diff {n_diff}), prs={n_prs}")
        if n_commits == 0 and n_prs == 0:
            log("  [warn] 未解析到缓存 data，将按全量导出（请确认输出目录正确）")
    elif incremental and out.exists() and any(out.iterdir()) and not (out / "data").is_dir():
        log("输出目录非空但无 data 缓存，将完整导出")
    else:
        log("全量导出（未启用增量或目录为空）")

    out.mkdir(parents=True, exist_ok=True)
    (out / "data").mkdir(exist_ok=True)
    copy_viewer(app_root() / "viewer", out)

    log(f"导出仓库: {repo}")
    log(f"输出目录: {out.resolve()}")

    # --- repo meta ---
    stage_begin("仓库信息")
    repo_info, _ = http_get(f"{API}/repos/{repo}", token)
    default_branch = repo_info.get("default_branch") or "main"
    meta = {
        "repo": repo,
        "full_name": repo_info.get("full_name"),
        "html_url": repo_info.get("html_url"),
        "description": repo_info.get("description"),
        "private": repo_info.get("private"),
        "default_branch": default_branch,
        "stargazers_count": repo_info.get("stargazers_count"),
        "forks_count": repo_info.get("forks_count"),
        "open_issues_count": repo_info.get("open_issues_count"),
        "language": repo_info.get("language"),
        "topics": repo_info.get("topics") or [],
        "created_at": repo_info.get("created_at"),
        "updated_at": repo_info.get("updated_at"),
        "pushed_at": repo_info.get("pushed_at"),
        "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "exporter": "Mirror_export",
        "incremental": incremental and bool(prev),
        "image_map": {},
        "limits": {
            "prs": args.max_prs,
            "issues": args.max_issues,
            "commits": args.max_commits,
            "branches": getattr(args, "max_branches", 100),
            "file_bytes": args.max_file_bytes,
        },
    }

    # --- tags & releases ---
    stage_begin("Tags/Releases", 2)
    log("拉取 tags ...")
    raw_tags = paginate(f"{API}/repos/{repo}/tags", token, args.max_tags, "tags")
    tags = [
        {
            "name": t.get("name"),
            "sha": (t.get("commit") or {}).get("sha"),
            "zipball_url": t.get("zipball_url"),
        }
        for t in raw_tags
    ]
    log(f"  tags: {len(tags)}")
    progress("Tags/Releases", 1, 2, f"tags {len(tags)}")

    log("拉取 releases ...")
    raw_releases = paginate(f"{API}/repos/{repo}/releases", token, args.max_tags, "releases")
    releases = [
        {
            "tag_name": r.get("tag_name"),
            "name": r.get("name"),
            "draft": r.get("draft"),
            "prerelease": r.get("prerelease"),
            "created_at": r.get("created_at"),
            "published_at": r.get("published_at"),
            "body": r.get("body") or "",
            "html_url": r.get("html_url"),
            "author": slim_user(r.get("author")),
            "target_commitish": r.get("target_commitish"),
        }
        for r in raw_releases
    ]
    log(f"  releases: {len(releases)}")
    progress("Tags/Releases", 2, 2, f"releases {len(releases)}")

    # --- release compare: commits / files / 关联 PR（增量：已有则复用） ---
    stage_begin("Release 对比", len(releases))
    log("为每个 Release 拉取对比 ...")
    prev_releases = {
        r.get("tag_name"): r for r in (prev.get("releases") or []) if r.get("tag_name")
    }
    # tags 新→旧；每个 release 对「它在 tags 里的前一个 tag」做 compare。
    # 注意：不能用 release 的下标去索引 tags —— 存在没有 Release 的 tag（CI/nightly 很常见），
    # 或 releases 条数/顺序与 tags 不一致时，下标会错位，导致 base 指向错误的 tag，
    # 甚至 base == head 自比（total_commits=0、无文件差异）。这里改为按 tag_name 定位。
    tag_names = [t.get("name") for t in tags if t.get("name")]
    tag_pos = {name: idx for idx, name in enumerate(tag_names)}
    for i, rel in enumerate(releases):
        tag = rel.get("tag_name")
        old = prev_releases.get(tag)
        # 从 body 里抽 #123 关联 PR。这一步只依赖 body（每轮都会重新拉到），
        # 与 compare 的缓存命中无关，因此必须放在复用判断之前无条件执行：
        # 否则命中缓存时 related_pr_nums 缺失，回填段会把 related_prs 覆盖成 []。
        body = rel.get("body") or ""
        rel["related_pr_nums"] = sorted({int(x) for x in re.findall(r"#(\d+)", body)})
        if (
            incremental
            and old
            and old.get("compare") is not None
            and old.get("published_at") == rel.get("published_at")
        ):
            rel["compare"] = old["compare"]
            progress("Release 对比", i + 1, len(releases), f"{tag}（缓存）")
            continue
        # base = 该 release 对应 tag 在 tags 列表里的下一个（更旧的）tag；
        # 找不到该 tag 时退回「按 release 顺序取下一个」，保证旧行为不至于完全失效。
        pos = tag_pos.get(tag)
        if pos is not None:
            base = tag_names[pos + 1] if pos + 1 < len(tag_names) else None
        else:
            base = tag_names[i + 1] if i + 1 < len(tag_names) else None
        if base == tag:
            base = None   # 防御：绝不与自身对比
        compare = {"base": base, "head": tag, "commits": [], "files": [], "total_commits": 0, "truncated": False}
        if base and not args.skip_details:
            try:
                cmp_url = f"{API}/repos/{repo}/compare/{urllib.parse.quote(base)}...{urllib.parse.quote(tag)}"
                raw_cmp, _ = http_get(cmp_url, token)
                c_commits = raw_cmp.get("commits") or []
                compare["total_commits"] = raw_cmp.get("total_commits") or len(c_commits)
                # 只保留列表展示需要的字段。
                # 必须带 parents：查看器 reachableSet() 沿 parents 回溯，
                # 缺该字段会让可达集合在此断链（版本对比的落后/分叉点失真、
                # Commit 页切到该 tag 也看不到更早历史）。与分支 compare 口径一致。
                compare["commits"] = [
                    {
                        "sha": c.get("sha"),
                        "message": (c.get("commit") or {}).get("message") or "",
                        "author_name": ((c.get("commit") or {}).get("author") or {}).get("name"),
                        "author_date": ((c.get("commit") or {}).get("author") or {}).get("date"),
                        "author": slim_user(c.get("author")),
                        "parents": [p.get("sha") for p in (c.get("parents") or []) if p.get("sha")],
                    }
                    for c in c_commits[:RELEASE_COMPARE_MAX_COMMITS]
                ]
                compare["files"] = [map_file(f) for f in (raw_cmp.get("files") or [])[:200]]
                compare["ahead_by"] = raw_cmp.get("ahead_by")
                compare["behind_by"] = raw_cmp.get("behind_by")
                compare["status"] = raw_cmp.get("status")
            except Exception as e:
                log(f"  [warn] compare {base}...{tag} 失败: {e}")
                compare["error"] = str(e)
        rel["compare"] = compare
        log(f"  release {tag}: base={base}, commits={compare.get('total_commits', 0)}")
        progress("Release 对比", i + 1, len(releases), f"{tag}")

    log(f"  releases 对比完成")

    # --- commits：无限累积缓存；每轮从 HEAD 往回爬 max_commits，与本地合并，不丢历史 ---
    stage_begin("Commits")
    log(f"拉取 commits ({default_branch}) 本轮窗口 {args.max_commits} ...")
    raw_commits = paginate(
        f"{API}/repos/{repo}/commits?sha={urllib.parse.quote(default_branch)}",
        token,
        args.max_commits,
        "commits",
    )
    prev_commits = {c.get("sha"): c for c in (prev.get("commits") or []) if c.get("sha")}
    api_shas = {c.get("sha") for c in raw_commits if c.get("sha")}

    diff_budget = args.max_commit_diffs
    if diff_budget is None or diff_budget <= 0:
        diff_budget = len(raw_commits) + len(prev_commits)
    n_c_reuse = 0
    n_c_fetch = 0
    n_c_nodiff = 0
    commits = []

    def commit_needs_diff(old: dict | None) -> bool:
        """该 commit 是否还需要（重新）抓取文件 diff。

        关键：必须区分「抓取失败 / 超出预算」与「抓到了，结果就是空」。
        旧实现只看 ``"files" in old``，而失败路径同样会写出 ``files: []``，
        于是失败被永久固化成「已完成」，之后调大 --max-commit-diffs 也补不回来。
        现在只有「明确标记过 has_diff」或「手里有非空文件列表」才算完成。
        """
        if args.skip_details:
            return False
        if old is None:
            return True
        if old.get("has_diff") or old.get("files"):
            return False
        # 既无 files 键、又没标记 has_diff（含 files 为空）→ 上轮没真正抓到，本轮重试
        return True

    def has_diff_done(old: dict | None) -> bool:
        if not old:
            return False
        return bool(old.get("has_diff") or old.get("files"))

    # 本轮待补 diff：先「窗口内新出现的」，再「历史缓存里仍缺的」
    need_list = []
    seen = set()
    for c in raw_commits:
        sha = c.get("sha")
        if not sha or sha in seen:
            continue
        seen.add(sha)
        old = prev_commits.get(sha) if incremental else None
        if old is not None and not commit_needs_diff(old):
            continue
        need_list.append(c)
    # 历史中仍无 diff 的（更旧的）
    if incremental and not args.skip_details:
        hist_missing = [
            old for sha, old in prev_commits.items()
            if sha and sha not in api_shas and commit_needs_diff(old)
        ]
        # 按时间新→旧
        hist_missing.sort(
            key=lambda x: x.get("author_date") or x.get("committer_date") or "",
            reverse=True,
        )
        need_list.extend(hist_missing)

    need_diff_n = min(len(need_list), diff_budget)
    log(
        f"  缓存已有 {len(prev_commits)} 条 · 本轮 API 窗口 {len(raw_commits)} · 待补 diff {need_diff_n}"
        f"（总待补 {len(need_list)}，本轮上限 {diff_budget}）"
    )

    def build_entry(c: dict, old: dict | None) -> dict:
        nonlocal n_c_fetch, n_c_nodiff, n_c_reuse
        sha = c.get("sha")
        if old is not None and not commit_needs_diff(old):
            n_c_reuse += 1
            return old
        files = []
        stats = {}
        fetched = False
        if (not args.skip_details) and sha and n_c_fetch < diff_budget:
            try:
                raw, _ = http_get(f"{API}/repos/{repo}/commits/{sha}", token)
                files = [map_file(f) for f in (raw.get("files") or [])]
                stats = raw.get("stats") or {}
                n_c_fetch += 1
                fetched = True
            except Exception as e:
                log(f"  [warn] commit {short_sha(sha)} files 失败: {e}")
                n_c_nodiff += 1
        else:
            if not args.skip_details:
                n_c_nodiff += 1
            if old:
                files = old.get("files") or []
                stats = old.get("stats") or {}
        c2 = dict(c)
        c2["stats"] = stats
        entry = map_commit(c2, files if fetched else [])
        entry["has_diff"] = bool(fetched) or has_diff_done(old)
        if not fetched and old:
            if old.get("files") is not None:
                entry["files"] = old.get("files") or []
            if old.get("stats"):
                entry["stats"] = old.get("stats") or {}
            if has_diff_done(old):
                entry["has_diff"] = True
        # 本轮没真正抓到（失败 / 超预算）且缓存里也没有 → 删掉 files 键，
        # 让「files 键存在」严格表示「已拿到确定结果」，失败条目下轮才会被重试。
        if not fetched and not has_diff_done(old):
            entry.pop("files", None)
        return entry

    # 1) 处理本轮 API 窗口（新→旧）
    # 进度以「本轮 API 窗口」为分母、已处理条数为分子；命中和无需 diff 的同样计入，
    # 这样纯增量全命中缓存时进度条也会走完，而不是纹丝不动。
    stage_begin("Commit diff", len(raw_commits))
    t_commit = make_throttle(1)
    seen2 = set()
    n_commit_seen = 0   # 真正写入 commits 的条数（去重后）
    n_commit_iter = 0   # 遍历到的原始条数（含重复/缺 sha），作为进度分子
    for c in raw_commits:
        n_commit_iter += 1
        # 上报点必须在任何 continue 之前，且分子用「遍历条数」：
        # 分子若用去重后的条数，遇到重复 sha / 缺 sha 时永远小于分母，进度到不了 100%。
        if t_commit("Commit diff", n_commit_iter, len(raw_commits)):
            sha0 = c.get("sha") or ""
            msg0 = ((c.get("commit") or {}).get("message") or "").split("\n")[0]
            progress("Commit diff", n_commit_iter, len(raw_commits), f"{short_sha(sha0)} {msg0[:48]}")
        sha = c.get("sha")
        if not sha or sha in seen2:
            continue
        seen2.add(sha)
        old = prev_commits.get(sha) if incremental else None
        commits.append(build_entry(c, old))
        n_commit_seen += 1
    # 极端情况：窗口为空但本轮确实执行了该阶段，补一条 100% 让进度条到位
    if not raw_commits:
        progress("Commit diff", 0, 0, "无新 commit")

    # 2) 合并历史缓存：不在本轮窗口里的一律保留（无限累积）
    if incremental and prev_commits:
        extra = 0
        for sha, old in prev_commits.items():
            if not sha or sha in api_shas:
                continue
            commits.append(old)
            extra += 1
        if extra:
            log(f"  保留历史 commit: +{extra}（缓存无限累积）")

    log(
        f"  commits 总计 {len(commits)}（本轮 diff 新拉 {n_c_fetch}, 缓存复用 {n_c_reuse}, 无 diff {n_c_nodiff}）"
    )

    # --- pull requests（增量：updated_at 未变则复用详情） ---
    stage_begin("Pull Requests")
    log("拉取 pull requests ...")
    # 按编号（创建顺序）从新到旧拉取，保证数量上限时优先保留高编号
    raw_prs = paginate(f"{API}/repos/{repo}/pulls?state=all&sort=created&direction=desc", token, args.max_prs, "prs")
    prev_prs = {p.get("number"): p for p in (prev.get("prs") or []) if p.get("number")}
    prs = []
    n_pr_reuse = 0
    n_pr_fetch = 0
    stage_begin("PR 详情", len(raw_prs))
    t_pr = make_throttle(1)
    for i, pr in enumerate(raw_prs, 1):
        num = pr.get("number")
        old = prev_prs.get(num)
        # 进度放在循环最前：命中缓存走 continue 的也会计入，纯增量全命中时进度条照常走完
        if t_pr("PR 详情", i, len(raw_prs)):
            progress("PR 详情", i, len(raw_prs), f"#{num} {str(pr.get('title') or '')[:40]}")
        # 复用判据：上轮「真的抓到了」才复用。旧缓存没有 *_fetched 标记时，
        # 退化为「有非空内容即视为成功」（空内容无法区分失败与空结果，宁可重拉一次）。
        old_files_ok = bool(old and (old.get("files_fetched") or old.get("files")))
        old_comments_ok = bool(
            old
            and (
                old.get("comments_fetched")
                or old.get("issue_comments")
                or not (pr.get("comments") or 0)   # 本来就没有评论，无需抓取
            )
        )
        if (
            incremental
            and not args.skip_details
            and old
            and old.get("updated_at") == pr.get("updated_at")
            and old_files_ok
            and old_comments_ok
        ):
            prs.append(old)
            n_pr_reuse += 1
            continue
        files = []
        comments = []
        pr_commits = []
        files_fetched = False
        comments_fetched = False
        if not args.skip_details:
            try:
                raw_files = paginate(f"{API}/repos/{repo}/pulls/{num}/files", token, 300, f"pr#{num} files")
                files = [map_file(f) for f in raw_files]
                files_fetched = True
            except Exception as e:
                log(f"  [warn] PR#{num} files 失败: {e}")
            try:
                raw_pc = paginate(f"{API}/repos/{repo}/pulls/{num}/commits", token, 100, f"pr#{num} commits")
                pr_commits = [map_commit(c, []) for c in raw_pc]
            except Exception as e:
                log(f"  [warn] PR#{num} commits 失败: {e}")
            if (pr.get("comments") or 0) > 0:
                try:
                    raw_c = paginate(f"{API}/repos/{repo}/issues/{num}/comments", token, 200, f"pr#{num} comments")
                    comments = [
                        {
                            "user": slim_user(c.get("user")),
                            "created_at": c.get("created_at"),
                            "body": c.get("body") or "",
                        }
                        for c in raw_c
                    ]
                    comments_fetched = True
                except Exception as e:
                    log(f"  [warn] PR#{num} comments 失败: {e}")
            else:
                # 无评论：视为已确认，无需每轮重查
                comments_fetched = True
            n_pr_fetch += 1
        entry = map_pr(pr, files, comments)
        entry["merged"] = bool(pr.get("merged_at"))
        entry["commits_list"] = pr_commits
        # 成功标记：与 files 内容解耦，保证「抓到空结果」与「没抓到」可区分
        entry["files_fetched"] = files_fetched
        entry["comments_fetched"] = comments_fetched
        prs.append(entry)
    prs.sort(key=lambda p: (p.get("number") or 0), reverse=True)
    log(f"  PRs: {len(prs)}（详情新拉 {n_pr_fetch}, 缓存复用 {n_pr_reuse}）")

    # 回填 release 关联 PR（body 里的 #num）
    pr_index = {p.get("number"): p for p in prs}
    for rel in releases:
        nums = sorted(rel.get("related_pr_nums") or [], reverse=True)
        rel["related_prs"] = [
            {
                "number": n,
                "title": (pr_index.get(n) or {}).get("title"),
                "state": (pr_index.get(n) or {}).get("state"),
                "merged": (pr_index.get(n) or {}).get("merged"),
            }
            for n in nums
            if n in pr_index
        ]

    # --- issues（增量） ---
    stage_begin("Issues")
    log("拉取 issues ...")
    # 数量上限语义：<=0 表示「不限」（与 --max-prs 一致）。
    # 注意不能用 `len(issues) >= args.max_issues` 直接判断——当 max_issues=0（CLI 默认）时
    # 该表达式恒为真，会在第一条 issue 之后就 break，使「0=全部」实际只导出 1 条。
    issue_limit = int(getattr(args, "max_issues", 0) or 0)

    def issue_limit_reached(n: int) -> bool:
        return issue_limit > 0 and n >= issue_limit

    # 按编号（创建顺序）从新到旧拉取；边翻页边过滤 pull_request，
    # 上限按「真正的 issue 数」计，避免 PR 占比高时取不够数（旧实现用 max_issues*2 启发式）。
    raw_issues_all = paginate_skipping(
        f"{API}/repos/{repo}/issues?state=all&sort=created&direction=desc",
        token,
        issue_limit,
        "issues",
        skip=lambda it: bool(it.get("pull_request")),
    )
    prev_issues = {i.get("number"): i for i in (prev.get("issues") or []) if i.get("number")}
    issues = []
    n_i_reuse = 0
    n_i_fetch = 0
    # 进度分母用「过滤后的条目数」：paginate_skipping 已剔除 PR，
    # 这里再滤一次只作防御（保持行为与旧版一致），不应让分母把 PR 算进去。
    raw_issues_all = [it for it in raw_issues_all if not it.get("pull_request")]
    progress("Issues", 0, len(raw_issues_all), "开始")
    t_issue = make_throttle(1)
    for i, item in enumerate(raw_issues_all, 1):
        num = item.get("number")
        # 同样放在循环前部：命中缓存的 issue 也要计入进度
        if t_issue("Issues", i, len(raw_issues_all)):
            progress("Issues", i, len(raw_issues_all), f"#{num} {str(item.get('title') or '')[:40]}")
        old = prev_issues.get(num)
        # 复用判据：同 PR 段。旧缓存无 comments_fetched 标记时，退化为
        # 「有非空评论 / 本来就没有评论」才算成功，失败产生的空数组会被重拉。
        old_comments_ok = bool(
            old
            and (
                old.get("comments_fetched")
                or old.get("issue_comments")
                or not (item.get("comments") or 0)
            )
        )
        if (
            incremental
            and not args.skip_details
            and old
            and old.get("updated_at") == item.get("updated_at")
            and old_comments_ok
        ):
            issues.append(old)
            n_i_reuse += 1
            if issue_limit_reached(len(issues)):
                break
            continue
        comments = []
        comments_fetched = False
        if (item.get("comments") or 0) > 0:
            if not args.skip_details:
                try:
                    raw_c = paginate(f"{API}/repos/{repo}/issues/{num}/comments", token, 200, f"issue#{num} comments")
                    comments = [
                        {
                            "user": slim_user(c.get("user")),
                            "created_at": c.get("created_at"),
                            "body": c.get("body") or "",
                        }
                        for c in raw_c
                    ]
                    comments_fetched = True
                except Exception as e:
                    log(f"  [warn] Issue#{num} comments 失败: {e}")
                n_i_fetch += 1
            # --skip-details：本轮有意不抓，保持 False，以便去掉该选项后能补抓
        else:
            # 本来就没有评论，无需抓取，视为已确认
            comments_fetched = True
        entry_issue = map_issue(item, comments)
        entry_issue["comments_fetched"] = comments_fetched
        issues.append(entry_issue)
        # 继承缓存里的 closed_by_pr（避免每次重查 timeline）
        if incremental and old and "closed_by_pr" in old:
            issues[-1]["closed_by_pr"] = old["closed_by_pr"]
        if issue_limit_reached(len(issues)):
            break
    issues.sort(key=lambda x: (x.get("number") or 0), reverse=True)
    log(f"  issues: {len(issues)}（详情新拉 {n_i_fetch}, 缓存复用 {n_i_reuse}）")

    # --- 标记「由哪个 PR 关闭」：解析 PR body 的 Closes/Fixes #n，以及 timeline ---
    log("关联 Issue ↔ 关闭它的 PR ...")
    close_re = re.compile(
        r"(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(\d+)",
        re.I,
    )
    closed_by_map: dict[int, dict] = {}
    for p in prs:
        body = p.get("body") or ""
        for m in close_re.finditer(body):
            n = int(m.group(1))
            if n not in closed_by_map:
                closed_by_map[n] = {
                    "number": p.get("number"),
                    "title": p.get("title"),
                    "merged": p.get("merged"),
                    "state": p.get("state"),
                    "source": "pr_body",
                }
    # 只对「关闭且缓存里还没有 closed_by_pr」的 issue 查 timeline，避免每次导出重复打 30 次
    need_timeline = []
    for i in issues:
        if i.get("state") != "closed":
            continue
        n = i.get("number")
        if n in closed_by_map:
            continue
        # 缓存里已有 closed_by_pr 字段（含 null）则不重查
        if incremental and i.get("closed_by_pr") is not None:
            continue
        if incremental and "closed_by_pr" in i and i.get("closed_by_pr") is None:
            # 上次已查过且没有，不再查
            continue
        need_timeline.append(i)

    if not args.skip_details:
        need_timeline = need_timeline[:20]
        if need_timeline:
            log(f"  timeline 待查 {len(need_timeline)} 条（仅缺失项）")
        stage_begin("Issue 关联", len(need_timeline))
        for idx, iss in enumerate(need_timeline, 1):
            n = iss.get("number")
            try:
                events, _ = http_get(f"{API}/repos/{repo}/issues/{n}/timeline", token)
                if not isinstance(events, list):
                    # 不能 continue：那会跳过下面的 progress()，该轮 done 少记一次，
                    # 阶段永远到不了 total（GUI 停在 N-1/N）。这里只记 warn，
                    # 统一在循环末尾上报进度。
                    log(f"  [warn] issue#{n} timeline 返回非列表，已跳过")
                else:
                    for ev in events:
                        et = ev.get("event")
                        src = ev.get("source") or {}
                        src_issue = src.get("issue") or {}
                        if src_issue.get("pull_request") and et in (
                            "cross-referenced", "connected", "closed", "referenced"
                        ):
                            pr_num = src_issue.get("number")
                            pr = pr_index.get(pr_num) or {}
                            closed_by_map[n] = {
                                "number": pr_num,
                                "title": pr.get("title") or src_issue.get("title"),
                                "merged": pr.get("merged"),
                                "state": pr.get("state") or src_issue.get("state"),
                                "source": "timeline",
                            }
                            break
            except Exception as e:
                log(f"  [warn] issue#{n} timeline 失败: {e}")
            # 上报点必须在所有分支之后（不得被 continue 跳过）
            progress("Issue 关联", idx, len(need_timeline), f"issue#{n}")

    for iss in issues:
        n = iss.get("number")
        if n in closed_by_map:
            iss["closed_by_pr"] = closed_by_map[n]
        elif "closed_by_pr" not in iss:
            iss["closed_by_pr"] = None
    log(f"  closed_by 命中 {sum(1 for i in issues if i.get('closed_by_pr'))} 条")

    # --- file tree + text contents（按 blob sha 增量） ---
    stage_begin("文件树")
    log(f"拉取文件树 ({default_branch}) ...")
    tree_url = f"{API}/repos/{repo}/git/trees/{urllib.parse.quote(default_branch)}?recursive=1"
    tree_resp, _ = http_get(tree_url, token)
    tree_items = tree_resp.get("tree") or []
    truncated = bool(tree_resp.get("truncated"))

    prev_files = prev.get("files") or {}
    prev_contents = prev_files.get("contents") or {}
    prev_tree_sha = {}
    for t in prev_files.get("tree") or []:
        if t.get("path") and t.get("sha"):
            prev_tree_sha[t["path"]] = t["sha"]

    # 先按增量规则把树过一遍，算出「需要新下载的文本数」作为进度分母，
    # 否则分母只能边跑边长，百分比会乱跳。
    tree_kept = [
        t for t in tree_items
        if not any(
            (t.get("path") or "").startswith(p) or f"/{p}" in f"/{t.get('path') or ''}"
            for p in SKIP_DIR_PREFIX
        )
    ]
    text_targets = []
    for t in tree_kept:
        if t.get("type") != "blob":
            continue
        path = t.get("path") or ""
        size = t.get("size") or 0
        if not (args.include_contents and is_textish(path) and size <= args.max_file_bytes):
            continue
        if incremental and path in prev_contents and prev_tree_sha.get(path) == t.get("sha"):
            continue  # 命中缓存，不计入本阶段分母
        text_targets.append(t)
    progress("文件树", len(tree_kept), len(tree_kept), f"{len(text_targets)} 个文本待下载")

    files_meta = []
    contents = {}
    text_count = 0     # 本轮**新下载**的文本数（仅用于日志 / file_stats）
    text_done = 0      # 本轮**已处理**的文本数（成功 / 失败 / 复用都计入，作为进度分子）
    text_reuse = 0
    skipped = 0
    stage_begin("文件内容", len(text_targets))
    t_text = make_throttle(1)
    for t in tree_kept:
        path = t.get("path") or ""
        typ = t.get("type")
        if typ == "tree":
            files_meta.append({"path": path, "type": "tree", "size": 0})
            continue
        if typ != "blob":
            continue
        size = t.get("size") or 0
        files_meta.append({
            "path": path,
            "type": "blob",
            "size": size,
            "sha": t.get("sha"),
            "url": t.get("url"),
        })
        if args.include_contents and is_textish(path) and size <= args.max_file_bytes:
            # blob sha 未变则复用（含 README）
            if (
                incremental
                and path in prev_contents
                and prev_tree_sha.get(path) == t.get("sha")
            ):
                contents[path] = prev_contents[path]
                text_reuse += 1
                text_done += 1
                # 命中缓存也要计入进度，否则纯增量导出时该阶段纹丝不动
                if t_text("文件内容", text_done, len(text_targets)):
                    progress("文件内容", text_done, len(text_targets), f"{path}（缓存）")
                continue
            try:
                raw, headers = http_get(t["url"], token, accept="application/vnd.github.raw")
                if isinstance(raw, bytes):
                    text = raw.decode("utf-8", errors="replace")
                else:
                    text = str(raw)
                contents[path] = text
                text_count += 1
                text_done += 1
                if t_text("文件内容", text_done, len(text_targets)):
                    progress("文件内容", text_done, len(text_targets), path)
            except Exception as e:
                # 失败也计入进度分子：否则该阶段（权重最高）会零上报、进度条停住
                text_done += 1
                skipped += 1
                log(f"  [warn] 读取 {path} 失败: {e}")
                if t_text("文件内容", text_done, len(text_targets)):
                    progress("文件内容", text_done, len(text_targets), f"{path}（失败）")
        elif not is_textish(path) or size > args.max_file_bytes:
            skipped += 1

    files_meta.sort(key=lambda x: x["path"])
    log(f"  文件树: {len(files_meta)} 项, 新下文本 {text_count}, 缓存复用 {text_reuse}, 跳过 {skipped}, truncated={truncated}")

    meta["file_stats"] = {
        "total": len(files_meta),
        "contents": text_count,
        "contents_reused": text_reuse,
        "skipped": skipped,
        "tree_truncated": truncated,
        # branches 段稍后填充；先给默认值，保证 --no-branches 时字段仍存在
        "branches": 0,
        "branches_api": 0,
        "branches_reused": 0,
        "branches_stale": False,
    }

    # --- download images (增量按 blob sha) + 正文外链图片 ---
    tree_image_paths = [
        t.get("path") or ""
        for t in tree_items
        if (t.get("type") == "blob")
        and Path(t.get("path") or "").suffix.lower() in IMAGE_EXT
        and not any((t.get("path") or "").startswith(p) for p in SKIP_DIR_PREFIX)
    ]
    if not args.no_assets:
        log("下载图片资源 ...")
        img_new, img_skip = download_repo_images(
            repo, token, tree_items, out,
            prev_index=prev_asset_index if incremental else None,
            stage="仓库图片",
        )
        meta["file_stats"]["images"] = img_new
        meta["file_stats"]["images_reused"] = img_skip
        # PR/Issue/Release 正文中的外链图片（user-attachments 等）
        body_urls = collect_body_image_urls(prs, issues, releases)
        if body_urls:
            log(f"下载正文图片 ({len(body_urls)} 个 URL) ...")
            stage_begin("正文图片")
            # 私有库 user-attachments 直链 404：先 GraphQL bodyHTML 解析成可下载 CDN URL
            aliases = resolve_attachment_aliases(repo, prs, issues, releases, token)
            image_map, b_new, b_skip, b_fail = download_body_images(
                body_urls,
                token,
                out,
                repo,
                tree_image_paths,
                reuse=incremental,
                aliases=aliases,
                stage="正文图片",
            )
            # 与上一轮合并：本轮没扫到的 URL（数量上限调小 / 条目被删）仍保留旧映射，
            # 只要 assets 里的文件还在，内网就还能显示
            meta["image_map"] = merge_image_map(
                (prev.get("meta") or {}).get("image_map") if incremental else None,
                image_map,
                out,
            )
            meta["file_stats"]["body_images"] = b_new
            meta["file_stats"]["body_images_reused"] = b_skip
            meta["file_stats"]["body_images_failed"] = b_fail
        else:
            meta["file_stats"]["body_images"] = 0
            meta["file_stats"]["body_images_reused"] = 0
            meta["file_stats"]["body_images_failed"] = 0
            # 本轮没有正文图片 URL：沿用上一轮映射（文件仍在 assets/externals）
            meta["image_map"] = merge_image_map(
                (prev.get("meta") or {}).get("image_map") if incremental else None,
                None,
                out,
            )
    else:
        meta["file_stats"]["images"] = 0
        meta["file_stats"]["images_reused"] = 0
        meta["file_stats"]["body_images"] = 0
        meta["file_stats"]["body_images_reused"] = 0
        meta["file_stats"]["body_images_failed"] = 0
        # --no-assets：不下载新图片，但不得清掉上一轮已下载文件的映射
        meta["image_map"] = merge_image_map(
            (prev.get("meta") or {}).get("image_map") if incremental else None,
            None,
            out,
        )

    # --- branches（清单 + 头部提交摘要 + 与默认分支的对比） ---
    # 只读镜像语义：分支列表是「快照」，上游删掉的分支本包不再保留（与 commit 的无限累积相反）。
    # 为省配额：tip 已落在主干历史里的分支（纯落后）不发请求，直接 0 次 API 得到结果。
    branches = []
    if getattr(args, "no_branches", False):
        stage_begin("Branches")
        log("跳过 branches（--no-branches）")
    else:
        stage_begin("Branches")
        log("拉取 branches ...")
        prev_branches = {
            b.get("name"): b for b in (prev.get("branches") or []) if b.get("name")
        }
        branches_failed = False
        try:
            raw_branches = paginate(
                f"{API}/repos/{repo}/branches",
                token,
                getattr(args, "max_branches", 100),
                "branches",
            )
        except Exception as e:
            # 请求失败与「上游真的没有分支」必须区分：分支是快照语义，
            # 若把失败当成空结果写出，上一轮抓到的分支会被静默清空、缓存也被连带摧毁。
            # 这里回退到上一轮快照（宁可保留旧数据），并明确标注本轮未刷新。
            branches_failed = True
            log(f"  [warn] branches 失败: {e}")
            if prev_branches:
                raw_branches = [
                    {
                        "name": b.get("name"),
                        "commit": {"sha": b.get("sha")},
                        "protected": b.get("protected"),
                    }
                    for b in prev_branches.values()
                ]
                log(f"  [warn] 沿用上一轮分支快照 {len(raw_branches)} 条（本轮未刷新）")
            else:
                raw_branches = []
                log("  [warn] 无上一轮分支快照可沿用，本次 branches 为空")
        # 主干 tip：优先取本轮 API 窗口第一条（最新），否则用默认分支的分支条目
        main_tip = None
        for c in raw_commits:
            if c.get("sha"):
                main_tip = c.get("sha")
                break
        if not main_tip:
            for b in raw_branches:
                if b.get("name") == default_branch:
                    main_tip = (b.get("commit") or {}).get("sha")
                    break
        # sha -> commit 摘要（主干历史，用于零请求补头部信息）
        commit_by_sha = {}
        for c in commits:
            if c.get("sha"):
                commit_by_sha[c["sha"]] = c
        for sha, c in prev_commits.items():
            commit_by_sha.setdefault(sha, c)
        # sha -> parents（本地兜底用）
        parents_map = {}
        for c in commits:
            if c.get("sha"):
                parents_map[c["sha"]] = [p for p in (c.get("parents") or []) if p]
        for sha, c in prev_commits.items():
            parents_map.setdefault(sha, [p for p in (c.get("parents") or []) if p])
        # 主干 tip 的祖先集合（判断分支 tip 是否已包含于主干），以及已知历史是否完整
        def ancestors_of(start):
            seen = set()
            if not start:
                return seen
            stack = [start]
            while stack:
                cur = stack.pop()
                if cur in seen:
                    continue
                seen.add(cur)
                stack.extend(parents_map.get(cur, []))
            return seen

        main_ancestors = ancestors_of(main_tip)
        # 已知图闭合（每个已知 commit 的 parents 都在图内）才说明走到了根，
        # 否则本地算出的「落后条数」只是下界，需在数据里标明 approx。
        history_complete = not any(
            p not in parents_map
            for ps in parents_map.values()
            for p in ps
        )

        stage_begin("分支对比", len(raw_branches))
        t_branch = make_throttle(1)
        n_branch_reuse = 0
        n_branch_api = 0
        n_branch_contained = 0
        max_branch_commits = int(getattr(args, "max_branch_commits", 100) or 0)

        for i, rb in enumerate(raw_branches, 1):
            name = rb.get("name")
            sha = (rb.get("commit") or {}).get("sha")
            if not name:
                continue
            entry = {
                "name": name,
                "sha": sha,
                "protected": bool(rb.get("protected")),
                "default": name == default_branch,
                # 增量判据：compare 的 ahead/behind 依赖默认分支，改名后必须重拉。
                # 必须在此处就写好，否则 reuse / contained 两条 continue 路径会漏掉它。
                "default_branch": default_branch,
            }

            # 增量：sha / 默认分支名 / **主干 tip** 都未变且上轮已有 compare/head → 整条复用
            # 必须比对主干 tip：ahead_by / behind_by 是相对主干算的，主干前进后旧值就错了。
            # 旧缓存没有 base_sha 字段时视为不匹配 —— 宁可重拉一次 compare，也不展示陈旧数字。
            old = prev_branches.get(name) if incremental else None
            old_cmp = (old or {}).get("compare") or {}
            if (
                old
                and old.get("sha") == sha
                and old.get("compare") is not None
                and old.get("head") is not None
                and old.get("default_branch") == default_branch
                and old_cmp.get("base") == default_branch
                and old_cmp.get("base_sha") == main_tip
            ):
                entry["head"] = old.get("head")
                entry["compare"] = old.get("compare")
                n_branch_reuse += 1
                branches.append(entry)
                if t_branch("分支对比", i, len(raw_branches)):
                    progress("分支对比", i, len(raw_branches), f"{name}（缓存）")
                continue

            # 头部提交摘要：能从本地 commit 池拿到就不发请求
            head = None
            local = commit_by_sha.get(sha)
            if local:
                head = {
                    "message": local.get("message") or "",
                    "author_name": local.get("author_name"),
                    "author_date": local.get("author_date"),
                }

            # 分支 tip 已包含于主干（纯落后）：0 次 API，本地算 ahead/behind
            if sha and sha in main_ancestors:
                n_branch_contained += 1
                entry["head"] = head or (old or {}).get("head")
                same_tip = bool(main_tip) and sha == main_tip
                if same_tip:
                    # 与主干同一提交：既没领先也没落后
                    ahead, behind, status = 0, 0, "identical"
                else:
                    # 落后 = 主干可达但该分支不可达的提交数（有完整历史时精确，否则为下界）
                    ahead = 0
                    behind = len(main_ancestors - ancestors_of(sha))
                    status = "behind"
                cmp_block = {
                    "base": default_branch,
                    "base_sha": main_tip,   # 供增量复用判据比对「主干 tip 是否变过」
                    "ahead_by": ahead,
                    "behind_by": behind,
                    "status": status,
                    "merge_base": sha,
                    "commits": [],
                    "files": [],
                    "truncated": False,
                }
                # 历史不完整时 behind 只是下界，明确标注，避免内网误读为精确值
                if not history_complete and not same_tip:
                    cmp_block["behind_approx"] = True
                entry["compare"] = cmp_block
                branches.append(entry)
                if t_branch("分支对比", i, len(raw_branches)):
                    progress("分支对比", i, len(raw_branches), f"{name}（已包含于主干）")
                continue

            # 需要一次 compare：{主干tip}...{分支tip}
            # --skip-details 仍拉 ahead/behind/commits 骨架，只跳过 files（决策 a）
            cmp_block = None
            if main_tip and sha and main_tip != sha:
                try:
                    cmp_url = (
                        f"{API}/repos/{repo}/compare/"
                        f"{urllib.parse.quote(main_tip)}...{urllib.parse.quote(sha)}"
                    )
                    raw_cmp, _ = http_get(cmp_url, token)
                    n_branch_api += 1
                    c_commits = raw_cmp.get("commits") or []
                    total = raw_cmp.get("total_commits")
                    if total is None:
                        total = len(c_commits)
                    keep = c_commits[:max_branch_commits] if max_branch_commits > 0 else c_commits
                    cmp_block = {
                        "base": default_branch,
                        "base_sha": main_tip,   # 供增量复用判据比对「主干 tip 是否变过」
                        "ahead_by": raw_cmp.get("ahead_by"),
                        "behind_by": raw_cmp.get("behind_by"),
                        "status": raw_cmp.get("status"),
                        "merge_base": (raw_cmp.get("merge_base_commit") or {}).get("sha"),
                        "total_commits": total,
                        # 只保留列表展示所需字段（与 release compare 同一裁剪口径）
                        "commits": [
                            {
                                "sha": c.get("sha"),
                                "message": (c.get("commit") or {}).get("message") or "",
                                "author_name": ((c.get("commit") or {}).get("author") or {}).get("name"),
                                "author_date": ((c.get("commit") or {}).get("author") or {}).get("date"),
                                "author": slim_user(c.get("author")),
                                "parents": [p.get("sha") for p in (c.get("parents") or [])],
                            }
                            for c in keep
                        ],
                        # 截断判定要同时覆盖两种来源：
                        #   ① compare 单次返回有上限（total > 实际返回条数）
                        #   ② 本次按 --max-branch-commits 主动裁剪（total > 保留条数）
                        # 只看 ① 会在「独有提交 100 < N <= compare 上限」时漏报，
                        # 用户看到被裁后的条数却没有任何「已截断」提示。
                        "truncated": bool(total and total > len(keep)),
                        # 保留「因 API 上限而截断」的原始语义，供需要区分两种截断的调用方使用
                        "api_truncated": bool(total and total > len(c_commits)),
                        "files": [] if args.skip_details else [map_file(f) for f in (raw_cmp.get("files") or [])],
                    }
                    if not head and cmp_block["commits"]:
                        tip_c = next(
                            (c for c in cmp_block["commits"] if c.get("sha") == sha),
                            cmp_block["commits"][-1],
                        )
                        head = {
                            "message": tip_c.get("message") or "",
                            "author_name": tip_c.get("author_name"),
                            "author_date": tip_c.get("author_date"),
                        }
                except Exception as e:
                    log(f"  [warn] compare {default_branch}...{name} 失败: {e}")
                    cmp_block = {"base": default_branch, "error": str(e)}
            else:
                # 无法对比（无主干 tip / 与主干同一提交）：保留旧块或写空骨架
                cmp_block = (old or {}).get("compare") or {
                    "base": default_branch, "base_sha": main_tip, "ahead_by": None, "behind_by": None,
                    "status": None, "merge_base": None, "commits": [], "files": [],
                    "truncated": False,
                }

            entry["head"] = head or (old or {}).get("head")
            entry["compare"] = cmp_block
            branches.append(entry)
            if t_branch("分支对比", i, len(raw_branches)):
                progress("分支对比", i, len(raw_branches), name)

        log(
            f"  branches: {len(branches)}（compare 新拉 {n_branch_api}, 缓存复用 {n_branch_reuse}, "
            f"已包含于主干 {n_branch_contained}）"
            + ("　[本轮 branches 请求失败，数据来自上一轮快照]" if branches_failed else "")
        )
        meta["file_stats"]["branches"] = len(branches)
        meta["file_stats"]["branches_api"] = n_branch_api
        meta["file_stats"]["branches_reused"] = n_branch_reuse
        # 供诊断：本轮分支快照是否因请求失败而沿用上一轮
        meta["file_stats"]["branches_stale"] = branches_failed

    # --- graph.js：提交池（非主干节点 + 引用表） ---
    # 供查看器按 ref 沿 parents 回溯（Commit 页切换分支）与渲染分支树。
    # 语义：graph 是「当前分支 / 标签状态」的视图模型，每轮按线上重新派生，**不做跨轮累积**——
    # 上游删掉分支后，其独有提交也随之从图中消失（与 branches 的快照语义一致）。
    # 主干历史的事实来源仍是 commits.js（无限累积），已合并的提交不会因此丢失。
    graph_nodes: dict = {}
    graph_branches: dict = {}
    graph_tags: dict = {}
    graph_truncated = False

    for b in branches:
        if b.get("name") and b.get("sha"):
            graph_branches[b["name"]] = b["sha"]
    for t in tags:
        if t.get("name") and t.get("sha"):
            graph_tags[t["name"]] = t["sha"]

    # 主干已导出的 sha：这些提交不进 graph（查看器运行时把两个池合并）
    main_pool = {c.get("sha") for c in commits if c.get("sha")}

    def _add_compare_node(c: dict) -> None:
        sha = c.get("sha")
        if not sha or sha in main_pool or sha in graph_nodes:
            return
        graph_nodes[sha] = {
            "sha": sha,
            "message": c.get("message") or "",
            "author_name": c.get("author_name"),
            "author_date": c.get("author_date"),
            "author": c.get("author") or {},
            "parents": [p for p in (c.get("parents") or []) if p],
        }

    for b in branches:
        cmp_block = b.get("compare") or {}
        if cmp_block.get("truncated"):
            graph_truncated = True
        for c in cmp_block.get("commits") or []:
            _add_compare_node(c)

    def _add_stub(sha: str) -> None:
        """父提交/引用指向镜像外的提交：建占位节点，图上画虚线而不是假装连得上。"""
        if not sha or sha in main_pool or sha in graph_nodes:
            return
        graph_nodes[sha] = {
            "sha": sha,
            "message": "",
            "author_name": None,
            "author_date": None,
            "author": {},
            "parents": [],
            "stub": True,
        }

    # release compare 里的真实提交记录：这些 sha 不能建 stub，
    # 否则查看器 commitPool 的 Map 去重会让空 stub 遮蔽真实记录（老 tag 的提交全不可见）。
    # 注意 release compare 的 commits 不进 graph_nodes，故必须单独收集。
    release_known: set = set()
    for r in releases:
        for c in (r.get("compare") or {}).get("commits") or []:
            sha_c = c.get("sha")
            if sha_c:
                release_known.add(sha_c)

    def _add_stub_safe(sha: str) -> None:
        """建占位，但绝不覆盖「已有真实记录」的 sha。"""
        if sha and sha in release_known:
            return
        _add_stub(sha)

    # 引用指向的提交：不在主干池也不在节点池 → 占位（如 compare 失败的领先分支）
    for sha in list(graph_branches.values()) + list(graph_tags.values()):
        _add_stub_safe(sha)
    # 节点引用的父提交：缺失则占位（导出窗口外 / compare 截断）
    for node in list(graph_nodes.values()):
        for p in node.get("parents") or []:
            _add_stub_safe(p)

    # --- 分支独有 commit 的逐条 diff（预算控制 + 增量复用） ---
    # 复用上一轮 graph 节点里已抓到的 diff（按 sha 命中，不消耗预算）。
    prev_graph_nodes = {}
    for n in (prev.get("graph") or {}).get("nodes") or []:
        if isinstance(n, dict) and n.get("sha"):
            prev_graph_nodes[n["sha"]] = n

    def _node_has_diff(n: dict) -> bool:
        return bool(n.get("has_diff")) or ("files" in n)

    branch_diff_budget = int(getattr(args, "max_branch_diffs", 30) or 0)
    if args.skip_details:
        branch_diff_budget = 0
    n_bd_fetch = 0
    n_bd_reuse = 0
    n_bd_nodiff = 0

    # 只为真实节点（非 stub）补 diff，按时间新→旧，保证预算优先给最近的提交
    diff_targets = [
        n for n in graph_nodes.values()
        if not n.get("stub") and n.get("sha")
    ]
    diff_targets.sort(
        key=lambda n: (n.get("author_date") or "", n.get("sha") or ""),
        reverse=True,
    )
    stage_begin("分支 diff", len(diff_targets))
    t_bdiff = make_throttle(1)
    for i, node in enumerate(diff_targets, 1):
        sha = node["sha"]
        old = prev_graph_nodes.get(sha)
        if old is not None and _node_has_diff(old):
            # 命中缓存：整块复用，且**不计入预算**
            node["files"] = old.get("files") or []
            node["stats"] = old.get("stats") or {}
            node["has_diff"] = True
            n_bd_reuse += 1
            if t_bdiff("分支 diff", i, len(diff_targets)):
                progress("分支 diff", i, len(diff_targets), f"{short_sha(sha)}（缓存）")
            continue
        if n_bd_fetch >= branch_diff_budget:
            n_bd_nodiff += 1
            if t_bdiff("分支 diff", i, len(diff_targets)):
                progress("分支 diff", i, len(diff_targets), f"{short_sha(sha)}（超出预算）")
            continue
        try:
            raw_c, _ = http_get(f"{API}/repos/{repo}/commits/{sha}", token)
            node["files"] = [map_file(f) for f in (raw_c.get("files") or [])]
            node["stats"] = raw_c.get("stats") or {}
            node["has_diff"] = True
            n_bd_fetch += 1
        except Exception as e:
            log(f"  [warn] 分支 commit {short_sha(sha)} diff 失败: {e}")
            n_bd_nodiff += 1
        if t_bdiff("分支 diff", i, len(diff_targets)):
            progress("分支 diff", i, len(diff_targets), short_sha(sha))
    if not diff_targets:
        progress("分支 diff", 0, 0, "无分支独有提交")

    graph = {
        "default_branch": default_branch,
        "refs": {"branches": graph_branches, "tags": graph_tags},
        "nodes": list(graph_nodes.values()),
        "truncated": graph_truncated,
    }
    meta["file_stats"]["graph_nodes"] = len(graph_nodes)
    meta["file_stats"]["graph_truncated"] = graph_truncated
    meta["file_stats"]["branch_diffs"] = n_bd_fetch
    meta["file_stats"]["branch_diffs_reused"] = n_bd_reuse
    meta["file_stats"]["branch_diffs_skipped"] = n_bd_nodiff
    if not getattr(args, "no_branches", False):
        log(
            f"  graph: 节点 {len(graph_nodes)}（主干池 {len(main_pool)} 不重复存）, "
            f"refs 分支 {len(graph_branches)} / 标签 {len(graph_tags)}, truncated={graph_truncated}"
        )
        if diff_targets:
            log(
                f"  分支 commit diff: 新拉 {n_bd_fetch}, 缓存复用 {n_bd_reuse}, "
                f"未拉 {n_bd_nodiff}（预算 {branch_diff_budget}）"
            )

    # --- write data JS (file:// 兼容) ---
    log("写出 data/*.js ...")
    data_dir = out / "data"
    writes = {
        "meta": meta,
        "prs": prs,
        "issues": issues,
        "commits": commits,
        "tags": tags,
        "releases": releases,
        "files": {"tree": files_meta, "contents": contents, "truncated": truncated},
        "branches": branches,
        "graph": graph,
    }
    stage_begin("写出数据", len(writes))
    for i, (name, payload) in enumerate(writes.items(), 1):
        p = data_dir / f"{name}.js"
        p.write_text(js_assign(name, payload), encoding="utf-8")
        log(f"  {p.name}  {p.stat().st_size / 1024:.1f} KB")
        progress("写出数据", i, len(writes), p.name)

    # manifest for index.html script order
    (data_dir / "manifest.json").write_text(
        json.dumps(list(writes.keys()), ensure_ascii=False),
        encoding="utf-8",
    )

    log("")
    log("完成。请将整个目录拷入内网，双击 index.html 打开。")
    log(f"  路径: {out.resolve()}")
    return out


def main():
    ap = argparse.ArgumentParser(description="导出 GitHub 仓库为内网只读静态站")
    ap.add_argument("--repo", default="", help="owner/name，例如 octocat/hello-world")
    ap.add_argument("--out", default=None, help="输出目录，默认 gh-mirror-<repo>-<date>")
    ap.add_argument("--token", default=None, help="GitHub Token（也可用环境变量 GITHUB_TOKEN）")
    ap.add_argument("--force", action="store_true", help="覆盖已存在的输出目录")
    ap.add_argument("--incremental", action="store_true", default=True,
                    help="增量导出：复用上次 data 缓存，只拉有变化的 PR/Issue/Commit/文件（默认开启）")
    ap.add_argument("--full", dest="incremental", action="store_false",
                    help="强制全量重新拉取，不使用缓存")
    ap.add_argument("--max-prs", type=int, default=0, help="最多导出多少个 PR；0=全部")
    ap.add_argument("--max-issues", type=int, default=0, help="最多导出多少个 Issue；0=全部")
    ap.add_argument("--max-commits", type=int, default=100,
                    help="每轮从 GitHub 往回爬取的 commit 数（默认 100；缓存无限累积，旧的不删）")
    ap.add_argument("--max-tags", type=int, default=0, help="最多导出多少个 tag/release；0=全部")
    ap.add_argument("--max-branches", type=int, default=100,
                    help="最多导出多少个分支（默认 100；0=全部）")
    ap.add_argument("--max-branch-commits", type=int, default=100,
                    help="每个分支最多保留多少条「独有提交」（默认 100；0=全部）")
    ap.add_argument("--max-branch-diffs", type=int, default=30,
                    help="本轮最多为多少条分支独有 commit 拉逐条 diff（默认 30；0=不拉）")
    ap.add_argument("--no-branches", action="store_true",
                    help="完全不导出分支数据（branches.js 写空数组，查看器自动降级）")
    ap.add_argument("--max-file-bytes", type=int, default=400_000)
    ap.add_argument("--skip-details", action="store_true", help="不拉 PR/Issue 的 diff 与评论（更快）")
    ap.add_argument("--no-contents", dest="include_contents", action="store_false", default=True,
                    help="不缓存源码文件内容（只保留文件树）")
    ap.add_argument("--max-commit-diffs", type=int, default=None,
                    help="本轮最多新拉多少条 commit 的文件 diff；默认与 --max-commits 相同")
    ap.add_argument("--no-assets", action="store_true",
                    help="不下载图片资源（README / PR / Issue 正文图片将无法离线显示）")
    args = ap.parse_args()

    if not args.out:
        slug = args.repo.replace("/", "-")
        date = datetime.now().strftime("%Y%m%d")
        args.out = f"gh-mirror-{slug}-{date}"

    try:
        export(args)
    except KeyboardInterrupt:
        log("\n已取消")
        sys.exit(130)
    except Exception as e:
        log(f"\n导出失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
