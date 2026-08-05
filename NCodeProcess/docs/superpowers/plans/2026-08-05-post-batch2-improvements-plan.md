# NCodeProcess 后续改进实施计划（Post-Batch 2 路线图与 Phase 1 详案）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 v1.0.0（Batch 2）基础上，按优先级消除主工具与报告查看器的已知问题：先解决线程安全/生命周期与子窗口适配两项高优先级项，再推进可靠性、需求缺口、低优先级清理，最终完成版本提升与文档同步。

**Architecture:** 保持现有分层（`core.py` 纯逻辑 + `gui.py` Tkinter 界面 + `preferences.py` 双后端存储 + `viewer.py` 纯函数/界面分离）。第一批改动集中在 `gui.py` 的扫描/处理生命周期与两个子窗口几何，`core.py` 仅增加可选进度回调，默认参数保证存量行为不变、存量测试零回归。后续工作包按同一模式逐个展开。

**Tech Stack:** Python 3.8 / Tkinter ttk / unittest（TDD，无第三方依赖）/ PyInstaller 5.13.2（仅发版用）。测试一律使用 conda Python 3.8 环境，命令形如 `conda run -n python38 python -m unittest ...`（实际环境名以本地流程文档为准）。

**关联文档：**
- 需求基线：`NCodeProcess-需求文档.md`（V1.1，第 13 节待确认事项、FR-07.2/07.3、第 15 节实施状态）
- 待办来源：`NCodeProcess-审查与待办.md`、`NCodeProcessReportViewer-审查与待办.md`
- 测试基线：`NCodeProcess-测试指南.md`（198 项）、`NCodeProcessReportViewer-测试指南.md`（6 项）
- 前置工作：Batch 1 / Batch 2 计划（已完成，见 `docs/archive/superpowers/plans/`）

---

## 〇、决策点（Phase 0，开始执行前须确认）

| 编号 | 决策 | 选项 | 影响 | 建议 |
|---|---|---|---|---|
| D1 | M03「紧贴 S 数值之后」的确切口径（需求文档第 13 节唯一 ⏳） | A：维持现状（`after-s`=块尾插入 `...S2000F1000M03`），同步修改需求文档 FR-05.4 文字描述；B：改为真正紧贴 S 值（`...S2000M03F1000`），修改 `add_m03` 与相关测试 | WP-03 | ✅ **已确认（2026-08-05）：方案 A，维持现状**；WP-03 仅修订需求文档 FR-05.4 与审查待办文字口径，不改 `add_m03` 代码 |
| D2 | Batch 2 配置（必填字段/M03 位置/F·S 上下限/辅助顺序/换行）是否持久化 | A：维持「仅本次运行生效」（与需求第 8 节一致）；B：扩展 `REGISTRY_DEFAULTS` + preferences 测试 + GUI 默认值 | WP-11 | 维持 A，除非车间明确要求重启后保留 |
| D3 | 机床行程 X/Y/Z 检查（FR-07.3） | 维持暂不实施 / 恢复规划 | 无（若恢复则新增 WP） | 维持暂不实施 |
| D4 | 计划文档的跟踪方式 | 本计划入库（`docs/superpowers/plans/` 未被 .gitignore 忽略），完成后归档至 `docs/archive/superpowers/plans/`（git 移除跟踪） | WP-17 | 按项目历史惯例：活动计划入库、完成归档 |

> **执行确认流程（2026-08-05 用户要求）**：每个工作包（WP）启动前，必须先向用户说明该 WP 的范围、涉及文件、验收标准与影响，获得明确确认后才能展开分计划并实施；Phase 1 的详细任务（Task）同样在启动前确认。WP/Task 完成后汇报结果，由用户决定是否进入下一项。

---

## 一、为什么做（对实际生产的意义）

1. **稳定性（Phase 1）**：后台线程直接调 Tk API、重复扫描无代际防护，会导致窗口关闭后残留 TclError 被静默吞掉、旧扫描结果覆盖新结果；处理过程只有一句状态文本，车间用户无法判断是否卡死。这是审查报告标为「高」且尚未处理的问题。
2. **可靠性（Phase 2）**：删除/覆盖不可恢复、只读目录无预检、gb18030 兜底几乎不失败（错误编码被静默接受）、换刀改写注释中的刀具号——都可能在正式目录上造成不可预期结果。
3. **需求缺口（Phase 2）**：FR-07.2 的刀具号一致性/孤立指令检查、FR-07.3 的互斥 G/M 专项，是需求文档中仅有的 🔶 部分实现项。
4. **查看器健壮性（Phase 4）**：柱状图对非数值字段无容错、报告被外部删除后索引失效、超大报告阻塞主线程。
5. **工程收尾（Phase 5）**：`__version__` 仍为 1.0.0，正式发版前需提升并同步版本资源与发布说明。

---

## 二、工作包总览与依赖

| WP | 主题 | 优先级 | 项目 | 主要文件 | 依赖 | 验收标准（一句话） |
|---|---|---|---|---|---|---|
| WP-01 | 后台线程安全与生命周期 | 高 | 主 | `gui.py`、`core.py` | 无 | 重复扫描旧结果不覆盖新结果；窗口销毁后线程不抛残留异常；处理过程显示「处理中 i/N」进度 |
| WP-02 | 全部子窗口居中与「全部程序信息」窗口屏幕适配 | 高 | 主 | `gui.py` | 无 | 设置/统计/对比/编辑/确认页 5 个子窗口全部居中显示；1366×768 下「全部程序信息」窗口不超屏幕、表头完整可见 |
| WP-03 | M03 紧贴 S 口径 | 中 | 主 | `core.py`、需求文档 | D1 | 按 D1 选定口径实现并同步需求文字 |
| WP-04 | 备份/回收站兜底与只读目录预检 | 中 | 主 | `core.py`、`gui.py` | 无 | 只读目录启动/扫描后有提示；处理前可选备份，删除/覆盖可恢复 |
| WP-05 | 编码识别、NUL 检测与 gbk/gb2312 支持 | 中 | 主 | `core.py`、`gui.py` | 无 | 解析信息显示实际编码；含 NUL 数据报 encoding 错误；支持 gbk/gb2312 识别与强制指定 |
| WP-06 | 注释边界统一与刀具圆角比较 | 中 | 主 | `core.py` | 无 | `add_initial_tool_change` 不改写注释内 T 号；`extract_tools` 普通立铣刀判定同时比较直径与圆角 |
| WP-07 | FR-07.2/07.3 校验缺口与 F 上离群 | 中 | 主 | `core.py` | 无 | 新增刀具号一致性、孤立 F/S 参数、互斥 M 指令、F 上离群校验并有测试锁定 |
| WP-08 | 异常归类细分 | 低 | 主 | `core.py` | 无 | MPF 读取异常按 encoding/permission/io 区分 kind |
| WP-09 | 未命名程序批量确认页 | 低 | 主 | `gui.py` | 无 | 多个未命名程序一次性列表确认，不再逐个弹模态框 |
| WP-10 | 启发式阈值进 Config | 低 | 主 | `core.py`、`gui.py` | 无 | `feed-outlier`/`multiple-spindle-speeds` 阈值可配置 |
| WP-11 | Batch 2 配置持久化 | 低 | 主 | `preferences.py`、`gui.py` | D2 | 按 D2 决定：持久化则扩展 REGISTRY_DEFAULTS + 测试，否则关闭 |
| WP-12 | 细节清理（MSG 缩进/单实例/杀软） | 低 | 主 | `core.py`、`gui.py` | 无 | MSG 行替换保留原缩进；防双 EXE 竞态；杀软误报实测记录 |
| WP-13 | 查看器柱状图容错与索引校验 | 中 | 查看器 | `viewer.py` | 无 | 非数值字段不抛异常；外部删除报告后选择不指向旧索引 |
| WP-14 | 查看器大报告后台加载 | 中 | 查看器 | `viewer.py` | 无 | 加载大报告不阻塞界面，有进度/状态反馈 |
| WP-15 | 查看器低优先级清理 | 低 | 查看器 | `viewer.py` | 无 | 死分支清理；汇总缺失显示「无数据」；图表悬停/导出；错误筛选 |
| WP-16 | 版本提升与发版 | 发版 | 两项目 | `__init__.py`、`VERSION.txt`、`version_info.txt`、发布说明 | WP-01~15 | 版本号同步三处 + SHA256SUMS/发布包一致 |
| WP-17 | 文档同步（贯穿） | 持续 | 两项目 | `docs/` 全部 + 两 README + 根 README | 每个 WP | 每个 WP 完成后受影响文档均已更新并核对 |

**执行顺序：**

```text
Phase 0: 确认 D1–D4
Phase 1: WP-01 → WP-02（详细任务见第三节）
Phase 2: WP-04 → WP-05 → WP-06 → WP-07 → WP-03（WP-03 需 D1 已确认）
Phase 3: WP-08 → WP-09 → WP-10 → WP-11 → WP-12
Phase 4: WP-13 → WP-14 → WP-15
Phase 5: WP-16（发版）；WP-17 全程随各 WP 执行
```

> 说明：第三节给出 Phase 1 的完整 bite-sized 任务；Phase 2 起的每个工作包在执行前按本计划模板展开为独立分计划（writing-plans 规范），避免在尚未设计细节前臆造代码。每份分计划均包含文件结构、失败测试、实现、回归与提交步骤。

---

## 三、Phase 1 详细任务（WP-01 后台线程安全与生命周期、WP-02 子窗口适配）

### WP-01 Task 1.1: 扫描代际防护

**Files:**
- Modify: `NCodeProcess/ncodeprocess/gui.py`（`App.__init__`、`App.scan`、`App.finish_scan`）
- Test: `NCodeProcess/tests/test_gui.py`

- [ ] **Step 1: 写失败测试**

在 `test_gui.py` 的布局/交互测试类（或新建 `ScanLifecycleTests`）中新增：

```python
def test_finish_scan_ignores_stale_generation(self):
    root, app = self._build_app(1286, 668)
    try:
        app._scan_generation = 2
        app.scan_result = None
        stale = ScanResult("stale", [], warnings=["stale"])
        app.finish_scan(stale, 1)   # 旧代结果：应被忽略
        self.assertIsNone(app.scan_result)
        app.finish_scan(stale, 2)   # 当前代结果：应生效
        self.assertIs(app.scan_result, stale)
    finally:
        root.destroy()
```

（`ScanResult` 已在 `test_gui.py` 顶部从 `ncodeprocess.core` 导入；`_build_app` 返回 `(root, app)` 元组。）

- [ ] **Step 2: 运行确认失败**

Run: `conda run -n python38 python -m unittest tests.test_gui.ScanLifecycleTests.test_finish_scan_ignores_stale_generation -v`
Expected: FAIL（当前 `finish_scan` 签名只有 `(self, result)`，传两个位置参数直接 TypeError；即使兼容也会无条件覆盖 `scan_result`）。

- [ ] **Step 3: 实现代际防护**

`App.__init__` 中新增状态：

```python
self._scan_generation = 0
```

`App.scan` 中（`def work():` 之前）：

```python
self._scan_generation += 1
generation = self._scan_generation
```

`work()` 改为：

```python
def work():
    result = build_plan(scan_directory(str(self.workdir), config), info, config, self.program_tools)
    try:
        self.after(0, lambda: self.finish_scan(result, generation))
    except tk.TclError:
        pass
```

（此处先用手写 `try/except tk.TclError`；Task 1.2 定义 `_safe_after` 后，将本处及 `process.work` 统一替换。）

`finish_scan` 签名改为 `def finish_scan(self, result, generation=None):`，方法体第一行：

```python
if generation is not None and generation != self._scan_generation:
    return
```

其余逻辑不动。

- [ ] **Step 4: 运行确认通过**

Run: 同 Step 2
Expected: PASS

- [ ] **Step 5: 全量回归并提交**

Run: `conda run -n python38 python -m unittest discover -s tests -v`
Expected: 159 项全部通过（WP-01~WP-07 及二级页面修复、样例刀具回归、刀具类型拆分完成后基线更新为 198 项，测试指南同步）。

```bash
git add NCodeProcess/ncodeprocess/gui.py NCodeProcess/tests/test_gui.py
git commit -m "fix(gui): 扫描代际防护，旧线程结果不再覆盖新扫描"
```

---

### WP-01 Task 1.2: 后台线程 Tk 调用安全（`_safe_after`）

**Files:**
- Modify: `NCodeProcess/ncodeprocess/gui.py`（`App.scan`、`App.process` 的 `work()`）
- Test: `NCodeProcess/tests/test_gui.py`

- [ ] **Step 1: 写失败测试**

```python
def test_safe_after_does_not_raise_after_destroy(self):
    root, app = self._build_app(1286, 668)
    root.destroy()
    app._safe_after(0, lambda: None)   # 不应抛 tk.TclError
    app._safe_after(50, lambda: None)
```

- [ ] **Step 2: 运行确认失败**

Run: `conda run -n python38 python -m unittest tests.test_gui.ScanLifecycleTests.test_safe_after_does_not_raise_after_destroy -v`
Expected: FAIL（`_safe_after` 尚不存在，AttributeError）。

- [ ] **Step 3: 实现 `_safe_after` 并替换两处线程回调**

在 `App` 中新增：

```python
def _safe_after(self, ms, callback):
    """线程回调安全出口：窗口销毁后 Tk 的 after 会抛 TclError，统一吞掉。"""
    try:
        return self.after(ms, callback)
    except tk.TclError:
        return None
```

将 `scan` 与 `process` 的 `work()` 中 `self.after(0, lambda: ...)` 全部替换为 `self._safe_after(0, lambda: ...)`。两处：
- `scan.work`：`self.after(0, lambda: self.finish_scan(result))` → 结合 Task 1.1 传入代际；
- `process.work`：`self.after(0, lambda: self.finish_process(report))`。

- [ ] **Step 4: 运行确认通过**

Run: 同 Step 2
Expected: PASS

- [ ] **Step 5: 回归并提交**

Run: 全量 159+2 项
Expected: 全部通过

```bash
git add NCodeProcess/ncodeprocess/gui.py NCodeProcess/tests/test_gui.py
git commit -m "fix(gui): 后台线程经 _safe_after 回主线程，窗口销毁后不再残留 TclError"
```

---

### WP-01 Task 1.3: 处理进度反馈

**Files:**
- Modify: `NCodeProcess/ncodeprocess/core.py`（`process_plan`）
- Modify: `NCodeProcess/ncodeprocess/gui.py`（`App.process`、新增 `_poll_process_progress`）
- Test: `NCodeProcess/tests/test_core.py`

- [ ] **Step 1: 写失败测试（core）**

在 `tests/test_core.py` 新增：

```python
def test_process_plan_reports_progress(self):
    root = self.make_dir()
    (root / "A.MPF").write_text("%\nN1G1X0Y0Z0F1000S5000\nM30\n", encoding="utf-8")
    config = self._cfg(require_end_marker=False)
    scan = build_plan(scan_directory(str(root), config), DEFAULT_INFO, config)
    progress = []
    process_plan(scan, str(root), config, progress_callback=lambda done, total, name: progress.append((done, total, name)))
    self.assertEqual(progress[-1][0], progress[-1][1])
    self.assertEqual(progress[-1][2], "A.MPF")
    self.assertEqual([done for done, _total, _name in progress], list(range(1, len(progress) + 1)))
```

（`make_dir` 为 `CoreTests` 实例方法，`_cfg`/`DEFAULT_INFO` 为现有 helper；写文件沿用 `(root / name).write_text(...)` 模式。）

- [ ] **Step 2: 运行确认失败**

Run: `conda run -n python38 python -m unittest tests.test_core.CoreTests.test_process_plan_reports_progress -v`
Expected: FAIL（`process_plan` 不接受 `progress_callback` 参数，TypeError）。

- [ ] **Step 3: 实现进度回调**

`process_plan` 签名改为：

```python
def process_plan(scan, output_dir=None, config=None, *, confirm_cleanup=True, progress_callback=None):
```

`ordered_files = sorted(...)` 之后：

```python
total = len(ordered_files)
for index, f in enumerate(ordered_files, start=1):
    if progress_callback is not None:
        progress_callback(index, total, f.source)
```

其余逻辑不动（回调在 GUI 线程之外执行，只允许写线程安全的结果槽，不允许直接调 Tk API）。

- [ ] **Step 4: 运行确认通过**

Run: 同 Step 2
Expected: PASS

- [ ] **Step 5: GUI 接入进度显示**

`App.__init__` 新增：

```python
self._processing = False
self._process_progress = None
self._process_progress_lock = threading.Lock()
```

`App.process` 的 `work()` 改为：

```python
def work():
    def report(done, total, name):
        with self._process_progress_lock:
            self._process_progress = (done, total, name)
    report_obj = process_plan(self.scan_result, str(self.workdir), self.config(), confirm_cleanup=True, progress_callback=report)
    self._safe_after(0, lambda: self.finish_process(report_obj))
```

新增轮询方法（处理启动时用 `_safe_after` 调度一次）：

```python
def _poll_process_progress(self):
    with self._process_progress_lock:
        progress = self._process_progress
    if progress is not None:
        done, total, name = progress
        self.status.set(f"正在处理当前目录……（{done}/{total}）{name}")
    if self._process_progress is not None or self._processing:
        self._safe_after(100, self._poll_process_progress)
```

`process()` 启动线程前置 `self._processing = True`；`finish_process` 中置 `self._processing = False` 并清空 `self._process_progress`。

- [ ] **Step 6: GUI 测试与全量回归**

新增 GUI 测试（`ScanLifecycleTests`）：

```python
def test_finish_process_clears_progress_state(self):
    root, app = self._build_app(1286, 668)
    try:
        app._processing = True
        app._process_progress = (1, 2, "A.MPF")
        with patch("ncodeprocess.gui.messagebox.showinfo"):
            app.finish_process(ProcessReport("in", "out", "start"))
        self.assertFalse(app._processing)
        self.assertIsNone(app._process_progress)
    finally:
        root.destroy()
```

Run: `conda run -n python38 python -m unittest tests.test_gui.ScanLifecycleTests -v`，再全量 `conda run -n python38 python -m unittest discover -s tests -v`
Expected: 全部通过（`_build_app` 已 patch `App.scan`，`finish_process` 内的 `self.scan()` 调用不会真正扫描）。

- [ ] **Step 7: 提交**

```bash
git add NCodeProcess/ncodeprocess/core.py NCodeProcess/ncodeprocess/gui.py NCodeProcess/tests/test_core.py NCodeProcess/tests/test_gui.py
git commit -m "feat(gui): 目录处理进度反馈（i/N + 当前文件），core 增加可选 progress_callback"
```

---

### WP-01 Task 1.4: 文档同步与基线更新

**Files:**
- Modify: `NCodeProcess/docs/NCodeProcess-审查与待办.md`（问题 1/2/3 标记 ✅ 已修复）
- Modify: `NCodeProcess/docs/NCodeProcess-程序理解与操作记录.md`（追加操作记录）
- Modify: `NCodeProcess/docs/NCodeProcess-测试指南.md`（测试基线 159 → 163）

- [ ] **Step 1: 更新审查与待办**

将「线程与生命周期」表 1/2/3 状态改为 ✅ 已修复，注明实现方式（代际编号、`_safe_after`、进度轮询）与提交哈希；从待办清单中移除对应高优先级项。

- [ ] **Step 2: 更新操作记录与测试指南**

在操作记录追加 2026-08-05 条目（三项改动 + 提交）；测试指南同步基线数字与新增测试类名。

- [ ] **Step 3: 提交**

```bash
git add NCodeProcess/docs
git commit -m "docs: WP-01 线程安全与进度反馈完成，同步审查待办/操作记录/测试基线"
```

---

### WP-02 Task 2.1: 全部子窗口居中与「全部程序信息」窗口屏幕适配

> 2026-08-05 用户要求扩大范围：所有程序里的子窗口都要居中显示，不要默认出现在屏幕左上角。

**Files:**
- Modify: `NCodeProcess/ncodeprocess/gui.py`（新增 `centered_position`、`App._show_centered`；替换 `open_settings`/`show_all_program_stats`/`compare_selected_programs`/`edit_program_code`/`confirm_processing` 5 处窗口定位）
- Test: `NCodeProcess/tests/test_gui.py`

- [x] **Step 1: 写失败测试**

纯函数测试（`LayoutMetricTests`）：

```python
def test_centered_position_places_window_at_parent_center(self):
    x, y = centered_position(100, 80, 1000, 600, 800, 600, 1366, 768)
    self.assertEqual((x, y), (200, 80))

def test_centered_position_clamps_inside_screen(self):
    x, y = centered_position(1200, 700, 1000, 600, 800, 600, 1366, 768)
    self.assertLessEqual(x + 800, 1366)
    self.assertLessEqual(y + 600, 768)
    x, y = centered_position(-500, -300, 400, 300, 800, 600, 1366, 768)
    self.assertEqual((x, y), (0, 0))
```

集成测试（`ScanLifecycleTests`，读 geometry 前先 `update_idletasks()`，因 Toplevel 未映射时读取尺寸为 1x1）：

```python
def test_all_stats_window_uses_screen_fitted_geometry(self):
    root, app = self._build_app(1286, 668)
    try:
        app.scan_result = ScanResult("tmp", [])
        with patch.object(tk.Toplevel, "winfo_screenwidth", return_value=1366), \
             patch.object(tk.Toplevel, "winfo_screenheight", return_value=768):
            app.show_all_program_stats()
        app.all_stats_window.update_idletasks()
        geometry = app.all_stats_window.geometry()
        width = int(geometry.split("x")[0])
        self.assertLessEqual(width, 1366)
        self.assertGreaterEqual(width, 1050)
        self.assertIn("+", geometry)
        x = int(geometry.split("+")[1])
        y = int(geometry.split("+")[2])
        self.assertGreaterEqual(x, 0)
        self.assertGreaterEqual(y, 0)
    finally:
        root.destroy()

def test_settings_window_is_centered(self):
    root, app = self._build_app(1286, 668)
    try:
        app.open_settings()
        app.settings_window.update_idletasks()
        geometry = app.settings_window.geometry()
        self.assertIn("+", geometry)
        x = int(geometry.split("+")[1])
        y = int(geometry.split("+")[2])
        self.assertGreaterEqual(x, 0)
        self.assertGreaterEqual(y, 0)
    finally:
        root.destroy()
```

- [x] **Step 2: 运行确认失败**

Run: `conda run -n python38 python -m unittest tests.test_gui.LayoutMetricTests.test_centered_position_places_window_at_parent_center tests.test_gui.ScanLifecycleTests.test_all_stats_window_uses_screen_fitted_geometry -v`
Expected: FAIL（`ImportError: cannot import name 'centered_position'`）。

- [x] **Step 3: 实现居中定位与统一方法**

模块级纯函数（`gui.py`，放在 `window_geometry_for_screen` 之后）：

```python
def centered_position(parent_x, parent_y, parent_w, parent_h, width, height, screen_w, screen_h):
    """Return the top-left position that centers a child window on its parent."""
    x = max(0, min(parent_x + (parent_w - width) // 2, screen_w - width))
    y = max(0, min(parent_y + (parent_h - height) // 2, screen_h - height))
    return x, y
```

`App._show_centered`（放在 `_safe_after` 之后）：

```python
def _show_centered(self, window, width=None, height=None, min_width=0, min_height=0):
    if width is None or height is None:
        window.update_idletasks()
        width = width or window.winfo_reqwidth()
        height = height or window.winfo_reqheight()
    screen_w = window.winfo_screenwidth()
    screen_h = window.winfo_screenheight()
    width = min(width, screen_w)
    height = min(height, screen_h)
    parent_x = self.master.winfo_rootx()
    parent_y = self.master.winfo_rooty()
    parent_w = max(self.master.winfo_width(), 1)
    parent_h = max(self.master.winfo_height(), 1)
    x, y = centered_position(parent_x, parent_y, parent_w, parent_h, width, height, screen_w, screen_h)
    window.geometry(f"{width}x{height}+{x}+{y}")
    if min_width:
        window.minsize(min_width, min_height)
```

替换 5 处窗口定位：
- `open_settings`：`win.resizable(False, False)` 后加 `self._show_centered(win)`（尺寸用请求尺寸，消除默认左上角）；
- `show_all_program_stats`：`window.geometry("1500x620")`/`minsize` 改为屏幕适配后 `self._show_centered(window, width, height, min_width=1050, min_height=420)`；
- `compare_selected_programs`：`self._show_centered(window, 1100, 650, min_width=800, min_height=480)`；
- `edit_program_code`：`self._show_centered(window, 900, 650, min_width=700, min_height=480)`；
- `confirm_processing`：`self._show_centered(window, 900, 650, min_width=700, min_height=460)`。

- [x] **Step 4: 运行确认通过**

Run: 同 Step 2 + settings 集成测试
Expected: PASS

- [x] **Step 5: 回归并提交**

Run: 全量测试（163 → 167 项）
Expected: 全部通过

```bash
git add NCodeProcess/ncodeprocess/gui.py NCodeProcess/tests/test_gui.py
git commit -m "fix(gui): 全部子窗口居中显示（设置/统计/对比/编辑/确认页），全部程序信息窗口按屏幕适配"
```

---

### WP-02 Task 2.2: 文档同步

**Files:**
- Modify: `NCodeProcess/docs/NCodeProcess-审查与待办.md`（问题 9 标记 ✅，待办清单移除）
- Modify: `NCodeProcess/docs/NCodeProcess-程序理解与操作记录.md`（追加 2.12 条目）
- Modify: `NCodeProcess/docs/NCodeProcess-测试指南.md`、需求文档第 15 节、本地流程文档（基线 163 → 167）

- [x] **Step 1: 更新审查与待办**

问题 9 状态改为 ✅ 已修复（注明 `centered_position`/`_show_centered`、5 窗口居中、提交 `1f79a6a`）；待办清单对应行标记已处理。

- [x] **Step 2: 操作记录追加并提交**

追加 2.12 条目（居中纯函数、统一方法、5 窗口替换、屏幕适配、测试基线 167），并同步测试指南/需求文档/流程文档/计划文档。

```bash
git add NCodeProcess/docs
git commit -m "docs: WP-02 子窗口居中与屏幕适配完成，同步审查待办/操作记录/测试基线为 167 项"
```

---

## 四、后续工作包（Phase 2–5）范围定义

> 每个工作包开始前，按第三节模板展开为独立分计划（含逐行失败测试、实现代码、运行命令与提交），保存至 `docs/superpowers/plans/`。本节约定范围与验收，防止范围蔓延。

### WP-03: M03 紧贴 S 口径（依赖 D1）

**已确认（2026-08-05）：方案 A，维持现状。** 不改代码；仅将需求文档 FR-05.4 的「紧贴 S 数值之后」表述修正为「追加在 S 所在程序块末尾、分号之前」，并在需求文档第 13 节与审查待办中将该待确认项标记为已确认（方案 A）。

> ✅ **已完成（2026-08-05）**：FR-05.4/第 13 节/第 15 节与审查与待办问题 2 均已同步方案 A 口径；无代码改动、测试基线保持 198 项。

### WP-04: 备份/回收站兜底与只读目录预检

- `scan_directory` 或 `App.finish_scan` 增加只读探测：`os.access(directory, os.W_OK)`，不可写时状态栏/弹窗提示（需求 9.3「不得静默失败」）。
- `process_plan` 增加可选 `backup: bool = False` 与备份目录参数：处理前将待写/待删文件复制到 `backup/YYYYMMDD_HHMMSS/`（或移动至回收子目录），删除/覆盖失败时可恢复。
- GUI 处理确认页新增「先备份再清理」勾选项（需求 FR-03.7）。
- 验收：只读目录有提示；启用备份后删除文件可恢复。

> ✅ **已完成（2026-08-05，提交 `b2704fc`）**：只读探测加入 `scan_directory` 警告 + GUI 弹窗；`process_plan(backup=True)` 处理前快照到 `backup/时间戳`（`ProcessReport.backup_dir` 记录）；GUI 执行前询问备份。测试 +5 项（备份恢复/无备份不建目录/只读警告/警告弹窗/备份询问），基线 167 → 172。

### WP-05: 编码回环校验

- `_decode` 的 `gb18030`/`cp1252` 兜底增加可读性校验：解码后按原编码回编再解码一致才算成功；无法可靠识别时抛错并携带提示（`kind="encoding"`）。
- GUI 在「文件编码」强制指定模式下，`read_text` 失败时提示用户选择编码（对应审查问题 8 的「中文可读性/回环校验」）。
- 验收：错误编码文件不再被静默按 gb18030 接受；新测试锁定回环逻辑。

> ⚠️ **方案调整（2026-08-05）**：原「回环校验」经实验验证不可行——CPython 的 gb18030/cp1252/utf-8 解码输出均可按同编码回编（GB18030 覆盖全部 Unicode），校验永不触发。经用户确认改为：① `_decode` 候选顺序 utf-8 → gb2312 → gbk → gb18030 → cp1252 精确识别；② 含 NUL 数据拒绝解码（防二进制被静默处理）；③ `FilePlan.encoding` 记录并写入报告（可审计）；④ GUI 解析信息页显示「文件编码」，编码下拉新增 gbk/gb2312。
>
> ✅ **已完成（2026-08-05，提交 `c551175`）**：上述 4 项全部实现，测试 +8 项，基线 172 → 180。

### WP-06: 注释边界统一与刀具圆角比较

- 抽取统一工具函数（如 `code_part(line)` = `line.split("(", 1)[0]`）供 `add_m03`、`add_initial_tool_change`、`validate_program` 使用；`add_initial_tool_change` 的 `tool_ref.sub` 只作用于代码部分，注释中的 `(T2 备用)` 不被改写。
- `extract_tools` 普通立铣刀判定增加圆角比较：`CUTTER` 圆角与 `TOOLNO` 名义圆角一致（FR-4.3.16）。
- 验收：`(T2 备用)` 保持 T2；圆角不一致的两直径相同刀具不误判普通立铣刀。

> ✅ **已完成（2026-08-05，提交 `92fc728`）**：`code_part` 统一去注释（5 处复用）；换刀改写仅作用于代码部分；普通立铣刀判定增加圆角一致比较。测试 +3 项，基线 182 → 185。
>
> **样例刀具识别验证（2026-08-05，提交 `ba33370`）**：按 `样例文件/数控程序/刀具说明.txt` 分析 HASS/V5-2500B 共 50 个样例 APT 的 CUTTER/TOOLNO 记录，当前 `extract_tools` 识别 50/50 准确（圆鼻/球头/平底→普通、反锥+单边角度、钻头、中心钻）；新增 3 个样例格式回归测试锁定。基线 185 → 188。
>
> **刀具类型拆分与 T 形刀识别（2026-08-05，提交 `1bf9081`）**：按用户确认，普通立铣刀拆分为圆鼻（0<R<D/2）、球头（R=D/2）、平底（R=0）；T 形刀初步识别（直径比≥2 且无夹角）；铅笔刀已有正夹角逻辑并补充防误判测试；`DEFAULT_TOOL_TYPES` 同步扩展并保留「普通立铣刀」兼容旧配置。基线 188 → 191。

### WP-07: FR-07.2/07.3 校验缺口

- 刀具号一致性：正文 `T\d+` 引用对应头部 `Tn` 存在，否则报 error/warning（`tool-number-missing`）。
- 孤立指令：无坐标的孤立 F/S 行、切削运动前的异常进给/转速引用，按需求口径提示。
- 互斥 G/M 专项：扩展 `conflicting-motion` 之外的 G/M 互斥规则（如 G00 与 G01 混用块、M03/M05 同块），级别与现有规则对齐。
- 验收：新增问题类型进入报告与校验页；需求文档 FR-07.2/07.3 状态改为 ✅。

> ✅ **已完成（2026-08-05，提交 `d44f532`）**：新增 `tool-number-missing`（warning）、`isolated-parameter`（warning，S5000M03 合法行不误报）、`mutually-exclusive-m`（error，M03+M05/M08+M09 同块）、F 上离群（主体 F 中位数 1000–10000 时出现 ≥10000 且 ≥3 倍 → `feed-outlier` warning，用户新增要求）。测试 +7 项，基线 191 → 198。需求文档 FR-07.2 ✅、FR-07.3 互斥项标注已实现。

### WP-08: 异常归类细分

- `scan_directory`/`process_plan` 的 `except Exception` 按 `PermissionError`（permission）、`OSError`（io）、`UnicodeError`（encoding）区分 `Issue.kind` 与建议文案。
- 验收：不同失败原因在报告中可区分。

### WP-09: 未命名程序批量确认页

- `finish_scan` 的 `simpledialog.askstring` 循环改为：未命名程序 ≥2 时打开列表式确认窗口（每行一个源文件 + 程序名输入框），复用 `confirm_processing` 的独立确认页模式。
- 验收：10 个未命名程序一次确认完成，不再弹 10 个模态框。

### WP-10: 启发式阈值进 Config

- `Config` 新增 `feed_outlier_ratio`/`feed_outlier_min_value` 与 `multiple_spindle_warn`（或统一 `heuristic: dict`）；`validate_program` 读取配置；GUI 校验规则页增加对应输入项（仅本次运行生效）。
- 验收：阈值可在设置中调整并有测试锁定。

### WP-11: Batch 2 配置持久化（依赖 D2）

- 若 D2 选 B：`REGISTRY_DEFAULTS` 扩展 6 组 Batch 2 键；`load_all`/`save_all`/`clear_all` 自动覆盖；`App.__init__` 与 `_apply_settings_defaults` 从加载值初始化；更新 `tests/test_preferences.py` 与 `tests/test_gui.py` 默认值断言。
- 若 D2 选 A：关闭本 WP，在文档记录决策。

### WP-12: 细节清理（MSG 缩进/单实例/杀软）

- `apply_header` 替换 MSG 行时保留原行缩进（FR-4.2.5）。
- 单实例：启动时以工作目录下锁文件或 `CreateMutex`（Win32）防止双 EXE 竞态写 `special_tools.json`/报告时间戳；次实例提示后退出。
- 杀软误报：按 `SECURITY.md` 构建一次发布包，在本机主流杀软实测并记录结果，必要时准备误报申诉说明（对应审查问题 14）。

### WP-13: 查看器柱状图容错与索引校验

- `_draw_bar_chart`：`float(value)` 包 `try/except (TypeError, ValueError)`，失败按 0 处理并继续。
- `refresh_reports` 后选择保持：iid 改用路径索引表或刷新时校验 `selection[0] < len(report_paths)`；`_on_report_selected` 越界时忽略。
- 验收：字段非数值的报告可正常打开；外部删除报告后点击不崩。

### WP-14: 查看器大报告后台加载

- `_load_report` 拆为后台线程加载 + 主线程 `after` 轮询完成标志（可复用主工具 `_safe_after` 思路）；加载中状态栏/报告标签显示「加载中…」。
- 验收：大报告加载期间界面可交互。

### WP-15: 查看器低优先级清理

- 删除 `iter_stats_rows` 的 `"是" if parameter == "G00"` 死分支。
- `report_summary` 缺失字段显示「无数据」。
- 柱状图悬停数值提示与导出（Tk Canvas `tag_bind` + 右键保存 PostScript/PNG）。
- 校验问题页「仅错误」筛选勾选。

### WP-16: 版本提升与发版

- `ncodeprocess/__init__.py` 与 `ncodeprocessreportviewer/__init__.py` 的 `__version__` 提升（建议 1.1.0）；同步 `VERSION.txt`、`version_info.txt` 与发布资产测试（`test_release_assets.py`）。
- 按 `build_portable.ps1` 分别打包两项目，校验 EXE SHA256 与 `SHA256SUMS.txt` 一致，同步 `Publish/` 各目标。
- 更新两份发布说明（新增版本章节）、需求文档第 15 节状态、README 版本表述。
- 提交前按流程文档执行 README 对外规范检查与隐私审核。

### WP-17: 文档同步（贯穿）

每个 WP 完成后执行：核对受影响文档（需求文档状态标注、审查与待办、发布说明、测试指南基线、操作记录、两项目 README、根 README），确认无受影响文档后才允许结束该 WP。

---

## 五、贯穿性约定

1. **TDD**：每个功能改动按「失败测试 → 确认 RED → 最小实现 → GREEN → 全量回归」执行；`test_core.py`/`test_gui.py`/`test_preferences.py`/`test_report_viewer.py`/`test_release_assets.py` 与源码分层一一对应。
2. **Python 3.8 兼容**：代码与测试避免 3.9+ 语法；测试必须用 conda Python 3.8 环境运行。
3. **提交规范**：小步提交，提交信息含 `feat/fix/refactor/docs` 前缀与中文说明；不提交构建产物（`build/`、`dist/`、`Publish/`）、样例目录、`NCodeProcessData/`。
4. **隐私审核**：提交前检查本机路径/用户名/邮箱/代理地址，一律通用化（占位符）。
5. **测试基线**：主工具 198 项、查看器 6 项为基线；每个 WP 完成后同步更新测试指南与需求文档第 15 节。
6. **文档同步**：见 WP-17，任何变更不得遗漏受影响文档。
7. **执行确认**：每个 WP/Task 启动前须经用户确认（见「〇、决策点」的执行确认流程），不得擅自连续执行多个工作包。

---

## 六、自检（Spec Coverage）

| 来源 | 要求 | 对应任务 |
|---|---|---|
| 审查与待办（主）高优先级 1-3 | 线程安全、代际防护、进度反馈 | WP-01（第三节 Task 1.1–1.3） |
| 审查与待办（主）高优先级 9 | 全部子窗口居中 + 全部程序信息窗口屏幕适配 | WP-02（第三节 Task 2.1） |
| 审查与待办（主）中优先级 4-5 | 备份/回收站、只读预检 | WP-04 |
| 审查与待办（主）中优先级 8 | 编码识别、NUL 检测、gbk/gb2312 支持 | WP-05 |
| 审查与待办（主）中优先级 13 | 注释边界统一、圆角比较 | WP-06 |
| 需求文档 FR-07.2/07.3 🔶 | 刀具号一致性、孤立指令、互斥 G/M | WP-07 |
| 审查与待办（主）低优先级 7/10/12/15/17/14 | 异常细分、批量确认、阈值、MSG 缩进、单实例、杀软 | WP-08/09/10/12 |
| 需求文档第 13 节 ⏳ | M03 紧贴 S 口径 | WP-03（依赖 D1） |
| 发布说明已知限制 | Batch 2 持久化、版本号 | WP-11（依赖 D2）、WP-16 |
| 查看器审查问题 2/4/5 | 柱状图容错、索引校验、后台加载 | WP-13/14 |
| 查看器审查问题 1/3/7/8 | 死分支、无数据、图表增强、错误筛选 | WP-15 |
| 文档同步强制约定 | 全部变更同步文档 | WP-17 |

**已知未纳入：** 机床行程 X/Y/Z 检查（D3，用户决定暂不实施）；报告聚合/对比（查看器开放扩展项，未列入本计划范围）。
