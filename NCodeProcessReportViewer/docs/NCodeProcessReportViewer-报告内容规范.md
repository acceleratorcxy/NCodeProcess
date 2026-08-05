# NCodeProcessReportViewer 报告内容规范（数据字典 V1.0）

> **维护说明**：本文档是报告查看器（消费端）侧的「报告内容清单」，与主工具生成端规范 `NCodeProcess-报告内容规范.md` 互为数据契约。查看器以本文件描述的字段结构解析、校验并展示报告；字段变更时必须同步两侧文档与查看器代码。本文档面向两类读者：需要知道报告里有什么的验收人员，以及需要维护查看器/主工具字段一致性的开发人员。

## 1. 查看器可读的报告范围

- **文件命名**：`ncodeprocess-report-*.json`（现行）与 `ncpostprocess-report-*.json`（旧版兼容）。
- **扫描位置**：查看器 EXE 所在目录、`NCodeProcessData/`、`NCPostProcessData/`（旧版数据目录），按修改时间倒序排列。
- **手动打开**：任意路径的 JSON 报告文件。
- **加载校验**（`load_report`）：根节点必须是 JSON 对象；`files` 必须是数组；数组中的非对象项会被过滤。校验失败时弹窗报错且不影响已加载报告。
- **编码**：读取使用 `utf-8-sig`，兼容带/不带 BOM。

## 2. 顶层结构总览

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

## 3. 全局元数据字段清单

| JSON 键 | 中文名 | 类型 | 必填 | 说明 | 查看器用途 |
|---|---|---|---|---|---|
| `input_dir` | 输入目录 | string | 是 | 处理输入目录绝对路径 | 概览页元信息展示 |
| `output_dir` | 输出目录 | string | 是 | 处理输出目录绝对路径 | 概览页元信息展示 |
| `started_at` | 开始时间 | string | 是 | ISO 8601 | 概览页元信息展示 |
| `finished_at` | 完成时间 | string | 是 | 中断时为空 | 概览页元信息展示 |
| `success` | 成功数 | int | 是 | 成功写入 MPF 数 | 概览汇总 + 结果柱状图 |
| `failed` | 失败数 | int | 是 | 失败文件数 | 概览汇总 + 结果柱状图 |
| `skipped` | 跳过数 | int | 是 | 跳过文件数 | 概览汇总 + 结果柱状图 |
| `moved` | 移动数 | int | 是 | aptsource 归档数 | 概览汇总 + 结果柱状图 |
| `deleted` | 删除数 | int | 是 | 删除文件数 | 概览汇总 + 结果柱状图 |
| `warnings` | 警告条数 | int | 是 | 全部问题 warning 合计 | 概览汇总 + 问题柱状图 |
| `errors` | 错误条数 | int | 是 | 全部问题 error 合计 | 概览汇总 + 问题柱状图 |
| `backup_dir` | 备份目录 | string | 否 | 未备份为空 | 概览页元信息（可选展示） |
| `runtime_log` | 运行日志 | array[object] | ✅ 已实现（WP-C6） | 运行事件序列，结构见第 8 节映射 | 「运行日志」页签时间线 |
| `log_path` | 完整日志路径 | string | ✅ 恒为空（WP-R4） | 不再生成磁盘日志文件，兼容保留字段 | 运行日志页签底部提示「日志已内嵌报告」 |
| `files` | 文件明细 | array | 是 | 见第 4 节 | 文件明细表 + 各页签 |

## 4. 文件级明细字段清单（`files[]`）

| JSON 键 | 中文名 | 类型 | 必填 | 说明 | 查看器用途 |
|---|---|---|---|---|---|
| `file` | 源文件 | string | 是 | 相对输入目录路径 | 文件明细表、问题表、差异页标题 |
| `action` | 计划动作 | string | 是 | `keep`/`move`/`delete`/`duplicate`/`error` | 文件明细表「动作」列 |
| `program` | 程序名 | string | 否 | 未识别为空 | 文件明细表「程序/文件」列优先显示 |
| `encoding` | 文件编码 | string | 否 | 检测/强制编码 | 概览元信息（可展示） |
| `changes` | 变更摘要 | array[string] | 否 | 见第 7 节 | 「修改与差异」页逐条展示「修改：…」 |
| `diff` | 修改差异 | array[string] | 否 | unified diff 行 | 「修改与差异」页红/绿着色 |
| `issues` | 问题 | array[object] | 否 | 见第 5 节 | 校验问题表、错误/警告计数、文件表着色 |
| `stats` | 参数统计 | object | 否 | 见第 6 节；null 时统计页跳过该文件 | 参数统计表 |
| `status` | 执行状态 | string | 否 | `success`/`failed`/`skipped`/`deleted`/`moved`/`duplicate-*`/`review` | 文件明细表「动作」列优先于 action 展示 |
| `error_kind` | 错误类别 | string | 否 | `encoding`/`permission`/`io`/`other` | 原始 JSON 页（可扩展为错误筛选） |
| `runtime_error` | 运行错误 | string | 否 | 失败原因 | 原始 JSON 页（可扩展展示） |
| `target` | 目标路径 | string | 否（✅ 已实现，第 12 节） | 重命名/移动/归档后的目标路径 | 文件明细表「目标」列 |
| `program_name_source` | 程序名来源 | string | 否（✅ 已实现，第 12 节） | `MSG`/`PPRINT`/`文件名`/`手动确认` | 文件明细表程序名旁来源标记 |

## 5. 问题条目字段清单（`issues[]`）

| JSON 键 | 中文名 | 类型 | 必填 | 查看器用途 |
|---|---|---|---|---|
| `file` | 文件 | string | 是 | 问题表「文件」列 |
| `line` | 行号 | int | 是 | 问题表「行」列 |
| `text` | 原始文本 | string | 否 | 问题表「原始文本」列 |
| `kind` | 类型码 | string | 是 | 问题表「类型」列；错误/警告计数依据 |
| `severity` | 级别 | string | 是 | `error` 红字加粗、`warning` 橙字加粗（文件表与问题表） |
| `suggestion` | 建议 | string | 否 | 问题表「建议」列 |

`kind` 已知取值及含义与生成端规范第 6 节完全一致（`program-name`、`file-too-large`、`encoding`、`permission`、`io`、`processing`、`duplicate-msg-field`、`duplicate-target`、`required-field`、`program-mismatch`、`block-number`、`G00`、`feed-zero`、`feed-range`、`spindle-range`、`negative-parameter`、`unclosed-quote`、`control-character`、`conflicting-motion`、`invalid-address`、`end-marker`、`spindle-speed`、`spindle-start`、`spindle-direction`、`tool-change`、`tool-number-missing`、`auto-tool-change-skipped`、`aux-order`、`feed-outlier`、`multiple-spindle-speeds`、`mutually-exclusive-m`）。新增类型时同步本文档与查看器高亮/筛选。

## 6. 参数统计对象字段清单（`stats`）

| JSON 键 | 中文名 | 类型 | 查看器用途 |
|---|---|---|---|
| `counts` | 出现次数 | object（F/S/X/Y/Z → int） | 参数统计表「次数」列 |
| `distinct` | 不同值列表 | object（F/S → array[string]） | 原始 JSON 页（可扩展为详细视图） |
| `minimum` | 最小值 | object（F/S/X/Y/Z → number/null） | 参数统计表「最小值」列 |
| `maximum` | 最大值 | object（同上） | 参数统计表「最大值」列 |
| `g00_count` | G00 次数 | int | 参数统计表「G00 检查」列（>0 显示「发现 N 处」，否则「未发现」） |

数值展示统一使用 `format_number`：三位小数、去除末尾零与小数点（`100.000 → 100`、`3.500 → 3.5`）；`null`/空显示为空。

## 7. 变更摘要与差异展示约定

- `changes`：逐条前缀「修改：」展示，保持生成端顺序。
- `diff`：按 unified diff 行前缀着色——`-`（非 `---`）红色（删除）、`+`（非 `+++`）绿色（新增）、`---`/`+++`/`@@` 蓝色（文件头/块头），其余默认色。
- 一个文件既有 `changes` 又有 `diff` 时先展示变更摘要再展示差异；两者都为空时跳过该文件。
- 主工具的「修改差异」页采用双栏 + 3 行上下文折叠；查看器为只读单流差异展示，两者数据同源（`diff`）。

## 8. 查看器页签 → 字段映射清单

| 页签 | 展示内容 | 消费字段 |
|---|---|---|
| 概览与可视化 | 汇总计数卡片、输入/输出目录与时间元信息（含工具版本/报告来源/报告结构版本/处理耗时/归档时间戳/备份目录/用户确认项/扫描警告，第 12 节）、文件处理结果柱状图、校验问题柱状图 | `success/failed/skipped/moved/deleted`、`warnings/errors`、`input_dir/output_dir/started_at/finished_at`、`app_version`/`generator`/`report_schema_version`/`elapsed_seconds`/`archive_stamp`/`backup_dir`/`user_confirmations`/`scan_warnings` |
| 参数统计 | 每文件每参数一行：文件/程序、参数、次数、最小值、最大值、G00 检查 | `files[].stats.{counts,minimum,maximum,g00_count}` |
| 校验问题 | 全部或所选文件的逐条问题 | `files[].issues[]` |
| 修改与差异 | 逐文件变更摘要 + unified diff | `files[].changes`、`files[].diff` |
| 运行日志 | 运行事件时间线（时间 / 级别 / 事件 / 消息 / 详情，级别着色，事件筛选；error 事件详情含 traceback） | `runtime_log[]`（WP-C6 已实现；详情列 WP-R1；WP-R4 起日志唯一载体） |
| 原始 JSON | 完整报告原文（只读、可横向滚动） | 全量 `files[]` |

## 9. 容错与兼容性规则（查看器侧）

- 缺失字段一律回退安全默认值：计数 `0`、字符串空、数组空，不得因单个文件缺字段而整体崩溃。
- `files` 中非对象项直接过滤；`issues` 中非 dict 项在计数时跳过。
- 报告文件在展示期间被外部删除/移动：重新扫描后列表刷新，不保留失效索引（当前实现通过每次 `refresh_reports` 重建列表保证）。
- 数值字段非数值时按原文本展示，不做 `float()` 抛异常。
- 兼容旧报告前缀 `ncpostprocess-report-*` 与旧数据目录 `NCPostProcessData`。
- `runtime_log` 缺失时「运行日志」页签显示空并提示；`runtime_log` 超限截断时展示截断说明；日志完整内嵌报告，`log_path` 恒为空（不再提示磁盘日志路径）。
- 超大报告（数十 MB）：建议后续将加载移至后台线程并给出进度/状态反馈（对应 WP-14 建议项），当前为同步加载。

## 10. 审计要求对照（需求 9.5，查看器角度）

| 信息 | 是否可查 |
|---|---|
| 处理时间 | ✅ 概览页 |
| 输入/输出目录 | ✅ 概览页 |
| 工具版本 | ✅ 概览页「工具版本」（`app_version`，第 12 节已实现） |
| 用户确认项 | ✅ 概览页「用户确认项」（`user_confirmations`，第 12 节已实现） |
| 每文件变更摘要 | ✅ 修改与差异页 |
| 自动补写 M03 / 重命名 / 移动 / 删除 | ✅ 修改与差异页 + 文件表状态 |
| 备份目录 | ✅（生成端写入后可在概览/原始 JSON 查看） |
| 运行过程事件 | ✅ 「运行日志」页签展示 `runtime_log` / `log_path`（WP-C6 已实现） |

## 11. 增强字段（查看器侧消费，第 12 节已实现）

> 本节字段已实现（2026-08-05）：`runtime_log`/`log_path` 见第 8 节（WP-C6）；其余字段由概览元信息与文件明细表消费展示。

| JSON 键 | 查看器展示（已实现） |
|---|---|
| `app_version` | ✅ 概览页元信息「工具版本」行 |
| `report_schema_version` | ✅ 概览页元信息「报告结构版本」行（按版本渲染逻辑后续可扩展） |
| `config_snapshot` | ✅ 原始 JSON 页查看（未单独建页） |
| `user_confirmations` | ✅ 概览页「用户确认项」列表 |
| `scan_warnings` | ✅ 概览页「扫描警告」提示区 |
| `archive_stamp` | ✅ 概览页归档时间戳展示 |
| `elapsed_seconds` | ✅ 概览页「处理耗时」 |
| `generator` | ✅ 概览页「报告来源（GUI/CLI）」 |
| `files[].target` | ✅ 文件明细表「目标」列 |
| `files[].program_name_source` | ✅ 文件明细表程序名旁来源标记 |

## 12. 示例 JSON

示例与生成端规范第 13 节完全一致（同一报告文件同时被主工具导出、被查看器解析），此处不重复粘贴；验收时以生成端示例为准，并用查看器打开验证各页签渲染。

## 13. 维护约定

- 查看器新增/调整展示字段时：同步更新本文档、生成端规范、`load_report`/`iter_stats_rows`/`report_summary` 等解析函数及测试（当前基线 9 项）。
- `kind` 枚举新增时：同步查看器问题表高亮与文件表着色规则。
- 运行日志 `event` 枚举新增时：同步查看器「运行日志」页签筛选/高亮、生成端规范第 9 节与 WP-C6 实现。
- 建议新增字段落地顺序：主工具生成 → 测试 → 查看器消费 → 回填两侧文档「已实现」标注。
