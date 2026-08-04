# Windows 7 响应式界面布局 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 NCodeProcess 在 Windows 7 1366×768、100% DPI 下的界面溢出、按钮隐藏、表头截断和分隔条闪动问题，并保证默认状态无需滚动即可看全主要表头。

**Architecture:** 将顶部固定 14 列网格拆为独立响应式行；将两个 PanedWindow 改为等权重 Frame 网格；将窗口尺寸和 Treeview 列宽计算提取为纯函数，使用 Configure 事件在窗口缩放时更新可伸缩列。业务扫描、解析、处理和报告逻辑不变。

**Tech Stack:** Python 3.8、Tkinter/ttk、unittest、PyInstaller 5.13.2、Windows 7 100% DPI。

---

## Task 1: 固化窗口尺寸与列宽纯函数的失败测试

**Files:**
- Modify: `NCodeProcess/tests/test_gui.py`
- Test target: `NCodeProcess/ncodeprocess/gui.py`（此任务暂不实现生产函数）

- [ ] **Step 1: 添加窗口尺寸测试**

在 `test_gui.py` 增加：

```python
from ncodeprocess.gui import fit_column_widths, window_geometry_for_screen


class LayoutMetricTests(unittest.TestCase):
    def test_supported_screen_geometry(self):
        self.assertEqual(window_geometry_for_screen(1366, 768), (1286, 668, 1180, 650))
        self.assertEqual(window_geometry_for_screen(1920, 1080), (1600, 900, 1180, 650))

    def test_smaller_screen_does_not_request_more_than_screen(self):
        width, height, min_width, min_height = window_geometry_for_screen(1024, 600)
        self.assertLessEqual(width, 1024)
        self.assertLessEqual(height, 600)
        self.assertEqual(min_width, width)
        self.assertEqual(min_height, height)
```

- [ ] **Step 2: 添加列宽压缩/扩展测试**

```python
    def test_fit_column_widths_shrinks_to_available_width(self):
        specs = (("fixed", 100, 80, False), ("stretch", 220, 120, True))
        result = fit_column_widths(300, specs)
        self.assertEqual(sum(result.values()), 300)
        self.assertGreaterEqual(result["fixed"], 80)
        self.assertGreaterEqual(result["stretch"], 120)

    def test_fit_column_widths_assigns_extra_space_only_to_stretch_columns(self):
        specs = (("fixed", 100, 80, False), ("stretch", 220, 120, True))
        result = fit_column_widths(400, specs)
        self.assertEqual(result["fixed"], 100)
        self.assertEqual(result["stretch"], 300)

    def test_table_profiles_fit_minimum_layout(self):
        self.assertLessEqual(sum(item[1] for item in KEEP_COLUMN_SPECS) + 82, 700)
        self.assertLessEqual(sum(item[1] for item in TOOL_COLUMN_SPECS) + 230, 620)
```

同时从 `ncodeprocess.gui` 导入 `KEEP_COLUMN_SPECS` 和 `TOOL_COLUMN_SPECS`，让测试明确绑定界面列宽配置。

- [ ] **Step 3: 运行测试确认 RED**

运行：

```powershell
conda run -n python38 python -m unittest tests.test_gui.LayoutMetricTests -v
```

预期：测试因 `window_geometry_for_screen`、`fit_column_widths` 和列配置尚未存在而失败，确认失败原因是目标布局接口缺失而不是导入或语法错误。

## Task 2: 实现布局纯函数并验证 GREEN

**Files:**
- Modify: `NCodeProcess/ncodeprocess/gui.py`
- Test: `NCodeProcess/tests/test_gui.py`

- [ ] **Step 1: 添加布局常量和窗口尺寸函数**

在 `DEFAULT_TOOL_TYPES` 后添加：

```python
KEEP_COLUMN_SPECS = (
    ("action", 58, 52, False),
    ("program", 112, 96, False),
    ("source", 205, 130, True),
    ("target", 150, 105, True),
)
TOOL_COLUMN_SPECS = (
    ("number", 50, 48, False),
    ("dia", 50, 48, False),
    ("coner", 82, 76, True),
    ("angle", 84, 78, True),
    ("type", 104, 88, True),
)


def window_geometry_for_screen(screen_width, screen_height):
    supported = screen_width >= 1366 and screen_height >= 768
    if supported:
        width = min(1600, max(1180, screen_width - 80))
        height = min(900, max(650, screen_height - 100))
        return width, height, 1180, 650
    width = min(1600, max(900, screen_width - 40))
    height = min(900, max(560, screen_height - 60))
    return width, height, width, height
```

- [ ] **Step 2: 添加确定性列宽分配函数**

```python
def fit_column_widths(available_width, specs):
    widths = {name: initial for name, initial, _minimum, _stretch in specs}
    if available_width <= 0:
        return widths
    minimums = {name: minimum for name, _initial, minimum, _stretch in specs}
    total = sum(widths.values())
    if available_width < total:
        deficit = total - available_width
        for group in (
            [name for name, _initial, _minimum, stretch in specs if stretch],
            [name for name, _initial, _minimum, _stretch in specs],
        ):
            while deficit and any(widths[name] > minimums[name] for name in group):
                for name in group:
                    if deficit and widths[name] > minimums[name]:
                        widths[name] -= 1
                        deficit -= 1
        return widths
    extra = available_width - total
    stretch = [name for name, _initial, _minimum, is_stretch in specs if is_stretch]
    if stretch:
        share, remainder = divmod(extra, len(stretch))
        for index, name in enumerate(stretch):
            widths[name] += share + (1 if index < remainder else 0)
    return widths
```

- [ ] **Step 3: 运行布局测试确认 GREEN**

运行相同命令，预期 `LayoutMetricTests` 全部通过。

## Task 3: 替换固定窗口和 PanedWindow 结构

**Files:**
- Modify: `NCodeProcess/ncodeprocess/gui.py`
- Test: `NCodeProcess/tests/test_gui.py`

- [ ] **Step 1: 添加失败的 Tk 集成测试**

增加一个测试，隐藏根窗口、阻止自动扫描、设置 1286×668，并检查控件请求尺寸和 PanedWindow 数量：

```python
class LayoutWidgetTests(unittest.TestCase):
    def test_minimum_layout_has_no_panedwindow_and_fits_width(self):
        root = tk.Tk()
        root.withdraw()
        try:
            with patch.object(App, "scan", lambda _self: None):
                app = App(root)
            root.geometry("1286x668")
            root.update_idletasks()
            classes = []
            pending = list(root.winfo_children())
            while pending:
                widget = pending.pop()
                classes.append(widget.winfo_class())
                pending.extend(widget.winfo_children())
            self.assertNotIn("TPanedwindow", classes)
            self.assertLessEqual(app.winfo_reqwidth(), 1286)
            self.assertLessEqual(app.winfo_reqheight(), 668)
        finally:
            root.destroy()
```

- [ ] **Step 2: 运行测试确认当前布局失败**

运行：

```powershell
conda run -n python38 python -m unittest tests.test_gui.LayoutWidgetTests -v
```

预期：当前布局报告 `TPanedwindow` 存在且请求宽度约 1947 像素，证明截图问题可以自动复现。

- [ ] **Step 3: 修改 `_configure_window_size` 使用纯函数**

调用 `window_geometry_for_screen` 设置 geometry 和 `minsize`，不要把最小高度固定为 780：

```python
width, height, min_width, min_height = window_geometry_for_screen(
    self.master.winfo_screenwidth(), self.master.winfo_screenheight()
)
self.master.geometry(f"{width}x{height}+{x}+{y}")
self.master.minsize(min_width, min_height)
```

- [ ] **Step 4: 删除分隔条定位，改用等权重网格**

将 `_present_window` 简化为 `update_idletasks` 后 `deiconify`，删除 `_set_initial_split_positions` 调用。将 `_build` 中的主布局替换为：

```python
content = ttk.Frame(self)
content.pack(fill="both", expand=True)
content.columnconfigure(0, weight=1, uniform="main")
content.columnconfigure(1, weight=1, uniform="main")
left = ttk.Notebook(content)
right = ttk.Frame(content)
left.grid(row=0, column=0, sticky="nsew", padx=(0, 3))
right.grid(row=0, column=1, sticky="nsew", padx=(3, 0))
right.rowconfigure(0, weight=1, uniform="detail")
right.rowconfigure(1, weight=1, uniform="detail")
notebook.grid(row=0, column=0, sticky="nsew")
tool_frame.grid(row=1, column=0, sticky="nsew", pady=(4, 0))
```

Store `self.detail_notebook` as before, but no longer expose `self.main_split` or `self.detail_split` as PanedWindow instances.

- [ ] **Step 5: Set low request dimensions for fill widgets and run the integration test**

Set `ttk.Treeview(..., height=1)` in `_table` and `_keep_tables`, and `tk.Text(..., width=1, height=1)` in `_text_with_scrollbars`. Run `LayoutWidgetTests`; expected result is PASS with no `TPanedwindow` and requested width/height within the target.

## Task 4: Rebuild top information rows

**Files:**
- Modify: `NCodeProcess/ncodeprocess/gui.py`
- Test: `NCodeProcess/tests/test_gui.py`

- [ ] **Step 1: Add a failing widget-presence test**

Store `self.drawing_choice_button`, `self.folder_choice_combo`, and `self.process_info_frame` during construction. Add a test that sets the root to 1286×668, calls `update_idletasks`, and asserts each widget has a positive width and lies within the root right edge.

- [ ] **Step 2: Replace the 14-column `info` grid**

Implement four nested rows: five equal basic fields, processing options, G00/custom type, and drawing choice. Configure candidate row columns `(label fixed, combo weight 1, button fixed)` and use `sticky="ew"` on the combobox. Keep exact existing command callbacks and variable names.

- [ ] **Step 3: Run the focused widget test**

```powershell
conda run -n python38 python -m unittest tests.test_gui.LayoutWidgetTests -v
```

Expected: all top-level controls remain mapped and inside the 1286-pixel client width.

## Task 5: Apply adaptive columns to program and tool tables

**Files:**
- Modify: `NCodeProcess/ncodeprocess/gui.py`
- Test: `NCodeProcess/tests/test_gui.py`

- [ ] **Step 1: Add a failing column-profile integration test**

After creating the app at 1286×668, call `update_idletasks` and assert the sum of visible widths for the program table plus its validation column is no greater than the left frame width, and the tool table plus editor is no greater than the right frame width.

- [ ] **Step 2: Extend `_table` with profile-aware sizing**

Add `_bind_column_profile(table, specs, available_width_callback)` that schedules one `after_idle` update per Configure burst and applies `fit_column_widths`. Keep horizontal scrollbar wiring unchanged. Use `KEEP_COLUMN_SPECS` and `TOOL_COLUMN_SPECS` for the two affected areas.

- [ ] **Step 3: Compact the keep table and preserve validation coloring**

Use `KEEP_COLUMN_SPECS` for the main table and keep the validation Treeview at 82 pixels. Keep `validation-error` and `validation-warning` tags unchanged. Source and target remain stretchable; action, program and validation stay compact.

- [ ] **Step 4: Compact and grid the tool editor**

Use `TOOL_COLUMN_SPECS`, set editor minimum width to 230, configure label/entry columns with weights, and make all three buttons `sticky="ew"`. Do not alter `load_tool_editor`, `upsert_tool`, `delete_tool` or persistence behavior.

- [ ] **Step 5: Run focused and full tests**

```powershell
conda run -n python38 python -m unittest tests.test_gui.LayoutMetricTests tests.test_gui.LayoutWidgetTests -v
conda run -n python38 python -m unittest discover -s tests -v
```

Expected: new layout tests and all existing 35 tests pass.

## Task 6: Add visual/runtime verification and documentation updates

**Files:**
- Modify: `NCodeProcess/README.md`
- Modify: `NCodeProcess/docs/NCodeProcess-用户手册.md`
- Modify: `NCodeProcess/NCodeProcess-需求文档.md`

- [ ] **Step 1: Document the supported minimum layout**

Add the exact 1366×768/100% DPI guarantee, the fact that splitters are no longer draggable, and the behavior of horizontal scrollbars for manually expanded long-text columns.

- [ ] **Step 2: Run source launch checks at three screen profiles**

Use the existing Python 3.8 environment with a hidden root and the layout test helper to run 1366×768, 1600×900 and 1920×1080 profiles. Record requested size, actual size, right edge of the drawing button, and table header widths. Every supported profile must have all controls inside the client width.

- [ ] **Step 3: Run all tests**

```powershell
conda run -n python38 python -m unittest discover -s tests -v
```

Expected: 35 existing tests plus the new layout tests pass.

## Task 7: Rebuild and verify the main executable

**Files:**
- Modify through generated artifacts: `NCodeProcess/dist/*`, `NCodeProcess/Publish/*`

- [ ] **Step 1: Build the main program**

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\build_portable.ps1 -CondaEnvironment python38
```

Run from `NCodeProcess`. Confirm exit code 0, AES bytecode encryption log, and no `.hardened-build` directory after completion.

- [ ] **Step 2: Run packaged smoke check**

Start `NCodeProcess.exe` for four seconds from its `dist` folder, confirm the process remains alive, then close only that exact process. Verify the package contains the EXE, README, SECURITY.md and SHA256SUMS.txt.

- [ ] **Step 3: Run final test and hash verification**

```powershell
conda run -n python38 python -m unittest discover -s tests -v
Get-FileHash .\dist\NCodeProcess.exe -Algorithm SHA256
Get-Content .\dist\NCodeProcess-Package\SHA256SUMS.txt
```

Confirm the recorded hash matches the executable, then synchronize the new main EXE and portable archive to the existing main-program test and Publish locations.

## Checkpoint and handoff

The workspace is not currently a Git repository, so commit steps cannot be executed. Each task therefore ends with a focused test or build checkpoint. After Task 7, provide the new EXE paths, test counts, supported minimum layout, and any Windows 7-specific limitations.
