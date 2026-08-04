# 程序设置界面 Batch 2（校验规则与补写策略）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 依据需求文档第 8 节「应在程序设置界面提供」、7.2/7.3 校验规则与 FR-05.7，将 Batch 1 未覆盖的配置项接入 `Config` + 校验逻辑 + 「程序设置」对话框。本计划覆盖 **5 项**：必填 MSG 字段、M03 补写位置策略、S/F 上下限、辅助指令顺序规则（**实施前需确认规则**）、换行强制策略。**机床行程 X/Y/Z 检查（FR-07.3）按用户决定不实施**，不在本计划范围。

**Architecture:** 与 Batch 1 相同——先在 `ncodeprocess/core.py` 扩展 `Config` 字段并新增/修改校验逻辑，再接入现有「程序设置…」`Toplevel` 对话框（Win7 ttk 兼容、不可缩放、仅内存生效、不写 NC 目录）。配置只影响本次运行；编制/审核/自定义刀具类型继续走注册表/设置文件，不受影响。

**Tech Stack:** Python 3.8 / Tkinter ttk / PyInstaller 5.13.2（仅打包验证用，本计划不含打包步骤）/ unittest（TDD）。测试一律使用 conda `python38`：`D:\anaconda3\envs\python38\python.exe`（详见 `docs/更改测试打包提交流程.md`）。

**关联文档：**
- 需求依据：`NCodeProcess-需求文档.md` 第 8 节、7.2/7.3、FR-05.4/FR-05.7
- 前置计划：`docs/superpowers/plans/2026-08-04-gui-config-settings-plan.md`（Batch 1，已完成，对话框与布局基线已锁定）
- 关键现状：`core.py` `Config`（L50-L75）、`FIELD_ORDER`（L18-L27）、`validate_program`（L851-L965）、`apply_header`（L635-L716）、`add_m03`（L766-L800）、`read_text`/`_atomic_write`（L269-L273/L1187-L1197）

---

## 一、为什么做（对实际生产任务的意义）

这批配置项的共性是：**把车间/机床相关的工艺规则从「人工盯屏」固化为「程序强制把关」**，在预览阶段拦截错误、在交付前统一格式，直接减少现场试切、撞机、断刀与返工：

| 项目 | 对实际生产任务的意义 |
|---|---|
| ① 必填 MSG 字段 | MSG 头部是 DNC 传输、版本追溯、工序卡填写的元数据来源。当前 8 个字段全部强制必填过于僵硬：车间不要求的字段（如 SHENHE/版次）会被强制填写而阻塞合法交付。可配置后按厂规收紧/放宽，既保证关键追溯信息（程序名/图号/版次/机床/控制系统）不丢，又不误伤本就不填的字段。 |
| ② M03 位置策略 | V5-2500B 与 HASS 后处理对主轴启动的处理不同：固定「贴 S」策略在部分机床会生成双主轴启动指令，或首刀无转速空转。按机床/后处理选「贴 S 后」或「独立行」，避免双启动、避免首刀空转，减少试切风险。 |
| ③ S/F 上下限 | F 误输（如 F3 想输 F3000）、S 超出主轴能力范围是断刀/工件损坏的常见诱因。把工艺包络固化到设置，扫描即 error 阻止输出，变「凭经验盯屏」为「机器强制把关」。 |
| ④ 辅助指令顺序（待确认） | M05 出现在程序结束之后、M08 迟于切削才开启等顺序错误属于安全类问题。规则确认后固化为校验，在预览阶段拦截，避免实际加工时才暴露。 |
| ⑤ 换行强制策略 | 部分老旧控制器对 CRLF/LF 敏感，换行不符会导致程序无法识别或乱码。混合来源目录下强制统一换行，减少现场「打不开/乱码」返工。 |

---

## 二、文件结构

- `NCodeProcess/ncodeprocess/core.py`：
  - `Config` 新增 6 组字段（见各 Task）。
  - 新增模块函数 `_effective_newline(text, config)`（Task E 用）。
  - 修改 `validate_program`（Task A/C/D）、`apply_header`（Task A）、`add_m03`（Task B）。
- `NCodeProcess/ncodeprocess/gui.py`：
  - `App._build` —— 新增配置 `tk.*Var`（9 个，见各 Task）。
  - `App.open_settings` —— 对话框新增 5 个分组；**布局重构**（Task F，640×420 限制将超）。
  - `App._confirm_settings` —— 新增输入校验（数值范围、必填字段合法性）。
  - `App.config` —— 注入全部新字段。
- `NCodeProcess/tests/test_core.py`：每 Task 新增锁定/失败测试。
- `NCodeProcess/tests/test_gui.py`：新增对话框项与布局回归测试；**更新 `test_settings_dialog_fits_1286_and_controls_visible` 的尺寸断言**（Task F）。

**新增 Config 字段一览（默认值保证存量行为不变，存量测试不回归）：**

```python
@dataclass
class Config:
    ...
    required_fields: List[str] = field(default_factory=lambda: [k for k, _r, _rq in FIELD_ORDER])  # 全部键
    m03_position: str = "after-s"            # after-s | standalone
    feed_min: Optional[float] = None         # None=不检查
    feed_max: Optional[float] = None
    spindle_min: Optional[float] = None
    spindle_max: Optional[float] = None
    aux_checks: set = field(default_factory=set)   # Task D，规则确认后定枚举
    newline: str = "auto"                    # auto | crlf | lf
```

---

## Task A: 必填 MSG 字段可配置

**需求依据：** 7.2「检查必填 MSG 字段是否存在且非空」；第 8 节「必填 MSG 字段」；字段表 L146-154（BIANZHI/SHENHE/DRAWING NUMBER/PART VERSION 可配置默认必填；PROGRAM/NC MACHINE/CONTROL SYSTEM 固定必填）。

**现状：** `FIELD_ORDER` 全部 `required=True` 硬编码，`validate_program` L861-863 与 `apply_header` L684-689 均直接使用。

**改动：**
- `Config.required_fields: List[str]`（默认全键）。
- `validate_program`：`for key, _label, _required in FIELD_ORDER: if key in config.required_fields and (missing or empty): ...`
- `apply_header`：`if value or (key in config.required_fields):` 插入缺失字段。
- GUI：`App._build` 新增 4 个 `BooleanVar`（`required_bianzhi/shenhe/drawing/part`，默认 True）+ 设置对话框「必填字段」分组；PROGRAM/NC MACHINE/CONTROL SYSTEM 固定必填（不可取消勾选，界面以灰色说明）。

**TDD 步骤：**
- [x] **Step 1: 写失败/锁定测试（test_core.py）**
  - `test_required_fields_can_omit_shhenhe`：`Config(required_fields=除 SHENHE 外全部)`，正文无 SHENHE 头部 → 无 `required-field` issue；反之默认配置 → 有。
  - `test_required_fields_drive_header_insertion`：非必填且值为空 → `apply_header` 不插入空 `MSG("SHENHE:")` 行。
- [x] **Step 2: 运行确认失败**（conda python38）
- [x] **Step 3: 实现 core 改动**
- [x] **Step 4: 全量 core 测试通过**
- [x] **Step 5: GUI 测试（test_gui.py）**
  - `test_required_fields_vars_exist_and_default_to_all`：4 个 var 默认 True，`config().required_fields` 含全部键。
  - `test_settings_dialog_toggles_required_field`：取消 SHENHE → 确认后 `config().required_fields` 不含 `"SHENHE"`。
- [x] **Step 6: 提交**：`git commit -m "feat(core): 必填 MSG 字段可配置（required_fields），validate/apply_header 跟随"`（56ca278）

---

## Task B: M03 补写位置策略

**需求依据：** FR-05.4（默认贴 S 后/分号前；无 S 时独立行）；FR-05.7「策略、插入位置可配置」；需求 L474 开放问题「无 S 时是否允许独立行」。

**现状：** `add_m03` 固定「先找 S 附加 M03；无 S 才插独立行」。

**改动：**
- `Config.m03_position: str = "after-s"`。
- `add_m03`：
  - `after-s`：现行为（贴首个有效 S 后、分号前；无 S 时首条运动前插独立行）。
  - `standalone`：无论有无 S，一律在第一条切削/运动指令前插独立 `M03` 行（复用现有分号风格判断 `any(l.rstrip().endswith(";")...)`）。
- GUI：`m03_position_var` Combobox（`紧贴 S 数值后` / `独立行`）。

**TDD 步骤：**
- [x] **Step 1: 写失败测试（test_core.py）**
  - `test_standalone_m03_position_inserts_independent_row_with_s_present`：有 S 时 `m03_position="standalone"` → 输出含独立 `M03` 行、S 行未附加 M03。
  - `test_after_s_position_is_default_and_attaches`：默认 `after-s` 保持现行为（锁定）。
- [x] **Step 2: 运行确认失败**
- [x] **Step 3: 实现 core 改动**（`_insert_standalone_m03`：第一条 G1/G2/G3 或 X/Y/Z 前插独立行，无运动指令回退第一条指令行）
- [x] **Step 4: core 全绿**
- [x] **Step 5: GUI 测试**：`test_m03_position_var_roundtrip`：设 `standalone` → `config().m03_position == "standalone"`。
- [x] **Step 6: 提交**：`git commit -m "feat(core): M03 补写位置策略可配置（after-s/standalone），add_m03 按策略插值"`（d3ac9ca）

---

## Task C: S/F 上下限

**需求依据：** 7.3「检查 F、S 是否为负数或超出用户配置的上下限」；第 8 节「S/F 范围…校验规则」。

**现状：** `validate_program` 已收集 `feed_values`/`spindle_values`（L913-918），已有 feed-zero（error）与 negative-parameter（error）。

**改动：**
- `Config.feed_min/feed_max/spindle_min/spindle_max: Optional[float] = None`。
- `validate_program`：在收集循环内对 F/S 追加越界检查，产出新 kind `feed-range` / `spindle-range`，severity=`error`，suggestion 指明越界值（如 `F 值 3 低于下限 100`）。
- GUI：4 个可留空 `Entry`（空=不检查）；`_confirm_settings` 校验：空或可 `float()` 且 ≥0，否则 `showerror`。

**TDD 步骤：**
- [x] **Step 1: 写失败测试（test_core.py）**
  - `test_feed_below_min_is_error`：`Config(feed_min=100)`，正文 `F3` → 含 `feed-range` error。
  - `test_feed_above_max_is_error`：`Config(feed_max=20000)`，`F30000` → error。
  - `test_spindle_limits_check_both_ends`：`spindle_min/spindle_max` 双向。
  - `test_limits_none_do_not_report`：全部 None → 无 `feed-range`/`spindle-range`（锁定）。
- [x] **Step 2: 运行确认失败**
- [x] **Step 3: 实现 core 改动**（validate_program 对 F/S 追加 feed-range/spindle-range error）
- [x] **Step 4: core 全绿**
- [x] **Step 5: GUI 测试（test_gui.py）**
  - `test_feed_limits_vars_roundtrip`：输入 `100`/空 → `config().feed_min == 100`、`feed_max is None`。
  - `test_settings_dialog_rejects_non_numeric_limits`：`feed_min_var="abc"` → `_confirm_settings` 弹错且对话框未关。
  - 布局：对话框高度断言放宽至 ≤520（Task F 重构后收紧）。
- [x] **Step 6: 提交**：`git commit -m "feat(core): F/S 上下限校验（feed/spindle_min/max），validate_program 越界报 error"`（f7c6481）

---

## Task D: 辅助指令顺序规则 —— 前置需确认规则

**需求依据：** 7.3「检查 M03、M05、M08 等辅助指令的顺序是否符合配置规则」；**需求 L478 为开放问题（规则未定义），必须先与需求方确认**。

**设计草案（确认后据此实现）：** `Config.aux_checks: set`，枚举候选：

```python
# 候选规则（实施前与用户确认取舍与级别）：
"m03-before-motion"   # M03 必须在切削运动 G01/G02/G03 之前      → error
"m05-before-end"      # M05 必须在 M02/M30 之前                  → warning
"m08-before-cut"      # M08（冷却液开）必须在首次切削之前         → warning
"m09-before-end"      # M09 必须在程序结束之前                   → warning
```

`validate_program` 增加顺序状态机：遍历正文记录指令首次/最后出现位置，校验后产出 `aux-order` 问题（含行号与建议）。GUI 按确认后的规则集做 Checkbutton 组。

**已确认规则（用户确认 2026-08-04）：** 4 条全部启用；级别按混合（m03-before-motion→error，m05/m08/m09→warning）；**仅当相关指令都出现且顺序错误时报告，M09 未出现时不提示 m09-before-end。**

**TDD 步骤：**
- [x] **Step 0（阻塞项，已完成）: 与用户确认规则集** —— 4 条规则全部启用、混合级别、M09 未出现不提示。
- [x] **Step 1: 写失败测试（test_core.py）**
  - `test_aux_m03_after_first_cut_is_error`、`test_aux_m03_before_first_cut_no_issue`
  - `test_aux_m05_after_end_is_warning`、`test_aux_m08_after_first_cut_is_warning`
  - `test_aux_m09_absent_produces_no_warning`、`test_aux_m09_after_end_is_warning`
  - `test_aux_checks_empty_disables_all`（锁定）
- [x] **Step 2–6: TDD 流程完成**（提交 0693a51；对话框高度断言放宽至 ≤620，Task F 收紧）

> **注意：** 若规则短期无法确认，本 Task 可单独延后，不影响 Task A/B/C/E。

---

## Task E: 换行强制策略

**需求依据：** L370「支持 CRLF、LF 换行符」；第 8 节「换行符策略（自动识别/强制指定）」。

**现状：** `read_text` 自动探测；`apply_header`/`add_m03`/`add_initial_tool_change` 三处各自 `"\r\n" if "\r\n" in text else "\n"`；`_atomic_write` 原样写。

**改动：**
- `Config.newline: str = "auto"`。
- 新增 `_effective_newline(text, config) -> str`：`auto` → 探测；`crlf` → `"\r\n"`；`lf` → `"\n"`。三处拼接点统一调用。
- 输出归一：`process_plan` 写 MPF 前（或 `_atomic_write` 前）将 `output_text` 的换行统一为目标风格：`text.replace("\r\n", "\n").replace("\r", "\n")` 后按策略 join。保证「强制策略下即使源文件混合换行，产物也一致」。
- GUI：`newline_var` Combobox（`自动识别`/`CRLF`/`LF`）。

**TDD 步骤：**
- [x] **Step 1: 写失败测试（test_core.py）**
  - `test_newline_force_lf_converts_crlf_source`：CRLF 源 + `newline="lf"` → `process_plan`/`_atomic_write` 后产物为 LF。
  - `test_newline_force_crlf_converts_lf_source`：LF 源 + `crlf` → CRLF。
  - `test_newline_auto_preserves_source_style`（锁定现行为）。
- [x] **Step 2: 运行确认失败**
- [x] **Step 3: 实现 core 改动（`_effective_newline` helper + 三处调用点）**
- [x] **Step 4: core 全绿**
- [x] **Step 5: GUI 测试**：`test_newline_var_roundtrip`：设 `lf` → `config().newline == "lf"`。对话框高度断言放宽至 ≤560（Task F 重构后收紧）。
- [x] **Step 6: 提交：** `git commit -m "feat(core): 换行强制策略（auto/crlf/lf），输出按策略归一"`（6cea51e）

---

## Task F: 设置对话框布局重构与回归锁定

**现状：** 对话框约 10 行，`test_settings_dialog_fits_1286_and_controls_visible` 锁定 `reqwidth ≤ 640`、`reqheight ≤ 420`。Batch 2 新增 5 个分组（必填字段、M03 策略、S/F 上下限、辅助指令顺序、换行）后必然超限。

**方案：** 对话框改为「分组 LabelFrame + 两列网格」，内容超高时允许纵向滚动（`Canvas + Frame` 滚动或 `ttk.Notebook` 两个页签：`基本设置` / `校验规则`）。**推荐 Notebook 两页**（Win7 ttk 原生、无需自绘滚动条，与主窗口风格一致）：

```
程序设置
├─ 基本设置（Batch 1 现有项 + 编码/扩展名/允许字符/APTSOURCE）
└─ 校验规则（必填字段、M03 策略、S/F 上下限、辅助指令顺序、换行、G00/结束标记/M06/S 开关）
```

**TDD 步骤：**
- [x] **Step 1: 写失败测试（test_gui.py）**
  - 更新 `test_settings_dialog_fits_1286_and_controls_visible`：两页重构后高度断言收紧为 ≤480。
  - 新增 `test_settings_dialog_pages_switch_and_controls_visible`：两页页签「基本设置/校验规则」切换互斥显示。
  - 新增 `test_batch2_controls_exist`：Batch 2 控件存在且默认值正确。
- [x] **Step 2: 运行确认失败**（旧单页布局 587 高度、无 settings_notebook）
- [x] **Step 3: 实现布局重构（open_settings 内 Notebook 两页 + settings_notebook/settings_pages）**
- [x] **Step 4: 全量回归（167 项通过；`test_cell_tooltip_hides_on_leave` 曾偶发失败，单独重跑通过，属 Tk `when="tail"` 时序抖动，与本次改动无关）**
- [x] **Step 5: 提交：** `git commit -m "refactor(gui): 程序设置对话框改为分组两页布局并锁定 Batch 2 控件"`（215e04c）

---

## 三、回归与验证

每完成一个 Task 后执行（使用 conda python38）：

```powershell
D:\anaconda3\envs\python38\python.exe -m unittest discover -s tests -v
```

- 当前基线 138 项；本计划预计新增约 20 项。
- 存量行为保护：Task A–E 的新 Config 字段默认值均等价于现行为，**Batch 1 的 80 余项 GUI 测试与全部 core 测试不得回归**；Task F 仅允许放宽对话框尺寸断言（改动前先跑 RED 确认新需求尺寸）。
- 已知无害噪音：Tk teardown 时 `can't invoke "event" command` 提示，对应测试仍判 `ok`。

## 四、自检（Self-Review）

- **需求覆盖**：第 8 节「必填 MSG 字段 / M03 补写策略 / S/F 范围 / 换行符策略」全部 GUI 化；7.3「辅助指令顺序」按确认后规则接入；**机床行程按用户决定不实施（已排除）**。G00/结束标记/M06/S 开关为 Batch 1 已完成项，不重复。
- **默认值兼容**：`required_fields`（全键）、`m03_position`（after-s）、`newline`（auto）、各 min/max（None）、`aux_checks`（空集）——全部等价现行为，存量测试不回归。
- **类型一致性**：新增 Var 命名（`required_bianzhi/shenhe/drawing/part`、`m03_position_var`、`feed_min_var` 等）与 `App.config` 注入字段一一对应；`Config` 字段名在 Task 内全局一致。
- **阻塞项标注**：Task D 明确标注 Step 0 需规则确认，未确认前不实施；其余 4 项相互独立、可并行/按序推进。
- **回归风险**：`validate_program`/`apply_header`/`add_m03` 均以「默认值分支不改变现路径」方式改造；Task F 布局重构是唯一触碰既有对话框的改动，用全量对话框测试锁定。
