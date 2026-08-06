# NCodeProcess 项目集合

本仓库包含两个相互独立的 Windows 免安装项目（目标平台 Windows 7 及后续版本，Python 3.8 / Tkinter 纯标准库实现）：

- [NCodeProcess](NCodeProcess/README.md)：CATIA 后处理 NC 程序批量整理工具——批量扫描、规范化命名、补全 MSG 头部、刀具信息、M03 补写、NC 校验、参数统计与报告导出。
- [NCodeProcessReportViewer](NCodeProcessReportViewer/README.md)：NC 处理报告只读查看器——解析 `ncodeprocess-report-*.json`，以概览、文件明细、统计、问题、差异、运行日志、原始 JSON 页签可视化展示，左侧程序列表联动过滤文件明细。

## 仓库结构

```
NCodeProcess/                     ← 仓库根
├─ NCodeProcess/                  ← 主程序项目
│  ├─ ncodeprocess/               # 源码
│  ├─ tests/                      # 测试
│  ├─ docs/                       # 文档
│  ├─ build_portable.ps1          # 打包脚本
│  └─ README.md
└─ NCodeProcessReportViewer/      ← 报告查看器项目
   ├─ ncodeprocessreportviewer/   # 源码
   ├─ tests/                      # 测试
   ├─ docs/                       # 文档
   ├─ build_portable.ps1
   └─ README.md
```

## 环境与工具链

- 测试/打包使用 Python 3.8（目标 Windows 7 兼容性要求）。
- 打包工具：PyInstaller，通过各项目 `build_portable.ps1` 脚本执行。
- 发布形态：单文件 EXE + 便携目录 + ZIP，免安装、开箱即用。

## 构建与测试

在对应项目目录执行：

```powershell
# 测试（在 Python 3.8 环境中运行）
python -m unittest discover -s tests -v

# 打包
powershell -ExecutionPolicy Bypass -File .\build_portable.ps1
```

> 构建说明：发布构建使用 `-OO` 优化、随机 PYZ 加密密钥与随机 hash seed，因此同一源码每次打包的产物**不可逐字节复现**（属防破解/防提取的有意设计）；完整性校验以发布包内 `SHA256SUMS.txt` 为准，不要用「与历史 EXE 字节一致」判断构建是否成功。

## 文档

两项目均在各自 `docs/` 下维护文档（需求、用户手册、发布说明、审查与待办、操作记录、测试指南、发布安全说明），入口见各项目 README「项目文档」章节。
