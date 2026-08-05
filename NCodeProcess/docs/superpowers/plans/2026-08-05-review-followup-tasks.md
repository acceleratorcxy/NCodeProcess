# NCodeProcess 审查整改实施计划（2026-08-05 审查后）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按 2026-08-05 双视角审查结论，分阶段消除 P0（M04/自动 M03 冲突）至 P3 的已知问题，保持 Python 3.8 / Tkinter / stdlib、Windows 7 兼容与 239+6 测试基线全绿。

**Architecture:** 保持现有分层（`core.py` 纯逻辑 + `gui.py` Tkinter + `preferences.py` 双后端存储 + `cli.py` + `viewer.py`）。P0/P1 改动集中在 `core.py` 的 M03/换刀/校验逻辑与 `gui.py` 编辑器，纯函数先测后改；P2 为配置补齐与工程优化；P3 为清理与文档同步。默认参数/行为不变，存量测试零回归。

**Tech Stack:** Python 3.8 / Tkinter ttk / unittest（TDD，无第三方运行时依赖）/ PyInstaller 5.13.2（仅打包用）。

**执行纪律（AGENTS.md 强制，覆盖本计划所有 WP）：**
- 每个 WP 启动前必须向用户说明范围、涉及文件、验收标准并取得明确确认。
- 每个 WP 完成后：全量测试绿 → `build_portable.ps1` 打包 EXE → 用户实测确认 → 才允许 git 提交（已确认改动可合并提交）。
- 提交信息使用 `fix:`/`feat:`/`refactor:`/`docs:`/`test:` 前缀 + 中文描述；不提交构建产物、样例数据、`测试/` 目录。
- 每个代码/UI/配置改动同步更新受影响的 `docs/` 文件（需求、发布说明、测试基线、操作记录、README、审查与待办）。
- 工作区当前含 11 个未提交文件（Batch 2 / WP-12 收尾），本计划以工作区现状为基线，不重复处理已修复项。

---

## 任务总览

| WP | 主题 | 优先级 | 依赖决策 | 主要文件 | 验收标准 |
|---|---|---|---|---|---|
| WP-A1 | M04 与自动补写 M03 冲突 | P0 | 无 | `core.py`、`tests/test_core.py` | 含 M04 程序不被补写 M03，且报 `spindle-direction` error |
| WP-A2 | 多刀程序自动换刀防护 | P1 | D4 | `core.py`、`tests/test_core.py` | 多把刀具/多 T 引用时跳过改写并在预览与报告可见 |
| WP-B1 | 代码编辑器换行保护 | P1 | 无 | `gui.py`、`tests/test_gui.py` | 仅行尾差异的编辑不触发重新处理，不改变换行风格 |
| WP-B2 | PROGRAM/NC MACHINE 覆盖口径 | P1 | D1 | `core.py` 或需求文档 | 代码与需求/帮助文案一致，测试锁定 |
| WP-B3 | M06 注释误判修复 | P1 | 无 | `core.py`、`tests/test_core.py` | 注释中的 M06 不满足 `require_m06` |
| WP-C1 | 最大文件大小/数量配置（9.4 补齐） | P2 | D5 | `core.py`、`gui.py`、`preferences.py`、需求文档 | 超限文件跳过并提示；配置持久化 |
| WP-C2 | 死代码清理 | P2 | 无 | `core.py`、`gui.py`、`viewer.py` | 删除 `PROGRAM_RE`、未用导入、`defer_stats` 等，测试全绿 |
| WP-C3 | CLI/GUI 配置面统一 | P2 | D7 | `cli.py`、`tests/`（新增 CLI 测试） | CLI 覆盖 GUI 全部持久化配置或文档明确定位 |
| WP-C4 | 扫描与应用并发互斥 | P2 | 无 | `gui.py`、`tests/test_gui.py` | 扫描运行中禁用应用按钮，避免共享 FilePlan 竞争 |
| WP-C5 | 嵌套忽略目录 + 未知扩展名口径 | P2 | D2 | `core.py`、需求文档 | 任意深度 `aptsource`/`NCodeProcessData` 均跳过；文档口径统一 |
| WP-C6 | 运行日志落盘 + 报告内嵌 | P2 | D6 | `core.py`、`gui.py`、`viewer.py` | 报告含 `runtime_log` 与 `log_path`；异常写入磁盘日志；查看器新增运行日志页签 |
| WP-C7 | `process_plan` 拆分重构 | P2 | 无 | `core.py` | 行为不变，239 项测试全绿 |
| WP-C8 | 清除设置后后端显示一致 | P2 | 无 | `gui.py`、`tests/test_gui.py` | 清除后 `storage_backend_var` 重置为实际默认 |
| WP-C9 | 抬刀阈值进 Config | P2 | 无 | `core.py`、`gui.py`、`preferences.py` | `retract_z_threshold` 可配置并持久化 |
| WP-D1 | 查看器清理 | P3 | 无 | `viewer.py` | 删除未用导入与死分支，6 项测试全绿 |
| WP-D2 | 常量与微优化 | P3 | 无 | `core.py` | 默认正则改用模块常量 |
| WP-D3 | 构建不可复现性文档化 | P3 | 无 | 根/项目 README、SECURITY.md | 说明 `-OO`/随机密钥/随机 hash seed 为防破解设计 |
| WP-D4 | 文档一致性总修订 | 贯穿 | D1/D2 | 需求、审查与待办、手册、发布说明 | 9.4 状态、5.2/13.3、4.2.6 与代码一致 |

---

## 决策点（Phase 0，执行前须确认）

| 编号 | 决策 | 选项 | 建议 |
|---|---|---|---|
| D1 | 已有非空 PROGRAM/NC MACHINE 是否允许 `overwrite_fields` 覆盖 | A：维持永久保护，修订需求文档 4.2.6/4.2.7 并依赖现有「修改程序名」同步 PROGRAM（推荐）；B：允许覆盖，改 `apply_header` | A |
| D2 | 未知扩展名处理口径 | A：维持完全忽略，删除需求文档 13.3「列入待确认」表述（推荐）；B：新增「待确认」清单展示 | A |
| D3 | M04 处理策略 | A：禁止自动补写并报 `spindle-direction` error（推荐）；B：仅 warning | A |
| D4 | 多刀程序自动换刀策略 | A：刀具列表 >1 或多 T 引用时跳过改写并提示（推荐）；B：维持现状仅文档警告 | A |
| D5 | `max_file_size`/`max_files` 默认值 | A：默认不限制（0），GUI 可配置（推荐）；B：默认 50MB/500 文件 | A |
| D6 | 运行日志的范围 | A：双轨——磁盘滚动日志 + 报告内嵌 `runtime_log`/`log_path`，查看器新增运行日志页签（推荐）；B：仅磁盘日志不进报告 | A |
| D7 | CLI 定位 | A：补齐 GUI 全部持久化配置参数并加载偏好（推荐）；B：文档声明 CLI 为最小可用，仅补缺失说明 | A |

---

## 文件结构总览

- `ncodeprocess/core.py`：M04 检测与互斥、换刀防护、M06 口径、文件上限、抬刀阈值、死代码清理、`process_plan` 拆分、常量、运行事件 hook（WP-C6）。
- `ncodeprocess/gui.py`：编辑器换行保护、设置页新增项、扫描/应用互斥、清除设置重置、日志初始化。
- `ncodeprocess/preferences.py`：新增持久化键（max_file_size/max_files/retract_z_threshold）。
- `ncodeprocess/cli.py`：按 D7 补齐参数或文档定位。
- `ncodeprocessreportviewer/viewer.py`：未用导入与死分支清理。
- `tests/test_core.py`、`tests/test_gui.py`：新增/调整测试（WP 各自内嵌）。
- `docs/`：每个 WP 同步（WP-D4 总修订）。

---

## 阶段 A：机械安全（P0/P1）

### WP-A1: M04 与自动补写 M03 冲突

**Files:**
- Modify: `ncodeprocess/core.py:918`（`add_m03`）、`ncodeprocess/core.py:1028`（`validate_program`）、顶部常量区
- Test: `tests/test_core.py`（`CoreTests`）

**范围：** 正文存在 M04 时禁止自动补写 M03（D3 默认 A）；同块 M03+M04 上报互斥错误。

- [ ] **Step 1: 写失败测试（tests/test_core.py，CoreTests 类内新增）**

```python
    def test_m04_blocks_auto_m03_and_reports_direction_error(self):
        cfg = self._cfg(auto_m03=True, m03_position="after-s")
        text = "N10 S5000 M04;\nN20 X10 Y10 F500;\n"
        out, changed, note = add_m03(text, cfg)
        self.assertFalse(changed)
        self.assertNotIn("M03", out)
        issues = validate_program(out, "t.MPF", "T", DEFAULT_INFO, cfg)
        direction = [i for i in issues if i.kind == "spindle-direction"]
        self.assertTrue(direction)
        self.assertEqual(direction[0].severity, "error")

    def test_m03_and_m04_same_block_is_mutually_exclusive(self):
        cfg = self._cfg(auto_m03=False)
        issues = validate_program("N10 S5000 M03 M04;\n", "t.MPF", "T", DEFAULT_INFO, cfg)
        self.assertTrue(any(i.kind == "mutually-exclusive-m" for i in issues))
```

- [ ] **Step 2: 运行确认失败**

Run: `conda run -n python38 python -m unittest tests.test_core.CoreTests.test_m04_blocks_auto_m03_and_reports_direction_error tests.test_core.CoreTests.test_m03_and_m04_same_block_is_mutually_exclusive -v`
Expected: 两个测试 FAIL（`spindle-direction` 不存在；互斥用例断言失败）

- [ ] **Step 3: 常量区新增 M04 正则（core.py 顶部，紧邻 M03_RE）**

```python
M04_RE = re.compile(r"(?<![A-Z])M0?4(?!\d)", re.I)
```

- [ ] **Step 4: `add_m03` 首个正文循环增加 M04 拦截**

```python
    for line in lines[start:]:
        if _parse_msg(line) or line.strip() in ("", "%"):
            continue
        code = code_part(line)
        if m03_re.search(code):
            return text, False, ""
        if M04_RE.search(code):
            return text, False, ""
```

- [ ] **Step 5: `validate_program` 记录 m04_pos 并新增互斥与方向错误**

循环前声明 `m04_pos: Optional[int] = None`；主循环内（`m03_pos` 赋值处之后）追加：

```python
        if m04_pos is None and "M" in upper_code and M04_RE.search(code):
            m04_pos = i
```

互斥检查（现有 M03/M05 分支旁）：

```python
            if M03_RE.search(code) and M04_RE.search(code):
                issues.append(Issue(filename, i, raw_line, "mutually-exclusive-m", "error",
                                    "同一程序段同时包含 M03 与 M04，主轴正转与反转互斥"))
```

结尾 `if not has_m03:` 分支改写为：

```python
    if not has_m03:
        if config.auto_m03:
            if m04_pos is not None:
                issues.append(Issue(filename, m04_pos, lines[m04_pos - 1], "spindle-direction", "error",
                                    "正文以 M04 反转启动主轴，已禁止自动补写 M03，请人工确认旋转方向与主轴指令"))
            else:
                issues.append(Issue(filename, start + 1, "", "spindle-start", "error",
                                    "自动补写 M03 失败：正文缺少可插入 M03 的指令位置，请手动补写 M03"))
        else:
            issues.append(Issue(filename, start + 1, "", "spindle-start", "warning", "正文中未找到 M03"))
```

- [ ] **Step 6: 运行确认通过**

Run: `conda run -n python38 python -m unittest tests.test_core -v`
Expected: 全绿（含既有 `test_missing_m03_is_error_when_auto_insert_enabled`、`test_hass_percent_and_existing_m03`）

- [ ] **Step 7: 文档同步**

需求文档 FR-05.6 增加「正文存在 M04 等反转启动指令时不得自动补写 M03，按错误上报」；审查与待办新增已修复项；测试基线 +2。

- [ ] **Step 8: 打包门与提交门**

`powershell -ExecutionPolicy Bypass -File .\build_portable.ps1` → 用户实测 → 确认后提交 `fix(core): M04 存在时禁止自动补写 M03 并新增互斥检查`。

### WP-A2: 多刀程序自动换刀防护

**Files:**
- Modify: `ncodeprocess/core.py:874`（`add_initial_tool_change`）、`ncodeprocess/core.py:1314/1400`（`reprocess_file`/`build_plan` 中 note 追加逻辑）
- Test: `tests/test_core.py`

**范围：** D4 默认 A：刀具列表 >1 或正文引用 ≥2 个不同 T 号时跳过改写，note 进入 `f.changes`（预览与报告可见）。

- [ ] **Step 1: 写失败测试**

```python
    def test_initial_tool_change_skipped_when_multiple_tools_configured(self):
        tools = [ToolInfo(1, "10"), ToolInfo(2, "10")]
        cfg = self._cfg(auto_tool_change=True)
        text = "N10 G90;\nT2M6;\nN20 X10 Y10 F500;\nN30 M30;\n"
        out, changed, note = add_initial_tool_change(text, tools, cfg)
        self.assertFalse(changed)
        self.assertNotIn("T1M6", out)
        self.assertIn("多", note)
```

- [ ] **Step 2: 运行确认失败**

Run: `conda run -n python38 python -m unittest tests.test_core.CoreTests.test_initial_tool_change_skipped_when_multiple_tools_configured -v`
Expected: FAIL（当前会改写为 T1M6）

- [ ] **Step 3: `add_initial_tool_change` 在重写前加防护**

在 `number = min(numbers)` 之后、循环改写之前：

```python
    if len(tools) > 1:
        return text, False, "刀具列表包含多把刀具，已跳过自动换刀改写，请人工确认"
    referenced = set()
    for line in body:
        code = code_part(line)
        referenced.update(int(m.group(1)) for m in TOOL_CALL_RE.finditer(code))
    if len(referenced) > 1:
        return text, False, "正文引用了多个刀具号（" + "、".join("T" + str(n) for n in sorted(referenced)) + "），已跳过自动换刀改写，请人工确认"
```

- [ ] **Step 4: `reprocess_file` 与 `build_plan` 的 note 追加逻辑改为按 note 非空追加**

两处现有写法：

```python
    if tool_changed:
        f.changes.append(tool_note)   # reprocess_file
    if tool_changed:
        changes.append(tool_note)     # build_plan
```

统一改为：

```python
    if tool_note:
        f.changes.append(tool_note)   # reprocess_file
    if tool_note:
        changes.append(tool_note)     # build_plan
```

- [ ] **Step 5: 运行确认通过**

Run: `conda run -n python38 python -m unittest tests.test_core -v`
Expected: 全绿（含既有 `test_optional_initial_tool_change_is_inserted_and_corrected` 单刀用例）

- [ ] **Step 6: 文档同步 + 打包门 + 提交门**

用户手册「自动添加换刀指令」注明多刀程序跳过；提交 `fix(core): 多刀程序跳过自动换刀改写并提示`。

---

## 阶段 B：工程正确性（P1）

### WP-B1: 代码编辑器换行保护

**Files:**
- Modify: `ncodeprocess/gui.py`（新增纯函数 + `edit_program_code` 的 `save()`）
- Test: `tests/test_gui.py`

- [ ] **Step 1: 写失败测试（纯函数级）**

```python
    def test_text_changed_ignoring_line_endings(self):
        from ncodeprocess.gui import text_changed_ignoring_line_endings as changed
        self.assertFalse(changed("N10 X10;\r\nN20 Y20;\r\n", "N10 X10;\nN20 Y20;\n"))
        self.assertTrue(changed("N10 X10;\r\n", "N10 X11;\r\n"))
```

- [ ] **Step 2: 运行确认失败**（函数不存在 → ImportError/FAIL）
- [ ] **Step 3: gui.py 新增纯函数**

```python
def text_changed_ignoring_line_endings(original: str, edited: str) -> bool:
    """比较编辑前后文本，忽略 CRLF/LF 差异，防止仅行尾变化触发重处理。"""
    def normalize(value: str) -> str:
        return value.replace("\r\n", "\n").replace("\r", "\n")
    return normalize(original) != normalize(edited)
```

- [ ] **Step 4: `edit_program_code` 的 `save()` 改用该函数**

`if new_text == f.original_text:` → `if not text_changed_ignoring_line_endings(f.original_text, new_text):`

- [ ] **Step 5: 回归**：`conda run -n python38 python -m unittest tests.test_gui -v` 全绿
- [ ] **Step 6: 文档同步 + 打包门 + 提交门**：`fix(gui): 编辑器保存忽略换行风格差异，避免 CRLF 静默转 LF`

### WP-B2: PROGRAM/NC MACHINE 覆盖口径（依赖 D1）

**Files:** `ncodeprocess/core.py:822`（`apply_header` 的 `protect_existing`）或 `docs/NCodeProcess-需求文档.md`

若 D1=A（推荐）：
- [ ] 需求文档 4.2.6/4.2.7 明确「PROGRAM、NC MACHINE、CONTROL SYSTEM、DATE 已有非空值均不得被覆盖选项修改；程序名以「修改程序名」流程同步」。
- [ ] 增加锁定测试（若不存在）：`test_existing_reprocessing_header_values_are_preserved` 已覆盖，补充断言 `overwrite_fields=True` 时 PROGRAM/NC MACHINE 仍保留。
- [ ] 打包门 + 提交门：`docs: 明确 PROGRAM/NC MACHINE 与受保护字段口径一致`（与 WP-D4 合并提交亦可）。

若 D1=B：
- [ ] `apply_header` 的 `protect_existing` 改为仅含 `CONTROL SYSTEM`/`DATE`；`validate_program` 的 `program-mismatch` 保持 error 并在 GUI 提供「以本次值为准」确认路径。
- [ ] 新增测试：`test_overwrite_fields_updates_program_and_machine`；回归后打包门 + 提交门。

### WP-B3: M06 注释误判修复

**Files:** `ncodeprocess/core.py:1162`（`require_m06` 检查）、`tests/test_core.py`

- [ ] **Step 1: 失败测试**

```python
    def test_m06_inside_comment_does_not_satisfy_requirement(self):
        cfg = self._cfg(require_m06=True)
        issues = validate_program("N10 T1;\nN20 (M06);\nN30 M30;\n", "t.MPF", "T", DEFAULT_INFO, cfg)
        self.assertTrue(any(i.kind == "tool-change" for i in issues))
```

- [ ] **Step 2: 运行确认失败**（当前注释 M06 满足检查 → FAIL）
- [ ] **Step 3: 主循环内记录 has_m06（code_part 口径），替换整块正则搜索**

```python
        if "M" in upper_code and M06_RE.search(code):
            has_m06 = True
```

结尾改为 `if config.require_m06 and tool_numbers and not has_m06:`。

- [ ] **Step 4: 回归**：`conda run -n python38 python -m unittest tests.test_core -v` 全绿
- [ ] **Step 5: 打包门 + 提交门**：`fix(core): M06 检查排除括号注释`

---

## 阶段 C：需求补齐与工程优化（P2）

### WP-C1: 最大文件大小/数量配置（9.4 补齐，依赖 D5）

**Files:** `ncodeprocess/core.py`、`ncodeprocess/gui.py`（设置页）、`ncodeprocess/preferences.py`、`tests/`

- [ ] `Config` 新增 `max_file_size: int = 0`、`max_files: int = 0`（0=不限制，D5 默认 A）。
- [ ] `scan_directory`：文件循环开头累计计数，`max_files` 超限后追加 warning 并 `break`；`max_file_size` 超限的 MPF 生成 `file-too-large` error issue（不进 build_plan）。
- [ ] 测试：`test_max_files_limit_stops_scan`、`test_max_file_size_skips_oversized_mpf`（构造 >limit 的 MPF 断言 issue）。
- [ ] `preferences.py`：`REGISTRY_DEFAULTS` 增加 `"max_file_size": ""`、`"max_files": ""`；GUI 设置「基本设置 → 文件处理」新增两个输入（留空=不限制），`config()` 解析（非法回退 0）。
- [ ] 持久化 roundtrip 测试：`test_max_limits_vars_roundtrip`。
- [ ] 需求文档 9.4 标注更新为「已实现（WP-C1）」；审查与待办登记。
- [ ] 打包门 + 提交门：`feat(core): 文件大小与数量上限可配置（9.4 补齐）`。

### WP-C2: 死代码清理

**Files:** `ncodeprocess/core.py:32`、`ncodeprocess/gui.py:33`、`ncodeprocessreportviewer/viewer.py:10/85`、`ncodeprocess/gui.py:1648`

- [ ] 删除 `core.py` 中从未使用的 `PROGRAM_RE`（已确认零引用）。
- [ ] 删除 `gui.py` 顶部未使用的 `validate_program` 导入。
- [ ] 删除 `viewer.py:10` 未使用的 `Dict` 导入；删除 `viewer.py:85` 中 `parameter == "G00"` 死分支。
- [ ] 删除 `Config.defer_stats` 字段与 `gui.py` `config()` 中 `defer_stats=False` 传参（保留 `validate_program` 的 `info` 形参不改签名，避免连带改动全部调用点，仅加注释说明保留原因）。
- [ ] 回归：主套件 239 + 查看器 6 全绿。
- [ ] 提交门：`refactor: 清理未使用常量/导入/死配置`（可与其它 WP 合并提交）。

### WP-C3: CLI/GUI 配置面统一（依赖 D7）

若 D7=A：
- [ ] `cli.py` 新增参数：`--m03-position {after-s,standalone}`、`--newline {auto,crlf,lf}`、`--feed-min/--feed-max/--spindle-min/--spindle-max`、`--aux-m03/--aux-m05/--aux-m08/--aux-m09`（开关）、`--feed-outlier-iqr/--feed-outlier-low/--feed-outlier-high`、`--multiple-spindle/--no-multiple-spindle`、`--max-file-size/--max-files`。
- [ ] CLI 启动时 `load_all()` 读持久化偏好作为默认，显式参数覆盖。
- [ ] 新增 `tests/test_cli.py`：`test_cli_newline_flag_applied`、`test_cli_loads_persisted_preferences`（临时注册表键）。
- [ ] 文档：README/用户手册补充 CLI 参数表。

若 D7=B：
- [ ] 仅补文档：README 注明 CLI 为最小可用预览/执行工具，GUI 为完整配置面；CLI 参数表列出支持项与不支持项。

### WP-C4: 扫描与应用并发互斥

**Files:** `ncodeprocess/gui.py`（`scan`/`finish_scan`/`apply_selected`/`apply_info`）、`tests/test_gui.py`

- [ ] `App.__init__` 增加 `self._scan_running = False`。
- [ ] `scan()` 开头置 True 并禁用「全部应用」「应用所选」按钮；`finish_scan` 末尾置 False 并恢复。
- [ ] `apply_selected`/`apply_info` 开头若 `self._scan_running` 则提示「扫描进行中，请稍候」并 return。
- [ ] 测试：`test_apply_buttons_disabled_while_scan_running`（按现有 test_gui 模式调用 `scan()` 后断言按钮 state）。
- [ ] 打包门 + 提交门：`fix(gui): 扫描期间禁用应用操作，避免共享 FilePlan 竞争`。

### WP-C5: 嵌套忽略目录 + 未知扩展名口径（依赖 D2）

**Files:** `ncodeprocess/core.py:501`（`ignored_directories` 判断）、需求文档

- [ ] `scan_directory` 判断改为检查目录部分任意层级：

```python
        if any(part.lower() in ignored_directories for part in relative_parts[:-1]):
            continue
```

- [ ] 测试：`test_recursive_scan_skips_nested_aptsource_directories`（递归模式下 `sub/aptsource/x.MPF` 不被处理）。
- [ ] D2=A 时：需求文档 13.3 删除「未知扩展名列入待确认清单」表述，统一为 5.2「完全忽略」。
- [ ] 打包门 + 提交门：`fix(core): 递归扫描跳过任意深度的数据/归档目录` + `docs:` 口径统一（可与 WP-D4 合并）。

### WP-C6: 运行日志落盘与报告内嵌（依赖 D6）

**范围：** 磁盘滚动日志 + 报告内嵌 `runtime_log`/`log_path`（用户已确认纳入报告），事件枚举与字段结构以 `NCodeProcess-报告内容规范.md` 第 9 节为准。

- [ ] `core.py` 提供运行事件 hook：`set_runtime_log_sink(callback)`；`scan_directory`/`build_plan`/`process_plan` 关键节点调用（`scan_start`/`scan_finish`/`scan_warning`/`plan_built`/`process_start`/`process_file`/`process_finish`/`backup_created`/`error`）。
- [ ] `ProcessReport` 增加 `runtime_log: List[dict]` 与 `log_path: str = ""` 字段；`process_plan` 结束时回填内存环形缓冲（最近 500 条）。
- [ ] `gui.py` 注册 sink：`RotatingFileHandler` 写 `NCodeProcessData/logs/ncodeprocess-YYYYMMDD.log`（UTF-8，3×1MB）；worker 异常 `logger.exception` 后经 `_safe_after` 回调状态栏。
- [ ] 导出报告时经 `ProcessReport.to_dict` 带出 `runtime_log` 与 `log_path`；截断时追加 `event=warning`、`detail=已截断，完整日志见 log_path` 事件。
- [ ] 测试：`test_scan_error_writes_log_file`（损坏目录 → 日志文件存在且含 traceback）、`test_report_contains_runtime_log`（处理后断言含 `scan_start`/`process_finish`）、`test_runtime_log_truncation_note`。
- [ ] 查看器：`viewer.py` 新增「运行日志」页签（时间/级别/事件/消息，级别着色，缺失回退空，log_path 失效提示）；测试 `test_runtime_log_tab`（查看器基线 6 → 7）。
- [ ] 文档：两份报告内容规范第 9 节/页签映射回填「已实现」；测试指南基线更新。
- [ ] 打包门 + 提交门：`feat(core): 运行日志双轨落盘并内嵌报告`（用户实测确认后提交）。

### WP-C7: `process_plan` 拆分重构

- [ ] 将 `process_plan` 主循环内的分支抽出私有纯函数：`_exec_duplicate(item, ...)`、`_exec_mpf(...)`、`_exec_aptsource(...)`、`_exec_delete(...)`，签名保持内部私有（`_` 前缀）。
- [ ] 行为零变化：重构前后对 `样例文件/数控程序/HASS` 与 `V5-2500B` 各跑一次，输出 diff 为空；239 项测试全绿。
- [ ] 提交门：`refactor(core): process_plan 按动作拆分执行器`。

### WP-C8: 清除设置后后端显示一致

- [ ] `_clear_registry_settings` 在 `clear_all()` 后把 `self.storage_backend_var.set("registry")`（与实际探测默认一致）。
- [ ] 扩展现有 `test_clear_registry_confirmed_removes_values`：断言 `storage_backend_var.get() == "registry"`。
- [ ] 提交门：`fix(gui): 清除设置后重置保存位置显示`。

### WP-C9: 抬刀阈值进 Config

**Files:** `ncodeprocess/core.py:49`（`RETRACT_Z_THRESHOLD`）、`ncodeprocess/core.py:1195`（`validate_program` 阶段分组）、`gui.py` 校验规则页、`preferences.py`

- [ ] `Config` 新增 `retract_z_threshold: float = 5.0`；`validate_program`（`core.py:1195`）改用 `config.retract_z_threshold`（保留模块常量作默认值引用）。
- [ ] `preferences.py` 新增 `"retract_z_threshold": "5"`；GUI「校验规则 → F 离群与 S 警告」新增输入（复用 `parse_positive_default`）。
- [ ] 测试：`test_retract_z_threshold_configurable`（阈值 10 时 Z8 行归入切削而非移动）、`test_retract_z_threshold_var_roundtrip`。
- [ ] 打包门 + 提交门：`feat(core): 抬刀高度阈值可配置`。

---

## 阶段 D：清理与收尾（P3）

### WP-D1: 查看器清理

- [ ] 删除 `viewer.py` 未用导入与 `iter_stats_rows` 死分支（若 WP-C2 未覆盖）。
- [ ] 回归：`conda run -n python38 python -m unittest discover -s tests -v`（查看器目录）6 项全绿。
- [ ] 提交门：`refactor(viewer): 清理未用代码`。

### WP-D2: 常量与微优化

- [ ] `core.py` 顶部定义 `DEFAULT_NAME_PATTERN = r"^[A-Za-z0-9_一-鿿-]+$"`；`Config.allowed_name_pattern` 与 `_safe_name` 默认参数引用该常量，消除每次调用构造 `Config()` 的开销。
- [ ] 回归 239 项全绿；提交门：`refactor(core): 程序名默认正则改模块常量`。

### WP-D3: 构建不可复现性文档化

- [ ] 根 `README.md` 与 `NCodeProcess/README.md`、`SECURITY.md` 注明：构建使用 `-OO`、随机 PYZ 密钥与随机 hash seed，产物不可逐字节复现（防破解/防提取设计），校验以发布包内 `SHA256SUMS.txt` 为准。
- [ ] 提交门：`docs: 说明构建不可复现设计与校验方式`。

### WP-D4: 文档一致性总修订（贯穿）

各 WP 完成时同步；最终统一核对：
- [ ] 需求文档：9.4 状态、5.2/13.3 口径、4.2.6 保护字段、FR-05.6 M04 条款、第 8 节配置清单（新增 max_file_size/max_files/retract_z_threshold）。
- [ ] 审查与待办：登记本计划全部已修复项与提交号。
- [ ] 测试指南：基线数量更新（预计 239+ 主 / 6+ 查看器）。
- [ ] 用户手册/程序理解与操作记录/发布说明：功能与配置描述同步。

---

## 执行方式与确认流程

1. 用户先确认 Phase 0 决策 D1–D7（默认取表中「建议」列）。
2. 每个 WP 启动前：说明范围、文件、验收标准，获得确认。
3. 每个 WP 完成后：全量测试 → `build_portable.ps1` 打包 → 用户实测 → 确认后提交。
4. 建议执行顺序：WP-A1 → WP-A2 → WP-B1 → WP-B3 → WP-B2（D1）→ 阶段 C（C1→C4→C5→C8→C9→C2→C6→C7）→ 阶段 D（D1→D2→D3→D4 总核）。

---

## Self-Review 记录

- **Spec 覆盖**：审查报告 P0（M04）、P1（换刀、换行、PROGRAM 口径、M06）、P2（9.4 配置、死代码、CLI 分叉、并发、嵌套忽略、日志、process_plan、后端显示、抬刀阈值）、P3（查看器、常量、构建文档、文档修订）均有对应 WP；无遗漏项。
- **占位符检查**：所有步骤含具体文件/行号/测试名/代码或明确决策分支；无 TBD。
- **类型/签名一致性**：新增测试与实现均引用既有 `ToolInfo`/`Issue`/`Config` 字段；`add_initial_tool_change`/`add_m03` 返回值契约（text, changed, note）保持不变；`validate_program` 签名不变。
