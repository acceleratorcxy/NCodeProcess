# NCodeProcess 处理报告内容规范（数据字典 V1.0）

> **维护说明**：本文档定义 NC 程序处理工具生成的处理报告（`ncodeprocess-report-*.json`）应包含的全部信息字段、类型、来源与审计用途，是「报告内容清单」的权威基线。主工具（生成端）与报告查看器（消费端）均以本文档为数据契约；配套文档见 `NCodeProcessReportViewer-报告内容规范.md`（查看器侧的展示映射与容错规则）。报告字段或生成逻辑变化时，必须同步更新本文档、配套文档、示例 JSON 与测试。

## 1. 报告定位与基本约定

- **格式**：JSON（UTF-8、`ensure_ascii=False`、缩进 2 空格）；另提供 CSV 问题清单（UTF-8 BOM，`utf-8-sig`）。
- **文件名**：`ncodeprocess-report-YYYYMMDD_HHMMSS.json`；同一秒内冲突时自动追加 `-1`、`-2` 后缀。
- **位置**：GUI 点击「导出报告」或 CLI 默认写入处理目录的 `NCodeProcessData/`；CLI 可通过 `--json-report` / `--csv-report` 自定义路径。
- **保留策略**：同一数据目录内仅保留最新 3 份，旧报告自动清理。
- **兼容旧版**：查看器兼容旧前缀 `ncpostprocess-report-*.json` 与旧数据目录 `NCPostProcessData`。
- **生成时机**：报告默认不自动落盘（FR-03.9），仅在用户点击导出或 CLI 指定后生成；处理完成弹窗提示报告未自动生成。
- **内容完整性**：自动补写 M03、重命名、移动、删除、备份等关键动作一律不得省略（需求 9.5）。

## 2. 顶层 JSON 结构总览

```json
{
  "input_dir": "D:\\NC\\2026-08-05",
  "output_dir": "D:\\NC\\2026-08-05",
  "started_at": "2026-08-05T09:30:00",
  "finished_at": "2026-08-05T09:30:12",
  "success": 12,
  "failed": 0,
  "skipped": 1,
  "moved": 2,
  "deleted": 3,
  "warnings": 5,
  "errors": 0,
  "backup_dir": "D:\\NC\\backup\\20260805_093000",
  "runtime_log": [],
  "log_path": "",
  "files": []
}
```

> 上表为当前实现；`runtime_log` / `log_path`（WP-C6）与第 12 节建议新增字段（app_version、config_snapshot、user_confirmations、files[].target 等）均已实现，接入后需同步本示例。

## 3. 全局元数据字段清单

| JSON 键 | 中文名 | 类型 | 必填 | 来源 / 说明 | 示例 | 审计用途 |
|---|---|---|---|---|---|---|
| `input_dir` | 输入目录 | string | 是 | `ProcessReport.input_dir`；EXE 所在目录或 CLI `--input` 的绝对路径 | `D:\NC\2026-08-05` | 追溯处理对象 |
| `output_dir` | 输出目录 | string | 是 | CLI `--output`；未指定时等于输入目录 | `D:\NC\2026-08-05` | 追溯产物位置 |
| `started_at` | 开始时间 | string（ISO 8601） | 是 | `datetime.isoformat(timespec="seconds")` | `2026-08-05T09:30:00` | 处理时间审计 |
| `finished_at` | 完成时间 | string | 是 | 处理结束时间；异常中断时保持空字符串 | `2026-08-05T09:30:12` | 判断完成/中断 |
| `success` | 成功数 | int | 是 | 成功写入的 MPF 文件数 | `12` | 汇总计数 |
| `failed` | 失败数 | int | 是 | 失败文件数（含错误拦截未写入） | `0` | 汇总计数 |
| `skipped` | 跳过数 | int | 是 | 跳过文件数（重复保留、清理未确认、无变更等） | `1` | 汇总计数 |
| `moved` | 移动数 | int | 是 | aptsource 归档成功数 | `2` | 汇总计数 |
| `deleted` | 删除数 | int | 是 | 删除文件数（中间文件、aptsource、重复清理） | `3` | 汇总计数 |
| `warnings` | 警告条数 | int | 是 | 全部文件 issues 中 `severity=warning` 合计 | `5` | 汇总计数 |
| `errors` | 错误条数 | int | 是 | 全部文件 issues 中 `severity=error` 合计 | `0` | 汇总计数 |
| `backup_dir` | 备份目录 | string | 否 | 启用备份时 `backup/YYYYMMDD_HHMMSS` 绝对路径；未备份为空 | `D:\NC\backup\20260805_093000` | 可恢复性审计 |
| `runtime_log` | 运行日志 | array[object] | ✅ 已实现（WP-C6） | 本次运行事件序列，结构见第 9 节 | `[...]` | 运行过程审计 |
| `log_path` | 完整日志路径 | string | ✅ 恒为空（WP-R4） | 不再生成磁盘日志文件，日志完整内嵌 `runtime_log`，本字段保留为空（兼容旧字段） | `""` | 兼容保留 |
| `apt_summary` | APT 全局摘要 | object | 否（✅ 已实现，WP-A3） | 全部程序最新 APT 的聚合值：`machines`（去重机床）、`spindle_speeds`（去重转速）、`tool_loads`（去重装夹刀具）、`operations`（去重操作名）、`tool_usage`（刀具号 → 使用程序数）；无 APT 时为空对象 | `{"machines": ["3-axis Machine.1"], "tool_usage": {"1": 1}}` | 备刀清单/工艺核对 |
| `files` | 文件明细 | array | 是 | 每文件一项，结构见第 5 节 | `[]` | 明细审计 |

## 4. 汇总计数字段口径说明

- `success` 仅统计「MPF 修改并原子写入成功」的文件；`failed` 统计所有最终失败的文件（含因校验错误被拦截的 MPF）；`skipped` 统计未产生文件操作的文件（重复文件在最新文件未写入成功时保留、清理未确认、无变更等）。
- `moved` 与 `deleted` 统计已实际发生的文件系统操作；预览阶段不产生任何计数。
- `warnings` / `errors` 为全文件问题条数合计，与各文件 `issues` 的实际条数一致，供查看器直接展示；CSV 逐条展开时条数应保持一致。
- 计数由 `process_plan` 在写盘过程中累加，报告导出的计数不得二次推断。

## 5. 文件级明细字段清单（`files[]`）

| JSON 键 | 中文名 | 类型 | 必填 | 来源 / 说明 | 示例 |
|---|---|---|---|---|---|
| `file` | 源文件 | string | 是 | 相对输入目录的源路径 | `prefix_AG6D311A0101.MPF` |
| `action` | 计划动作 | string | 是 | `keep` / `move` / `delete` / `duplicate` / `error`（扫描错误） | `keep` |
| `program` | 规范程序名 | string | 否 | 提取或确认后的程序名；未识别为空 | `AG6D311A0101` |
| `encoding` | 文件编码 | string | 否 | 自动检测或强制指定的编码 | `gb2312` |
| `changes` | 变更摘要 | array[string] | 否 | 人类可读变更条目，顺序与执行一致（见第 8 节） | `["补全/更新 BIANZHI", "第 5 行 S 指令后补写 M03", "重命名为 AG6D311A0101.MPF"]` |
| `diff` | 修改差异 | array[string] | 否 | unified diff 行（修改前 → 修改后），仅原文本与处理后文本不同时存在 | `["--- 源 (before)", "+++ 目标 (after)", "@@ -1,3 +1,6 @@", "-旧行", "+新行"]` |
| `issues` | 校验/处理问题 | array[object] | 否 | 结构见第 6 节；无问题为空数组 | `[]` |
| `stats` | 参数统计 | object | 否 | 结构见第 7 节；MPF 必有，其余文件可为空 | `{...}` |
| `status` | 执行状态 | string | 否 | `success` / `failed` / `skipped` / `deleted` / `moved` / `duplicate-retained` / `duplicate-overwritten` / `duplicate-removed` / `duplicate-resolved` / `review` | `success` |
| `error_kind` | 错误类别 | string | 否 | `encoding` / `permission` / `io` / `other`；仅失败文件出现 | `io` |
| `runtime_error` | 运行错误 | string | 否 | 失败原因文本（如目标已存在、权限拒绝） | `目标已存在: AG6D311A0101.MPF` |
| `target` | 目标路径 | string | 否（✅ 已实现，第 12 节） | 重命名/移动/归档后的目标路径（绝对路径；无目标时为空） | `D:\NC\AG6D311A0101.MPF` |
| `program_name_source` | 程序名来源 | string | 否（✅ 已实现，第 12 节） | `MSG` / `PPRINT` / `文件名` / `手动确认` | `MSG` |
| `apt_meta` | APT 元数据 | object | 否（✅ 已实现，WP-A1） | 最新 APTSOURCE 解析的规划元数据：machine/pp_table/catia_version/generated_at/operate/operations/transform/spindles/feeds/coolant/tool_loads/program_name/operation_feeds/operation_spindles/tools（CUTTER/TOOLNO 刀具规格：number/dia/tool_coner/tool_type/tool_angle）；无 APT 时为空 | `{"machine": "3-axis Machine.1", "tools": [{"number": 1, "dia": "20.000", ...}]}` |
| `toolpath_stats` | APT 轨迹统计 | object | 否（✅ 已实现，WP-A2） | 最新 APTSOURCE 的 GOTO 点数/XYZ 行程极值/圆弧数/抬刀次数/抬刀平面（抬刀次数可为手动修订值） | `{"goto_count": 24156, "min_x": -334.45, ...}` |

## 6. 问题条目字段清单（`issues[]`）

| JSON 键 | 中文名 | 类型 | 必填 | 说明 | 示例 |
|---|---|---|---|---|---|
| `file` | 文件 | string | 是 | 同所属文件项的 `file` | `prefix_AG6D311A0101.MPF` |
| `line` | 行号 | int | 是 | 1 起始；文件级问题约定行号 1 | `5` |
| `text` | 原始文本 | string | 否 | 该行原始内容；文件级问题为空 | `N5 S5000` |
| `kind` | 问题类型码 | string | 是 | 稳定枚举（见下），供筛选与报告统计 | `spindle-start` |
| `severity` | 级别 | string | 是 | `error` / `warning` / `info` | `error` |
| `suggestion` | 建议 | string | 否 | 修复建议中文文案 | `请手动补写 M03` |

### `kind` 已知取值（新增类型时必须同步本文档与查看器）

| kind | 级别 | 含义 |
|---|---|---|
| `program-name` | error | 无法确定程序名，需手动确认 |
| `file-too-large` | error | 文件超过配置的单文件大小上限，已跳过处理（WP-C1） |
| `encoding` | error | 编码无法识别 / NUL 字节 / 强制编码失败 |
| `permission` | error | 无权限读取文件 |
| `io` | error | 文件读取/写入/移动失败 |
| `processing` | error | 头部/刀具/M03 处理内部错误 |
| `duplicate-msg-field` | warning | MSG 同键重复，保留第一条 |
| `duplicate-target` | warning | 目标文件名重复，按最新覆盖 |
| `required-field` | error | 必填 MSG 字段缺失或为空 |
| `program-mismatch` | error | PROGRAM 字段与规范化文件名不一致 |
| `block-number` | warning | N 号未递增/重复 |
| `G00` | error/warning | 发现 G00/G0 快速定位（级别可配置） |
| `feed-zero` | error | F0 进给为零 |
| `feed-range` | error | F 超出上下限 |
| `spindle-range` | error | S 超出上下限 |
| `negative-parameter` | error | F/S 为负值 |
| `unclosed-quote` | error | 引号未闭合 |
| `control-character` | error | 行内存在异常控制字符 |
| `conflicting-motion` | error | 同一程序段含多个运动 G 指令 |
| `invalid-address` | error | 地址后缺少数值 |
| `end-marker` | error | 缺少 % / M30 / M02 结束标记 |
| `spindle-speed` | error | 启用检查时正文无 S 转速 |
| `spindle-start` | error/warning | 无 M03；自动补写失败为 error，关闭补写为 warning |
| `spindle-direction` | error | 正文以 M04 反转启动主轴，已禁止自动补写 M03（WP-A1） |
| `tool-change` | error | 存在刀具调用但缺少 M06（启用检查时） |
| `tool-number-missing` | warning | 正文 T 调用无对应头部 Tn |
| `auto-tool-change-skipped` | warning | 多刀程序不具备自动添加换刀指令条件，已禁用并跳过（WP-A2） |
| `aux-order` | error/warning | 辅助指令顺序违规（M03/M05/M08/M09） |
| `feed-outlier` | warning | F 离群（按工艺阶段分组检测） |
| `multiple-spindle-speeds` | warning | 程序含多个不同 S 值 |
| `mutually-exclusive-m` | error | 同块互斥 M 指令（M03+M05、M03+M04、M08+M09；WP-A1 已接入 M03+M04） |
| `apt-spindle-direction` | error | APT 规划主轴方向与正文不一致（CLW 应 M03 / CCLW 应 M04；双方向时给保留/删除建议，WP-A4） |
| `apt-spindle-mismatch` | warning | MPF 的 S 转速均不在 APT 规划转速集合 ±1% 内（加工参数不符，WP-A4） |
| `apt-feed-mismatch` | warning | MPF 存在 F 值不在 APT 规划进给集合 ±10% 内（加工参数不符，WP-A4） |
| `apt-coolant-missing` | info | APT 规划 COOLNT/ON 无 M08，或 OFF 有 M08 无 M09（提示，WP-A4） |
| `apt-tool-load-mismatch` | info | APT 规划装夹刀具未全部在正文调用（提示，仅告知、不阻止输出，WP-A4） |
| `apt-tool-param-mismatch` | warning | MPF 头部 Tn 几何（直径 ±2%、圆角 ±5%）与 APT TOOLNO/CUTTER 不一致（WP-A4） |
| `apt-program-name-conflict` | warning | MPF 的 PROGRAM 字段与 APT `$$` 程序名不一致（WP-A4） |
| `apt-date-stale` | info | MPF 头部 DATE 早于 APT 生成时间（提示，WP-A4） |

## 7. 参数统计对象字段清单（`stats`）

| JSON 键 | 中文名 | 类型 | 说明 | 示例 |
|---|---|---|---|---|
| `counts` | 出现次数 | object | `F`/`S`/`X`/`Y`/`Z` → int，正文按代码部分统计 | `{"F": 10, "S": 2, "X": 30, "Y": 30, "Z": 31}` |
| `distinct` | 不同值列表 | object | `F`/`S` → array[string]，保留原始文本与出现顺序；X/Y/Z 不维护 | `{"F": ["100", "500"], "S": ["2000"]}` |
| `minimum` | 最小值 | object | `F`/`S`/`X`/`Y`/`Z` → number 或 `null`（未出现） | `{"F": 100.0, "S": 2000.0, "X": -2.5, "Y": 0.0, "Z": -10.0}` |
| `maximum` | 最大值 | object | 同 minimum | `{"F": 500.0, "S": 2000.0, "X": 4.0, "Y": 8.0, "Z": 50.0}` |
| `g00_count` | G00 次数 | int | 正文 G00/G0 出现次数 | `0` |

统计口径：仅统计头部结束后的正文代码部分，MSG、括号注释、空行与程序起始/结束标记不计入；支持负数、小数与科学计数法。

## 8. 变更摘要（`changes`）与差异（`diff`）记录约定

`changes` 为字符串数组，每条对应一次实际或计划中的修改，当前已知条目：

- `补全/更新 <KEY>` / `插入 <KEY>`（BIANZHI、SHENHE、PROGRAM、DRAWING NUMBER、PART VERSION、NC MACHINE、CONTROL SYSTEM、DATE）
- `插入刀具 T<n>` / `更新刀具 T<n>`
- `重复头部字段 <KEY>（第 N 行）`
- `重命名为 <目标文件名>`
- `第 N 行 S 指令后补写 M03` / `第 N 行前插入独立 M03`
- `在程序正文首行添加/更新换刀指令 <TnM6>`

`diff` 为 unified diff 行序列，包含 `---`（修改前文件名）、`+++`（修改后文件名）、`@@` 块头与实际增删行；查看器按行前缀着色（红=删除、绿=新增），且仅当 `original_text != output_text` 时生成。

## 9. 运行日志（`runtime_log` / `log_path`）记录约定

> **实施状态：✅ 已实现（WP-C6，2026-08-05）**。`core.py` 提供 `RuntimeLog` 事件源（内存环形缓冲默认 500 条 + 磁盘滚动日志 `NCodeProcessData/logs/ncodeprocess-YYYYMMDD.log`，单文件 1MB 轮转、磁盘最多保留 3 个文件）；`scan_directory`/`build_plan`/`process_plan`/`save_timestamped_report` 埋点，GUI 与 CLI 启动时 attach 磁盘日志并记录 startup/shutdown/settings 事件；`ProcessReport.runtime_log`/`log_path` 在导出时内嵌最新快照；缓冲超限截断时自动追加截断说明事件。
> **实施状态：✅ 已实现（WP-C6 基础；WP-R4 改为仅内存缓冲）**。`core.py` 提供 `RuntimeLog` 事件源：内存环形缓冲默认 500 条，`scan_directory`/`build_plan`/`process_plan`/`save_timestamped_report` 埋点，GUI/CLI 记录 startup/shutdown/settings 等事件；**不再生成任何磁盘日志文件**——运行日志完整内嵌报告 `runtime_log`，`log_path` 恒为空（兼容保留字段）；缓冲超限截断时自动追加截断说明事件。

**定位**：`runtime_log` 记录程序本次运行的执行过程事件（扫描、计划、处理、备份、导出、异常），与 `files[].issues`（文件有什么问题）、`files[].changes`（文件被改成什么样）互补：issues 回答「文件有什么问题」，runtime_log 回答「程序运行期间发生了什么」。

**顶层字段**：

| JSON 键 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `runtime_log` | array[object] | 是 | 运行事件序列，每条结构见下（WP-C6 已实现；WP-R4 起为唯一日志载体） |
| `log_path` | string | 否 | 恒为空（兼容保留字段；WP-R4 起不再生成磁盘日志文件） |

**事件条目字段（`runtime_log[]`）**：

| JSON 键 | 类型 | 必填 | 说明 | 示例 |
|---|---|---|---|---|
| `time` | string（ISO 8601） | 是 | 事件发生时间 | `2026-08-05T09:30:01` |
| `level` | string | 是 | `info` / `warning` / `error` | `info` |
| `event` | string | 是 | 事件类型码（枚举见下） | `scan_start` |
| `message` | string | 是 | 人类可读中文描述 | `开始扫描目录：D:\NC\2026-08-05` |
| `detail` | string | 否 | 附加上下文；`error` 事件必填异常 traceback；截断时记录截断说明 | `FileNotFoundError: ...` |

**事件类型码（`event`）已知枚举**（新增时必须同步本文档、查看器与 WP-C6 实现）：

- `startup` / `shutdown`：程序启动 / 退出（含单实例拒绝）
- `scan_start` / `scan_finish` / `scan_warning`：扫描生命周期与全局警告（无 MPF、只读目录等）
- `plan_built`：处理计划生成（文件总数、MPF 数）
- `process_start` / `process_file` / `process_finish`：处理生命周期（`process_file` 记录当前文件与进度 i/N）
- `backup_created`：备份快照完成（含备份目录）
- `export_start` / `export_finish`：报告导出（含路径与旧报告清理）
- `settings_loaded` / `settings_saved`：配置加载 / 保存（不含敏感值）
- `error` / `warning`：通用异常与警告（`error` 的 `detail` 必填 traceback）

**记录与导出规则**：

- 运行期间事件始终写入内存环形缓冲（默认最近 500 条）；磁盘滚动日志（`NCodeProcessData/logs/ncodeprocess-YYYYMMDD.log`，UTF-8，保留 3×1MB）**仅在导出报告时启用**（导出时 attach → 把缓冲历史一并落盘 → 写报告 → detach），用户不点导出报告时不自动生成 `NCodeProcessData` 目录与日志文件（WP-R3）。
- 导出报告时：`runtime_log` 内嵌本次会话缓冲（报告即单个 JSON 文件）；超限截断时追加一条 `level=warning`、`event=warning`、`message=已截断`、`detail=空` 的事件。
- 不再生成磁盘日志文件（WP-R4）：`NCodeProcessData` 目录与日志仅在用户导出报告时创建（且仅生成报告 JSON）。
- 隐私：运行日志不记录密码、令牌、邮箱、代理地址；编制/审核姓名属业务数据，不进运行日志。
- CLI 与 GUI 共用同一事件源：`core.py` 提供事件 hook，`gui` / `cli` 各自注册 sink。

## 10. CSV 报告格式

- 编码：UTF-8 BOM（`utf-8-sig`）；换行：CRLF。
- 表头：`file,line,text,kind,severity,suggestion`。
- 内容：逐文件逐问题展开一行；无问题的文件输出一行 `info` 记录（`kind` 为文件动作，`severity=info`）。
- CSV 不包含统计、差异与变更摘要，仅作问题清单快速分发用。

## 11. 审计要求对照（需求 9.5）

| 需求条款 | 报告现状 |
|---|---|
| 记录处理时间 | ✅ `started_at` / `finished_at` |
| 记录输入目录 | ✅ `input_dir` |
| 记录输出目录 | ✅ `output_dir` |
| 记录工具版本 | ✅ `app_version`（第 12 节已实现） |
| 记录用户确认项 | ✅ `user_confirmations`（第 12 节已实现，GUI/CLI 传入备份、清理、归档、覆盖等确认项） |
| 每文件变更摘要 | ✅ `changes` + `diff` |
| 不省略自动补写 M03 | ✅ `changes`（“第 N 行 S 指令后补写 M03”） |
| 不省略重命名/移动/删除 | ✅ `changes` + `action` + `status` |
| 记录备份 | ✅ `backup_dir`（启用备份时） |
| 记录运行过程事件 | ✅ `runtime_log` + `log_path`（WP-C6 已实现） |

## 12. 增强字段清单（第 12 节，已实现）

> 本节全部字段已实现（2026-08-05）：`runtime_log`/`log_path` 见第 9 节（WP-C6）；其余字段由 `process_plan` 生成时填写（`app_version` 延迟读取 `__version__`、`config_snapshot` 取处理时生效的 `Config`、`generator` 由调用方传 `gui`/`cli`、`user_confirmations` 由调用方传入、`files[].target` 记录实际目标路径、`files[].program_name_source` 由扫描/手动确认标记）。

| JSON 键 | 类型 | 状态 / 说明 |
|---|---|---|
| `app_version` | string | ✅ 已实现：工具版本追溯（来源 `__version__`），满足需求 9.5「工具版本」 |
| `report_schema_version` | int | ✅ 已实现：报告结构版本号（当前 1），供查看器按版本渲染与兼容 |
| `config_snapshot` | object | ✅ 已实现：处理时生效的 `Config` 关键键值（编码、递归、保存 APTSOURCE、G00 级别、M03 策略、F/S 上下限、辅助顺序、启发式阈值、文件上限、抬刀阈值、备份开关等），保证结果可复现 |
| `user_confirmations` | array[string] | ✅ 已实现：用户确认项（备份确认、清理确认、程序名确认、覆盖确认），满足需求 9.5 |
| `scan_warnings` | array[string] | ✅ 已实现：全局扫描警告（无 MPF、目录只读等） |
| `archive_stamp` | string | ✅ 已实现：APTSOURCE 归档时间戳 `YYYYMMDD_HHMMSS` |
| `elapsed_seconds` | number | ✅ 已实现：处理耗时（finished_at - started_at） |
| `generator` | string | ✅ 已实现：报告来源（`gui` / `cli`） |
| `files[].target` | string | ✅ 已实现：重命名/移动/归档后的目标路径 |
| `files[].program_name_source` | string | ✅ 已实现：程序名来源（MSG / 文件名 / PPRINT / 手动确认） |

## 13. 完整示例 JSON

```json
{
  "input_dir": "D:\\NC\\2026-08-05",
  "output_dir": "D:\\NC\\2026-08-05",
  "started_at": "2026-08-05T09:30:00",
  "finished_at": "2026-08-05T09:30:12",
  "success": 1,
  "failed": 0,
  "skipped": 0,
  "moved": 1,
  "deleted": 2,
  "warnings": 2,
  "errors": 0,
  "backup_dir": "D:\\NC\\backup\\20260805_093000",
  "app_version": "1.0.0",
  "report_schema_version": 1,
  "config_snapshot": {
    "encoding": "auto",
    "g00_level": "error",
    "m03_position": "after-s",
    "newline": "auto",
    "aux_checks": ["m03-before-motion", "m05-before-end", "m08-before-cut", "m09-before-end"]
  },
  "user_confirmations": ["已确认：执行目录处理"],
  "scan_warnings": [],
  "archive_stamp": "20260805_093000",
  "elapsed_seconds": 12.0,
  "generator": "gui",
  "runtime_log": [
    {"time": "2026-08-05T09:30:01", "level": "info", "event": "scan_start", "message": "开始扫描目录：D:\\NC\\2026-08-05", "detail": ""},
    {"time": "2026-08-05T09:30:03", "level": "info", "event": "plan_built", "message": "生成处理计划：3 个文件，1 个 MPF", "detail": ""},
    {"time": "2026-08-05T09:30:12", "level": "info", "event": "process_finish", "message": "处理完成：成功 1，失败 0，移动 1，删除 2", "detail": ""}
  ],
  "log_path": "",
  "files": [
    {
      "file": "prefix_AG6D311A0101.MPF",
      "action": "keep",
      "program": "AG6D311A0101",
      "target": "D:\\NC\\2026-08-05\\AG6D311A0101.MPF",
      "program_name_source": "MSG",
      "encoding": "gb2312",
      "changes": [
        "补全/更新 BIANZHI",
        "插入 SHENHE",
        "插入刀具 T1",
        "第 5 行 S 指令后补写 M03",
        "重命名为 AG6D311A0101.MPF"
      ],
      "diff": [
        "--- prefix_AG6D311A0101.MPF (before)",
        "+++ AG6D311A0101.MPF (after)",
        "@@ -1,2 +1,5 @@",
        "-MSG(\"BIANZHI:\")",
        "+MSG(\"BIANZHI:张工\")",
        "+MSG(\"SHENHE:李工\")",
        "+MSG(\"T1:DIA=20.000,TOOL_CONER=3.000\")"
      ],
      "issues": [
        {
          "file": "prefix_AG6D311A0101.MPF",
          "line": 3,
          "text": "MSG(\"PROGRAM:AG6D311A0101\")",
          "kind": "duplicate-msg-field",
          "severity": "warning",
          "suggestion": "MSG 字段 PROGRAM 出现多次，已保留第一条有效记录，请确认是否合并或删除重复项"
        }
      ],
      "stats": {
        "counts": {"F": 3, "S": 1, "X": 4, "Y": 4, "Z": 5},
        "distinct": {"F": ["100", "500"], "S": ["2000"]},
        "minimum": {"F": 100.0, "S": 2000.0, "X": -2.5, "Y": 0.0, "Z": -10.0},
        "maximum": {"F": 500.0, "S": 2000.0, "X": 4.0, "Y": 8.0, "Z": 50.0},
        "g00_count": 0
      },
      "status": "success"
    },
    {
      "file": "prefix_AG6D311A0101_I.aptsource",
      "action": "move",
      "program": "AG6D311A0101",
      "target": "D:\\NC\\2026-08-05\\aptsource\\20260805_093000\\AG6D311A0101.aptsource",
      "program_name_source": "文件名",
      "encoding": "gb2312",
      "changes": [],
      "diff": [],
      "issues": [],
      "stats": null,
      "status": "moved"
    },
    {
      "file": "a.LOG",
      "action": "delete",
      "program": "",
      "encoding": "",
      "changes": [],
      "diff": [],
      "issues": [],
      "stats": null,
      "status": "deleted"
    }
  ]
}
```

## 14. 维护约定

- 任何报告字段的新增、删除或语义调整：同步更新本文档、`NCodeProcessReportViewer-报告内容规范.md`、示例 JSON、查看器容错逻辑与相关测试。
- `kind` 枚举新增时必须同时更新查看器的展示/筛选/高亮规则。
- 报告生成逻辑改动（`ProcessReport`、`process_plan` 的 item 组装、`save_timestamped_report`、`write_csv`）视为行为变更，按仓库流程走测试、打包与提交门。
- 建议新增字段（第 12 节）落地时，先实现、补测试，再回填本文档「已实现」标注。
- 运行日志 `event` 枚举新增时必须同步查看器筛选/高亮、WP-C6 实现与本规范第 9 节。
