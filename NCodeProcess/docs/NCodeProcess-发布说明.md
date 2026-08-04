# NCodeProcess 发布说明

> 本文件记录各版本发布内容；新版本在此追加，旧版本保留备查。

## 版本索引

| 版本 | 主题 | 日期 |
|---|---|---|
| 1.0.0（Batch 2） | 校验规则与补写策略配置 GUI 化 | 2026-08-05 |

---

# 1.0.0（Batch 2）—— 校验规则与补写策略

> 日期：2026-08-05（开发跨 2026-08-04 晚至 2026-08-05 凌晨）
> 范围：程序设置界面第 8 节配置项第二批（5 项校验/策略功能 + 1 项对话框布局重构）
> 关联计划：`docs/archive/superpowers/plans/2026-08-04-gui-config-settings-batch2-plan.md`（已完成）

## 一、发布概述

在 Batch 1（编码、待删除扩展名、允许字符、结束标记/M06/S 开关、APTSOURCE 子目录等 GUI 化）基础上，Batch 2 把需求文档第 8 节剩余的**校验规则与补写策略**全部接入 `Config` + 校验逻辑 + 「程序设置」对话框，让车间/机床相关的工艺规则从「人工盯屏」固化为「程序强制把关」：

| # | 功能 | 生产意义 |
|---|---|---|
| 1 | 必填 MSG 字段可配置 | MSG 头部是 DNC 传输、版本追溯、工序卡填写的元数据来源；可配置避免「不要求字段被强制填写而阻塞合法交付」 |
| 2 | M03 补写位置策略 | 按机床/后处理选「贴 S 后」或「独立行」，避免双主轴启动指令、避免首刀空转，减少试切风险 |
| 3 | F/S 上下限校验 | 把工艺包络固化到设置，扫描即 error 阻止输出，拦截 F 误输/S 超限导致的断刀与工件损坏 |
| 4 | 辅助指令顺序校验 | M03/M05/M08/M09 顺序错误属安全类问题，预览阶段拦截，避免实际加工时才暴露 |
| 5 | 换行强制策略 | 老旧控制器对 CRLF/LF 敏感；混合来源目录下强制统一换行，减少现场「打不开/乱码」返工 |
| 6 | 设置对话框两页重构 | 5 组新控件加入后对话框过高，改为「基本设置 / 校验规则」两个页签，恢复紧凑布局 |

> **范围外说明**：需求 7.3 的「机床行程 X/Y/Z 检查」按用户决定本次**不实施**。

## 二、新增/变更明细

### 1. 必填 MSG 字段可配置（提交 `e1af69f`）
- `Config.required_fields`（默认全键）；`validate_program` 与 `apply_header` 按配置判断；GUI 4 个可勾选项（编制/审核/图号/版次），程序/机床/控制系统固定必填。

### 2. M03 补写位置策略（提交 `28029c5`）
- `Config.m03_position`（`after-s` 默认 / `standalone`）；standalone 在第一条切削/运动指令（G1/G2/G3 或 X/Y/Z）前插入独立 M03 行，无运动指令时回退首条指令行前。

### 3. F/S 上下限校验（提交 `327da7c`）
- `Config.feed_min/max`、`spindle_min/max`（None 不检查）；越界报 `feed-range`/`spindle-range`（error）；GUI 4 个可留空输入框（负数拦截）。

### 4. 辅助指令顺序校验（提交 `75b6b74`）
- `Config.aux_checks`（core 默认空集；GUI 默认全开）；规则：`m03-before-motion`（error）、`m05-before-end`/`m08-before-cut`/`m09-before-end`（warning）；**仅当相关指令都出现且顺序错误时报告，M09 未出现不提示**。

### 5. 换行强制策略（提交 `01b9de4`）
- `Config.newline`（`auto` 默认 / `crlf` / `lf`）；`_effective_newline` 统一 `apply_header`/`add_initial_tool_change`/`add_m03` 三处换行选择，强制策略下输出归一。

### 6. 设置对话框两页布局重构（提交 `71756fb`）
- `ttk.Notebook` 两页：基本设置（编码/扩展名/允许字符/APTSOURCE/主程序扩展名/输出扩展名）与校验规则（结束标记/M06/S、G00 级别、必填字段、M03 策略、F/S 上下限、换行、辅助顺序）；对话框宽 ≤640、高 ≤500。

### 7. 界面布局优化（提交 `6224090`、`3146557`）
- 必填字段 4 勾选项等间距；G00 级别移入设置校验规则页（主窗口移除）；自定义刀具类型在程序信息区独立成行；F/S 上下限「输入框 ~ 输入框」改入独立子容器紧凑排列。

## 三、新增配置项一览（Config → GUI）

| Config 字段 | 默认值 | 界面位置 | 是否持久化 |
|---|---|---|---|
| `required_fields` | 全部 8 键 | 校验规则 → 必填 MSG 字段 | 仅本次运行 |
| `m03_position` | `after-s` | 校验规则 → M03 补写位置 | 仅本次运行 |
| `feed_min/feed_max` | `None` | 校验规则 → F 上下限 | 仅本次运行 |
| `spindle_min/spindle_max` | `None` | 校验规则 → S 上下限 | 仅本次运行 |
| `aux_checks` | 空集（GUI 默认 4 条全开） | 校验规则 → 辅助指令顺序 | 仅本次运行 |
| `newline` | `auto` | 校验规则 → 换行策略 | 仅本次运行 |

> Batch 2 配置按需求第 8 节「配置优先保存在内存中」设计为仅本次运行生效；编制、审核、自定义刀具类型及 Batch 1 的部分设置继续走注册表/设置文件。

## 四、新增校验问题类型

| 问题类型（kind） | 级别 | 触发条件 |
|---|---|---|
| `feed-range` | error | F 值低于 `feed_min` 或高于 `feed_max` |
| `spindle-range` | error | S 值低于 `spindle_min` 或高于 `spindle_max` |
| `aux-order` | error/warning | 辅助指令顺序违例（M03 规则为 error，其余 warning） |

## 五、测试与验证

- 测试基线：Batch 2 前 138 项 → 完成后 **170 项**，全量通过（conda python38 / Python 3.8.19）。
- 新增测试 32 项（Batch 2 29 + 布局 2 + 间距 1），全部遵循 TDD（先 RED 确认功能缺失，再实现转 GREEN）。
- 默认值兼容：`required_fields`（全键）、`m03_position`（after-s）、4 个上下限（None）、`newline`（auto）等价现行为，存量测试零回归；`aux_checks` core 层默认空集、GUI 层默认启用（用户确认）。
- 已知无害噪音：Tk teardown 偶发 `can't invoke "event" command` 提示；`test_cell_tooltip_hides_on_leave` 在全量负载下偶发失败、单独重跑即过（`when="tail"` 时序抖动）。

## 六、提交记录

| 提交 | 内容 |
|---|---|
| `e1af69f` | feat(core): 必填 MSG 字段可配置（required_fields），validate/apply_header 跟随 |
| `28029c5` | feat(core): M03 补写位置策略可配置（after-s/standalone），add_m03 按策略插值 |
| `327da7c` | feat(core): F/S 上下限校验（feed/spindle_min/max），validate_program 越界报 error |
| `75b6b74` | feat(core): 辅助指令顺序校验（aux_checks），M03/M05/M08/M09 顺序规则 |
| `01b9de4` | feat(core): 换行强制策略（auto/crlf/lf），输出按策略归一 |
| `71756fb` | refactor(gui): 程序设置对话框改为分组两页布局并锁定 Batch 2 控件 |
| `6224090` | refactor(gui): 必填字段等间距排版、G00 级别移入设置、自定义刀具类型独立成行 |
| `3146557` | refactor(gui): F/S 上下限输入框与~改为独立子容器紧凑排列 |

## 七、已知限制与后续建议

1. **仅本次运行生效**：Batch 2 配置不持久化；如需与 Batch 1 一致持久化，需扩展 `REGISTRY_DEFAULTS`（会同步调整 preferences 相关测试）。
2. **机床行程检查**：需求 7.3 的 X/Y/Z 行程校验未实施（用户决定），如后续需要可单独规划。
3. **辅助指令顺序规则粒度**：当前为全局规则集合，不区分机床。
4. **版本号**：`__version__` 仍 1.0.0，正式发版前建议按需提升并同步 `VERSION.txt`/`version_info.txt`。
5. **未处理项**：线程安全、子窗口适配、备份/只读预检等见 `docs/NCodeProcess-审查与待办.md`。

## 八、相关文档

- 实施计划（已归档，仅本地保留、git 不跟踪）：`docs/archive/superpowers/plans/2026-08-04-gui-config-settings-batch2-plan.md`
- 需求文档：`docs/NCodeProcess-需求文档.md`（第 8 节、7.2/7.3、FR-05.4/FR-05.7）
- 程序理解与操作记录：`docs/NCodeProcess-程序理解与操作记录.md`
- 审查与待办：`docs/NCodeProcess-审查与待办.md`
