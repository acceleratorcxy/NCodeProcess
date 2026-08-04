# Release Note —— NCodeProcess 程序设置 Batch 2（校验规则与补写策略）

> 日期：2026-08-04
> 当前版本：`__version__` = 1.0.0（本次未升级版本号，建议正式发版前按需提升）
> 范围：程序设置界面第 8 节配置项第二批（5 项校验/策略功能 + 1 项对话框布局重构）
> 关联计划：`docs/superpowers/plans/2026-08-04-gui-config-settings-batch2-plan.md`

---

## 一、本次发布概述

在 Batch 1（编码、待删除扩展名、允许字符、结束标记/M06/S 开关、APTSOURCE 子目录等 GUI 化）基础上，Batch 2 把需求文档第 8 节剩余的**校验规则与补写策略**全部接入 `Config` + 校验逻辑 + 「程序设置」对话框，让车间/机床相关的工艺规则从「人工盯屏」固化为「程序强制把关」：

| # | 功能 | 生产意义 |
|---|---|---|
| 1 | 必填 MSG 字段可配置 | MSG 头部是 DNC 传输、版本追溯、工序卡填写的元数据来源；可配置避免「不要求字段被强制填写而阻塞合法交付」 |
| 2 | M03 补写位置策略 | 按机床/后处理选「贴 S 后」或「独立行」，避免双主轴启动指令、避免首刀空转，减少试切风险 |
| 3 | F/S 上下限校验 | 把工艺包络固化到设置，扫描即 error 阻止输出，拦截 F 误输/S 超限导致的断刀与工件损坏 |
| 4 | 辅助指令顺序校验 | M03/M05/M08/M09 顺序错误属安全类问题，预览阶段拦截，避免实际加工时才暴露 |
| 5 | 换行强制策略 | 老旧控制器对 CRLF/LF 敏感；混合来源目录下强制统一换行，减少现场「打不开/乱码」返工 |
| 6 | 设置对话框两页重构 | 5 组新控件加入后对话框过高，改为「基本设置 / 校验规则」两个页签，恢复紧凑布局 |

> **范围外说明**：需求 7.3 的「机床行程 X/Y/Z 检查」按用户决定本次**不实施**，不在本版本范围内。

---

## 二、新增/变更明细

### 1. 必填 MSG 字段可配置（提交 `56ca278`）

- **Config 新增**：`required_fields: List[str]`，默认 = 全部 8 个头部键（等价原行为）。
- **core 变更**：
  - `validate_program`：必填检查改为按 `config.required_fields` 判断。
  - `apply_header`：字段移出必填列表且值为空时，不再插入空 `MSG("SHENHE:")` 行。
- **GUI**：设置对话框「校验规则」页新增 4 个可勾选项（编制/审核/图号/版次）；程序/机床/控制系统固定必填、不可取消。
- **生产意义**：车间可按自身管理要求收紧/放宽必填追溯字段，既保证关键信息不丢，又不误伤按厂规本就不填的字段。

### 2. M03 补写位置策略（提交 `d3ac9ca`）

- **Config 新增**：`m03_position: "after-s" | "standalone"`，默认 `after-s`（等价原行为）。
- **core 变更**：`add_m03` 增加 standalone 分支，新增 `_insert_standalone_m03`：
  - `after-s`：紧贴首个有效 S 数值后（分号前）；无 S 时在首条指令行前插独立行。
  - `standalone`：无论有无 S，一律在第一条**切削/运动指令**（G1/G2/G3 或 X/Y/Z）前插入独立 `M03` 行；无运动指令时回退首条指令行前。
- **GUI**：设置对话框「校验规则」页新增「M03 补写位置」下拉框（after-s / standalone）。
- **生产意义**：V5-2500B 与 HASS 后处理对主轴启动处理不同，固定策略在部分机床会生成双启动指令；可配置后按机床选择，避免双启动与首刀空转。

### 3. F/S 上下限校验（提交 `f7c6481`）

- **Config 新增**：`feed_min/feed_max/spindle_min/spindle_max: Optional[float]`，默认 `None`（不检查）。
- **core 变更**：`validate_program` 对正文 F/S 值追加越界检查，产出新问题类型：
  - `feed-range` / `spindle-range`，级别 **error**（阻止输出），suggestion 指明越界值。
- **GUI**：设置对话框「校验规则」页新增 F/S 上下限 4 个输入框（留空 = 不检查）；非法或负数输入在确认时拦截。
- **生产意义**：F 误输（如 F3 想输 F3000）、S 超出主轴能力范围是断刀/工件损坏的常见诱因；配置后扫描即拦截，变「凭经验盯屏」为「机器强制把关」。
- **与既有检查的关系**：`feed-zero`（error）、`negative-parameter`（error）、`feed-outlier`（warning 启发式）继续保留，各司其职。

### 4. 辅助指令顺序校验（提交 `0693a51`）

- **Config 新增**：`aux_checks: set`，默认空集（core 层不改变行为）；规则枚举：
  | 规则 | 含义 | 级别 |
  |---|---|---|
  | `m03-before-motion` | M03 先于首次切削运动 | **error** |
  | `m05-before-end` | M05 先于程序结束 | warning |
  | `m08-before-cut` | M08 先于首次切削 | warning |
  | `m09-before-end` | M09 先于程序结束 | warning |
- **core 变更**：`validate_program` 跟踪首次切削（G1/G2/G3 或 X/Y/Z，排除 G0 快速定位）、M03/M05/M08/M09 与结束指令位置，按规则产出 `aux-order` 问题。
- **确认的语义（用户确认）**：**仅当相关指令都出现且顺序错误时报告**；例如 M09 未出现时不提示 m09-before-end，出现且晚于结束指令时才提示。
- **GUI**：设置对话框「校验规则」页新增「辅助指令顺序」勾选组（4 条规则，**默认全部启用**）。
- **生产意义**：M05 出现在程序结束之后、M08 迟于首次切削才开启等顺序错误属安全类问题，预览阶段即可拦截。

### 5. 换行强制策略（提交 `6cea51e`）

- **Config 新增**：`newline: "auto" | "crlf" | "lf"`，默认 `auto`（跟随源文件，等价原行为）。
- **core 变更**：新增 `_effective_newline(text, config)`，统一 `apply_header` / `add_initial_tool_change` / `add_m03` 三处换行选择；强制 crlf/lf 时输出换行归一，混合换行源文件产物一致。
- **GUI**：设置对话框「校验规则」页新增「换行策略」下拉框（auto / crlf / lf）。
- **生产意义**：部分老旧控制器对换行符敏感，换行不符导致程序无法识别或乱码；强制统一后减少现场返工。

### 6. 设置对话框两页布局重构（提交 `215e04c`）

- **重构**：`open_settings` 由单页长对话框改为 `ttk.Notebook` 两页：
  - **基本设置**：文件编码、待删除扩展名、程序名允许字符、APTSOURCE 归档子目录、主程序扩展名、输出扩展名。
  - **校验规则**：结束标记/M06/S 开关、必填 MSG 字段、M03 补写位置、F/S 上下限、换行策略、辅助指令顺序。
- **兼容**：全部既有对话框行为（确认应用/取消丢弃/非法拦截/恢复默认/清除注册表/回车与 Esc 快捷键）保持不变。
- **布局回归**：对话框请求尺寸从 ~590 高度收回至 **宽 ≤640 × 高 ≤480**（Win7 1366×768 下完整显示）。

---

## 三、新增配置项一览（Config → GUI）

| Config 字段 | 默认值 | 界面位置 | 是否持久化 |
|---|---|---|---|
| `required_fields` | 全部 8 键 | 校验规则 → 必填 MSG 字段（4 项可勾选） | 仅本次运行 |
| `m03_position` | `after-s` | 校验规则 → M03 补写位置 | 仅本次运行 |
| `feed_min/feed_max` | `None` | 校验规则 → F 上下限 | 仅本次运行 |
| `spindle_min/spindle_max` | `None` | 校验规则 → S 上下限 | 仅本次运行 |
| `aux_checks` | 空集（GUI 默认 4 条全开） | 校验规则 → 辅助指令顺序 | 仅本次运行 |
| `newline` | `auto` | 校验规则 → 换行策略 | 仅本次运行 |

> 说明：Batch 2 配置按需求第 8 节「配置优先保存在内存中」设计为**仅本次运行生效**，不写入注册表/设置文件；编制、审核、自定义刀具类型及 Batch 1 的部分设置继续走注册表/设置文件。

---

## 四、新增校验问题类型

| 问题类型（kind） | 级别 | 触发条件 |
|---|---|---|
| `feed-range` | error | F 值低于 `feed_min` 或高于 `feed_max` |
| `spindle-range` | error | S 值低于 `spindle_min` 或高于 `spindle_max` |
| `aux-order` | error/warning | 辅助指令顺序违例（M03 规则为 error，其余 warning） |

---

## 五、测试与验证

- 测试基线：Batch 2 前 **138 项** → 完成后 **167 项**，全量通过（conda python38 / Python 3.8.19）。
- 新增测试 **29 项**：Task A 4、B 4、C 6、D 9、E 4、F 2，全部遵循 TDD（先 RED 确认功能缺失，再实现转 GREEN）。
- 默认值兼容：`required_fields`（全键）、`m03_position`（after-s）、4 个上下限（None）、`newline`（auto）等价现行为，存量测试零回归；`aux_checks` core 层默认空集，GUI 层默认启用 4 条规则（用户确认）。
- 已知无害噪音：Tk teardown 偶发 `can't invoke "event" command` 提示（对应测试判 ok）；`test_cell_tooltip_hides_on_leave` 在全量负载下偶发失败 1 次，单独重跑通过，属 `when="tail"` 时序抖动，与本次改动无关。

---

## 六、提交记录

| 提交 | 内容 |
|---|---|
| `56ca278` | feat(core): 必填 MSG 字段可配置（required_fields），validate/apply_header 跟随 |
| `d3ac9ca` | feat(core): M03 补写位置策略可配置（after-s/standalone），add_m03 按策略插值 |
| `f7c6481` | feat(core): F/S 上下限校验（feed/spindle_min/max），validate_program 越界报 error |
| `0693a51` | feat(core): 辅助指令顺序校验（aux_checks），M03/M05/M08/M09 顺序规则 |
| `6cea51e` | feat(core): 换行强制策略（auto/crlf/lf），输出按策略归一 |
| `215e04c` | refactor(gui): 程序设置对话框改为分组两页布局并锁定 Batch 2 控件 |

本地领先 `origin/master` 6 个提交，尚未推送。

---

## 七、已知限制与后续建议

1. **仅本次运行生效**：Batch 2 配置不持久化，重启后回到默认值。如希望与 Batch 1 一致持久化到注册表/设置文件，需扩展 `REGISTRY_DEFAULTS`（会同步调整 preferences 相关测试），可作为后续任务。
2. **机床行程检查**：需求 7.3 的 X/Y/Z 行程校验未实施（用户决定），如后续需要可单独规划（`Config.machine_limits` + 越程校验 + 未配置时 info 提示）。
3. **辅助指令顺序规则粒度**：当前为全局规则集合，不区分机床；如不同机床规则不同，可扩展为按机床配置（后续可议）。
4. **版本号**：本次未升级 `__version__`（仍 1.0.0），正式发版前建议按需提升并同步 `VERSION.txt` / `version_info.txt`。

---

## 八、相关文档

- 实施计划：`docs/superpowers/plans/2026-08-04-gui-config-settings-batch2-plan.md`
- Batch 1 计划：`docs/superpowers/plans/2026-08-04-gui-config-settings-plan.md`
- 需求文档：`NCodeProcess-需求文档.md`（第 8 节、7.2/7.3、FR-05.4/FR-05.7）
- 流程文档：`docs/更改测试打包提交流程.md`
