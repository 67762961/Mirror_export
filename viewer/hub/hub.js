/* 多仓库总览 · 查看器
 *
 * 与单个项目页（js/app.js）是两套独立程序：项目页服务「一个仓库」的浏览，
 * 本文件服务「多个仓库」的汇总。两者共用 css 令牌与数据格式，但不共享状态。
 *
 * 数据来源（file:// 下唯一可行方式，见 ARCHITECTURE.md）：
 *   - repos.js          清单，window.GH_HUB.order = ["项目文件夹", ...]
 *   - <文件夹>/data/*.js 由本页按需 <script> 注入后立刻取快照，再清空全局键
 * 不能用 fetch：file:// 下读本地 JSON 会被浏览器拒绝；也不能用 ES module。
 */
(function () {
  "use strict";

  const HUB = window.GH_HUB || {};
  const ORDER = Array.isArray(HUB.order) ? HUB.order.filter((x) => typeof x === "string") : [];

  /* 阶段一：出计数与打开中列表；阶段二：出贡献墙所需的逐日数据 */
  const PHASE1 = ["meta", "prs", "issues"];
  const PHASE2 = ["commits", "tags", "releases"];

  /* 北京时间（固定 UTC+8）——与项目页 app.js 的 TZ_OFFSET_MS 必须一致 */
  const TZ_OFFSET_MS = (8 * 60 + 0) * 60 * 1000;

  const store = Object.create(null); // 文件夹 -> { meta, prs, issues, ... }
  const failed = Object.create(null); // 文件夹 -> 失败的文件名

  const state = {
    readyPhase1: false,
    readyPhase2: false,
  };

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  /* ---------- 北京时间 ---------- */
  function bjNow() {
    return new Date(Date.now() + TZ_OFFSET_MS);
  }

  function dayKeyOf(d) {
    const y = d.getUTCFullYear();
    const m = String(d.getUTCMonth() + 1).padStart(2, "0");
    const day = String(d.getUTCDate()).padStart(2, "0");
    return `${y}-${m}-${day}`;
  }

  function dayKey(iso) {
    if (!iso) return null;
    try {
      const d = new Date(iso);
      if (Number.isNaN(d.getTime())) return null;
      return dayKeyOf(new Date(d.getTime() + TZ_OFFSET_MS));
    } catch {
      return null;
    }
  }

  function fmtDate(iso) {
    if (!iso) return "—";
    try {
      const d = new Date(iso);
      if (Number.isNaN(d.getTime())) return iso;
      const b = new Date(d.getTime() + TZ_OFFSET_MS);
      const p = (n) => String(n).padStart(2, "0");
      return `${b.getUTCFullYear()}/${b.getUTCMonth() + 1}/${b.getUTCDate()} ` +
        `${p(b.getUTCHours())}:${p(b.getUTCMinutes())}:${p(b.getUTCSeconds())}`;
    } catch {
      return iso;
    }
  }

  /* ---------- 加载 ---------- */
  /* 逐段编码：目录名可能含空格或 #，encodeURI 对这两种会静默失败 */
  function dataSrc(folder, name) {
    const segs = String(folder).split("/").filter(Boolean).map(encodeURIComponent);
    return segs.join("/") + "/data/" + name + ".js";
  }

  /* 注入一个 data/*.js 并在 onload 里立刻取快照：
   * 下一个文件会覆盖同名全局键，不立刻取会拿到别人的数据。 */
  function loadOne(folder, name) {
    return new Promise((resolve) => {
      const s = document.createElement("script");
      s.src = dataSrc(folder, name);
      s.onload = () => {
        const val = window.GH_DATA ? window.GH_DATA[name] : undefined;
        if (window.GH_DATA) window.GH_DATA[name] = null;
        resolve({ ok: true, val });
      };
      s.onerror = () => resolve({ ok: false, val: null });
      document.head.appendChild(s);
    });
  }

  async function loadPhase(names) {
    for (const folder of ORDER) {
      store[folder] = store[folder] || Object.create(null);
      for (const name of names) {
        const r = await loadOne(folder, name);
        if (r.ok) {
          store[folder][name] = r.val;
        } else {
          failed[folder] = failed[folder] || name;
        }
      }
    }
  }

  /* ---------- 缺失文件 ---------- */
  function missingText(folder) {
    const name = failed[folder];
    return name ? `缺少 ${name}.js` : "";
  }

  /* ---------- 汇总 ---------- */
  function repoRows() {
    return ORDER.map((folder) => {
      const s = store[folder] || {};
      const meta = s.meta || {};
      const prs = Array.isArray(s.prs) ? s.prs : [];
      const issues = Array.isArray(s.issues) ? s.issues : [];
      const commits = Array.isArray(s.commits) ? s.commits : [];
      return {
        folder,
        name: meta.full_name || meta.repo || folder,
        exportedAt: meta.exported_at || "",
        counts: {
          prs: prs.length,
          issues: issues.length,
          commits: commits.length,
          tags: Array.isArray(s.tags) ? s.tags.length : 0,
          releases: Array.isArray(s.releases) ? s.releases.length : 0,
        },
        openPrs: prs.filter((p) => p && p.state === "open" && !p.merged).length,
        openIssues: issues.filter((i) => i && i.state === "open").length,
        prLimit: (meta.limits && meta.limits.prs) || 0,
        missing: missingText(folder),
      };
    });
  }

  function totals(rows) {
    const t = { prs: 0, issues: 0, commits: 0, tags: 0, releases: 0, openPrs: 0, openIssues: 0 };
    for (const r of rows) {
      t.prs += r.counts.prs;
      t.issues += r.counts.issues;
      t.commits += r.counts.commits;
      t.tags += r.counts.tags;
      t.releases += r.counts.releases;
      t.openPrs += r.openPrs;
      t.openIssues += r.openIssues;
    }
    return t;
  }

  /* ---------- 渲染：总览 ---------- */
  /* 阶段一拿不到 commits/tags/releases，这些数字显示占位符而不是 0：
   * 显示 0 会被误读成「这个仓库确实没有提交」。 */
  const PENDING = "…";

  function statCell(n, label) {
    return `<div class="stat"><div class="stat-n">${n}</div><div class="stat-l">${esc(label)}</div></div>`;
  }

  function staleNote(rows) {
    const ts = rows.map((r) => r.exportedAt).filter(Boolean).sort();
    if (!ts.length) return "";
    const same = ts[0] === ts[ts.length - 1];
    const range = same
      ? esc(fmtDate(ts[0]))
      : `最早 ${esc(fmtDate(ts[0]))} · 最新 ${esc(fmtDate(ts[ts.length - 1]))}`;
    return `<div class="warn-box">本页是<b>混合快照</b>：${range}（北京时间）。` +
      `数字来自各仓库离线包，不代表 GitHub 当前状态。</div>`;
  }

  function repoTable(rows) {
    const p2 = state.readyPhase2;
    const body = rows.map((r) => {
      const limit = r.prLimit ? `<span class="muted small"> / ${r.prLimit}</span>` : "";
      const commitCell = p2 ? r.counts.commits : `<span class="muted">${PENDING}</span>`;
      const tagCell = p2 ? r.counts.tags : `<span class="muted">${PENDING}</span>`;
      const relCell = p2 ? r.counts.releases : `<span class="muted">${PENDING}</span>`;
      return `<tr>
        <td>${esc(r.name)}<div class="muted small mono">${esc(r.folder)}</div></td>
        <td class="num">${r.counts.prs}${limit}</td>
        <td class="num">${r.counts.issues}</td>
        <td class="num">${commitCell}</td>
        <td class="num">${tagCell}</td>
        <td class="num">${relCell}</td>
        <td class="num">${r.openPrs}</td>
        <td class="num">${r.openIssues}</td>
        <td class="small">${esc(fmtDate(r.exportedAt))}${r.missing ? `<div class="muted small">${esc(r.missing)}</div>` : ""}</td>
      </tr>`;
    }).join("");

    return `<div class="panel">
      <div class="panel-hd">仓库</div>
      <table class="repo-table">
        <thead><tr>
          <th>仓库</th><th>PR</th><th>Issue</th><th>Commit</th><th>Tag</th><th>Release</th>
          <th>打开 PR</th><th>打开 Issue</th><th>导出时间（北京时间）</th>
        </tr></thead>
        <tbody>${body}</tbody>
      </table>
    </div>`;
  }

  function renderOverview() {
    const rows = repoRows();
    const t = totals(rows);
    const p2 = state.readyPhase2;
    return `
      <h1 class="h1">全部仓库总览</h1>
      <p class="muted">${ORDER.length} 个离线镜像 · 合并统计 · 口径与各项目页一致</p>
      ${staleNote(rows)}
      <div class="stats">
        ${statCell(t.prs, "Pull Requests")}
        ${statCell(t.issues, "Issues")}
        ${statCell(p2 ? t.commits : PENDING, "Commits")}
        ${statCell(p2 ? t.releases : PENDING, "Releases")}
        ${statCell(t.openPrs, "打开中的 PR")}
        ${statCell(t.openIssues, "打开中的 Issue")}
      </div>
      ${p2 ? "" : `<div class="warn-box">正在加载 commits / tags / releases…（Commit 与 Release 数字稍后补齐）</div>`}
      ${repoTable(rows)}
    `;
  }

  /* ---------- 渲染入口 ---------- */
  function render() {
    const el = $("#content");
    if (!el) return;
    if (!ORDER.length) {
      el.innerHTML = `<div class="empty">repos.js 里没有任何仓库。<br/>` +
        `请先运行导出（带 --hub-root）或在 repos.js 中登记项目文件夹。</div>`;
      return;
    }
    el.innerHTML = renderOverview();
  }

  function renderFoot() {
    const foot = $("#hub-foot");
    if (foot) {
      foot.innerHTML = `内网只读 · 离线可用<br/>北京时间（UTC+8）<br/>${ORDER.length} 个镜像`;
    }
    const sub = $("#brand-sub");
    if (sub) {
      sub.textContent = !state.readyPhase1
        ? "加载中…"
        : state.readyPhase2 ? "已全部加载" : "已加载计数";
    }
  }

  function bindNav() {
    $$(".nav-item").forEach((btn) => {
      btn.addEventListener("click", () => {
        $$(".nav-item").forEach((b) => b.classList.toggle("active", b === btn));
        render();
      });
    });
  }

  /* ---------- 启动 ---------- */
  async function boot() {
    bindNav();
    render();
    renderFoot();

    if (!ORDER.length) return;

    await loadPhase(PHASE1);
    state.readyPhase1 = true;
    render();
    renderFoot();

    await loadPhase(PHASE2);
    state.readyPhase2 = true;
    render();
    renderFoot();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
