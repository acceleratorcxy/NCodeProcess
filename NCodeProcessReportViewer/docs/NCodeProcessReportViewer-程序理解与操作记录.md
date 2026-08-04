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

---

## 三、后续建议（可选）

1. **大报告性能**：后台线程加载 + 进度提示（当前主线程同步解析）。
2. **字段完整性校验**：汇总缺失值当前显示 0，可加强为显式「无数据」。
3. **图表增强**：柱状图悬停数值、导出图片。
4. **报告聚合/对比**：多报告汇总对比（需求 8 节待确认项）。
