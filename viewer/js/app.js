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
    releaseDetail: null,
    search: "",
    filePath: null,
    showMdRaw: false,
    compareBase: null,
    compareHead: null,
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
          const known = (D.commits || []).some((c) => c.sha && (c.sha === sha || c.sha.startsWith(sha)));
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
          const known = (D.commits || []).some((c) => c.sha && (c.sha === g1 || String(c.sha).startsWith(g1)));
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
    D.files = D.files || { tree: [], contents: {} };
    D.files.tree = D.files.tree || [];
    D.files.contents = D.files.contents || {};
    D.meta = D.meta || {};
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
  function setView(view, opts = {}) {
    state.view = view;
    if (opts.prDetail !== undefined) state.prDetail = opts.prDetail;
    if (opts.issueDetail !== undefined) state.issueDetail = opts.issueDetail;
    if (opts.commitDetail !== undefined) state.commitDetail = opts.commitDetail;
    if (opts.releaseDetail !== undefined) state.releaseDetail = opts.releaseDetail;
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
      <div class="row" data-commit="${esc(c.sha)}">
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

  /* ---------- overview ---------- */
  function renderOverview() {
    const m = D.meta;
    const openIssues = D.issues.filter((i) => i.state === "open").length;
    const merged = D.prs.filter((p) => p.merged).length;

    const stat = (n, label, view, filter) =>
      `<div class="stat stat-click" data-goto="${view}"${filter ? ` data-goto-filter="${filter}"` : ""} title="点击进入 ${esc(label)}">
        <div class="stat-n">${n}</div><div class="stat-l">${esc(label)}</div>
      </div>`;

    return `
      <h1 class="h1">${esc(m.full_name || m.repo || "仓库")}</h1>
      <p class="muted" style="margin-top:0">${esc(m.description || "（无描述）")}</p>
      ${m.private ? `<div class="warn-box">私有仓库只读镜像 · 导出于 ${esc(fmtDate(m.exported_at))}</div>` : ""}
      <div class="stats">
        ${stat(D.prs.length, "Pull Requests", "prs")}
        ${stat(merged, "Merged PR", "prs", "merged")}
        ${stat(D.issues.length, "Issues", "issues")}
        ${stat(openIssues, "Open Issue", "issues", "open")}
        ${stat(D.commits.length, "Commits", "commits")}
        ${stat(D.tags.length, "Tags", "tags")}
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
      <button class="back-link" data-back="prs">← 返回 PR 列表</button>
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
      <button class="back-link" data-back="issues">← 返回 Issue 列表</button>
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
      const c = D.commits.find((x) => x.sha === state.commitDetail);
      return c ? renderCommitDetail(c) : `<div class="empty">未找到 commit ${esc(shortSha(state.commitDetail))}</div>`;
    }

    const list = D.commits.filter((c) => {
      const hay = `${c.sha} ${c.message || ""} ${c.author_name || ""}`;
      return matchSearch(hay);
    });
    return `
      <h1 class="h1">Commits</h1>
      <p class="muted small" style="margin-top:0">默认分支 ${esc(D.meta.default_branch || "")} · 最多 ${D.commits.length} 条 · 点击行查看文件变更</p>
      <div class="count-line">显示 ${list.length} 条</div>
      <div class="list">
        ${list.map(commitRow).join("") || `<div class="empty">无匹配结果</div>`}
      </div>
    `;
  }

  function renderCommitDetail(c) {
    const files = c.files || [];
    const rest = (c.message || "").split("\n").slice(1).join("\n").trim();
    return `
      <button class="back-link" data-back="commits">← 返回 Commit 列表</button>
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
      <button class="back-link" data-back="tags">← 返回 Releases</button>
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
  function renderCompare() {
    const names = D.tags.map((t) => t.name);
    const opts = names.map((n) => `<option value="${esc(n)}">${esc(n)}</option>`).join("");

    let resultHtml = `<div class="empty">选择两个 Tag / 分支名，本地用已导出的 Commit 列表做近似对比。<br/>完整 diff 请在外网用导出脚本对特定 compare 拉取，或对照 PR 的文件变更。</div>`;

    if (state.compareBase && state.compareHead && state.compareBase !== state.compareHead) {
      const base = state.compareBase;
      const head = state.compareHead;
      const baseSha = (D.tags.find((t) => t.name === base) || {}).sha;
      const headSha = (D.tags.find((t) => t.name === head) || {}).sha;
      let slice = D.commits.slice();
      const headIdx = headSha ? slice.findIndex((c) => c.sha === headSha) : 0;
      const baseIdx = baseSha ? slice.findIndex((c) => c.sha === baseSha) : -1;
      let between = [];
      if (baseIdx >= 0 && headIdx >= 0) {
        const [a, b] = headIdx < baseIdx ? [headIdx, baseIdx] : [baseIdx, headIdx];
        between = slice.slice(a, b + 1);
      } else if (baseIdx >= 0) {
        between = slice.slice(0, baseIdx);
      } else {
        between = slice.slice(0, 30);
      }

      resultHtml = `
        <div class="warn-box">本地对比基于已导出的 ${D.commits.length} 条 commit，可能不完整。导出更全的 commit 历史可增大 --max-commits。</div>
        <div class="panel">
          <div class="panel-hd">${esc(base)} … ${esc(head)} · 约 ${between.length} 个提交</div>
          <div class="panel-bd" style="padding:0">
            ${between.map(commitRow).join("") || `<div class="empty">未在镜像中找到这两个点之间的提交</div>`}
          </div>
        </div>`;
    }

    return `
      <h1 class="h1">版本对比</h1>
      <p class="muted small" style="margin-top:0">选择 base → head，查看导出镜像中落在两者之间的 commit。</p>
      <div class="filters">
        <label class="small muted">base</label>
        <select id="cmp-base">${opts}</select>
        <label class="small muted">head</label>
        <select id="cmp-head">${opts}</select>
        <button class="btn btn-primary" id="cmp-run">对比</button>
      </div>
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
      case "compare": html = renderCompare(); break;
      case "files": html = renderFiles(); break;
      case "readme": html = renderReadme(); break;
      default: html = renderOverview();
    }
    el.innerHTML = html;
    bindContent();
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
      n.addEventListener("click", () => setView("prs", { prDetail: Number(n.dataset.pr) }));
    });
    content.querySelectorAll("[data-issue]").forEach((n) => {
      n.addEventListener("click", () => setView("issues", { issueDetail: Number(n.dataset.issue) }));
    });
    content.querySelectorAll("[data-commit]").forEach((n) => {
      n.addEventListener("click", () => setView("commits", { commitDetail: n.dataset.commit }));
    });
    content.querySelectorAll("[data-release]").forEach((n) => {
      n.addEventListener("click", () => setView("tags", { releaseDetail: n.dataset.release }));
    });
    content.querySelectorAll("[data-back]").forEach((n) => {
      n.addEventListener("click", () => {
        const v = n.dataset.back;
        if (v === "prs") setView(v, { prDetail: null });
        else if (v === "issues") setView(v, { issueDetail: null });
        else if (v === "commits") setView(v, { commitDetail: null });
        else if (v === "tags") setView(v, { releaseDetail: null });
        else setView(v);
      });
    });
    content.querySelectorAll("[data-pr-filter]").forEach((n) => {
      n.addEventListener("click", () => {
        state.prFilter = n.dataset.prFilter;
        render();
      });
    });
    content.querySelectorAll("[data-issue-filter]").forEach((n) => {
      n.addEventListener("click", () => {
        state.issueFilter = n.dataset.issueFilter;
        render();
      });
    });
    content.querySelectorAll("[data-path]").forEach((n) => {
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
          state.releaseDetail = null;
          setView("prs", { prDetail: num, issueDetail: null, commitDetail: null });
        } else if (type === "issue") {
          state.releaseDetail = null;
          setView("issues", { issueDetail: num, prDetail: null, commitDetail: null });
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
        setView("commits", { commitDetail: n.dataset.commitRef });
      });
    });
    content.querySelectorAll("[data-release-ref]").forEach((n) => {
      n.addEventListener("click", (e) => {
        e.preventDefault();
        setView("tags", { releaseDetail: n.dataset.releaseRef });
      });
    });
    content.querySelectorAll("[data-goto]").forEach((n) => {
      n.addEventListener("click", () => {
        const view = n.dataset.goto;
        const filt = n.dataset.gotoFilter;
        if (view === "prs" && filt) state.prFilter = filt;
        if (view === "issues" && filt) state.issueFilter = filt;
        if (view === "commits") state.commitDetail = null;
        setView(view);
      });
    });

    const cmpRun = $("#cmp-run");
    if (cmpRun) {
      const baseSel = $("#cmp-base");
      const headSel = $("#cmp-head");
      if (baseSel && !state.compareBase) baseSel.value = D.tags[0] ? D.tags[0].name : "";
      if (headSel && !state.compareHead) headSel.value = D.tags[1] ? D.tags[1].name : (D.tags[0] ? D.tags[0].name : "");
      if (baseSel && state.compareBase) baseSel.value = state.compareBase;
      if (headSel && state.compareHead) headSel.value = state.compareHead;
      cmpRun.addEventListener("click", () => {
        state.compareBase = baseSel.value;
        state.compareHead = headSel.value;
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
        state.search = "";
        const s = $("#global-search");
        if (s) s.value = "";
        state.prDetail = null;
        state.issueDetail = null;
        state.commitDetail = null;
        state.releaseDetail = null;
        setView(btn.dataset.view);
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
