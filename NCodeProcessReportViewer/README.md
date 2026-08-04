# NC 处理报告查看器

`NCodeProcessReportViewer.exe` 是独立的 JSON 报告查看程序，不会修改 NC 程序或报告内容。

## 项目文档

- [需求文档](docs/NCodeProcessReportViewer-需求文档.md)（含实施状态总览）
- [用户手册](docs/NCodeProcessReportViewer-用户手册.md)（另有 Word/PDF 版）
- [发布说明](docs/NCodeProcessReportViewer-发布说明.md)
- [审查与待办](docs/NCodeProcessReportViewer-审查与待办.md)
- [程序理解与操作记录](docs/NCodeProcessReportViewer-程序理解与操作记录.md)
- [测试指南](docs/NCodeProcessReportViewer-测试指南.md)

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
- 完整原始 JSON 内容。

界面中的长表格和文本区域均支持横向、纵向滚动。

## 测试与打包

```powershell
conda run -n python38 python -m unittest discover -s tests -v
.\build_portable.ps1 -CondaEnvironment python38
```

发布文件位于 `dist`，包括单文件 EXE、便携目录和 ZIP 压缩包。

打包脚本会排除查看器不使用的网络、SSL、通用压缩、多进程和 XML 模块，以减少单文件 EXE 与便携 ZIP 的体积。

发布构建已启用随机密钥字节码加密、`-OO` 优化、运行时反调试、调试模块裁剪、临时构建痕迹清理和 SHA-256 完整性记录，并支持 Authenticode 数字签名。具体说明见 [SECURITY.md](SECURITY.md)。
