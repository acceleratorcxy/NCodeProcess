# NCodeProcess 项目集合

本仓库包含两个相互独立的 Windows 免安装项目（目标平台 Windows 7 及后续版本，Python 3.8 / Tkinter 纯标准库实现）：

- [NCodeProcess](NCodeProcess/README.md)：CATIA 后处理 NC 程序批量整理工具——批量扫描、规范化命名、补全 MSG 头部、刀具信息、M03 补写、NC 校验、参数统计与报告导出。
- [NCodeProcessReportViewer](NCodeProcessReportViewer/README.md)：NC 处理报告只读查看器——解析 `ncodeprocess-report-*.json`，以概览、统计、问题、差异、原始 JSON 页签可视化展示。

## 仓库结构

```
NCodeProcess/                     ← 仓库根
├─ NCodeProcess/                  ← 主程序项目
│  ├─ ncodeprocess/               # 源码（core 纯逻辑 / gui / cli / preferences）
│  ├─ tests/                      # 测试（159 项全通过）
│  ├─ docs/                       # 文档（需求/手册/发布说明/审查与待办/操作记录/测试指南）
│  ├─ build_portable.ps1          # 打包脚本
│  ├─ dist/ + Publish/            # 构建/发布产物（不入库）
│  └─ README.md
└─ NCodeProcessReportViewer/      ← 报告查看器项目
   ├─ ncodeprocessreportviewer/   # 源码（viewer）
   ├─ tests/                      # 测试（6 项全通过）
   ├─ docs/                       # 文档（需求/手册/发布说明/审查与待办/操作记录/测试指南）
   ├─ build_portable.ps1
   ├─ dist/ + Publish/            # 构建/发布产物（不入库）
   └─ README.md
```

## 环境与工具链

| 项目 | 要求 |
|---|---|
| 测试/打包 Python | conda 环境 `python38`（Python 3.8.19）——目标 Windows 7，**必须用 3.8 测试打包** |
| 打包工具 | PyInstaller 5.13.2（python38 环境内） |
| 打包脚本 | 各项目 `build_portable.ps1`（参数 `-CondaEnvironment python38`） |
| 发布形态 | 单文件 EXE + 便携目录 + ZIP；随机密钥字节码加密、`-OO` 优化、SHA-256 完整性记录（见各项目 SECURITY.md） |

## 构建与测试

测试（在对应项目目录执行，必须先激活或指定 python38）：

```powershell
conda run -n python38 python -m unittest discover -s tests -v
```

打包：

```powershell
powershell -ExecutionPolicy Bypass -File .\build_portable.ps1
```

- 主程序测试基线 **159 项**全通过；查看器 **6 项**全通过。
- 完整的更改 → 测试 → 打包 → 提交 → 推送操作规范见本地维护文档 `NCodeProcess/docs/NCodeProcess-更改测试打包提交流程.md`（不入库）。

## 文档

两项目均在各自 `docs/` 下维护 6 份文档（均入库）：需求文档（含实施状态总览）、用户手册、发布说明、审查与待办、程序理解与操作记录、测试指南。入口见各项目 README「项目文档」章节。

## 约定

- `dist/`、`Publish/`、`*.exe`、`*.zip`、`*.pdf`、`*.spec` 及运行时目录（`.superpowers/`）一律不入库。
- 入库内容不得包含本机隐私（绝对路径、个人邮箱、本机代理地址等）；文档与代码、流程、发布同步维护。
