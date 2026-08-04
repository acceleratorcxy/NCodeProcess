# NCodeProcess 程序理解与操作记录

> 本文件合并自 `2026-08-04-程序理解与操作记录.md`（Batch 1 及更早）与 `2026-08-05-程序理解与操作记录-Batch2.md`（Batch 2 会话），后续开发操作在此持续追加。
> 历史实施计划与设计规格见 `docs/archive/superpowers/`（任务已完成，归档备查；该目录仅本地保留，git 不跟踪）。

---

## 一、关于这个程序的详细理解

### 1. 程序定位

**NCodeProcess** 是 CATIA 后处理 NC 程序批量整理工具，目标平台为 Windows 7（必须兼容）及后续 Windows，交付形态为免安装便携式 EXE（PyInstaller 单文件、AES 加密 PYZ、Python 3.8 / Tkinter 纯标准库）。后处理通常生成多类文件：`MPF`（数控主程序，交付核心）、`aptsource`（APT 源文件，是否留档由用户决定，默认删除）、`LOG`/`MOAPTIndexes`（中间文件，默认清理）。工具在 EXE 所在目录自动完成：批量整理文件 → 规范程序名 → 补全程序头部 → 检查并补写 M03 → 校验 NC 指令 → 参数统计与处理报告。V5-2500B 与 HASS 两类后处理生成的 MPF 头部差异大（HASS 常缺头部需自动补写、V5-2500B 常缺 M03）。

### 2. 架构分层

```
NCodeProcess/
├─ ncodeprocess/
│  ├─ __init__.py      # 版本号（当前 1.0.0）
│  ├─ core.py          # 纯逻辑层：模型、扫描、计划、处理、校验、统计（无 GUI 依赖）
│  ├─ gui.py           # Tkinter 界面层（表格、表单、设置对话框、编辑/对比窗口、悬停提示）
│  ├─ cli.py           # 命令行入口
│  ├─ preferences.py   # 设置存储：注册表 + 后备设置文件双后端
│  └─ __main__.py      # 入口
├─ launcher.pyw / NCodeProcess.pyw / NCodeProcess.spec / build_portable.ps1
├─ tests/              # test_core / test_gui / test_preferences / test_release_assets
└─ docs/               # 需求文档、用户手册、发布说明、审查与待办、操作记录、流程文档
```

设计上 `core.py` 与 GUI 解耦：CLI 与 GUI 复用同一套处理逻辑。

### 3. 核心数据模型（core.py）

| 模型 | 作用 |
|---|---|
| `Config` | 处理配置。基础项（递归/编码/M03/G00/结束标记/M06/S 等）+ Batch 2 新增：`required_fields`（必填 MSG 字段）、`m03_position`（M03 补写位置）、`feed_min/max`/`spindle_min/max`（F/S 上下限）、`aux_checks`（辅助指令顺序规则集）、`newline`（换行策略） |
| `ProgramInfo` | 编制/审核/图号/版次/机床/控制系统/日期/刀具；`fields(program)` 生成 MSG 头字段 |
| `ToolInfo` | 刀具号 + DIA/TOOL_CONER/TOOL_ANGLE/TOOL_TYPE |
| `FilePlan` | 单文件处理计划（来源/类型/程序名/目标/动作/问题/文本/统计/修改说明） |
| `Issue` | 校验问题（文件名/行号/类型/级别/原文/建议） |
| `Stats` | F/S/X/Y/Z 计数、极值、G00 计数 |
| `ScanResult` / `ProcessReport` | 扫描结果 / 处理报告（JSON/CSV） |

### 4. 处理流水线

```
scan_directory(目录, Config) → build_plan(scan, ProgramInfo, Config)
    [program_defaults 头部默认值 → apply_header 补/写 MSG 头（含 required_fields 判断）
     → add_initial_tool_change → add_m03（按 m03_position 策略）
     → analyze_program（validate_program 校验 + 统计，含 feed/spindle 上下限、aux_checks 顺序规则）]
→ GUI/CLI 预览 → process_plan（原子写/移动/删除，输出按 newline 策略归一）
→ save_timestamped_report / write_json / write_csv
```

校验规则要点：`validate_program` 检查头部完整性、N 号规律、G00（级别可配）、缺结束标记、控制字符、非法地址、F0、负 F/S、S/F 上下限、换刀与 M06、辅助指令顺序等；**自动补写 M03 失败时 spindle-start 升级为 error**（FR-05.6）。

### 5. GUI 结构与交互（Win7 兼容）

- **顶部程序信息区**：基本信息（编制/审核/图号/版次/日期）、处理选项（应用设置/保存编制校对/覆盖已有值/自动补写 M03/自动换刀）、**自定义刀具类型**（独立一行：输入框+添加类型）、图号候选（可伸缩下拉框+固定选取按钮）。
- **「程序设置…」对话框**：`ttk.Notebook` 两页——**基本设置**（编码/待删除扩展名/允许字符/APTSOURCE/主程序扩展名/输出扩展名）与**校验规则**（结束标记/M06/S 开关、G00 级别、必填 MSG 字段、M03 补写位置、F/S 上下限、换行策略、辅助指令顺序）。布局锁定宽 ≤640、高 ≤500。
- **主工作区**：左右 50/50 网格（无 PanedWindow）；左侧 Notebook（保留/归档、APTSOURCE、待删除），右侧 Notebook（解析信息、校验问题、参数统计、修改差异）+ 刀具信息区（表格+编辑器）。
- **程序代码编辑/对比窗口**：双击或右键编辑代码（行号 gutter，保存后内存重跑流水线）；多选两条程序对比（左右独立行号、独立滚动、红删绿增）。
- **单元格悬停提示**：9 张表格悬停 1.5 秒、内容被截断时弹出置顶小窗。
- **字体/列宽自适应**：`window_geometry_for_screen` 按屏幕收缩；`choose_ui_font_family` 优选微软雅黑；列宽按实际字体 `measure` 拟合，保证默认状态无需拖拽可见全部表头。

### 6. 关键机制

- **设置存储双后端（preferences.py）**：优先写 `HKCU\Software\NCodeProcess`（REG_SZ）；注册表不可写时回退 `%APPDATA%\NCodeProcess\settings.json`（不可用再回退用户主目录）。`load_all` 注册表优先、后备文件覆盖；`save_all` 返回 `(backend, location)` 供界面提示；`clear_all` 双后端一起清。Batch 1 设置项持久化到注册表/文件，Batch 2 新增项**仅本次运行生效**（按需求第 8 节「配置优先保存在内存中」）。
- **校验问题类型**：`required-field/G00/end-marker/spindle-start/tool-change/feed-zero/feed-range/spindle-range/aux-order` 等。
- **线程安全**：GUI 界面刷新通过 `self.after()` 回到主线程；启动延后回调在窗口销毁时安全取消。
- **GUI 测试隔离**：`test_gui._build_app` 固定使用 `TEST_SETTINGS_KEY` 创建 App，避免本机真实注册表残留设置污染默认值断言。
- **打包**：`build_portable.ps1` 每次生成临时 16 位 AES 键注入 PyInstaller 加密 PYZ；产出 `dist\NCodeProcess.exe`、`NCodeProcess-Package\`、`NCodeProcess-Windows7-Portable.zip` 及 `SHA256SUMS.txt`。
- **推送**：直连 GitHub 常失败，可用 `git -c http.proxy=http://<代理地址> push origin master` 单次注入代理（本机实际代理地址见本地流程文档 `docs/NCodeProcess-更改测试打包提交流程.md`）。

---

## 二、操作记录

### 2.1 2026-08-04（Batch 1 及更早）

| # | 改动 | 说明 |
|---|---|---|
| 1 | 窗口尺寸优化 | 1920×1080/Win7 下过大 → 收缩至约 1290×720，默认可见全部表头 |
| 2 | M03 补写误触发修复 | `add_m03` 搜索 S 指令时先剔除括号注释 |
| 3 | M03 无法补写升级为 error | 自动补写开启且无可插入位置时 spindle-start 从 warning 升级为 error（FR-05.6） |
| 4 | 手动编辑程序代码 | 右键「编辑程序代码」打开编辑页，保存后重新审查（`reprocess_file`） |
| 5 | 编辑页行号 | 手动编辑页只读行号 gutter，随编辑/滚动同步 |
| 6 | 多选对比 + 右键菜单 | 程序选择栏支持多选；编辑与对比入口改为右键菜单；对比**有且只有 2 条**选中 |
| 7 | 对比窗口配色 + 双侧行号 | 不同部分红底 `#ffd6d6`、相同部分绿底 `#d9f2d9`；两侧各自行号 |
| 8 | 对比窗口左右独立滚动 | 修复「B 侧滚动带动 A 侧行号」回归 |
| 9 | 对比窗口标题显示文件名+程序名 | 标题与两侧面板均显示「文件名（程序名）」 |
| 10 | 单元格悬停提示 | 9 张表格悬停 1.5 秒、内容被截断时弹出置顶小窗 |
| 11 | 注册表写入统一模型 | `REGISTRY_DEFAULTS`/`load_all`/`save_all`/`clear_all`；恢复默认与清除覆盖编制/审核 |
| 12 | 程序设置 GUI 化（第一批） | 编码/待删除扩展名/允许字符/结束标记/M06/S/APTSOURCE 子目录；顶部栏更名为「程序运行配置」 |

### 2.2 2026-08-04 晚 – 2026-08-05（Batch 2 会话）

| # | 改动 | 说明 | 提交 |
|---|---|---|---|
| 1 | 设置文件后备存储 | 注册表不可写时回退 `%APPDATA%\NCodeProcess\settings.json`，再回退用户主目录；双后端 | `9ffe5d3` |
| 2 | Task A：必填 MSG 字段可配置 | `Config.required_fields`；validate/apply_header 跟随；GUI 4 个可勾选项（程序/机床/控制系统固定必填） | `e1af69f` |
| 3 | Task B：M03 补写位置策略 | `m03_position`（after-s/standalone）；standalone 在首条切削/运动指令前插独立行 | `28029c5` |
| 4 | Task C：F/S 上下限 | `feed/spindle_min·max`；越界报 `feed-range`/`spindle-range`（error） | `327da7c` |
| 5 | Task E：换行强制策略 | `newline`（auto/crlf/lf）；`_effective_newline` 统一三处拼接点，输出按策略归一 | `01b9de4` |
| 6 | Task D：辅助指令顺序校验 | `aux_checks`（M03/M05/M08/M09 四条，规则经用户确认）；M03 规则为 error，其余 warning；M09 未出现不提示 | `75b6b74` |
| 7 | Task F：设置对话框两页重构 | `ttk.Notebook` 两页（基本设置/校验规则） | `71756fb` |
| 8 | 布局优化 | 必填字段等间距；G00 级别移入设置校验规则页；自定义刀具类型独立成行；修复 `_build_app` 真实注册表污染 | `6224090` |
| 9 | F/S 上下限间距修复 | 「输入框 ~ 输入框」改入每行独立子容器 pack 紧凑排列 | `3146557` |
| 10 | 文档归档 | Batch 2 计划、Release Note、本操作记录、流程文档维护 | `f992574` 等 |

**关键决策**
- **机床行程 X/Y/Z 检查不实施**：需求 7.3 该条按用户决定排除在 Batch 2 之外。
- **辅助指令顺序规则确认**：4 条规则全部启用；级别混合；仅当相关指令都出现且顺序错误时报告。
- **自定义刀具类型不放 tool_frame**：实测放底部刀具信息区会使主窗口最低高度超限（reqheight 696>668，破坏 1286×668 最低布局锁定），最终放回程序信息区腾出的行。
- **GUI 测试注册表隔离**：本机真实 `HKCU\Software\NCodeProcess` 残留 EXE 运行保存的 `require_m06=1`/`require_spindle_speed=1` 曾导致默认值断言失败；`_build_app` 改用 `TEST_SETTINGS_KEY`。

### 2.3 打包与同步记录（conda python38）

| 时间 | EXE SHA256（前缀） | 内容 |
|---|---|---|
| 2026-08-04 18:02 | `1433d0…` | 窗口 1290×720 优化 |
| 2026-08-04 19:43–20:06 | `b04076…`/`bec88f…`/`2a9c81…`/`bafd54…` | +M03 注释修复 / +M03 升级 error / +手动编辑 / +编辑页行号 |
| 2026-08-04 之后 | `aca8cb4d…`/`5fd3752d…`/`fc2595f7…` | +对比功能 / +独立滚动、标题 / +悬停提示 |
| 2026-08-05 00:00 前后 | `0ac50327…` | Batch 2 全部功能 + 两页重构 + 计划/Release Note 归档 |
| 2026-08-05 00:13 后 | `ce9b2b42…` | + 布局优化（6224090） |
| 2026-08-05 00:20 后 | `eb7264ed…` | + F/S 上下限间距修复（3146557） |

每次打包后校验 EXE 哈希与 `SHA256SUMS.txt` 一致，并同步 `Publish\` 5 个目标（exe / 便携包 / Package 目录 / 无md发布 zip / 最终发布 zip）。

### 2.4 提交记录（截至 2026-08-05）

```
3bcd84a docs: 操作记录中代理地址通用化为占位符
4b35160 docs: 流程文档改名对齐命名格式并保持仅本地维护
77ecec2 docs: 需求文档升级 V1.1 并同步发布说明引用
ebecd78 docs: 完善需求文档并移入 docs 目录，纳入同步维护
a3d3015 docs: 完善流程文档并同步发布说明/审查与待办/操作记录
449826c chore(git): 归档文档仅本地保留，从 git 移除跟踪
01834c3 docs: 整理 docs 文件夹，归档已完成计划并合并审查与记录文档
3146557 refactor(gui): F/S 上下限输入框与~改为独立子容器紧凑排列
6224090 refactor(gui): 必填字段等间距排版、G00 级别移入设置、自定义刀具类型独立成行
f992574 docs: 归档 Batch 2 实施计划与发布说明
71756fb refactor(gui): 程序设置对话框改为分组两页布局并锁定 Batch 2 控件
75b6b74 feat(core): 辅助指令顺序校验（aux_checks），M03/M05/M08/M09 顺序规则
01b9de4 feat(core): 换行强制策略（auto/crlf/lf），输出按策略归一
327da7c feat(core): F/S 上下限校验（feed/spindle_min/max），validate_program 越界报 error
28029c5 feat(core): M03 补写位置策略可配置（after-s/standalone），add_m03 按策略插值
e1af69f feat(core): 必填 MSG 字段可配置（required_fields），validate/apply_header 跟随
9ffe5d3 feat(prefs): 注册表不可写时回退到设置文件保存设置
（更早提交见 `git log`；注：2026-08-05 隐私清洗重写历史后全部提交哈希已变更，上表为当前哈希）
```

### 2.5 配套工作

- **实施计划**：`docs/archive/superpowers/plans/`（Batch 1、Batch 2、Win7 布局计划，任务已完成；仅本地保留，git 不跟踪）
- **设计规格**：`docs/archive/superpowers/specs/`（同上，仅本地保留）
- **发布说明**：`docs/NCodeProcess-发布说明.md`
- **审查与待办**：`docs/NCodeProcess-审查与待办.md`
- **测试指南**：`docs/NCodeProcess-测试指南.md`（测试架构、基线、隔离约定与添加测试的做法）
- **流程文档**：`docs/NCodeProcess-更改测试打包提交流程.md`（仅本地维护，不入库，2026-08-05 起在 `.gitignore` 中）
- **测试基线**：会话起始 132 项 → Batch 2 后 170 项 → 2026-08-05 合并精简后 **159 项**（覆盖不变），全量通过（conda python38）

### 2.6 隐私清洗（2026-08-05）

- **起因**：例行检查入库文件时发现：操作记录含本机代理地址 `http://127.0.0.1:7890`；git 历史提交的作者/提交者邮箱为个人邮箱 `cxyhhh@icloud.com`。
- **处理**：
  1. 操作记录代理地址改为占位符 `http://<代理地址>`，实际地址仅保留在本地流程文档（提交 `3bcd84a`）。
  2. README 示例路径 `D:\CATIA\输出目录` 经用户确认保留（纯示例，无个人信息）。
  3. 重写全部历史邮箱 `cxyhhh@icloud.com` → `noreply@example.com`：`git filter-branch --env-filter` → 删除 `refs/original` 备份 → `reflog expire --expire=now --all` → `gc --prune=now` → `push --force-with-lease` 覆盖远程。
- **影响**：历史提交哈希全部改变（本文档及发布说明中的哈希已同步更新为当前值）；旧哈希引用失效；远程已强制推送，其他克隆需重新同步。
- **约定**：后续提交前按流程文档「提交前隐私审核」检查入库内容，本机路径/用户名/邮箱/代理地址等一律通用化后再提交。

---

## 三、后续建议（可选）

1. **Batch 2 配置持久化**：必填字段/M03 策略/S/F 上下限/辅助顺序/换行目前仅本次运行生效；如需与 Batch 1 一致持久化，需扩展 `REGISTRY_DEFAULTS` 并同步调整 preferences 测试。
2. **机床行程检查**：按用户决定暂不实施；如后续需要可单独规划（`Config.machine_limits` + 越程校验 + 未配置时 info 提示）。
3. **辅助指令顺序规则按机床配置**：当前为全局规则集，不区分机床。
4. **版本号**：当前 `__version__` 仍 1.0.0，正式发版前建议提升并同步 `VERSION.txt`/`version_info.txt`。
5. **流程文档入库**：`docs/NCodeProcess-更改测试打包提交流程.md` 按用户决定仅本地维护、不入库（已在 `.gitignore` 中；如需团队共享可重新评估）。
6. **审查与待办**：详见 `docs/NCodeProcess-审查与待办.md`（线程安全、子窗口适配、备份/只读预检等未处理项）。
