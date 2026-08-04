# 程序设置界面（第 8 节配置 GUI 化）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 依据需求逻辑审查报告第二大点第 5 项，将需求文档第 8 节"应在程序设置界面提供"的配置项接入 GUI——本计划覆盖**第一批：GUI 化 Config 模型已支持但界面未暴露的字段**（编码、待删除扩展名、程序名允许字符、结束标记检查、M06 检查、S 转速检查、APTSOURCE 归档子目录名）。

**Architecture:** 在 `ncodeprocess/gui.py` 主窗口新增"程序设置…"按钮，打开独立 `Toplevel` 设置对话框（Win7 ttk 兼容、不可缩放、仅内存生效，不写 NC 目录）。对话框控件直接绑定到 `App` 上的 `tk.*Var`，确认时校验后回写 `App.config()`，取消则丢弃。新增配置只影响本次运行（符合需求第 8 节"配置优先保存在内存中"），编制/审核/自定义刀具类型继续走注册表，不受影响。

**Tech Stack:** Python 3.8 / Tkinter ttk / PyInstaller 5.13.2（仅打包验证用，本计划不含打包步骤）/ unittest（TDD）

**关联文档：**
- 需求依据：`NCodeProcess-需求文档.md` 第 8 节、FR-05.7、FR-07.3、7.3 布局约束
- 问题依据：`docs/2026-08-04-需求逻辑审查报告.md` 第二大点第 5 项
- 关键现状：`core.py` `Config`（L50-L71）、`gui.py` `App._build`（L522-L580）、`App.config`（L977-L978）

---

## 文件结构

- `NCodeProcess/ncodeprocess/core.py`：**不新增逻辑**（Config 字段已存在）。仅当 TDD 发现缺口时按任务补测试锁定行为。
- `NCodeProcess/ncodeprocess/gui.py`：
  - `parse_delete_extensions(raw)` —— 新模块函数，解析逗号/分号/空白分隔的扩展名并校验（`.log` 形式）
  - `App._build` —— 新增 7 个 `tk.*Var` + 顶部"程序设置…"按钮
  - `App.open_settings` / `App._confirm_settings` / `App._cancel_settings` —— 设置对话框（打开/确认/取消）
  - `App._parsed_delete_extensions` —— 从 `delete_extensions_var` 解析集合（复用模块函数）
  - `App.config` —— 将新 var 注入 Config
  - `App.finish_scan` —— 手动确认程序名的硬编码正则改用配置的允许字符模式（L1002）
- `NCodeProcess/tests/test_core.py`：新增锁定已支持配置行为测试（编码强制、删除扩展名过滤、结束标记/M06/S 开关、允许字符模式）
- `NCodeProcess/tests/test_gui.py`：新增 `SettingsDialogTests`（默认值、确认应用、取消丢弃、非法输入拦截、按钮可见性）

**Batch 2（后续批次，仅列出条目，不进入本计划执行）**：必填 MSG 字段、M03 补写位置策略、S/F 上下限、机床行程 X/Y/Z、辅助指令顺序规则、换行强制策略。这些需要先在 `Config` 增加字段并在 `core.py` 增加校验逻辑，再接入 GUI。

---

## Task 1: core 层锁定已支持配置行为（TDD 防回归）

**Files:**
- Modify: `NCodeProcess/tests/test_core.py`（新增 6 个测试，追加到 `CoreTests` 类内、`if __name__ == "__main__":` 之前）

- [x] **Step 1: 写失败/锁定测试（全部一次性写入）**

```python
    def test_forced_encoding_reads_gb18030_file(self):
        root = self.make_dir()
        path = root / "x_P.MPF"
        path.write_bytes('MSG("PROGRAM:P")\nN1X1S100M03\nN2M30\n'.encode("gb18030"))
        plan = build_plan(scan_directory(str(root), Config(encoding="gb18030")),
                          ProgramInfo("A", "B", "D", "V", "M", "C", "DATE"),
                          Config(encoding="gb18030"))
        self.assertEqual(next(f for f in plan.files if f.kind == "mpf").program, "P")

    def test_delete_extensions_config_filters_cleanup_plan(self):
        root = self.make_dir()
        (root / "x_P.MPF").write_text("N1S100M03\nN2M30\n", encoding="utf-8")
        (root / "a.LOG").write_text("log", encoding="utf-8")
        (root / "b.MOAPTIndexes").write_text("idx", encoding="utf-8")
        result = scan_directory(str(root), Config(delete_extensions={".log"}))
        kinds = {f.kind for f in result.files}
        self.assertIn("intermediate", kinds)
        sources = [f.source for f in result.files if f.kind == "intermediate"]
        self.assertEqual(sources, ["a.LOG"])
        # .MOAPTIndexes 不在白名单 → 属"其他扩展名"，完全忽略
        self.assertNotIn("b.MOAPTIndexes", [f.source for f in result.files])

    def test_end_marker_check_can_be_disabled(self):
        text = "MSG(\"PROGRAM:P\")\nN1S100M03\n"
        issues = validate_program(text, "P.MPF", "P", ProgramInfo("A", "B", "D", "V", "M", "C", "DATE"),
                                  Config(g00_level="allow", require_end_marker=False))
        self.assertFalse(any(i.kind == "end-marker" for i in issues))

    def test_m06_requirement_can_be_enabled(self):
        text = "MSG(\"PROGRAM:P\")\nN1T1\nN2S100M03\nN3M30\n"
        issues = validate_program(text, "P.MPF", "P", ProgramInfo("A", "B", "D", "V", "M", "C", "DATE"),
                                  Config(g00_level="allow", require_m06=True))
        self.assertTrue(any(i.kind == "tool-change" and i.severity == "error" for i in issues))

    def test_spindle_speed_requirement_can_be_enabled(self):
        text = "MSG(\"PROGRAM:P\")\nN1G1X1\nN2M30\n"
        issues = validate_program(text, "P.MPF", "P", ProgramInfo("A", "B", "D", "V", "M", "C", "DATE"),
                                  Config(g00_level="allow", require_spindle_speed=True, auto_m03=False))
        self.assertTrue(any(i.kind == "spindle-speed" and i.severity == "error" for i in issues))

    def test_allowed_name_pattern_controls_program_extraction(self):
        root = self.make_dir()
        path = root / "程序_P.MPF"
        path.write_text('MSG("PROGRAM:P")\nN1S100M03\nN2M30\n', encoding="utf-8")
        strict = Config(allowed_name_pattern=r"^[A-Za-z0-9]+$")
        plan = build_plan(scan_directory(str(root), strict), ProgramInfo("A", "B", "D", "V", "M", "C", "DATE"), strict)
        mpf = next(f for f in plan.files if f.kind == "mpf")
        self.assertEqual(mpf.program, "P")  # 来自 MSG PROGRAM 字段，与文件名无关
        self.assertEqual(Path(mpf.target).name, "P.MPF")
```

- [x] **Step 2: 运行确认失败（或证明缺口）**

Run:
```powershell
conda run -n python38 python -m unittest tests.test_core.CoreTests.test_forced_encoding_reads_gb18030_file tests.test_core.CoreTests.test_delete_extensions_config_filters_cleanup_plan tests.test_core.CoreTests.test_end_marker_check_can_be_disabled tests.test_core.CoreTests.test_m06_requirement_can_be_enabled tests.test_core.CoreTests.test_spindle_speed_requirement_can_be_enabled tests.test_core.CoreTests.test_allowed_name_pattern_controls_program_extraction -v
```
Expected: 若某项意外 PASS 说明行为已存在（则保留该测试为锁定测试，不计 RED）；若 FAIL 说明存在缺口，转入 Step 3 修 core。

- [x] **Step 3: 修复 core 缺口（仅当 Step 2 有 FAIL 时执行）**

按 FAIL 的具体断言修改 `core.py`（例如 `scan_directory` 的扩展名过滤、`validate_program` 的开关逻辑）。**若全部 PASS，跳过本步。**

- [x] **Step 4: 全量 core 测试通过**

Run:
```powershell
conda run -n python38 python -m unittest tests.test_core -v
```
Expected: 全绿。

- [x] **Step 5: 提交**

```powershell
git add NCodeProcess/ncodeprocess/core.py NCodeProcess/tests/test_core.py
git commit -m "test: 锁定编码/删除扩展名/结束标记/M06/S转速/允许字符配置行为"
```

---

## Task 2: gui.py 新增 `parse_delete_extensions` 模块函数

**Files:**
- Modify: `NCodeProcess/ncodeprocess/gui.py`（模块级函数，放在 `DEFAULT_TOOL_TYPES`（L34）之后）

- [x] **Step 1: 写失败测试（追加到 test_gui.py 顶部，`from ncodeprocess.gui import (...)` 增加导入）**

```python
    def test_parse_delete_extensions_normalizes_and_validates(self):
        self.assertEqual(gui.parse_delete_extensions(".LOG, .moaptindexes"), {".log", ".moaptindexes"})
        self.assertEqual(gui.parse_delete_extensions(""), set())
        self.assertEqual(gui.parse_delete_extensions(".log；.txt"), {".log", ".txt"})
        with self.assertRaises(ValueError):
            gui.parse_delete_extensions("log")  # 缺前导点
        with self.assertRaises(ValueError):
            gui.parse_delete_extensions(".bad_ext;")  # 含非法字符
```

放置：新增 `class SettingsDialogTests(unittest.TestCase):` 放在 `LayoutWidgetTests` 之前，测试类内第一个方法就是上面这个；同时 `test_gui.py` 第 10-15 行的 import 列表增加 `parse_delete_extensions`。

- [x] **Step 2: 运行确认失败**

Run:
```powershell
conda run -n python38 python -m unittest tests.test_gui.SettingsDialogTests.test_parse_delete_extensions_normalizes_and_validates -v
```
Expected: FAIL with "AttributeError: module 'ncodeprocess.gui' has no attribute 'parse_delete_extensions'"。

- [x] **Step 3: 实现**

```python
def parse_delete_extensions(raw: str) -> set:
    """Normalize a comma/semicolon/whitespace separated extension list."""
    parts = [p.strip().lower() for p in re.split(r"[,;，；\s]+", raw or "") if p.strip()]
    for part in parts:
        if not re.match(r"^\.[a-z0-9]+$", part):
            raise ValueError(f"扩展名格式无效：{part}（应为 .log 形式，逗号分隔）")
    return set(parts)
```

- [x] **Step 4: 运行确认通过**

Run: 同 Step 2 命令。Expected: PASS。

- [x] **Step 5: 提交**

```powershell
git add NCodeProcess/ncodeprocess/gui.py NCodeProcess/tests/test_gui.py
git commit -m "feat(gui): 增加待删除扩展名解析函数 parse_delete_extensions"
```

---

## Task 3: 主窗口新增配置变量与"程序设置…"按钮

**Files:**
- Modify: `NCodeProcess/ncodeprocess/gui.py` 的 `App._build`（约 L522-L580）

- [x] **Step 1: 写失败测试（追加到 `SettingsDialogTests`）**

```python
    def test_settings_button_and_vars_exist(self):
        root, app = self._build_app(1286, 668)
        try:
            self.assertTrue(app.settings_button.winfo_ismapped())
            self.assertEqual(app.encoding_var.get(), "auto")
            self.assertEqual(app.delete_extensions_var.get(), ".log, .moaptindexes")
            self.assertEqual(app.allowed_name_pattern_var.get(), r"^[A-Za-z0-9_一-鿿-]+$")
            self.assertEqual(app.aptsource_dir_var.get(), "aptsource")
            self.assertTrue(app.require_end_marker_var.get())
            self.assertFalse(app.require_m06_var.get())
            self.assertFalse(app.require_spindle_speed_var.get())
        finally:
            root.destroy()
```

注意：`SettingsDialogTests` 需要复用 `LayoutWidgetTests._build_app`——把 `_build_app` 与 `_column_total` 提升为模块级函数 `_build_app(root, width, height)`（或让 `SettingsDialogTests` 继承 `LayoutWidgetTests`）。**推荐继承**：`class SettingsDialogTests(LayoutWidgetTests):`，这样 `_build_app` 自动可用。

- [x] **Step 2: 运行确认失败**

Run:
```powershell
conda run -n python38 python -m unittest tests.test_gui.SettingsDialogTests.test_settings_button_and_vars_exist -v
```
Expected: FAIL with "AttributeError: ... has no attribute 'settings_button'"。

- [x] **Step 3: 实现（_build 内）**

在 L550-L553 现有 var 定义旁新增：

```python
        self.encoding_var = tk.StringVar(value="auto")
        self.delete_extensions_var = tk.StringVar(value=".log, .moaptindexes")
        self.allowed_name_pattern_var = tk.StringVar(value=r"^[A-Za-z0-9_一-鿿-]+$")
        self.aptsource_dir_var = tk.StringVar(value="aptsource")
        self.require_end_marker_var = tk.BooleanVar(value=True)
        self.require_m06_var = tk.BooleanVar(value=False)
        self.require_spindle_speed_var = tk.BooleanVar(value=False)
```

在顶部"自动扫描目录"帧（L523-L531）的目录标签后加按钮：

```python
        self.settings_button = ttk.Button(top, text="程序设置…", command=self.open_settings)
        self.settings_button.pack(side="left", padx=8)
```

（放在 L526 `ttk.Label(top, text=str(self.workdir)).pack(...)` 之后，左侧布局，不挤占右侧复选框。）

- [x] **Step 4: 运行确认通过**

Run: 同 Step 2。Expected: PASS。

- [x] **Step 5: 提交**

```powershell
git add NCodeProcess/ncodeprocess/gui.py NCodeProcess/tests/test_gui.py
git commit -m "feat(gui): 主窗口新增程序设置按钮与配置变量"
```

---

## Task 4: 设置对话框（打开/确认/取消）

**Files:**
- Modify: `NCodeProcess/ncodeprocess/gui.py`（`App` 类内新增 3 个方法，放在 `apply_info`（L959）之前）

- [x] **Step 1: 写失败测试**

```python
    def test_settings_dialog_opens_and_confirm_applies(self):
        root, app = self._build_app(1286, 668)
        try:
            with patch.object(App, "scan") as scan_mock:
                app.open_settings()
                self.assertIsNotNone(app.settings_window)
                app.encoding_var.set("gb18030")
                app.delete_extensions_var.set(".log")
                app.require_m06_var.set(True)
                app.require_end_marker_var.set(False)
                app._confirm_settings()
                self.assertIsNone(app.settings_window)
                config = app.config()
                self.assertEqual(config.encoding, "gb18030")
                self.assertEqual(config.delete_extensions, {".log"})
                self.assertTrue(config.require_m06)
                self.assertFalse(config.require_end_marker)
                scan_mock.assert_called_once_with()
        finally:
            root.destroy()

    def test_settings_dialog_cancel_discards(self):
        root, app = self._build_app(1286, 668)
        try:
            app.open_settings()
            app.encoding_var.set("gb18030")
            app._cancel_settings()
            self.assertEqual(app.config().encoding, "auto")
        finally:
            root.destroy()

    def test_settings_dialog_rejects_invalid_values(self):
        root, app = self._build_app(1286, 668)
        try:
            app.open_settings()
            with patch("ncodeprocess.gui.messagebox.showerror") as err_mock:
                app.delete_extensions_var.set("txt")
                app._confirm_settings()
                err_mock.assert_called_once()
            self.assertIsNotNone(app.settings_window)  # 对话框未关闭
            self.assertEqual(app.config().delete_extensions, {".log", ".moaptindexes"})

            app.allowed_name_pattern_var.set("[")
            with patch("ncodeprocess.gui.messagebox.showerror") as err_mock:
                app._confirm_settings()
                err_mock.assert_called_once()
            self.assertIsNotNone(app.settings_window)
        finally:
            root.destroy()
```

- [x] **Step 2: 运行确认失败**

Run:
```powershell
conda run -n python38 python -m unittest tests.test_gui.SettingsDialogTests.test_settings_dialog_opens_and_confirm_applies tests.test_gui.SettingsDialogTests.test_settings_dialog_cancel_discards tests.test_gui.SettingsDialogTests.test_settings_dialog_rejects_invalid_values -v
```
Expected: FAIL（`App` 无 `open_settings`/`_confirm_settings`/`_cancel_settings`/`settings_window`）。

- [x] **Step 3: 实现（三个方法 + `_parsed_delete_extensions`）**

```python
    def open_settings(self):
        if getattr(self, "settings_window", None) is not None and self.settings_window.winfo_exists():
            self.settings_window.lift()
            return
        win = tk.Toplevel(self.master)
        win.title("程序设置")
        win.transient(self.master)
        win.resizable(False, False)
        body = ttk.Frame(win, padding=10)
        body.pack(fill="both", expand=True)

        def labeled(row, text, widget):
            ttk.Label(body, text=text).grid(row=row, column=0, sticky="w", padx=(0, 6), pady=3)
            widget.grid(row=row, column=1, sticky="w", pady=3)

        encoding_combo = ttk.Combobox(body, textvariable=self.encoding_var, state="readonly", width=16,
                                      values=("auto", "utf-8", "utf-8-sig", "gb18030", "cp1252"))
        labeled(0, "文件编码", encoding_combo)
        ttk.Label(body, text="自动识别或强制指定").grid(row=0, column=2, sticky="w", padx=(6, 0))

        delete_entry = ttk.Entry(body, textvariable=self.delete_extensions_var, width=24)
        labeled(1, "待删除扩展名", delete_entry)
        ttk.Button(body, text="恢复默认", command=lambda: self.delete_extensions_var.set(".log, .moaptindexes")).grid(row=1, column=2, padx=(6, 0))
        ttk.Label(body, text="逗号分隔，如 .log,.moaptindexes；留空则全部保留").grid(row=2, column=1, columnspan=2, sticky="w")

        pattern_entry = ttk.Entry(body, textvariable=self.allowed_name_pattern_var, width=24)
        labeled(3, "程序名允许字符", pattern_entry)
        ttk.Button(body, text="恢复默认", command=lambda: self.allowed_name_pattern_var.set(r"^[A-Za-z0-9_一-鿿-]+$")).grid(row=3, column=2, padx=(6, 0))

        apt_entry = ttk.Entry(body, textvariable=self.aptsource_dir_var, width=24)
        labeled(4, "APTSOURCE 归档子目录", apt_entry)

        ttk.Checkbutton(body, text="要求程序结束标记（%/M30/M02）", variable=self.require_end_marker_var).grid(row=5, column=0, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Checkbutton(body, text="要求刀具调用包含 M06", variable=self.require_m06_var).grid(row=6, column=0, columnspan=3, sticky="w")
        ttk.Checkbutton(body, text="要求切削前有 S 转速", variable=self.require_spindle_speed_var).grid(row=7, column=0, columnspan=3, sticky="w")

        actions = ttk.Frame(win, padding=(10, 0, 10, 10))
        actions.pack(fill="x")
        ttk.Button(actions, text="确定", command=self._confirm_settings).pack(side="right")
        ttk.Button(actions, text="取消", command=self._cancel_settings).pack(side="right", padx=(0, 8))
        win.bind("<Return>", lambda _event: self._confirm_settings())
        win.bind("<Escape>", lambda _event: self._cancel_settings())
        self.settings_window = win

    def _parsed_delete_extensions(self):
        return parse_delete_extensions(self.delete_extensions_var.get())

    def _confirm_settings(self):
        try:
            self._parsed_delete_extensions()
            re.compile(self.allowed_name_pattern_var.get().strip())
        except (ValueError, re.error) as error:
            messagebox.showerror("程序设置无效", str(error), parent=self.settings_window)
            return
        self.settings_window.destroy()
        self.settings_window = None
        self.scan()

    def _cancel_settings(self):
        self.settings_window.destroy()
        self.settings_window = None
```

- [x] **Step 4: 运行确认通过**

Run: 同 Step 2 命令 + 全量 `tests.test_gui -v`。
Expected: 全部 PASS（含既有 80 余项 GUI 测试不回归）。

- [x] **Step 5: 提交**

```powershell
git add NCodeProcess/ncodeprocess/gui.py NCodeProcess/tests/test_gui.py
git commit -m "feat(gui): 程序设置对话框（确认应用/取消丢弃/非法输入拦截）"
```

---

## Task 5: `App.config` 注入新配置 + `finish_scan` 使用配置的允许字符

**Files:**
- Modify: `NCodeProcess/ncodeprocess/gui.py` `App.config`（L977-L978）与 `App.finish_scan`（L1002）

- [x] **Step 1: 写失败测试**

```python
    def test_config_injects_all_new_settings(self):
        root, app = self._build_app(1286, 668)
        try:
            app.encoding_var.set("gb18030")
            app.delete_extensions_var.set(".log")
            app.allowed_name_pattern_var.set(r"^[A-Za-z0-9]+$")
            app.aptsource_dir_var.set("archive")
            app.require_end_marker_var.set(False)
            app.require_m06_var.set(True)
            app.require_spindle_speed_var.set(True)
            config = app.config()
            self.assertEqual(config.encoding, "gb18030")
            self.assertEqual(config.delete_extensions, {".log"})
            self.assertEqual(config.allowed_name_pattern, r"^[A-Za-z0-9]+$")
            self.assertEqual(config.aptsource_dir, "archive")
            self.assertFalse(config.require_end_marker)
            self.assertTrue(config.require_m06)
            self.assertTrue(config.require_spindle_speed)
        finally:
            root.destroy()
```

- [x] **Step 2: 运行确认失败**

Run:
```powershell
conda run -n python38 python -m unittest tests.test_gui.SettingsDialogTests.test_config_injects_all_new_settings -v
```
Expected: FAIL（`config()` 未传新字段，断言不等）。

- [x] **Step 3: 实现**

将 `App.config`（L977-L978）替换为：

```python
    def config(self):
        return Config(
            recursive=self.recursive.get(),
            save_aptsource=self.save_aptsource.get(),
            aptsource_dir=self.aptsource_dir_var.get().strip() or "aptsource",
            overwrite_fields=self.overwrite_fields.get(),
            auto_m03=self.auto_m03.get(),
            auto_tool_change=self.auto_tool_change.get(),
            defer_stats=False,
            g00_level=self.g00_level.get(),
            delete_extensions=self._parsed_delete_extensions(),
            allowed_name_pattern=self.allowed_name_pattern_var.get().strip(),
            encoding=self.encoding_var.get().strip(),
            require_end_marker=self.require_end_marker_var.get(),
            require_m06=self.require_m06_var.get(),
            require_spindle_speed=self.require_spindle_speed_var.get(),
        )
```

将 `finish_scan` L1002 的硬编码正则替换为配置模式：

```python
            pattern = self.allowed_name_pattern_var.get().strip()
            if value and re.match(pattern, value.strip()):
                f.program = value.strip()
```

（`pattern` 默认值等价于原 `^[A-Za-z0-9_\u4e00-\u9fff-]+$`，行为不变；用户收紧模式后手动确认也按新模式校验。）

- [x] **Step 4: 运行确认通过**

Run:
```powershell
conda run -n python38 python -m unittest discover -s tests -v
```
Expected: 全部测试通过（core 82 项 + GUI 既有 + 本批新增）。

- [x] **Step 5: 提交**

```powershell
git add NCodeProcess/ncodeprocess/gui.py NCodeProcess/tests/test_gui.py
git commit -m "feat(gui): config() 注入编码/扩展名/允许字符/校验开关，手动确认程序名改用配置模式"
```

---

## Task 6: 布局回归验证（Win7 1286×668 不溢出）

**Files:**
- Modify: `NCodeProcess/tests/test_gui.py`（`SettingsDialogTests` 内新增）

- [x] **Step 1: 写测试**

```python
    def test_settings_dialog_fits_1286_and_controls_visible(self):
        root, app = self._build_app(1286, 668)
        try:
            app.open_settings()
            win = app.settings_window
            win.update_idletasks()
            self.assertLessEqual(win.winfo_reqwidth(), 640)
            self.assertLessEqual(win.winfo_reqheight(), 420)
            for text in ("确定", "取消"):
                self.assertTrue(any(
                    child.winfo_class() == "TButton" and child.cget("text") == text
                    for child in win.winfo_children()
                ))
        finally:
            root.destroy()
```

- [x] **Step 2: 运行确认失败**

Run:
```powershell
conda run -n python38 python -m unittest tests.test_gui.SettingsDialogTests.test_settings_dialog_fits_1286_and_controls_visible -v
```
Expected: 若对话框实现尺寸超限则 FAIL，否则 PASS（该测试同时作为锁定）。按 FAIL 时调整 `open_settings` 中控件宽度/字号。

- [x] **Step 3: 全量回归 + 提交**

Run:
```powershell
conda run -n python38 python -m unittest discover -s tests -v
```
Expected: 全绿。

```powershell
git add NCodeProcess/tests/test_gui.py
git commit -m "test(gui): 程序设置对话框 1286 布局回归锁定"
```

---

## Batch 2 预告（不在本计划执行范围）

需求第 8 节剩余项需先在 `core.py` 扩展 `Config` 并新增校验逻辑，再接入同一对话框。建议单独计划实施：

1. **必填 MSG 字段**：`Config.required_fields: List[str]`（默认 = `FIELD_ORDER` 全部键）；`validate_program`/`apply_header` 改用该字段；GUI 用逗号输入或多选。
2. **M03 补写位置策略**（FR-05.7）：`Config.m03_position: "after-s" | "standalone"`；`add_m03` 按策略插值；`require_spindle_speed` 联动；GUI Combobox。
3. **S/F 上下限**：`Config.feed_min/feed_max/spindle_min/spindle_max: Optional[float]`；`validate_program` 越界报错；GUI 4 个数值输入。
4. **机床行程 X/Y/Z**（FR-07.3）：`Config.machine_limits: Dict[str, Tuple[float, float]]`；`validate_program` 越程报错、未配置输出 info"未执行行程检查"；GUI 每轴 min/max + 启用复选框。
5. **辅助指令顺序规则**（FR-07.3）：需先与需求方确认具体规则（M03→切削、M05 于 M02 前、M08 启用时机等），再设计配置结构与校验。
6. **换行强制策略**：`Config.newline: "auto" | "crlf" | "lf"`；`read_text`/`_atomic_write` 按策略处理；GUI Combobox。

---

## 自检（Self-Review）

- **需求覆盖**：第 8 节中"递归/保存 aptsource/G00 级别/覆盖已有值"GUI 已支持（不重复做）；本计划覆盖"待删除扩展名、程序名允许字符、编码、结束标记、M06、S 转速、APTSOURCE 子目录名"7 项 GUI 化；Batch 2 覆盖"必填字段、M03 策略、S/F 上下限、机床行程、辅助指令顺序、换行策略"6 项。需求"EXE 所在目录默认输入输出"为既有机制，无需改动。
- **占位符扫描**：全部代码步骤含完整代码与预期输出，无 TBD。
- **类型一致性**：`parse_delete_extensions`、`_parsed_delete_extensions`、`_confirm_settings`、`_cancel_settings`、`settings_window`、7 个新 `Var` 在 Task 2-6 中命名一致；`Config` 字段名与 `core.py` 现有定义一致。
- **回归风险**：`config()` 新增字段均为 `Config` 已有参数（关键字安全）；`finish_scan` 模式替换前后语义等价；顶部按钮布局经 Task 6 锁定。
