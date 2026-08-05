# NCodeProcess 测试套件整改实施计划（2026-08-05）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 消除测试套件的重复执行与冗余，补上 CLI/CSV 覆盖空白，修复偶发 flake，将主项目测试从 239 项精简到约 205 项、全套耗时从约 47s 降到 30s 以内，且行为零回退。

**Architecture:** 全部改动限于 `tests/` 与文档，不触碰 `ncodeprocess/*.py` 生产代码。核心思路：① 在 `tests/test_gui.py` 内抽 `LayoutWidgetMixin`（同模块定义，规避 discover 顶层导入与包导入两种模式下 `import support` 不一致），让 `SettingsDialogTests` 不再继承测试方法（消除 19 个重复执行）；② 按主题合并重叠用例（subTest 参数化）；③ 删除/弱化脆弱断言；④ 用轮询 pump 修复 tooltip 时序 flake；⑤ 新增 CLI、CSV、程序名提取测试。

**Tech Stack:** Python 3.8 / unittest（TDD，无第三方运行时依赖）；计时用 `Measure-Command` 或逐用例脚本（临时，不入库，可选入库为 `tools/profile_tests.py`）。

**执行纪律（AGENTS.md 强制）：**
- 每个 WP 启动前向用户说明范围、涉及文件、验收标准并取得确认。
- 测试类 WP 无需打包 EXE（无生产代码改动），但必须：全量测试绿 → 用户确认 → 才允许 git 提交。
- 提交信息使用 `refactor(test):` / `test:` / `docs:` 前缀 + 中文描述；提交不包含构建产物。
- 每次合并/删除用例后同步测试指南基线数字（或按 D-A 改为以 discover 输出为准）。
- 本计划基线：主项目 239 项（其中 19 项为 `SettingsDialogTests` 继承 `LayoutWidgetTests` 造成的重复执行）、查看器 6 项；全套约 47.5s。

---

## 任务总览

| WP | 主题 | 优先级 | 依赖决策 | 涉及文件 | 验收标准 |
|---|---|---|---|---|---|
| WP-T1 | helper 抽 mixin，修复继承重复执行 | 高 | 无 | `tests/test_gui.py` | 用例 239→220，全套耗时明显下降，全绿 |
| WP-T2 | 合并 11 组重叠用例 | 高 | 无 | `tests/test_core.py`、`tests/test_gui.py` | 用例 220→201，全绿 |
| WP-T3 | tooltip 测试去时序化 | 高 | 无 | `tests/support.py`、`tests/test_gui.py` | 连续 10 次全套无 flake |
| WP-T4 | 删除/弱化脆弱断言 | 中 | D-B | `tests/test_preferences.py`、`tests/test_release_assets.py`、`tests/test_gui.py` | 用例 201→200；版本升级/换主题不再碎 |
| WP-T5 | 新增 CLI / CSV / 程序名提取测试 | 中 | 无 | `tests/test_cli.py`（新增）、`tests/test_core.py` | 用例 200→205；`cli.py` 不再零覆盖 |
| WP-T6 | 拆分大测试文件 | 低 | D-D | `tests/test_gui_*.py`、`tests/test_core_*.py` | 按主题分文件，用例数不变全绿 |
| WP-T7 | 基线文档与流程收尾 | 低 | D-A/D-C | 测试指南、审查与待办、`run_tests.ps1`（可选）、CI（可选） | 基线数字与文档一致；一键测试脚本可用 |

## 决策点（Phase 0，执行前确认）

| 编号 | 决策 | 选项 | 建议 |
|---|---|---|---|
| D-A | 测试基线数字的维护方式 | A：文档写死数字，增删用例时同步修改（现状）；B：文档改为「以 `python -m unittest discover -s tests -v` 输出为准」，数字仅作参考（推荐） | B |
| D-B | `test_defaults_cover_all_registry_items` 处理 | A：直接删除，由 roundtrip 测试兜底（推荐）；B：改为与 Config 字段的结构对照（仍是复制真源，不推荐） | A |
| D-C | 是否新增 CI | A：新增 GitHub Actions（windows-latest + Python 3.8，跑主/查看器两套 discover）；B：不加 CI，仅提供 `run_tests.ps1`（推荐先 B，CI 列为后续） | B |
| D-D | 是否本轮拆分大测试文件 | A：本轮拆分；B：仅抽 helper，拆分列为后续（推荐，先拿低风险收益） | B |

---

## 文件结构

- `tests/test_gui.py`：模块顶部新增 `LayoutWidgetMixin`（`_build_app`/`_descendants`/`_collect_buttons`/`_relative_x_to_root`/`_column_total`/`_pump_until`）；`LayoutWidgetTests` 与 `SettingsDialogTests` 改为继承 `unittest.TestCase, LayoutWidgetMixin`；`ScanLifecycleTests` 复用 mixin；合并/弱化/删除若干用例；tooltip 三测试改用 `_pump_until`。
- `tests/test_core.py`：合并 11 组中的 8 组；新增 `write_csv`、`extract_program_name` 直测。
- `tests/test_cli.py`（新增）：CLI 预览不写盘、`--yes` 必填校验（退出码 2）、`--yes` 全流程、`--csv-report`。
- `tests/test_preferences.py`：按 D-B 删除 `test_defaults_cover_all_registry_items`。
- `tests/test_release_assets.py`：版本断言改为由 `__version__` 推导；构建脚本断言删减。
- `docs/NCodeProcess-测试指南.md`、`docs/NCodeProcess-审查与待办.md`：基线与登记。
- `run_tests.ps1`（新增，可选，D-C=B 时提供）。

---

## WP-T1: helper 抽 mixin，修复继承重复执行

**Files:**
- Modify: `tests/test_gui.py`（模块顶部新增 mixin；`LayoutWidgetTests`、`SettingsDialogTests`、`ScanLifecycleTests`）

**背景：** `class SettingsDialogTests(LayoutWidgetTests)` 继承了 `LayoutWidgetTests` 全部 19 个 `test_*` 方法，套件里每个布局/对比/tooltip 测试跑两遍（239 = 220 独立 + 19 重复），且 `ScanLifecycleTests` 与 `LayoutWidgetTests` 存在逐字重复的 `_build_app`/`_descendants`。

- [ ] **Step 1: 记录基线**

Run: `conda run -n python38 python -m unittest discover -s tests -v 2>&1 | Select-Object -Last 3`
Expected: `Ran 239 tests ... OK`；用 `Measure-Command` 记录耗时（约 47s）。

- [ ] **Step 2: 在 `tests/test_gui.py` 顶部（imports 之后、`DiffViewTests` 之前）新增 mixin（完整代码）**

```python
import time


class LayoutWidgetMixin:
    """布局/交互/生命周期测试共用的窗口构造、遍历与等待 helper（不含 test_ 用例）。"""

    def _build_app(self, width, height):
        root = tk.Tk()
        root.withdraw()
        with patch.object(App, "scan", lambda _self: None):
            # 与本模块既有 _build_app 保持一致：使用模块级隔离注册表键。
            app = App(root, settings_registry_key=TEST_SETTINGS_KEY)
        root.geometry(f"{width}x{height}")
        root.deiconify()
        root.update_idletasks()
        root.update()
        root.update_idletasks()
        return root, app

    @staticmethod
    def _descendants(widget):
        for child in widget.winfo_children():
            yield child
            yield from LayoutWidgetMixin._descendants(child)

    @staticmethod
    def _collect_buttons(widget):
        buttons = []
        for child in widget.winfo_children():
            if child.winfo_class() == "TButton":
                buttons.append(child)
            buttons.extend(LayoutWidgetMixin._collect_buttons(child))
        return buttons

    @staticmethod
    def _relative_x_to_root(widget, root):
        x = 0
        current = widget
        while current is not root:
            x += current.winfo_x()
            current = current.master
        return x

    @staticmethod
    def _column_total(table, columns):
        return sum(int(table.column(column, "width")) for column in columns)

    @staticmethod
    def _pump_until(root, predicate, message, timeout_ms=2000):
        """轮询 root.update() 直到条件成立，消除合成事件时序 flake。"""
        deadline = time.time() + timeout_ms / 1000.0
        while time.time() < deadline:
            root.update()
            if predicate():
                return True
        return False
```

> 说明：mixin 定义在本模块内，直接引用模块级 `TEST_SETTINGS_KEY` 常量与既有 `tk`/`patch`/`App` 导入，两种调用方式（`discover -s tests` 与 `python -m unittest tests.test_gui`）均无导入路径问题；不新建 `support.py`，避免 discover 顶层导入与包导入两种模式下 `import support` 不一致。

- [ ] **Step 3: 改造 `tests/test_gui.py`**

3a. 删除各测试类中的 `_build_app`/`_descendants`/`_collect_buttons`/`_relative_x_to_root`/`_column_total` 重复定义（mixin 已在模块顶部提供）。

3b. 三个类的声明改为（`SettingsDialogTests` 不再继承 `LayoutWidgetTests`）：

```python
class LayoutWidgetTests(unittest.TestCase, LayoutWidgetMixin):
    pass

class SettingsDialogTests(unittest.TestCase, LayoutWidgetMixin):
    pass

class ScanLifecycleTests(unittest.TestCase, LayoutWidgetMixin):
    pass
```

3c. 删除 `ScanLifecycleTests` 内重复的 `_build_app`/`_descendants`（mixin 已提供）。

3d. mixin 无 `test_` 方法且不继承 `unittest.TestCase`，不会被当作测试收集；`SettingsDialogTests` 不再继承 `LayoutWidgetTests`，其 19 个继承用例消失，套件运行时计数回到每个用例只跑一次。

- [ ] **Step 4: 回归并核对用例数**

Run: `conda run -n python38 python -m unittest discover -s tests -v 2>&1 | Select-Object -Last 3`
Expected: `Ran 220 tests ... OK`；耗时较基线下降（预计 30s 内）。

- [ ] **Step 5: 提交门**

用户确认后提交：`refactor(test): 抽取 LayoutWidgetMixin，消除 SettingsDialogTests 继承导致的用例重复执行`。

---

## WP-T2: 合并 11 组重叠用例

**Files:** `tests/test_core.py`、`tests/test_gui.py`

全部合并均为「删除旧方法、写入合并方法」，行为断言不变；每组合并后立即跑目标类验证。以下按组给出完整合并代码。

### 组 1：换行三连 → 1（test_core）

删除 `test_newline_force_lf_converts_crlf_source` / `test_newline_force_crlf_converts_lf_source` / `test_newline_auto_preserves_source_style`，写入：

```python
    def test_newline_policy_converts_and_preserves_source_style(self):
        def run(payload_bytes, cfg):
            root = self.make_dir()
            (root / "x_P.MPF").write_bytes(payload_bytes)
            plan = build_plan(scan_directory(str(root), cfg), DEFAULT_INFO, cfg)
            report = process_plan(plan, str(root), cfg)
            self.assertEqual(report.success, 1)
            return (root / "P.MPF").read_bytes()

        crlf = b'MSG("PROGRAM:P")\r\nN1S1000M03\r\nN2M30\r\n'
        lf = b'MSG("PROGRAM:P")\nN1S1000M03\nN2M30\n'
        with self.subTest(mode="lf-from-crlf"):
            data = run(crlf, Config(g00_level="allow", newline="lf"))
            self.assertNotIn(b"\r\n", data)
            self.assertIn(b"\n", data)
        with self.subTest(mode="crlf-from-lf"):
            data = run(lf, Config(g00_level="allow", newline="crlf"))
            self.assertIn(b"\r\n", data)
            self.assertNotIn(b"\n", data.replace(b"\r\n", b""))
        with self.subTest(mode="auto-preserves-crlf"):
            data = run(crlf, self._cfg())
            self.assertIn(b"\r\n", data)
```

### 组 2：align_lines 两测试 → 1（test_core）

```python
    def test_align_lines_tags(self):
        rows = align_lines("A\nB\nC\nD", "A\nB\nX\nD")
        self.assertEqual(rows, [("A", "", "A", ""), ("B", "", "B", ""), ("C", "changed", "X", "changed"), ("D", "", "D", "")])
        self.assertIn(("", "", "B", "added"), align_lines("A\nC", "A\nB\nC"))
        self.assertIn(("B", "removed", "", ""), align_lines("A\nB\nC", "A\nC"))
```

### 组 3：aux 顺序四测试 → 1（test_core）

```python
    def test_aux_order_rules(self):
        cases = (
            ("m03-before-motion", "N1G1X10\nN2M03\nN3M30\n", True),
            ("m03-before-motion", "N1M03\nN2G1X10\nN3M30\n", False),
            ("m05-before-end", "N1M03\nN2M30\nN3M05\n", True),
            ("m08-before-cut", "N1G1X10\nN2M08\nN3M30\n", True),
            ("m09-before-end", "N1M03\nN2M30\nN3M09\n", True),
            ("m09-before-end", "N1M03\nN2M30\n", False),
        )
        for rule, body, expected in cases:
            with self.subTest(rule=rule, body=body[:12]):
                text = '%\nMSG("PROGRAM:P")\n' + body + '%\n'
                issues = validate_program(text, "P.MPF", "P", DEFAULT_INFO, Config(g00_level="allow", aux_checks={rule}))
                found = any(i.kind == "aux-order" for i in issues)
                self.assertEqual(found, expected)
```

### 组 4：M03 注释两测试 → 1（test_core）

```python
    def test_m03_ignores_spindle_mention_inside_comment(self):
        comment_only = '%\nMSG("PROGRAM:P")\nN1G1X10 (FEED S5000 OK)\nN2M30\n%\n'
        out, changed, _note = add_m03(comment_only, Config())
        self.assertTrue(changed)
        self.assertNotIn("(FEED S5000 OK)M03", out)
        self.assertIn("M03\nN1G1X10 (FEED S5000 OK)", out)

        real_spindle = '%\nMSG("PROGRAM:P")\nN1G1X10 (FEED S5000 MAX)\nN2G1X20S1000\nN3M30\n%\n'
        out, changed, _note = add_m03(real_spindle, Config())
        self.assertTrue(changed)
        self.assertNotIn("(FEED S5000 MAX)M03", out)
        self.assertIn("N2G1X20S1000M03", out)
```

### 组 5：普通立铣刀识别三测试 → 1（test_core）

```python
    def test_ordinary_mill_family_detection(self):
        cases = (
            ("CUTTER/ 16.000000, 3.000000\nTOOLNO/5, 16.000000, 3.000000,, 120.000000,$\n", "16.000", "3.000", "圆鼻立铣刀"),
            ("CUTTER/ 10.000000,  5.000000\nTOOLNO/1, 10.000000, 5.000000,, 120.000000,$\n", "10.000", "5.000", "球头立铣刀"),
            ("CUTTER/ 10.000000,  0.000000\nTOOLNO/2, 10.000000, 0.000000,, 120.000000,$\n", "10.000", "0.000", "平底立铣刀"),
            ("CUTTER/ 20.000000,  3.000000\nTOOLNO/3, 20.000000, 3.000000,, 120.000000,$\n", "20.000", "3.000", "圆鼻立铣刀"),
            ("CUTTER/10,2\nTOOLNO/1,10,1,,\n", "10.000", "2.000", ""),
        )
        for text, dia, coner, tool_type in cases:
            with self.subTest(tool_type=tool_type or "none"):
                tool = extract_tools(text)[0]
                self.assertEqual(tool.dia, dia)
                self.assertEqual(tool.tool_coner, coner)
                self.assertEqual(tool.tool_type, tool_type)
```

> 说明：第 5 个用例即原 `test_ordinary_mill_requires_matching_corner_radius`（圆角不一致不判普通立铣刀），合并后保留为 subTest。

### 组 6：钻类识别两测试 → 1（test_core）

```python
    def test_drill_and_center_drill_detection(self):
        sample_drill = ("CUTTER/  5.200000,  0.000000,  2.600000,  1.501111, 30.000000,$\n"
                        "TOOLNO/9,    5.200000,,  120.000000,  120.000000,$\n"
                        "45.000000,    1.501000,   35.000000,2,    0.000000,NOTE\n")
        sample_center = ("CUTTER/  2.500000,  0.000000,  1.250000,  0.751076, 31.000000,$\n"
                         "TOOLNO/13,    2.500000,,  118.000000,  120.000000,$\n"
                         "5.000000,,   11.000000,,    0.000000,NOTE\n")
        size_independent = (
            ("CUTTER/ 7.250000, 0.000000, 3.625000, 2.000000, 31.000000,$\n         0.000000, 11.000000\nTOOLNO/13, 7.250000,, 118.000000, 120.000000,$\n    5.000000,, 11.000000,, 0.000000,NOTE\n", "7.250", "中心钻"),
            ("CUTTER/ 8.750000, 0.000000, 4.375000, 2.500000, 30.000000,$\n         0.000000, 35.000000\nTOOLNO/10, 8.750000,, 120.000000, 120.000000,$\n   45.000000, 2.500000, 35.000000,2, 0.000000,NOTE\n", "8.750", "钻头"),
        )
        cases = (
            (sample_drill, "5.200", "钻头"),
            (sample_center, "2.500", "中心钻"),
            *size_independent,
        )
        for text, dia, expected_type in cases:
            with self.subTest(tool_type=expected_type):
                tool = extract_tools(text)[0]
                self.assertEqual(tool.dia, dia)
                self.assertEqual(tool.tool_type, expected_type)
                self.assertEqual(tool.tool_angle, "")
                self.assertNotIn("TOOL_ANGLE", tool.to_msg())
```

> 说明：删除 `test_sample_apt_drill_and_center_drill_with_continuation`、`test_drill_types_detected_independent_of_diameter`、`test_matching_cutter_and_toolno_is_ordinary_end_mill`、`test_round_nose_split_into_ball_flat_and_round_nose`、`test_sample_apt_round_nose_ball_and_flat_mills_are_ordinary`、`test_ordinary_mill_requires_matching_corner_radius`（组 5/6 已覆盖）。

### 组 7：roundtrip 四测试 → 1（test_gui）

删除 `test_m03_position_var_roundtrip` / `test_newline_var_roundtrip` / `test_feed_limits_vars_roundtrip` / `test_batch2_controls_exist`，写入：

```python
    def test_batch2_var_defaults_and_roundtrip(self):
        root, app = self._build_app(1286, 668)
        try:
            self.assertEqual(app.m03_position_var.get(), "after-s")
            self.assertEqual(app.newline_var.get(), "auto")
            for name in ("feed_min_var", "feed_max_var", "spindle_min_var", "spindle_max_var"):
                self.assertEqual(getattr(app, name).get(), "")

            app.m03_position_var.set("standalone")
            app.newline_var.set("lf")
            app.feed_min_var.set("100")
            config = app.config()
            self.assertEqual(config.m03_position, "standalone")
            self.assertEqual(config.newline, "lf")
            self.assertEqual(config.feed_min, 100.0)
            self.assertIsNone(config.feed_max)
            self.assertIsNone(config.spindle_min)
            self.assertIsNone(config.spindle_max)
        finally:
            root.destroy()
```

### 组 8：设置确认两测试 → 1（test_gui）

删除 `test_settings_dialog_opens_and_confirm_applies` 与 `test_settings_dialog_saves_to_registry_on_confirm`，写入：

```python
    def test_settings_dialog_confirm_applies_and_persists(self):
        root, app = self._build_app(1286, 668)
        try:
            app.settings_registry_key = TEST_SETTINGS_KEY
            with patch.object(App, "scan") as scan_mock:
                app.open_settings()
                app.encoding_var.set("gb18030")
                app.delete_extensions_var.set(".log")
                app.program_extensions_var.set(".mpf,.nc")
                app.require_m06_var.set(True)
                app.require_end_marker_var.set(False)
                app._confirm_settings()
                self.assertIsNone(app.settings_window)
                config = app.config()
                self.assertEqual(config.encoding, "gb18030")
                self.assertEqual(config.delete_extensions, {".log"})
                self.assertEqual(config.program_extensions, {".mpf", ".nc"})
                self.assertTrue(config.require_m06)
                self.assertFalse(config.require_end_marker)
                scan_mock.assert_called_once_with()
            saved = load_all(TEST_SETTINGS_KEY)
            self.assertEqual(saved["encoding"], "gb18030")
            self.assertEqual(saved["program_extensions"], ".mpf,.nc")
        finally:
            clear_all(TEST_SETTINGS_KEY)
            root.destroy()
```

### 组 9：config 注入三测试 → 1（test_gui）

删除 `test_config_injects_all_new_settings` / `test_config_injects_program_extensions` / `test_required_fields_vars_exist_and_default_to_all`，写入：

```python
    def test_config_injects_vars_and_required_fields(self):
        root, app = self._build_app(1286, 668)
        try:
            for name in ("required_bianzhi_var", "required_shenhe_var", "required_drawing_var", "required_part_var"):
                self.assertTrue(getattr(app, name).get())
            self.assertEqual([key for key, _label, _required in FIELD_ORDER], app.config().required_fields)

            app.encoding_var.set("gb18030")
            app.delete_extensions_var.set(".log")
            app.allowed_name_pattern_var.set(r"^[A-Za-z0-9]+$")
            app.aptsource_dir_var.set("archive")
            app.program_extensions_var.set(".mpf,.nc")
            app.program_output_extension_var.set(".NC")
            app.require_end_marker_var.set(False)
            app.require_m06_var.set(True)
            app.require_spindle_speed_var.set(True)
            config = app.config()
            self.assertEqual(config.encoding, "gb18030")
            self.assertEqual(config.delete_extensions, {".log"})
            self.assertEqual(config.allowed_name_pattern, r"^[A-Za-z0-9]+$")
            self.assertEqual(config.aptsource_dir, "archive")
            self.assertEqual(config.program_extensions, {".mpf", ".nc"})
            self.assertEqual(config.program_output_extension, ".NC")
            self.assertFalse(config.require_end_marker)
            self.assertTrue(config.require_m06)
            self.assertTrue(config.require_spindle_speed)
        finally:
            root.destroy()
```

### 组 10：F 离群四测试 → 2（test_core）

删除 `test_feed_outlier_high_value_warns` / `test_feed_outlier_high_value_thresholds` / `test_feed_outlier_high_ratio_configurable` / `test_feed_outlier_dynamic_for_small_feed_program`，写入：

```python
    def test_feed_outlier_high_and_low_detection(self):
        def body(feed):
            return "\n".join(f"N{i}G1X{i}F{feed}" for i in range(1, 6))

        cases = (
            (body(3000) + "\nN6G1X60F15000\nN7M30\n", 1),   # 高值检出
            (body(20000) + "\nN6G1X60F25000\nN7M30\n", 0),  # 主体本身上万不误报
            (body(300) + "\nN6G1X60F5\nN7M30\n", 1),        # 小进给程序低值检出
            (body(300) + "\nN6G1X60F1500\nN7M30\n", 1),     # 小进给程序高值检出
        )
        for text, expected in cases:
            with self.subTest(feed=text.split("F")[1].split("\n")[0]):
                issues = validate_program(text, "P.MPF", "P", DEFAULT_INFO, self._cfg())
                self.assertEqual(len([i for i in issues if i.kind == "feed-outlier"]), expected)

    def test_feed_outlier_ratio_configurable(self):
        body = "\n".join(f"N{i}G1X{i}F3000" for i in range(1, 6))
        text = body + "\nN6G1X60F50\nN7M30\n"       # 50 < 3000×0.1
        self.assertEqual(len([i for i in validate_program(text, "P.MPF", "P", DEFAULT_INFO, self._cfg()) if i.kind == "feed-outlier"]), 1)
        relaxed = self._cfg(feed_outlier_low_ratio=0.01)
        self.assertFalse(any(i.kind == "feed-outlier" for i in validate_program(text, "P.MPF", "P", DEFAULT_INFO, relaxed)))

        text = body + "\nN6G1X60F15000\nN7M30\n"    # 15000 ≥ 3000×3
        self.assertEqual(len([i for i in validate_program(text, "P.MPF", "P", DEFAULT_INFO, self._cfg()) if i.kind == "feed-outlier"]), 1)
        raised = self._cfg(feed_outlier_high_ratio=6.0)
        self.assertFalse(any(i.kind == "feed-outlier" for i in validate_program(text, "P.MPF", "P", DEFAULT_INFO, raised)))
```

### 组 11：设置窗口尺寸两测试 → 1（test_gui）

删除 `test_settings_dialog_fits_1286_and_controls_visible` 与 `test_settings_window_size_matches_content`，保留合并版：

```python
    def test_settings_dialog_fits_and_controls_visible(self):
        root, app = self._build_app(1286, 668)
        try:
            app.open_settings()
            win = app.settings_window
            win.update_idletasks()
            self.assertLessEqual(win.winfo_reqwidth(), 640)
            self.assertLessEqual(win.winfo_reqheight(), 500)
            self.assertGreaterEqual(win.winfo_reqwidth(), 400)
            self.assertGreaterEqual(win.winfo_reqheight(), 300)
            texts = {button.cget("text") for button in self._collect_buttons(win)}
            self.assertIn("确定", texts)
            self.assertIn("取消", texts)
            self.assertIn("恢复默认", texts)
            self.assertIn("清除注册表", texts)
        finally:
            root.destroy()
```

- [ ] **Step 1: 按组执行**：每组先删除旧方法、写入合并方法，然后跑目标类

Run（每组后）：`conda run -n python38 python -m unittest tests.test_core.CoreTests -v`（test_core 组）或 `conda run -n python38 python -m unittest tests.test_gui.SettingsDialogTests -v`（test_gui 组）
Expected: 对应组用例数下降且全绿。

- [ ] **Step 2: 全量回归并核对用例数**

Run: `conda run -n python38 python -m unittest discover -s tests -v 2>&1 | Select-Object -Last 3`
Expected: `Ran 201 tests ... OK`（220 − 19）。

- [ ] **Step 3: 提交门**：`refactor(test): 合并 11 组重叠用例，主套件 239→201`（用户确认后）。

---

## WP-T3: tooltip 测试去时序化

**Files:** `tests/test_gui.py`（mixin 已含 `_pump_until`）

**背景：** 全量计时运行中 `test_cell_tooltip_hides_on_leave` 偶发失败（`winfo_viewable` 为 0），单跑 3 次均通过——合成事件在整套负载下时序不稳定。

- [ ] **Step 1: 改写三个 tooltip 测试，显示/隐藏断言改为轮询**

`test_cell_tooltip_shows_truncated_content_after_hover` / `test_cell_tooltip_stays_hidden_for_fully_visible_cell` / `test_cell_tooltip_hides_on_leave` 中：

- `self.assertTrue(app.cell_tooltip.window.winfo_viewable())` → `self.assertTrue(self._pump_until(root, lambda: app.cell_tooltip.window.winfo_viewable(), "tooltip 应显示"))`
- 隐藏断言 → `self.assertTrue(self._pump_until(root, lambda: not app.cell_tooltip.window.winfo_viewable(), "tooltip 应隐藏"))`
- 保持 `patch.object(gui, "CELL_TOOLTIP_DELAY_MS", 0)` 与 `bbox` 计算不变。

- [ ] **Step 2: 稳定性验证**

Run: `1..10 | ForEach-Object { conda run -n python38 python -m unittest tests.test_gui.LayoutWidgetTests -v 2>&1 | Select-String '^OK$|^FAILED' }`
Expected: 10 次全部 `OK`。

- [ ] **Step 3: 提交门**：`test(gui): tooltip 断言改为轮询 pump，消除偶发 flake`。

---

## WP-T4: 删除/弱化脆弱断言

**Files:** `tests/test_preferences.py`、`tests/test_release_assets.py`、`tests/test_gui.py`

- [ ] **Step 1: 按 D-B 删除 `test_defaults_cover_all_registry_items`**（test_preferences.py，整段删除；`REGISTRY_DEFAULTS` 导入保留供其它测试使用）。

- [ ] **Step 2: test_release_assets 版本断言改为动态推导**

```python
    def test_windows_version_resource_matches_package_version(self):
        version_resource = PROJECT_ROOT / "version_info.txt"
        self.assertTrue(version_resource.is_file())
        content = version_resource.read_text(encoding="utf-8")
        version = ncodeprocess.__version__
        major, minor, patch = (int(part) for part in version.split("."))
        self.assertIn(f"filevers=({major}, {minor}, {patch}, 0)", content)
        self.assertIn(f"prodvers=({major}, {minor}, {patch}, 0)", content)
        self.assertIn("StringStruct('ProductName', 'NCodeProcess')", content)
        self.assertIn("StringStruct('InternalName', 'NCodeProcess')", content)
        self.assertIn("StringStruct('OriginalFilename', 'NCodeProcess.exe')", content)
```

- [ ] **Step 3: 构建脚本断言删减**：`test_build_configuration_packages_version_metadata_without_cleaning_all_dist` 仅保留 `version=os.path.join(project_root, "version_info.txt")`、`VERSION.txt` 与 `__version__` 一致性、`'VERSION.txt'` 打包 3 条断言；删除 3 个 `Join-Path $dist` 字符串断言（脚本路径重构不应碎测试）。

- [ ] **Step 4: 像素间距测试弱化**

`test_required_field_checkbuttons_have_equal_spacing` 与 `test_feed_spindle_limit_rows_have_tight_consistent_spacing` 中：
- 删除 `assertEqual(len(set(deltas)), 1)`（精确相等），改为 `assertLessEqual(max(deltas) - min(deltas), 2)`（容差 2px）；
- `feed` 组的 `assertEqual(len(set(gaps)), 1)` 同样改为容差断言，保留 `gaps[0] <= 6`。

- [ ] **Step 5: 回归**

Run: `conda run -n python38 python -m unittest discover -s tests -v 2>&1 | Select-Object -Last 3`
Expected: `Ran 200 tests ... OK`（201 − 1）。

- [ ] **Step 6: 提交门**：`refactor(test): 删除注册表键集复制断言并弱化版本/像素级断言`。

---

## WP-T5: 新增 CLI / CSV / 程序名提取测试

**Files:**
- Create: `tests/test_cli.py`
- Modify: `tests/test_core.py`

- [ ] **Step 1: 新增 `tests/test_cli.py`（完整代码）**

```python
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from ncodeprocess.cli import main


FULL_HEADER = (
    'MSG("BIANZHI:A")\n'
    'MSG("SHENHE:B")\n'
    'MSG("PROGRAM:P")\n'
    'MSG("DRAWING NUMBER:D")\n'
    'MSG("PART VERSION:V")\n'
    'MSG("NC MACHINE:M")\n'
    'MSG("CONTROL SYSTEM:SIE840D")\n'
    'MSG("DATE:Jul 31 09:38:23 2026")\n'
)


class CliTests(unittest.TestCase):
    def make_dir(self):
        return Path(tempfile.mkdtemp(prefix="ncodeprocess-cli-"))

    @staticmethod
    def _run(argv):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = main(argv)
        return code, buffer.getvalue()

    def test_preview_without_yes_never_writes(self):
        root = self.make_dir()
        (root / "x_P.MPF").write_text(FULL_HEADER + "N1S1000M03\nN2M30\n", encoding="utf-8")
        code, _out = self._run(["--input", str(root)])
        self.assertEqual(code, 0)
        self.assertFalse((root / "P.MPF").exists())
        self.assertFalse((root / "NCodeProcessData").exists())

    def test_yes_requires_drawing_and_part_version(self):
        root = self.make_dir()
        (root / "x_P.MPF").write_text(FULL_HEADER + "N1S1000M03\nN2M30\n", encoding="utf-8")
        code, _out = self._run(["--input", str(root), "--yes"])
        self.assertEqual(code, 2)

    def test_yes_executes_and_writes_default_report(self):
        root = self.make_dir()
        (root / "x_P.MPF").write_text(FULL_HEADER + "N1S1000M03\nN2M30\n", encoding="utf-8")
        code, _out = self._run(["--input", str(root), "--yes", "--drawing-number", "D", "--part-version", "V"])
        self.assertEqual(code, 0)
        self.assertTrue((root / "P.MPF").exists())
        reports = list((root / "NCodeProcessData").glob("ncodeprocess-report-*.json"))
        self.assertEqual(len(reports), 1)

    def test_csv_report_written(self):
        root = self.make_dir()
        (root / "x_P.MPF").write_text(FULL_HEADER + "N1S1000M03\nN2M30\n", encoding="utf-8")
        csv_path = root / "issues.csv"
        code, _out = self._run(["--input", str(root), "--yes", "--drawing-number", "D", "--part-version", "V", "--csv-report", str(csv_path)])
        self.assertEqual(code, 0)
        content = csv_path.read_text(encoding="utf-8-sig")
        self.assertTrue(content.startswith("file,line,text,kind,severity,suggestion"))
```

> 说明：`FULL_HEADER` 保证无必填字段错误；正文含 `M30` 满足结束标记；无 G00；`--yes` 时 `drawing-number`/`part-version` 必填由 CLI 拦截（退出码 2）与正常路径分别覆盖。

- [ ] **Step 2: test_core 新增 `write_csv` 与 `extract_program_name` 直测**

```python
    def test_write_csv_emits_header_and_issue_rows(self):
        report = ProcessReport("in", "out", "start")
        report.files = [{
            "file": "A.MPF",
            "action": "keep",
            "issues": [{"file": "A.MPF", "line": 3, "text": "N3", "kind": "feed-zero", "severity": "error", "suggestion": "修正 F0"}],
        }]
        root = self.make_dir()
        path = root / "report.csv"
        report.write_csv(path)
        content = path.read_text(encoding="utf-8-sig")
        lines = content.splitlines()
        self.assertEqual(lines[0], "file,line,text,kind,severity,suggestion")
        self.assertIn("A.MPF,3,N3,feed-zero,error,修正 F0", lines[1])

    def test_extract_program_name_priority_and_suffix_rules(self):
        from ncodeprocess.core import extract_program_name
        root = self.make_dir()
        cases = (
            ('MSG("PROGRAM:FROM_MSG")\n', "from-msg.MPF", "FROM_MSG"),          # MSG 优先
            ("PPRINT PROGNAME FROM_PPRINT\n", "x.MPF", "FROM_PPRINT"),           # PPRINT 次之
            ("", "prefix_AG6D311A0101.MPF", "AG6D311A0101"),                     # 文件名下划线取末段
            ("", "AG6D311A0101_I.MPF", "AG6D311A0101"),                          # _I 后缀剔除
        )
        for text, name, expected in cases:
            with self.subTest(name=name):
                path = root / name
                path.write_text(text, encoding="utf-8")
                self.assertEqual(extract_program_name(path), expected)
```

- [ ] **Step 3: 回归**

Run: `conda run -n python38 python -m unittest discover -s tests -v 2>&1 | Select-Object -Last 3`
Expected: `Ran 205 tests ... OK`（200 + 5）。

- [ ] **Step 4: 提交门**：`test(cli,core): 新增 CLI 全流程/CSV/程序名提取测试，消除覆盖空白`。

---

## WP-T6: 拆分大测试文件（依赖 D-D）

若 D-D=A：
- [ ] 新建 `tests/test_gui_layout.py`（`LayoutWidgetTests` + 布局相关）、`tests/test_gui_settings.py`（`SettingsDialogTests` + `ProcessingConfirmationTests` 等）、`tests/test_gui_scan.py`（`ScanLifecycleTests` + `StartupCallbackTests` + `ReportExportTests`）、`tests/test_gui_diff.py`（`DiffViewTests`）；`LayoutWidgetMixin` 随 `test_gui_layout.py` 迁移，其余文件 `from test_gui_layout import LayoutWidgetMixin`（discover 顶层导入模式下同目录模块可直接导入）。
- [ ] 删除 `tests/test_gui.py`；`tests/test_core.py` 按 `header/tools/m03/validation/plan` 拆分时保持 `CoreTests` 类名与 `make_dir`/`_cfg`/`_mpf` helper 集中（移到 `tests/support.py`）。
- [ ] 全量回归用例数不变（205），提交门：`refactor(test): 按主题拆分大测试文件`。

若 D-D=B（推荐）：本 WP 跳过，仅保留在待办清单。

---

## WP-T7: 基线文档与流程收尾（依赖 D-A/D-C）

- [ ] 按 D-A 更新 `docs/NCodeProcess-测试指南.md`：基线改为「主项目约 205 项、查看器 6 项，以 `python -m unittest discover -s tests -v` 输出为准」。
- [ ] `docs/NCodeProcess-审查与待办.md` 登记本计划全部已修复项与提交号。
- [ ] 按 D-C=B：新增 `run_tests.ps1`（`NCodeProcess/` 项目根）：

```powershell
param([string]$CondaEnvironment = 'python38')
$ErrorActionPreference = 'Stop'
Write-Host '== 主程序测试 =='
conda run -n $CondaEnvironment python -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host '== 报告查看器测试 =='
Push-Location ..\NCodeProcessReportViewer
try {
    conda run -n $CondaEnvironment python -m unittest discover -s tests -v
} finally {
    Pop-Location
}
```

- [ ] （若 D-C=A）新增 `.github/workflows/tests.yml`：`windows-latest` + `actions/setup-python@v5`（python-version 3.8）+ 两个项目分别 `python -m unittest discover -s tests -v`；注册表测试在 runner 上可直接写 HKCU。
- [ ] 提交门：`docs: 测试基线以 discover 输出为准并新增一键测试脚本`。

---

## 执行顺序与确认流程

1. Phase 0 确认 D-A~D-D（默认取建议列：D-A=B、D-B=A、D-C=B、D-D=B）。
2. 建议顺序：WP-T1 → WP-T2 → WP-T3 → WP-T4 → WP-T5 → WP-T7（T6 按 D-D 决定）。
3. 每个 WP 启动前单独向用户说明范围并确认；每个 WP 完成后全量测试绿 → 用户确认 → 提交。

## Self-Review 记录

- **Spec 覆盖**：审查结论中的继承重复执行（T1）、11 组重叠合并（T2）、tooltip flake（T3）、脆弱断言（T4）、CLI/CSV/程序名覆盖空白（T5）、大文件拆分（T6）、基线文档与流程（T7）均有对应 WP；无遗漏。
- **占位符检查**：所有合并/新增测试均给出完整代码与删除对象；命令给出精确路径与预期输出；无 TBD。
- **类型/签名一致性**：合并测试复用的 helper（`_build_app`/`_descendants`/`_collect_buttons`/`_pump_until`）在 WP-T1 定义、后续 WP 引用，名称一致；`mixin` 不含 `test_` 方法，不会改变 discover 计数；用例数推算：239 → 220（T1）→ 201（T2）→ 200（T4 删 1）→ 205（T5 增 5），与文档一致。
