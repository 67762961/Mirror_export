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


def progress(stage: str, done: int, total: int, note: str = "") -> None:
    """人类可读进度 + 供 GUI 解析的机器行。"""
    total = max(0, int(total or 0))
    done = max(0, int(done or 0))
    pct = int(done * 100 / total) if total else 0
    bar = f"[{done}/{total} {pct:3d}%]"
    suffix = f" · {note}" if note else ""
    log(f"  {stage} {bar}{suffix}")
    print(f"__PROGRESS__{{\"stage\":\"{stage}\",\"done\":{done},\"total\":{total}}}", flush=True)


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
    for name in ("meta", "prs", "issues", "commits", "tags", "releases", "files", "branches"):
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
) -> tuple[int, int]:
    """把仓库里的图片落到 out/assets。返回 (新下载, 跳过复用)。"""
    assets_dir = out / "assets"
    assets_dir.mkdir(exist_ok=True)
    index = dict(prev_index or {})
    n_new = 0
    n_skip = 0
    for t in tree:
        path = t.get("path") or ""
        typ = t.get("type")
        size = t.get("size") or 0
        sha = t.get("sha") or ""
        if typ != "blob":
            continue
        ext = Path(path).suffix.lower()
        if ext not in IMAGE_EXT or size > max_bytes:
            continue
        if any(path.startswith(p) for p in SKIP_DIR_PREFIX):
            continue
        rel = asset_rel(path)
        dst = assets_dir / rel
        if (
            index.get(path) == sha
            and dst.exists()
            and dst.stat().st_size > 0
        ):
            n_skip += 1
            continue
        try:
            raw, _ = http_get(t["url"], token, accept="application/vnd.github.raw")
            data = raw if isinstance(raw, bytes) else str(raw).encode("utf-8", errors="replace")
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(data)
            index[path] = sha
            n_new += 1
            if n_new % 20 == 0:
                log(f"  图片资源: {n_new}")
        except Exception as e:
            log(f"  [warn] 图片 {path} 失败: {e}")
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

    for url in urls:
        if not url or url.startswith("data:"):
            continue
        # 相对路径交给查看器按 assets/<path> 处理；此处只管绝对 URL
        if not re.match(r"^https?://", url, re.I):
            continue
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
            if n_new % 20 == 0:
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
    for rel in ("index.html", "css/app.css", "js/app.js"):
        src = viewer_src / rel
        if not src.exists():
            raise FileNotFoundError(f"缺少 viewer 文件: {src}")
        dst = out / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(src.read_bytes())


def asset_rel(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")


def export(args) -> Path:
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
            "file_bytes": args.max_file_bytes,
        },
    }

    # --- tags & releases ---
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

    # --- release compare: commits / files / 关联 PR（增量：已有则复用） ---
    log("为每个 Release 拉取对比 ...")
    prev_releases = {
        r.get("tag_name"): r for r in (prev.get("releases") or []) if r.get("tag_name")
    }
    # tags 新→旧；每个 release 对上一个 tag 做 compare
    tag_names = [t.get("name") for t in tags if t.get("name")]
    for i, rel in enumerate(releases):
        tag = rel.get("tag_name")
        old = prev_releases.get(tag)
        if (
            incremental
            and old
            and old.get("compare") is not None
            and old.get("published_at") == rel.get("published_at")
        ):
            rel["compare"] = old["compare"]
            rel["related_prs"] = old.get("related_prs") or []
            continue
        base = tag_names[i + 1] if i + 1 < len(tag_names) else None
        compare = {"base": base, "head": tag, "commits": [], "files": [], "total_commits": 0, "truncated": False}
        if base and not args.skip_details:
            try:
                cmp_url = f"{API}/repos/{repo}/compare/{urllib.parse.quote(base)}...{urllib.parse.quote(tag)}"
                raw_cmp, _ = http_get(cmp_url, token)
                c_commits = raw_cmp.get("commits") or []
                compare["total_commits"] = raw_cmp.get("total_commits") or len(c_commits)
                # 只保留列表展示需要的字段
                compare["commits"] = [
                    {
                        "sha": c.get("sha"),
                        "message": (c.get("commit") or {}).get("message") or "",
                        "author_name": ((c.get("commit") or {}).get("author") or {}).get("name"),
                        "author_date": ((c.get("commit") or {}).get("author") or {}).get("date"),
                        "author": slim_user(c.get("author")),
                    }
                    for c in c_commits[:100]
                ]
                compare["files"] = [map_file(f) for f in (raw_cmp.get("files") or [])[:200]]
                compare["ahead_by"] = raw_cmp.get("ahead_by")
                compare["behind_by"] = raw_cmp.get("behind_by")
                compare["status"] = raw_cmp.get("status")
            except Exception as e:
                log(f"  [warn] compare {base}...{tag} 失败: {e}")
                compare["error"] = str(e)
        rel["compare"] = compare
        # 从 body 里抽 #123 关联已导出的 PR（稍后回填）
        body = rel.get("body") or ""
        nums = sorted({int(x) for x in re.findall(r"#(\d+)", body)})
        rel["related_pr_nums"] = nums
        log(f"  release {tag}: base={base}, commits={compare.get('total_commits', 0)}")

    log(f"  releases 对比完成")

    # --- commits：无限累积缓存；每轮从 HEAD 往回爬 max_commits，与本地合并，不丢历史 ---
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
        if args.skip_details:
            return False
        if old is None:
            return True
        if old.get("has_diff"):
            return False
        if "files" in old:
            return False
        return True

    def has_diff_done(old: dict | None) -> bool:
        if not old:
            return False
        if old.get("has_diff"):
            return True
        return "files" in old

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
                msg0 = ((c.get("commit") or {}).get("message") or "").split("\n")[0]
                progress("Commit diff", n_c_fetch, need_diff_n, f"{short_sha(sha)} {msg0[:48]}")
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
        return entry

    # 1) 处理本轮 API 窗口（新→旧）
    seen2 = set()
    for c in raw_commits:
        sha = c.get("sha")
        if not sha or sha in seen2:
            continue
        seen2.add(sha)
        old = prev_commits.get(sha) if incremental else None
        commits.append(build_entry(c, old))

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
    log("拉取 pull requests ...")
    # 按编号（创建顺序）从新到旧拉取，保证数量上限时优先保留高编号
    raw_prs = paginate(f"{API}/repos/{repo}/pulls?state=all&sort=created&direction=desc", token, args.max_prs, "prs")
    prev_prs = {p.get("number"): p for p in (prev.get("prs") or []) if p.get("number")}
    prs = []
    n_pr_reuse = 0
    n_pr_fetch = 0
    for i, pr in enumerate(raw_prs, 1):
        num = pr.get("number")
        old = prev_prs.get(num)
        if (
            incremental
            and not args.skip_details
            and old
            and old.get("updated_at") == pr.get("updated_at")
            and old.get("files") is not None
        ):
            prs.append(old)
            n_pr_reuse += 1
            continue
        files = []
        comments = []
        pr_commits = []
        if not args.skip_details:
            try:
                raw_files = paginate(f"{API}/repos/{repo}/pulls/{num}/files", token, 300, f"pr#{num} files")
                files = [map_file(f) for f in raw_files]
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
                except Exception as e:
                    log(f"  [warn] PR#{num} comments 失败: {e}")
            n_pr_fetch += 1
            progress("PR 详情", i, len(raw_prs), f"#{num} {str(pr.get('title') or '')[:40]}")
        entry = map_pr(pr, files, comments)
        entry["merged"] = bool(pr.get("merged_at"))
        entry["commits_list"] = pr_commits
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
    log("拉取 issues ...")
    # 按编号（创建顺序）从新到旧拉取；仍过滤 pull_request
    raw_issues_all = paginate(f"{API}/repos/{repo}/issues?state=all&sort=created&direction=desc", token, args.max_issues * 2, "issues")
    prev_issues = {i.get("number"): i for i in (prev.get("issues") or []) if i.get("number")}
    issues = []
    n_i_reuse = 0
    n_i_fetch = 0
    for item in raw_issues_all:
        if item.get("pull_request"):
            continue
        num = item.get("number")
        old = prev_issues.get(num)
        if (
            incremental
            and not args.skip_details
            and old
            and old.get("updated_at") == item.get("updated_at")
            and old.get("issue_comments") is not None
        ):
            issues.append(old)
            n_i_reuse += 1
            if len(issues) >= args.max_issues:
                break
            continue
        comments = []
        if not args.skip_details and (item.get("comments") or 0) > 0:
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
            except Exception as e:
                log(f"  [warn] Issue#{num} comments 失败: {e}")
            n_i_fetch += 1
        issues.append(map_issue(item, comments))
        # 继承缓存里的 closed_by_pr（避免每次重查 timeline）
        if incremental and old and "closed_by_pr" in old:
            issues[-1]["closed_by_pr"] = old["closed_by_pr"]
        if len(issues) >= args.max_issues:
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
        for idx, iss in enumerate(need_timeline, 1):
            n = iss.get("number")
            try:
                events, _ = http_get(f"{API}/repos/{repo}/issues/{n}/timeline", token)
                if not isinstance(events, list):
                    continue
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
            if idx % 10 == 0:
                log(f"  timeline: {idx}/{len(need_timeline)}")

    for iss in issues:
        n = iss.get("number")
        if n in closed_by_map:
            iss["closed_by_pr"] = closed_by_map[n]
        elif "closed_by_pr" not in iss:
            iss["closed_by_pr"] = None
    log(f"  closed_by 命中 {sum(1 for i in issues if i.get('closed_by_pr'))} 条")

    # --- file tree + text contents（按 blob sha 增量） ---
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

    files_meta = []
    contents = {}
    text_count = 0
    text_reuse = 0
    skipped = 0
    for t in tree_items:
        path = t.get("path") or ""
        typ = t.get("type")
        if any(path.startswith(p) or f"/{p}" in f"/{path}" for p in SKIP_DIR_PREFIX):
            continue
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
                continue
            try:
                raw, headers = http_get(t["url"], token, accept="application/vnd.github.raw")
                if isinstance(raw, bytes):
                    text = raw.decode("utf-8", errors="replace")
                else:
                    text = str(raw)
                contents[path] = text
                text_count += 1
                if text_count % 50 == 0:
                    log(f"  文件内容: {text_count}")
            except Exception as e:
                skipped += 1
                log(f"  [warn] 读取 {path} 失败: {e}")
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
            repo, token, tree_items, out, prev_index=prev_asset_index if incremental else None
        )
        meta["file_stats"]["images"] = img_new
        meta["file_stats"]["images_reused"] = img_skip
        # PR/Issue/Release 正文中的外链图片（user-attachments 等）
        body_urls = collect_body_image_urls(prs, issues, releases)
        if body_urls:
            log(f"下载正文图片 ({len(body_urls)} 个 URL) ...")
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
            )
            meta["image_map"] = image_map
            meta["file_stats"]["body_images"] = b_new
            meta["file_stats"]["body_images_reused"] = b_skip
            meta["file_stats"]["body_images_failed"] = b_fail
        else:
            meta["file_stats"]["body_images"] = 0
            meta["file_stats"]["body_images_reused"] = 0
            meta["file_stats"]["body_images_failed"] = 0
    else:
        meta["file_stats"]["images"] = 0
        meta["file_stats"]["images_reused"] = 0
        meta["file_stats"]["body_images"] = 0
        meta["file_stats"]["body_images_reused"] = 0
        meta["file_stats"]["body_images_failed"] = 0
        meta["image_map"] = {}

    # --- branches (轻量，用于筛选) ---
    log("拉取 branches ...")
    try:
        raw_branches = paginate(f"{API}/repos/{repo}/branches", token, 50, "branches")
        branches = [
            {
                "name": b.get("name"),
                "sha": (b.get("commit") or {}).get("sha"),
                "protected": b.get("protected"),
            }
            for b in raw_branches
        ]
    except Exception as e:
        log(f"  [warn] branches 失败: {e}")
        branches = []

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
    }
    for name, payload in writes.items():
        p = data_dir / f"{name}.js"
        p.write_text(js_assign(name, payload), encoding="utf-8")
        log(f"  {p.name}  {p.stat().st_size / 1024:.1f} KB")

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
