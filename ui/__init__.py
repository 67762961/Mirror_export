"""Mirror_export · Fluent 界面层（PyQt6 + PyQt6-Fluent-Widgets）。

分层约定：
- `ui.theme` / `ui.theme_manager` / `ui.icons`：设计令牌、主题与图标；
- `ui.widgets`：可复用的小组件（表单行、进度四段、日志区、输入对话框）；
- `ui.panels`：导出页（只负责布局，不写业务逻辑）；
- `ui.window`：主窗口与控制器（组合读写、Token 掩码、导出线程、进度与锁定）。

与导出核心的边界保持不变：界面只通过 `export.export(args)` 调用，进度只认
stdout 里的 `__PROGRESS__{json}` 机器行；纯逻辑（缓存 / 进度解析）在 `gui_core`。
"""
