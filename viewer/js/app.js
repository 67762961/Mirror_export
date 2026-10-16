/* GitHub 只读镜像 · 离线查看器 */
(function () {
  "use strict";

  const D = window.GH_DATA || {};
  const state = {
    view: "overview",
    prFilter: "all",
    issueFilter: "open",
    prDetail: null,
    issueDetail: null,
    commitDetail: null,
    commitRef: null,
    // Commit 页：显示档位（150/300/600/0=全部）与当前显示条数（「加载更多」累加）
    commitLimit: 150,
    commitRows: 150,
    releaseDetail: null,
    branchDetail: null,
    // 分支树：显示档位（150/300/600/0=全部）与当前显示条数（「加载更多」累加）
    graphLimit: 150,
    graphRows: 150,
    search: "",
    filePath: null,
    showMdRaw: false,
    compareBase: null,
    compareHead: null,
    // 版本对比结果列表：显示档位与当前条数（与分支树/Commit 页同一套模式）
    compareLimit: 150,
    compareRows: 150,
    rangeKey: "7",
    rangeCustomDays: 30,
  };
  // 换页导航历史（栈底 → 栈顶）；过滤/搜索/compare 不入栈
  const navHistory = [];

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function fmtDate(iso) {
    if (!iso) return "—";
    try {
      const d = new Date(iso);
      if (Number.isNaN(d.getTime())) return iso;
      return d.toLocaleString("zh-CN", { hour12: false });
    } catch {
      return iso;
    }
  }

  function shortSha(sha) {
    return sha ? String(sha).slice(0, 7) : "";
  }

  function fmtSize(n) {
    if (n == null) return "";
    if (n < 1024) return n + " B";
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
    return (n / 1024 / 1024).toFixed(2) + " MB";
  }

  /* ---------- issue/PR refs + internal GitHub links ---------- */
  function findIssueOrPr(num) {
    num = Number(num);
    const pr = (D.prs || []).find((p) => p.number === num);
    if (pr) return { type: "pr", item: pr };
    const iss = (D.issues || []).find((i) => i.number === num);
    if (iss) return { type: "issue", item: iss };
    return null;
  }

  function refHtml(num) {
    const hit = findIssueOrPr(num);
    const type = hit ? hit.type : "unknown";
    return `<a href="#${type}-${num}" class="issue-ref" data-ref="${num}" data-ref-type="${type}">#${num}</a>`;
  }

  function linkifyRefs(html) {
    // #123 → 站内链接（跳过已有 data-ref、代码块内的 #）
    return String(html).replace(/(^|[\s(>])#(\d{1,7})\b/g, (m, pre, num) => {
      // 避免匹配已经生成的 data-ref="#..."
      return pre + refHtml(num);
    });
  }

  function rewriteInternalUrls(html) {
    // github.com/owner/repo/pull|issues|commit|releases → 站内 data-*
    const repo = (D.meta && (D.meta.repo || D.meta.full_name)) || "";
    const ownerName = String(repo);
    return String(html)
      .replace(
        new RegExp(`https?://github\\.com/${ownerName.replace("/", "\\/")}\/pull\\/(\\d+)`, "gi"),
        (_, n) => `<a href="#pr-${n}" class="issue-ref" data-ref="${n}" data-ref-type="pr">#${n}</a>`
      )
      .replace(
        new RegExp(`https?://github\\.com/${ownerName.replace("/", "\\/")}\/issues\\/(\\d+)`, "gi"),
        (_, n) => `<a href="#issue-${n}" class="issue-ref" data-ref="${n}" data-ref-type="issue">#${n}</a>`
      )
      .replace(
        new RegExp(`https?://github\\.com/${ownerName.replace("/", "\\/")}\/commit\\/(\\w{7,40})`, "gi"),
        (_, sha) => {
          // 合并池：主干 + 分支独有 + PR 内 + Release compare（原先只认主干，分支提交点不动）
          const hit = findCommitAnywhere(sha);
          const known = !!hit;
          return known
            ? `<a href="#commit-${sha}" class="issue-ref" data-commit-ref="${sha}" title="查看 commit"><code>${sha.slice(0, 7)}</code></a>`
            : `<code title="镜像中无此 commit 详情">${sha.slice(0, 7)}</code>`;
        }
      )
      .replace(
        new RegExp(`https?://github\\.com/${ownerName.replace("/", "\\/")}\/releases\\/tag\\/([\\w.\\-]+)`, "gi"),
        (_, tag) => {
          const known = (D.releases || []).some((r) => r.tag_name === tag || r.name === tag);
          return known
            ? `<a href="#release-${tag}" class="issue-ref" data-release-ref="${tag}">${esc(tag)}</a>`
            : esc(tag);
        }
      )
      // 其它仓库的 pull/issue 链接保持外链
      .replace(
        /https?:\/\/github\.com\/([^/\s]+)\/([^/\s]+)\/pull\/(\d+)/gi,
        (m, o, r, n) => (o + "/" + r === ownerName ? m : m)
      );
  }

  function processRichHtml(html) {
    return rewriteInternalUrls(rewriteMappedImgSrcs(linkifyRefs(html)));
  }

  /** 把 HTML <img src> 中已下载的外链映射为 assets 本地路径，并规范展示属性 */
  function rewriteMappedImgSrcs(html) {
    return String(html).replace(/<img\b[^>]*>/gi, (tag) => normalizeImgTag(tag));
  }

  function lookupImageMap(src, map) {
    if (!src || !map) return null;
    if (map[src]) return map[src];
    const bare = String(src).split("#")[0].split("?")[0];
    if (map[bare]) return map[bare];
    return null;
  }

  /** 规范 <img>：映射本地路径、去掉原始 width/height、补 md-img 与预览提示 */
  function normalizeImgTag(tag) {
    const map = (D.meta && D.meta.image_map) || {};
    let src = "";
    const srcM = /\bsrc\s*=\s*(["'])([^"']+)\1/i.exec(tag) || /\bsrc\s*=\s*([^\s>]+)/i.exec(tag);
    if (srcM) src = srcM[2] != null ? srcM[2] : srcM[1];
    const mapped = lookupImageMap(mdHref(src), map);
    const finalSrc = mapped ? "assets/" + mapped : src;
    let alt = "";
    const altM = /\balt\s*=\s*(["'])([^"']*)\1/i.exec(tag);
    if (altM) alt = altM[2];
    const full = finalSrc || src || "";
    return (
      `<img class="md-img" src="${esc(finalSrc)}" alt="${esc(alt)}"` +
      ` data-full-src="${esc(full)}" loading="lazy"` +
      ` title="${esc(alt ? alt + " · 右键新标签打开原图" : "右键新标签打开原图")}"` +
      ` onerror="this.classList.add('md-img-broken')" />`
    );
  }

  /** 表格单元格：图片包一层等高容器，便于并列对比 */
  function wrapImgCell(html) {
    const s = String(html || "");
    const hasImg = /<img\b/i.test(s);
    // restore 前可能是 HB 占位符（\u0000HB0\u0000）
    const hasHb = /\u0000HB\d+\u0000/.test(s);
    if (!hasImg && !hasHb) return s;
    if (/class="md-img-cell"/.test(s)) return s;
    return `<div class="md-img-cell">${s}</div>`;
  }

  function mdImgHtml(src, alt) {
    const local = mdSrc(src);
    const full = local;
    return (
      `<img class="md-img" src="${local}" alt="${esc(alt || "")}" loading="lazy"` +
      ` data-full-src="${esc(full)}"` +
      ` title="${esc(alt ? alt + " · 右键新标签打开原图" : "右键新标签打开原图")}"` +
      ` onerror="this.classList.add('md-img-broken')">`
    );
  }

  /* ---------- lightweight Markdown (GitHub-like) ---------- */
  function mdInline(text) {
    let s = esc(decodeEntities(text));
    // protect code spans first
    const codes = [];
    s = s.replace(/`([^`]+)`/g, (_, c) => {
      codes.push(c);
      return `\u0000C${codes.length - 1}\u0000`;
    });
    // images（支持 ![alt](url) 与 ![alt](<url>)；后者在 esc 后变成 &lt;url&gt;）
    s = s.replace(/!\[([^\]]*)\]\(\s*(&lt;[^&]+?&gt;|<[^>]+>|[^)\s]+)\s*\)/g, (_, alt, src) => {
      return mdImgHtml(src, alt);
    });
    // markdown links
    s = s.replace(/\[([^\]]+)\]\(\s*(&lt;[^&]+?&gt;|<[^>]+>|[^)\s]+)\s*\)/g, (_, label, href) => {
      const h = mdHref(href);
      return `<a href="${esc(h)}" class="md-link" data-md-href="${esc(h)}">${label}</a>`;
    });
    // autolinks / bare urls → 同上
    s = s.replace(/&lt;(https?:\/\/[^\s&]+)&gt;/g, (_, u) => `<a href="${esc(u)}" class="md-link" data-md-href="${esc(u)}">${esc(u)}</a>`);
    s = s.replace(/(^|[\s(])((?:https?:\/\/)[^\s<)]+)/g, (_, p, u) => `${p}<a href="${esc(u)}" class="md-link" data-md-href="${esc(u)}">${esc(u)}</a>`);
    // issue refs (not in code)
    s = s.replace(/(^|[\s(])#(\d{1,7})\b/g, (_, p, n) => p + refHtml(n));
    // @mentions
    s = s.replace(/(^|[\s(])@([A-Za-z0-9\-]{2,40})\b/g, (_, p, u) => p + `<span class="mention">@${u}</span>`);
    // strikethrough / bold / italic
    s = s.replace(/~~([^~]+)~~/g, "<del>$1</del>");
    s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    s = s.replace(/(?<!\*)\*([^*]+)\*(?!\*)/g, "<em>$1</em>");
    s = s.replace(/(^|[^_\w])_([^_]+)_(?![_\w])/g, "$1<em>$2</em>");
    // restore code
    s = s.replace(/\u0000C(\d+)\u0000/g, (_, i) => `<code>${codes[Number(i)]}</code>`);
    return s;
  }

  function mdHref(href) {
    href = String(href || "").trim();
    // 原始 <url> 或 esc 之后的 &lt;url&gt;
    if (href.startsWith("&lt;") && href.endsWith("&gt;")) {
      href = href.slice(4, -4).trim();
    } else if (href.charAt(0) === "<" && href.charAt(href.length - 1) === ">") {
      href = href.slice(1, -1).trim();
    }
    return href;
  }

  function decodeEntities(s) {
    // em/ensp/nbsp 用 Unicode 空格，避免被当成 4 空格缩进代码块
    return String(s)
      .replace(/&emsp;/gi, " ")
      .replace(/&ensp;/gi, " ")
      .replace(/&nbsp;/gi, " ")
      .replace(/&thinsp;/gi, " ")
      .replace(/&zwnj;/gi, "")
      .replace(/&zwj;/gi, "")
      .replace(/&#8195;/g, " ")
      .replace(/&#8194;/g, " ")
      .replace(/&#160;/g, " ")
      .replace(/&lt;/g, "<")
      .replace(/&gt;/g, ">")
      .replace(/&quot;/g, '"')
      .replace(/&#39;/g, "'")
      .replace(/&apos;/g, "'")
      .replace(/&amp;/g, "&");
  }

  function mdSrc(src) {
    src = mdHref(src);
    const map = (D.meta && D.meta.image_map) || {};
    const mapped = lookupImageMap(src, map);
    if (mapped) return esc("assets/" + mapped);
    if (/^(https?:|data:)/i.test(src)) return esc(src);
    const clean = src.replace(/^\.\//, "").replace(/^\//, "");
    return esc("assets/" + clean);
  }

  /** 在 markdown 渲染后，把 GitHub 外链改写成站内跳转 */
  function upgradeMdLinks(rootHtml) {
    const repo = (D.meta && (D.meta.repo || D.meta.full_name)) || "";
    if (!repo) return rootHtml;
    const ownerName = repo.replace("/", "/");
    const patterns = [
      {
        re: new RegExp(`https?://github\\.com/${ownerName.replace("/", "\\/")}\/pull\\/(\\d+)`, "gi"),
        to: (_, n) => `#pr-${n}`,
        type: "pr",
      },
      {
        re: new RegExp(`https?://github\\.com/${ownerName.replace("/", "\\/")}\/issues\\/(\\d+)`, "gi"),
        to: (_, n) => `#issue-${n}`,
        type: "issue",
      },
      {
        re: new RegExp(`https?://github\\.com/${ownerName.replace("/", "\\/")}\/commit\\/(\\w{7,40})`, "gi"),
        to: (_, sha) => `#commit-${sha}`,
        type: "commit",
      },
      {
        re: new RegExp(`https?://github\\.com/${ownerName.replace("/", "\\/")}\/releases\\/tag\\/([\\w.\\-]+)`, "gi"),
        to: (_, tag) => `#release-${tag}`,
        type: "release",
      },
      {
        // compare/a...b → 跳到 head release 或普通外链
        re: new RegExp(`https?://github\\.com/${ownerName.replace("/", "\\/")}\/compare\\/([\\w.\\-]+)\\.\\.\\.([\\w.\\-]+)`, "gi"),
        to: (_, a, b) => `#release-${b}`,
        type: "release",
      },
    ];
    // 替换 data-md-href 与 href 中的 github 链接
    let s = String(rootHtml);
    for (const p of patterns) {
      s = s.replace(p.re, (m, g1) => {
        const href = p.to(m, g1);
        if (p.type === "commit") {
          // 合并池：分支独有 / PR 内提交也能站内跳转
          const known = !!findCommitAnywhere(g1);
          if (!known) return m;
          return href;
        }
        if (p.type === "release") {
          const known = (D.releases || []).some((r) => r.tag_name === g1 || r.name === g1);
          if (!known) return m;
          return href;
        }
        return href;
      });
    }
    // 给已改写成 #pr- / #issue- 的链接加 data-ref
    s = s.replace(
      /href="#(pr|issue|commit|release)-([^"]+)"/g,
      (m, kind, id) => {
        if (kind === "pr" || kind === "issue") {
          return m + ` class="issue-ref" data-ref="${id}" data-ref-type="${kind}"`;
        }
        if (kind === "commit") {
          return m + ` class="issue-ref" data-commit-ref="${id}"`;
        }
        return m + ` class="issue-ref" data-release-ref="${id}"`;
      }
    );
    // 避免重复 class
    s = s.replace(/class="md-link"([^>]*)class="issue-ref"/g, 'class="issue-ref"$1');
    s = s.replace(/class="issue-ref"([^>]*)class="md-link"/g, 'class="issue-ref"$1');
    s = s.replace(/data-md-href="[^"]*"\s*/g, "");
    return s;
  }

  function isTableRow(line) {
    return line.trim().startsWith("|") && line.trim().length > 1 && line.indexOf("|") >= 0;
  }

  function indentWidth(line) {
    const m = /^[ ]*/.exec(line);
    return m ? m[0].length : 0;
  }

  function parseListMarker(line) {
    const m = /^([ ]*)(?:([-*+])|(\d+)[.)])\s+(.*)$/.exec(line);
    if (!m) return null;
    return { type: m[2] ? "ul" : "ol", indent: m[1].length, content: m[4] };
  }

  function parseAtxHeading(text) {
    const m = /^(#{1,6})\s+(.*?)\s*#*\s*$/.exec(text);
    if (!m) return null;
    return { level: m[1].length, content: m[2] };
  }

  function renderHeadingHtml(level, content, inList) {
    // 页面外层已有 h1，标题整体下移一级
    let lv = level + 1;
    if (lv < 1) lv = 1;
    if (lv > 6) lv = 6;
    return `<h${lv} class="md-h${lv}${inList ? " md-h-inlist" : ""}">${mdInline(content)}</h${lv}>`;
  }

  function renderListItemBody(content) {
    const h = parseAtxHeading(content);
    if (h) return renderHeadingHtml(h.level, h.content, true);
    const task = content.match(/^\[([ xX])\]\s+(.*)$/);
    if (task) {
      const done = task[1].toLowerCase() === "x";
      return `<input type="checkbox" disabled${done ? " checked" : ""}/> ${mdInline(task[2])}`;
    }
    return mdInline(content);
  }

  function mdToHtml(src) {
    if (src == null || src === "") return "";
    let text = decodeEntities(String(src)).replace(/\r\n/g, "\n").replace(/\t/g, "    ");
    text = text.replace(/<!--[\s\S]*?-->/g, "");
    const htmlBlocks = [];
    text = text.replace(/<(details|summary|div|table|thead|tbody|tr|td|th|p|ul|ol|li|pre|blockquote|figure|section)\b[\s\S]*?<\/\1>/gi, (m) => {
      htmlBlocks.push(m);
      return `\n\n HB${htmlBlocks.length - 1} \n\n`;
    });
    // img/br/hr 用行内占位（勿加换行），否则会打断 |图1|图2| 表格行
    text = text.replace(/<(br|hr|img)\b[^>]*\/?>/gi, (m) => {
      htmlBlocks.push(m);
      return ` HB${htmlBlocks.length - 1} `;
    });

    const lines = text.split("\n");
    const out = [];
    let i = 0;
    let para = [];
    let listStack = [];
    let quote = [];
    let code = null;
    let listPara = null;

    function restoreHtml(h) {
      return h.replace(/ HB(\d+) /g, (_, idx) => {
        let block = htmlBlocks[Number(idx)] || "";
        block = block
          .replace(/<!--[\s\S]*?-->/g, "")
          .replace(/<script[\s\S]*?<\/script>/gi, "")
          .replace(/<iframe[\s\S]*?<\/iframe>/gi, "");
        // 原始 HTML <img> 也走 image_map
        block = rewriteMappedImgSrcs(block);
        return block.replace(/(^|[\s(>])#(\d{1,7})\b/g, (mm, pre, n) => pre + refHtml(n));
      });
    }

    function flushPara() {
      if (para.length) {
        out.push(`<p>${restoreHtml(mdInline(para.join("\n"))).replace(/\n/g, "<br/>")}</p>`);
        para = [];
      }
    }

    function flushListPara() {
      if (!listPara || !listPara.lines.length) {
        listPara = null;
        return;
      }
      const html = `<p>${restoreHtml(mdInline(listPara.lines.join("\n"))).replace(/\n/g, "<br/>")}</p>`;
      appendToLastListItem(html);
      listPara = null;
    }

    function closeTopList() {
      const list = listStack.pop();
      const tag = list.type === "ol" ? "ol" : "ul";
      const html = `<${tag}>${list.items.map((it) => `<li>${it}</li>`).join("")}</${tag}>`;
      if (listStack.length) {
        const parent = listStack[listStack.length - 1];
        const last = parent.items[parent.items.length - 1] || "";
        parent.items[parent.items.length - 1] = last + html;
      } else {
        out.push(html);
      }
    }

    function flushList() {
      flushListPara();
      while (listStack.length) closeTopList();
    }

    function flushQuote() {
      if (quote.length) {
        out.push(`<blockquote>${mdToHtml(quote.join("\n"))}</blockquote>`);
        quote = [];
      }
    }

    function flushAll() {
      flushPara();
      flushList();
      flushQuote();
    }

    function appendToLastListItem(html) {
      if (!listStack.length) return;
      const top = listStack[listStack.length - 1];
      if (!top.items.length) return;
      top.items[top.items.length - 1] += html;
    }

    function openList(type, indent) {
      listStack.push({ type, indent, items: [] });
    }

    function closeListsDeeperThan(indent) {
      flushListPara();
      while (listStack.length && listStack[listStack.length - 1].indent > indent) {
        closeTopList();
      }
    }

    function pushListItem(type, indent, content) {
      closeListsDeeperThan(indent);
      if (!listStack.length) {
        openList(type, indent);
      } else {
        const top = listStack[listStack.length - 1];
        if (indent > top.indent) {
          openList(type, indent);
        } else if (top.type !== type) {
          closeTopList();
          openList(type, indent);
        }
      }
      listStack[listStack.length - 1].items.push(renderListItemBody(content));
    }

    function looksLikeListContinuation(line) {
      if (!listStack.length) return false;
      if (!line.trim()) return false;
      if (parseListMarker(line)) return false;
      const ind = indentWidth(line);
      // 比当前列表更深 → 当前项续写
      if (ind > listStack[listStack.length - 1].indent) return true;
      // 与内层 marker 同缩进、但相对外层列表仍缩进 → 挂到外层项（嵌套列表后的标题/正文）
      return listStack.some((l, idx) => idx < listStack.length - 1 && ind > l.indent);
    }

    function prepareListContinuation() {
      const ind = indentWidth(lines[i]);
      while (listStack.length && listStack[listStack.length - 1].indent >= ind) {
        closeTopList();
      }
    }

    function nextNonBlank(idx) {
      let j = idx;
      while (j < lines.length && !lines[j].trim()) j++;
      return j;
    }

    function blankStillInList(idx) {
      let j = nextNonBlank(idx);
      if (j >= lines.length) return false;
      const next = lines[j];
      const nm = parseListMarker(next);
      if (nm) {
        return listStack.some((l) => nm.indent >= l.indent);
      }
      const ind = indentWidth(next);
      return listStack.some((l) => ind > l.indent);
    }

    while (i < lines.length) {
      const line = lines[i];
      const trimmed = line.trim();

      const hb = trimmed.match(/^ HB(\d+) $/);
      if (hb) {
        if (listStack.length && looksLikeListContinuation(line)) {
          prepareListContinuation();
          flushListPara();
          appendToLastListItem(restoreHtml(` HB${hb[1]} `));
        } else {
          flushAll();
          out.push(restoreHtml(` HB${hb[1]} `));
        }
        i++;
        continue;
      }

      const fence = line.match(/^(\s*)(`{3,}|~{3,})(\S*)\s*$/);
      if (fence) {
        if (code) {
          const block = `<div class="md-codeblock"${code.lang ? ` data-lang="${esc(code.lang)}"` : ""}><pre><code>${esc(code.lines.join("\n"))}</code></pre></div>`;
          if (listStack.length && code.inList) appendToLastListItem(block);
          else out.push(block);
          code = null;
        } else {
          const inList = listStack.length > 0 && looksLikeListContinuation(line);
          if (!inList) flushAll();
          else flushListPara();
          code = { lang: fence[3] || "", lines: [], inList };
        }
        i++;
        continue;
      }
      if (code) {
        code.lines.push(line);
        i++;
        continue;
      }

      if (!trimmed) {
        if (listStack.length && blankStillInList(i + 1)) {
          flushListPara();
          i++;
          continue;
        }
        flushAll();
        i++;
        continue;
      }

      const listMark = parseListMarker(line);
      if (listMark) {
        flushPara();
        flushQuote();
        pushListItem(listMark.type, listMark.indent, listMark.content);
        i++;
        continue;
      }

      if (looksLikeListContinuation(line)) {
        flushQuote();
        prepareListContinuation();
        const h = parseAtxHeading(trimmed);
        if (h) {
          flushListPara();
          appendToLastListItem(renderHeadingHtml(h.level, h.content, true));
          i++;
          continue;
        }
        if (isTableRow(line)) {
          flushListPara();
          const rows = [];
          const splitRow = (l) =>
            l.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());
          const start = i;
          const headers = splitRow(line);
          i += 1;
          if (i < lines.length && /^\|[\s:|-]+\|?\s*$/.test(lines[i].trim())) i++;
          while (i < lines.length && isTableRow(lines[i]) && looksLikeListContinuation(lines[i])) {
            rows.push(splitRow(lines[i]));
            i++;
          }
          if (i === start + 1 && rows.length === 0) {
            i = start;
            if (!listPara) listPara = { lines: [] };
            listPara.lines.push(trimmed);
            i++;
          } else {
            appendToLastListItem(renderMdTable(headers, rows));
          }
          continue;
        }
        if (!listPara) listPara = { lines: [] };
        listPara.lines.push(trimmed);
        i++;
        continue;
      }

      const h = parseAtxHeading(trimmed);
      if (h) {
        flushAll();
        out.push(renderHeadingHtml(h.level, h.content, false));
        i++;
        continue;
      }

      if (/^(-{3,}|\*{3,}|_{3,})\s*$/.test(trimmed)) {
        flushAll();
        out.push("<hr/>");
        i++;
        continue;
      }

      if (isTableRow(line) && i + 1 < lines.length && /^\|[\s:|-]+\|?\s*$/.test(lines[i + 1].trim())) {
        flushAll();
        const rows = [];
        const splitRow = (l) =>
          l.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());
        const headers = splitRow(line);
        i += 2;
        while (i < lines.length && isTableRow(lines[i])) {
          rows.push(splitRow(lines[i]));
          i++;
        }
        out.push(renderMdTable(headers, rows));
        continue;
      }

      if (/^>\s?/.test(trimmed)) {
        flushPara();
        flushList();
        quote.push(trimmed.replace(/^>\s?/, ""));
        i++;
        continue;
      }

      if (/^ {4,}/.test(line) && !listStack.length && !para.length) {
        flushAll();
        const buf = [];
        while (i < lines.length && (/^ {4,}/.test(lines[i]) || !lines[i].trim())) {
          if (!lines[i].trim() && i + 1 < lines.length && !/^ {4,}/.test(lines[i + 1])) break;
          buf.push(lines[i].replace(/^ {4}/, ""));
          i++;
        }
        out.push(`<div class="md-codeblock"><pre><code>${esc(buf.join("\n"))}</code></pre></div>`);
        continue;
      }

      flushList();
      flushQuote();
      para.push(trimmed);
      i++;
    }
    if (code) {
      const block = `<div class="md-codeblock"${code.lang ? ` data-lang="${esc(code.lang)}"` : ""}><pre><code>${esc(code.lines.join("\n"))}</code></pre></div>`;
      if (code.inList && listStack.length) appendToLastListItem(block);
      else out.push(block);
    }
    flushAll();
    let html = out.join("\n");
    html = restoreHtml(html);
    return html;
  }

  function mdHtml(text) {
    const h = mdToHtml(text);
    if (!h || h === "") return `<div class="empty">（无内容）</div>`;
    return `<div class="markdown-body">${upgradeMdLinks(h)}</div>`;
  }

  /** Markdown 表格：图片单元格等高并列（|图1|图2| 对比表） */
  function renderMdTable(headers, rows) {
    const rawAll = headers.concat(...rows).join("\n");
    const cookedAll =
      headers.map((c) => mdInline(c)).join("") +
      rows.map((r) => r.map((c) => mdInline(c)).join("")).join("");
    const hasImg =
      /<img\b/i.test(cookedAll) ||
      /!\[|user-attachments|data-full-src|<img\b/i.test(rawAll) ||
      /\u0000HB\d+\u0000/.test(cookedAll + rawAll);
    const headCells = headers.map((c) => `<th>${wrapImgCell(mdInline(c))}</th>`).join("");
    const bodyRows = rows
      .map((r) => `<tr>${r.map((c) => `<td>${wrapImgCell(mdInline(c))}</td>`).join("")}</tr>`)
      .join("");
    const cls = hasImg ? "md-table md-img-compare" : "md-table";
    return (
      `<div class="md-table-wrap"><table class="${cls}">` +
      `<thead><tr>${headCells}</tr></thead><tbody>${bodyRows}</tbody></table></div>`
    );
  }

  function labelHtml(labels) {
    if (!labels || !labels.length) return "";
    return labels
      .map((l) => {
        const c = l.color || "6e7681";
        const r = parseInt(c.slice(0, 2), 16);
        const g = parseInt(c.slice(2, 4), 16);
        const b = parseInt(c.slice(4, 6), 16);
        const lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
        const fg = lum > 0.6 ? "#1f2328" : "#ffffff";
        return `<span class="label" style="background:#${esc(c)};color:${fg}">${esc(l.name)}</span>`;
      })
      .join(" ");
  }

  function prStateClass(pr) {
    if (pr.draft && pr.state === "open") return "state-draft";
    if (pr.merged) return "state-merged";
    if (pr.state === "open") return "state-open";
    return "state-closed";
  }

  function prStateText(pr) {
    if (pr.draft && pr.state === "open") return "Draft";
    if (pr.merged) return "Merged";
    if (pr.state === "open") return "Open";
    return "Closed";
  }

  function issueStateText(issue) {
    if (issue.state === "open") return "Open";
    if (issue.state_reason === "completed") return "Closed · completed";
    if (issue.state_reason === "not_planned") return "Closed · not planned";
    return "Closed";
  }

  function matchSearch(text) {
    if (!state.search) return true;
    return String(text || "").toLowerCase().includes(state.search.toLowerCase());
  }

  /* 按编号（创建顺序）从新到旧：大号在前 */
  function byNumberDesc(a, b) {
    return (b.number || 0) - (a.number || 0);
  }

  function loadAllData() {
    D.prs = D.prs || [];
    D.issues = D.issues || [];
    D.commits = D.commits || [];
    D.tags = D.tags || [];
    D.releases = D.releases || [];
    D.branches = D.branches || [];
    // graph.js：提交池 + 引用表（旧导出包没有此文件，缺省即降级）
    D.graph = D.graph || {};
    D.graph.refs = D.graph.refs || {};
    D.graph.refs.branches = D.graph.refs.branches || {};
    D.graph.refs.tags = D.graph.refs.tags || {};
    D.graph.nodes = D.graph.nodes || [];
    D.files = D.files || { tree: [], contents: {} };
    D.files.tree = D.files.tree || [];
    D.files.contents = D.files.contents || {};
    D.meta = D.meta || {};
    commitPoolCache = null;
  }

  /* graph.js 的节点池（主干提交在 commits.js 里，这里只补非主干节点） */
  function findGraphNode(sha) {
    if (!sha) return null;
    const nodes = (D.graph && D.graph.nodes) || [];
    return nodes.find((n) => n.sha === sha) || null;
  }

  /* 提交总池：主干（含 diff）→ graph 节点（分支独有，含 diff）→ PR 内 commit → Release compare。
     按此优先级先命中者字段更全，后两者只是「能点开」，通常没有 diff。 */
  let commitPoolCache = null;
  function commitPool() {
    if (commitPoolCache) return commitPoolCache;
    const map = new Map();
    const add = (c) => { if (c && c.sha && !map.has(c.sha)) map.set(c.sha, c); };
    (D.commits || []).forEach(add);
    ((D.graph && D.graph.nodes) || []).forEach(add);
    (D.prs || []).forEach((p) => (p.commits_list || []).forEach(add));
    (D.releases || []).forEach((r) => (((r.compare || {}).commits) || []).forEach(add));
    commitPoolCache = map;
    return map;
  }

  /* 按 sha 找提交；精确值未命中时容忍短 sha（站内链接可能只给前 7 位） */
  function findCommitAnywhere(sha) {
    if (!sha) return null;
    const pool = commitPool();
    const s = String(sha);
    if (pool.has(s)) return pool.get(s);
    for (const [k, v] of pool) {
      if (k.startsWith(s) || s.startsWith(k)) return v;
    }
    return null;
  }

  /* Commit 页的 ref 选项：分支（默认分支优先）+ 标签（新→旧）；旧包无 graph 时返回空 */
  function commitRefs() {
    const br = (D.graph && D.graph.refs && D.graph.refs.branches) || {};
    const tg = (D.graph && D.graph.refs && D.graph.refs.tags) || {};
    const def = (D.meta && D.meta.default_branch) || "";
    const out = [];
    Object.keys(br)
      .sort((a, b) => {
        if ((a === def) !== (b === def)) return a === def ? -1 : 1;
        return a.localeCompare(b);
      })
      .forEach((n) => out.push({ type: "branch", name: n, sha: br[n], isDefault: n === def }));
    // 标签按「指向的提交时间」新→旧排（与提交列表方向一致）；
    // 取不到时间时（如指向未导出提交）排在最后，内部再按名字倒序，保证顺序稳定。
    Object.keys(tg)
      .map((n) => {
        const c = findCommitAnywhere(tg[n]);
        return {
          name: n,
          sha: tg[n],
          date: (c && (c.author_date || c.committer_date)) || "",
        };
      })
      .sort((a, b) => {
        if (a.date !== b.date) return a.date < b.date ? 1 : -1;
        return b.name.localeCompare(a.name);
      })
      .forEach((t) => out.push({ type: "tag", name: t.name, sha: t.sha }));
    return out;
  }

  function refKey(r) { return r ? r.type + ":" + r.name : ""; }

  function currentCommitRef() {
    const refs = commitRefs();
    if (!refs.length) return null;
    return refs.find((r) => refKey(r) === state.commitRef) || refs[0];
  }

  function byDateDesc(a, b) {
    const ka = a.author_date || a.committer_date || "";
    const kb = b.author_date || b.committer_date || "";
    if (ka !== kb) return ka < kb ? 1 : -1;
    return String(b.sha || "").localeCompare(String(a.sha || ""));
  }

  /* 该 ref 可达的提交：从 tip 沿 parents 回溯（即 GitHub 的 reachable 语义） */
  function commitsForRef(ref) {
    if (!ref) return (D.commits || []).slice();
    // 默认分支沿用 commits.js 的原始顺序，保证与旧版观感一致
    if (ref.type === "branch" && ref.isDefault) return (D.commits || []).slice();
    const pool = commitPool();
    const seen = new Set();
    const out = [];
    const stack = [ref.sha];
    while (stack.length) {
      const sha = stack.pop();
      if (!sha || seen.has(sha)) continue;
      seen.add(sha);
      const c = pool.get(sha);
      if (!c) continue;
      if (!c.stub) out.push(c);
      (c.parents || []).forEach((p) => { if (p && !seen.has(p)) stack.push(p); });
    }
    out.sort(byDateDesc);
    return out;
  }

  function findReadmePath() {
    const contents = D.files.contents || {};
    const tree = D.files.tree || [];
    const candidates = Object.keys(contents).filter((p) => {
      const base = p.split("/").pop().toLowerCase();
      return base === "readme.md" || base === "readme.markdown" || base === "readme";
    });
    if (candidates.length) {
      // prefer root
      candidates.sort((a, b) => a.split("/").length - b.split("/").length);
      return candidates[0];
    }
    const hit = tree.find((t) => {
      if (t.type !== "blob") return false;
      const base = String(t.path).split("/").pop().toLowerCase();
      return base === "readme.md" || base === "readme.markdown" || base === "readme";
    });
    return hit ? hit.path : null;
  }

  /* ---------- hover card for #N ---------- */
  let hoverTimer = null;
  let hoverEl = null;

  function hideHover() {
    hoverEl = $("#hover-card");
    if (hoverEl) {
      hoverEl.hidden = true;
      hoverEl.innerHTML = "";
    }
  }

  function scheduleHideHover() {
    clearTimeout(hoverTimer);
    hoverTimer = setTimeout(hideHover, 180);
  }

  function showRefHover(anchor) {
    clearTimeout(hoverTimer);
    hoverTimer = setTimeout(() => {
      const num = Number(anchor.dataset.ref);
      const hit = findIssueOrPr(num);
      hoverEl = $("#hover-card");
      if (!hoverEl) return;
      if (!hit) {
        hoverEl.innerHTML = `<div class="hc-title">#${num} 不在镜像中</div><div class="hc-meta">导出时可能超出数量上限</div>`;
      } else if (hit.type === "pr") {
        const pr = hit.item;
        hoverEl.innerHTML = `
          <div class="hc-title">
            <span class="state-dot ${prStateClass(pr)}"></span>
            <span>#${pr.number} ${esc(pr.title)}</span>
          </div>
          <div class="hc-meta">
            <span class="pill ${pr.merged ? "pill-purple" : pr.state === "open" ? "pill-green" : "pill-red"}">${prStateText(pr)}</span>
            <span>${esc((pr.user && pr.user.login) || "")}</span>
            <span>${fmtDate(pr.updated_at)}</span>
          </div>
          <div class="hc-body">${esc(String(pr.body || "").slice(0, 160))}${(pr.body || "").length > 160 ? "…" : ""}</div>`;
      } else {
        const iss = hit.item;
        hoverEl.innerHTML = `
          <div class="hc-title">
            <span class="state-dot ${iss.state === "open" ? "state-open" : "state-closed"}"></span>
            <span>#${iss.number} ${esc(iss.title)}</span>
          </div>
          <div class="hc-meta">
            <span class="pill ${iss.state === "open" ? "pill-green" : "pill-red"}">${issueStateText(iss)}</span>
            <span>${esc((iss.user && iss.user.login) || "")}</span>
            <span>${fmtDate(iss.updated_at)}</span>
          </div>
          <div class="hc-body">${esc(String(iss.body || "").slice(0, 160))}${(iss.body || "").length > 160 ? "…" : ""}</div>`;
      }
      const rect = anchor.getBoundingClientRect();
      hoverEl.hidden = false;
      const w = 360;
      let left = rect.left + window.scrollX;
      const maxLeft = window.scrollX + document.documentElement.clientWidth - w - 12;
      if (left > maxLeft) left = maxLeft;
      if (left < 8) left = 8;
      let top = rect.bottom + window.scrollY + 8;
      hoverEl.style.left = left + "px";
      hoverEl.style.top = top + "px";
      // 若底部放不下，改到上方
      requestAnimationFrame(() => {
        const hr = hoverEl.getBoundingClientRect();
        if (hr.bottom > window.innerHeight - 8) {
          hoverEl.style.top = rect.top + window.scrollY - hr.height - 8 + "px";
        }
      });
    }, 220);
  }

  /* ---------- navigation ---------- */
  function snapshot() {
    return {
      view: state.view,
      prFilter: state.prFilter,
      issueFilter: state.issueFilter,
      prDetail: state.prDetail,
      issueDetail: state.issueDetail,
      commitDetail: state.commitDetail,
      commitRef: state.commitRef,
      commitLimit: state.commitLimit,
      commitRows: state.commitRows,
      releaseDetail: state.releaseDetail,
      branchDetail: state.branchDetail,
      graphLimit: state.graphLimit,
      graphRows: state.graphRows,
      search: state.search,
      filePath: state.filePath,
      compareBase: state.compareBase,
      compareHead: state.compareHead,
      compareLimit: state.compareLimit,
      compareRows: state.compareRows,
    };
  }

  function restore(snap) {
    state.view = snap.view;
    state.prFilter = snap.prFilter;
    state.issueFilter = snap.issueFilter;
    state.prDetail = snap.prDetail;
    state.issueDetail = snap.issueDetail;
    state.commitDetail = snap.commitDetail;
    state.commitRef = snap.commitRef;
    state.commitLimit = snap.commitLimit != null ? snap.commitLimit : 150;
    state.commitRows = snap.commitRows != null ? snap.commitRows : 150;
    state.releaseDetail = snap.releaseDetail;
    state.branchDetail = snap.branchDetail;
    state.graphLimit = snap.graphLimit != null ? snap.graphLimit : 150;
    state.graphRows = snap.graphRows != null ? snap.graphRows : 150;
    state.search = snap.search;
    state.filePath = snap.filePath;
    state.compareBase = snap.compareBase;
    state.compareHead = snap.compareHead;
    state.compareLimit = snap.compareLimit != null ? snap.compareLimit : 150;
    state.compareRows = snap.compareRows != null ? snap.compareRows : 150;
    state.showMdRaw = false;
  }

  function clearDetails() {
    state.prDetail = null;
    state.issueDetail = null;
    state.commitDetail = null;
    state.releaseDetail = null;
    state.branchDetail = null;
    state.filePath = null;
  }

  function pushHistory() {
    navHistory.push(snapshot());
  }

  function goBack() {
    if (navHistory.length) {
      restore(navHistory.pop());
      const s = $("#global-search");
      if (s) s.value = state.search || "";
    } else {
      // 无更早历史：回退到当前视图对应列表
      clearDetails();
    }
    $$(".nav-item").forEach((b) => b.classList.toggle("active", b.dataset.view === state.view));
    render();
  }

  function setView(view, opts = {}) {
    const push = opts.pushHistory !== false;
    if (push) pushHistory();
    state.view = view;
    if (opts.clearSearch) {
      state.search = "";
      const s = $("#global-search");
      if (s) s.value = "";
    }
    if (opts.clearDetails) clearDetails();
    if (opts.prDetail !== undefined) state.prDetail = opts.prDetail;
    if (opts.issueDetail !== undefined) state.issueDetail = opts.issueDetail;
    if (opts.commitDetail !== undefined) state.commitDetail = opts.commitDetail;
    if (opts.releaseDetail !== undefined) state.releaseDetail = opts.releaseDetail;
    if (opts.branchDetail !== undefined) state.branchDetail = opts.branchDetail;
    if (opts.filePath !== undefined) state.filePath = opts.filePath;
    $$(".nav-item").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
    render();
  }

  /* ---------- rows ---------- */
  function prRow(pr) {
    return `
      <div class="row" data-pr="${pr.number}">
        <span class="state-dot ${prStateClass(pr)}" title="${prStateText(pr)}"></span>
        <div class="row-main">
          <div class="row-title">#${pr.number} ${esc(pr.title)}</div>
          <div class="row-meta">
            <span class="pill ${pr.merged ? "pill-purple" : pr.state === "open" ? "pill-green" : "pill-red"}">${prStateText(pr)}</span>
            <span>${esc((pr.user && pr.user.login) || "unknown")}</span>
            <span>更新于 ${esc(fmtDate(pr.updated_at))}</span>
            <span class="mono">${esc(pr.base && pr.base.ref)} ← ${esc(pr.head && pr.head.ref)}</span>
            ${pr.changed_files ? `<span>${pr.changed_files} 文件</span>` : ""}
            <span class="stat-add">+${pr.additions || 0}</span>
            <span class="stat-del">-${pr.deletions || 0}</span>
            ${labelHtml(pr.labels)}
          </div>
        </div>
      </div>`;
  }

  function issueRow(issue) {
    return `
      <div class="row" data-issue="${issue.number}">
        <span class="state-dot ${issue.state === "open" ? "state-open" : "state-closed"}"></span>
        <div class="row-main">
          <div class="row-title">#${issue.number} ${esc(issue.title)}</div>
          <div class="row-meta">
            <span class="pill ${issue.state === "open" ? "pill-green" : "pill-red"}">${issueStateText(issue)}</span>
            <span>${esc((issue.user && issue.user.login) || "unknown")}</span>
            <span>更新于 ${esc(fmtDate(issue.updated_at))}</span>
            <span>${issue.comments || 0} 评论</span>
            ${labelHtml(issue.labels)}
          </div>
        </div>
      </div>`;
  }

  function commitRow(c) {
    const firstLine = (c.message || "").split("\n")[0];
    const nFiles = (c.files || []).length;
    return `
      <div class="row commit-row" data-commit="${esc(c.sha)}">
        <div class="row-main">
          <div class="row-title">${esc(firstLine)}</div>
          <div class="row-meta">
            <span class="sha" title="${esc(c.sha)}">${shortSha(c.sha)}</span>
            <span>${esc(c.author_name || (c.author && c.author.login) || "—")}</span>
            <span>${esc(fmtDate(c.author_date || c.committer_date))}</span>
            ${nFiles ? `<span>${nFiles} 文件</span>` : ""}
            ${c.stats && c.stats.additions != null ? `<span class="stat-add">+${c.stats.additions}</span>` : ""}
            ${c.stats && c.stats.deletions != null ? `<span class="stat-del">-${c.stats.deletions}</span>` : ""}
          </div>
        </div>
      </div>`;
  }

  /* ---------- contribution wall ---------- */
  function localDayKey(d) {
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, "0");
    const day = String(d.getDate()).padStart(2, "0");
    return `${y}-${m}-${day}`;
  }

  function dayKey(iso) {
    if (!iso) return null;
    try {
      const d = new Date(iso);
      if (Number.isNaN(d.getTime())) return null;
      return localDayKey(d);
    } catch {
      return null;
    }
  }

  function parseDayKey(key) {
    const [y, m, d] = String(key).split("-").map(Number);
    return new Date(y, (m || 1) - 1, d || 1);
  }

  function resolveTagDate(tag) {
    if (!tag) return null;
    const rel = (D.releases || []).find((r) => r.tag_name === tag.name);
    if (rel) {
      const k = dayKey(rel.published_at || rel.created_at);
      if (k) return k;
    }
    const sha = tag.sha;
    if (sha) {
      // 走合并池：标签可能指向分支独有提交（不在 commits.js 里）
      const c = findCommitAnywhere(sha);
      if (c) {
        const k = dayKey(c.author_date || c.committer_date);
        if (k) return k;
      }
    }
    return null;
  }

  let activityCache = null;
  function getActivityIndex() {
    if (activityCache) return activityCache;
    const map = new Map();
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
    (D.commits || []).forEach((c) => bump(dayKey(c.author_date || c.committer_date), "commit"));
    (D.prs || []).forEach((p) => bump(dayKey(p.created_at), "pr"));
    (D.issues || []).forEach((i) => bump(dayKey(i.created_at), "issue"));
    (D.releases || []).forEach((r) => bump(dayKey(r.published_at || r.created_at), "release"));
    let tagUnknown = 0;
    (D.tags || []).forEach((t) => {
      const k = resolveTagDate(t);
      if (k) bump(k, "tag");
      else tagUnknown += 1;
    });
    activityCache = { map, tagUnknown };
    return activityCache;
  }

  function wallDateRange() {
    const { map } = getActivityIndex();
    const endIso = (D.meta && (D.meta.exported_at || D.meta.pushed_at)) || new Date().toISOString();
    let end = dayKey(endIso) || localDayKey(new Date());
    const keys = Array.from(map.keys()).sort();
    let start = keys[0] || null;
    if (!start) {
      const d = parseDayKey(end);
      d.setFullYear(d.getFullYear() - 1);
      start = localDayKey(d);
    }
    return { start, end };
  }

  function emptyDayBuckets() {
    return { commit: 0, pr: 0, issue: 0, tag: 0, release: 0, total: 0 };
  }

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

  function contribTip(day, bucket) {
    const b = bucket || emptyDayBuckets();
    const parts = [];
    if (b.commit) parts.push(`${b.commit} 次提交`);
    if (b.pr) parts.push(`${b.pr} 个 PR`);
    if (b.issue) parts.push(`${b.issue} 个 Issue`);
    if (b.tag) parts.push(`${b.tag} 个 Tag`);
    if (b.release) parts.push(`${b.release} 个 Release`);
    const detail = parts.length ? parts.join(" · ") : "无贡献";
    return `${day} · ${b.total} 次贡献：${detail}`;
  }

  function rangeWindowDays() {
    switch (state.rangeKey) {
      case "1": return 1;
      case "7": return 7;
      case "30": return 30;
      case "90": return 90;
      case "365": return 365;
      case "all": return Infinity;
      case "custom": {
        const n = Number(state.rangeCustomDays);
        return Number.isFinite(n) && n >= 1 ? Math.floor(n) : 7;
      }
      default: return 7;
    }
  }

  function summarizeRange() {
    const { map, tagUnknown } = getActivityIndex();
    const { start, end } = wallDateRange();
    const days = rangeWindowDays();
    let fromKey = start;
    if (Number.isFinite(days)) {
      const from = parseDayKey(end);
      from.setDate(from.getDate() - (days - 1));
      fromKey = localDayKey(from);
    }
    // 与墙一致：含 from 与 end
    const acc = emptyDayBuckets();
    for (const [k, v] of map.entries()) {
      if (k < fromKey || k > end) continue;
      acc.commit += v.commit;
      acc.pr += v.pr;
      acc.issue += v.issue;
      acc.tag += v.tag;
      acc.release += v.release;
      acc.total += v.total;
    }
    let label;
    if (state.rangeKey === "1") label = "今天";
    else if (state.rangeKey === "all") label = "从最初";
    else if (state.rangeKey === "custom") label = `最近 ${rangeWindowDays()} 天`;
    else label = `最近 ${rangeWindowDays()} 天`;
    return { acc, label, tagUnknown, fromKey, end };
  }

  function renderContribWall() {
    const { map, tagUnknown } = getActivityIndex();
    const { start, end } = wallDateRange();
    const startD = parseDayKey(start);
    // 对齐到周日（与 GitHub 一致）
    const gridStart = new Date(startD);
    gridStart.setDate(gridStart.getDate() - gridStart.getDay());

    const positives = [];
    for (const v of map.values()) {
      if (v.total > 0) positives.push(v.total);
    }

    const weeks = [];
    const months = [];
    let prevMonth = null;
    let cursor = new Date(gridStart);
    const endD = parseDayKey(end);
    while (cursor <= endD) {
      const col = [];
      for (let i = 0; i < 7; i++) {
        const y = cursor.getFullYear();
        const m = String(cursor.getMonth() + 1).padStart(2, "0");
        const dd = String(cursor.getDate()).padStart(2, "0");
        const day = `${y}-${m}-${dd}`;
        const bucket = map.get(day) || emptyDayBuckets();
        const inRange = day >= start && day <= end;
        const lv = inRange ? levelForCount(bucket.total, positives) : 0;
        col.push({
          day,
          lv: inRange ? lv : -1,
          tip: inRange ? contribTip(day, bucket) : "",
          muted: !inRange,
        });
        cursor.setDate(cursor.getDate() + 1);
      }
      // 月份标签：该列周日所在月与上一列不同则显示
      const weekMonth = new Date(parseDayKey(col[0].day));
      const mm = weekMonth.getMonth();
      months.push(mm !== prevMonth ? weekMonth.getMonth() + 1 : 0);
      prevMonth = mm;
      weeks.push(col);
    }

    const monthHtml = months
      .map((mo) => `<div class="contrib-month-label">${mo ? mo + "月" : ""}</div>`)
      .join("");
    const weekHtml = weeks
      .map(
        (col) =>
          `<div class="contrib-week">${col
            .map((cell) => {
              if (cell.muted || cell.lv < 0) {
                return `<div class="cw-cell" style="visibility:hidden"></div>`;
              }
              const lvClass = cell.lv > 0 ? ` l${cell.lv}` : "";
              return `<div class="cw-cell${lvClass}" title="${esc(cell.tip)}"></div>`;
            })
            .join("")}</div>`
      )
      .join("");

    const legendHtml = [0, 1, 2, 3, 4]
      .map((lv) => `<div class="cw-cell${lv ? " l" + lv : ""}"></div>`)
      .join("");

    const sum = summarizeRange();
    const rangeOptions = [
      ["1", "1 天"],
      ["7", "7 天"],
      ["30", "一个月"],
      ["90", "三个月"],
      ["365", "一年"],
      ["all", "从最初"],
      ["custom", "自定义…"],
    ]
      .map(([v, lab]) => `<option value="${v}"${state.rangeKey === v ? " selected" : ""}>${lab}</option>`)
      .join("");

    const summaryHtml = renderRangeSummaryText(sum);

    return `
      <div class="panel">
        <div class="panel-hd">贡献墙</div>
        <div class="panel-bd contrib-body">
          <div class="contrib-scroll" id="contrib-scroll">
            <div class="contrib-inner">
              <div class="contrib-months">${monthHtml}</div>
              <div class="contrib-main">
                <div class="contrib-dow" aria-hidden="true">
                  <span></span><span>一</span><span></span><span>三</span><span></span><span>五</span><span></span>
                </div>
                <div class="contrib-weeks">${weekHtml}</div>
              </div>
            </div>
          </div>
          <div class="contrib-footer">
            <div class="contrib-legend">Less${legendHtml}More</div>
            <div class="cw-summary">
              <select id="cw-range" aria-label="汇总时间范围">${rangeOptions}</select>
              <input id="cw-custom-days" type="number" min="1" max="20000" step="1"
                value="${esc(String(state.rangeCustomDays))}"
                ${state.rangeKey === "custom" ? "" : "hidden"}
                aria-label="自定义天数" />
              <span id="cw-summary-text">${summaryHtml}</span>
            </div>
          </div>
          ${tagUnknown ? `<div class="muted small">另有 ${tagUnknown} 个 Tag 日期未知，未计入热力图。</div>` : ""}
        </div>
      </div>
    `;
  }

  function renderRangeSummaryText(sum) {
    const n = (x) => `<span class="cw-num">${x}</span>`;
    const parts = [
      `${n(sum.acc.commit)} 次提交`,
      `${n(sum.acc.pr)} 个 PR`,
      `${n(sum.acc.issue)} 个 Issue`,
      `${n(sum.acc.tag)} 个 Tag`,
      `${n(sum.acc.release)} 个 Release`,
    ];
    let note = "";
    if (sum.tagUnknown) {
      note = `（另有 ${sum.tagUnknown} 个 Tag 日期未知）`;
    }
    return `${esc(sum.label)}：${parts.join("、")}${note}`;
  }

  function scrollContribToEnd() {
    const sc = document.getElementById("contrib-scroll");
    if (sc) sc.scrollLeft = sc.scrollWidth;
  }

  /* ---------- overview ---------- */
  function renderOverview() {
    const m = D.meta;

    // reset=true：点击后把该视图的筛选重置为「全部」，保证方块数字与列表条数一致
    const stat = (n, label, view, filter, reset) =>
      `<div class="stat stat-click" data-goto="${view}"${filter ? ` data-goto-filter="${filter}"` : ""}${reset ? ` data-goto-reset="1"` : ""} title="点击进入 ${esc(label)}">
        <div class="stat-n">${n}</div><div class="stat-l">${esc(label)}</div>
      </div>`;

    return `
      <h1 class="h1">${esc(m.full_name || m.repo || "仓库")}</h1>
      <p class="muted" style="margin-top:0">${esc(m.description || "（无描述）")}</p>
      ${m.private ? `<div class="warn-box">私有仓库只读镜像 · 导出于 ${esc(fmtDate(m.exported_at))}</div>` : ""}
      <div class="stats">
        ${stat(D.prs.length, "Pull Requests", "prs", null, true)}
        ${stat(D.issues.length, "Issues", "issues", null, true)}
        ${stat(D.commits.length, "Commits", "commits")}
        ${stat(D.tags.length, "Tags", "tags")}
        ${stat(D.branches.length, "Branches", "branches")}
      </div>
      <div class="panel">
        <div class="panel-hd">仓库信息</div>
        <div class="panel-bd">
          <dl class="kv">
            <dt>默认分支</dt><dd class="mono">${esc(m.default_branch || "—")}</dd>
            <dt>主语言</dt><dd>${esc(m.language || "—")}</dd>
            <dt>最后推送</dt><dd>${esc(fmtDate(m.pushed_at || m.updated_at))}</dd>
            <dt>导出时间</dt><dd>${esc(fmtDate(m.exported_at))}</dd>
            <dt>Topics</dt><dd>${(m.topics || []).map((t) => `<span class="pill">${esc(t)}</span>`).join(" ") || "—"}</dd>
            <dt>源地址</dt><dd class="mono small">${esc(m.html_url || "")}</dd>
          </dl>
        </div>
      </div>
      ${renderContribWall()}
      <div class="panel">
        <div class="panel-hd">最近提交</div>
        <div class="panel-bd" style="padding:0">
          ${D.commits.slice(0, 8).map(commitRow).join("") || `<div class="empty">无提交</div>`}
        </div>
      </div>
      <div class="panel">
        <div class="panel-hd">打开中的 PR</div>
        <div class="panel-bd" style="padding:0">
          ${D.prs.filter((p) => p.state === "open").sort(byNumberDesc).slice(0, 8).map(prRow).join("") || `<div class="empty">无打开中的 PR</div>`}
        </div>
      </div>
    `;
  }

  /* ---------- PR ---------- */
  function renderPrs() {
    if (state.prDetail != null) {
      const pr = D.prs.find((p) => p.number === state.prDetail);
      return pr ? renderPrDetail(pr) : `<div class="empty">未找到 PR #${state.prDetail}</div>`;
    }

    let list = D.prs.slice().sort(byNumberDesc);
    if (state.prFilter === "open") list = list.filter((p) => p.state === "open" && !p.merged);
    else if (state.prFilter === "merged") list = list.filter((p) => p.merged);
    else if (state.prFilter === "closed") list = list.filter((p) => p.state === "closed" && !p.merged);
    list = list.filter((p) => matchSearch(`${p.number} ${p.title} ${p.body || ""} ${(p.user && p.user.login) || ""}`));

    return `
      <h1 class="h1">Pull Requests</h1>
      <div class="filters">
        ${["all", "open", "merged", "closed"].map((f) => {
          const label = { all: "全部", open: "打开", merged: "已合并", closed: "已关闭未合并" }[f];
          return `<button class="chip ${state.prFilter === f ? "on" : ""}" data-pr-filter="${f}">${label}</button>`;
        }).join("")}
      </div>
      <div class="count-line">共 ${list.length} 条</div>
      <div class="list">${list.map(prRow).join("") || `<div class="empty">无匹配结果</div>`}</div>
    `;
  }

  function renderPrDetail(pr) {
    const files = pr.files || [];
    const prCommits = pr.commits_list || [];
    return `
      <button class="back-link" data-nav-back>← 返回</button>
      <div class="detail-hd">
        <span class="state-dot ${prStateClass(pr)}" style="margin-top:8px"></span>
        <div>
          <h1 class="detail-title">#${pr.number} ${esc(pr.title)}</h1>
          <div class="detail-meta">
            <span class="pill ${pr.merged ? "pill-purple" : pr.state === "open" ? "pill-green" : "pill-red"}">${prStateText(pr)}</span>
            <span>${esc((pr.user && pr.user.login) || "")} 于 ${esc(fmtDate(pr.created_at))} 创建</span>
            · <span>${pr.comments || 0} 条对话</span>
            · <span>${files.length} 个变更文件</span>
            · <span class="stat-add">+${pr.additions || 0}</span> <span class="stat-del">-${pr.deletions || 0}</span>
          </div>
        </div>
      </div>
      <div class="panel">
        <div class="panel-hd">分支</div>
        <div class="panel-bd mono small">
          base: ${esc((pr.base && pr.base.label) || (pr.base && pr.base.ref) || "")} (${shortSha(pr.base && pr.base.sha)})<br/>
          head: ${esc((pr.head && pr.head.label) || (pr.head && pr.head.ref) || "")} (${shortSha(pr.head && pr.head.sha)})
        </div>
      </div>
      <div class="panel">
        <div class="panel-hd">描述</div>
        <div class="panel-bd">${mdHtml(pr.body || "")}</div>
      </div>
      ${(pr.issue_comments || []).length ? `
        <div class="panel">
          <div class="panel-hd">评论</div>
          <div class="panel-bd">
            ${(pr.issue_comments || []).map(commentHtml).join("")}
          </div>
        </div>` : ""}
      <h2 class="h2">Commits (${prCommits.length})</h2>
      <div class="list" style="margin-bottom:18px">
        ${prCommits.length
          ? prCommits.map(commitRow).join("")
          : `<div class="empty">未导出 PR 内 commit 列表（可能导出时加了 --skip-details）</div>`}
      </div>
      <h2 class="h2">文件变更 (${files.length})</h2>
      ${files.length ? files.map(fileDiffHtml).join("") : `<div class="empty">未导出 diff（可能导出时加了 --skip-details）</div>`}
    `;
  }

  function commentHtml(c) {
    return `
      <div class="comment">
        <div class="comment-hd">
          <span>${esc((c.user && c.user.login) || "unknown")}</span>
          <span>${esc(fmtDate(c.created_at))}</span>
        </div>
        ${mdHtml(c.body || "")}
      </div>`;
  }

  function renderPatch(patch) {
    if (!patch) return `<div class="muted small" style="padding:10px">（无 patch，可能为二进制或过大）</div>`;
    const lines = patch.split("\n");
    const rows = [];
    for (const line of lines) {
      if (line.startsWith("@@")) {
        rows.push(`<tr class="hunk"><td class="diff-line-no"></td><td class="diff-sign"></td><td>${esc(line)}</td></tr>`);
      } else if (line.startsWith("+")) {
        rows.push(`<tr class="add"><td class="diff-line-no"></td><td class="diff-sign">+</td><td>${esc(line.slice(1))}</td></tr>`);
      } else if (line.startsWith("-")) {
        rows.push(`<tr class="del"><td class="diff-line-no"></td><td class="diff-sign">−</td><td>${esc(line.slice(1))}</td></tr>`);
      } else {
        const body = line.startsWith(" ") ? line.slice(1) : line;
        rows.push(`<tr><td class="diff-line-no"></td><td class="diff-sign"></td><td>${esc(body)}</td></tr>`);
      }
    }
    return `<table class="diff-table"><tbody>${rows.join("")}</tbody></table>`;
  }

  function fileDiffHtml(f) {
    const status = f.status || "modified";
    return `
      <div class="file-block">
        <div class="file-hd">
          <span class="pill">${esc(status)}</span>
          <span class="file-path">${esc(f.filename)}</span>
          <span class="stat-add">+${f.additions || 0}</span>
          <span class="stat-del">-${f.deletions || 0}</span>
        </div>
        ${renderPatch(f.patch)}
      </div>`;
  }

  /* ---------- Issues ---------- */
  function renderIssues() {
    if (state.issueDetail != null) {
      const issue = D.issues.find((i) => i.number === state.issueDetail);
      return issue ? renderIssueDetail(issue) : `<div class="empty">未找到 Issue #${state.issueDetail}</div>`;
    }

    let list = D.issues.slice().sort(byNumberDesc);
    if (state.issueFilter === "open") list = list.filter((i) => i.state === "open");
    else if (state.issueFilter === "closed") list = list.filter((i) => i.state === "closed");
    list = list.filter((i) => matchSearch(`${i.number} ${i.title} ${i.body || ""} ${(i.user && i.user.login) || ""}`));

    return `
      <h1 class="h1">Issues</h1>
      <div class="filters">
        ${["all", "open", "closed"].map((f) => {
          const label = { all: "全部", open: "打开", closed: "已关闭" }[f];
          return `<button class="chip ${state.issueFilter === f ? "on" : ""}" data-issue-filter="${f}">${label}</button>`;
        }).join("")}
      </div>
      <div class="count-line">共 ${list.length} 条</div>
      <div class="list">${list.map(issueRow).join("") || `<div class="empty">无匹配结果</div>`}</div>
    `;
  }

  function renderIssueDetail(issue) {
    const cb = issue.closed_by_pr;
    return `
      <button class="back-link" data-nav-back>← 返回</button>
      <div class="detail-hd">
        <span class="state-dot ${issue.state === "open" ? "state-open" : "state-closed"}" style="margin-top:8px"></span>
        <div>
          <h1 class="detail-title">#${issue.number} ${esc(issue.title)}</h1>
          <div class="detail-meta">
            <span class="pill ${issue.state === "open" ? "pill-green" : "pill-red"}">${issueStateText(issue)}</span>
            <span>${esc((issue.user && issue.user.login) || "")} 于 ${esc(fmtDate(issue.created_at))} 创建</span>
            ${labelHtml(issue.labels)}
          </div>
        </div>
      </div>
      ${cb ? `
        <div class="closed-by-box">
          <span class="state-dot ${cb.merged ? "state-merged" : "state-closed"}"></span>
          <div>
            <div>由此 PR 关闭：<a class="issue-ref" data-ref="${cb.number}" data-ref-type="pr" href="#pr-${cb.number}">#${cb.number} ${esc(cb.title || "")}</a>
              ${cb.merged ? `<span class="pill pill-purple" style="margin-left:8px">Merged</span>` : ""}
            </div>
            <div class="muted small" style="margin-top:2px">来源：${cb.source === "timeline" ? "GitHub Timeline" : "PR 描述中的 Closes/Fixes"}</div>
          </div>
        </div>` : ""}
      <div class="panel">
        <div class="panel-hd">描述</div>
        <div class="panel-bd">${mdHtml(issue.body || "")}</div>
      </div>
      ${(issue.issue_comments || []).length ? `
        <div class="panel">
          <div class="panel-hd">评论 (${issue.issue_comments.length})</div>
          <div class="panel-bd">
            ${issue.issue_comments.map(commentHtml).join("")}
          </div>
        </div>` : ""}
    `;
  }

  /* ---------- Commits ---------- */
  function renderCommits() {
    if (state.commitDetail) {
      const c = findCommitAnywhere(state.commitDetail);
      return c ? renderCommitDetail(c) : `<div class="empty">未找到 commit ${esc(shortSha(state.commitDetail))}</div>`;
    }

    const refs = commitRefs();
    const ref = currentCommitRef();
    const all = commitsForRef(ref);
    const matched = all.filter((c) => {
      const hay = `${c.sha} ${c.message || ""} ${c.author_name || ""}`;
      return matchSearch(hay);
    });

    // 搜索时跨全量匹配、不做截断（否则要翻很多页才找得到）；
    // 无搜索时按档位截断，可点「加载更多」追加。
    const searching = !!state.search;
    const unlimited = !(state.commitRows > 0);
    const list = (searching || unlimited) ? matched : matched.slice(0, state.commitRows);
    const remain = (searching || unlimited) ? 0 : Math.max(0, matched.length - list.length);
    const commitLimitOptions = [[150, "最近 150 条"], [300, "最近 300 条"], [600, "最近 600 条"], [0, "全部"]]
      .map(([v, lab]) => `<option value="${v}"${state.commitLimit === v ? " selected" : ""}>${lab}</option>`)
      .join("");

    // 只有存在 graph 引用表时才显示切换器（旧导出包自动隐藏）
    const selector = refs.length
      ? `<div class="filters">
          <label class="small muted">查看</label>
          <select id="commit-ref">
            ${refs
              .map((r) => `<option value="${esc(refKey(r))}"${ref && refKey(r) === refKey(ref) ? " selected" : ""}>${r.type === "branch" ? "分支" : "标签"} · ${esc(r.name)}${r.isDefault ? "（默认）" : ""}</option>`)
              .join("")}
          </select>
        </div>`
      : "";

    const hint = ref
      ? (ref.type === "branch" && ref.isDefault
          ? `默认分支 ${esc(D.meta.default_branch || "")}`
          : `${ref.type === "branch" ? "分支" : "标签"} ${esc(ref.name)} 可达的提交`)
      : `默认分支 ${esc((D.meta && D.meta.default_branch) || "")}`;

    const toolbar = searching
      ? ""
      : `<div class="filters list-toolbar">
           <label class="small muted">显示</label>
           <select id="commit-limit" aria-label="显示条数">${commitLimitOptions}</select>
           <span class="small muted">共 ${matched.length} 个提交${remain ? ` · 已显示最近 ${list.length} 条` : ""}</span>
         </div>`;
    const moreBtn = remain
      ? `<div class="list-more">
           <button class="btn" id="commit-more">加载更多（还有 ${remain} 条）</button>
         </div>`
      : "";
    const countText = searching
      ? `匹配 ${list.length} 条（跨全部 ${all.length} 条）`
      : `显示 ${list.length} / ${matched.length} 条`;

    return `
      <h1 class="h1">Commits</h1>
      <p class="muted small" style="margin-top:0">${hint} · 共 ${all.length} 条 · 点击行查看文件变更</p>
      ${selector}
      ${toolbar}
      <div class="count-line">${countText}</div>
      <div class="list">
        ${list.map(commitRow).join("") || `<div class="empty">无匹配结果</div>`}
      </div>
      ${moreBtn}
    `;
  }

  function renderCommitDetail(c) {
    const files = c.files || [];
    const rest = (c.message || "").split("\n").slice(1).join("\n").trim();
    return `
      <button class="back-link" data-nav-back>← 返回</button>
      <div class="detail-hd">
        <div>
          <h1 class="detail-title">${esc((c.message || "").split("\n")[0])}</h1>
          <div class="detail-meta">
            <span class="sha">${esc(c.sha)}</span>
            · <span>${esc(c.author_name || (c.author && c.author.login) || "—")}</span>
            · <span>${esc(fmtDate(c.author_date || c.committer_date))}</span>
            ${c.stats && c.stats.additions != null
              ? `· <span class="stat-add">+${c.stats.additions}</span> <span class="stat-del">-${c.stats.deletions || 0}</span>`
              : ""}
          </div>
        </div>
      </div>
      ${rest ? `<div class="panel"><div class="panel-hd">完整说明</div><div class="panel-bd">${mdHtml(c.message || "")}</div></div>` : ""}
      ${c.parents && c.parents.length ? `<p class="muted small mono">parent: ${c.parents.map((p) => shortSha(p)).join(", ")}</p>` : ""}
      <h2 class="h2">文件变更 (${files.length})</h2>
      ${files.length
        ? files.map(fileDiffHtml).join("")
        : `<div class="empty">该 commit 未导出文件 diff（导出时可能加了 --skip-details，或超出 --max-commit-diffs）</div>`}
    `;
  }

  /* ---------- Tags / Releases ---------- */
  function renderTags() {
    if (state.releaseDetail) {
      const r = (D.releases || []).find((x) => (x.tag_name || x.name) === state.releaseDetail);
      return r ? renderReleaseDetail(r) : `<div class="empty">未找到 Release ${esc(state.releaseDetail)}</div>`;
    }

    const releases = (D.releases || []).filter((r) =>
      matchSearch(`${r.tag_name} ${r.name} ${r.body || ""}`)
    );
    // 仅有 tag、无 release 说明的，也列出来
    const relTags = new Set((D.releases || []).map((r) => r.tag_name));
    const extraTags = (D.tags || [])
      .filter((t) => !relTags.has(t.name) && matchSearch(t.name));

    return `
      <h1 class="h1">Releases</h1>
      <p class="muted small" style="margin-top:0">与 Tag 一一对应 · 点击查看说明、Commits、文件变更与关联 PR</p>
      <div class="count-line">共 ${releases.length} 个 Release${extraTags.length ? ` · 另有 ${extraTags.length} 个无说明 Tag` : ""}</div>
      <div class="release-list">
        ${releases.map(releaseRow).join("") || `<div class="empty">无 Release</div>`}
      </div>
      ${extraTags.length ? `
        <h2 class="h2" style="margin-top:20px">其它 Tags</h2>
        <div class="list">
          ${extraTags.map((t) => `
            <div class="row" style="cursor:default">
              <div class="row-main">
                <div class="row-title mono">${esc(t.name)}</div>
                <div class="row-meta"><span class="sha">${shortSha(t.sha)}</span></div>
              </div>
            </div>`).join("")}
        </div>` : ""}
    `;
  }

  function releaseRow(r) {
    const tag = r.tag_name || "";
    const title = r.name || tag;
    const nCommits = (r.compare && r.compare.total_commits) || (r.compare && r.compare.commits && r.compare.commits.length) || 0;
    const nFiles = (r.compare && r.compare.files && r.compare.files.length) || 0;
    const nPrs = (r.related_prs || []).length;
    return `
      <div class="release-card" data-release="${esc(tag)}">
        <div class="release-hd">
          <span class="state-dot state-open" title="Release"></span>
          <div class="release-title">${esc(title)}</div>
          <span class="pill mono">${esc(tag)}</span>
          ${r.prerelease ? `<span class="pill">Pre-release</span>` : ""}
          ${r.draft ? `<span class="pill">Draft</span>` : ""}
        </div>
        <div class="release-meta">
          <span>${esc((r.author && r.author.login) || "")}</span>
          <span>发布于 ${esc(fmtDate(r.published_at || r.created_at))}</span>
          ${nCommits ? `<span>${nCommits} commits</span>` : ""}
          ${nFiles ? `<span>${nFiles} 文件</span>` : ""}
          ${nPrs ? `<span>${nPrs} 个关联 PR</span>` : ""}
        </div>
        ${r.body ? `<div class="release-excerpt">${esc(String(r.body).slice(0, 160))}${String(r.body).length > 160 ? "…" : ""}</div>` : ""}
      </div>`;
  }

  function renderReleaseDetail(r) {
    const cmp = r.compare || {};
    const files = cmp.files || [];
    const commits = cmp.commits || [];
    const prs = (r.related_prs || []).slice().sort(byNumberDesc);
    return `
      <button class="back-link" data-nav-back>← 返回</button>
      <div class="detail-hd">
        <div>
          <h1 class="detail-title">${esc(r.name || r.tag_name)}</h1>
          <div class="detail-meta">
            <span class="pill mono">${esc(r.tag_name)}</span>
            ${r.prerelease ? `<span class="pill">Pre-release</span>` : ""}
            <span>${esc((r.author && r.author.login) || "")} 于 ${esc(fmtDate(r.published_at || r.created_at))} 发布</span>
            ${cmp.base ? `· <span class="mono small">${esc(cmp.base)} … ${esc(cmp.head || r.tag_name)}</span>` : ""}
            ${cmp.total_commits != null ? `· <span>${cmp.total_commits} commits</span>` : ""}
          </div>
        </div>
      </div>
      <div class="panel">
        <div class="panel-hd">Release Notes</div>
        <div class="panel-bd">${mdHtml(r.body || "")}</div>
      </div>
      ${prs.length ? `
        <div class="panel">
          <div class="panel-hd">关联 PR（来自 Release Notes）</div>
          <div class="panel-bd" style="padding:0">
            ${prs.map((p) => `
              <div class="row" data-pr="${p.number}">
                <span class="state-dot ${p.merged ? "state-merged" : p.state === "open" ? "state-open" : "state-closed"}"></span>
                <div class="row-main">
                  <div class="row-title">#${p.number} ${esc(p.title || "")}</div>
                  <div class="row-meta">
                    <span class="pill ${p.merged ? "pill-purple" : p.state === "open" ? "pill-green" : "pill-red"}">${p.merged ? "Merged" : p.state === "open" ? "Open" : "Closed"}</span>
                  </div>
                </div>
              </div>`).join("")}
          </div>
        </div>` : ""}
      <h2 class="h2">Commits (${cmp.total_commits != null ? cmp.total_commits : commits.length})</h2>
      ${cmp.error ? `<div class="warn-box">对比失败：${esc(cmp.error)}</div>` : ""}
      <div class="list" style="margin-bottom:18px">
        ${commits.length
          ? commits.map((c) => `
              <div class="row" ${D.commits && D.commits.some((x) => x.sha === c.sha) ? `data-commit="${esc(c.sha)}"` : 'style="cursor:default"'}>
                <div class="row-main">
                  <div class="row-title">${esc((c.message || "").split("\n")[0])}</div>
                  <div class="row-meta">
                    <span class="sha">${shortSha(c.sha)}</span>
                    <span>${esc(c.author_name || (c.author && c.author.login) || "")}</span>
                    <span>${esc(fmtDate(c.author_date))}</span>
                  </div>
                </div>
              </div>`).join("")
          : `<div class="empty">无 commit 对比数据${cmp.base ? "" : "（可能是首个 Release，无上一 tag）"}</div>`}
      </div>
      <h2 class="h2">文件变更 (${files.length})</h2>
      ${files.length
        ? files.map(fileDiffHtml).join("")
        : `<div class="empty">无文件变更或未导出 diff</div>`}
    `;
  }

  /* ---------- Compare ---------- */
  /* 该 ref 可达的提交 sha 集合（沿 parents 回溯），用于做集合差 */
  function reachableSet(ref) {
    const pool = commitPool();
    const seen = new Set();
    if (!ref || !ref.sha) return seen;
    const stack = [ref.sha];
    while (stack.length) {
      const sha = stack.pop();
      if (!sha || seen.has(sha)) continue;
      seen.add(sha);
      const c = pool.get(sha);
      ((c && c.parents) || []).forEach((p) => { if (p && !seen.has(p)) stack.push(p); });
    }
    return seen;
  }

  /* 对比可选的 ref：有 graph 时复用 commitRefs（分支默认优先、标签按时间新→旧）；
     旧包没有 graph 时退化为纯 tag 列表。 */
  function compareRefs() {
    const g = D.graph && D.graph.refs;
    const hasGraphRefs = !!g && (Object.keys(g.branches || {}).length || Object.keys(g.tags || {}).length);
    if (hasGraphRefs) return commitRefs();
    return (D.tags || []).map((t) => ({ type: "tag", name: t.name, sha: t.sha, isDefault: false }));
  }

  function findCompareRef(refs, key) {
    return refs.find((r) => refKey(r) === key) || null;
  }

  /* 对比结果：head 相对 base 的独有提交 + 落后条数 + 分叉点。
     用「可达集合差」而不是数组下标切片——后者对分支根本不成立
     （分支的提交不在 commits.js 里，切片只能在主干上近似）。 */
  function compareResult(baseRef, headRef) {
    const pool = commitPool();
    const baseSet = reachableSet(baseRef);
    const headSet = reachableSet(headRef);
    const headOnly = [];
    const baseOnly = [];
    headSet.forEach((sha) => {
      if (baseSet.has(sha)) return;
      const c = pool.get(sha);
      if (c && !c.stub) headOnly.push(c);
    });
    baseSet.forEach((sha) => {
      if (headSet.has(sha)) return;
      const c = pool.get(sha);
      if (c && !c.stub) baseOnly.push(c);
    });
    headOnly.sort(byDateDesc);
    baseOnly.sort(byDateDesc);
    // 分叉点：两端都可达的提交里最新的那个（近似 merge base）
    let mergeBase = null;
    let bestKey = null;
    baseSet.forEach((sha) => {
      if (!headSet.has(sha)) return;
      const c = pool.get(sha);
      const k = (c && (c.author_date || c.committer_date)) || "";
      if (bestKey === null || k >= bestKey) { bestKey = k; mergeBase = sha; }
    });
    return { headOnly, baseOnly, mergeBase };
  }

  /* 导出时预计算好的文件差异：只在「方向完全一致」时可用；
     方向相反时增删会对调，直接展示会误导，故不展示。 */
  function precomputedFiles(baseRef, headRef) {
    for (const b of (D.branches || [])) {
      const cmp = b.compare || {};
      if (!cmp.base || !b.name) continue;
      if (cmp.base === baseRef.name && b.name === headRef.name) {
        return { files: cmp.files || [], from: "导出时已缓存该分支与默认分支的对比" };
      }
    }
    for (const r of (D.releases || [])) {
      const cmp = r.compare || {};
      const tag = r.tag_name || r.name;
      if (!cmp.base || !tag) continue;
      if (cmp.base === baseRef.name && tag === headRef.name) {
        return { files: cmp.files || [], from: "导出时已缓存该 Release 与上一 Tag 的对比" };
      }
    }
    return null;
  }

  function renderCompare() {
    const refs = compareRefs();
    const label = (r) => (r.type === "branch" ? "分支 · " : "标签 · ") + r.name;
    const optionHtml = (cur) => refs
      .map((r) => `<option value="${esc(refKey(r))}"${cur && refKey(r) === refKey(cur) ? " selected" : ""}>${esc(label(r))}</option>`)
      .join("");

    // 默认：base = 默认分支，head = 最新 tag（无 tag 则取第一个非默认分支）
    const defRef = refs.find((r) => r.type === "branch" && r.isDefault) || refs[0] || null;
    const newestTag = refs.find((r) => r.type === "tag") || null;
    const firstOther = refs.find((r) => !defRef || refKey(r) !== refKey(defRef)) || null;
    const baseRef = findCompareRef(refs, state.compareBase) || defRef;
    const headRef = findCompareRef(refs, state.compareHead) || newestTag || firstOther;

    const selectors = refs.length
      ? `<div class="filters">
           <label class="small muted">base</label>
           <select id="cmp-base">${optionHtml(baseRef)}</select>
           <label class="small muted">head</label>
           <select id="cmp-head">${optionHtml(headRef)}</select>
           <button class="btn btn-primary" id="cmp-run">对比</button>
         </div>`
      : "";

    let resultHtml = refs.length
      ? `<div class="empty">选择 base → head 做本地对比。<br/>镜像里只有提交列表与「导出时预计算好的」文件差异，无法在内网现算任意两点的 diff。</div>`
      : `<div class="empty">该导出包未包含分支 / 标签引用表（graph.js），无法对比。<br/>重新导出即可获得分支对比能力。</div>`;

    if (refs.length && baseRef && headRef && refKey(baseRef) !== refKey(headRef)) {
      const { headOnly, baseOnly, mergeBase } = compareResult(baseRef, headRef);
      const unlimited = !(state.compareRows > 0);
      const list = unlimited ? headOnly : headOnly.slice(0, state.compareRows);
      const remain = unlimited ? 0 : Math.max(0, headOnly.length - list.length);
      const pre = precomputedFiles(baseRef, headRef);

      const fileBlock = pre
        ? (pre.files.length
            ? `<h2 class="h2">文件变更 (${pre.files.length})</h2>
               <p class="muted small" style="margin-top:0">${esc(pre.from)}；为这两个 ref 的聚合差异</p>
               ${pre.files.map(fileDiffHtml).join("")}`
            : `<div class="panel"><div class="panel-hd">文件变更</div><div class="panel-bd"><div class="empty">导出时未缓存文件差异（可能加了 --skip-details）</div></div></div>`)
        : `<p class="muted small" style="margin-top:0">文件变更：本地无法现算这两个 ref 的 diff，只能对比提交列表；逐文件变更请看分支详情页或 Release 详情页。</p>`;

      const limitOptions = [[150, "最近 150 条"], [300, "最近 300 条"], [600, "最近 600 条"], [0, "全部"]]
        .map(([v, lab]) => `<option value="${v}"${state.compareLimit === v ? " selected" : ""}>${lab}</option>`)
        .join("");
      const toolbar = headOnly.length > 150
        ? `<div class="filters list-toolbar">
             <label class="small muted">显示</label>
             <select id="cmp-limit" aria-label="显示条数">${limitOptions}</select>
             <span class="small muted">head 独有 ${headOnly.length} 个${remain ? ` · 已显示最近 ${list.length} 条` : ""}</span>
           </div>`
        : "";
      const moreBtn = remain
        ? `<div class="list-more"><button class="btn" id="cmp-more">加载更多（还有 ${remain} 条）</button></div>`
        : "";

      resultHtml = `
        <div class="panel">
          <div class="panel-hd">${esc(label(baseRef))} … ${esc(label(headRef))}</div>
          <div class="panel-bd">
            <div class="row-meta">
              <span class="stat-add">head 领先 ${headOnly.length} 个提交</span>
              <span class="stat-del">落后 ${baseOnly.length} 个提交</span>
              ${mergeBase ? `<span>分叉点 <span class="sha">${shortSha(mergeBase)}</span></span>` : `<span class="small muted">未找到共同祖先（历史可能被截断）</span>`}
            </div>
          </div>
        </div>
        ${toolbar}
        <h2 class="h2">head 独有的提交 (${headOnly.length})</h2>
        <div class="list" style="margin-bottom:18px">
          ${list.map(commitRow).join("") || `<div class="empty">head 没有 base 之外的提交${baseOnly.length ? `（head 反而落后 base ${baseOnly.length} 个提交，可把 base / head 对调再看）` : ""}</div>`}
        </div>
        ${moreBtn}
        ${fileBlock}`;
    }

    return `
      <h1 class="h1">版本对比</h1>
      <p class="muted small" style="margin-top:0">选择 base → head，查看 head 相对 base 的差异（提交级；文件差异仅在有缓存时显示）。</p>
      ${selectors}
      ${resultHtml}
    `;
  }

  /* ---------- Files ---------- */
  function buildTreeIndex(tree) {
    return tree.slice().sort((a, b) => a.path.localeCompare(b.path));
  }

  function renderFiles() {
    const tree = buildTreeIndex(D.files.tree);
    const contents = D.files.contents || {};
    let list = tree;
    if (state.search) {
      list = tree.filter((t) => t.path.toLowerCase().includes(state.search.toLowerCase()));
    }

    let current = state.filePath;
    if (!current) {
      const firstBlob = tree.find((t) => t.type === "blob" && contents[t.path]) || tree.find((t) => t.type === "blob");
      current = firstBlob ? firstBlob.path : null;
      state.filePath = current;
    }

    const code = current && contents[current] != null ? contents[current] : null;
    const meta = tree.find((t) => t.path === current);
    const isMd = !!current && /\.(md|markdown|mdown|mkd)$/i.test(current);

    return `
      <h1 class="h1">文件浏览</h1>
      <p class="muted small" style="margin-top:0">默认分支 ${esc(D.meta.default_branch || "")} · 已缓存文本 ${Object.keys(contents).length} 个
        ${(D.files.truncated ? " · 树被截断" : "")}</p>
      <div class="tree">
        <div class="tree-list" id="tree-list">
          ${list.map((t) => {
            const isDir = t.type === "tree";
            const active = t.path === current;
            const name = t.path.split("/").pop();
            const indent = Math.max(0, t.path.split("/").length - 1) * 10;
            return `
              <div class="tree-item ${active ? "active" : ""}" data-path="${esc(t.path)}" style="padding-left:${8 + indent}px">
                <span class="${isDir ? "dir" : "file"}">${isDir ? "▸" : "·"}</span>
                <span>${esc(name)}</span>
                ${!isDir && t.size ? `<span class="tree-size">${fmtSize(t.size)}</span>` : ""}
              </div>`;
          }).join("") || `<div class="empty">无文件</div>`}
        </div>
        <div class="code-view">
          <div class="code-hd">
            ${current ? esc(current) : "未选择文件"}
            ${meta && meta.size ? ` · ${fmtSize(meta.size)}` : ""}
            ${code != null ? (isMd ? " · Markdown 渲染" : " · 已缓存") : current ? " · 未缓存（二进制/过大/未导出）" : ""}
            ${isMd && code != null ? `<button type="button" class="btn-link" id="md-raw-toggle" data-shown="${state.showMdRaw ? "1" : "0"}">${state.showMdRaw ? "显示排版" : "查看原文"}</button>` : ""}
          </div>
          ${code == null
            ? `<div class="empty">该文件内容不在镜像中</div>`
            : isMd && !state.showMdRaw
              ? `<div class="md-file-pane readme-view">${mdHtml(code)}</div>`
              : `<pre class="code-body">${esc(code)}</pre>`}
        </div>
      </div>
    `;
  }

  /* ---------- Readme ---------- */
  function renderReadme() {
    const path = findReadmePath();
    if (!path) {
      return `
        <h1 class="h1">README</h1>
        <div class="empty">镜像中未找到 README.md<br/>请确认导出时未加 --no-contents，且仓库根目录存在 README</div>`;
    }
    const content = (D.files.contents || {})[path];
    if (content == null) {
      return `
        <h1 class="h1">README</h1>
        <div class="empty">找到了 ${esc(path)}，但文件内容未缓存（可能过大或导出时加了 --no-contents）</div>`;
    }
    return `
      <h1 class="h1">README</h1>
      <p class="muted small" style="margin-top:0">${esc(path)} · 默认分支 ${esc(D.meta.default_branch || "")}
        · 图片路径映射到 assets/（导出时已下载仓库图片）</p>
      <article class="readme-view">
        ${mdHtml(content)}
      </article>
    `;
  }

  /* ---------- Branches ---------- */
  function branchHeadSummary(b) {
    const h = b.head || {};
    const parts = [];
    const msg = String(h.message || "").split("\n")[0];
    if (msg) parts.push(esc(msg));
    if (h.author_name) parts.push(esc(h.author_name));
    if (h.author_date) parts.push(esc(fmtDate(h.author_date)));
    return parts.join(" · ");
  }

  function branchRow(b) {
    const cmp = b.compare || {};
    const ahead = cmp.ahead_by;
    const behind = cmp.behind_by;
    const summary = branchHeadSummary(b);
    return `
      <div class="row" data-branch="${esc(b.name)}">
        <div class="row-main">
          <div class="row-title mono">${esc(b.name)}</div>
          <div class="row-meta">
            ${b.default ? `<span class="pill pill-green">默认分支</span>` : ""}
            ${b.protected ? `<span class="pill">Protected</span>` : ""}
            <span class="sha" title="${esc(b.sha || "")}">${shortSha(b.sha)}</span>
            ${ahead != null ? `<span class="stat-add">领先 ${ahead}</span>` : ""}
            ${behind != null ? `<span class="stat-del">落后 ${behind}</span>` : ""}
            ${summary ? `<span>${summary}</span>` : ""}
          </div>
        </div>
      </div>`;
  }

  /* 泳道图：把 graph 节点 + 引用表交给 gitgraph.js 做纯布局，这里只拼 DOM。
     graph.js 缺失（旧导出包）时不渲染图，只显示分支表。 */
  function renderGitGraph() {
    const G = window.GH_GRAPH;
    // graph.js 只存非主干节点（省体积）；分支树要画出完整历史，
    // 因此这里把主干（commits.js）与 graph 节点合并，主干优先（字段更全）。
    const merged = [];
    const seen = new Set();
    (D.commits || []).forEach((c) => {
      if (c && c.sha && !seen.has(c.sha)) { seen.add(c.sha); merged.push(c); }
    });
    ((D.graph && D.graph.nodes) || []).forEach((c) => {
      if (c && c.sha && !seen.has(c.sha)) { seen.add(c.sha); merged.push(c); }
    });
    const nodes = merged;
    if (!G || !nodes.length) return "";

    const refs = (D.graph && D.graph.refs) || {};
    const layoutOpts = {
      branches: refs.branches || {},
      tags: refs.tags || {},
      defaultBranch: (D.meta && D.meta.default_branch) || (D.graph && D.graph.default_branch) || "",
    };
    let layout;
    try {
      // 第一趟：全量布局，只为拿到「全局最大泳道数」。
      // 必须在首屏就定好左侧宽度，否则「加载更多」后 maxLane 变大、已显示的行会整体右移。
      // 实测 3000 节点全量布局仅约 2ms，可忽略。
      const probe = G.buildRows(nodes, layoutOpts, { maxRows: 0 });
      // 第二趟：按当前显示条数渲染，并强制用全局泳道数，保证宽度恒定
      layout = G.buildRows(nodes, layoutOpts, {
        maxRows: state.graphRows > 0 ? state.graphRows : 0,
        laneCount: probe.laneCount,
      });
      layout.totalNodes = probe.totalNodes;
      layout.allRows = probe.rowCount;
    } catch (e) {
      // 布局异常不应拖垮整页：退化为不显示图
      return `<div class="warn-box">分支树渲染失败：${esc(e && e.message)}</div>`;
    }
    if (!layout.rowCount) return "";

    const rows = layout.cells.map((cell) => {
      // 分支标签跟随「该提交所在分支线」的颜色（与连线、圆点同色）；默认分支额外加绿环
      const colorCls = "gg-c" + ((((cell.color | 0) % 8) + 8) % 8);
      const refsHtml = cell.refs
        .map((r) => r.type === "branch"
          ? `<span class="gg-ref gg-ref-branch ${colorCls}${r.isDefault ? " gg-ref-default" : ""}" title="${r.isDefault ? "默认分支" : "分支"}">${esc(r.name)}</span>`
          : `<span class="gg-ref gg-ref-tag" title="标签">${esc(r.name)}</span>`)
        .join("");
      const title = String(cell.message || "").split("\n")[0];
      const known = !!findCommitAnywhere(cell.sha);
      return `
        <div class="gitgraph-row" ${known ? `data-commit="${esc(cell.sha)}"` : 'style="cursor:default"'}>
          <div class="gitgraph-gutter">${G.cellSvg(cell)}</div>
          <div class="gitgraph-main">
            ${refsHtml}
            <span class="gitgraph-msg">${esc(title) || (cell.stub ? "&lt;历史未导出&gt;" : "（无提交说明）")}</span>
            <span class="gitgraph-meta">
              <span class="sha" title="${esc(cell.sha)}">${shortSha(cell.sha)}</span>
              ${cell.authorName ? `<span>${esc(cell.authorName)}</span>` : ""}
              ${cell.authorDate ? `<span>${esc(fmtDate(cell.authorDate))}</span>` : ""}
            </span>
          </div>
        </div>`;
    }).join("");

    const shown = layout.rowCount;
    const total = layout.allRows != null ? layout.allRows : layout.totalNodes;
    const remain = Math.max(0, total - shown);
    const limitOptions = [[150, "最近 150 条"], [300, "最近 300 条"], [600, "最近 600 条"], [0, "全部"]]
      .map(([v, lab]) => `<option value="${v}"${state.graphLimit === v ? " selected" : ""}>${lab}</option>`)
      .join("");
    const toolbar = `
      <div class="filters gitgraph-toolbar">
        <label class="small muted">显示</label>
        <select id="graph-limit" aria-label="显示条数">${limitOptions}</select>
        <span class="small muted">共 ${total} 个提交${remain ? ` · 已显示最近 ${shown} 条` : ""}</span>
      </div>`;
    const moreBtn = remain
      ? `<div class="gitgraph-more">
           <button class="btn" id="graph-more">加载更多（还有 ${remain} 条）</button>
         </div>`
      : "";

    return `
      <div class="panel">
        <div class="panel-hd">分支树${layout.truncated ? `（仅显示最近 ${shown} / ${total} 个提交）` : ""}${layout.overflow ? `（分支线较多，可横向滚动查看）` : ""}</div>
        <div class="panel-bd gitgraph-body">
          ${toolbar}
          <div class="gitgraph">${rows}</div>
          ${moreBtn}
          <div class="gitgraph-legend">
            <span><span class="gg-dot-legend"></span> 提交</span>
            <span><span class="gg-dot-legend gg-dot-legend-merge"></span> 合并提交</span>
            <span><span class="gg-dot-legend gg-dot-legend-stub"></span> 历史未导出</span>
            <span><span class="gg-ref gg-ref-branch gg-c0">分支</span> 分支</span>
            <span><span class="gg-ref gg-ref-tag">标签</span> 标签</span>
          </div>
        </div>
      </div>`;
  }

  function renderBranches() {
    if (state.branchDetail) {
      const b = (D.branches || []).find((x) => x.name === state.branchDetail);
      return b ? renderBranchDetail(b) : `<div class="empty">未找到分支 ${esc(state.branchDetail)}</div>`;
    }

    const all = (D.branches || []).slice();
    const list = all
      .filter((b) => matchSearch(`${b.name} ${b.sha || ""}`))
      .sort((a, b) => {
        if (!!a.default !== !!b.default) return a.default ? -1 : 1;
        return String(a.name).localeCompare(String(b.name));
      });
    const hasCompare = all.some((b) => b.compare && b.compare.ahead_by != null);

    return `
      <h1 class="h1">Branches</h1>
      <p class="muted small" style="margin-top:0">默认分支 ${esc((D.meta && D.meta.default_branch) || "—")} · 共 ${all.length} 个分支${hasCompare ? "" : " · 本次导出未包含与默认分支的差异统计"}</p>
      ${renderGitGraph()}
      <div class="count-line">显示 ${list.length} 个</div>
      <div class="list">${list.map(branchRow).join("") || `<div class="empty">无分支数据（导出包可能未包含 branches.js）</div>`}</div>
    `;
  }

  function renderBranchDetail(b) {
    const cmp = b.compare || {};
    const commits = cmp.commits || [];
    const files = cmp.files || [];
    const known = (sha) => !!findCommitAnywhere(sha);
    return `
      <button class="back-link" data-nav-back>← 返回</button>
      <div class="detail-hd">
        <div>
          <h1 class="detail-title mono">${esc(b.name)}</h1>
          <div class="detail-meta">
            ${b.default ? `<span class="pill pill-green">默认分支</span>` : ""}
            ${b.protected ? `<span class="pill">Protected</span>` : ""}
            <span class="sha">${esc(b.sha || "")}</span>
          </div>
        </div>
      </div>
      <div class="panel">
        <div class="panel-hd">头部提交</div>
        <div class="panel-bd">
          ${b.head ? `
            <div class="branch-head-msg">${esc(String(b.head.message || "").split("\n")[0] || "（无提交说明）")}</div>
            <div class="row-meta">
              <span>${esc(b.head.author_name || "—")}</span>
              <span>${esc(fmtDate(b.head.author_date))}</span>
              ${known(b.sha) ? `<span class="small muted">该提交在镜像中，可在 Commits 页查看</span>` : ""}
            </div>` : `<div class="empty">本次导出未包含头部提交信息</div>`}
        </div>
      </div>
      ${cmp.ahead_by != null || cmp.behind_by != null ? `
        <div class="panel">
          <div class="panel-hd">与默认分支的差异</div>
          <div class="panel-bd">
            <div class="row-meta">
              <span class="stat-add">领先 ${cmp.ahead_by != null ? cmp.ahead_by : "—"} 个提交</span>
              <span class="stat-del">落后 ${cmp.behind_by != null ? cmp.behind_by : "—"} 个提交</span>
              ${cmp.merge_base ? `<span>分叉点 <span class="sha">${shortSha(cmp.merge_base)}</span></span>` : ""}
              ${cmp.truncated ? `<span class="small muted">（提交列表已截断）</span>` : ""}
            </div>
          </div>
        </div>` : ""}
      ${commits.length ? `
        <h2 class="h2">该分支独有的提交 (${commits.length})</h2>
        <div class="list" style="margin-bottom:18px">
          ${commits.map((c) => {
            const node = findGraphNode(c.sha);
            const cFiles = (node && node.files) || [];
            const nFiles = cFiles.length;
            return `
            <div class="row" ${known(c.sha) ? `data-commit="${esc(c.sha)}"` : 'style="cursor:default"'}>
              <div class="row-main">
                <div class="row-title">${esc(String(c.message || "").split("\n")[0])}</div>
                <div class="row-meta">
                  <span class="sha" title="${esc(c.sha || "")}">${shortSha(c.sha)}</span>
                  <span>${esc(c.author_name || (c.author && c.author.login) || "—")}</span>
                  <span>${esc(fmtDate(c.author_date))}</span>
                  ${nFiles ? `<span>${nFiles} 文件</span>` : ""}
                  ${node && node.stats && node.stats.additions != null ? `<span class="stat-add">+${node.stats.additions}</span>` : ""}
                  ${node && node.stats && node.stats.deletions != null ? `<span class="stat-del">-${node.stats.deletions}</span>` : ""}
                </div>
                ${nFiles ? `
                  <details class="branch-commit-diff">
                    <summary>查看文件变更 (${nFiles})</summary>
                    ${cFiles.map(fileDiffHtml).join("")}
                  </details>` : ""}
              </div>
            </div>`;
          }).join("")}
        </div>` : ""}
      ${files.length ? `
        <h2 class="h2">文件变更 (${files.length})</h2>
        ${files.map(fileDiffHtml).join("")}` : ""}
    `;
  }

  /* ---------- main render ---------- */
  function render() {
    const el = $("#content");
    let html = "";
    switch (state.view) {
      case "overview": html = renderOverview(); break;
      case "prs": html = renderPrs(); break;
      case "issues": html = renderIssues(); break;
      case "commits": html = renderCommits(); break;
      case "tags": html = renderTags(); break;
      case "branches": html = renderBranches(); break;
      case "compare": html = renderCompare(); break;
      case "files": html = renderFiles(); break;
      case "readme": html = renderReadme(); break;
      default: html = renderOverview();
    }
    el.innerHTML = html;
    bindContent();
    // 贡献墙默认停在最右侧（最新）；下拉变更后也会经 render 再次拉回最右
    if (state.view === "overview") scrollContribToEnd();
  }

  /* ---------- 图片：右键在新标签页打开原图（保留当前页） ---------- */
  function openImageInNewTab(src) {
    if (!src) return;
    try {
      const url = new URL(src, window.location.href).href;
      window.open(url, "_blank", "noopener,noreferrer");
    } catch (e) {
      window.open(src, "_blank", "noopener");
    }
  }

  function enhanceMarkdownImages(root) {
    if (!root) return;
    root.querySelectorAll("img.md-img, .markdown-body img, .md-table img").forEach((img) => {
      img.removeAttribute("width");
      img.removeAttribute("height");
      img.classList.add("md-img");
      const src = img.getAttribute("src") || "";
      if (!img.getAttribute("data-full-src")) {
        img.setAttribute("data-full-src", src);
      }
      const alt = img.getAttribute("alt") || "";
      img.setAttribute("title", alt ? `${alt} · 右键新标签打开原图` : "右键新标签打开原图");
      if (!img.dataset.mdBound) {
        img.dataset.mdBound = "1";
        img.addEventListener("contextmenu", (e) => {
          e.preventDefault();
          const full = img.getAttribute("data-full-src") || img.getAttribute("src") || "";
          openImageInNewTab(full);
        });
      }
    });
    root.querySelectorAll("table").forEach((table) => {
      if (table.querySelector("td img, th img, .md-img-cell")) {
        table.classList.add("md-img-compare");
        table.querySelectorAll("td, th").forEach((cell) => {
          if (cell.querySelector(".md-img-cell")) return;
          if (!cell.querySelector("img")) {
            cell.classList.add("md-img-cell-empty");
            return;
          }
          const wrap = document.createElement("div");
          wrap.className = "md-img-cell";
          while (cell.firstChild) wrap.appendChild(cell.firstChild);
          cell.appendChild(wrap);
        });
      }
    });
  }

  function bindContent() {
    const content = $("#content");

    content.querySelectorAll("[data-pr]").forEach((n) => {
      n.addEventListener("click", () => setView("prs", { prDetail: Number(n.dataset.pr), clearDetails: true }));
    });
    content.querySelectorAll("[data-issue]").forEach((n) => {
      n.addEventListener("click", () => setView("issues", { issueDetail: Number(n.dataset.issue), clearDetails: true }));
    });
    content.querySelectorAll("[data-commit]").forEach((n) => {
      n.addEventListener("click", () => setView("commits", { commitDetail: n.dataset.commit, clearDetails: true }));
    });
    content.querySelectorAll("[data-release]").forEach((n) => {
      n.addEventListener("click", () => setView("tags", { releaseDetail: n.dataset.release, clearDetails: true }));
    });
    content.querySelectorAll("[data-branch]").forEach((n) => {
      n.addEventListener("click", () => setView("branches", { branchDetail: n.dataset.branch, clearDetails: true }));
    });
    content.querySelectorAll("[data-nav-back]").forEach((n) => {
      n.addEventListener("click", () => goBack());
    });
    content.querySelectorAll("[data-pr-filter]").forEach((n) => {
      n.addEventListener("click", () => {
        state.prFilter = n.dataset.prFilter;
        render();
      });
    });
    // Commit 页条数选择 / 加载更多：属于同页显示控制，不入导航历史
    const commitLimitSel = $("#commit-limit");
    if (commitLimitSel) {
      commitLimitSel.addEventListener("change", () => {
        const v = Number(commitLimitSel.value);
        state.commitLimit = v;
        state.commitRows = v > 0 ? v : 0; // 0 表示全部
        render();
      });
    }
    const commitMoreBtn = $("#commit-more");
    if (commitMoreBtn) {
      commitMoreBtn.addEventListener("click", () => {
        state.commitRows = (state.commitRows > 0 ? state.commitRows : 0) + 150;
        render();
      });
    }

    // 分支树条数选择 / 加载更多：属于同页显示控制，不入导航历史
    const graphLimitSel = $("#graph-limit");
    if (graphLimitSel) {
      graphLimitSel.addEventListener("change", () => {
        const v = Number(graphLimitSel.value);
        state.graphLimit = v;
        // 切档位即重置显示条数：0 表示全部
        state.graphRows = v > 0 ? v : 0;
        render();
      });
    }
    const graphMoreBtn = $("#graph-more");
    if (graphMoreBtn) {
      graphMoreBtn.addEventListener("click", () => {
        // 每次追加一档（150）；若当前档位为「全部」则无按钮
        state.graphRows = (state.graphRows > 0 ? state.graphRows : 0) + 150;
        render();
      });
    }

    // Commit 页 ref 切换：属于同页筛选，不入导航历史
    const commitRefSel = $("#commit-ref");
    if (commitRefSel) {
      commitRefSel.addEventListener("change", () => {
        state.commitRef = commitRefSel.value;
        // 换 ref 相当于换一批提交：回到当前档位重新看
        state.commitRows = state.commitLimit > 0 ? state.commitLimit : 0;
        render();
      });
    }
    content.querySelectorAll("[data-issue-filter]").forEach((n) => {
      n.addEventListener("click", () => {
        state.issueFilter = n.dataset.issueFilter;
        render();
      });
    });
    content.querySelectorAll("[data-path]").forEach((n) => {
      // 文件树点选属于同页切换，不入导航历史
      n.addEventListener("click", () => {
        state.filePath = n.dataset.path;
        render();
      });
    });
    const mdRawBtn = $("#md-raw-toggle");
    if (mdRawBtn) {
      mdRawBtn.addEventListener("click", () => {
        state.showMdRaw = !state.showMdRaw;
        render();
      });
    }

    // #N → PR / Issue；commit / release 内链
    content.querySelectorAll("[data-ref]").forEach((n) => {
      n.addEventListener("click", (e) => {
        e.preventDefault();
        const num = Number(n.dataset.ref);
        const type = n.dataset.refType || (findIssueOrPr(num) || {}).type;
        if (type === "pr") {
          setView("prs", { prDetail: num, clearDetails: true });
        } else if (type === "issue") {
          setView("issues", { issueDetail: num, clearDetails: true });
        } else {
          // 未知编号：跳到对应列表搜索
          state.search = "";
          const s = $("#global-search");
          if (s) s.value = "";
        }
      });
      n.addEventListener("mouseenter", (e) => showRefHover(n));
      n.addEventListener("mouseleave", () => scheduleHideHover());
    });
    content.querySelectorAll("[data-commit-ref]").forEach((n) => {
      n.addEventListener("click", (e) => {
        e.preventDefault();
        setView("commits", { commitDetail: n.dataset.commitRef, clearDetails: true });
      });
    });
    content.querySelectorAll("[data-release-ref]").forEach((n) => {
      n.addEventListener("click", (e) => {
        e.preventDefault();
        setView("tags", { releaseDetail: n.dataset.releaseRef, clearDetails: true });
      });
    });
    content.querySelectorAll("[data-goto]").forEach((n) => {
      n.addEventListener("click", () => {
        const view = n.dataset.goto;
        const filt = n.dataset.gotoFilter;
        const reset = n.dataset.gotoReset;
        pushHistory();
        // 方块数字为总数，点击后筛选重置为「全部」，避免残留上次筛选导致条数对不上
        if (view === "prs") state.prFilter = filt || (reset ? "all" : state.prFilter);
        if (view === "issues") state.issueFilter = filt || (reset ? "all" : state.issueFilter);
        clearDetails();
        setView(view, { pushHistory: false });
      });
    });

    // 贡献墙汇总时间窗：不缩放热力图；变更后将墙拉回最右（经 render → scrollContribToEnd）
    const rangeSel = $("#cw-range");
    if (rangeSel) {
      rangeSel.addEventListener("change", () => {
        state.rangeKey = rangeSel.value;
        render();
      });
    }
    const customDays = $("#cw-custom-days");
    if (customDays) {
      const applyCustomDays = () => {
        const n = Number(customDays.value);
        state.rangeCustomDays = Number.isFinite(n) && n >= 1 ? Math.floor(n) : 1;
        const tip = $("#cw-summary-text");
        if (tip) tip.innerHTML = renderRangeSummaryText(summarizeRange());
      };
      customDays.addEventListener("input", applyCustomDays);
      customDays.addEventListener("change", applyCustomDays);
    }

    // 版本对比：base/head 选择 + 结果列表的档位与加载更多（同页显示控制，不入导航历史）
    const cmpRun = $("#cmp-run");
    if (cmpRun) {
      const baseSel = $("#cmp-base");
      const headSel = $("#cmp-head");
      cmpRun.addEventListener("click", () => {
        state.compareBase = baseSel.value;
        state.compareHead = headSel.value;
        // 换对比目标相当于换一批提交：回到当前档位重新看
        state.compareRows = state.compareLimit > 0 ? state.compareLimit : 0;
        render();
      });
    }
    const cmpLimitSel = $("#cmp-limit");
    if (cmpLimitSel) {
      cmpLimitSel.addEventListener("change", () => {
        const v = Number(cmpLimitSel.value);
        state.compareLimit = v;
        state.compareRows = v > 0 ? v : 0;
        render();
      });
    }
    const cmpMoreBtn = $("#cmp-more");
    if (cmpMoreBtn) {
      cmpMoreBtn.addEventListener("click", () => {
        state.compareRows = (state.compareRows > 0 ? state.compareRows : 0) + 150;
        render();
      });
    }

    enhanceMarkdownImages(content);
  }

  function boot() {
    loadAllData();

    const m = D.meta || {};
    $("#brand-title").textContent = m.full_name || m.repo || "只读镜像";
    $("#brand-sub").textContent = `导出 ${m.exported_at ? fmtDate(m.exported_at) : "—"}`;

    $("#cnt-prs").textContent = String(D.prs.length);
    $("#cnt-issues").textContent = String(D.issues.length);
    $("#cnt-commits").textContent = String(D.commits.length);
    $("#cnt-branches").textContent = String(D.branches.length);

    $("#topbar-meta").innerHTML = m.html_url
      ? `<span class="mono small">${esc(m.html_url)}</span>`
      : "";

    $("#sidebar-foot").innerHTML = `
      内网只读 · 离线可用<br/>
      数据源：GitHub API 导出包<br/>
      ${m.private ? "私有仓库镜像" : "公开仓库镜像"}
    `;

    $$(".nav-item").forEach((btn) => {
      btn.addEventListener("click", () => {
        setView(btn.dataset.view, { clearDetails: true, clearSearch: true });
      });
    });

    const search = $("#global-search");
    let timer = null;
    search.addEventListener("input", () => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        state.search = search.value.trim();
        render();
      }, 120);
    });

    render();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
