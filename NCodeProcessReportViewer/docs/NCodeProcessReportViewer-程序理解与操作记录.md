# NCodeProcessReportViewer 程序理解与操作记录

> 本文件记录查看器的架构理解、关键机制与操作记录，后续开发在此持续追加。
> 需求基线见 `docs/NCodeProcessReportViewer-需求文档.md`；测试说明见 `docs/NCodeProcessReportViewer-测试指南.md`。

---

## 一、关于这个程序的详细理解

### 1. 程序定位

**NCodeProcessReportViewer** 是独立的 NC 后处理报告只读查看器，目标平台 Windows 7（必须兼容），交付形态为免安装便携式 EXE（PyInstaller 单文件、AES 加密 PYZ、Python 3.8 / Tkinter 纯标准库）。它解析 NCodeProcess 生成的 `ncodeprocess-report-*.json`，将处理结果、参数统计、校验问题、修改差异可视化呈现；**只读**，不修改 NC 程序、报告或用户配置。

### 2. 架构分层

```
NCodeProcessReportViewer/
├─ ncodeprocessreportviewer/
│  ├─ __init__.py      # 入口（main）
│  └─ viewer.py        # 纯逻辑函数 + ReportViewer(Tkinter) 界面类
├─ tests/              # test_report_viewer.py（6 项）
├─ build_portable.ps1  # 打包脚本（conda Python 3.8 环境）
└─ docs/               # 需求文档、发布说明、审查与待办、用户手册、操作记录、测试指南
```

`viewer.py` 设计上把纯逻辑（发现/加载/汇总/统计/几何）与 Tkinter 界面（`ReportViewer` 类）分层：纯函数便于单元测试，GUI 类只负责呈现与交互。

### 3. 核心函数（viewer.py 纯逻辑层）

| 函数 | 作用 |
|---|---|
| `discover_reports(directory)` | 扫描目录 + `NCodeProcessData`/`NCPostProcessData` 子目录，匹配两种报告模式，按修改时间倒序 |
| `load_report(path)` | 读取并校验 JSON（`utf-8-sig`，兼容 BOM）；根节点非对象或 `files` 非数组时报错；过滤非 dict 的 files 项 |
| `report_summary(data)` | 生成 7 项汇总（成功/失败/跳过/移动/删除/警告/错误），缺失值显示 0 |
| `file_issue_counts(item)` | 统计单文件 error/warning/总问题数 |
| `iter_stats_rows(data, selected)` | 生成 F/S/X/Y/Z 次数、最小、最大值及 G00 行（支持全部/选中文件） |
| `format_number(value)` | 数值保留三位小数并去尾零；非数值原样返回 |
| `window_geometry_for_screen(w, h)` | 按屏幕收缩默认/最小窗口（1366×768 → 1206×640，最小 1160×640） |

### 4. GUI 结构与交互（ReportViewer 类）

- **工具栏**：「打开报告文件」「重新扫描报告」+ 当前报告标签。
- **左栏**：报告列表（时间/文件）+ 报告文件明细（程序/文件、动作、校验列，error/warning 着色）。
- **右栏 Notebook 五页**：概览与可视化（7 项汇总 + 元信息 + 双柱状图）/ 参数统计 / 校验问题 / 修改与差异 / 原始 JSON。
- **选中即刷新**：`_on_report_selected`/`_on_file_selected` 触发 `_update_views`，不重复读文件。
- **图表**：Tk Canvas 自绘柱状图，`<Configure>` 事件自适应重绘。

### 5. 关键机制

- **只读保证**：查看器不写任何文件；原始 JSON 页 Text 设为 disabled。
- **兼容旧版**：`REPORT_PATTERNS` 含 `ncpostprocess-report-*.json`，`DATA_DIR_NAMES` 含 `NCPostProcessData`。
- **报告校验**：根节点必须为对象、`files` 必须为数组；读取失败（OSError/ValueError/JSONDecodeError）弹窗提示。
- **打包**：`build_portable.ps1` 每次生成随机 AES 键注入加密 PYZ，产出单文件 EXE/便携目录/ZIP 及 SHA256SUMS.txt；排除查看器不用的网络/SSL/压缩/多进程/XML 模块。

---

## 二、操作记录

### 2.1 开发与发布（2026-08-03 → 2026-08-05）

| # | 事项 | 说明 |
|---|---|---|
| 1 | 需求文档初稿 | 5 节（定位/发现/可视化/界面/发布），2026-08-03 |
| 2 | 查看器首版实现 | `viewer.py` 纯逻辑 + ReportViewer 界面；测试 6 项 |
| 3 | 打包与发布 | `build_portable.ps1` 产出 EXE/便携 ZIP，同步 `Publish\`（exe / 便携 zip / 无md发布 zip / 最终发布 zip） |
| 4 | 文档体系完善 | 2026-08-05 参照 NCodeProcess 建立完整文档：需求 V1.1（移入 docs/）、发布说明、审查与待办、操作记录、测试指南 |

### 2.2 提交记录

```
（查看器相关提交见仓库 `git log`——NCodeProcessReportViewer 与 NCodeProcess 同仓库，按提交信息区分）
```

### 2.3 文档同步强制约定

> 按项目全局约定（见 NCodeProcess 操作记录 2.8）：所有更改必须完善可能受影响的文档；本查看器改动时同步更新本文件与发布说明、需求文档、审查与待办、测试指南。

### 2.4 发布安全说明整理（2026-08-05）

- **处理**：依据构建代码（`build_portable.ps1`、`NCodeProcessReportViewer.spec`、`security_runtime_hook.py`）核对后，将根目录 `SECURITY.md` 整理为统一规范结构（六章：加固目标 / 已启用加固措施 13 项表 / 构建与签名 / 完整性验证 / 安全边界 / 维护约定），并新增 docs 文档 `docs/NCodeProcessReportViewer-发布安全说明.md`（文档信息表 + 六章），两者内容同步维护。
- **同步更新**：README「项目文档」列表加入「发布安全说明」。
- **约定**：纳入文档同步强制约定；构建脚本/spec/运行时钩子变化时，`SECURITY.md` 与 docs 版一并核对更新。

### 2.5 WP-C6 运行日志页签（2026-08-05）

| # | 改动 | 说明 |
|---|---|---|
| 1 | 纯逻辑 helper | 新增 `runtime_log_events(data, event_filter)`：缺字段回退空串、非对象条目过滤、按事件类型筛选，供页签渲染与测试复用 |
| 2 | 「运行日志」页签 | Notebook 增加第 6 页（概览/统计/问题/差异/运行日志/原始 JSON）：时间/级别/事件/消息四列表格，error 红、warning 橙加粗；顶部事件类型下拉筛选（含「全部」）；底部 `log_path` 提示（不包含运行日志 / 未记录磁盘路径 / 文件已不存在 / 完整日志路径） |
| 3 | 容错 | `runtime_log` 缺失时页签显示空并提示；非 dict 条目过滤不崩溃；新报告加载后筛选下拉自动重置 |

**测试基线**：6 → **8 项**（`runtime_log_events` 过滤与容错、运行日志页签渲染与 log_path 提示），全量通过。

### 2.6 报告第 12 节字段消费（2026-08-05）

| # | 改动 | 说明 |
|---|---|---|
| 1 | 概览元信息扩展 | 概览页新增工具版本（`app_version`）、报告来源（`generator`）、报告结构版本（`report_schema_version`）、处理耗时（`elapsed_seconds`）、APTSOURCE 归档时间戳（`archive_stamp`）、用户确认项（`user_confirmations`）、扫描警告（`scan_warnings`）展示；仅在字段存在时显示，避免挤占图表空间 |
| 2 | 文件明细表 | 新增「目标」列（`files[].target`）；程序/文件单元格在程序名旁追加来源标记（`program_name_source`：MSG/PPRINT/文件名/手动确认）；列宽压缩为 115/62/70/105，1290×720 下仍无横向溢出 |
| 3 | 容错 | 新字段缺失时按安全默认值展示，不影响旧报告加载 |

**测试基线**：8 → **9 项**（概览元信息与目标列/来源标记渲染），全量通过。

### 2.7 悬停浮窗显示完整单元格内容（2026-08-06）

| # | 改动 | 说明 |
|---|---|---|
| 1 | `CellTooltip` 移植 | 从主工具复制 `CellTooltip`（置顶小窗）+ `CELL_TOOLTIP_DELAY_MS=1500` |
| 2 | 绑定 5 张表格 | 报告列表/文件明细/参数统计/校验问题/运行日志均绑定 `<Motion>`/`<Leave>`/点击/滚轮隐藏；`_cell_truncated` 按字体实测宽度与列宽比较，仅截断内容弹出提示 |

**测试基线**：9 → **10 项**（截断判定：超长提示、短内容不提示），全量通过。

---

## 三、后续建议（可选）

1. **大报告性能**：后台线程加载 + 进度提示（当前主线程同步解析）。
2. **字段完整性校验**：汇总缺失值当前显示 0，可加强为显式「无数据」。
3. **图表增强**：柱状图悬停数值、导出图片。
4. **报告聚合/对比**：多报告汇总对比（需求 8 节待确认项）。
