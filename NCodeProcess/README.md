# CATIA 后处理 NC 程序处理工具

当前发布版本：1.0.0。

这是一个面向 Windows 7 及后续版本的免安装 NC 程序整理工具。程序使用 Python 标准库 Tkinter 编写，核心处理不依赖网络或第三方运行库。

将单个 `NCodeProcess.exe` 复制到需要处理的 CATIA 后处理文件目录，双击后程序会自动扫描 EXE 所在目录，不需要选择输入目录。MPF、APTSOURCE、待删除文件使用三个独立表格。APTSOURCE 默认不保存；只有手动勾选“保存 APTSOURCE（按时间归档）”后，才会归档到 `aptsource/YYYYMMDD_HHMMSS/`。选择 MPF 后，右侧以表格展示已有/计划写入的 MSG 信息、校验问题以及 F/S/X/Y/Z 参数统计，并可按程序新增、修改或删除刀具以及添加自定义刀具类型。

无 MSG 头部的程序默认按 HASS 处理，机床为 `HASS`；已有 MSG 头部的程序默认机床为 `2500B`；控制系统均固定为 `SIE840D`。程序信息填写后必须点击“应用设置”才会进入预览和处理，未点击时按默认规则处理。生成的 MSG 头部与第一条 NC 正文之间不额外添加空行。

图号和版次为必填项，未填写或未点击“应用设置”时会放弃修改。图号默认保持为空，界面提供当前目录以及向上三层目录的选择框，点击读取按钮后才填入所选文件夹名。新增的特殊刀具类型和按程序修改的刀具信息保存在 `NCodeProcessData/special_tools.json`。处理报告默认不输出；点击“导出报告”后，无需选择路径，带时间的 JSON 报告会自动保存到当前目录的 `NCodeProcessData` 文件夹，并只保留最新三份。默认刀具类型还包括钻头和中心钻，刀具 MSG 始终位于程序头信息最后。

升级自旧版时，程序仍会读取旧 `NCPostProcessData/special_tools.json` 和旧注册表偏好项；新增配置与报告统一写入 `NCodeProcessData`，报告文件名统一为 `ncodeprocess-report-*.json`。

## 项目文档

- [需求文档](docs/NCodeProcess-需求文档.md)（V1.1，含实施状态总览与待确认事项）
- [用户手册](docs/NCodeProcess-用户手册.md)（另有 Word/PDF 版）
- [发布说明](docs/NCodeProcess-发布说明.md)
- [审查与待办](docs/NCodeProcess-审查与待办.md)
- [程序理解与操作记录](docs/NCodeProcess-程序理解与操作记录.md)
- [测试指南](docs/NCodeProcess-测试指南.md)
- [发布安全说明](docs/NCodeProcess-发布安全说明.md)

## 界面与显示支持

界面以 Windows 7、1366×768、100% DPI 为最低完整布局目标。程序默认以非全屏方式启动，并按当前屏幕尺寸自适应；在该目标屏幕上默认窗口约为 1286×668，最小支持窗口约为 1180×650。低于此分辨率时，程序仅保证请求尺寸不超过屏幕可用范围，完整布局不作保证。

主工作区采用稳定的左右 50/50 网格，不使用可拖拽分隔条，因此启动和窗口重排时不会出现分隔条闪动。顶部“程序信息”采用四行响应式布局：第一行为基本信息，第二行为处理选项，第三行为 G00 级别与自定义刀具类型，第四行为图号候选。图号候选行为“说明 + 可伸缩下拉框 + 固定选取按钮”，长候选文本不会挤压或隐藏选取按钮。

程序保留 Windows 原生 `ttk` 主题，不强制切换为 `clam`。界面字体按系统实际可用顺序选择 `Microsoft YaHei UI`、`Microsoft YaHei`、`Segoe UI`、`Tahoma`（均不可用时回退 Tk 默认字体）；Treeview 正文为 9pt，表头为 8pt 粗体。运行时以实际字体 `measure` 计算列宽：校验列按“999 错 / 999 警”摘要内容预留空间，刀具号和所有保留文件/刀具表标题均以粗体表头实测宽度加安全留白确定最小宽度，确保 Windows 7 与 Windows 11 的表头完整显示。

在最低完整布局下，程序选择表和刀具表默认无横向溢出、无需横向滚动。窗口放大时，程序表的 `source`、`target` 列，以及刀具表的 `TOOL_CONER`、`TOOL_ANGLE`、`TOOL_TYPE` 列会扩展；`action`、`program`、`number`、`dia` 等列保持紧凑。源文件和目标文件仅在界面显示层增加间隔，不改变实际文件路径或导出报告内容。用户手动拉宽长文本列后，水平滚动条可查看溢出内容；窗口缩放时已手动设置的列宽会保留。刀具编辑器始终预留约 230px 的可见宽度；启动延后回调会在窗口关闭时安全取消，避免残留 Tk 回调异常。

“自定义刀具类型”的输入框与“添加类型”按钮紧邻排列，右侧保留可伸缩空间，避免在较宽窗口中分散控件或在最小布局中互相挤压。

## 运行

开发环境（Python 3.8+）：

```text
双击 NCodeProcess.pyw
```

也可以使用命令行预览：

```text
python -m ncodeprocess -i "D:\\CATIA\\输出目录" --bianzhi CHENXINYU --shenhe GAOWEI --drawing-number D0354F31311-201 --part-version A --nc-machine 2500B --control-system SIE840D
```

预览不会修改文件；确认执行时追加 `--yes`。命令行如需保存 APTSOURCE，应追加 `--save-aptsource`。此外支持 `--output` 指定独立输出目录、`--overwrite`、`--overwrite-fields`、`--g00-level`、`--no-m03`、`--tool number,dia,tool_coner,tool_type` 和 JSON/CSV 报告导出。

## 处理内容

- 第一层（可选递归）仅识别 `.MPF`、`.aptsource`、`.LOG`、`.MOAPTIndexes`。
- 根据 MPF 的 `PROGRAM`、APT 的 `PPRINT PROGNAME` 或文件名提取程序名，并规范化 MPF/APT 配对名称。
- aptsource 默认在确认后删除；手动启用保存时才归档到 `aptsource` 子目录。所有归档和清理动作均在界面预览并二次确认。
- 保持原文件编码（UTF-8、UTF-8 BOM、GB18030 等）和 CRLF/LF 换行风格，MPF 使用临时文件原子替换。
- 读取/补全 MSG 头部，HASS 的 `%` 起始行始终保留在第一行；刀具信息支持自动识别和人工编辑。
- 检查正文 M03，按首个 S 指令补写；统计 F/S/X/Y/Z；报告语法、G00、程序结束标记、刀具换刀等问题。

## 程序设置

「程序设置…」对话框分两页：**基本设置**（编码、待删除扩展名、允许字符、APTSOURCE 子目录、主程序扩展名、输出扩展名）与**校验规则**（结束标记/M06/S 检查开关，G00 级别：错误/警告/允许，必填 MSG 字段：编制/审核/图号/版次可勾选、程序/机床/控制系统固定必填，M03 补写位置：贴 S 后/独立行，F/S 上下限，F 离群校验：按移动/进刀/切削阶段分组检测常见档位（可调 IQR 倍数与低值/高值比例，孤立异常提示），多 S 值警告开关，换行策略：自动/CRLF/LF，辅助指令顺序：M03/M05/M08/M09）。

## 便携打包

在 Windows 上准备 Python 3.8 环境，并安装 PyInstaller 后运行：

```powershell
.\build_portable.ps1
```

输出为单文件 `dist\NCodeProcess.exe`，同时生成 `dist\NCodeProcess-Windows7-Portable.zip`。把 EXE 移到待处理目录即可使用，不会创建安装项、启动项或文件关联。编制和审核/校对信息保存在当前 Windows 用户设置中，不会在 NC 目录生成配置文件，因此移动 EXE 后仍可保留。

打包脚本会排除程序未使用的网络、SSL、通用压缩、多进程和 XML 模块，以减小发布文件体积；这些排除项不影响 NC 文件扫描、统计、校验、目录处理和报告导出功能。

发布构建已启用随机密钥字节码加密、`-OO` 优化、运行时反调试、调试模块裁剪、临时构建痕迹清理和 SHA-256 完整性记录；还可通过证书指纹启用 Authenticode 数字签名。具体构建与验证方法见 [SECURITY.md](SECURITY.md)。

## 已知规则

程序名允许中文、英文、数字、下划线和连字符。默认把 G00/G0 作为错误，默认要求程序包含 `%`、M30 或 M02 结束标记；这些规则均可在界面或命令行调整。N 号在不同后处理段落重新开始时会报告警告，不会阻止输出。

## 验证

在 Python 3.8 环境中运行：

```powershell
python -m unittest discover -s tests -v
```
