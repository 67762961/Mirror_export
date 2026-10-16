/* GitHub 只读镜像 · 分支树泳道图（纯函数，无 DOM 依赖）
 *
 * 设计：本模块只做「数据 → 布局」的纯计算，返回可直接渲染的几何信息；
 * 具体 DOM 拼接在 app.js 里完成。好处是布局算法能在 Node 里单测，
 * 也避免把拓扑排序与泳道分配的复杂度混进查看器主逻辑。
 *
 * 三条关键设计（改代码前先理解，否则很容易改坏观感）：
 *   1) 泳道会「回收复用」：合并提交把分支收回时，紧接着的新分支优先复用刚腾出的泳道，
 *      于是「从主干拉出 → 合并回主干」这种最常见的 PR 模式只占 1 条泳道宽度，
 *      不会每合并一次就往右扩一格。
 *   2) 颜色跟着「分支线」走，而不是跟着「泳道号」走：同一个泳道在上下两行可能承载
 *      两条不同分支（上面那条收进来、下面那条放出去），所以颜色按线段逐条记录。
 *   3) 颜色在「分支头（ref）」处切换：一条线属于哪个分支，取决于它向下走时最近经过的
 *      分支头。这样「next 领先 main 且未合并」时，next 的独有提交是 next 的颜色，
 *      跨过 main 的 tip 之后才变回 main 的颜色；而被合并回主干的 PR 分支仍保留自己的色。
 *
 * 对外接口（挂在 window.GH_GRAPH）：
 *   topoOrder(nodes)             -> 拓扑排序后的节点数组（子在前、父在后）
 *   buildRows(nodes, refs, opts) -> { cells, maxLane, rowCount, truncated, totalNodes, overflow }
 *   cellSvg(cell, opts)          -> string（单行泳道 SVG）
 */
(function (global) {
  "use strict";

  // 单行高度与泳道宽度（与 css 中的 .gitgraph-row / .gitgraph-gutter 保持一致）
  // 行高固定，保证 SVG 正好铺满一行、跨行连线不错位。
  // 取 26/14 让「水平跨距(14) ≈ 垂直跨距(13)」：拐角贝塞尔才逼近四分之一圆弧，
  // 跨距比越大曲线越扁、越像折线（这是「切角够不够圆」的关键，别随意改这两个数）。
  var ROW_H = 26;
  var LANE_W = 14;
  var DOT_R = 4;
  // 合并提交用更大的空心圆（与 VSCode Git Graph 一致）
  var MERGE_DOT_R = 5;
  // 颜色总数：css 里定义了 .gg-c0..7
  var LANE_COLORS = 8;
  // 默认分支固定用 0 号色，其余分支在 1..LANE_COLORS-1 之间循环，避免与主干撞色
  var MAIN_COLOR = 0;
  var BRANCH_COLORS = LANE_COLORS - 1;
  // 单侧最多渲染多少条泳道；超出只标记 overflow（图可横向滚动），不截断数据
  var MAX_LANES = 8;
  // 贝塞尔控制点系数：0.5523 是四分之一圆的三次贝塞尔逼近值，比 0.5 更圆
  var TANGENT_K = 0.5523;

  function normDate(n) {
    return (n && (n.author_date || n.committer_date)) || "";
  }

  /* 新的排前面：日期大者优先；日期相同用 sha 兜底保证稳定 */
  function isNewer(a, b) {
    var da = normDate(a);
    var db = normDate(b);
    if (da !== db) return da > db;
    return String(a.sha || "") > String(b.sha || "");
  }

  /* 拓扑排序：保证「子提交排在父提交之前」，同级按日期新→旧。
     先用 Kahn（入度 = 未排出的子节点数），再把环中/未被覆盖的节点按日期补在末尾，
     保证任何输入都不丢节点、不死循环。 */
  function topoOrder(nodes) {
    var bySha = new Map();
    nodes.forEach(function (n) {
      if (n && n.sha) bySha.set(n.sha, n);
    });

    var pending = new Map(); // sha -> 尚未排出的子节点数
    nodes.forEach(function (n) {
      if (n && n.sha) pending.set(n.sha, 0);
    });
    nodes.forEach(function (n) {
      if (!n || !n.sha) return;
      (n.parents || []).forEach(function (p) {
        if (bySha.has(p)) pending.set(p, (pending.get(p) || 0) + 1);
      });
    });

    var ready = nodes.filter(function (n) {
      return n && n.sha && pending.get(n.sha) === 0;
    });
    var emitted = new Set();
    var out = [];

    while (ready.length) {
      // 每轮挑「最新」的一个，保证图形按时间从上到下
      var bi = 0;
      for (var i = 1; i < ready.length; i++) {
        if (isNewer(ready[i], ready[bi])) bi = i;
      }
      var n = ready.splice(bi, 1)[0];
      if (emitted.has(n.sha)) continue;
      emitted.add(n.sha);
      out.push(n);
      (n.parents || []).forEach(function (p) {
        if (!bySha.has(p) || emitted.has(p)) return;
        var left = (pending.get(p) || 0) - 1;
        pending.set(p, left);
        if (left <= 0 && !emitted.has(p)) ready.push(bySha.get(p));
      });
    }

    // 环或异常输入：剩余节点按日期新→旧追加（不死循环、不丢数据）
    var rest = nodes.filter(function (n) {
      return n && n.sha && !emitted.has(n.sha);
    });
    rest.sort(function (a, b) {
      return isNewer(a, b) ? -1 : isNewer(b, a) ? 1 : 0;
    });
    rest.forEach(function (n) {
      out.push(n);
    });
    return out;
  }

  /* 泳道分配 + 逐行线段计算。
     线段坐标用「泳道号 + 0..1 的行内纵向比例」表示，渲染时再换算成像素；
     颜色号按线段记录（见文件头说明 2、3）。

     opts.maxRows：最多布局多少行。**不传 = 默认 150**；显式传 0 或负数 = 不限
     （「不限」用于「显示全部」与首屏探测全局泳道数）。
     opts.laneCount：强制统一的泳道列数（用于「加载更多」时保持左侧宽度不变）。 */
  function buildRows(nodes, refs, opts) {
    opts = opts || {};
    var rawMax = opts.maxRows;
    var unlimited = rawMax === 0 || (typeof rawMax === "number" && rawMax < 0);
    var maxRows = unlimited ? Infinity : (rawMax > 0 ? rawMax : 150);
    var maxLanes = opts.maxLanes > 0 ? opts.maxLanes : MAX_LANES;

    var bySha = new Map();
    (nodes || []).forEach(function (n) {
      if (n && n.sha) bySha.set(n.sha, n);
    });

    var ordered = topoOrder(Array.from(bySha.values()));
    var truncated = ordered.length > maxRows;
    if (truncated) ordered = ordered.slice(0, maxRows);

    // 引用表：sha -> [{type, name, isDefault}]（用于展示标签）
    var refMap = new Map();
    function addRef(sha, ref) {
      if (!sha) return;
      if (!refMap.has(sha)) refMap.set(sha, []);
      refMap.get(sha).push(ref);
    }
    var branches = (refs && refs.branches) || {};
    var tags = (refs && refs.tags) || {};
    var def = (refs && refs.defaultBranch) || "";
    Object.keys(branches).forEach(function (name) {
      addRef(branches[name], { type: "branch", name: name, isDefault: name === def });
    });
    Object.keys(tags).forEach(function (name) {
      addRef(tags[name], { type: "tag", name: name, isDefault: false });
    });

    // 分支头表：sha -> {name, isDefault}。同一提交被多个分支指向时，默认分支优先。
    var headAt = new Map();
    Object.keys(branches).forEach(function (name) {
      var sha = branches[name];
      if (!sha) return;
      var isDefault = name === def;
      var cur = headAt.get(sha);
      if (!cur || (isDefault && !cur.isDefault)) {
        headAt.set(sha, { name: name, isDefault: isDefault });
      }
    });

    // 分支名 -> 固定色号（主干恒为 MAIN_COLOR，其余在 1..7 循环）
    var branchColorMap = new Map();
    var colorSeq = 0;
    function nextBranchColor() {
      var c = MAIN_COLOR + 1 + (colorSeq % BRANCH_COLORS);
      colorSeq += 1;
      return c;
    }
    function colorForBranch(head) {
      if (head.isDefault) return MAIN_COLOR;
      if (!branchColorMap.has(head.name)) branchColorMap.set(head.name, nextBranchColor());
      return branchColorMap.get(head.name);
    }

    var lanes = [];        // lanes[i] = 该泳道正在等待的 sha（null = 空闲）
    var laneColor = [];    // laneColor[i] = 该泳道当前这条线的色号
    var laneBranch = [];   // laneBranch[i] = 该泳道当前属于哪个分支头（用于调试/扩展）
    var cells = [];
    var maxLane = 0;
    var overflow = false;

    /* 取一条空闲泳道：优先 prefer 列表里的（用于回收刚合并掉的泳道），
       其次最左空闲，都没有才向右扩一条。 */
    function allocFree(prefer) {
      var i;
      if (prefer && prefer.length) {
        for (i = 0; i < prefer.length; i++) {
          var p = prefer[i];
          if (p < lanes.length && lanes[p] === null) return p;
        }
      }
      for (i = 0; i < lanes.length; i++) {
        if (lanes[i] === null) return i;
      }
      lanes.push(null);
      laneColor.push(null);
      laneBranch.push(null);
      return lanes.length - 1;
    }

    /* 新占一条泳道时确定它的颜色：父/子提交若是分支头就用该分支的颜色，
       否则给一个新分支色（保证不同分支线颜色不同）。 */
    function seedLane(laneIdx, sha) {
      var head = headAt.get(sha);
      if (head) {
        laneBranch[laneIdx] = head.name;
        laneColor[laneIdx] = colorForBranch(head);
      } else {
        laneBranch[laneIdx] = null;
        laneColor[laneIdx] = nextBranchColor();
      }
    }

    for (var idx = 0; idx < ordered.length; idx++) {
      var node = ordered[idx];
      var sha = node.sha;
      var above = lanes.slice();
      var aboveColor = laneColor.slice();

      // 1) 选承载本提交的泳道：优先复用「正等待该提交」的泳道
      var lane = lanes.indexOf(sha);
      if (lane === -1) {
        lane = allocFree(null);
        laneColor[lane] = null;
        laneBranch[lane] = null;
        seedLane(lane, sha);
      } else {
        // 续用已有泳道：若本提交是分支头，颜色在此切换
        // （这是「next 领先 main」这类场景的关键：跨过 main 的 tip 才变回主干色）
        var headHere = headAt.get(sha);
        if (headHere) {
          laneBranch[lane] = headHere.name;
          laneColor[lane] = colorForBranch(headHere);
        } else if (laneColor[lane] == null) {
          seedLane(lane, sha);
        }
      }
      var commitColor = laneColor[lane];

      // 2) 其余同样等待该提交的泳道在本行收束（合并点），随后腾空待复用
      var merged = [];
      for (var j = 0; j < lanes.length; j++) {
        if (j !== lane && lanes[j] === sha) {
          merged.push(j);
          lanes[j] = null;
          laneColor[j] = null;
          laneBranch[j] = null;
        }
      }

      // 3) 本泳道接上第一个父提交（同一条线延续，颜色不变）
      var parents = (node.parents || []).filter(function (p) {
        return bySha.has(p);
      });
      lanes[lane] = parents.length ? parents[0] : null;
      laneColor[lane] = parents.length ? commitColor : null;
      if (!parents.length) laneBranch[lane] = null;

      // 4) 合并提交的其余父提交：各自需要一条泳道。
      //    优先回收本行刚合并掉的泳道（形成「出去又回来」的单宽度）；
      //    颜色按该父提交是否为分支头决定，因此合并回主干的线用的是分支自己的颜色。
      var extraLanes = [];
      for (var k = 1; k < parents.length; k++) {
        var pl = lanes.indexOf(parents[k]);
        if (pl === -1 || merged.indexOf(pl) >= 0) {
          pl = allocFree(merged);
          laneColor[pl] = null;
          laneBranch[pl] = null;
          seedLane(pl, parents[k]);
          lanes[pl] = parents[k];
        }
        extraLanes.push(pl);
      }

      var below = lanes.slice();
      var laneCount = Math.max(above.length, below.length);

      // 5) 生成线段（每条线段自带颜色号）
      var segments = [];
      for (var c = 0; c < laneCount; c++) {
        if (c === lane) continue; // 本行提交所在泳道单独处理
        var a = c < above.length && above[c] !== null;
        var b = c < below.length && below[c] !== null;
        var isExtra = extraLanes.indexOf(c) >= 0;
        if (isExtra) {
          // 新建/回收的父泳道：下方那段由 parentLanes 画，这里只画「从上方汇入本提交」
          if (a) segments.push({ x1: c, y1: 0, x2: lane, y2: 0.5, color: aboveColor[c] });
          continue;
        }
        if (a && b) {
          segments.push({ x1: c, y1: 0, x2: c, y2: 1, color: aboveColor[c] });        // 贯穿
        } else if (a) {
          segments.push({ x1: c, y1: 0, x2: lane, y2: 0.5, color: aboveColor[c] });   // 汇入本提交
        } else if (b) {
          segments.push({ x1: lane, y1: 0.5, x2: c, y2: 1, color: laneColor[c] });    // 分出新泳道
        }
      }
      // 本泳道自身的上半段（用「进来那条线」的颜色，颜色切换行才会分成两色）
      if (lane < above.length && above[lane] !== null) {
        segments.push({ x1: lane, y1: 0, x2: lane, y2: 0.5, color: aboveColor[lane] });
      }
      // 指向父提交的连线：第一个父延续本泳道，其余父各指向自己的泳道
      if (parents.length) {
        segments.push({ x1: lane, y1: 0.5, x2: lane, y2: 1, color: laneColor[lane] });
      }
      extraLanes.forEach(function (pl) {
        segments.push({ x1: lane, y1: 0.5, x2: pl, y2: 1, color: laneColor[pl] });
      });

      // 6) 收尾：裁掉尾部空闲泳道，避免图越来越宽
      while (lanes.length && lanes[lanes.length - 1] === null) {
        lanes.pop();
        laneColor.pop();
        laneBranch.pop();
      }

      if (lane > maxLane) maxLane = lane;
      if (lane >= maxLanes) overflow = true;

      cells.push({
        sha: sha,
        lane: lane,
        laneCount: laneCount,
        segments: segments,
        // 圆点颜色 = 本提交所属分支线的颜色（颜色切换行取切换后的颜色）
        color: commitColor != null ? commitColor : aboveColor[lane],
        refs: refMap.get(sha) || [],
        stub: !!node.stub,
        // 合并提交（有效父提交 ≥2）：渲染成更重的空心圆
        isMerge: parents.length >= 2,
        message: node.message || "",
        authorName: node.author_name || (node.author && node.author.login) || "",
        authorDate: node.author_date || node.committer_date || "",
        parents: (node.parents || []).slice(),
        hasDiff: !!node.has_diff || !!node.files,
      });
    }

    // 统一左侧留白宽度：默认用本次布局的最大泳道数；
    // 若调用方指定了 laneCount（「加载更多」时传首屏探测到的全局值），
    // 则用它兜底——否则追加内容后 maxLane 变大，已显示的行会整体右移。
    var gutterLanes = Math.max(maxLane + 1, opts.laneCount > 0 ? opts.laneCount : 0);
    cells.forEach(function (cell) {
      cell.laneCount = Math.max(cell.laneCount, gutterLanes);
    });

    return {
      cells: cells,
      maxLane: Math.max(maxLane, gutterLanes - 1),
      laneCount: gutterLanes,
      rowCount: cells.length,
      truncated: truncated,
      totalNodes: bySha.size,
      overflow: overflow,
      maxLanes: maxLanes,
    };
  }

  /* 单行泳道 SVG。坐标全部为数字，无字符串注入面。
     观感对齐 VSCode Git Graph：
       · 同泳道走直线；跨泳道走三次贝塞尔曲线；
       · 曲线在「圆点端」用水平切线、在「泳道端」用垂直切线，
         两段在圆点处自然接成一个圆角肘（而不是尖角折线）；
       · 线段按各自的分支线颜色着色；
       · 合并提交（父提交 ≥2）画更大的空心圆，普通提交画实心圆；
       · stub 节点画虚线空心圆。 */
  function cellSvg(cell, opts) {
    opts = opts || {};
    var laneW = opts.laneW || LANE_W;
    var rowH = opts.rowH || ROW_H;
    var dotR = opts.dotR || DOT_R;
    var width = Math.max(1, cell.laneCount) * laneW;

    function px(laneNum) {
      return (laneNum + 0.5) * laneW;
    }
    // 保留一位小数，避免浮点长尾撑大 SVG
    function num(v) {
      return Math.round(v * 10) / 10;
    }
    function colorClass(c) {
      var n = (c | 0) % LANE_COLORS;
      if (n < 0) n += LANE_COLORS;
      return "gg-c" + n;
    }

    var parts = [];
    cell.segments.forEach(function (s) {
      var colorCls = colorClass(s.color);
      var x1 = px(s.x1);
      var x2 = px(s.x2);
      var y1 = s.y1 * rowH;
      var y2 = s.y2 * rowH;
      var d;
      if (x1 === x2) {
        d = "M" + num(x1) + " " + num(y1) + "L" + num(x2) + " " + num(y2);
      } else {
        var dx = x2 - x1;
        var dy = y2 - y1;
        // 端点在圆点（y=0.5）→ 水平切线；在泳道边缘 → 垂直切线
        var h1 = s.y1 === 0.5;
        var h2 = s.y2 === 0.5;
        var c1x = h1 ? x1 + dx * TANGENT_K : x1;
        var c1y = h1 ? y1 : y1 + dy * TANGENT_K;
        var c2x = h2 ? x2 - dx * TANGENT_K : x2;
        var c2y = h2 ? y2 : y2 - dy * TANGENT_K;
        d = "M" + num(x1) + " " + num(y1) +
            "C" + num(c1x) + " " + num(c1y) + " " + num(c2x) + " " + num(c2y) + " " + num(x2) + " " + num(y2);
      }
      parts.push('<path class="' + colorCls + '" d="' + d + '"/>');
    });

    // 圆点：合并 / 普通 / stub 三种形态
    var dotColor = colorClass(cell.color);
    var cx = px(cell.lane);
    var cy = rowH / 2;
    if (cell.stub) {
      parts.push('<circle class="gg-dot gg-dot-stub" cx="' + num(cx) + '" cy="' + num(cy) + '" r="' + dotR + '"/>');
    } else if (cell.isMerge) {
      // 合并提交：白心 + 彩色描边，视觉上比普通提交更重
      parts.push('<circle class="gg-dot gg-dot-merge ' + dotColor + '" cx="' + num(cx) + '" cy="' + num(cy) + '" r="' + MERGE_DOT_R + '"/>');
    } else {
      parts.push('<circle class="gg-dot ' + dotColor + '" cx="' + num(cx) + '" cy="' + num(cy) + '" r="' + dotR + '"/>');
    }

    return (
      '<svg class="gitgraph-svg" width="' + width + '" height="' + rowH + '" ' +
      'viewBox="0 0 ' + width + " " + rowH + '" aria-hidden="true">' +
      '<g class="gg-lines">' + parts.join("") + "</g></svg>"
    );
  }

  global.GH_GRAPH = {
    ROW_H: ROW_H,
    LANE_W: LANE_W,
    DOT_R: DOT_R,
    MERGE_DOT_R: MERGE_DOT_R,
    LANE_COLORS: LANE_COLORS,
    MAIN_COLOR: MAIN_COLOR,
    MAX_LANES: MAX_LANES,
    TANGENT_K: TANGENT_K,
    topoOrder: topoOrder,
    buildRows: buildRows,
    cellSvg: cellSvg,
  };
})(typeof window !== "undefined" ? window : globalThis);
