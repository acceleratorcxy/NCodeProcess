# NCodeProcessReportViewer 测试指南

> 用途：说明查看器当前测试情况（架构、基线、覆盖、已知噪音）与新增/维护测试的做法。
> 适用范围：本机 Windows + conda `Python 3.8 环境` 环境；目标平台 Windows 7，**必须使用 Python 3.8 跑测试**。

> **维护说明**：本文件纳入版本管理，随测试变化持续维护；测试数量、结构、运行命令变化后同步更新本文件，并同步 `NCodeProcessReportViewer-程序理解与操作记录.md`、`NCodeProcessReportViewer-发布说明.md`、`NCodeProcessReportViewer-需求文档.md`。

---

## 一、测试架构总览

- **框架**：Python 标准库 `unittest`（无第三方依赖），Python 3.8（conda `Python 3.8 环境`）。
- **目录**：`tests/`，模块 `tests/test_report_viewer.py`。
- **当前基线**：**22 项全部通过**（2026-08-06 F 离群检测明细展示后为 22 项；以 `python -m unittest discover -s tests -v` 输出为准）。
- **运行命令**（cwd 为 `NCodeProcessReportViewer` 目录）：

| 场景 | 命令 |
|---|---|
| 全量测试 | `conda run -n <Python 3.8 环境名> python -m unittest discover -s tests -v` |
| 单用例 | `conda run -n <Python 3.8 环境名> python -m unittest tests.test_report_viewer.ReportViewerTests -v` |

## 二、测试类与覆盖范围

| 测试类 | 覆盖内容 |
|---|---|
| `ReportViewerTests` | 纯逻辑：`discover_reports`（当前目录 + NCodeProcessData + 旧版 NCPostProcessData、忽略无关文件）、`load_report`（加载校验 + 根节点非对象报错）、`report_summary`/`file_issue_counts`/`iter_stats_rows`（汇总、问题计数、参数与 G00 行）、`runtime_log_events`（运行日志条目容错与事件筛选，WP-C6） |
| `LayoutMetricTests` | `window_geometry_for_screen`：1366×768 / 1920×1080 精确值、小屏不超屏幕 |
| `ReportViewerLayoutTests` | Tk 交互：默认窗口（1290×720）报告列表、文件明细、参数统计、校验问题表头完整可见、无横向溢出；运行日志页签渲染与 log_path 提示（WP-C6）；概览元信息与文件明细「目标」列/来源标记渲染（报告第 12 节）；悬停浮窗截断判定（超长单元格提示、短内容不提示） |

## 三、测试隔离约定

1. **临时目录**：文件系统测试用 `make_dir()`（`tempfile.mkdtemp`），不污染真实目录。
2. **样本报告**：`sample_report()` 生成固定结构 JSON，覆盖汇总/问题/统计/差异字段。
3. **GUI 隔离**：`_build_viewer` 创建独立 Tk 根窗口并 `withdraw`/`deiconify` 控制显示，用例 `finally: root.destroy()`。
4. **无注册表/文件写入**：查看器只读，测试不涉及真实用户数据。

## 四、已知无害噪音

- 全量测试中偶见 Tk teardown 的 `can't invoke "event" command: application has been destroyed` 提示，对应用例仍判 `ok`，非失败。

## 五、添加测试的做法（TDD 约定）

1. **分层对应**：改动 `viewer.py` 纯逻辑函数 → 补 `ReportViewerTests`/`LayoutMetricTests`；改动界面 → 补 `ReportViewerLayoutTests`。
2. **RED → GREEN**：先写失败测试确认功能缺失，再最小实现，最后全量回归（保持 6 项全绿）。
3. **布局断言**：表头可见性用 `xview()[1] >= 0.999` 锁定；窗口几何用精确值断言。
4. **Python 3.8 兼容**：测试代码避免 3.9+ 语法。

## 六、与其他文档的关系

- 需求文档（第 7 节实施状态总览）、发布说明（测试基线）、操作记录、审查与待办：见 `docs/` 同名文档。
- 打包/提交流程：与 NCodeProcess 共用本地流程文档 `NCodeProcess-更改测试打包提交流程.md`（含 NCodeProcessReportViewer 时流程相同）。
