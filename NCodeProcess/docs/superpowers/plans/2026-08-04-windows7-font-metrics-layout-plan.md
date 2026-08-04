# Windows 7 Font Metrics Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make NCodeProcess table headers, validation summaries, program columns, and custom tool controls render clearly on Windows 7 while preserving the current Windows 11 native theme and behavior.

**Architecture:** Keep the native ttk theme, normalize the application font family through Tk named fonts, and derive runtime column profiles from actual font measurements instead of relying only on Windows 11 pixel constants. Reuse the existing Configure/after_idle column allocator, restore an equal main grid, and keep business data separate from display-only spacing.

**Tech Stack:** Python 3.8, Tkinter/ttk, `tkinter.font`, unittest, PyInstaller 5.13.2, Windows 7/11.

---

### Task 1: Add RED tests for font-aware layout metrics

**Files:**
- Modify: `NCodeProcess/tests/test_gui.py`
- Test target: `NCodeProcess/ncodeprocess/gui.py`

- [ ] **Step 1: Add pure metric tests**

Add tests for the new public helpers:

```python
class FontAwareLayoutMetricTests(unittest.TestCase):
    def test_choose_ui_font_family_uses_first_available_candidate(self):
        self.assertEqual(
            gui.choose_ui_font_family({"Tahoma", "Microsoft YaHei"}),
            "Microsoft YaHei",
        )

    def test_font_layout_profile_expands_for_win7_style_metrics(self):
        widths = {
            "程序名": 45,
            "999 错 / 999 警": 105,
            "刀具号": 48,
        }
        profile = gui.font_layout_profile(lambda text: widths.get(text, 40))
        keep = {item[0]: item for item in profile.keep_specs}
        tool = {item[0]: item for item in profile.tool_specs}
        self.assertGreater(keep["program"][1], gui.KEEP_COLUMN_SPECS[1][1])
        self.assertGreaterEqual(profile.validation_width, 105 + 20)
        self.assertGreaterEqual(tool["number"][1], 48 + 20)
```

- [ ] **Step 2: Add bounds tests**

Verify the scale remains 1.0 for the Windows 11 baseline measure of 36 pixels for `程序名`, is capped for extreme font metrics, and preserves fixed/stretch flags.

- [ ] **Step 3: Run RED**

Run:

```powershell
conda run -n python38 python -m unittest tests.test_gui.FontAwareLayoutMetricTests -v
```

Expected: clear failures because `choose_ui_font_family` and `font_layout_profile` do not exist.

### Task 2: Implement typography selection and measured profiles

**Files:**
- Modify: `NCodeProcess/ncodeprocess/gui.py`
- Test: `NCodeProcess/tests/test_gui.py`

- [ ] **Step 1: Add immutable profile data and font helpers**

Add `UI_FONT_CANDIDATES = ("Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", "Tahoma")`, a `FontLayoutProfile` named tuple/dataclass compatible with Python 3.8, and:

```python
def choose_ui_font_family(available_families):
    available = set(available_families)
    for family in UI_FONT_CANDIDATES:
        if family in available:
            return family
    return "TkDefaultFont"
```

`font_layout_profile(measure)` must calculate a clamped scale from `measure("程序名") / 36.0`, scale the existing initial/minimum widths without changing stretch flags, and override:

```python
validation_width = max(round(82 * scale), measure("999 错 / 999 警") + 20)
tool_number_width = max(round(50 * scale), measure("刀具号") + 20)
```

- [ ] **Step 2: Configure Tk named fonts before building widgets**

Add `App._configure_typography()` before `_build()`. Select the family from `tkfont.families`, configure Tk named fonts to the chosen family at size 9, and configure `Treeview`/`Treeview.Heading` fonts plus a row height based on the actual line space. Do not change `ttk.Style.theme_use()`.

- [ ] **Step 3: Store the runtime profile**

Create the measured profile from the configured Treeview font and store `self.keep_column_specs`, `self.tool_column_specs`, and `self.validation_column_width` for widget construction.

- [ ] **Step 4: Run GREEN and full tests**

Run the focused tests, then:

```powershell
conda run -n python38 python -m unittest discover -s tests -v
```

### Task 3: Integrate Windows 7-safe table widths and equal panes

**Files:**
- Modify: `NCodeProcess/ncodeprocess/gui.py`
- Modify: `NCodeProcess/tests/test_gui.py`

- [ ] **Step 1: Add RED Tk tests**

Add a Windows 7-style test that temporarily configures the application fonts to a larger measured font, builds the App at 1600×900, and asserts:

```python
self.assertGreaterEqual(app.keep_issue_table.column("issues", "width"), expected_validation)
self.assertGreaterEqual(app.tool_table.column("number", "width"), expected_tool_number)
self.assertEqual(app.keep_table.winfo_width() + app.keep_issue_table.winfo_width() <= app.left_notebook.winfo_width(), True)
self.assertAlmostEqual(app.left_notebook.winfo_width(), app.right_detail_frame.winfo_width(), delta=2)
```

Also assert every visible heading width is at least the measured heading text plus theme padding.

- [ ] **Step 2: Use runtime profiles everywhere**

Use `self.keep_column_specs` and `self.tool_column_specs` in `_keep_tables`, tool-table setup, and `_bind_column_profile`. Set the validation Treeview column to `self.validation_column_width`. Before the first idle profile update, initialize Treeview columns with their measured minimum widths so widget request width does not force overflow.

- [ ] **Step 3: Restore equal main panes**

Configure both `content` columns with `weight=1, uniform="main"`. Store `self.left_notebook = left` and `self.right_detail_frame = right` for verification. The actual 1286×668 layout must remain inside the root even if requested widths differ.

- [ ] **Step 4: Add display-only column separation**

When inserting KEEP rows, prefix the displayed source and target strings with one normal space through a helper such as:

```python
def _display_with_gap(value):
    return " " + str(value) if value else ""
```

Do not modify `FileRecord`, filenames, processing targets, report data, or selection identifiers.

- [ ] **Step 5: Verify scrolling and resizing**

At 1286×668 and 1600×900 assert the default xview reaches 1.0, fixed columns stay fixed, stretch columns expand, and manually widening a stretch column makes the existing horizontal scrollbar usable.

### Task 4: Keep custom tool controls adjacent

**Files:**
- Modify: `NCodeProcess/ncodeprocess/gui.py`
- Modify: `NCodeProcess/tests/test_gui.py`

- [ ] **Step 1: Add RED geometry test**

Store `self.custom_tool_type_entry` and `self.add_tool_type_button`. At 1286×668, assert both are mapped and the horizontal gap between the entry right edge and button left edge is no more than 8 pixels.

- [ ] **Step 2: Group the controls**

Inside the G00/tool-options row, create a compact `custom_type_group` frame containing the label, entry, and button in adjacent columns. Leave remaining row expansion after the group, not between the entry and button. Preserve `self.new_type_var`, button text, and `command=self.add_tool_type`.

- [ ] **Step 3: Run focused and full tests**

Run all LayoutMetric/LayoutWidget tests and full discovery. Verify every button request width is no greater than its actual width.

### Task 5: Update compatibility documentation

**Files:**
- Modify: `NCodeProcess/README.md`
- Modify: `NCodeProcess/docs/NCodeProcess-用户手册.md`
- Modify: `NCodeProcess/NCodeProcess-需求文档.md`

- [ ] **Step 1: Document font/DPI-aware columns**

State that Windows 7 and Windows 11 retain native ttk themes while the program selects a compatible Chinese UI font and measures headings at runtime. Explain that validation and tool-number columns reserve content-safe widths and the main panes remain equal.

- [ ] **Step 2: Document the compact custom tool row**

Update screenshots/text descriptions as applicable so the custom tool input and add button are described as adjacent controls.

- [ ] **Step 3: Re-run Markdown UTF-8/fence checks and all tests**

### Task 6: Rebuild and publish the corrected versioned executable

**Files:**
- Use existing changes: `NCodeProcess/version_info.txt`, `NCodeProcess/VERSION.txt`, `NCodeProcess/NCodeProcess.spec`, `NCodeProcess/build_portable.ps1`
- Generated: `NCodeProcess/dist/*`, `NCodeProcess/Publish/*`, test-folder EXEs

- [ ] **Step 1: Run all tests**

Expected: the existing 53 tests plus the new Windows 7 layout tests pass.

- [ ] **Step 2: Build with AES and runtime hardening**

Run:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\build_portable.ps1 -CondaEnvironment python38
```

Confirm encryption, runtime hook, version resource, `VERSION.txt`, and `.hardened-build` cleanup.

- [ ] **Step 3: Smoke, hash, and synchronize**

Start only the new EXE PID for four seconds, stop only that PID, verify FileVersion/ProductVersion 1.0.0.0 and SHA manifests, then synchronize the current EXE/portable ZIP to `Publish`, `测试/HASS`, and `测试/V5-2500B`.

- [ ] **Step 4: Refresh legacy Publish archives without deleting the PDF**

Rebuild `Publish/无md发布/NCodeProcess.zip` with the new EXE plus the existing PDF, and `Publish/最终发布/NCodeProcess-Hardened.zip` with the new portable ZIP plus the existing PDF. Confirm no archive contains the old main-program hash.

## Final verification

Run a final independent code review, all unittests, three window profiles, Windows version-resource inspection, archive-content inspection, and SHA256 comparison. The workspace is not a Git repository, so task checkpoints are tests, probes, build logs, and exact file hashes rather than commits.
