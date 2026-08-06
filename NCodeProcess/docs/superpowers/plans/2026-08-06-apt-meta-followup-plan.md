# NCodeProcess APT 信息提取与交叉校验 + 收尾实施计划（2026-08-06）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 从 APT 源文件提取机械加工元数据（主轴/进给/冷却/刀具装夹/操作阶段/机床/坐标系/轨迹行程），并新增「APT 规划 vs MPF 执行」交叉校验；同时完成既有收尾项 F1（`apply_info` 后台化）与 F2（运行日志 traceback 埋点与截断去重）。报告与查看器消费新字段，主 253 + 查看器 18 项测试保持全绿。

**Architecture:** 全部新逻辑放在 `core.py`（流式 APT 解析 + 交叉校验纯函数），经 `build_plan` 挂到 `FilePlan` 与报告 `files[]`；`viewer.py` 新增「APT 信息」页签消费；F1/F2 为既有收尾（`gui.py`/`core.py` 小改）。APT 解析只针对每个程序的最新源文件（沿用 `latest_apt` 按 mtime 选择），复用流式读取与 mtime 缓存模式，不读全量历史。

**Tech Stack:** Python 3.8 / Tkinter / unittest / PyInstaller 5.13.2 + UPX（仅构建期）。

**执行纪律（AGENTS.md 强制）：**
- 每个 WP 启动前向用户说明范围、涉及文件、验收标准并取得确认。
- 每个 WP 完成后：全量测试绿 → `build_portable.ps1` 打包 → 用户实测确认 → 才允许 git 提交（测试类 WP 可不打包）。
- 提交信息使用 `feat:` / `fix:` / `perf:` / `docs:` 前缀 + 中文描述。
- 代码/UI/配置改动同步更新：需求文档、报告内容规范、发布说明、审查与待办、测试指南、用户手册、操作记录、README。
- 前置：工作区当前含未提交的 F7（查看器问题筛选/CSV 导出/加载状态 + 查看器文档），按 D-A6 先行提交，避免与后续改动叠加。

---

## 任务总览

| WP | 主题 | 优先级 | 依赖决策 | 涉及文件 | 验收标准 |
|---|---|---|---|---|---|
| WP-A1 | `extract_apt_meta`：APT 头部与加工参数解析 | 高 | 无 | `core.py`、`tests/test_core.py` | 解析机床/后处理表/操作列表/变换矩阵/SPINDL/FEDRAT/COOLNT/LOADTL；缓存生效 |
| WP-A2 | `extract_apt_toolpath`：轨迹统计 | 中 | D-A2 | `core.py`、`tests/test_core.py` | GOTO 点数/XYZ 行程/圆弧数/抬刀次数（含阈值） |
| WP-A3 | `build_plan` 挂载 + 报告字段 | 高 | D-A3 | `core.py`、`tests/test_core.py` | 最新 APT 的 meta/stats 进 `FilePlan` 与报告 `files[]`；`apt_summary` 全局摘要 |
| WP-A4 | APT↔MPF 交叉校验 | 高 | D-A1 | `core.py`、`tests/test_core.py` | 方向不一致 error；S/F 容差、COOLNT、LOADTL 不一致 warning；`apt-*` kind 入报告 |
| WP-A5 | 查看器「APT 信息」页签 | 中 | D-A3 | `viewer.py`、`tests/test_report_viewer.py` | 新页签展示 meta/toolpath；缺失回退；查看器 18 → 20+ 项 |
| WP-F1 | `apply_info` 后台重处理（收尾） | 高 | D-A5 | `gui.py`、`tests/test_gui.py` | 25 MPF 应用不再阻塞界面；测试同步化全绿 |
| WP-F2 | 运行日志埋点补齐与截断去重（收尾） | 中 | D-A5 | `core.py`、`tests/test_core.py` | `process_mpf`/APT 异常 traceback 进 `runtime_log`；截断警告只出现一次 |
| WP-A8 | 文档同步 | 贯穿 | 无 | 需求/报告规范/手册/审查/发布说明 | 新字段与 `apt-*` kind 入文档；基线更新 |

## 决策点（Phase 0，执行前确认）

| 编号 | 决策 | 选项 | 建议 |
|---|---|---|---|
| D-A1 | 交叉校验级别 | A：主轴方向不一致 error、其余（S/F/COOLNT/LOADTL）warning（推荐）；B：全部 warning | A |
| D-A2 | 轨迹统计范围 | A：GOTO 点数 + XYZ 行程 + 圆弧数 + 抬刀次数 + 路径总长；B：仅点数与行程（推荐，路径长度收益一般且报告体积增加） | B |
| D-A3 | 报告粒度 | A：每文件 `apt_meta`/`toolpath_stats` + 报告级 `apt_summary`（推荐）；B：仅每文件 | A |
| D-A4 | 估算切削时间 | A：粗估纳入；B：不纳入（推荐，粗估易误导，行程/档位已足够） | B |
| D-A5 | 是否合并 F1/F2 收尾 | A：合并执行（推荐）；B：仅 APT 相关 | A |
| D-A6 | 未提交的 F7 改动 | A：开工前先提交（推荐）；B：并入首批提交 | A |

---

## 文件结构

- `ncodeprocess/core.py`：新增 `AptMeta`/`ToolpathStats` dataclass、`extract_apt_meta`、`extract_apt_toolpath`、`crosscheck_apt`；`_APT_META_CACHE`/`_APT_TOOLPATH_CACHE`；`FilePlan` 增加 `apt_meta`/`apt_toolpath`；`build_plan`/`process_plan` 挂载与报告字段；F2 的 `emit_event` 与 `_reported_dropped`。
- `ncodeprocess/gui.py`：F1 的 `reprocess_plans` 纯函数与 `_finish_apply_info`。
- `ncodeprocessreportviewer/viewer.py`：`apt_meta_rows`/`toolpath_stats_rows` 纯函数与「APT 信息」页签。
- `tests/test_core.py`、`tests/test_gui.py`、`tests/test_report_viewer.py`：新增测试。
- `docs/`：报告内容规范（`apt_meta`/`toolpath_stats`/`apt_summary`/`apt-*` kind）、需求文档（FR-06/07 补充）、用户手册、审查与待办、发布说明。

---

## WP-A1: `extract_apt_meta`：APT 头部与加工参数解析

**Files:** `ncodeprocess/core.py`、`tests/test_core.py`

- [ ] **Step 1: 新增 dataclass 与常量正则（core.py，`extract_tools` 附近）**

```python
@dataclass
class AptMeta:
    """APT 源文件的机械加工元数据（规划值，供报告与交叉校验）。"""
    machine: str = ""
    pp_table: str = ""
    catia_version: str = ""
    generated_at: str = ""
    operate: str = ""
    operations: List[str] = field(default_factory=list)
    transform: Optional[List[float]] = None          # $$ 位姿矩阵数值（样例为 3×4，缺失为 None）
    spindles: List[Tuple[str, str, str]] = field(default_factory=list)  # (转速, 单位, 方向)
    feeds: List[Tuple[str, str]] = field(default_factory=list)          # (进给, 单位) 去重保序
    coolant: List[str] = field(default_factory=list)                    # ON/OFF/MIST/FLOOD
    tool_loads: List[int] = field(default_factory=list)                 # LOADTL 首参数（刀具号）

    def to_dict(self):
        return asdict(self)


APT_MACHIN_RE = re.compile(r"^\$\$\s*MACHIN\s+(.+)$", re.I)
APT_PPTABLE_RE = re.compile(r"^\$\$\s*PP-TABLE\s*:\s*(.+)$", re.I)
APT_VERSION_RE = re.compile(r"^\$\$\s*CATIA\s+APT\s+VERSION\s+(.+)$", re.I)
APT_GENERATED_RE = re.compile(r"^\$\$\s*Generated\s+on\s+(.+)$", re.I)
APT_OPERATE_RE = re.compile(r"^\$\$\s*OPERATE\s+(.+)$", re.I)
APT_OPERATION_RE = re.compile(r"^\$\$\s*OPERATION\s+NAME\s*:\s*(.+)$", re.I)
APT_TRANSFORM_RE = re.compile(
    r"^\$\$\s+([+-]?\d+\.\d+)\s+([+-]?\d+\.\d+)\s+([+-]?\d+\.\d+)\s+([+-]?\d+\.\d+)\s*$")
APT_SPINDL_RE = re.compile(r"SPINDL\s*/\s*([0-9.]+)\s*,\s*([A-Z]+)\s*,\s*(CLW|CCLW)", re.I)
APT_FEDRAT_RE = re.compile(r"FEDRAT\s*/\s*([0-9.]+)\s*,\s*([A-Z]+)", re.I)
APT_COOLNT_RE = re.compile(r"COOLNT\s*/\s*(ON|OFF|MIST|FLOOD)", re.I)
APT_LOADTL_RE = re.compile(r"LOADTL\s*/\s*(\d+)(?:\s*,\s*(\d+))?", re.I)
```

- [ ] **Step 2: 新增 `extract_apt_meta`（流式单遍）**

```python
def extract_apt_meta(path: Path, encoding: str = "auto") -> AptMeta:
    """流式解析 APT 文件，返回头部元数据与加工参数（仅保留去重值）。"""
    meta = AptMeta()
    seen_ops = set()
    seen_spindles = set()
    seen_feeds = set()
    seen_coolant = set()
    seen_loads = set()
    transform = []
    with path.open("rb") as stream:
        for raw_line in stream:
            if len(raw_line) > 400:
                raw_line = raw_line[:400]
            try:
                line = _decode(raw_line, encoding)[0].strip()
            except (UnicodeDecodeError, ValueError):
                continue
            m = APT_MACHIN_RE.match(line)
            if m and not meta.machine:
                meta.machine = m.group(1).strip()
                continue
            m = APT_PPTABLE_RE.match(line)
            if m and not meta.pp_table:
                meta.pp_table = m.group(1).strip()
                continue
            m = APT_VERSION_RE.match(line)
            if m and not meta.catia_version:
                meta.catia_version = m.group(1).strip()
                continue
            m = APT_GENERATED_RE.match(line)
            if m and not meta.generated_at:
                meta.generated_at = m.group(1).strip()
                continue
            m = APT_OPERATE_RE.match(line)
            if m and not meta.operate:
                meta.operate = m.group(1).strip()
                continue
            m = APT_OPERATION_RE.match(line)
            if m:
                name = m.group(1).strip()
                if name not in seen_ops:
                    seen_ops.add(name)
                    meta.operations.append(name)
                continue
            m = APT_TRANSFORM_RE.match(line)
            if m:
                transform.extend(float(part) for part in m.groups())
                continue
            m = APT_SPINDL_RE.search(line)
            if m:
                key = (m.group(1), m.group(2).upper(), m.group(3).upper())
                if key not in seen_spindles:
                    seen_spindles.add(key)
                    meta.spindles.append(key)
                continue
            m = APT_FEDRAT_RE.search(line)
            if m:
                key = (m.group(1), m.group(2).upper())
                if key not in seen_feeds:
                    seen_feeds.add(key)
                    meta.feeds.append(key)
                continue
            m = APT_COOLNT_RE.search(line)
            if m:
                value = m.group(1).upper()
                if value not in seen_coolant:
                    seen_coolant.add(value)
                    meta.coolant.append(value)
                continue
            m = APT_LOADTL_RE.search(line)
            if m:
                number = int(m.group(1))
                if number not in seen_loads:
                    seen_loads.add(number)
                    meta.tool_loads.append(number)
    if transform:
        meta.transform = transform
    return meta
```

> 说明：HASS 与 V5-2500B 的 APT 均为 CATIA APT 1.0 结构（100 个样例实测一致），`$$` 记录与参数记录统一按行解析；`_decode` 对整行解码失败时跳过（防续行截断）。

- [ ] **Step 3: 新增缓存（与 `_APT_TOOL_CACHE` 同模式）**

```python
_APT_META_CACHE: Dict[str, Tuple[int, int, str, AptMeta]] = {}

def _extract_apt_meta_cached(path: Path, encoding: str = "auto") -> AptMeta:
    stat = path.stat()
    key = str(path.resolve())
    cached = _APT_META_CACHE.get(key)
    if cached and cached[:3] == (stat.st_mtime_ns, stat.st_size, encoding):
        return cached[3]
    meta = extract_apt_meta(path, encoding)
    if len(_APT_META_CACHE) > 1000:
        _APT_META_CACHE.clear()
    _APT_META_CACHE[key] = (stat.st_mtime_ns, stat.st_size, encoding, meta)
    return meta
```

- [ ] **Step 4: 新增失败测试（用样例真实格式）**

```python
    def test_apt_meta_extracts_header_and_process_records(self):
        root = self.make_dir()
        apt = root / "x.aptsource"
        apt.write_text(
            "$$     Generated on 2026年7月31日 9:30:05\n"
            "$$     CATIA APT VERSION 1.0\n"
            "$$ PP-TABLE : HPM1150U.PPTable\n"
            "$$ FILENAME  D0354F31311-201.CATProcess\n"
            "$$ OPERATE   Part Operation.1\n"
            "$$ MACHIN    3-axis Machine.1\n"
            "PPRINT PROGNAME AG6D311A0101\n"
            "COOLNT/ON\n"
            "$$ OPERATION NAME : Tool Change.1\n"
            "$$ OPERATION NAME : Roughing.3\n"
            "CUTTER/ 20.000000, 3.000000\n"
            "TOOLNO/1, 20.000000, 3.000000,, 120.000000,$\n"
            "LOADTL/1,1\n"
            "SPINDL/ 5000.0000,RPM,CLW\n"
            "FEDRAT/ 3000.0000,MMPM\n"
            "FEDRAT/ 6000.0000,MMPM\n",
            encoding="utf-8",
        )
        meta = extract_apt_meta(apt)
        self.assertEqual(meta.machine, "3-axis Machine.1")
        self.assertEqual(meta.pp_table, "HPM1150U.PPTable")
        self.assertEqual(meta.catia_version, "1.0")
        self.assertEqual(meta.operations, ["Tool Change.1", "Roughing.3"])
        self.assertEqual(meta.coolant, ["ON"])
        self.assertEqual(meta.spindles, [("5000.0000", "RPM", "CLW")])
        self.assertEqual(meta.feeds, [("3000.0000", "MMPM"), ("6000.0000", "MMPM")])
        self.assertEqual(meta.tool_loads, [1])

    def test_apt_meta_parses_transform_matrix(self):
        text = "$$    -0.99863    -0.05232     0.00137 18984.32985\n"
        apt = self.make_dir() / "m.aptsource"
        apt.write_text(text, encoding="utf-8")
        meta = extract_apt_meta(apt)
        self.assertEqual(len(meta.transform or []), 4)
        self.assertAlmostEqual(meta.transform[3], 18984.32985)

    def test_apt_meta_cached_by_mtime(self):
        root = self.make_dir()
        apt = root / "c.aptsource"
        apt.write_text("$$ MACHIN  A\n", encoding="utf-8")
        first = _extract_apt_meta_cached(apt)
        second = _extract_apt_meta_cached(apt)
        self.assertIs(first, second)
        apt.write_text("$$ MACHIN  B\n", encoding="utf-8")
        third = _extract_apt_meta_cached(apt)
        self.assertEqual(third.machine, "B")
```

- [ ] **Step 5: 回归**：`D:\anaconda3\envs\python38\python.exe -m unittest tests.test_core -v` 全绿。
- [ ] **Step 6: 提交门**：`feat(core): APT 元数据流式解析（机床/操作/主轴/进给/冷却/装夹/矩阵）`。

---

## WP-A2: `extract_apt_toolpath`：轨迹统计（依赖 D-A2=B）

**Files:** `ncodeprocess/core.py`、`tests/test_core.py`

- [ ] **Step 1: 新增 dataclass 与解析函数**

```python
@dataclass
class ToolpathStats:
    goto_count: int = 0
    min_x: float = 0.0
    max_x: float = 0.0
    min_y: float = 0.0
    max_y: float = 0.0
    min_z: float = 0.0
    max_z: float = 0.0
    arc_count: int = 0
    retract_count: int = 0

    def to_dict(self):
        return asdict(self)


def extract_apt_toolpath(path: Path, encoding: str = "auto", retract_threshold: float = RETRACT_Z_THRESHOLD) -> ToolpathStats:
    """流式统计 GOTO 轨迹：点数、XYZ 行程、圆弧数、抬刀次数（Z ≥ 阈值）。"""
    stats = ToolpathStats()
    nums = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?")
    initialized = False
    with path.open("rb") as stream:
        for raw_line in stream:
            if raw_line.startswith(b"GOTO"):
                values = [float(value) for value in nums.findall(raw_line.decode("ascii", errors="ignore"))]
                if len(values) < 3:
                    continue
                x, y, z = values[0], values[1], values[2]
                if not initialized:
                    stats.min_x = stats.max_x = x
                    stats.min_y = stats.max_y = y
                    stats.min_z = stats.max_z = z
                    initialized = True
                else:
                    stats.min_x = min(stats.min_x, x)
                    stats.max_x = max(stats.max_x, x)
                    stats.min_y = min(stats.min_y, y)
                    stats.max_y = max(stats.max_y, y)
                    stats.min_z = min(stats.min_z, z)
                    stats.max_z = max(stats.max_z, z)
                stats.goto_count += 1
                if z >= retract_threshold:
                    stats.retract_count += 1
            elif b"CIRCLE" in raw_line:
                stats.arc_count += 1
    return stats
```

> 说明：首次 GOTO 用该行坐标初始化全部极值，之后逐点更新；`arc_count` 按 `TLON,GOFWD (CIRCLE/...` 续行中的 `CIRCLE` 计数；坐标不足 3 个的 GOTO 行跳过。

- [ ] **Step 2: 新增失败测试（样例真实轨迹）**

```python
    def test_toolpath_stats_streaming(self):
        root = self.make_dir()
        apt = root / "t.aptsource"
        apt.write_text(
            "GOTO / -334.44634,  167.43157,  100.00000\n"
            "GOTO / -335.27541,  165.68970,   -1.94071\n"
            "TLON,GOFWD/ (CIRCLE/ 1.0, 2.0, 3.0,$\n"
            "GOTO / -330.00000,  160.00000,   20.00000\n",
            encoding="utf-8",
        )
        stats = extract_apt_toolpath(apt, retract_threshold=20.0)
        self.assertEqual(stats.goto_count, 3)
        self.assertAlmostEqual(stats.min_z, -1.94071)
        self.assertAlmostEqual(stats.max_z, 100.0)
        self.assertEqual(stats.arc_count, 1)
        self.assertEqual(stats.retract_count, 2)  # Z=100 与 Z=20 均 ≥ 阈值
```

- [ ] **Step 3: 缓存与回归**（同 WP-A1 模式加 `_APT_TOOLPATH_CACHE`）；`tests.test_core -v` 全绿。
- [ ] **Step 4: 提交门**：`feat(core): APT 轨迹统计（行程/点数/圆弧/抬刀）`。

---

## WP-A3: `build_plan` 挂载与报告字段（依赖 D-A3=A）

**Files:** `ncodeprocess/core.py`、`tests/test_core.py`

- [ ] **Step 1: `FilePlan` 增加字段**

```python
    apt_meta: Optional[AptMeta] = None
    apt_toolpath: Optional[ToolpathStats] = None
```

- [ ] **Step 2: `build_plan` 对最新 APT 解析并挂载**

在 `latest_apt` 循环内（解析 tools 之后）：

```python
            try:
                apt_plan.apt_meta = _extract_apt_meta_cached(directory / apt_plan.source, config.encoding)
                apt_plan.apt_toolpath = extract_apt_toolpath(directory / apt_plan.source, config.encoding, config.retract_z_threshold)
            except Exception:
                apt_plan.apt_meta = None
                apt_plan.apt_toolpath = None
```

`process_mpf` 内（`analyze_program` 之后）：

```python
                apt_plan = latest_apt.get(f.program)
                f.apt_meta = apt_plan[1].apt_meta if apt_plan else None
                f.apt_toolpath = apt_plan[1].apt_toolpath if apt_plan else None
```

> 说明：`latest_apt` 在并行处理前构建完成，且元数据已在其中挂到对应 APT 计划，线程内只读引用，无竞争。

- [ ] **Step 3: 报告 `files[]` 新增字段**

`process_plan` 的 item 增加：

```python
        "apt_meta": f.apt_meta.to_dict() if f.apt_meta else None,
        "toolpath_stats": f.apt_toolpath.to_dict() if f.apt_toolpath else None,
```

报告级 `apt_summary`（`ProcessReport` 新增字段，`process_plan` 结束时聚合）：

```python
    report.apt_summary = {
        "machines": sorted({meta.machine for f in scan.files if f.apt_meta and f.apt_meta.machine}),
        "spindle_speeds": sorted({float(speed) for f in scan.files if f.apt_meta for speed, _u, _d in f.apt_meta.spindles}),
        "tool_loads": sorted({number for f in scan.files if f.apt_meta for number in f.apt_meta.tool_loads}),
    }
```

- [ ] **Step 4: 新增失败测试**

```python
    def test_build_plan_attaches_apt_meta_and_report_fields(self):
        root = self.make_dir()
        (root / "x_P.MPF").write_text('MSG("PROGRAM:P")\nN1S1000M03\nN2M30\n', encoding="utf-8")
        (root / "x_P_I.aptsource").write_text(
            "$$ MACHIN 3-axis Machine.1\n$$ OPERATION NAME : Roughing.3\n"
            "SPINDL/ 1000.0000,RPM,CLW\nFEDRAT/ 500.0000,MMPM\n"
            "GOTO / 0.0, 0.0, 10.0\nGOTO / 10.0, 0.0, -1.0\n",
            encoding="utf-8",
        )
        cfg = self._cfg()
        plan = build_plan(scan_directory(str(root), cfg), DEFAULT_INFO, cfg)
        mpf = self._mpf(plan)
        self.assertEqual(mpf.apt_meta.machine, "3-axis Machine.1")
        self.assertEqual(mpf.apt_toolpath.goto_count, 2)
        report = process_plan(plan, str(root), cfg)
        item = report.files[0]
        self.assertEqual(item["apt_meta"]["machine"], "3-axis Machine.1")
        self.assertEqual(item["toolpath_stats"]["goto_count"], 2)
        self.assertIn("3-axis Machine.1", report.apt_summary["machines"])
```

- [ ] **Step 5: 回归 + 提交门**：`feat(core): APT 元数据/轨迹统计挂载计划与报告`。

---

## WP-A4: APT↔MPF 交叉校验（依赖 D-A1=A）

**Files:** `ncodeprocess/core.py`、`tests/test_core.py`

- [ ] **Step 1: 新增纯函数 `crosscheck_apt`**

```python
def crosscheck_apt(mpf_text: str, meta: AptMeta, filename: str, config: Config) -> List[Issue]:
    """APT 规划信息与 MPF 执行指令交叉校验。

    主轴方向不一致为 error（CLW→M03、CCLW→M04）；S/F 数值容差、冷却液、
    刀具装夹不一致为 warning。APT 为规划值，后处理可能取整/倍率，故一律容差。
    """
    issues: List[Issue] = []
    lines = mpf_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    start = _header_end(lines)
    s_values = []
    f_values = []
    has_m03 = has_m04 = has_m08 = has_m09 = False
    t_calls = set()
    for i, raw_line in enumerate(lines[start:], start=start + 1):
        code = code_part(raw_line)
        upper = code.upper()
        if M03_RE.search(code):
            has_m03 = True
        if M04_RE.search(code):
            has_m04 = True
        if M08_RE.search(code):
            has_m08 = True
        if M09_RE.search(code):
            has_m09 = True
        for match in TOOL_CALL_RE.finditer(code):
            t_calls.add(int(match.group(1)))
        for parameter in ADDR_RE.finditer(code):
            key = parameter.group(1).upper()
            if key == "S":
                s_values.append(float(parameter.group(2)))
            elif key == "F":
                f_values.append(float(parameter.group(2)))

    def tolerance(reference, ratio=0.01, minimum=1.0):
        return max(reference * ratio, minimum)

    if meta.spindles:
        directions = {direction.upper() for _speed, _units, direction in meta.spindles}
        if "CLW" in directions and has_m04 and not has_m03:
            issues.append(Issue(filename, start + 1, "", "apt-spindle-direction", "error",
                                "APT 规划主轴正转（CLW），但程序正文使用 M04 反转，请核对旋转方向"))
        if "CCLW" in directions and has_m03 and not has_m04:
            issues.append(Issue(filename, start + 1, "", "apt-spindle-direction", "error",
                                "APT 规划主轴反转（CCLW），但程序正文使用 M03 正转，请核对旋转方向"))
        apt_speeds = [float(speed) for speed, _units, _direction in meta.spindles]
        if s_values and not any(
            any(abs(value - speed) <= tolerance(speed) for speed in apt_speeds) for value in s_values
        ):
            issues.append(Issue(filename, start + 1, "", "apt-spindle-mismatch", "warning",
                                "MPF 的 S 转速均不在 APT 规划转速集合内，请核对转速"))

    if meta.feeds and f_values:
        apt_feeds = [float(feed) for feed, _units in meta.feeds]
        if not all(
            any(abs(value - feed) <= tolerance(feed, 0.10, 1.0) for feed in apt_feeds) for value in f_values
        ):
            issues.append(Issue(filename, start + 1, "", "apt-feed-mismatch", "warning",
                                "MPF 中存在 F 值不在 APT 规划进给集合 ±10% 内，请核对进给"))

    if "ON" in [value.upper() for value in meta.coolant] and not has_m08:
        issues.append(Issue(filename, start + 1, "", "apt-coolant-missing", "warning",
                            "APT 规划冷却液开启（COOLNT/ON），但程序正文未找到 M08"))
    if "OFF" in [value.upper() for value in meta.coolant] and has_m08 and not has_m09:
        issues.append(Issue(filename, start + 1, "", "apt-coolant-missing", "warning",
                            "APT 规划冷却液关闭（COOLNT/OFF），但程序正文未找到 M09"))

    if meta.tool_loads and t_calls and not set(meta.tool_loads).issubset(t_calls):
        missing = sorted(set(meta.tool_loads) - t_calls)
        issues.append(Issue(filename, start + 1, "", "apt-tool-load-mismatch", "warning",
                            "APT 规划装夹刀具 T%s 未在正文调用，请核对换刀序列" % "、".join(str(n) for n in missing)))
    return issues
```

- [ ] **Step 2: `build_plan`/`reprocess_file` 调用并入 issues**

`process_mpf` 中 `f.issues.extend(validation_issues)` 之后：

```python
                if f.apt_meta is not None:
                    f.issues.extend(crosscheck_apt(new, f.apt_meta, f.source, config))
```

`reprocess_file` 同样在 `analyze_program` 后追加（有 `f.apt_meta` 时）。

- [ ] **Step 3: 新增失败测试**

```python
    def test_crosscheck_spindle_direction_error(self):
        meta = AptMeta(spindles=[("5000.0000", "RPM", "CLW")])
        issues = crosscheck_apt("MSG(\"PROGRAM:P\")\nN1S5000M04\nN2M30\n", meta, "P.MPF", self._cfg(auto_m03=False))
        self.assertTrue(any(i.kind == "apt-spindle-direction" and i.severity == "error" for i in issues))

    def test_crosscheck_tolerances_and_missing(self):
        meta = AptMeta(spindles=[("5000.0000", "RPM", "CLW")], feeds=[("3000.0000", "MMPM")],
                       coolant=["ON"], tool_loads=[1, 2])
        issues = crosscheck_apt("MSG(\"PROGRAM:P\")\nN1T1M06\nN2S5000M03\nN3G1X10F3000\nN4M30\n", meta, "P.MPF", self._cfg(auto_m03=False))
        self.assertFalse(any(i.kind.startswith("apt-") for i in issues))  # 全部匹配
        issues = crosscheck_apt("MSG(\"PROGRAM:P\")\nN1S9000M03\nN2G1X10F9999\nN3M30\n", meta, "P.MPF", self._cfg(auto_m03=False))
        kinds = {i.kind for i in issues}
        self.assertTrue({"apt-spindle-mismatch", "apt-feed-mismatch", "apt-coolant-missing", "apt-tool-load-mismatch"} <= kinds)
```

- [ ] **Step 4: 回归 + 提交门**：`feat(core): APT↔MPF 交叉校验（方向 error、S/F/冷却/装夹 warning）`。

---

## WP-A5: 查看器「APT 信息」页签（依赖 D-A3=A）

**Files:** `ncodeprocessreportviewer/viewer.py`、`tests/test_report_viewer.py`

- [ ] **Step 1: 新增纯函数**

```python
def apt_meta_rows(item: dict) -> List[Tuple[str, str]]:
    """把文件项的 apt_meta/toolpath_stats 展开为 (键, 值) 展示行。"""
    meta = item.get("apt_meta") or {}
    stats = item.get("toolpath_stats") or {}
    rows = []
    if not meta and not stats:
        return rows
    rows.append(("机床型号", str(meta.get("machine") or "")))
    rows.append(("后处理表", str(meta.get("pp_table") or "")))
    rows.append(("CATIA APT 版本", str(meta.get("catia_version") or "")))
    rows.append(("生成时间", str(meta.get("generated_at") or "")))
    rows.append(("操作", "、".join(str(v) for v in (meta.get("operations") or []))))
    spindles = ["%s%s %s" % (speed, units, direction) for speed, units, direction in (meta.get("spindles") or [])]
    rows.append(("主轴规划", "、".join(spindles)))
    feeds = ["%s%s" % (value, units) for value, units in (meta.get("feeds") or [])]
    rows.append(("进给规划", "、".join(feeds)))
    rows.append(("冷却液", "、".join(str(v) for v in (meta.get("coolant") or []))))
    rows.append(("装夹刀具", "、".join("T%d" % n for n in (meta.get("tool_loads") or []))))
    if stats:
        rows.append(("轨迹点数", str(stats.get("goto_count", 0))))
        rows.append(("X 行程", "%.3f .. %.3f" % (stats.get("min_x", 0), stats.get("max_x", 0))))
        rows.append(("Y 行程", "%.3f .. %.3f" % (stats.get("min_y", 0), stats.get("max_y", 0))))
        rows.append(("Z 行程", "%.3f .. %.3f" % (stats.get("min_z", 0), stats.get("max_z", 0))))
        rows.append(("圆弧数", str(stats.get("arc_count", 0))))
        rows.append(("抬刀次数", str(stats.get("retract_count", 0))))
    return rows
```

- [ ] **Step 2: 新增「APT 信息」页签**

`ReportViewer._build` 在「运行日志」后插入 `apt_page`（`ttk.LabelFrame` + 键值表格 + 垂直滚动）；`_update_views` 中调用 `_fill_apt(selected)`；选中「全部」时展示全部文件的 meta 汇总行。`apt-*` kind 由问题表通用着色覆盖，无需额外规则。

- [ ] **Step 3: 新增失败测试**

```python
    def test_apt_meta_rows_expand_fields(self):
        from ncodeprocessreportviewer.viewer import apt_meta_rows
        item = {"apt_meta": {"machine": "3-axis Machine.1", "operations": ["Roughing.3"],
                             "spindles": [["5000.0000", "RPM", "CLW"]], "feeds": [["3000.0000", "MMPM"]],
                             "coolant": ["ON"], "tool_loads": [1]},
                "toolpath_stats": {"goto_count": 2, "min_x": 0.0, "max_x": 10.0}}
        rows = dict(apt_meta_rows(item))
        self.assertEqual(rows["机床型号"], "3-axis Machine.1")
        self.assertIn("Roughing.3", rows["操作"])
        self.assertEqual(rows["主轴规划"], "5000.0000RPM CLW")
        self.assertEqual(rows["轨迹点数"], "2")
        self.assertEqual(apt_meta_rows({}), [])
```

- [ ] **Step 4: UI 冒烟**：构建查看器加载含 `apt_meta` 的样例报告，切到「APT 信息」页签断言表非空。
- [ ] **Step 5: 回归**：查看器 18 → 20 项左右全绿；提交门：`feat(viewer): APT 信息页签与轨迹统计展示`。

---

## WP-F1: `apply_info` 后台重处理（收尾，依赖 D-A5=A）

**Files:** `ncodeprocess/gui.py`、`tests/test_gui.py`

- [ ] **Step 1: 模块级纯函数 `reprocess_plans`**（与 `2026-08-06-round2-followup-plan.md` WP-F1 相同代码，`reprocess_file` 循环抽出）。
- [ ] **Step 2: `apply_info` 改后台线程 + `_finish_apply_info`**（同上计划 Step 3 代码；主线程捕获 `config`/计划快照）。
- [ ] **Step 3: 测试同步化**：`test_gui.py` 补 `import threading` 与 `_sync_thread` 桩，`test_apply_info_*` 统一加 `patch("ncodeprocess.gui.threading.Thread", _sync_thread(threading.Thread))`。
- [ ] **Step 4: 回归 + 提交门**：`perf(gui): 全部应用改为后台重处理，消除 UI 冻结`。

---

## WP-F2: 运行日志埋点补齐与截断去重（收尾，依赖 D-A5=A）

**Files:** `ncodeprocess/core.py`、`tests/test_core.py`

- [ ] **Step 1: `process_mpf` 的 `except Exception` 补 `emit_event("error", ..., detail=traceback.format_exc())`**（`traceback` 已导入）。
- [ ] **Step 2: `scan_directory` APT 前缀解析的 `except Exception` 补 `emit_event("warning", "scan_warning", ..., detail=traceback.format_exc())`**。
- [ ] **Step 3: `RuntimeLog` 增加 `_reported_dropped`，`snapshot` 仅当 `self._dropped > self._reported_dropped` 时追加截断警告并更新计数**（与 round2 计划 Step 2c 相同）。
- [ ] **Step 4: 测试**：`test_process_mpf_error_emits_runtime_event`、`test_snapshot_truncation_warning_appears_once`（round2 计划已给）；`CoreTests.setUp` 加 `reset_runtime_log()`。
- [ ] **Step 5: 回归 + 提交门**：`fix(core): 处理/APT 异常 traceback 进运行日志，截断警告去重`。

---

## WP-A8: 文档同步（贯穿）

- [ ] 报告内容规范：新增 `files[].apt_meta`/`files[].toolpath_stats`、报告级 `apt_summary` 字段字典与示例；`kind` 枚举表追加 `apt-spindle-direction`/`apt-spindle-mismatch`/`apt-feed-mismatch`/`apt-coolant-missing`/`apt-tool-load-mismatch`。
- [ ] 需求文档：FR-06/FR-07 补充「APT 元数据提取与交叉校验」条款；第 15 节实施状态更新。
- [ ] 用户手册：说明 APT 规划信息在报告/查看器的展示与交叉校验口径（容差、warning/error 级别）。
- [ ] 审查与待办：登记本计划各 WP 与提交号；待办更新（机床行程检查数据源已就绪）。
- [ ] 测试指南/发布说明：基线数字更新（主 253+、查看器 20+）。

---

## 执行顺序与确认流程

1. Phase 0 确认 D-A1~D-A6（建议：A/A/A/B/A/A）。
2. 建议顺序：WP-A1 → WP-A2 → WP-A3 → WP-A4 → WP-A5 → WP-F1 → WP-F2 → WP-A8。
3. 每个 WP 启动前单独说明范围并确认；完成后全量测试 → 打包（测试类除外）→ 用户实测 → 确认后提交。
4. 完成后的计划文档按仓库惯例归档至 `docs/archive/`（本地保留，git 移除跟踪）。

---

## Self-Review 记录

- **Spec 覆盖**：APT 分析结论六类信息均有 WP（元数据 A1、轨迹 A2、挂载/报告 A3、交叉校验 A4、查看器 A5）；既有收尾 F1/F2 合并；估算时间按 D-A4 不纳入；机床行程检查登记待办（数据源已就绪）。
- **占位符检查**：每个 WP 含完整代码、测试名与命令；无 TBD。
- **签名/一致性**：`AptMeta`/`ToolpathStats`/`crosscheck_apt`/`apt_meta_rows` 命名唯一；`FilePlan` 新字段不影响既有构造（dataclass 默认值）；`extract_apt_meta` 与 `_extract_apt_tools_from_path` 共用流式+缓存模式；`apt-*` kind 全部经 `Issue` 统一进入报告与问题表，无需查看器专项着色。
