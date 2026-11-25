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
          // 按文件累计：一个目录可能同时缺多个文件，只记第一个会漏报
          (failed[folder] = failed[folder] || []).push(name);
        }
      }
    }
  }

  /* ---------- 缺失文件 ---------- */
  function missingText(folder) {
    const names = failed[folder];
    if (!names || !names.length) return "";
    return `缺少 ${names.map((n) => n + ".js").join("、")}`;
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

  /* ---------- 逐日索引（贡献墙用） ---------- */
  /* 口径与项目页 getActivityIndex() 完全一致：
   *   Commit  → author_date（缺则 committer_date）
   *   PR      → created_at
   *   Issue   → created_at
   *   Release → published_at（缺则 created_at）
   *   Tag     → 同名 Release 的日期；找不到则用 tag 指向的 commit 日期；
   *             两者都没有就不计入墙（并在页面注明多少个 Tag 日期未知）
   */
  let activityCache = null;

  function commitBySha() {
    const map = new Map();
    for (const folder of ORDER) {
      const s = store[folder] || {};
      for (const c of Array.isArray(s.commits) ? s.commits : []) {
        if (c && c.sha && !map.has(c.sha)) map.set(c.sha, c);
      }
    }
    return map;
  }

  function getActivityIndex() {
    if (activityCache) return activityCache;
    const map = new Map(); // "YYYY-MM-DD" -> { commit, pr, issue, tag, release, total }
    let tagUnknown = 0;

    const bump = (key, type) => {
      if (!key) return;
      let e = map.get(key);
      if (!e) {
        e = { commit: 0, pr: 0, issue: 0, tag: 0, release: 0, total: 0 };
        map.set(key, e);
      }
      e[type] += 1;
      e.total += 1;
    };

    const shaMap = commitBySha();

    for (const folder of ORDER) {
      const s = store[folder] || {};
      for (const c of Array.isArray(s.commits) ? s.commits : []) {
        bump(dayKey(c && (c.author_date || c.committer_date)), "commit");
      }
      for (const p of Array.isArray(s.prs) ? s.prs : []) bump(dayKey(p && p.created_at), "pr");
      for (const i of Array.isArray(s.issues) ? s.issues : []) bump(dayKey(i && i.created_at), "issue");
      for (const r of Array.isArray(s.releases) ? s.releases : []) {
        bump(dayKey(r && (r.published_at || r.created_at)), "release");
      }
    }

    // Tag 日期：按「同名 Release → 指向的 commit」两步回退
    for (const folder of ORDER) {
      const s = store[folder] || {};
      const releases = Array.isArray(s.releases) ? s.releases : [];
      const tags = Array.isArray(s.tags) ? s.tags : [];
      const relByTag = new Map();
      for (const r of releases) if (r) relByTag.set(r.tag_name || r.name, r);
      for (const t of tags) {
        if (!t) continue;
        let k = null;
        const rel = relByTag.get(t.name);
        if (rel) k = dayKey(rel.published_at || rel.created_at);
        if (!k && t.sha) {
          const c = shaMap.get(t.sha);
          if (c) k = dayKey(c.author_date || c.committer_date);
        }
        if (k) bump(k, "tag");
        else tagUnknown += 1;
      }
    }

    activityCache = { map, tagUnknown };
    return activityCache;
  }

  /* ---------- 打开中的 PR / Issue（跨仓库） ---------- */
  /* 与项目页同口径：PR 看 state==="open" && !merged；Issue 看 state==="open"。
   * draft 不排除——项目页也没排除，排除会让两页条数不一致。 */
  function openItems(kind) {
    const out = [];
    for (const folder of ORDER) {
      const s = store[folder] || {};
      const list = kind === "PR" ? s.prs : s.issues;
      for (const item of Array.isArray(list) ? list : []) {
        if (!item) continue;
        if (kind === "PR") {
          if (item.state === "open" && !item.merged) out.push({ folder, item });
        } else if (item.state === "open") {
          out.push({ folder, item });
        }
      }
    }
    // 最近更新的排前面（页面文案说的就是「更新于」）
    out.sort((a, b) => String(b.item.updated_at || "").localeCompare(String(a.item.updated_at || "")));
    return out;
  }

  function openListPanel(kind) {
    const items = openItems(kind);
    const head = `打开中的 ${kind}（全部仓库）`;
    if (!items.length) {
      return `<div class="panel"><div class="panel-hd">${head}</div>
        <div class="panel-bd"><div class="empty">无打开中的 ${kind}</div></div></div>`;
    }
    const rows = items.map(({ folder, item }) => {
      const labels = (Array.isArray(item.labels) ? item.labels : [])
        .map((l) => `<span class="pill">${esc(l && l.name)}</span>`)
        .join("");
      return `<div class="row">
        <div class="row-main">
          <div class="row-title">#${esc(item.number)} ${esc(item.title)}</div>
          <div class="row-meta">
            <span class="pill pill-repo">${esc(folder)}</span>
            <span>${esc((item.user && item.user.login) || "unknown")}</span>
            <span>更新于 ${esc(fmtDate(item.updated_at))}</span>
            ${labels}
          </div>
        </div>
      </div>`;
    }).join("");
    return `<div class="panel"><div class="panel-hd">${head}
      <span class="muted small">· ${items.length} 条</span></div>
      <div class="panel-bd row-list">${rows}</div></div>`;
  }

  /* ---------- 贡献墙 ---------- */
  function levelForCount(n, positives) {
    if (!n || n <= 0) return 0;
    if (positives.length <= 3) {
      if (n <= 1) return 1;
      if (n <= 3) return 2;
      return n <= 6 ? 3 : 4;
    }
    const sorted = positives.slice().sort((a, b) => a - b);
    const quantile = (p) => {
      const idx = (sorted.length - 1) * p;
      const lo = Math.floor(idx);
      const hi = Math.ceil(idx);
      return sorted[lo] + (sorted[hi] - sorted[lo]) * (idx - lo);
    };
    const q1 = quantile(0.25);
    const q2 = quantile(0.5);
    const q3 = quantile(0.75);
    if (n <= q1) return 1;
    if (n <= q2) return 2;
    if (n <= q3) return 3;
    return 4;
  }

  function parseDayKey(key) {
    const [y, m, d] = String(key).split("-").map(Number);
    return new Date(Date.UTC(y, (m || 1) - 1, d || 1));
  }

  function wallRange(map) {
    const keys = Array.from(map.keys()).sort();
    const end = keys.length ? keys[keys.length - 1] : dayKeyOf(bjNow());
    const start = keys.length ? keys[0] : end;
    return { start, end };
  }

  function renderContribWall() {
    const { map, tagUnknown } = getActivityIndex();
    const { start, end } = wallRange(map);

    // 对齐到周日（与 GitHub / 项目页一致）
    const gridStart = parseDayKey(start);
    gridStart.setUTCDate(gridStart.getUTCDate() - gridStart.getUTCDay());
    const endD = parseDayKey(end);

    const positives = [];
    for (const v of map.values()) if (v.total > 0) positives.push(v.total);

    const weeks = [];
    const months = [];
    let prevMonth = null;
    const cursor = new Date(gridStart);
    while (cursor <= endD) {
      const col = [];
      for (let i = 0; i < 7; i++) {
        const k = dayKeyOf(cursor);
        const inRange = cursor <= endD;
        const n = inRange ? ((map.get(k) || { total: 0 }).total) : 0;
        const lv = inRange ? levelForCount(n, positives) : 0;
        const title = inRange ? `${k} · ${n} 次贡献` : "";
        col.push(`<span class="cw-cell lv${lv}" title="${esc(title)}"></span>`);
        cursor.setUTCDate(cursor.getUTCDate() + 1);
      }
      const colStart = new Date(gridStart);
      colStart.setUTCDate(colStart.getUTCDate() + weeks.length * 7);
      const mm = colStart.getUTCMonth();
      months.push(mm !== prevMonth ? `<span class="cw-month">${mm + 1}月</span>` : `<span class="cw-month"></span>`);
      prevMonth = mm;
      weeks.push(`<div class="cw-week">${col.join("")}</div>`);
    }

    const legend = [1, 2, 3, 4].map((lv) => `<span class="cw-cell lv${lv}"></span>`).join("");
    const unknown = tagUnknown
      ? `<div class="muted small" style="margin-top:8px">另有 ${tagUnknown} 个 Tag 日期未知，未计入热力图。</div>`
      : "";

    return `<div class="panel">
      <div class="panel-hd">总贡献墙
        <span class="muted small">· 北京时间（UTC+8）· 合并 ${ORDER.length} 个仓库</span></div>
      <div class="panel-bd">
        <div class="cw-scroll"><div class="cw-inner">
          <div class="cw-months">${months.join("")}</div>
          <div class="cw-weeks">${weeks.join("")}</div>
        </div></div>
        <div class="cw-legend muted small">少 ${legend} 多</div>
        ${unknown}
      </div>
    </div>`;
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
      ${p2 ? "" : `<div class="warn-box">正在加载 commits / tags / releases…（贡献墙、Commit 与 Release 数字稍后补齐）</div>`}
      ${p2 ? renderContribWall() : ""}
      ${openListPanel("PR")}
      ${openListPanel("Issue")}
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
