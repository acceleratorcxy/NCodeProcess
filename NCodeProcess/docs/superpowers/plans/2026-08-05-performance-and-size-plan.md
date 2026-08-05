# NCodeProcess 性能与打包体积优化实施计划（2026-08-05）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 25 个 MPF 的处理管线从约 2.1s 降到 1.5s 以内（100 文件约 8s → 5s），并把主程序 EXE 从 8.95MB 压到约 7MB（不含 UPX）或 4.5~5.5MB（含 UPX），且行为零回退、Windows 7 兼容不变。

**Architecture:** 性能改动集中在 `core.py`（正则提升 + F 离群单次遍历；管线单次切行为可选重构）与 `gui.py`（可选：应用操作只重处理受影响计划）。体积改动分四层：① `gui.py` 用 FNV-1a 替代 `hashlib.md5` 并在主 spec 排除 `hashlib`/`_hashlib`，去掉 libcrypto-3-x64.dll（-1.9MB）；② 两个 spec 补充 excludes；③ 安装并启用 UPX（需杀软复测门）；④ tcl/tk 数据裁剪实验（Win7 实测门）。全部 WP 均以 220 项主测试 + 6 项查看器测试全绿为前提。

**Tech Stack:** Python 3.8 / Tkinter / unittest / PyInstaller 5.13.2 + tinyaes（构建期）/ UPX 3.96 或 4.x（仅构建期，D-S1 决定）。

**执行纪律（AGENTS.md 强制）：**
- 每个 WP 启动前向用户说明范围、涉及文件、验收标准并取得确认。
- 每个 WP 完成后：全量测试绿 → `build_portable.ps1` 打包 → 用户实测（体积类 WP 另需杀软/Windows 7 冒烟）→ 确认后提交。
- 体积类 WP 必须记录「优化前/后」体积与 ZIP 体积，写入审查与待办。
- 提交信息使用 `perf:` / `build:` / `docs:` 前缀 + 中文描述。

---

## 基准数据（2026-08-05 实测）

| 指标 | 现值 |
|---|---|
| build_plan 耗时（25 个 MPF / 125 文件，HASS） | 2.103s |
| build_plan 耗时（重复扫描，缓存生效） | 2.012s |
| 100 文件外推 | 约 8s |
| dist/NCodeProcess.exe | 8.95 MB |
| dist/NCodeProcess-Windows7-Portable.zip | 8.73 MB |
| 查看器 dist/NCodeProcessReportViewer.exe | 7.05 MB |
| 归档内 libcrypto-3-x64.dll（压缩后） | 1.89 MB |
| 归档内 python38.dll / tcl86t / tk86t | 2.02 / 0.83 / 0.68 MB |

---

## 任务总览

| WP | 主题 | 优先级 | 依赖决策 | 涉及文件 | 验收标准 |
|---|---|---|---|---|---|
| WP-P1 | 正则提升 + F 离群单次遍历 | 高 | 无 | `core.py`、`tests/test_core.py` | build_plan -10~20%，220 项全绿 |
| WP-P2 | 处理管线单次切行重构 | 中 | D-P1 | `core.py`、`tests/test_core.py` | build_plan -30~40%（含 P1 后），220 项全绿 |
| WP-P3 | GUI 应用操作只重处理受影响计划 | 低 | D-P1 | `gui.py`、`tests/test_gui.py` | 「全部应用/应用所选」不再整目录重扫 |
| WP-S1 | FNV 替代 md5，排除 hashlib/libcrypto | 高 | 无 | `gui.py`、`NCodeProcess.spec`、`tests/test_gui.py` | EXE -1.9MB，互斥体名测试不变全绿 |
| WP-S2 | 补充 excludes | 中 | 无 | 两个 `.spec` | EXE -0.3~1MB（实测记录） |
| WP-S3 | 安装并启用 UPX | 中 | D-S1 | 构建环境、`requirements-build.txt`、README | EXE -30~45%，Defender/360/火绒复测通过 |
| WP-S4 | tcl/tk 数据裁剪实验 | 低 | D-S2 | `NCodeProcess.spec`、`tools/prune_tcl_data.py` | EXE -0.2~0.5MB，Windows 7 中文渲染实测通过 |
| WP-S5 | ZIP 更高压缩比 | 低 | D-S3 | `build_portable.ps1` | ZIP 再 -5~15%（可选） |

---

## 决策点（Phase 0，执行前确认）

| 编号 | 决策 | 选项 | 建议 |
|---|---|---|---|
| D-P1 | 性能范围 | A：仅 WP-P1（正则提升 + F 离群单次遍历，低风险）（推荐先做）；B：A + WP-P2 管线重构 + WP-P3 | A 先行，P2/P3 视 P1 收益再定 |
| D-S1 | 是否启用 UPX | A：安装并启用，先做 Defender 实测，360/火绒可测则测，误报则回退 `upx=False`（推荐）；B：不启用，仅做 S1/S2 | A（以杀软复测为门） |
| D-S2 | tcl/tk 数据裁剪 | A：作为实验 WP，保留白名单编码/语言，Win7 实测门；B：不做（推荐，收益小风险中） | B 优先，如需再开 A |
| D-S3 | ZIP 压缩工具 | A：保持 `Compress-Archive`；B：有 7-Zip 时用 `-mx=9` 重建（推荐 A，收益一般） | A |

---

## 文件结构

- `ncodeprocess/core.py`：模块常量正则（`S_RE`/`MOTION_ANY_RE`/`TOOL_REF_RE`/`STANDALONE_CHANGE_RE`）、`add_m03`/`_insert_standalone_m03`/`add_initial_tool_change` 复用模块常量、`validate_program` F 离群收集并入主遍历（WP-P1）；可选单次切行管线（WP-P2）。
- `ncodeprocess/gui.py`：`single_instance_mutex_name` 改用 FNV-1a，删除 `import hashlib`（WP-S1）；可选应用操作局部重处理（WP-P3）。
- `NCodeProcess.spec` / `NCodeProcessReportViewer.spec`：excludes 追加（WP-S1 的 hashlib、WP-S2 列表、WP-S4 的 datas 过滤）。
- `tests/test_core.py`、`tests/test_gui.py`：新增/调整测试。
- `requirements-build.txt`：记录 UPX（conda）或说明文档。
- `tools/prune_tcl_data.py`（WP-S4，可选）：构建期 tcl 数据白名单过滤。
- `docs/`：审查与待办、发布说明、README 同步。

---

## WP-P1: 正则提升 + F 离群单次遍历

**Files:** `ncodeprocess/core.py`、`tests/test_core.py`

**范围：** 三个纯性能重构，行为零变化，靠现有 220 项测试兜底。

- [ ] **Step 1: 常量区新增模块级正则（core.py 顶部，紧邻 `M03_RE`）**

```python
# 供 add_m03 / add_initial_tool_change 复用，避免每次调用重复编译。
S_RE = re.compile(r"(?<![A-Z])S\s*" + NUM, re.I)
MOTION_ANY_RE = re.compile(r"(?<![A-Z])(?:G0*[0-3]|[XYZ]\s*" + NUM + r")", re.I)
TOOL_REF_RE = re.compile(r"(?<![A-Z])T\d+(?!\d)", re.I)
STANDALONE_CHANGE_RE = re.compile(r"^\s*(?:N\d+\s*)?T\d+\s*M0?6\s*;?\s*$", re.I)
```

- [ ] **Step 2: `add_m03` 与 `_insert_standalone_m03` 改用模块常量**

`add_m03` 内删除 `m03_re = re.compile(...)` 与 `s_re = re.compile(...)` 两行，改用模块 `M03_RE` / `M04_RE`（WP-A1 新增）/ `S_RE`；`_insert_standalone_m03` 内删除 `motion_re = re.compile(...)`，改用 `MOTION_ANY_RE`。`add_initial_tool_change` 内删除 `tool_ref` / `standalone_change` 两行编译，改用 `TOOL_REF_RE` / `STANDALONE_CHANGE_RE`。

- [ ] **Step 3: `validate_program` 的 F 离群收集并入主遍历**

3a. 主循环前声明 `current_z: Optional[float] = None` 与 `stage_feeds`（从现有第二遍循环处移来）。

3b. 主循环中把「地址参数遍历」改为单次解析并同时收集 F 阶段：

```python
        parameters = list(ADDR_RE.finditer(code))
        z_value = None
        has_xy = False
        for parameter in parameters:
            key = parameter.group(1).upper()
            if key == "Z":
                try:
                    z_value = float(parameter.group(2))
                except ValueError:
                    pass
            elif key in "XY":
                has_xy = True
        if z_value is not None:
            current_z = z_value
        if g00_matches or (current_z is not None and current_z >= RETRACT_Z_THRESHOLD):
            stage = "move"
        elif z_value is not None and not has_xy:
            stage = "plunge"
        else:
            stage = "cut"
        for parameter in parameters:
            key = parameter.group(1).upper()
            raw_value = parameter.group(2)
            value = float(raw_value)
            if stats is not None and key in stats.counts:
                stats.counts[key] += 1
                if key in "FS" and raw_value not in stats.distinct[key]:
                    stats.distinct[key].append(raw_value)
                if stats.minimum[key] is None or value < stats.minimum[key]:
                    stats.minimum[key] = value
                if stats.maximum[key] is None or value > stats.maximum[key]:
                    stats.maximum[key] = value
            if key not in "FS":
                continue
            if key == "F":
                if value == 0:
                    issues.append(Issue(filename, i, raw_line, "feed-zero", "error", "发现 F0：进给为零，属于严重异常，请立即修正"))
                if config.feed_min is not None and value < config.feed_min:
                    issues.append(Issue(filename, i, raw_line, "feed-range", "error", f"F 值 {raw_value} 低于下限 {config.feed_min:g}"))
                if config.feed_max is not None and value > config.feed_max:
                    issues.append(Issue(filename, i, raw_line, "feed-range", "error", f"F 值 {raw_value} 超过上限 {config.feed_max:g}"))
                if value > 0:
                    stage_feeds[stage].append((i, raw_value, value, raw_line))
            else:
                spindle_values.append((i, raw_value, value, raw_line))
                has_s = True
                if config.spindle_min is not None and value < config.spindle_min:
                    issues.append(Issue(filename, i, raw_line, "spindle-range", "error", f"S 值 {raw_value} 低于下限 {config.spindle_min:g}"))
                if config.spindle_max is not None and value > config.spindle_max:
                    issues.append(Issue(filename, i, raw_line, "spindle-range", "error", f"S 值 {raw_value} 超过上限 {config.spindle_max:g}"))
            if value < 0:
                issues.append(Issue(filename, i, raw_line, "negative-parameter", "error", f"{key} 不得为负数"))
```

> 说明：原第二遍循环（stage 收集）删除，仅保留其后的离群检测块（`stage_labels` 循环）。`g00_matches` 在主循环前部已计算，直接复用，不再二次 `G00_RE.search`。若 WP-C9（抬刀阈值进 Config）先行，`RETRACT_Z_THRESHOLD` 替换为 `config.retract_z_threshold`。

- [ ] **Step 4: 回归与基准**

Run: `conda run -n python38 python -m unittest tests.test_core -v` 全绿；再用计时脚本（或 `Measure-Command`）复测 `build_plan`，记录 HASS 25 文件耗时。
Expected: 220 项全绿；build_plan 较 2.10s 下降（-10~20%）。

- [ ] **Step 5: 提交门**：`perf(core): 正则模块常量复用，F 离群收集并入主遍历`（用户确认后）。

---

## WP-P2: 处理管线单次切行重构（依赖 D-P1=B）

**Files:** `ncodeprocess/core.py`、`tests/test_core.py`

**范围：** `apply_header` / `add_initial_tool_change` / `add_m03` / `analyze_program` 目前各自 `replace + split + _header_end`；重构为共享一次切行。

- [ ] **Step 1: 新增内部函数 `_split_lines(text) -> (lines, newline, had_trailing)`**（把四处重复的规范化代码收敛到一个私有函数）。
- [ ] **Step 2: 新增 `_join_lines(lines, newline, had_trailing)`** 反向收敛。
- [ ] **Step 3: 改造管线**：新增 `_process_mpf_text(text, program, info, config, tools)` 一次切行后依次执行头部/换刀/M03/分析，各步改为接受 `lines` 并返回（`lines`, `changed`, `note`）而不是重切全文；`build_plan.process_mpf` 与 `reprocess_file` 改调该函数。
- [ ] **Step 4: 回归**：220 项全绿；对 HASS/V5 样例做「重构前后输出逐字节 diff 为空」验证。
- [ ] **Step 5: 基准复测**：build_plan 预期较 WP-P1 后再 -15~20%（累计 -30~40%）。
- [ ] **Step 6: 提交门**：`perf(core): 处理管线单次切行，消除重复文本归一化`。

> 若 D-P1=A，本 WP 跳过并登记为待办。

---

## WP-P3: GUI 应用操作只重处理受影响计划（依赖 D-P1=B）

**Files:** `ncodeprocess/gui.py`、`tests/test_gui.py`

- [ ] `apply_info`/`apply_selected` 不再整目录 `scan()`：改为对受影响 `FilePlan` 调 `reprocess_file` + `populate_file_tables`（`apply_selected` 已如此，`apply_info` 需同样处理并在末尾保留一次轻量 `scan()` 以刷新候选图号等全局数据）。
- [ ] 测试：`test_apply_info_does_not_rescan_whole_directory`（patch `scan` 断言最多调用一次且不因应用信息触发）。
- [ ] 提交门：`perf(gui): 应用程序信息不再整目录重扫`。

---

## WP-S1: FNV 替代 md5，排除 hashlib/libcrypto

**Files:** `ncodeprocess/gui.py`、`NCodeProcess.spec`、`tests/test_gui.py`

**背景：** `_hashlib.pyd` 拉入 `libcrypto-3-x64.dll`（归档内 1.89MB）。互斥体名只需确定性哈希，不要求密码学安全。

- [ ] **Step 1: 改写 `single_instance_mutex_name` 并删除 hashlib 导入**

```python
def single_instance_mutex_name(anchor_path: str) -> str:
    """基于 EXE/脚本绝对路径生成稳定的命名互斥体名（FNV-1a 64 位，非密码学用途）。"""
    normalized = os.path.normcase(os.path.abspath(anchor_path)).encode("utf-8")
    value = 0xCBF29CE484222325
    for byte in normalized:
        value ^= byte
        value = (value * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return "NCodeProcess_" + format(value, "016x")
```

删除 `gui.py` 顶部 `import hashlib`。

- [ ] **Step 2: 现有测试锁定 + 补充格式断言**

`test_single_instance_mutex_name_is_stable_and_path_specific` 已断言稳定/路径特异/前缀；补充：`assertRegex(first, r"^NCodeProcess_[0-9a-f]{16}$")`。

- [ ] **Step 3: 主 spec excludes 追加 `hashlib`、`_hashlib`**

`NCodeProcess.spec` 的 `excludes=[...]` 追加两项（查看器 spec 已包含，无需改）。

- [ ] **Step 4: 全量测试 + 重建 + 体积对比**

Run: `conda run -n python38 python -m unittest tests.test_gui -v`；`powershell -ExecutionPolicy Bypass -File .\build_portable.ps1`。
Expected: 全绿；新 `dist\NCodeProcess.exe` 较 8.95MB 减少约 1.9MB（记录实测值）；`pyi-archive_viewer` 中不再出现 `libcrypto-3-x64.dll`。

- [ ] **Step 5: 提交门**：`build: 互斥体名改用 FNV-1a，排除 hashlib/libcrypto（EXE -1.9MB）`。

---

## WP-S2: 补充 excludes

**Files:** `NCodeProcess.spec`、`NCodeProcessReportViewer.spec`

- [ ] **Step 1: 两个 spec 的 `excludes` 统一追加**

```python
        "email", "http", "urllib", "pydoc_data", "ensurepip", "distutils",
        "setuptools", "pip", "venv", "tkinter.test", "tkinter.tix",
        "cgi", "telnetlib", "ftplib", "smtplib", "imaplib", "nntplib",
        "socketserver", "http.server", "wsgiref", "json.tool", "xmlrpc", "asyncio",
```

> 主 spec 的 `hashlib`/`_hashlib` 追加仅在 WP-S1 完成后进行（依赖）。

- [ ] **Step 2: 重建并验证启动**

两个项目分别 `build_portable.ps1`；启动 EXE 冒烟（扫描 + 设置对话框 + 导出报告）。用 `pyi-archive_viewer` 确认 `email`/`http` 等未再被打包；记录新体积（预期 -0.3~1MB）。

- [ ] **Step 3: 提交门**：`build: 补充 excludes 裁剪无用标准库`。

---

## WP-S3: 安装并启用 UPX（依赖 D-S1）

**Files:** 构建环境、`requirements-build.txt`、`README.md`、`NCodeProcess-审查与待办.md`

- [ ] **Step 1: 安装 UPX（仅构建期）**

Run: `conda install -n python38 -c conda-forge upx`
若 conda-forge 的 UPX 4.x 与 PyInstaller 5.13 兼容性异常（构建日志出现 UPX 相关错误或体积无变化），改用 UPX 3.96（下载 win64 压缩包解压到 PATH）。
验证：`conda run -n python38 python -c "import shutil; print(shutil.which('upx'))"` 输出非空。

- [ ] **Step 2: 重建并确认 UPX 真正生效**

Run: `powershell -ExecutionPolicy Bypass -File .\build_portable.ps1`
检查构建输出：PyInstaller 打印 `UPX is available`（或 warn 文件无 UPX 跳过提示）；`pyi-archive_viewer` 显示 DLL 条目 `is_compressed` 且压缩后体积显著下降。
Expected: EXE 8.95 → 约 4.5~5.5MB（含 S1/S2 后基数），记录实测。

- [ ] **Step 3: 杀软复测门（强制）**

用 `MpCmdRun` 全盘扫描新 EXE（复刻 WP-12 流程）；360/火绒本机有则测，无则留待车间实测并登记。任一杀软误报 → 回退 `upx=False`（保留 S1/S2 成果）并更新审查与待办。

- [ ] **Step 4: Windows 7 冒烟**

在 Win7 环境启动 EXE、完成一次扫描+预览+导出，确认 UPX 压缩后运行正常。

- [ ] **Step 5: 文档与提交门**

`requirements-build.txt` 注明 UPX 为可选构建依赖（conda-forge `upx`）；README 记录启用/回退状态；提交：`build: 启用 UPX 压缩（EXE -xx%）` 或 `docs: UPX 误报回退记录`。

---

## WP-S4: tcl/tk 数据裁剪实验（依赖 D-S2）

**Files:** `NCodeProcess.spec`、`NCodeProcessReportViewer.spec`、`tools/prune_tcl_data.py`（可选）

- [ ] **Step 1: spec 内 datas 过滤（白名单）**

```python
def _keep_tcl_data(entry):
    name = entry[0].replace("\\", "/")
    if name.startswith("tcl/msgs/"):
        return name.rsplit("/", 1)[-1] in {"en.msg", "zh_cn.msg", "zh_tw.msg"}
    if name.startswith("tcl/encoding/"):
        return name.rsplit("/", 1)[-1] in {"ascii.enc", "cp1252.enc", "cp936.enc", "iso8859-1.enc", "utf-8.enc"}
    return True

a.datas = [entry for entry in a.datas if _keep_tcl_data(entry)]
```

- [ ] **Step 2: 重建并记录体积差**（预期 -0.2~0.5MB）。
- [ ] **Step 3: Windows 7 中文渲染实测门**：启动 GUI、扫描含中文路径/图号样例、打开设置与差异页，确认无乱码/编码错误；失败则回退该过滤。
- [ ] **Step 4: 提交门**：`build: 裁剪 tcl 本地化与编码表（EXE -xx%）` 或回退记录。

---

## WP-S5: ZIP 更高压缩比（依赖 D-S3）

- [ ] 若 D-S3=B 且有 7-Zip：`build_portable.ps1` 增加可选 `-Use7Zip` 参数，用 `7z a -tzip -mx=9` 生成 ZIP，对比 `Compress-Archive` 结果（预期 -5~15%）。
- [ ] 提交门：`build: ZIP 可选 7-Zip 最大压缩`。

---

## 执行顺序与确认流程

1. Phase 0 确认 D-P1、D-S1~D-S3（建议：D-P1=A、D-S1=A、D-S2=B、D-S3=A）。
2. 建议顺序：WP-P1 → WP-S1 → WP-S2 → WP-S3（杀软门）→ 视收益再评估 WP-P2/P3、S4、S5。
3. 每个 WP 启动前单独确认；完成后全量测试 → 打包 → 用户实测（体积类含杀软/Win7 冒烟）→ 确认后提交。
4. 与既有计划的关系：WP-P1 的 `RETRACT_Z_THRESHOLD` 引用若与 WP-C9（抬刀阈值进 Config）冲突，以 config 值为准；本计划不重复实现 WP-C9。

---

## Self-Review 记录

- **Spec 覆盖**：性能审查的三项（正则提升、F 离群单次遍历、管线单次切行）与体积审查的四层（libcrypto、excludes、UPX、tcl/tk）及 ZIP 均有对应 WP；基准数据、预期区间、回退路径齐备。
- **占位符检查**：每个 WP 含精确文件、命令、预期输出；WP-P1/S1 给出完整代码；无 TBD。
- **签名/一致性**：`single_instance_mutex_name` 返回值仍为 `NCodeProcess_` 前缀稳定字符串，既有测试可直接锁定；`validate_program` 签名不变；模块常量命名与既有 `M03_RE` 风格一致；WP-S1 与 WP-S2 的 hashlib 排除有依赖标注。
