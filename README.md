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

1. 填 **GitHub Token**（可下拉选最近几条，或粘贴新 Token）
2. 填仓库 `owner/name`
3. 输出目录会**自动记住上次路径**
4. 默认勾选 **增量导出**：只拉有变化的 Commit / PR / Issue / README，其余复用上次缓存
5. 点 **开始导出**
6. 完成后点 **打开输出目录**，把整个文件夹拷进内网

配置缓存文件：exe 同目录的 `gh_token_cache.json`（Token、仓库、输出路径）。
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
```

CMD 用法：`set GITHUB_TOKEN=ghp_xxx` 再运行。

导出完成后会生成类似：

```
gh-mirror-67762961-Csv_reader-20260914/
  index.html
  css/app.css
  js/app.js
  data/*.js
  assets/            # README 树内图片
  assets/externals/  # PR/Issue/Release 正文外链图片
```

把**整个文件夹**拷到 U 盘。

## 内网查看

1. 拷贝文件夹到内网任意位置
2. 双击 `index.html`
3. 左侧切换：概览 / PR / Issue / Commit / Tag / 版本对比 / 文件浏览
4. 详情页顶部「← 返回」回到上一个浏览页（如 PR → Commit 后返回 PR）；无更早历史时回对应列表

> 数据以 `data/*.js` 加载，兼容 `file://` 协议，无需起本地服务器。

## 功能说明

概览页含 GitHub 风格贡献墙：按日汇总 Commit / PR / Issue / Tag / Release，颜色越深表示当日贡献越多；热力图默认横滑至最右侧。墙下方可按 1 天 / 7 天 / 30 天 / 90 天 / 365 天 / 从最初 / 自定义天数查看分类汇总（只影响汇总行，不缩放热力图）。

- **PR 列表**：筛选打开 / 已合并 / 已关闭，点进详情看描述、评论、文件 diff；正文 Markdown 图片若已导出则本地显示；详情内可点 commit / `#N` 等站内跳转，用「← 返回」回到上一浏览页
- **Issue 列表**：同样支持筛选与详情评论；评论中的图片同样走 `image_map` 本地化
- **Commit**：默认分支提交历史，含完整 message
- **Tag / Release**：标签与发行说明；body 中的图片同样会被下载
- **版本对比**：基于已导出 commit 的近似对比（选 base → head）
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

- Token 只在外网导出机环境变量中使用，**不会写入**导出包
- 导出包不含认证信息，可安全拷入内网
- 建议使用 fine-grained token 或 classic token，用完可撤销

## 定期更新建议

1. 外网执行 `export.py`（加 `--force`）
2. 把新输出目录拷入内网，替换旧目录
3. 旧目录可归档保留，便于对照历史快照
