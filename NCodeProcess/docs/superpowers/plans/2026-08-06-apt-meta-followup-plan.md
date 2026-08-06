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

## 〇、APT 数据利用全景（设计思考）

APT 是 CATIA 的规划数据源：它同时包含「后处理前」的加工意图（操作、刀具、转速、进给、冷却、坐标系、公差）与「完整轨迹」（GOTO 点位、圆弧/直线几何），而 MPF 是「后处理后」的执行指令。两者互补，利用方式按价值分层如下。

### 数据 → 信息 → 用途映射

| APT 记录 | 可提取信息 | 利用场景 | 消费方 | 落地状态 |
|---|---|---|---|---|
| `SPINDL/` | 转速、单位、方向（CLW/CCLW） | 主轴方向与转速核对（安全） | 校验 + 报告 | 本轮 WP-A1/A4 |
| `FEDRAT/` | 进给档位集合（MMPM 等） | F 离群权威档位源；MPF F 容差核对 | 校验 + 报告 | 本轮 WP-A1/A4 |
| `COOLNT/` | 冷却液开/关 | M08/M09 一致性核对 | 校验 | 本轮 WP-A1/A4 |
| `LOADTL/` | 装夹刀具号序列 | 换刀序列核对；备刀清单 | 校验 + 报告 | 本轮 WP-A1/A4/A3 |
| `CUTTER/TOOLNO/` | 刀具几何与类型 | 刀具识别（已有） | 头部/刀具表 | ✅ 已实现 |
| `$$ OPERATION NAME` | 操作名与顺序 | 工艺阶段标签；每操作统计 | 报告 + 查看器 | 本轮 WP-A1/A3（操作清单） |
| `$$ MACHIN / PP-TABLE / CATIA VERSION` | 机床型号、后处理表、生成器版本 | 程序族谱/审计 | 报告 | 本轮 WP-A1 |
| `$$ 位姿矩阵 / OPERATE` | 工件坐标系位姿、Part Operation | 坐标系/装夹审计；斜轴加工提示 | 报告 | 本轮 WP-A1 |
| `GOTO/` | 点数、XYZ 行程、抬刀次数 | 行程范围、完整性指纹、排产参考 | 报告 + 查看器 | 本轮 WP-A2/A3 |
| `TLON,GOFWD (CIRCLE/LINE)` | 圆弧/直线数 | 粗精加工判断辅助、轨迹复杂度 | 报告 | 本轮 WP-A2 |
| `$$ Generated on` | 后处理生成时间 | 与 MPF DATE 比对 → 改版识别 | 报告 | 本轮 A1 提取，比对为后续 |
| `INTOL/OUTTOL` | 公差 | 加工精度档案 | 报告 | 后续（见 L4） |

### 利用分层与设计要点

**L1 质量与安全（本轮 WP-A4）**
- 主轴方向精确核对：APT CLW ↔ MPF M03、CCLW ↔ M04，不一致报 error——把现行「见 M04 就禁补 M03」升级为可解释的工艺判断。
- S/F 容差核对（S ±1%、F ±10%）：APT 是规划值，后处理可能取整或倍率，一律 warning。
- 冷却液、装夹序列核对：均 warning，进 `apt-*` kind 与问题表。

**L2 统计与报告（本轮 WP-A1~A3/A5）**
- 元数据 + 轨迹行程写入报告，供车间在查看器直接核对「程序覆盖范围 / 用了哪些刀 / 什么转速进给」。
- 目录级汇总（WP-A3 扩展）：`apt_summary` 增加「操作清单」与「刀具使用」——直接回答「这批零件需要准备哪些刀、各刀用在几个程序」，形成备刀清单依据。

**L3 效率与排产（后续可选，设计已就绪）**
- 每操作统计：按 `$$ OPERATION NAME` 分组统计该操作的刀具/转速/进给/行程——用于工艺卡核对与工时分析（解析时记录「当前操作上下文」，把 FEDRAT/SPINDL 归入所在操作段）。
- 路径长度与 GOTO 密度：粗估加工量，供排产参考（不做时间估算，避免误导，D-A4=B）。
- 程序完整性指纹：同图号新旧程序的 GOTO 点数、操作数、刀具集合、行程范围对比——快速发现「后处理重出时轨迹缺失/刀具遗漏」；指纹字段已在 WP-A2/A3 产出，对比功能留待查看器增强批次。

**L4 审计与版本管理（后续可选）**
- 生成时间 vs MPF DATE：`$$ Generated on` 与头部 DATE 相差过大时提示「文件可能被二次修改/重命名」，辅助版本追溯。
- 程序族谱：CATIA 版本 + PP-TABLE + 机床型号进入报告头信息，形成「哪个后处理、哪个机床、哪个版本生成」的可追溯档案。
- 公差档案：`INTOL/OUTTOL` 进 `apt_meta`（解析成本为零），报告可展示粗/精加工公差。

**L5 预留能力（数据就绪，开关启用）**
- 行程限位检查：`toolpath_stats` 的 XYZ 行程已就绪，未来只需配置机床限位表即可启用（对应暂缓的「机床行程检查」，用户可随时决定开启）。
- 安全平面建议：抬刀次数与最大 Z 已统计，可给出安全高度建议值。
- 斜轴/坐标系提示：位姿矩阵含非零旋转时提示「存在旋转坐标系，注意五轴/斜向加工风险」。

**口径与风险（贯穿所有利用点）**
- APT 是规划值、MPF 是执行值，交叉校验一律容差且默认 warning；
- 只解析每个程序的最新 APT（`latest_apt` 按 mtime），避免历史归档污染；
- 报告只存聚合值（极值/计数/去重集合），不存轨迹点，控制体积。

---

## 现有模块 × APT 补强映射（设计思考）

上面「全景」面向新能力；本节回答「当前程序已设计好的模块，各能用 APT 数据补强什么」。凡标「落点」的均已进入本计划 WP。

| 现有模块 | 现有逻辑 | APT 补强 | 落点 |
|---|---|---|---|
| M03 补写 / 主轴方向（`add_m03`、`spindle-direction`） | M04 存在即禁补 M03（保守） | `SPINDL` CLW/CCLW 权威方向：CLW 应配 M03、CCLW 应配 M04；方向不一致 error；正文同含 M03+M04 时给出「保留哪个、删除哪个」建议 | WP-A4 扩展 |
| F 离群检测（`feed-outlier` 阶段分组） | Z 阈值启发式分移动/进刀/切削组 | `FEDRAT` 集合 = 权威常见档位；按 `$$ OPERATION NAME` 操作上下文分组优先、不可用回退启发式（D-A7） | WP-A4 + WP-A9 |
| S/F 上下限（`feed-range`/`spindle-range`） | 手工配置 min/max | 由 APT 集合生成建议上下限（×0.8/×1.2）进 `apt_summary` 与查看器展示，不自动改配置 | WP-A9 |
| 辅助指令顺序 + 互斥 M（`aux-order`、`mutually-exclusive-m`） | 纯顺序规则、无意图 | `COOLNT` → M08/M09 期望（A4 已含）；`SPINDL` 方向让互斥检查给出明确取舍建议 | WP-A4 扩展 |
| 刀具识别 / 刀号一致性（`extract_tools`、`tool-number-missing`） | CUTTER/TOOLNO 几何识别；正文 T 与头部 Tn 一致性 | `LOADTL` 权威装夹列表（A4 已含）；MPF Tn 几何参数与 APT TOOLNO/CUTTER 不一致（用户改过）时 warning | WP-A4 扩展 |
| 程序名提取（`extract_program_name`） | PPRINT / MSG / 文件名 | `$$` 头部程序名行作第三候选；PPRINT/`$$`/文件名多源不一致时提示核对 | WP-A1 扩展 |
| 头部补全 / DATE 维护（`apply_header`、`update_header_date`） | 变更时写处理时刻 | MPF DATE 早于 APT 生成时间 → 「文件可能被二次修改」warning；不自动填充（D-A9） | WP-A10 |
| 重复目标裁决（`duplicate-target` 按 mtime） | 同目标按修改时间最新覆盖 | APT 生成时间与 mtime 排序不一致时在现有 warning 中追加提示；不改变裁决（D-A8） | WP-A10 |
| 程序对比（`compare_selected_programs`） | 两个 MPF 文本 diff | 增加「APT 规划差异」面板（刀具/操作/主轴/进给），回答「工艺差在哪」 | WP-A11 |
| 统计 / 全部程序信息（`calculate_stats`、`show_all_program_stats`） | F/S/X/Y/Z 统计表 | 总览窗口增加操作数、Z 行程最大等规划列 | WP-A11 |
| 报告与查看器 | 第 12 节字段、运行日志、问题表 | `apt_meta`/`toolpath_stats`/`apt_summary` + 「APT 信息」页签 | WP-A1~A5 |

口径沿用全景章节：APT 是规划值，一律容差且默认 warning；只解析最新 APT；报告只存聚合值。

---

## 任务总览

| WP | 主题 | 优先级 | 依赖决策 | 涉及文件 | 验收标准 |
|---|---|---|---|---|---|
| WP-A1 | `extract_apt_meta`：APT 头部与加工参数解析 | 高 | 无 | `core.py`、`tests/test_core.py` | 解析机床/后处理表/操作列表/变换矩阵/SPINDL/FEDRAT/COOLNT/LOADTL/`$$` 程序名；操作级进给/主轴分组；缓存生效 —— ✅ 已处理（2026-08-06） |
| WP-A2 | `extract_apt_toolpath`：轨迹统计 | 中 | D-A2 | `core.py`、`tests/test_core.py`、`gui.py` | GOTO 点数/XYZ 行程/圆弧数/抬刀次数（自适应平面）；抬刀高度自定义 + 参数统计页展示 + 全部程序窗口轨迹列 —— ✅ 已处理（2026-08-06） |
| WP-A3 | `build_plan` 挂载 + 报告字段 | 高 | D-A3 | `core.py`、`tests/test_core.py` | 最新 APT 的 meta/stats 进 `FilePlan` 与报告 `files[]`；`apt_summary` 全局摘要 —— ✅ 已处理（2026-08-06，含操作清单/刀具使用） |
| WP-A4 | APT↔MPF 交叉校验 | 高 | D-A1 | `core.py`、`tests/test_core.py` | 方向不一致 error；加工参数不符（S/F 容差、刀具几何）与程序名冲突 warning；COOLNT、LOADTL、DATE 过期提示；`apt-*` kind 入报告 —— ✅ 已处理（2026-08-06，按用户确认级别） |
| WP-A5 | 查看器「APT 信息」页签 | 中 | D-A3 | `viewer.py`、`tests/test_report_viewer.py` | 新页签展示 meta/toolpath；缺失回退；查看器 18 → 20+ 项 —— ✅ 已处理（2026-08-06，查看器 21 项全绿） |
| WP-A9 | 校验意图化：操作级 F 档位 / 互斥 M 方向建议 / S·F 建议上下限 | 高 | D-A7 | `core.py`、`tests/test_core.py` | 操作级进给/主轴进报告；M03+M04 冲突给出 APT 方向建议；`apt_summary` 输出建议限值 |
| WP-A10 | APT 生成时间参与头部 DATE 与重复裁决 | 中 | D-A8/D-A9 | `core.py`、`tests/test_core.py` | DATE 过期 warning；重复目标时提示 APT 生成时间与 mtime 排序不一致；不改变裁决 |
| WP-A11 | 对比与总览增强：APT 规划差异面板 + 总览规划列 | 低 | D-A3 | `gui.py`、`viewer.py`、`tests/` | 程序对比窗口显示 APT 规划差异；全部程序信息增加操作数/Z 行程列 |
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
| D-A7 | F 离群是否按 APT 操作分组 | A：APT 操作上下文可用时优先、不可用回退现有 Z 启发式（推荐）；B：仅保留启发式 | A |
| D-A8 | 重复目标裁决是否纳入 APT 生成时间 | A：仅提示排序不一致、不改变裁决（推荐，避免行为变化）；B：直接纳入裁决 | A |
| D-A9 | 新头部 DATE 是否自动用 APT 生成时间填充 | A：不填充，仅报告与改版识别提示（推荐，避免中文日期格式转换风险）；B：填充 | A |

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

- [x] **Step 1: 新增 dataclass 与常量正则（core.py，`extract_tools` 附近）**

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
    program_name: str = ""                                              # $$ 头部程序名行（字母开头候选）
    operation_feeds: Dict[str, List[Tuple[str, str]]] = field(default_factory=dict)      # 操作名 → 进给档位
    operation_spindles: Dict[str, List[Tuple[str, str, str]]] = field(default_factory=dict)  # 操作名 → 主轴

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
# $$ 头部程序名行（字母开头，排除矩阵/纯数字行），如 "$$ AG6D311A0101"。
APT_PROGNAME_DOLLAR_RE = re.compile(r"^\$\$\s+([A-Za-z][A-Za-z0-9_-]*)\s*$")
```

- [x] **Step 2: 新增 `extract_apt_meta`（流式单遍）**

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
    current_operation = ""
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
                current_operation = name
                continue
            m = APT_PROGNAME_DOLLAR_RE.match(line)
            if m and not meta.program_name:
                meta.program_name = m.group(1)
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
                if current_operation:
                    op_list = meta.operation_spindles.setdefault(current_operation, [])
                    if key not in op_list:
                        op_list.append(key)
                continue
            m = APT_FEDRAT_RE.search(line)
            if m:
                key = (m.group(1), m.group(2).upper())
                if key not in seen_feeds:
                    seen_feeds.add(key)
                    meta.feeds.append(key)
                if current_operation:
                    op_list = meta.operation_feeds.setdefault(current_operation, [])
                    if key not in op_list:
                        op_list.append(key)
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

- [x] **Step 3: 新增缓存（与 `_APT_TOOL_CACHE` 同模式）**

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

- [x] **Step 4: 新增失败测试（用样例真实格式）**

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

    def test_apt_meta_operation_grouping_and_program_name(self):
        apt = self.make_dir() / "g.aptsource"
        apt.write_text(
            "$$ AG6D311A0101\n"
            "$$ OPERATION NAME : Roughing.3\n"
            "SPINDL/ 5000.0000,RPM,CLW\n"
            "FEDRAT/ 3000.0000,MMPM\n"
            "FEDRAT/ 6000.0000,MMPM\n"
            "$$ OPERATION NAME : Finishing.1\n"
            "SPINDL/ 8000.0000,RPM,CLW\n",
            encoding="utf-8",
        )
        meta = extract_apt_meta(apt)
        self.assertEqual(meta.program_name, "AG6D311A0101")
        self.assertEqual(meta.operation_feeds["Roughing.3"], [("3000.0000", "MMPM"), ("6000.0000", "MMPM")])
        self.assertEqual(meta.operation_spindles["Finishing.1"], [("8000.0000", "RPM", "CLW")])

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

- [x] **Step 5: 回归**：`D:\anaconda3\envs\python38\python.exe -m unittest tests.test_core -v` 全绿。
- [x] **Step 6: 提交门**：`feat(core): APT 元数据流式解析（机床/操作/主轴/进给/冷却/装夹/矩阵）`。

---

## WP-A2: `extract_apt_toolpath`：轨迹统计（依赖 D-A2=B）

**Files:** `ncodeprocess/core.py`、`tests/test_core.py`

- [x] **Step 1: 新增 dataclass 与解析函数**

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

- [x] **Step 2: 新增失败测试（样例真实轨迹）**

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

- [x] **Step 3: 缓存与回归**（同 WP-A1 模式加 `_APT_TOOLPATH_CACHE`）；`tests.test_core -v` 全绿。
- [x] **Step 4: 提交门**：`feat(core): APT 轨迹统计（行程/点数/圆弧/抬刀）`。

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
        "apt_suggested_limits": suggest_apt_limits(f.apt_meta) if f.apt_meta else None,
```

报告级 `apt_summary`（`ProcessReport` 新增字段，`process_plan` 结束时聚合；含操作清单与刀具使用，支撑备刀清单）：

```python
    report.apt_summary = {
        "machines": sorted({meta.machine for f in scan.files if f.apt_meta and f.apt_meta.machine}),
        "spindle_speeds": sorted({float(speed) for f in scan.files if f.apt_meta for speed, _u, _d in f.apt_meta.spindles}),
        "tool_loads": sorted({number for f in scan.files if f.apt_meta for number in f.apt_meta.tool_loads}),
        "operations": sorted({name for f in scan.files if f.apt_meta for name in f.apt_meta.operations}),
        "tool_usage": {
            number: sum(1 for f in scan.files if f.apt_meta and number in f.apt_meta.tool_loads)
            for number in sorted({n for f in scan.files if f.apt_meta for n in f.apt_meta.tool_loads})
        },
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
        self.assertIn("Roughing.3", report.apt_summary["operations"])
        self.assertEqual(report.apt_summary["tool_usage"][1], 1)
```

- [ ] **Step 5: 回归 + 提交门**：`feat(core): APT 元数据/轨迹统计挂载计划与报告`。

---

## WP-A4: APT↔MPF 交叉校验（依赖 D-A1=A）

**Files:** `ncodeprocess/core.py`、`tests/test_core.py`

- [ ] **Step 1: 新增纯函数 `crosscheck_apt`**

```python
_MONTHS_EN = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def parse_nc_date(text: str) -> Optional[datetime]:
    """解析 NC 头部 DATE（英文月份格式）为 datetime；失败返回 None。"""
    match = re.match(r"^([A-Za-z]{3})\s+(\d{1,2})\s+(\d{1,2}):(\d{2}):(\d{2})\s+(\d{4})$", text.strip())
    if not match:
        return None
    month = match.group(1).capitalize()
    if month not in _MONTHS_EN:
        return None
    try:
        return datetime(int(match.group(6)), _MONTHS_EN.index(month) + 1, int(match.group(2)),
                        int(match.group(3)), int(match.group(4)), int(match.group(5)))
    except ValueError:
        return None


def parse_apt_generated(text: str) -> Optional[datetime]:
    """解析 APT $$ Generated on 中文日期（2026年7月31日 9:30:05）为 datetime；失败返回 None。"""
    match = re.match(r"^(\d{4})年(\d{1,2})月(\d{1,2})日\s*(\d{1,2}):(\d{2}):(\d{2})", text.strip())
    if not match:
        return None
    try:
        return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)),
                        int(match.group(4)), int(match.group(5)), int(match.group(6)))
    except ValueError:
        return None


def crosscheck_apt(mpf_text: str, meta: AptMeta, filename: str, config: Config,
                   apt_tools: Sequence[ToolInfo] = ()) -> List[Issue]:
    """APT 规划信息与 MPF 执行指令交叉校验。

    主轴方向不一致为 error（CLW→M03、CCLW→M04）；S/F 数值容差、冷却液、
    刀具装夹/几何参数、程序名冲突、DATE 过期为 warning。APT 为规划值，
    后处理可能取整/倍率，故一律容差。
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
        if has_m03 and has_m04 and directions:
            direction = next(iter(directions))
            keep, drop = ("M03", "M04") if direction == "CLW" else ("M04", "M03")
            issues.append(Issue(filename, start + 1, "", "apt-spindle-direction", "error",
                                "正文同时含 M03 与 M04；APT 规划方向为 %s，建议保留 %s 并删除 %s" % (direction, keep, drop)))
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

    if apt_tools:
        header_text = "\n".join(lines[:start])
        mpf_tools = {tool.number: tool for tool in extract_tools(header_text)}
        for apt_tool in apt_tools:
            mpf_tool = mpf_tools.get(apt_tool.number)
            if mpf_tool is None:
                continue
            for attr, ratio in (("dia", 0.02), ("tool_coner", 0.05)):
                apt_value = getattr(apt_tool, attr).strip()
                mpf_value = getattr(mpf_tool, attr).strip()
                if not apt_value or not mpf_value:
                    continue
                try:
                    if abs(float(mpf_value) - float(apt_value)) > max(float(apt_value) * ratio, 0.01):
                        issues.append(Issue(filename, start + 1, "", "apt-tool-param-mismatch", "warning",
                                            "T%d 的 %s MPF=%.3f 与 APT 规划 %.3f 不一致，请核对" % (
                                                apt_tool.number, attr, float(mpf_value), float(apt_value))))
                        break
                except ValueError:
                    continue

    if meta.program_name:
        header_program = extract_header_fields(mpf_text).get("PROGRAM", "").strip()
        if header_program and header_program != meta.program_name:
            issues.append(Issue(filename, start + 1, "", "apt-program-name-conflict", "warning",
                                "MPF 的 PROGRAM 字段为 %s，与 APT 程序名 %s 不一致，请核对" % (header_program, meta.program_name)))

    if meta.generated_at:
        apt_time = parse_apt_generated(meta.generated_at)
        header_date = extract_header_fields(mpf_text).get("DATE", "").strip()
        mpf_date = parse_nc_date(header_date) if header_date else None
        if apt_time and mpf_date and mpf_date < apt_time:
            issues.append(Issue(filename, start + 1, "", "apt-date-stale", "warning",
                                "MPF 头部 DATE 早于 APT 生成时间，文件可能在后处理之后被修改"))
    return issues
```

- [ ] **Step 2: `build_plan`/`reprocess_file` 调用并入 issues**

`process_mpf` 中 `f.issues.extend(validation_issues)` 之后：

```python
                if f.apt_meta is not None:
                    f.issues.extend(crosscheck_apt(
                        new, f.apt_meta, f.source, config,
                        apt_tools=auto_tools[f.program][1] if f.program in auto_tools else (),
                    ))
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

    def test_crosscheck_dual_direction_suggests_keep(self):
        meta = AptMeta(spindles=[("5000.0000", "RPM", "CLW")])
        issues = crosscheck_apt("MSG(\"PROGRAM:P\")\nN1S5000M03M04\nN2M30\n", meta, "P.MPF", self._cfg(auto_m03=False))
        direction = [i for i in issues if i.kind == "apt-spindle-direction"]
        self.assertTrue(direction)
        self.assertIn("保留 M03", direction[0].suggestion)

    def test_crosscheck_tool_param_program_name_and_date(self):
        meta = AptMeta(program_name="P", generated_at="2026年7月31日 9:30:05")
        issues = crosscheck_apt(
            'MSG("PROGRAM:Q")\nMSG("DATE:Jul 30 09:00:00 2026")\nMSG("T1:DIA=10.000,TOOL_CONER=1.000")\nN1T1M06\nN2M30\n',
            meta, "P.MPF", self._cfg(auto_m03=False),
            apt_tools=[ToolInfo(1, "12.000", "1.000")],
        )
        kinds = {i.kind for i in issues}
        self.assertTrue({"apt-program-name-conflict", "apt-date-stale", "apt-tool-param-mismatch"} <= kinds)
```

- [ ] **Step 4: 回归 + 提交门**：`feat(core): APT↔MPF 交叉校验（方向/参数/程序名/日期，S/F/冷却/装夹 warning）`。

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

## WP-A9: 校验意图化：操作级工艺参数与 S/F 建议限值（依赖 D-A7=A）

**Files:** `ncodeprocess/core.py`、`tests/test_core.py`

**范围：** 把 APT 的「操作上下文」与「规划集合」变成报告层工艺信息；互斥 M 的方向建议已在 WP-A4 实现；F 离群逐行映射到操作段风险高（MPF/APT 分段不对齐），不做，回退保持现有启发式 + APT 全集核对（D-A7）。

- [ ] **Step 1: 新增 `suggest_apt_limits` 纯函数**

```python
def suggest_apt_limits(meta: AptMeta) -> Dict[str, Optional[float]]:
    """由 APT 规划集合生成 F/S 上下限建议（×0.8 / ×1.2）；无规划值时 None。"""
    feeds = [float(value) for value, _units in meta.feeds]
    spindles = [float(value) for value, _units, _direction in meta.spindles]
    return {
        "feed_min": min(feeds) * 0.8 if feeds else None,
        "feed_max": max(feeds) * 1.2 if feeds else None,
        "spindle_min": min(spindles) * 0.8 if spindles else None,
        "spindle_max": max(spindles) * 1.2 if spindles else None,
    }
```

- [ ] **Step 2: `apt_summary` 增加全目录建议限值与操作级信息**

在 WP-A3 的 `apt_summary` 聚合代码中追加：

```python
        "suggested_feed_range": _suggested_range(scan.files, "feeds"),
        "suggested_spindle_range": _suggested_range(scan.files, "spindles"),
```

并新增模块级助手：

```python
def _suggested_range(plans, attr):
    """汇总全部 APT 的 F/S 规划集合并生成建议区间（[min, max] 或 None）。"""
    values = set()
    for plan in plans:
        if not plan.apt_meta:
            continue
        if attr == "feeds":
            values.update(float(value) for value, _units in plan.apt_meta.feeds)
        else:
            values.update(float(value) for value, _units, _direction in plan.apt_meta.spindles)
    if not values:
        return None
    return [min(values) * 0.8, max(values) * 1.2]
```

- [ ] **Step 3: 查看器 APT 页签增加操作级工艺行（扩展 WP-A5 的 `apt_meta_rows`）**

```python
    op_feeds = meta.get("operation_feeds") or {}
    for op_name in sorted(op_feeds):
        rows.append(("操作进给 · %s" % op_name, "、".join("%s%s" % pair for pair in op_feeds[op_name])))
    op_spindles = meta.get("operation_spindles") or {}
    for op_name in sorted(op_spindles):
        rows.append(("操作主轴 · %s" % op_name,
                     "、".join("%s%s %s" % triple for triple in op_spindles[op_name])))
    limits = item.get("apt_suggested_limits") or {}
    if limits:
        rows.append(("APT 建议 F 上下限", "%.0f .. %.0f" % (limits["feed_min"], limits["feed_max"])
                     if limits.get("feed_min") is not None else ""))
        rows.append(("APT 建议 S 上下限", "%.0f .. %.0f" % (limits["spindle_min"], limits["spindle_max"])
                     if limits.get("spindle_min") is not None else ""))
```

（`suggested_limits` 由查看器按 `apt_meta` 计算并附加，或在生成端写入 `files[].apt_meta.suggested_limits`——采用后者，见 WP-A3 Step 3 追加字段。）

- [ ] **Step 4: 新增失败测试**

```python
    def test_suggest_apt_limits(self):
        meta = AptMeta(feeds=[("3000.0000", "MMPM"), ("6000.0000", "MMPM")],
                       spindles=[("5000.0000", "RPM", "CLW")])
        limits = suggest_apt_limits(meta)
        self.assertAlmostEqual(limits["feed_min"], 2400.0)
        self.assertAlmostEqual(limits["feed_max"], 7200.0)
        self.assertAlmostEqual(limits["spindle_min"], 4000.0)
        self.assertAlmostEqual(limits["spindle_max"], 6000.0)
        self.assertEqual(suggest_apt_limits(AptMeta()), {"feed_min": None, "feed_max": None,
                                                         "spindle_min": None, "spindle_max": None})
```

- [ ] **Step 5: 回归 + 提交门**：`feat(core): APT 操作级工艺参数与 S/F 建议限值`。

---

## WP-A10: APT 生成时间参与头部 DATE 与重复裁决（依赖 D-A8=A / D-A9=A）

**Files:** `ncodeprocess/core.py`、`tests/test_core.py`

**范围：** DATE 过期检查已由 WP-A4 的 `apt-date-stale` 覆盖；本 WP 完成重复目标裁决的排序提示与报告生成时间区间。不自动填充 DATE（D-A9）。

- [ ] **Step 1: 新增模块级助手与 duplicate 提示**

```python
def _apt_generated_time(plan) -> Optional[datetime]:
    if plan.apt_meta and plan.apt_meta.generated_at:
        return parse_apt_generated(plan.apt_meta.generated_at)
    return None
```

在 `build_plan` 的重复目标裁决循环（`for loser in items:` 内）追加：

```python
            winner_apt = _apt_generated_time(winner)
            loser_apt = _apt_generated_time(loser)
            if winner_apt is not None and loser_apt is not None:
                mtime_order = winner.modified_time >= loser.modified_time
                apt_order = winner_apt >= loser_apt
                if mtime_order != apt_order:
                    hint = "；注意：APT 生成时间与文件修改时间排序不一致，请确认以哪个版本为准"
                    loser.issues[-1].suggestion += hint
                    winner.issues[-1].suggestion += hint
```

- [ ] **Step 2: `apt_summary` 增加生成时间区间**

```python
def _apt_generated_range(plans) -> Optional[List[str]]:
    """返回全部 APT 生成时间区间 [最早, 最晚]（ISO 字符串）；无数据返回 None。"""
    times = [value for plan in plans if _apt_generated_time(plan) for value in [(_apt_generated_time(plan)).isoformat(timespec="seconds")]]
    return [min(times), max(times)] if times else None
```

`apt_summary` 增加 `"generated_range": _apt_generated_range(scan.files)`。

- [ ] **Step 3: 新增失败测试**

```python
    def test_duplicate_apt_time_order_hint(self):
        root = self.make_dir()
        old = root / "old_P.MPF"
        new = root / "new_P.MPF"
        old.write_text("N1X1S100M03\nN2M30\n", encoding="utf-8")
        new.write_text("N1X9S100M03\nN2M30\n", encoding="utf-8")
        os.utime(old, (1000, 1000))
        os.utime(new, (2000, 2000))
        cfg = self._cfg()
        scan = scan_directory(str(root), cfg)
        for plan in scan.files:
            if plan.kind == "mpf":
                plan.apt_meta = AptMeta(generated_at="2026年7月31日 9:30:05")
        build_plan(scan, DEFAULT_INFO, cfg)
        older = next(f for f in scan.files if f.source == old.name)
        warning = next(i for i in older.issues if i.kind == "duplicate-target")
        self.assertIn("APT 生成时间", warning.suggestion)

    def test_apt_summary_generated_range(self):
        root = self.make_dir()
        (root / "P.MPF").write_text('MSG("PROGRAM:P")\nN1S1000M03\nN2M30\n', encoding="utf-8")
        (root / "x_P_I.aptsource").write_text("$$     Generated on 2026年7月31日 9:30:05\n", encoding="utf-8")
        cfg = self._cfg()
        plan = build_plan(scan_directory(str(root), cfg), DEFAULT_INFO, cfg)
        report = process_plan(plan, str(root), cfg)
        self.assertEqual(len(report.apt_summary["generated_range"]), 2)
```

- [ ] **Step 4: 回归 + 提交门**：`feat(core): APT 生成时间参与重复裁决提示与报告区间`。

---

## WP-A11: 对比与总览增强：APT 规划差异面板 + 总览规划列

**Files:** `ncodeprocess/core.py`、`ncodeprocess/gui.py`、`tests/test_core.py`、`tests/test_gui.py`

- [ ] **Step 1: 新增纯函数 `apt_diff_lines`（core.py，供 GUI 对比窗口使用）**

```python
def apt_diff_lines(left_meta: Optional[AptMeta], right_meta: Optional[AptMeta]) -> List[str]:
    """两个程序的 APT 规划差异摘要；None 视为无规划，仅在对应维度不同时输出。"""
    def summarize(meta):
        if meta is None:
            return None
        return {
            "刀具": "、".join("T%d" % number for number in meta.tool_loads) or "-",
            "操作": "、".join(meta.operations) or "-",
            "主轴": "、".join("%s%s %s" % triple for triple in meta.spindles) or "-",
            "进给": "、".join("%s%s" % pair for pair in meta.feeds) or "-",
        }
    left = summarize(left_meta)
    right = summarize(right_meta)
    lines = []
    for key in ("刀具", "操作", "主轴", "进给"):
        left_value = left.get(key, "-") if left else "-"
        right_value = right.get(key, "-") if right else "-"
        if left_value != right_value:
            lines.append("%s：左=%s | 右=%s" % (key, left_value, right_value))
    return lines
```

- [ ] **Step 2: 对比窗口增加「APT 规划差异」面板**

`compare_selected_programs` 在左右窗格下方增加 `ttk.LabelFrame`（文本区域，`apt_diff_lines(left.apt_meta, right.apt_meta)` 结果逐行展示；空结果显示「两程序均无 APT 规划数据」）。

- [ ] **Step 3: 全部程序信息窗口增加规划列**

`show_all_program_stats` 表头在 `g00` 后追加 `("ops", "操作数"), ("zmax", "Z 最大")`（列宽 70/90）；每行取 `f.apt_meta` 的操作数与 `f.apt_toolpath.max_z`（无数据为 `-`）。

- [ ] **Step 4: 新增失败测试**

```python
    def test_apt_diff_lines(self):
        left = AptMeta(tool_loads=[1, 2], operations=["Roughing.3"],
                       spindles=[("5000.0000", "RPM", "CLW")], feeds=[("3000.0000", "MMPM")])
        right = AptMeta(tool_loads=[1, 2], operations=["Roughing.3", "Finishing.1"],
                        spindles=[("8000.0000", "RPM", "CLW")], feeds=[("3000.0000", "MMPM"), ("6000.0000", "MMPM")])
        lines = apt_diff_lines(left, right)
        self.assertTrue(any(line.startswith("操作") for line in lines))
        self.assertTrue(any(line.startswith("主轴") for line in lines))
        self.assertFalse(any(line.startswith("刀具") for line in lines))
        self.assertEqual(apt_diff_lines(left, None), [])
```

`test_gui` 增加对比窗口冒烟：两个带 `apt_meta` 的 `FilePlan` 打开对比后断言「APT 规划差异」面板存在且非空。

- [ ] **Step 5: 回归 + 提交门**：`feat: 程序对比显示 APT 规划差异，总览增加规划列`。

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

- **Spec 覆盖**：APT 分析结论六类信息均有 WP（元数据 A1、轨迹 A2、挂载/报告 A3、交叉校验 A4、查看器 A5）；既有收尾 F1/F2 合并；估算时间按 D-A4 不纳入；机床行程检查登记待办（数据源已就绪）。「APT 数据利用全景」章节按 L1~L5 分层：L1/L2 本轮落地，L3~L5 登记后续可选。「现有模块 × APT 补强映射」章节把 11 个现有模块逐项映射到 A1~A5 扩展与新增 WP-A9/A10/A11（操作级工艺参数与建议限值、生成时间参与重复裁决与 DATE 过期检查、对比/总览增强），并对既有收尾 F1/F2 保持合并。
- **占位符检查**：每个 WP 含完整代码、测试名与命令；无 TBD。
- **签名/一致性**：`AptMeta`/`ToolpathStats`/`crosscheck_apt`/`apt_meta_rows` 命名唯一；`FilePlan` 新字段不影响既有构造（dataclass 默认值）；`extract_apt_meta` 与 `_extract_apt_tools_from_path` 共用流式+缓存模式；`apt-*` kind 全部经 `Issue` 统一进入报告与问题表，无需查看器专项着色。
