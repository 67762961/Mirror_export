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
- 增量导出是默认行为：Commit 按 sha 无限累积、PR/Issue 按 `updated_at` 判断是否复用详情、文件内容与图片按 blob sha 判断变化；改增量逻辑时不得静默丢弃历史缓存。
- 分支（branches）语义与其他数据相反：**分支列表是快照**，上游删掉的分支本包不再保留（只读镜像：GitHub 上删了就看不到了）；已抓到的 commit 节点仍在 `commits.js` 中累积。分支条目按 `sha` + `default_branch` 未变判断复用；tip 已包含于主干历史的分支（纯落后）**不发 compare 请求**，`behind_by` 本地按祖先集合计算，历史不完整时标 `behind_approx`。compare 的超限判断用 `total_commits > len(commits)`，不硬编码 API 上限。
- 分支独有 commit 的逐条 diff 走 `--max-branch-diffs`（默认 30，0=不拉；`--skip-details` 强制为 0）：只为非 stub 节点拉取，按 `author_date` 新→旧排序以保证预算优先给最近提交；命中上一轮 `graph` 节点缓存的**不消耗预算**。预算耗尽后剩余节点只保留骨架（图上不断链），不报错。
- 查看器查提交一律走 `commitPool()` / `findCommitAnywhere()`（主干 → graph → PR 内 → Release compare 按 sha 去重合并），**禁止再写只查 `D.commits` 的查找**：分支独有提交与未合并 PR 的提交不在 `commits.js` 里，只查主干会让它们点开报「未找到」。Commit 页的 ref 切换（`commitRefs` / `commitsForRef`）从 `graph.refs` 取选项、沿 `parents` 回溯（需带环状保护），无 `graph.js` 的旧包要退回平铺列表。
- 二进制扩展名见 `export.BINARY_EXT`；单文件正文默认上限 400KB；图片下载上限 2MB（树内图与 PR/Issue/Release 正文外链图共用）。正文外链图落在 `assets/externals/`，URL→本地路径映射写在 `meta.image_map`。调整这些限制时同步更新 README 与 ARCHITECTURE。
- 查看器 XSS 边界：所有从 GitHub 拉取的 HTML/文本在渲染前必须 `esc()` 或经受控的 `processRichHtml()`；禁止把未转义的 API 字段直接 `innerHTML`。
- 版本对比（`renderCompare`）用**可达集合差**（`reachableSet` 沿 `parents` 回溯 + `compareResult` 求集合差），**禁止退回按 `commits.js` 数组下标切片**——分支独有提交不在 `commits.js` 里，切片只能近似主干、对分支完全错误。文件差异**只显示导出时预计算好的**（`branches[].compare` / `releases[].compare`），且**方向相反时故意不展示**（增删对调会误导）；改这里时不要"顺手"把反向也算上。
- 修改或新增算法/数据结构前，必须先阅读 ARCHITECTURE.md，确认数据流和调用链。
