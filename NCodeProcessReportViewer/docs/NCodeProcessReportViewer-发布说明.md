# NCodeProcessReportViewer 发布说明

> 本文件记录各版本发布内容；新版本在此追加，旧版本保留备查。

## 版本索引

| 版本 | 主题 | 日期 |
|---|---|---|
| 1.0.0 | NC 处理报告可视化查看器首版 | 2026-08-05 |

---

# 1.0.0 —— NC 处理报告可视化查看器首版

> 日期：2026-08-05（开发跨 2026-08-03 至 2026-08-05）
> 范围：独立只读查看器，解析 NCodeProcess 生成的 `ncodeprocess-report-*.json`

## 一、发布概述

`NCodeProcessReportViewer` 是独立的 NC 后处理报告查看工具：只读解析 NCodeProcess 生成的 JSON 报告，将处理结果、参数统计、校验问题、修改差异以可视化方式呈现，不修改任何 NC 程序、报告或配置。面向车间/工艺人员快速核对批量处理结果，无需打开原始 JSON。

## 二、功能清单

| # | 功能 | 说明 |
|---|---|---|
| 1 | 自动文件发现 | 扫描 EXE 目录 + `NCodeProcessData`，兼容旧版 `NCPostProcessData` 与 `ncpostprocess-report-*.json`；按修改时间倒序 |
| 2 | 打开外部报告 | 任意位置 JSON 报告；JSON 错误/根节点非对象时明确报错 |
| 3 | 概览与可视化 | 7 项汇总（成功/失败/跳过/移动/删除/警告/错误）+ 输入输出目录、时间元信息 + 处理结果/校验问题双柱状图（Tk Canvas 自绘） |
| 4 | 参数统计 | 全部文件或选中文件的 F/S/X/Y/Z 次数、最小值、最大值及 G00 数量 |
| 5 | 校验问题 | 文件/行号/级别/类型/原始文本/建议；error 红色、warning 橙色加粗 |
| 6 | 修改与差异 | 修改说明 + unified diff；新增绿、删除红、`@@`/头部蓝 |
| 7 | 原始 JSON | 只读完整数据核对（缩进、禁用编辑） |
| 8 | 界面 | 1290×720 默认 / 1160×640 最小；左栏报告+文件明细、右栏 Notebook 五页；全部表格/文本区纵横滚动 |

## 三、测试与验证

- 测试基线：**6 项全部通过**（conda python38 / Python 3.8.19，约 0.4 秒），覆盖文件发现、报告加载校验、汇总/问题/统计行生成、窗口几何、表格无横向溢出。
- 详见 `NCodeProcessReportViewer-测试指南.md`。

## 四、发布形态

- `build_portable.ps1`（`-CondaEnvironment python38`）产出单文件 `NCodeProcessReportViewer.exe`、便携目录与 ZIP。
- 发布构建：随机密钥字节码加密、`-OO` 优化、运行时反调试、调试模块裁剪、临时构建痕迹清理、SHA-256 完整性记录（详见 `SECURITY.md`）。

## 五、已知限制与后续建议

1. **大报告性能**：报告 JSON 全量加载并同步解析（无后台线程/分页），超大报告可能在主线程阻塞——可后续增加后台加载与进度反馈。
2. **数据兼容**：报告字段缺失时汇总显示 0，统计/差异页跳过缺失数据；如需严格校验字段完整性可后续加强。
3. **图表交互**：柱状图无悬停数值提示/导出，如需可后续扩展。

## 六、相关文档

- 需求文档：`docs/NCodeProcessReportViewer-需求文档.md`
- 用户手册：`docs/NCodeProcessReportViewer-用户手册.md`
- 程序理解与操作记录：`docs/NCodeProcessReportViewer-程序理解与操作记录.md`
- 审查与待办：`docs/NCodeProcessReportViewer-审查与待办.md`
- 测试指南：`docs/NCodeProcessReportViewer-测试指南.md`
