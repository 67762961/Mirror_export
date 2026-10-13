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
- 进度由 `export.py` 顶部的 `STAGES` 阶段表驱动，**新增阶段必须在此登记**，否则该段进度会缺失；阶段内高频循环用 `make_throttle(1)` 按每 1% 节流上报，且**命中增量缓存的条目也要计入 done**（上报点放在 `continue` 之前），否则纯增量导出时进度条会长时间不动。机器行字段与跳过阶段的折算规则见 ARCHITECTURE.md「进度协议」。
- 查看器数据文件命名固定：`meta` / `prs` / `issues` / `commits` / `tags` / `releases` / `files` / `branches` / `graph`；新增数据集时须同步更新 `export.py` 的 `writes`、`manifest.json`、`viewer/index.html` 的 script 标签。
- `data/*.js` 格式约定：`window.GH_DATA.<name>=<json>;`，JSON 中的 `</` 必须转义为 `<\/`，防止脚本注入。
- Token 只通过环境变量或 GUI 传入，**禁止写入导出包**；本机 GUI 缓存文件为 exe 同目录的 `gh_token_cache.json`，按**组合**保存（仓库 + 输出目录 + Token + 数量上限 + 选项），支持整体切换与旧版自动迁移。
- 组合（profile）读写约定：控件 ↔ 组合的字段搬运只允许写在 `_apply_profile` / `_capture_profile` 两处；新增可保存字段时须同时更新 `PROFILE_FIELDS`、`DEFAULT_PROFILE`（给默认值）与 `_migrate_v1` 的继承列表，否则旧缓存会被重置为默认值。Token 写控件统一走 `_set_token_field()`。
- 导出期间会锁定全部导出参数控件（见 `_set_profile_btns_state` 与 `self.param_widgets`）：因为工作线程结束时把**启动时的快照**写回磁盘，导出中被改动的控件会被静默吞掉。新增任何可编辑参数控件时，必须 `self.param_widgets.append(w)` 登记，否则用户能在导出中改它、改动却丢失。
- 增量导出是默认行为：Commit 按 sha 无限累积、PR/Issue 按 `updated_at` 判断是否复用详情、文件内容与图片按 blob sha 判断变化；改增量逻辑时不得静默丢弃历史缓存。
- 分支（branches）语义与其他数据相反：**分支列表是快照**，上游删掉的分支本包不再保留（只读镜像：GitHub 上删了就看不到了）；已抓到的 commit 节点仍在 `commits.js` 中累积。分支条目按 `sha` + `default_branch` 未变判断复用；tip 已包含于主干历史的分支（纯落后）**不发 compare 请求**，`behind_by` 本地按祖先集合计算，历史不完整时标 `behind_approx`。compare 的超限判断用 `total_commits > len(commits)`，不硬编码 API 上限。
- 二进制扩展名见 `export.BINARY_EXT`；单文件正文默认上限 400KB；图片下载上限 2MB（树内图与 PR/Issue/Release 正文外链图共用）。正文外链图落在 `assets/externals/`，URL→本地路径映射写在 `meta.image_map`。调整这些限制时同步更新 README 与 ARCHITECTURE。
- 查看器 XSS 边界：所有从 GitHub 拉取的 HTML/文本在渲染前必须 `esc()` 或经受控的 `processRichHtml()`；禁止把未转义的 API 字段直接 `innerHTML`。
- 修改或新增算法/数据结构前，必须先阅读 ARCHITECTURE.md，确认数据流和调用链。
