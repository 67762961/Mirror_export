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
- 查看器数据文件命名固定：`meta` / `prs` / `issues` / `commits` / `tags` / `releases` / `files` / `branches`；新增数据集时须同步更新 `export.py` 的 `writes`、`manifest.json`、`viewer/index.html` 的 script 标签。
- `data/*.js` 格式约定：`window.GH_DATA.<name>=<json>;`，JSON 中的 `</` 必须转义为 `<\/`，防止脚本注入。
- Token 只通过环境变量或 GUI 传入，**禁止写入导出包**；本机 GUI 缓存文件为 exe 同目录的 `gh_token_cache.json`。
- 增量导出是默认行为：Commit 按 sha 无限累积、PR/Issue 按 `updated_at` 判断是否复用详情、文件内容与图片按 blob sha 判断变化；改增量逻辑时不得静默丢弃历史缓存。
- 二进制扩展名见 `export.BINARY_EXT`；单文件正文默认上限 400KB；图片下载上限 2MB。调整这些限制时同步更新 README 与 ARCHITECTURE。
- 查看器 XSS 边界：所有从 GitHub 拉取的 HTML/文本在渲染前必须 `esc()` 或经受控的 `processRichHtml()`；禁止把未转义的 API 字段直接 `innerHTML`。
- 修改或新增算法/数据结构前，必须先阅读 ARCHITECTURE.md，确认数据流和调用链。
