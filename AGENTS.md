### 项目概况与技术栈
- 本项目是 Mirror_export（GitHub 内网只读镜像导出工具）：在外网拉取仓库 PR / Issue / Commit / Tag / Release / 文件树，生成可拷入内网、用浏览器离线浏览的自包含静态站。
- 后端导出基于 Python 3.8+ 标准库（`urllib` / `json` / `argparse`），无 pip 依赖；图形界面使用 Tkinter（`app_gui.py`），可打包为 `dist/Mirror_export.exe`。
- 前端查看器为纯静态 HTML/CSS/JS（`viewer/`），数据以 `data/*.js` 注入 `window.GH_DATA`，兼容 `file://` 协议，无需本地服务器。
- 内网侧只需任意现代浏览器，不依赖 Python、Node 或网络。

### 核心命令
- 先思考再编码；如有歧义，先确认需求再修改。
- 简单优先，不增加无关功能与重构。
- 精确修改，只改和任务相关的代码、注释和文档。
- 代码注释和说明文档统一使用中文。
- 代码与 .md 文件用 UTF-8 编码；必要时保持 CRLF 格式。
- 修改导出逻辑、增量缓存、数据结构或查看器视图前，必须先阅读 ARCHITECTURE.md，理解数据流和模块边界。
- 修改后要同步检查 README.md、ARCHITECTURE.md、AGENTS.md 中的目录结构、CLI 参数与 data 字段说明。

### 编码规范
- 导出核心统一放在 `export.py`：HTTP、分页、字段裁剪、增量合并、写出 `data/*.js`。
- `app_gui.py` 只负责界面与参数收集，通过 `export_mod.export(args)` 调用核心；进度依赖 stdout 中的 `__PROGRESS__{json}` 机器行。
- 进度由 `export.py` 顶部的 `STAGES` 阶段表驱动，**新增阶段必须在此登记**，否则该段进度会缺失；阶段内高频循环用 `make_throttle(1)` 按每 1% 节流上报，且**命中增量缓存的条目也要计入 done**（上报点放在 `continue` 之前），否则纯增量导出时进度条会长时间不动。`_SEEN_STAGES` / `_STAGE_DECLARED` / `_LAST_OVERALL` 是**单轮状态**，同一进程内连续导出（GUI 切换组合再导出）必须在 `export()` 开头调用 `reset_progress()`，否则上一轮遗留的 100% 会让新一轮首帧直接冲到 100%。机器行字段与跳过阶段的折算规则见 ARCHITECTURE.md「进度协议」。
- 查看器数据文件命名固定：`meta` / `prs` / `issues` / `commits` / `tags` / `releases` / `files` / `branches` / `graph`；新增数据集时须同步更新 `export.py` 的 `writes`、`manifest.json`、`viewer/index.html` 的 script 标签。
- **新增 `viewer/` 下的文件时，必须同步 `export.copy_viewer()` 的白名单**（`index.html` / `css/app.css` / `js/app.js` / `js/gitgraph.js`），否则导出包缺文件、内网打开直接报错（`build_exe.py` 整目录打包 `viewer/`，无需改）。分支树的布局算法放在 `viewer/js/gitgraph.js`，是**无 DOM 依赖的纯函数**（拓扑排序 / 泳道分配 / SVG 生成），须保持可在 Node 里直接单测；渲染拼接只写在 `app.js`。
- 分支树的观感约定（改样式时注意这几条联动，改一处要同步另一处）：① `gitgraph.js` 的 `ROW_H` 必须等于 css `.gitgraph-row` 的 `height`，且**分隔线只能用 `box-shadow` 不能用 `border`**——`border` 占掉 1px 内容高度会让相邻行 SVG 出现缝隙、竖线被截断成虚线；② 配色只在 css `.gg-c0..7` 里用 `--gg-c` / `--gg-bg` 定义一次，连线、圆点、标签都引用它，禁止各写一遍颜色；③ 跨泳道连线用三次贝塞尔曲线、同泳道用直线，控制点系数用 `TANGENT_K = 0.5523`（四分之一圆逼近值）且 `LANE_W≈ROW_H/2`，否则拐角发扁像折线；合并提交用更大的空心圆；④ **颜色跟「分支线」走、不跟泳道号**，并在**分支头处切换色号**（色号 0 固定给默认分支，其余 1..7 循环）；⑤ 泳道必须**回收复用**，保证「PR 拉出再合并回主干」只占 1 条泳道宽度。
- 分支树条数控制与「加载更多」：`buildRows` 的 `maxRows` **不传=默认 150、显式 0 或负数=不限**；渲染前必须**先做一趟全量布局拿 `laneCount`**，再按当前条数渲染并把 `laneCount` 传进去，否则追加后 `maxLane` 变大、已显示的行会整体右移（实测 `maxRows=5` 时 laneCount=2、全量=4）。改动行数相关逻辑后要复验「加载更多后首行宽度不变」。档位选择与加载更多都属**同页显示控制，不入 `navHistory`**。大列表性能瓶颈在 DOM 不在布局（3000 行布局仅 ~2ms、DOM 占 96%），故 `.gitgraph-row` 用 `content-visibility: auto` + `contain-intrinsic-size`（后者必给，否则滚动条乱跳）。
- 列表类「档位 + 加载更多」是共用模式，分支树与 Commit 页都按它实现：档位 `150/300/600/全部`（0=全部），每次追加 150；`state.<x>Limit` / `state.<x>Rows` 必须纳入 `snapshot`/`restore`；切换 ref / 视图要重置 `Rows` 回档位值。**搜索时一律不截断**（跨全量匹配并显示全部命中，隐藏档位与加载更多），否则用户要翻很多页才找得到。列表行都挂 `content-visibility: auto` + `contain-intrinsic-size`；不传 `contain-intrinsic-size` 会让滚动条乱跳。
- `data/*.js` 格式约定：`window.GH_DATA.<name>=<json>;`，JSON 中的 `</` 必须转义为 `<\/`，防止脚本注入。
- Token 只通过环境变量或 GUI 传入，**禁止写入导出包**；本机 GUI 缓存文件为 exe 同目录的 `gh_token_cache.json`，按**组合**保存（仓库 + 输出目录 + Token + 数量上限 + 选项），支持整体切换与旧版自动迁移。
- 组合（profile）读写约定：控件 ↔ 组合的字段搬运只允许写在 `_apply_profile` / `_capture_profile` 两处；新增可保存字段时须同时更新 `PROFILE_FIELDS`、`DEFAULT_PROFILE`（给默认值）与 `_migrate_v1` 的继承列表，否则旧缓存会被重置为默认值。Token 写控件统一走 `_set_token_field()`。
- 导出期间会锁定全部导出参数控件（见 `_set_profile_btns_state` 与 `self.param_widgets`）：因为工作线程结束时把**启动时的快照**写回磁盘，导出中被改动的控件会被静默吞掉。新增任何可编辑参数控件时，必须 `self.param_widgets.append(w)` 登记，否则用户能在导出中改它、改动却丢失。
- GUI 工作线程的异常处理必须**兜住 `BaseException`**（不能只 `except Exception`）：`export()` 参数校验失败时走 `sys.exit()`，抛的是 `SystemExit`（`BaseException` 子类），漏接会让 `__DONE__` / `__FAIL__` 都不入队 → 界面永久停在「导出中…」、全部控件禁用，只能重启。`__FAIL__` 与 `__DONE__` 两个分支必须恢复同一组控件（含「打开输出目录」）。
- 缓存写盘结果必须**如实反馈**：`save_cache()` 返回 bool，调用方（`upsert_profile` 等）据此抛错或提示，**禁止**在写盘失败时仍打印「已保存」——程序放在 Program Files / 只读 U 盘 / 磁盘满时用户会以为参数已持久化，重启后全部丢失。
- `_normalize_profile` 对**每个字段**都要类型防御（含 `name` 用 `str()` 兜底）：`load_cache` 的 `except` 会把整个缓存文件隔离成 `.bad`，一个脏字段就足以让用户看到「0 个组合」且不知原因。
- 「清除缓存」等提示语承诺清空的范围，实现必须一致（清完后调 `_apply_profile(dict(DEFAULT_PROFILE, name=""))` 让控件真正归位），否则界面留着旧值、用户点导出会拿残留参数跑一次。
- 增量导出是默认行为：Commit 按 sha 无限累积、PR/Issue 按 `updated_at` 判断是否复用详情、文件内容与图片按 blob sha 判断变化；改增量逻辑时不得静默丢弃历史缓存。**「失败 ≠ 空结果」是硬约束**：抓取失败或超出预算的条目**不得**写成确定的空结果（commit 不带 `files` 键；PR/Issue 不带 `files_fetched` / `comments_fetched` 标记），否则一次网络抖动或一次 `--skip-details` 会被永久固化成「已完成」，之后调大 `--max-commit-diffs` 也补不回来。新增可增量复用的字段时，必须同时提供「已确认抓到」的成功标记。
- 请求失败不得被当成空数据写出：`/branches` 等列表请求失败时**沿用上一轮快照**（分支是快照语义，清空会连带摧毁缓存），并在 `meta.file_stats.branches_stale` 标注本轮未刷新。
- 跨轮派生字段不得依赖缓存命中分支：`related_pr_nums` 这类「只由本轮已拿到的数据派生」的字段，必须在复用判断**之前**无条件计算，否则命中缓存时会缺失该字段、后续回填段把 `related_prs` 覆盖成空值，静默丢数据。改 Release 对比段时注意这条。
- `meta.image_map` 必须**跨轮合并**（新结果优先，并剔除目标文件已不存在的悬空项），不得整块覆盖：第二轮带 `--no-assets`、或本轮图片 URL 集合为空时，`assets/externals` 里已下载的图片仍要被查看器正确引用，否则正文图全部裂图。`--full` 全量导出不继承旧映射。
- 分支（branches）语义与其他数据相反：**分支列表是快照**，上游删掉的分支本包不再保留（只读镜像：GitHub 上删了就看不到了）；已抓到的 commit 节点仍在 `commits.js` 中累积。分支条目复用判据必须包含**主干 tip**（`compare.base_sha == main_tip`）：`ahead_by`/`behind_by` 是相对主干算的，主干前进后旧值就错了；旧缓存没有 `base_sha` 时视为不匹配、宁可重拉一次 compare。tip 已包含于主干历史的分支（纯落后）**不发 compare 请求**，`behind_by` 本地按祖先集合计算，历史不完整时标 `behind_approx` —— 该字段**前端必须消费**（显示「约 N」+「历史不完整，落后条数为近似下界」提示），否则用户会把下界当精确值（真实落后可能几千条）。
- `--max-branch-commits` 主动裁剪时**必须置 `truncated`**（判据：`total > len(keep)`），不能只反映「compare 单次返回上限」；否则用户看到被裁后的条数却没有任何「已截断」提示。前端应显示 `N / total`。`graph.truncated` 由 `cmp_block.truncated` 派生，会自动同步。
- 分支独有 commit 的逐条 diff 走 `--max-branch-diffs`（默认 30，0=不拉；`--skip-details` 强制为 0）：只为非 stub 节点拉取，按 `author_date` 新→旧排序以保证预算优先给最近提交；命中上一轮 `graph` 节点缓存的**不消耗预算**。预算耗尽后剩余节点只保留骨架（图上不断链），不报错。
- 查看器查提交一律走 `commitPool()` / `findCommitAnywhere()`（主干 → graph → PR 内 → Release compare 按 sha 去重合并），**禁止再写只查 `D.commits` 的查找**：分支独有提交与未合并 PR 的提交不在 `commits.js` 里，只查主干会让它们点开报「未找到」。Commit 页的 ref 切换（`commitRefs` / `commitsForRef`）从 `graph.refs` 取选项、沿 `parents` 回溯（需带环状保护），无 `graph.js` 的旧包要退回平铺列表。**「可点」判定也要走池**（Release 详情、分支详情、分支树行都用 `findCommitAnywhere`）；但 **stub 节点虽在池中也必须排除**（`!cell.stub`），否则点进去是空提交页。
- `commitPool()` 合并时**非 stub 必须优先于 stub**：stub 是「历史未导出」的占位（message/parents 皆空），若它先占住 sha，后面 release compare 里的真实记录会被 Map 去重丢掉 → 老 tag 的提交列表 0 条、详情页空标题。导出侧也要配合：`_add_stub` **不得覆盖已有真实记录的 sha**（release compare 的 commits 不进 `graph.nodes`，必须单独收集 `release_known` 再判断）。
- release compare 的 commit 条目**必须带 `parents`**（与分支 compare 同口径）：查看器 `reachableSet()` 沿 parents 回溯，缺该字段会让可达集合在此断链，版本对比的落后条数/分叉点失真、Commit 页切到该 tag 看不到更早历史。
- `asset_rel()` **禁止用 `lstrip("./")`**：它按字符集剥离会把 `.github/banner.png` 变成 `github/banner.png`，而查看器 `mdSrc` 兜底只去掉真正的 `./` 前缀，仍去找 `assets/.github/banner.png` → 内网裂图。只能剥真正的 `./` 前缀与开头 `/`。
- 分支树的 `overflow`（「可横向滚动」提示）按**整行泳道数** `laneCount` 判定，不能只看本行提交所在的 `lane`：某行活跃泳道可能比提交自身更靠右，只看 lane 会漏报。
- 预计算文件差异（`precomputedFiles`）比对 ref 时**必须同时比 type**（用 `refKey`，如 `branch:main` / `tag:main`）：仓库里可能同名分支与标签，只比 `name` 会把「默认分支→某分支」的差异当成「同名 tag→某分支」展示，起点对象不同、增删会误导。
- 版本对比页与分支树同样必须**接入全局搜索**（`state.search`）：搜索时跨全量匹配、不截断、隐藏档位与「加载更多」，与 Commit 页口径一致。
- 二进制扩展名见 `export.BINARY_EXT`；单文件正文默认上限 400KB；图片下载上限 2MB（树内图与 PR/Issue/Release 正文外链图共用）。正文外链图落在 `assets/externals/`，URL→本地路径映射写在 `meta.image_map`。调整这些限制时同步更新 README 与 ARCHITECTURE。
- 查看器 XSS 边界：所有从 GitHub 拉取的 HTML/文本在渲染前必须 `esc()` 或经受控的 `processRichHtml()`；禁止把未转义的 API 字段直接 `innerHTML`。**正文里的原始 HTML 一律先过 `sanitizeHtml()` 白名单**（`viewer/js/app.js` 顶部）：只保留排版类标签与安全属性，丢弃全部 `on*` 事件属性、`style` 属性，以及 `javascript:` / `vbscript:` / `data:` / `blob:` / `file:` 协议。新增可渲染标签或属性时必须同时加进 `SAFE_TAGS` / `SAFE_ATTRS` 白名单，**禁止**退回「黑名单只删 script/iframe」的写法。右键开图等 `window.open` 出口同样必须校验协议（只放行 http(s)/file）。
- 版本对比（`renderCompare`）用**可达集合差**（`reachableSet` 沿 `parents` 回溯 + `compareResult` 求集合差），**禁止退回按 `commits.js` 数组下标切片**——分支独有提交不在 `commits.js` 里，切片只能近似主干、对分支完全错误。文件差异**只显示导出时预计算好的**（`branches[].compare` / `releases[].compare`），且**方向相反时故意不展示**（增删对调会误导）；改这里时不要"顺手"把反向也算上。
- 修改或新增算法/数据结构前，必须先阅读 ARCHITECTURE.md，确认数据流和调用链。
