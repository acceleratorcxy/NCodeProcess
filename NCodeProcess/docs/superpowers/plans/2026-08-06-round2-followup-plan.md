# NCodeProcess 第三轮收尾实施计划（2026-08-06 复审后）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 收尾 2026-08-06 全面复审发现的剩余问题：消除「全部应用」UI 冻结、补全运行日志 traceback 埋点与截断去重、查看器图表容错、分号语义文档化、构建脚本缩进；按 D2 决定是否顺带完成查看器增强（WP-13~15）与版本提升发版（WP-16）。

**Architecture:** 全部改动延续现有分层。核心改动在 `gui.py`（apply_info 后台重处理）、`core.py`（运行事件埋点 + RuntimeLog 截断去重）、`viewer.py`（图表数值容错）；文档类 WP 不改行为；发版 WP 仅同步版本号与发布说明。每个 WP 保持行为零回退，以 245 项主测试 + 10 项查看器测试全绿为前提。

**Tech Stack:** Python 3.8 / Tkinter / unittest / PyInstaller 5.13.2 + UPX 3.96（仅构建期）。

**执行纪律（AGENTS.md 强制）：**
- 每个 WP 启动前向用户说明范围、涉及文件、验收标准并取得确认。
- 每个 WP 完成后：全量测试绿 → `build_portable.ps1` 打包 → 用户实测确认 → 才允许 git 提交（测试类 WP 可不打包）。
- 提交信息使用 `perf:` / `fix:` / `feat:` / `refactor:` / `docs:` 前缀 + 中文描述。
- 代码/UI/配置改动同步更新受影响文档：需求文档、报告内容规范、发布说明、审查与待办、测试指南、用户手册、操作记录、README。
- 本计划为第三轮收尾；2026-08-05 三份计划（审查整改/性能体积/测试整改）已完成的 WP 不再重复处理，未执行的 WP-P2/S4/S5 按 D4 登记为可选待办。

---

## 任务总览

| WP | 主题 | 优先级 | 依赖决策 | 涉及文件 | 验收标准 |
|---|---|---|---|---|---|
| WP-F1 | 「全部应用」后台重处理，消除 UI 冻结 | 高 | 无 | `gui.py`、`tests/test_gui.py` | 25 MPF 应用不再阻塞界面；测试同步化后全绿 —— ✅ 已处理（2026-08-06，打包实测通过） |
| WP-F2 | 运行日志埋点补齐与截断去重 | 中 | 无 | `core.py`、`tests/test_core.py` | `process_mpf`/APT 异常带 traceback 进 `runtime_log`；截断警告只出现一次 —— ✅ 已处理（2026-08-06，含识别数据入日志，249 项全绿） |
| WP-F3 | 查看器图表数值容错（WP-13 部分） | 中 | 无 | `viewer.py`、`tests/test_report_viewer.py` | 非数值字段不崩溃，回退 0；10 项测试全绿 —— ✅ 已处理（2026-08-06，查看器 15 项全绿） |
| WP-F4 | `code_part` 分号语义文档化 + 锁定测试 | 低 | 无 | 需求文档、用户手册、`tests/test_core.py` | 需求/手册明确「分号后视为注释」；测试锁定 —— ✅ 已处理（2026-08-06，含注释敏感点审计修复，主 253 项全绿） |
| WP-F5 | 构建脚本缩进清理 + 测试文件拆分（WP-T6） | 低 | D3 | `build_portable.ps1`、`tests/test_gui_*.py` | 缩进规整；拆分后用例数不变全绿 —— ✅ 已处理（2026-08-06，仅缩进清理；WP-T6 按 D3=B 维持登记待办） |
| WP-F6 | 版本提升与发版（WP-16） | 发版 | D1/D2 | `__init__.py`、`VERSION.txt`、`version_info.txt`、发布说明 | 版本三处一致，`test_release_assets` 动态断言通过，打包+SHA256SUMS |
| WP-F7 | 查看器增强 WP-14/15（大报告加载、问题筛选/导出） | 低 | D2 | `viewer.py`、`tests/test_report_viewer.py` | 加载大报告有状态提示；问题表可筛选并可导出 CSV —— ✅ 已处理（2026-08-06，查看器 18 项全绿） |

## 决策点（Phase 0，执行前确认）

| 编号 | 决策 | 选项 | 建议 |
|---|---|---|---|
| D1 | 版本号（若发版） | A：1.0.0 → 1.1.0（本批次功能/收尾整体发版）；B：1.0.1（仅缺陷修复补丁）；C：暂不发版 | C 优先（未到发版窗口），发版时选 A |
| D2 | 本轮范围 | A：仅收尾 WP-F1~F5（推荐）；B：A + 查看器增强（F3 扩展至 F7）+ 发版（F6） | A |
| D3 | 是否本轮拆分大测试文件（WP-T6） | A：本轮拆 `test_gui.py`（保留 `test_core.py` 暂缓）；B：继续登记待办（推荐，先做低风险收尾） | B |
| D4 | WP-P2/S4/S5 是否执行 | A：本轮执行；B：维持登记不执行（推荐：P1 已达目标、tcl 裁剪风险中收益小、ZIP 收益有限） | B |

---

## 文件结构

- `ncodeprocess/gui.py`：模块级纯函数 `reprocess_plans`；`apply_info` 改为后台线程重处理 + `_finish_apply_info` 主线程刷新。
- `ncodeprocess/core.py`：`process_mpf` 与 APT 前缀解析异常补 `emit_event`（含 traceback）；`RuntimeLog` 增加 `_reported_dropped` 去重。
- `ncodeprocessreportviewer/viewer.py`：`chart_number` 容错函数；`_draw_bar_chart` 使用；可选问题筛选/导出与加载状态。
- `tests/test_gui.py`、`tests/test_core.py`、`tests/test_report_viewer.py`：新增/调整测试。
- `build_portable.ps1`：缩进规整（WP-F5）。
- `tests/test_gui_layout.py` / `test_gui_settings.py` / `test_gui_scan.py`（D3=A 时）。
- `docs/`：需求、用户手册、审查与待办、发布说明同步。

---

## WP-F1: 「全部应用」后台重处理，消除 UI 冻结

**Files:** `ncodeprocess/gui.py`、`tests/test_gui.py`

**背景：** 现有 `apply_info` 在 UI 线程同步 `reprocess_file` 全部 MPF（25 个约 1.4s 卡界面），末尾又 `scan()` 一次。修复为后台线程重处理，主线程只负责刷新。

- [x] **Step 1: 新增模块级纯函数（gui.py，`App` 类之前）**

```python
def reprocess_plans(plans, info, config, program_tools):
    """内存重处理一组 FilePlan，返回实际处理的计划（供后台线程调用）。"""
    applied = []
    for plan in plans:
        if plan.kind == "mpf" and plan.program and plan.original_text is not None:
            reprocess_file(plan, info, config, tools=program_tools.get(plan.program, []))
            applied.append(plan)
    return applied
```

- [x] **Step 2: 新增失败测试（同步化线程后断言）**

test_gui.py 顶部补 `import threading`，并增加同步执行 helper（放在测试类外）：

```python
def _sync_thread(thread_class):
    """把 threading.Thread 替换为同步执行 target 的桩（GUI 线程测试专用）。"""
    class SynchronousThread(thread_class):
        def start(self):
            self._target(*self._args, **self._kwargs)
    return SynchronousThread
```

`test_apply_info_records_applied_header_values` 改为：

```python
    def test_apply_info_records_applied_header_values(self):
        root, app = self._build_app(1286, 668)
        try:
            plan = FilePlan("A.MPF", "mpf", "A", "A.MPF", "keep")
            plan.original_text = 'MSG("PROGRAM:A")\nN1G1X10F1000S5000M03\nM30\n'
            app.scan_result = ScanResult("tmp", [plan])
            app.info_vars["drawing"].set("NEWDRAW")
            app.info_vars["version"].set("V9")
            with patch("ncodeprocess.gui.threading.Thread", _sync_thread(threading.Thread)), \
                 patch.object(app, "scan"):
                app.apply_info()
            self.assertEqual(app.program_header_values.get("A", {}).get("drawing"), "NEWDRAW")
            self.assertIn("NEWDRAW", plan.output_text or "")
        finally:
            root.destroy()
```

- [x] **Step 3: `apply_info` 改为后台重处理**

```python
    def apply_info(self):
        if self._scan_running:
            messagebox.showinfo("扫描进行中", "扫描进行中，请稍候再应用程序信息。", parent=self.master)
            return
        v = self.info_vars
        if not v["drawing"].get().strip() or not v["version"].get().strip():
            messagebox.showerror("信息不完整", "图号和版次为必填项。未应用设置，也不会修改任何 MPF 文件。", parent=self.master)
            return
        self.applied_info = ProgramInfo(v["bianzhi"].get().strip(), v["shenhe"].get().strip(),
                                        v["drawing"].get().strip(), v["version"].get().strip(),
                                        "", "SIE840D", v["date"].get().strip())
        self.info_defaults.update({key: v[key].get().strip() for key in self.info_defaults})
        # 主线程捕获配置与计划快照，避免工作线程读取 Tk 变量。
        preview_config = self.config()
        plans = [p for p in (self.scan_result.files if self.scan_result else [])
                 if p.kind == "mpf" and p.program and p.original_text is not None]
        generation = self._scan_generation
        self.status.set("正在应用程序信息并生成预览……")

        def work():
            applied = reprocess_plans(plans, self.info(), preview_config, self.program_tools)
            self._safe_after(0, lambda: self._finish_apply_info(applied, generation))

        threading.Thread(target=work, daemon=True).start()

    def _finish_apply_info(self, applied_plans, generation):
        if generation != self._scan_generation:
            return
        v = self.info_vars
        for plan_file in applied_plans:
            self.program_header_values[plan_file.program] = {
                "bianzhi": v["bianzhi"].get().strip(),
                "shenhe": v["shenhe"].get().strip(),
                "drawing": v["drawing"].get().strip(),
                "version": v["version"].get().strip(),
                "date": v["date"].get().strip(),
            }
        mode = "覆盖修改" if self.overwrite_fields.get() else "按默认逻辑（保留已有值）"
        self.status.set(f"已生成 {len(applied_plans)} 个程序的预览（{mode}）。确认无误后点击“确认并执行处理”写入文件。")
        previous_program = None
        if self.keep_table.selection():
            try:
                previous_program = self.scan_result.files[int(self.keep_table.selection()[0])].program
            except (IndexError, TypeError, ValueError):
                previous_program = None
        self.populate_file_tables()
        if previous_program:
            for iid in self.keep_table.get_children():
                try:
                    if self.scan_result.files[int(iid)].program == previous_program:
                        self.keep_table.selection_set(iid)
                        self.keep_table.focus(iid)
                        break
                except (IndexError, TypeError, ValueError):
                    continue
        self.show_selected()
        # 末尾保留一次轻量扫描，刷新图号候选等目录级全局数据（后台线程，不阻塞界面）。
        self.scan()
```

- [x] **Step 4: 更新受影响的既有测试**

`test_apply_info_records_applied_header_values`（Step 2 已给）；`test_apply_info_*` 中所有直接断言 `apply_info()` 后立即生效的用例统一加 `_sync_thread` 补丁；`test_finish_scan_*` 不受影响。

- [x] **Step 5: 回归**

Run: `D:\anaconda3\envs\python38\python.exe -m unittest tests.test_gui -v`
Expected: 全绿（预计 GUI 用例数不变）。

- [x] **Step 6: 提交门**：`perf(gui): 全部应用改为后台重处理，消除 UI 冻结`（用户确认后）。

---

## WP-F2: 运行日志埋点补齐与截断去重

**Files:** `ncodeprocess/core.py`、`tests/test_core.py`

- [x] **Step 1: 新增失败测试**

```python
    def test_process_mpf_error_emits_runtime_event(self):
        root = self.make_dir()
        (root / "A.MPF").write_text('MSG("PROGRAM:A")\nN1S1000M03\nN2M30\n', encoding="utf-8")
        cfg = self._cfg()
        scan = scan_directory(str(root), cfg)
        with patch("ncodeprocess.core.apply_header", side_effect=RuntimeError("boom")):
            build_plan(scan, DEFAULT_INFO, cfg)
        events = runtime_log().snapshot()
        self.assertTrue(any(e["event"] == "error" and "A.MPF" in e["message"] and "Traceback" in e["detail"] for e in events))

    def test_snapshot_truncation_warning_appears_once(self):
        from ncodeprocess.core import RuntimeLog
        log = RuntimeLog(max_events=2)
        for index in range(5):
            log.emit("info", "event", f"事件 {index}")
        first = log.snapshot()
        second = log.snapshot()
        count = lambda entries: sum(1 for e in entries if "已截断" in e["message"])
        self.assertEqual(count(first), 1)
        self.assertEqual(count(second), 1)
```

> 说明：`test_core.py` 顶部补 `runtime_log`/`reset_runtime_log` 导入；`CoreTests.setUp` 增加 `reset_runtime_log()`，避免跨用例的事件累积污染断言。

- [x] **Step 2: 实现埋点与去重**

2a. `build_plan.process_mpf` 的 `except Exception as e:` 分支改为：

```python
            except Exception as e:
                emit_event("error", "error", f"处理文件失败：{f.source}", detail=traceback.format_exc())
                f.issues.append(Issue(f.source, 1, "", "processing", "error", str(e)))
```

2b. `scan_directory` 的 APT 前缀解析 `except Exception:` 分支改为：

```python
            except Exception:
                emit_event("warning", "scan_warning", f"APTSOURCE 头部解析失败：{rel}", detail=traceback.format_exc())
```

2c. `RuntimeLog` 增加去重字段并修改 `snapshot`：

```python
    def __init__(self, max_events: int = MAX_LOG_EVENTS):
        self._events: "deque[RuntimeEvent]" = deque(maxlen=max_events)
        self._dropped = 0
        self._reported_dropped = 0
        self._lock = threading.Lock()

    def snapshot(self) -> List[dict]:
        with self._lock:
            entries = [entry.to_dict() for entry in self._events]
            if self._dropped > self._reported_dropped:
                entries.append({
                    "time": datetime.now().isoformat(timespec="seconds"),
                    "level": "warning",
                    "event": "warning",
                    "message": f"运行日志已截断：报告内嵌日志仅保留最近 {self._events.maxlen} 条事件",
                    "detail": "",
                })
                self._reported_dropped = self._dropped
            return entries
```

- [x] **Step 3: 回归**

Run: `D:\anaconda3\envs\python38\python.exe -m unittest tests.test_core -v`
Expected: 全绿（含既有 RuntimeLog 用例）。

- [x] **Step 4: 提交门**：`fix(core): 处理/APT 异常 traceback 进运行日志，截断警告去重`。

---

## WP-F3: 查看器图表数值容错（WP-13 部分）

**Files:** `ncodeprocessreportviewer/viewer.py`、`tests/test_report_viewer.py`

- [x] **Step 1: 新增失败测试**

```python
    def test_chart_number_fallback(self):
        from ncodeprocessreportviewer.viewer import chart_number
        self.assertEqual(chart_number(5), 5.0)
        self.assertEqual(chart_number("3"), 3.0)
        self.assertEqual(chart_number("abc"), 0)
        self.assertEqual(chart_number(None), 0)
```

- [x] **Step 2: 新增 `chart_number` 并在 `_draw_bar_chart` 使用**

```python
def chart_number(value, default=0):
    """柱状图数值容错：非数值/缺失回退 0，防止篡改报告导致崩溃。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
```

`_draw_bar_chart` 中：

```python
        maximum = max([chart_number(value) for _label, value in values] + [1])
        ...
            bar_height = chart_height * chart_number(value) / maximum
```

- [x] **Step 3: 冒烟测试（构建查看器 + 非数值报告不崩溃）**

`test_report_viewer.py` 的 `ReportViewerLayoutTests` 新增：构造 `report_data={"success": "abc", "errors": "x", ...}` 后调用 `app._update_views()` 不抛异常（沿用 `_build_viewer` harness，选 `"all"`）。

- [x] **Step 4: 回归**：查看器用例数 10 → 12 项全绿（chart_number 单测 + 非数值报告冒烟；以 discover 输出为准）。
- [x] **Step 5: 提交门**：`fix(viewer): 柱状图对非数值字段容错`。

---

## WP-F4: `code_part` 分号语义文档化 + 锁定测试

**Files:** 需求文档、用户手册、`tests/test_core.py`

- [x] **Step 1: 新增锁定测试**

```python
    def test_semicolon_after_code_is_trailing_comment(self):
        # HASS 后处理：分号视为块终止符，分号后内容按注释处理，不参与统计/校验/补写。
        text = 'MSG("PROGRAM:P")\nN10 S5000;S9000\nN20 M30\n%\n'
        issues = validate_program(text, "P.MPF", "P", DEFAULT_INFO, self._cfg(auto_m03=False))
        self.assertFalse(any(i.kind == "multiple-spindle-speeds" for i in issues))  # S9000 在注释区
        stats = calculate_stats(text)
        self.assertEqual(stats.counts["S"], 1)
        out, changed, _note = add_m03('MSG("PROGRAM:P")\nN10 S5000;说明\nN20 M30\n', self._cfg())
        self.assertTrue(changed)
        self.assertIn("S5000M03;说明", out)
```

- [x] **Step 2: 文档同步**

需求文档：术语/FR-06 统计口径新增一条——「正文中分号视为程序块终止符，分号之后的内容视为行内注释，不参与统计、校验与指令检测（HASS 后处理格式）」。用户手册「处理规则」章节同步。

- [x] **Step 3: 提交门**：`docs: 明确分号后内容按行内注释处理`（与测试同提交）。

---

## WP-F5: 构建脚本缩进清理 + 测试文件拆分（依赖 D3）

**Files:** `build_portable.ps1`；D3=A 时 `tests/test_gui_*.py`

- [x] **Step 1: 清理 `build_portable.ps1` 缩进**

`$spec = Join-Path $root 'NCodeProcess.spec'` 与 `$pyInstallerArgs = @(...)` 及其后续参数行统一缩进到 `try` 块内（与 `$distTargets` 同级），功能零变化；用 PowerShell 语法检查 `powershell -NoProfile -Command "[scriptblock]::Create((Get-Content -Raw build_portable.ps1))"` 通过。

- [ ] **Step 2（D3=A）: 拆分 `test_gui.py`**

- 新建 `tests/test_gui_layout.py`：`LayoutWidgetMixin` + `LayoutWidgetTests` + `DiffViewTests` + `LayoutMetricTests`；
- 新建 `tests/test_gui_settings.py`：`SettingsDialogTests` + `ProcessingConfirmationTests` + `FontAwareLayoutMetricTests`；
- 新建 `tests/test_gui_scan.py`：`ScanLifecycleTests` + `StartupCallbackTests` + `ReportExportTests`；
- `test_gui_layout.py` 导出 `LayoutWidgetMixin`，其余文件 `from test_gui_layout import LayoutWidgetMixin`（discover 顶层导入模式下同目录模块可直接导入）；
- 删除 `tests/test_gui.py`；模块级 `TEST_SETTINGS_KEY` 随 `test_gui_layout.py` 迁移，其余文件导入。

- [x] **Step 3: 回归**

Run: `.\run_tests.ps1`
Expected: 主 245 + 查看器 10 全绿，用例数不变。

- [x] **Step 4: 提交门**：`refactor: 构建脚本缩进规整；测试文件按主题拆分`（D3=B 时仅缩进部分）。

---

## WP-F6: 版本提升与发版（依赖 D1/D2）

**Files:** `ncodeprocess/__init__.py`、`VERSION.txt`、`version_info.txt`、两项目发布说明、README

- [ ] **Step 1: 按 D1 更新版本**（示例 D1=A：`1.1.0`）

- `ncodeprocess/__init__.py`：`__version__ = "1.1.0"`；
- `VERSION.txt`：`NCodeProcess 1.1.0`；
- `version_info.txt`：`filevers=(1, 1, 0, 0)` / `prodvers=(1, 1, 0, 0)`；
- 查看器 `__init__.py` 与 `VERSION.txt`（如存在）同步。

- [ ] **Step 2: 发布说明与文档同步**

两项目发布说明新增第三轮收尾批次（F1~F5 摘要、测试基线、体积/性能数据）；README 版本号更新。

- [ ] **Step 3: 全量测试 + 打包**

Run: `.\run_tests.ps1` 全绿；两项目 `build_portable.ps1` → `SHA256SUMS.txt` 自动生成；`test_release_assets` 动态版本断言通过。

- [ ] **Step 4: 用户实测 + 提交门**：`feat: 版本提升至 1.1.0 并同步发布文档`（用户确认后）。

---

## WP-F7: 查看器增强 WP-14/15（依赖 D2=B）

**Files:** `ncodeprocessreportviewer/viewer.py`、`tests/test_report_viewer.py`

- [x] **WP-14 大报告加载状态**：`_load_report` 前设置 `report_label` 为「正在加载报告……」，`update_idletasks` 后再解析渲染；超大报告提示「报告较大，加载中」。测试：mock 一个含 5000 files 的 `load_report` 数据，断言状态标签先变为加载中（UI 冒烟）。
- [x] **WP-15 问题筛选与导出**：`issues_page` 增加级别筛选下拉（全部/error/warning）；新增「导出问题 CSV」按钮（`filedialog.asksaveasfilename` + csv 写入，utf-8-sig）。测试：`_fill_issues` 筛选后行数正确；导出 helper 纯函数 `issues_csv_rows(report_data) -> list[tuple]` 单测。
- [x] **回归**：查看器用例数 12 → 15 项左右全绿（以 discover 输出为准）；提交门：`feat(viewer): 大报告加载状态与问题筛选/导出`。

---

## 执行顺序与确认流程

1. Phase 0 确认 D1~D4（建议：D1=C、D2=A、D3=B、D4=B）。
2. 建议顺序：WP-F1 → WP-F2 → WP-F3 → WP-F4 → WP-F5；D2=B 时追加 WP-F6 → WP-F7。
3. 每个 WP 启动前单独说明范围并确认；完成后全量测试 → 打包（测试类除外）→ 用户实测 → 确认后提交。
4. 完成后的计划文档按仓库惯例归档至 `docs/archive/`（本地保留，git 移除跟踪）。

---

## Self-Review 记录

- **Spec 覆盖**：复审报告全部遗留项均有对应 WP——UI 冻结（F1）、日志埋点/去重（F2）、图表容错（F3）、分号语义（F4）、构建缩进 + 拆测试（F5）；发版与查看器增强按 D1/D2 决定（F6/F7）；WP-P2/S4/S5 按 D4 登记不执行；机床行程维持用户决策。
- **占位符检查**：每个 WP 含具体文件、代码、测试名与命令；无 TBD。
- **签名/一致性**：`reprocess_plans`/`chart_number` 为新增纯函数，命名唯一；`RuntimeLog.__init__` 新增 `_reported_dropped` 不影响既有调用；`apply_info` 行为语义（仅预览、不写文件）保持不变，测试经 `_sync_thread` 同步化后断言不变。
