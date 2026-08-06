# NC 处理报告查看器

当前发布版本：1.0.0。

`NCodeProcessReportViewer.exe` 是独立的 JSON 报告查看程序，用于查看 [NCodeProcess](https://github.com/acceleratorcxy/NCodeProcess) 生成的 `ncodeprocess-report-*.json` 处理报告；只读，不会修改 NC 程序或报告内容。

## 项目文档

- [需求文档](docs/NCodeProcessReportViewer-需求文档.md)（含实施状态总览）
- [用户手册](docs/NCodeProcessReportViewer-用户手册.md)（另有 Word/PDF 版）
- [发布说明](docs/NCodeProcessReportViewer-发布说明.md)
- [审查与待办](docs/NCodeProcessReportViewer-审查与待办.md)
- [程序理解与操作记录](docs/NCodeProcessReportViewer-程序理解与操作记录.md)
- [测试指南](docs/NCodeProcessReportViewer-测试指南.md)
- [发布安全说明](docs/NCodeProcessReportViewer-发布安全说明.md)

## 使用方法

1. 将 EXE 放到包含 `NCodeProcessData` 文件夹的处理目录后启动。
2. 程序会自动扫描当前目录和 `NCodeProcessData` 中的 `ncodeprocess-report-*.json`。
3. 也可以点击“打开报告文件”，查看其他位置的报告。

查看器同时兼容旧版 `NCPostProcessData` 目录和 `ncpostprocess-report-*.json` 文件；新版本默认使用 `NCodeProcessData` 与 `ncodeprocess-report-*.json`。

## 查看内容

- 处理成功、失败、跳过、移动、删除、警告和错误数量。
- 处理结果与校验问题柱状图。
- 每个程序的 F、S、X、Y、Z 出现次数、最小值、最大值及 G00 数量。
- 校验问题的文件、行号、级别、原始内容和处理建议。
- 文件修改说明及红绿色高亮差异。
- 本次处理的事件时间线（运行日志），可按事件类型筛选；日志完整内嵌报告，不再生成磁盘日志文件。
- 概览页的工具版本、报告来源、处理耗时、归档时间戳、用户确认项与扫描警告；文件明细表的目标路径与程序名来源标记。
- 完整原始 JSON 内容。

界面中的长表格和文本区域均支持横向、纵向滚动。
表格单元格内容被列宽截断时，鼠标悬停会自动弹出浮窗显示完整内容。

## 界面

默认窗口约 1500×800（最小 1250×680），按屏幕尺寸自适应（1366×768 目标屏为 1250×680）；左栏为报告列表与程序列表（程序 + 校验情况，含「全部程序」与「未配对文件」行，点击后右侧文件明细联动展示该程序相关文件），右栏为七个页签（概览与可视化 / 文件明细 / 参数统计 / 校验问题 / 修改与差异 / 运行日志 / 原始 JSON），默认展示概览与可视化。选中报告、程序或文件后立即刷新相关页面，不重复读取文件。运行日志页下方提供「选中事件详情」只读预览区，完整展示多行 traceback 与关键运行数据。

## 测试与打包

```powershell
# 测试（在 Python 3.8 环境中运行）
python -m unittest discover -s tests -v

# 打包（默认使用脚本配置的 Python 3.8 环境）
.\build_portable.ps1
```

发布文件位于 `dist`，包括单文件 EXE、便携目录和 ZIP 压缩包。

打包脚本会排除查看器不使用的网络、SSL、通用压缩、多进程和 XML 模块，以减少单文件 EXE 与便携 ZIP 的体积。

发布构建已启用随机密钥字节码加密、`-OO` 优化、运行时反调试、调试模块裁剪、临时构建痕迹清理和 SHA-256 完整性记录，并支持 Authenticode 数字签名。具体说明见 [SECURITY.md](SECURITY.md)。
