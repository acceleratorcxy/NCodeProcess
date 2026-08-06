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

> Batch 2 配置与 WP-10 启发式阈值自 WP-11 起**全部持久化**（注册表默认 / AppData / 用户主目录可选，切换清空残留，支持导出）；编制、审核、自定义刀具类型及 Batch 1 设置继续走注册表/设置文件。

## 四、新增校验问题类型

| 问题类型（kind） | 级别 | 触发条件 |
|---|---|---|
| `feed-range` | error | F 值低于 `feed_min` 或高于 `feed_max` |
| `spindle-range` | error | S 值低于 `spindle_min` 或高于 `spindle_max` |
| `aux-order` | error/warning | 辅助指令顺序违例（M03 规则为 error，其余 warning） |

## 五、测试与验证

- 测试基线：Batch 2 前 138 项 → 完成后 **170 项** → 2026-08-05 测试代码合并精简后 **159 项**（覆盖不变），全量通过（conda Python 3.8 / 3.8.19）。
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

1. ~~**仅本次运行生效**~~：Batch 2 配置与 WP-10 启发式阈值已由 WP-11 实现持久化（`REGISTRY_DEFAULTS` 扩展全部键，保存位置可选：注册表/AppData/用户主目录，切换清空残留，支持导出）。
2. **机床行程检查**：需求 7.3 的 X/Y/Z 行程校验未实施（用户决定），如后续需要可单独规划。
3. **辅助指令顺序规则粒度**：当前为全局规则集合，不区分机床。
4. **版本号**：`__version__` 仍 1.0.0，正式发版前建议按需提升并同步 `VERSION.txt`/`version_info.txt`。
5. **未处理项**：线程安全、子窗口适配、备份/只读预检等见 `docs/NCodeProcess-审查与待办.md`。

## 附：审查整改工作包摘要（2026-08-05/06，未独立发版）

按「NCodeProcess 审查整改实施计划」完成 P0–P3 全部工作包（版本号保持 1.0.0，待正式发版统一提升）：

- **WP-A1**（P0）：M04 反转禁止自动补写 M03，报 `spindle-direction` error；同段 M03+M04 互斥。
- **WP-A2**（P1）：多刀程序跳过自动换刀改写并警告（`auto-tool-change-skipped`），执行确认页列出。
- **WP-B1**（P1）：代码编辑器保存忽略 CRLF/LF 差异。
- **WP-B2**（P1）：PROGRAM 经「修改程序名」同步；NC MACHINE/CONTROL SYSTEM 永久保护；DATE 自动更新。
- **WP-B3**（P1）：M06 检查排除括号与分号注释。
- **WP-C1**（P2）：文件大小/数量上限可配置（默认不限制）；设置对话框两页框宽统一。
- **WP-C3**（P2）：CLI 补齐 GUI 全部持久化配置参数并加载偏好。
- **WP-C4**（P2）：扫描期间禁用应用操作，避免共享数据竞争。
- **WP-C5**（P2）：递归扫描跳过任意深度数据/归档目录；未知扩展名口径统一为完全忽略。
- **WP-C6**（P2）：运行日志双轨（磁盘滚动 + 报告内嵌 `runtime_log`/`log_path`）；查看器新增运行日志页签；报告第 12 节增强字段（`app_version`/`config_snapshot`/`user_confirmations`/`files[].target` 等）。
- **WP-C7**（P2）：`process_plan` 按动作拆分为私有执行器。
- **WP-C8**（P2）：删除「清除注册表」按钮；恢复默认自动切回注册表并清空另两处残留。
- **WP-C9**（P2）：抬刀高度阈值进 Config（默认 20），50 样例 0 误报。
- **WP-C2/D1/D2**（P2/P3）：死代码清理、程序名默认正则提为模块常量。
- **WP-D3/D4**（P3）：构建不可复现性文档化、文档一致性总修订。

测试基线：主工具 **245 项**、查看器 **10 项**（以 `python -m unittest discover -s tests -v` 输出为准）。完整记录见 `docs/NCodeProcess-程序理解与操作记录.md` 与 `docs/NCodeProcess-审查与待办.md`。

## 附：测试套件整改摘要（2026-08-06，未独立发版）

按「NCodeProcess 测试套件整改实施计划」完成 WP-T1/T2/T3/T4/T5/T7（WP-T6 大文件拆分登记待办）：

- **WP-T1**：抽取 `LayoutWidgetMixin`，消除 `SettingsDialogTests` 继承导致的 19 项重复执行。
- **WP-T2**：合并 11 组重叠用例（subTest 表驱动，断言不变）。
- **WP-T3**：tooltip 断言改轮询 pump，连续 10 次无 flake。
- **WP-T4**：删除注册表键集复制断言、版本资源断言动态推导、像素间距断言加容差。
- **WP-T5**：新增 CLI 全流程/CSV/程序名提取测试，消除覆盖空白。
- **WP-T7**：测试基线改为以 discover 输出为准；新增 `run_tests.ps1` 一键跑主/查看器两套测试。

全套耗时由约 51s 降至约 37s；仅改动测试与文档，未触碰生产代码。

## 附：性能与打包体积优化摘要（2026-08-06，未独立发版）

按「NCodeProcess 性能与打包体积优化实施计划」完成 WP-P1/P3/S1/S2/S3：

- **性能**：`build_plan`（25 MPF）2.072s → **1.450s（-30%）**；「全部应用」改为内存局部重处理、预览立即刷新（WP-P1/P3）。
- **体积**：互斥体名 FNV-1a 替代 md5（排除 hashlib/libcrypto，-1.9MB）；spec 补充 excludes；启用 UPX 3.96 压缩——主 EXE **8.95 → 6.02MB（-33%）**、查看器 **7.06 → 5.94MB**（WP-S1/S2/S3）。
- **安全**：Windows Defender 复测未检出威胁；360/火绒与 Windows 7 真机冒烟登记留待车间实测。
- **跳过（可选待办）**：WP-P2（管线重构）、WP-S4（tcl/tk 裁剪）、WP-S5（ZIP 再压缩），详见审查与待办。

## 附：第三轮收尾摘要（2026-08-06，未独立发版）

按「NCodeProcess 第三轮收尾实施计划」（`docs/superpowers/plans/2026-08-06-round2-followup-plan.md`）执行，决策点确认：D1=C（版本保持 1.0.0，尚未正式发布）、D2=A（仅收尾 WP-F1~F5）、D3=B、D4=B。

- **WP-F1**（已处理，提交后）：「全部应用」改为后台线程重处理（`reprocess_plans` 纯函数 + `_finish_apply_info` 主线程刷新 + 代际防护），25 MPF 点「全部应用」不再冻结界面；`apply_selected` 保持同步；主工具 245 项全绿，打包版用户实测通过。

后续 WP-F2（运行日志埋点与截断去重）、F3（查看器图表容错）、F4（分号语义文档化）、F5（构建脚本缩进清理）随本轮推进同步追加。

## 八、相关文档

- 实施计划（已归档，仅本地保留、git 不跟踪）：`docs/archive/superpowers/plans/2026-08-04-gui-config-settings-batch2-plan.md`
- 需求文档：`docs/NCodeProcess-需求文档.md`（第 8 节、7.2/7.3、FR-05.4/FR-05.7）
- 程序理解与操作记录：`docs/NCodeProcess-程序理解与操作记录.md`
- 审查与待办：`docs/NCodeProcess-审查与待办.md`
