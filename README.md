# Mirror_export

在外网把 GitHub 仓库导出成**自包含静态站**，用 U 盘拷进内网，双击 `index.html` 即可浏览 PR / Issue / Commit / Tag / 源码，无需联网、无需写权限。

## 你需要什么

| 环境 | 要求 |
|------|------|
| 外网 | `dist/Mirror_export.exe`（图形界面，免装 Python），或 Python 3.8+ 跑 `export.py` |
| Token | GitHub Personal Access Token，权限勾选 `repo`（私有库） |
| 内网 | 任意现代浏览器（Chrome / Edge） |
| 传输 | U 盘 / 文件拷贝 |

## 外网导出（一次性）

### 方式 A：图形界面 exe（推荐）

把 `dist/Mirror_export.exe` 拷到外网机器，双击打开：

1. **选「组合」**：一个组合 = 仓库 + 输出目录 + Token + 数量上限 + 各选项，选一下就全部带出来
2. 新建项目：填好各字段后点 **另存为…**，起个名（如 `A项目-产品库`），之后下拉里直接切
3. 改完字段点 **保存** 写回当前组合；也可 **重命名** / **删除**
4. Token 直接填在输入框（切组合时自动带出）；勾 **显示明文** 可查看完整 Token，默认掩码显示
5. 默认勾选 **增量导出**：只拉有变化的 Commit / PR / Issue / README，其余复用上次缓存
6. 点 **开始导出**
7. 完成后点 **打开输出目录**，把整个文件夹拷进内网

配置缓存文件：exe 同目录的 `gh_token_cache.json`（保存全部组合：仓库、输出目录、Token、选项）。
旧版缓存（按 Token 平铺的格式）首次启动会**自动迁移**成组合，无需手动处理。

#### 关于「组合」

一个组合 = 一套完整的导出参数，打包保存、整体切换，多个项目互不干扰：

| 字段 | 说明 |
|------|------|
| 名称 | 组合的显示名，出现在下拉里（如 `A项目-产品库`） |
| 仓库 | `owner/name` |
| 输出目录 | 该项目的导出目标目录 |
| Token | 该组合专属 Token（可与其他组合不同） |
| 数量上限 | PR / Issue / 每轮 Commit / Tag |
| 选项 | 跳过 diff、缓存源码、覆盖输出目录、增量导出 |

按钮含义：

- **保存**：把当前各字段写回下拉里选中的那个组合
- **另存为…**：用当前字段**新建**一个组合（复制一套改仓库，是最快的多项目做法）
- **重命名 / 删除**：管理组合
- **清除缓存**：删掉本机全部组合（含 Token），慎用

典型用法：第一次填好 A 项目的所有字段 → 点【另存为…】起名 `A项目` → 再改仓库/输出目录/Token（若不同）→ 再点【另存为…】起名 `B项目`。之后在下拉里切换即可，仓库、路径、Token、上限、选项会**一起**换过来。

三个细节：

- **导出期间**整个参数区会锁住（仓库 / 输出目录 / Token / 数量上限 / 各选项 / 组合下拉与按钮 / 清除缓存），文字与方块变灰、无法编辑，导出结束自动恢复。
  这是为了不让中途的修改被导出任务结尾的写回覆盖掉（任务结束时会把**开始时**的参数存进组合，若期间改过就会出现「界面是新值、缓存是旧值」）。
- **删除当前组合后**，界面会自动切到最近用过的那个组合，和下次启动时加载的组合保持一致。
- 缓存文件若损坏，会被改名成 `gh_token_cache.json.bad` 留档，程序从空配置重新开始，不会直接把原内容写没。

缓存文件是纯文本 JSON，形如：

```json
{
  "version": 2,
  "profiles": [
    {"name": "A项目-产品库", "repo": "ownerA/repoA", "out": "D:\\mirror\\repoA",
     "token": "ghp_xxx", "max_prs": 80, "max_issues": 80, "max_commits": 100, "max_tags": 50,
     "skip_details": false, "include_contents": true, "force": true, "incremental": true,
     "last_used": "2026-10-12T07:33:18Z"}
  ],
  "active": "A项目-产品库"
}
```

> 注意：Token 在本文件中是**明文**，且每个组合各存一份，请勿把该文件拷给别人。

导出包内：`data/*.js` + `assets/.index.json`（文件/图片 blob sha、正文外链图 `ext:<url>`，用于判断变化）。
PR / Issue / Release 正文里的截图（如 `user-attachments`）会下载到 `assets/externals/`，映射写在 `meta.image_map`，内网可离线显示。

### 方式 B：源码（需要 Python 3.8+）

```powershell
# 1. 进入本目录
cd Mirror_export

# 2. 设置 Token（PowerShell）
$env:GITHUB_TOKEN = "ghp_你的token"

# 3. 导出
python export.py --repo 67762961/Csv_reader

# 可选参数
python export.py --repo 67762961/Csv_reader --out gh-mirror --force
python export.py --repo 67762961/Csv_reader --max-prs 100 --max-commits 300
python export.py --repo 67762961/Csv_reader --skip-details   # 只要列表，不要 diff，更快
python export.py --repo 67762961/Csv_reader --max-branches 50   # 限制分支数（默认 100）
python export.py --repo 67762961/Csv_reader --max-branch-diffs 0  # 不拉分支提交的逐条 diff
python export.py --repo 67762961/Csv_reader --no-branches      # 完全不要分支数据
```

CMD 用法：`set GITHUB_TOKEN=ghp_xxx` 再运行。

导出完成后会生成类似：

```
gh-mirror-67762961-Csv_reader-20260914/
  index.html
  css/app.css
  js/app.js
  js/gitgraph.js     # 分支树泳道布局
  data/*.js
  assets/            # README 树内图片
  assets/externals/  # PR/Issue/Release 正文外链图片
```

把**整个文件夹**拷到 U 盘。

## 内网查看

1. 拷贝文件夹到内网任意位置
2. 双击 `index.html`
3. 左侧切换：概览 / PR / Issue / Commit / Tag / Branch / 版本对比 / 文件浏览
4. 详情页顶部「← 返回」回到上一个浏览页（如 PR → Commit 后返回 PR）；无更早历史时回对应列表

> 数据以 `data/*.js` 加载，兼容 `file://` 协议，无需起本地服务器。

## 功能说明

概览页含 GitHub 风格贡献墙：按日汇总 Commit / PR / Issue / Tag / Release，颜色越深表示当日贡献越多；热力图默认横滑至最右侧。墙下方可按 1 天 / 7 天 / 30 天 / 90 天 / 365 天 / 从最初 / 自定义天数查看分类汇总（只影响汇总行，不缩放热力图）。

- **PR 列表**：筛选打开 / 已合并 / 已关闭，点进详情看描述、评论、文件 diff；正文 Markdown 图片若已导出则本地显示；详情内可点 commit / `#N` 等站内跳转，用「← 返回」回到上一浏览页
- **Issue 列表**：同样支持筛选与详情评论；评论中的图片同样走 `image_map` 本地化
- **Commit**：默认显示默认分支的提交历史，含完整 message；顶部下拉可**切换分支 / 标签**，查看该 ref 可达的提交（像 GitHub 一样按分支看历史）。列表默认显示最近 150 条，可按 `150 / 300 / 600 / 全部` 切换档位，或点「加载更多」逐批追加；搜索时跨全部提交匹配、不受档位限制
- **Tag / Release**：标签与发行说明；body 中的图片同样会被下载
- **Branch**：顶部是 **分支树（VSCode Git Graph 风格泳道图）**——彩色泳道、平滑曲线、合并提交用空心圆标出，分支与标签贴在对应提交上（虚线空心点表示「该提交不在镜像里」）；分支树默认显示最近 150 条，同样支持档位切换与「加载更多」，且**加载后左侧泳道宽度保持不变**；下方是分支清单（默认分支标记、protected、短 sha、头部提交摘要、与默认分支的领先/落后条数）；点进详情看该分支独有的提交与文件差异——每个独有提交可展开查看自己的 diff。**分支列表是快照**：上游删掉的分支，镜像里也不再保留（Github 上删了就看不到了，本项目是只读镜像，就该如此）；与 commit 的「无限累积」语义相反
- **版本对比**：base / head 可选**分支或标签**，像 GitHub 一样看「head 相对 base 领先/落后多少、分叉点在哪、head 独有哪些提交」。默认是「默认分支 → 最新标签」。文件差异只在导出时预计算过的情况下显示（内网不能现算任意两点的 diff）：默认分支 ↔ 某分支用分支详情页缓存的那份，标签 ↔ 上一标签用 Release 缓存的那份；方向相反时不做展示（增删会对调）
- **文件浏览**：默认分支文件树 + 已缓存的文本源码

## 限制（设计如此）

| 限制 | 原因 / 调节 |
|------|-------------|
| 只读 | 不写回 GitHub，内网本来就无外网 |
| 默认最多 80 PR / 80 Issue / 200 Commit | 控制包体积，可用 `--max-*` 调大 |
| 单文件 >400KB 不缓存正文 | 只保留文件树条目 |
| 二进制不缓存 | `.mat` / `.png` / `.xlsx` 等仅列路径 |
| 单张图片 >2MB 不下载 | 树内图与 PR/Issue 正文外链图共用上限；`--no-assets` 则全部跳过 |
| 版本对比是近似 | 完整 compare API 可再扩展 |
| 速率限制 | Token 配额约 5000 次/小时，脚本会自动等待 |

## 精简包（只要代码不看 PR）

```powershell
python export.py --skip-details --no-contents --max-prs 20 --max-commits 50
```

## 本地预览（无 GitHub 时）

目录内 `preview/` 已是完整演示包（白色主题），双击即可：

```powershell
Start-Process C:\_TOOLS\Mirror_export\preview\index.html
```

## Token 安全

- Token 只在外网导出机环境变量或 GUI 组合中使用，**不会写入**导出包
- 导出包不含认证信息，可安全拷入内网
- 建议使用 fine-grained token 或 classic token，用完可撤销
- GUI 里 Token 默认**掩码显示**，勾【显示明文】才看得到完整值
- 本机 `gh_token_cache.json` 内的 Token 为明文保存（每个组合各一份），请勿把该文件拷给别人
- 需要清掉本机保存的 Token：点【清除缓存】（会连同所有组合一起删除），或直接删掉该 json 文件

## 定期更新建议

1. 外网执行 `export.py`（加 `--force`）
2. 把新输出目录拷入内网，替换旧目录
3. 旧目录可归档保留，便于对照历史快照
