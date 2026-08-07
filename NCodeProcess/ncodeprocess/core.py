from __future__ import annotations

import csv
import difflib
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import traceback
from collections import Counter, deque
from dataclasses import asdict, dataclass, field
from datetime import datetime
from math import log
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


FIELD_ORDER = [
    ("BIANZHI", "编制", True),
    ("SHENHE", "审核", True),
    ("PROGRAM", "程序", True),
    ("DRAWING NUMBER", "图号", True),
    ("PART VERSION", "版次", True),
    ("NC MACHINE", "机床", True),
    ("CONTROL SYSTEM", "控制系统", True),
    ("DATE", "日期", True),
]
DEFAULT_DELETE_EXTENSIONS = {".log", ".moaptindexes"}
LEGACY_DATA_DIR_NAMES = {"ncpostprocessdata"}
REPORT_PREFIXES = ("ncodeprocess-report", "ncpostprocess-report")
# 程序名允许字符默认正则（WP-D2 模块常量；用户可在设置中自定义，Config.allowed_name_pattern 优先生效）。
DEFAULT_NAME_PATTERN = r"^[A-Za-z0-9_一-鿿-]+$"
# 《F值异常检测方法》第 3 节档位表：一档 100/300（轴向切入/下刀）、
# 四档 5000/6000（非切削移动/抬刀）。
# Legacy context constants retained only for the unreachable compatibility block below;
# the active episode/peer-group detector never uses fixed F values.
FEED_LOW_GEARS = (100.0, 300.0)
FEED_HIGH_GEARS = (5000.0, 6000.0)
# 仅作为角色内“近似档位”的软先验，不得绕过角色距离/包络/硬边界判定。
# 这样可以容纳低频但真实存在的圆整档位，同时仍能检出合法档位错用。
KNOWN_FEED_GEARS = (100.0, 300.0, 600.0, 900.0, 1000.0, 1500.0, 1800.0,
                    2000.0, 2500.0, 3000.0, 3500.0, 5000.0, 6000.0)
# 相邻 Z 趋势阈值：窗口（当前行及前后各 2 行有效 Z）总变化 ≥ 10 视为大幅上升
# （退刀/移动）；≤ -1 视为下降（下刀/进刀）。
FEED_TREND_RISE = 10.0
FEED_TREND_DROP = 1.0
# 振荡（啄钻/跳刀）识别：±4 行窗口内 Z 方向变化次数 ≥ 该值。
# 按用户 280 条标注对齐全率最优（98.9%），取 3。
FEED_OSC_MIN = 3
MSG_RE = re.compile(r'^\s*MSG\s*\(\s*["\'](.*?)["\']\s*\)\s*;?\s*$', re.I)
PPRINT_RE = re.compile(r"\bPPRINT\s+PROGNAME\s+([A-Za-z0-9_-]+)", re.I)
NUM = r"[+-]?(?:(?:\d+(?:\.\d*)?)|(?:\.\d+))(?:[Ee][+-]?\d+)?"
ADDR_RE = re.compile(r"(?<![A-Za-z])([A-Z])\s*(" + NUM + r")", re.I)
N_RE = re.compile(r"^\s*N(\d+)", re.I)
G00_RE = re.compile(r"(?<![A-Z])G0{1,2}(?=\s|[XYZFIJKS]|;|$)", re.I)
END_LINE_RE = re.compile(r"^%\s*;?$", re.I)
END_CODE_RE = re.compile(r"(?<![A-Z])M(?:30|02)(?!\d)", re.I)
M03_RE = re.compile(r"(?<![A-Z])M0?3(?!\d)", re.I)
M04_RE = re.compile(r"(?<![A-Z])M0?4(?!\d)", re.I)
M06_RE = re.compile(r"(?<![A-Z])M0?6(?!\d)", re.I)
M05_RE = re.compile(r"(?<![A-Z])M0?5(?!\d)", re.I)
M08_RE = re.compile(r"(?<![A-Z])M0?8(?!\d)", re.I)
M09_RE = re.compile(r"(?<![A-Z])M0?9(?!\d)", re.I)
# WP-P1：供 add_m03 / add_initial_tool_change 复用的模块级正则，避免每次调用重复编译。
S_RE = re.compile(r"(?<![A-Z])S\s*" + NUM, re.I)
MOTION_ANY_RE = re.compile(r"(?<![A-Z])(?:G0*[0-3]|[XYZ]\s*" + NUM + r")", re.I)
TOOL_REF_RE = re.compile(r"(?<![A-Z])T\d+(?!\d)", re.I)
STANDALONE_CHANGE_RE = re.compile(r"^\s*(?:N\d+\s*)?T\d+\s*M0?6\s*;?\s*$", re.I)
# 切削/进给运动：G1/G2/G3 或 X/Y/Z 坐标（G0 为快速定位，不计入切削）。
CUT_RE = re.compile(r"(?<![A-Z])(?:G0*[123](?!\d)|[XYZ]\s*" + NUM + r")", re.I)
# Z 值达到该阈值视为抬刀高度（移动/退刀阶段），低于该值的正 Z（如 Z5/Z15）仍属切削面。
# 默认 20 由用户确认（WP-C9），可通过 Config.retract_z_threshold 调整。
RETRACT_Z_THRESHOLD = 20.0
TOOL_CALL_RE = re.compile(r"(?<![A-Z])T(\d+)(?!\d)", re.I)
MOTION_RE = re.compile(r"(?<![A-Z])G0*([0-3])(?!\d)", re.I)
INVALID_ADDR_RE = re.compile(r"(?<![A-Za-z])([GTMFSXYZIJ])\s*(?=$|[;\s])", re.I)
CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_SCAN_TEXT_CACHE: Dict[str, Tuple[int, int, str, Tuple[str, str, str]]] = {}
_APT_TOOL_CACHE: Dict[str, Tuple[int, int, str, List[ToolInfo]]] = {}


@dataclass
class Config:
    recursive: bool = False
    aptsource_dir: str = "aptsource"
    # APT source files are intermediate output by default.  They are only
    # moved to the timestamped archive when the operator explicitly enables
    # this option.
    save_aptsource: bool = False
    data_dir_name: str = "NCodeProcessData"
    delete_extensions: set = field(default_factory=lambda: set(DEFAULT_DELETE_EXTENSIONS))
    overwrite_existing: bool = False
    overwrite_fields: bool = False
    require_end_marker: bool = True
    require_m06: bool = False
    g00_level: str = "error"  # error, warning, allow
    auto_m03: bool = True
    auto_tool_change: bool = False
    parallel_workers: int = 4
    require_spindle_speed: bool = False
    allowed_name_pattern: str = DEFAULT_NAME_PATTERN
    encoding: str = "auto"
    # 主程序文件的扩展名集合（小写，默认仅 .mpf），例如 {".mpf", ".nc", ".txt"}。
    # 输出（重命名后）使用的扩展名单独配置，默认 .MPF，保持历史行为。
    program_extensions: set = field(default_factory=lambda: {".mpf"})
    program_output_extension: str = ".MPF"
    # 必填的 MSG 头部字段键（默认全部 FIELD_ORDER 键）；PROGRAM/NC MACHINE/
    # CONTROL SYSTEM 在 GUI 中固定必填，其余可按车间要求收紧或放宽。
    required_fields: List[str] = field(default_factory=lambda: [key for key, _label, _required in FIELD_ORDER])
    # M03 补写位置策略：after-s（紧贴首个 S 数值后，默认）/ standalone（独立行）。
    m03_position: str = "after-s"
    # F/S 数值上下限（默认 20~10000 / 500~12000）；越界按 error 上报。
    # 文档《F值异常检测方法》：F 硬边界默认 20~10000、S 默认 500~12000。
    feed_min: Optional[float] = 20.0
    feed_max: Optional[float] = 10000.0
    spindle_min: Optional[float] = 500.0
    spindle_max: Optional[float] = 12000.0
    # 换行策略：auto（跟随源文件）/ crlf / lf。
    newline: str = "auto"
    # 辅助指令顺序规则集合（默认空 = 全部关闭）：
    #   m03-before-motion  M03 先于首次切削运动（error）
    #   m05-before-end     M05 先于程序结束（warning）
    #   m08-before-cut     M08 先于首次切削（warning）
    #   m09-before-end     M09 先于程序结束（warning，M09 未出现时不提示）
    aux_checks: set = field(default_factory=set)
    # F episode/peer-group 参数（GUI 校验规则页可调，持久化）：
    #   min_count 是同结构 peer group 形成重复参照的最小样本门槛；
    #   ratio 是 log(F) 相对距离的倍率阈值；
    #   low/high_ratio 是同结构参照的相对容差，不代表全局合法 F 范围。
    feed_outlier_min_count: int = 3
    feed_outlier_ratio: float = 2.0
    feed_outlier_low_ratio: float = 0.8
    feed_outlier_high_ratio: float = 1.2
    # 程序包含多个不同 S 值时是否报 warning（多主轴转速切换）。
    multiple_spindle_warn: bool = True
    # 处理前是否询问备份（GUI 基本设置可开关，持久化）。
    ask_backup: bool = True
    # WP-C1：单文件大小上限（字节）与单次扫描文件数上限（0 = 不限制，需求 9.4）。
    max_file_size: int = 0
    max_files: int = 0
    # WP-C9：抬刀高度阈值（Z 达到该值视为移动/退刀阶段），可配置并持久化。
    retract_z_threshold: float = RETRACT_Z_THRESHOLD


@dataclass
class ProgramInfo:
    bianzhi: str = ""
    shenhe: str = ""
    drawing_number: str = ""
    part_version: str = ""
    nc_machine: str = ""
    control_system: str = ""
    date: str = ""
    tools: List["ToolInfo"] = field(default_factory=list)

    def fields(self, program: str) -> Dict[str, str]:
        date = self.date or format_nc_date()
        return {
            "BIANZHI": self.bianzhi,
            "SHENHE": self.shenhe,
            "PROGRAM": program,
            "DRAWING NUMBER": self.drawing_number,
            "PART VERSION": self.part_version,
            "NC MACHINE": self.nc_machine,
            "CONTROL SYSTEM": self.control_system,
            "DATE": date,
        }


def format_nc_date(now: Optional[datetime] = None) -> str:
    """Format NC header time with locale-independent English month names."""
    value = now or datetime.now()
    months = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
    return f"{months[value.month - 1]} {value.day:02d} {value:%H:%M:%S %Y}"


@dataclass
class ToolInfo:
    number: int
    dia: str = ""
    tool_coner: str = ""
    tool_type: str = ""
    tool_angle: str = ""

    def __post_init__(self):
        self.dia = _format_tool_value(self.dia)
        self.tool_coner = _format_tool_value(self.tool_coner)
        self.tool_angle = _format_tool_value(self.tool_angle)

    def to_msg(self) -> str:
        values = []
        if self.dia.strip():
            values.append("DIA=" + self.dia.strip())
        if self.tool_coner.strip():
            values.append("TOOL_CONER=" + self.tool_coner.strip())
        if self.tool_angle.strip():
            values.append("TOOL_ANGLE=" + self.tool_angle.strip())
        if self.tool_type.strip():
            values.append("TOOL_TYPE=" + self.tool_type.strip())
        return f"T{self.number}:" + ",".join(values) if values else ""


def _format_tool_value(value: str) -> str:
    """Normalize numeric tool values to three decimal places."""
    raw = str(value).strip()
    if not raw:
        return ""
    try:
        return "{:.3f}".format(float(raw))
    except ValueError:
        return raw


@dataclass
class Issue:
    filename: str
    line: int
    text: str
    kind: str
    severity: str  # error, warning, info
    suggestion: str


@dataclass
class Stats:
    counts: Dict[str, int] = field(default_factory=dict)
    distinct: Dict[str, List[str]] = field(default_factory=dict)
    minimum: Dict[str, Optional[float]] = field(default_factory=dict)
    maximum: Dict[str, Optional[float]] = field(default_factory=dict)
    # Number of rapid-positioning G00/G0 commands in the program body.
    # Kept with the precomputed statistics so the GUI can render the full
    # file browser without rescanning program text.
    g00_count: int = 0

    def as_dict(self):
        return asdict(self)


@dataclass
class FeedEpisode:
    """One explicit F event and the movement structure until the next F."""
    line: int
    raw_value: str
    value: float
    raw_line: str
    signature: str
    feature: str
    at_retract: bool = False
    phase_role: str = "transition-uncertain"
    direction: str = "unknown"
    transition_evidence: Dict[str, object] = field(default_factory=dict)
    start_z: Optional[float] = None
    end_z: Optional[float] = None
    motion_row_count: int = 0


@dataclass
class FeedOutlierData:
    """F 离群检测过程数据（episode/peer-group 结构对照法）。

    - common_feeds/stage_common_feeds：兼容诊断汇总，不是固定合法 F 表
    - phase_common_feeds：按进退刀阶段汇总的程序内参照
    - peer_groups：按 episode 结构签名分组的程序内参照及样本计数
    - compatible_peer_groups：同阶段兼容结构父组
    - insufficient_evidence：结构组不足、无重复参照或模式不稳定的记录
      （普通唯一结构只计入 coverage，不逐条输出）
    - episodes：显式 F episode 的阶段角色与转阶段证据
    - coverage：可比较与未比较 episode 数量
    - envelope：兼容诊断字段，仅表示汇总参照的相对容差范围
    - outliers：同结构 episode 相对离群明细，含 peer_group、sample_count、
      confidence、evidence 等证据字段
    - boundary_errors：硬边界（超 F/S 上下限，与 issues 中 feed-range 对应）
    - context_reviews：上下文角色复核（切削区突现大档 / 快速移动用小档等）
    """
    apt_feeds: List[float] = field(default_factory=list)
    common_feeds: List[float] = field(default_factory=list)
    stage_common_feeds: Dict[str, List[float]] = field(default_factory=dict)
    phase_common_feeds: Dict[str, List[float]] = field(default_factory=dict)
    peer_groups: Dict[str, dict] = field(default_factory=dict)
    compatible_peer_groups: Dict[str, dict] = field(default_factory=dict)
    insufficient_evidence: List[dict] = field(default_factory=list)
    episodes: List[dict] = field(default_factory=list)
    coverage: Dict[str, int] = field(default_factory=dict)
    envelope: List[Optional[float]] = field(default_factory=lambda: [None, None])
    min_count: int = 3
    ratio: float = 2.0
    low_ratio: float = 0.8
    high_ratio: float = 1.2
    outliers: List[dict] = field(default_factory=list)
    boundary_errors: List[dict] = field(default_factory=list)
    context_reviews: List[dict] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


@dataclass
class FilePlan:
    source: str
    kind: str
    program: Optional[str] = None
    target: Optional[str] = None
    action: str = "keep"
    issues: List[Issue] = field(default_factory=list)
    original_text: Optional[str] = None
    output_text: Optional[str] = None
    stats: Optional[Stats] = None
    changes: List[str] = field(default_factory=list)
    # Cached source metadata used to avoid rereading/reparsing APT files.
    parsed_tools: List[ToolInfo] = field(default_factory=list)
    modified_time: float = 0.0
    encoding: str = ""
    overwrite_target: bool = False
    duplicate_winner: str = ""
    duplicate_target: str = ""
    # 程序名来源：MSG / PPRINT / 文件名 / 手动确认（报告内容规范第 12 节）。
    program_name_source: str = ""
    # WP-A2：多刀程序跳过自动添加换刀指令的原因（空 = 未跳过）。
    auto_tool_change_skipped: str = ""
    # WP-A2：最新 APT 的元数据与轨迹统计（挂载到 MPF 计划，供展示/报告/校验）。
    apt_meta: Optional[AptMeta] = None
    apt_toolpath: Optional[ToolpathStats] = None
    apt_source_path: Optional[str] = None
    apt_encoding: str = ""
    # WP-A9 修订：F 离群检测过程数据（报告 files[].feed_outlier）。
    feed_outlier: Optional[FeedOutlierData] = None


@dataclass
class ScanResult:
    input_dir: str
    files: List[FilePlan]
    warnings: List[str] = field(default_factory=list)
    archive_stamp: str = ""
    # Drawing-number candidates discovered while scanning (label, value).
    # These are suggestions only; the GUI must not apply them automatically.
    drawing_candidates: List[Tuple[str, str]] = field(default_factory=list)


@dataclass
class ProcessReport:
    input_dir: str
    output_dir: str
    started_at: str
    finished_at: str = ""
    success: int = 0
    failed: int = 0
    skipped: int = 0
    moved: int = 0
    deleted: int = 0
    warnings: int = 0
    errors: int = 0
    files: List[dict] = field(default_factory=list)
    backup_dir: str = ""
    # WP-C6：本次运行事件子集（内存环形缓冲快照）与完整磁盘日志路径。
    runtime_log: List[dict] = field(default_factory=list)
    log_path: str = ""
    # 报告内容规范第 12 节建议新增字段。
    app_version: str = ""
    report_schema_version: int = 1
    config_snapshot: dict = field(default_factory=dict)
    user_confirmations: List[str] = field(default_factory=list)
    scan_warnings: List[str] = field(default_factory=list)
    archive_stamp: str = ""
    elapsed_seconds: float = 0.0
    generator: str = ""
    # WP-A3：报告级 APT 全局摘要（机床/转速/刀具/操作/刀具使用次数）。
    apt_summary: dict = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)

    def refresh_runtime_log(self) -> None:
        """导出/结束处理前刷新运行时事件快照（含导出事件与 log_path）。"""
        self.runtime_log = runtime_log().snapshot()
        # WP-R4：不再生成磁盘日志文件，log_path 恒为空；运行日志完整内嵌 runtime_log。
        self.log_path = ""

    def write_json(self, path: Path):
        # 导出时内嵌本次会话缓冲的最新快照，保证报告包含导出开始事件。
        self.refresh_runtime_log()
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    def write_csv(self, path: Path):
        rows = []
        for item in self.files:
            for issue in item.get("issues", []):
                rows.append({"file": item.get("file", ""), **issue})
            if not item.get("issues"):
                rows.append({"file": item.get("file", ""), "line": "", "text": "", "kind": item.get("action", ""), "severity": "info", "suggestion": ""})
        with path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["file", "line", "text", "kind", "severity", "suggestion"])
            writer.writeheader()
            writer.writerows(rows)


# --- 运行日志（WP-C6；WP-R3/R4 仅内存缓冲，日志内嵌报告 runtime_log）---
MAX_LOG_EVENTS = 500


@dataclass
class RuntimeEvent:
    """单条运行日志事件：时间 / 级别 / 事件类型 / 中文描述 / 附加上下文。"""

    time: str
    level: str
    event: str
    message: str
    detail: str = ""

    def to_dict(self):
        return {
            "time": self.time,
            "level": self.level,
            "event": self.event,
            "message": self.message,
            "detail": self.detail,
        }


class RuntimeLog:
    """内存环形缓冲的统一事件源（CLI/GUI 共用）。

    - 内存缓冲保留最近 max_events 条（默认 500），超限丢弃最旧事件并累计丢弃数；
    - snapshot() 供报告内嵌；发生丢弃时追加一条截断说明（仅当丢弃数变化时追加一次，
      多次导出不会重复出现）；
    - 不生成任何磁盘日志文件（WP-R4）：最终报告即单个 JSON，运行日志完整内嵌其中。
    """

    def __init__(self, max_events: int = MAX_LOG_EVENTS):
        self._events: "deque[RuntimeEvent]" = deque(maxlen=max_events)
        self._dropped = 0
        self._reported_dropped = 0
        self._lock = threading.Lock()

    def emit(self, level: str, event: str, message: str, detail: str = "") -> None:
        entry = RuntimeEvent(datetime.now().isoformat(timespec="seconds"), level, event, message, detail)
        with self._lock:
            if len(self._events) == self._events.maxlen:
                self._dropped += 1
            self._events.append(entry)

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

    def clear(self) -> None:
        with self._lock:
            self._events.clear()
            self._dropped = 0
            self._reported_dropped = 0


# 模块级共享事件源：core 内部埋点直接调用 emit_event；GUI/CLI 启动时 attach 磁盘日志。
_runtime_log = RuntimeLog()


def runtime_log() -> RuntimeLog:
    return _runtime_log


def reset_runtime_log() -> RuntimeLog:
    """清空并替换共享事件源（测试隔离与程序启动时调用）。"""
    global _runtime_log
    _runtime_log = RuntimeLog()
    return _runtime_log


def emit_event(level: str, event: str, message: str, detail: str = "") -> None:
    _runtime_log.emit(level, event, message, detail)


def save_timestamped_report(report: ProcessReport, directory: Path, keep: int = 3, now: Optional[datetime] = None) -> Path:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    path = directory / ("ncodeprocess-report-" + stamp + ".json")
    suffix = 1
    while path.exists():
        path = directory / ("ncodeprocess-report-" + stamp + "-" + str(suffix) + ".json")
        suffix += 1
    # WP-R4：导出报告不生成任何磁盘日志文件，运行日志完整内嵌报告 runtime_log。
    emit_event("info", "export_start", f"开始导出报告：{directory}")
    report.write_json(path)
    reports = sorted(directory.glob("ncodeprocess-report-*.json"), key=lambda p: (p.stat().st_mtime, p.name), reverse=True)
    removed = 0
    for old in reports[max(1, keep):]:
        try:
            old.unlink()
            removed += 1
        except OSError:
            pass
    emit_event("info", "export_finish", f"报告已导出：{path.name}；清理旧报告 {removed} 份")
    return path


def _decode(data: bytes, forced: str = "auto") -> Tuple[str, str]:
    if b"\x00" in data:
        raise UnicodeDecodeError(
            "unknown", data, 0, len(data),
            "数据包含 NUL 字节，疑似二进制或 UTF-16 文件，无法按文本解码",
        )
    if forced and forced.lower() != "auto":
        return data.decode(forced), forced
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig"), "utf-8-sig"
    for enc in ("utf-8", "gb2312", "gbk", "gb18030", "cp1252"):
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            pass
    raise UnicodeDecodeError("unknown", data, 0, len(data), "unable to identify file encoding")


def read_text(path: Path, encoding: str = "auto") -> Tuple[str, str, str]:
    data = path.read_bytes()
    text, used = _decode(data, encoding)
    newline = "\r\n" if b"\r\n" in data else "\n"
    return text, used, newline


def _effective_newline(text: str, config: Config) -> str:
    """按换行策略返回输出使用的换行符：auto 跟随源文本，crlf/lf 强制指定。"""
    if config.newline == "crlf":
        return "\r\n"
    if config.newline == "lf":
        return "\n"
    return "\r\n" if "\r\n" in text else "\n"


def _read_text_cached(path: Path, encoding: str = "auto") -> Tuple[str, str, str]:
    """Reuse unchanged source text during repeated GUI scans."""
    stat = path.stat()
    key = str(path.resolve())
    signature = (stat.st_mtime_ns, stat.st_size, encoding)
    cached = _SCAN_TEXT_CACHE.get(key)
    if cached and cached[:3] == signature:
        return cached[3]
    result = read_text(path, encoding)
    if len(_SCAN_TEXT_CACHE) > 1000:
        _SCAN_TEXT_CACHE.clear()
    _SCAN_TEXT_CACHE[key] = signature + (result,)
    return result


def _read_prefix(path: Path, encoding: str = "auto", limit: int = 131072) -> Tuple[str, str]:
    """Read only a small file prefix for metadata that is defined in headers."""
    with path.open("rb") as stream:
        data = stream.read(limit)
    try:
        return _decode(data, encoding)
    except UnicodeDecodeError:
        # A prefix can end in the middle of a multibyte character.  Header
        # records are ASCII, so ignoring that incomplete trailing character is
        # safe and avoids loading a multi-megabyte APT source unnecessarily.
        return data.decode("utf-8", errors="ignore"), ""


def _extract_apt_tools_from_path(path: Path, encoding: str = "auto") -> List[ToolInfo]:
    """Stream an APT file and retain only cutter/tool definition records."""
    stat = path.stat()
    key = str(path.resolve())
    cached = _APT_TOOL_CACHE.get(key)
    if cached and cached[:3] == (stat.st_mtime_ns, stat.st_size, encoding):
        return list(cached[3])
    selected = []
    take_continuation = False
    with path.open("rb") as stream:
        for raw_line in stream:
            upper = raw_line.upper()
            is_toolno = b"TOOLNO" in upper and b"/" in upper
            if take_continuation or (b"CUTTER" in upper and b"/" in upper) or is_toolno:
                selected.append(raw_line)
            take_continuation = is_toolno
    data = b"".join(selected)
    try:
        text = _decode(data, encoding)[0]
    except UnicodeDecodeError:
        text = data.decode("utf-8", errors="ignore")
    tools = extract_tools(text)
    if len(_APT_TOOL_CACHE) > 1000:
        _APT_TOOL_CACHE.clear()
    _APT_TOOL_CACHE[key] = (stat.st_mtime_ns, stat.st_size, encoding, list(tools))
    return tools


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
    tools: List[dict] = field(default_factory=list)                     # CUTTER/TOOLNO 刀具规格（number/dia/tool_coner/tool_type/tool_angle）

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
    tool_lines = []
    take_tool_continuation = False
    with path.open("rb") as stream:
        for raw_line in stream:
            upper_line = raw_line.upper()
            is_toolno = b"TOOLNO" in upper_line and b"/" in upper_line
            if take_tool_continuation or (b"CUTTER" in upper_line and b"/" in upper_line) or is_toolno:
                tool_lines.append(raw_line)
            take_tool_continuation = is_toolno
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
    if tool_lines:
        try:
            tool_text = _decode(b"".join(tool_lines), encoding)[0]
        except (UnicodeDecodeError, ValueError):
            tool_text = b"".join(tool_lines).decode("utf-8", errors="ignore")
        meta.tools = [
            {"number": tool.number, "dia": tool.dia, "tool_coner": tool.tool_coner,
             "tool_type": tool.tool_type, "tool_angle": tool.tool_angle}
            for tool in extract_tools(tool_text)
        ]
    return meta


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


@dataclass
class ToolpathStats:
    """APT 轨迹统计（规划轨迹，供报告与查看器展示）。"""
    goto_count: int = 0
    min_x: float = 0.0
    max_x: float = 0.0
    min_y: float = 0.0
    max_y: float = 0.0
    min_z: float = 0.0
    max_z: float = 0.0
    arc_count: int = 0
    retract_count: int = 0
    retract_plane: Optional[float] = None          # 自适应抬刀平面（出现最多的最高面）

    def to_dict(self):
        return asdict(self)


_APT_TOOLPATH_CACHE: Dict[str, Tuple[int, int, str, ToolpathStats]] = {}
_APT_TRACE_CACHE: Dict[str, Tuple[int, int, str, List[float]]] = {}


def _retract_runs(z_values: Sequence[float], plane: float, tolerance: float) -> int:
    """统计 Z ≥ 平面-容差 的连续上升段数量（连续高 Z 点合并为一次抬刀）。"""
    count = 0
    in_run = False
    threshold = plane - tolerance
    for z in z_values:
        high = z >= threshold
        if high and not in_run:
            count += 1
        in_run = high
    return count


def _adaptive_retract_plane(z_values: Sequence[float]) -> Optional[float]:
    """出现最多的最高面：在最高重复 Z 面的邻带内，取出现次数最多的 Z 值。

    孤立高点（频次 < 2）不参与；邻带宽度 = max(5, 最高面×5%)；
    无法确定时回退文件最高 Z。
    """
    if not z_values:
        return None
    counts = Counter(round(value, 3) for value in z_values)
    repeated = sorted((value for value, count in counts.items() if count >= 2), reverse=True)
    if not repeated:
        return max(z_values)
    highest = repeated[0]
    band_low = highest - max(5.0, highest * 0.05)
    candidates = [(counts[value], value) for value in repeated if value >= band_low]
    if not candidates:
        return highest
    _count, plane = max(candidates)
    return plane


def _stream_z_values(path: Path, encoding: str = "auto") -> List[float]:
    """流式读取 GOTO 轨迹的 Z 序列（供抬刀高度重算使用）。"""
    nums = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?")
    z_values: List[float] = []
    with path.open("rb") as stream:
        for raw_line in stream:
            if not raw_line.startswith(b"GOTO"):
                continue
            values = [float(value) for value in nums.findall(raw_line.decode("ascii", errors="ignore"))]
            if len(values) >= 3:
                z_values.append(values[2])
    return z_values


def extract_apt_toolpath(path: Path, encoding: str = "auto") -> ToolpathStats:
    """流式统计 APT 轨迹：GOTO 点数、XYZ 行程、圆弧数、抬刀次数（自适应平面）。"""
    stats = ToolpathStats()
    nums = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?")
    z_values: List[float] = []
    initialized = False
    native_retract = 0
    with path.open("rb") as stream:
        for raw_line in stream:
            if raw_line.lstrip().startswith(b"RAPID") or b"GOHOME" in raw_line:
                native_retract += 1
            elif raw_line.startswith(b"GOTO"):
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
                z_values.append(z)
            elif b"CIRCLE" in raw_line:
                stats.arc_count += 1
    if native_retract:
        # APT 原生快速移动标记优先（未来后处理可能输出 RAPID/GOHOME）。
        stats.retract_count = native_retract
    else:
        plane = _adaptive_retract_plane(z_values)
        if plane is not None:
            stats.retract_plane = plane
            stats.retract_count = _retract_runs(z_values, plane, max(5.0, plane * 0.05))
    try:
        stat = path.stat()
        key = str(path.resolve())
        _APT_TRACE_CACHE[key] = (stat.st_mtime_ns, stat.st_size, encoding, z_values)
        if len(_APT_TRACE_CACHE) > 1000:
            _APT_TRACE_CACHE.clear()
    except OSError:
        pass
    return stats


def _extract_apt_toolpath_cached(path: Path, encoding: str = "auto") -> ToolpathStats:
    stat = path.stat()
    key = str(path.resolve())
    cached = _APT_TOOLPATH_CACHE.get(key)
    if cached and cached[:3] == (stat.st_mtime_ns, stat.st_size, encoding):
        return cached[3]
    stats = extract_apt_toolpath(path, encoding)
    if len(_APT_TOOLPATH_CACHE) > 1000:
        _APT_TOOLPATH_CACHE.clear()
    _APT_TOOLPATH_CACHE[key] = (stat.st_mtime_ns, stat.st_size, encoding, stats)
    return stats


def recount_retracts(path: Path, height: float, encoding: str = "auto") -> int:
    """按指定抬刀高度重算抬刀次数（优先使用轨迹缓存；无缓存时重新流式读取）。"""
    stat = path.stat()
    key = str(path.resolve())
    cached = _APT_TRACE_CACHE.get(key)
    if not cached or cached[:3] != (stat.st_mtime_ns, stat.st_size, encoding):
        z_values = _stream_z_values(path, encoding)
        _APT_TRACE_CACHE[key] = (stat.st_mtime_ns, stat.st_size, encoding, z_values)
    else:
        z_values = cached[3]
    return _retract_runs(z_values, height, max(5.0, height * 0.05))


def _first_lines(text: str, limit: int) -> List[str]:
    """Return a small text prefix without splitting the complete file."""
    return [line.rstrip("\r") for line in text.split("\n", limit)[:limit]]


def _safe_name(name: str, pattern: str = DEFAULT_NAME_PATTERN) -> bool:
    return bool(name and re.match(pattern, name) and not any(c in name for c in '\\/:*?"<>|'))


def code_part(line: str) -> str:
    """Return the NC code part, stripping comments in both common forms.

    Parenthesised comments ``( ... )`` are removed entirely; anything after a
    semicolon is treated as a trailing comment (the semicolon itself is kept
    as the block terminator).  Instructions inside either comment form are
    therefore never treated as real commands by parsing and validation.
    """
    if "(" in line:
        line = line.split("(", 1)[0]
    if ";" in line:
        line = line.split(";", 1)[0] + ";"
    return line


def _quantile(sorted_values: Sequence[float], q: float) -> float:
    """Linear-interpolated quantile (numpy-style, Type 7) for a sorted sample."""
    if not sorted_values:
        return 0.0
    position = (len(sorted_values) - 1) * q
    lower = int(position)
    upper = min(lower + 1, len(sorted_values) - 1)
    fraction = position - lower
    return sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * fraction


def _program_name_and_source(path: Path, text: Optional[str] = None, pattern: str = DEFAULT_NAME_PATTERN) -> Tuple[Optional[str], str]:
    """提取程序名并返回来源标记：MSG / PPRINT / 文件名（报告内容规范第 12 节）。"""
    if text:
        for line in _first_lines(text, 80):
            m = MSG_RE.match(line)
            if m:
                payload = m.group(1)
                key, sep, value = payload.partition(":")
                if sep and key.strip().upper() == "PROGRAM" and _safe_name(value.strip(), pattern):
                    return value.strip(), "MSG"
            m = PPRINT_RE.search(line)
            if m and _safe_name(m.group(1), pattern):
                return m.group(1), "PPRINT"
    stem = path.stem
    if stem.upper().endswith("_I"):
        stem = stem[:-2]
    if "_" in stem:
        candidate = stem.rsplit("_", 1)[1]
        if _safe_name(candidate, pattern):
            return candidate, "文件名"
    if _safe_name(stem, pattern):
        return stem, "文件名"
    return None, ""


def extract_program_name(path: Path, text: Optional[str] = None, pattern: str = DEFAULT_NAME_PATTERN) -> Optional[str]:
    name, _source = _program_name_and_source(path, text, pattern)
    return name


def _iter_files(directory: Path, recursive: bool) -> Iterable[Path]:
    iterator = directory.rglob("*") if recursive else directory.glob("*")
    return (p for p in iterator if p.is_file())


def extract_drawing_candidates(text: str) -> List[Tuple[str, str]]:
    """Extract drawing numbers advertised by an APT source header.

    CATIA APT exports commonly contain either of these records::

        $$ FILENAME      D0354F31311-201.CATProcess
        $$ PRODUCTNAME   NCSetup_M-D0354F31311-201_11.47.18

    Both records yield ``D0354F31311-201``.  The result keeps the source
    label so the GUI can explain where a suggestion came from, and removes
    duplicate values while preserving file order.
    """
    candidates: List[Tuple[str, str]] = []
    seen = set()

    def add(label: str, value: str) -> None:
        value = value.strip().strip('"\'')
        if not value or value in seen:
            return
        # Drawing numbers are deliberately permissive (letters, digits,
        # underscores and dashes are all seen in production data).
        if not re.match(r"^[A-Za-z0-9][A-Za-z0-9_-]*$", value):
            return
        seen.add(value)
        candidates.append((label, value))

    for raw_line in _first_lines(text, 200):
        line = raw_line.strip()
        match = re.match(r"^\$\$\s*FILENAME\s+(.+?)\s*$", line, re.I)
        if match:
            token = Path(match.group(1).strip().strip('"\'')).name
            value = re.sub(r"\.CATProcess$", "", token, flags=re.I)
            value = Path(value).stem
            add("APT FILENAME", value)
            continue
        match = re.match(r"^\$\$\s*PRODUCTNAME\s+(.+?)\s*$", line, re.I)
        if match:
            product = match.group(1).strip().strip('"\'')
            pm = re.search(r"NCSetup_M-(.+?)(?:_[0-9]+(?:\.[0-9]+){1,3})?$", product, re.I)
            if pm:
                add("APT PRODUCTNAME", pm.group(1))
    return candidates


def scan_directory(input_dir: str, config: Optional[Config] = None) -> ScanResult:
    config = config or Config()
    emit_event("info", "scan_start", f"开始扫描目录：{input_dir}")
    directory = Path(input_dir).resolve()
    files: List[FilePlan] = []
    warnings: List[str] = []
    drawing_candidates: List[Tuple[str, str]] = []
    drawing_seen = set()
    if not directory.is_dir():
        emit_event("warning", "scan_warning", f"输入目录不存在：{directory}")
        return ScanResult(str(directory), [], [f"输入目录不存在: {directory}"])
    if not os.access(directory, os.W_OK):
        warnings.append("当前目录只读：处理写入、移动、删除与报告导出可能失败，请先开放目录写权限")
    running_exe = Path(sys.executable).resolve() if getattr(sys, "frozen", False) else None
    scanned = 0
    for path in sorted(_iter_files(directory, config.recursive)):
        scanned += 1
        if config.max_files and scanned > config.max_files:
            warnings.append(f"扫描文件数量超过上限 {config.max_files}，已停止扫描，请调整「文件数量上限」或清理目录")
            break
        relative_parts = path.relative_to(directory).parts
        ignored_directories = {config.aptsource_dir.lower(), config.data_dir_name.lower()} | LEGACY_DATA_DIR_NAMES
        # WP-C5：递归扫描时任意深度的数据/归档目录均被忽略（如 sub/aptsource/、a/b/NCodeProcessData/）。
        if any(part.lower() in ignored_directories for part in relative_parts[:-1]):
            continue
        if running_exe and path.resolve() == running_exe:
            continue
        if path.name.lower().startswith(REPORT_PREFIXES) and path.suffix.lower() == ".json":
            continue
        ext = path.suffix.lower()
        rel = str(path.relative_to(directory))
        if ext in config.program_extensions:
            try:
                if config.max_file_size and path.stat().st_size > config.max_file_size:
                    plan = FilePlan(rel, "mpf", None, None, "error")
                    plan.issues.append(Issue(rel, 1, "", "file-too-large", "error",
                                             f"文件大小 {path.stat().st_size} 字节超过上限 {config.max_file_size} 字节，已跳过处理"))
                    files.append(plan)
                    continue
                text, used_encoding, _ = _read_text_cached(path, config.encoding)
                program, name_source = _program_name_and_source(path, text, config.allowed_name_pattern)
                plan = FilePlan(rel, "mpf", program, str(directory / (program + config.program_output_extension)) if program else None, "keep")
                plan.program_name_source = name_source
                plan.original_text = text
                plan.encoding = used_encoding
                plan.modified_time = path.stat().st_mtime
                drawing = extract_header_fields(text).get("DRAWING NUMBER", "").strip()
                drawing_label = "MPF提取"
                if drawing and (drawing_label, drawing) not in drawing_seen:
                    drawing_seen.add((drawing_label, drawing))
                    drawing_candidates.append((drawing_label, drawing))
                if not program:
                    plan.issues.append(Issue(rel, 1, "", "program-name", "error", "请手动确认程序名"))
            except UnicodeError as e:
                emit_event("error", "error", f"读取文件失败：{rel}", detail=str(e))
                plan = FilePlan(rel, "mpf", None, None, "error")
                plan.issues.append(Issue(rel, 1, "", "encoding", "error", str(e)))
            except PermissionError as e:
                emit_event("error", "error", f"无权限读取文件：{rel}", detail=str(e))
                plan = FilePlan(rel, "mpf", None, None, "error")
                plan.issues.append(Issue(rel, 1, "", "permission", "error", f"无权限读取文件：{e}"))
            except OSError as e:
                emit_event("error", "error", f"读取文件失败：{rel}", detail=str(e))
                plan = FilePlan(rel, "mpf", None, None, "error")
                plan.issues.append(Issue(rel, 1, "", "io", "error", f"读取文件失败：{e}"))
            files.append(plan)
        elif ext == ".aptsource":
            program, name_source = _program_name_and_source(path, None, config.allowed_name_pattern)
            apt_prefix = ""
            plan = FilePlan(rel, "aptsource", program, None, "move", original_text=None, parsed_tools=[], modified_time=path.stat().st_mtime)
            plan.program_name_source = name_source
            try:
                apt_prefix, apt_encoding = _read_prefix(path, config.encoding)
                plan.encoding = apt_encoding
                for label, value in extract_drawing_candidates(apt_prefix):
                    source_label = "APT提取"
                    if (source_label, value) not in drawing_seen:
                        drawing_seen.add((source_label, value))
                        drawing_candidates.append((source_label, value))
            except Exception:
                emit_event("warning", "scan_warning", f"APTSOURCE 头部解析失败：{rel}", detail=traceback.format_exc())
            # Full APT parsing is deferred to build_plan, which selects only
            # the newest source for each program.  This keeps repeated scans
            # fast when the archive contains many historical APT files.
            files.append(plan)
        elif ext in config.delete_extensions:
            files.append(FilePlan(rel, "intermediate", None, None, "delete"))
        else:
            # The application is intentionally limited to MPF, aptsource,
            # LOG and MOAPTIndexes. All other files are completely ignored.
            continue
    if not any(f.kind == "mpf" for f in files):
        warnings.append("目录中未找到 MPF 文件")
    for warning in warnings:
        emit_event("warning", "scan_warning", warning)
    result = ScanResult(str(directory), files, warnings, datetime.now().strftime("%Y%m%d_%H%M%S"), drawing_candidates)
    emit_event("info", "scan_finish",
               f"扫描完成：{len(files)} 个文件，MPF {sum(f.kind == 'mpf' for f in files)} 个，"
               f"APTSOURCE {sum(f.kind == 'aptsource' for f in files)} 个，"
               f"待删除 {sum(f.kind == 'intermediate' for f in files)} 个，图号候选 {len(drawing_candidates)} 项")
    return result


def _parse_msg(line: str) -> Optional[Tuple[str, str]]:
    m = MSG_RE.match(line)
    if not m:
        return None
    payload = m.group(1)
    key, sep, value = payload.partition(":")
    if not sep:
        return None
    return key.strip(), value


def program_defaults(text: str, info: ProgramInfo) -> ProgramInfo:
    """Load reusable header values and fill defaults only when absent."""
    result = ProgramInfo(info.bianzhi, info.shenhe, info.drawing_number, info.part_version, info.nc_machine, info.control_system, info.date, list(info.tools))
    existing = extract_header_fields(text)
    result.nc_machine = existing.get("NC MACHINE", "") or ("2500B" if existing else "HASS")
    result.control_system = existing.get("CONTROL SYSTEM", "") or "SIE840D"
    result.date = existing.get("DATE", "") or info.date
    return result


def extract_header_fields(text: str) -> Dict[str, str]:
    """Return the first value of each MSG header field."""
    fields: Dict[str, str] = {}
    for line in _first_lines(text, 120):
        parsed = _parse_msg(line)
        if parsed:
            key, value = parsed
            fields.setdefault(key.strip().upper(), value.strip())
    return fields


def extract_tools(text: str) -> List[ToolInfo]:
    """Extract editable tool defaults from MSG rows or APT CUTTER/TOOLNO records.

    APT's CUTTER record carries the actual cutter diameter and corner radius.
    TOOLNO repeats a nominal diameter and may carry an included angle.  Equal
    cutter/nominal diameters identify an ordinary end mill unless the included
    angle is over 100 degrees.  Unequal diameters identify reverse-taper or
    pencil cutters from the angle sign.  Included angles over 100 degrees are
    drills or center drills; their continuation row distinguishes the two.
    The editable angle is always the single-side angle (half of TOOLNO's
    included angle).
    """
    found: Dict[int, ToolInfo] = {}
    cutter_dia = ""
    cutter_coner = ""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        parsed = _parse_msg(line)
        if parsed:
            key, value = parsed
            m = re.match(r"^T(\d+)$", key.strip(), re.I)
            if m:
                number = int(m.group(1)); values = {}
                for part in value.split(":", 1)[-1].split(","):
                    k, sep, v = part.partition("=")
                    if sep: values[k.strip().upper()] = v.strip()
                found[number] = ToolInfo(
                    number,
                    values.get("DIA", ""),
                    values.get("TOOL_CONER", ""),
                    values.get("TOOL_TYPE", ""),
                    values.get("TOOL_ANGLE", ""),
                )

        cutter = re.search(r"\bCUTTER\s*/\s*(" + NUM + r")\s*,\s*(" + NUM + r")", line, re.I)
        if cutter:
            cutter_dia, cutter_coner = cutter.group(1), cutter.group(2)

        m = re.search(
            r"\bTOOLNO\s*/\s*(\d+)\s*,\s*(" + NUM + r")\s*,\s*(" + NUM + r")?\s*,\s*(" + NUM + r")?",
            line,
            re.I,
        )
        if m:
            number = int(m.group(1))
            nominal_dia = m.group(2) or ""
            nominal_coner = m.group(3) or ""
            included_angle = m.group(4) or ""
            continuation = []
            if index + 1 < len(lines):
                next_line = lines[index + 1].strip()
                if "," in next_line and not re.match(r"^(?:[A-Z][A-Z0-9_]*|\$\$)\s*/", next_line, re.I):
                    continuation = [part.strip() for part in next_line.split(",")]
            current = found.get(number, ToolInfo(number))
            dia = cutter_dia or nominal_dia or current.dia
            coner = cutter_coner or nominal_coner or current.tool_coner
            tool_type = current.tool_type
            tool_angle = current.tool_angle
            if included_angle:
                try:
                    angle_value = float(included_angle)
                except ValueError:
                    angle_value = 0.0
                if abs(angle_value) > 100.0:
                    # CATIA drill records expose a tip-height value and/or a
                    # numeric tool code (commonly 2) on TOOLNO's continuation
                    # row.  Center drills leave those positions empty.  These
                    # positions do not depend on cutter diameter, so the rule
                    # works for arbitrary drill sizes.
                    tip_height = continuation[1] if len(continuation) > 1 else ""
                    tool_code = continuation[3] if len(continuation) > 3 else ""
                    def nonzero(value: str) -> bool:
                        if not value:
                            return False
                        try:
                            return abs(float(value)) > 1e-9
                        except ValueError:
                            return True
                    tool_type = "钻头" if nonzero(tip_height) or nonzero(tool_code) else "中心钻"
                    # Drill and center-drill angles are used only for type
                    # classification; their angle is intentionally omitted
                    # from the MPF tool MSG row.
                    tool_angle = ""

            if cutter_dia and nominal_dia and tool_type not in ("钻头", "中心钻"):
                try:
                    same_dia = abs(float(cutter_dia) - float(nominal_dia)) <= 1e-9
                    if cutter_coner and nominal_coner:
                        same_coner = abs(float(cutter_coner) - float(nominal_coner)) <= 1e-9
                    else:
                        same_coner = not cutter_coner and not nominal_coner
                except ValueError:
                    same_dia = cutter_dia.strip() == nominal_dia.strip()
                    same_coner = cutter_coner.strip() == nominal_coner.strip()
                if same_dia and same_coner:
                    # 普通立铣刀按 R 与 D 的关系细分：球头 R=D/2、平底 R=0、其余圆鼻。
                    try:
                        radius = float(cutter_coner)
                        mill_dia = float(cutter_dia)
                    except ValueError:
                        radius = mill_dia = 0.0
                    if radius <= 1e-9:
                        tool_type = "平底立铣刀"
                    elif abs(radius * 2 - mill_dia) <= 1e-6:
                        tool_type = "球头立铣刀"
                    else:
                        tool_type = "圆鼻立铣刀"
                elif included_angle:
                    try:
                        angle_value = float(included_angle) / 2.0
                        tool_angle = "{:.6f}".format(angle_value)
                        if angle_value < 0:
                            tool_type = "反锥立铣刀"
                        elif angle_value > 0:
                            tool_type = "铅笔铣刀"
                    except ValueError:
                        pass
                else:
                    # T 形刀初步识别：直径差异大（比值 >= 2）且无锥度角度。
                    try:
                        cutter_value = float(cutter_dia)
                        nominal_value = float(nominal_dia)
                        ratio = max(cutter_value, nominal_value) / min(cutter_value, nominal_value)
                    except (ValueError, ZeroDivisionError):
                        ratio = 1.0
                    if ratio >= 2.0:
                        tool_type = "T形刀"
            found[number] = ToolInfo(number, dia, coner, tool_type, tool_angle)
            # A CUTTER record belongs to the next TOOLNO only.
            cutter_dia = ""
            cutter_coner = ""
    return [found[n] for n in sorted(found)]


def _msg_line(key: str, value: str, semicolon: bool = False) -> str:
    return f'MSG("{key}:{value}")' + (";" if semicolon else "")


def _header_end(lines: Sequence[str]) -> int:
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or re.match(r"^%\s*;?$", stripped) or _parse_msg(line):
            continue
        if stripped.startswith("(") and stripped.endswith(")"):
            continue
        return i
    return len(lines)


def apply_header(text: str, program: str, info: ProgramInfo, config: Config, *, replace_tools: bool = False, filename: str = "") -> Tuple[str, List[str], List[Issue]]:
    newline = _effective_newline(text, config)
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    had_trailing = lines and lines[-1] == ""
    if had_trailing:
        lines = lines[:-1]
    end = _header_end(lines)
    header = lines[:end]
    body = lines[end:]
    existing_tool_keys = set()
    existing_tool_objects = {}
    skip_tools = set()
    if replace_tools or info.tools:
        existing_tool_objects = {tool.number: tool for tool in extract_tools("\n".join(header))}
        new_tools_by_number = {tool.number: tool for tool in info.tools}
        filtered = []
        for line in header:
            parsed = _parse_msg(line)
            if parsed:
                tool_match = re.match(r"^T(\d+)$", parsed[0], re.I)
                if tool_match:
                    number = int(tool_match.group(1))
                    existing_tool_keys.add(parsed[0].upper())
                    new_tool = new_tools_by_number.get(number)
                    old_tool = existing_tool_objects.get(number)
                    if new_tool is not None and old_tool is not None and (
                        old_tool.dia == new_tool.dia
                        and old_tool.tool_coner == new_tool.tool_coner
                        and old_tool.tool_angle == new_tool.tool_angle
                        and old_tool.tool_type == new_tool.tool_type
                    ):
                        # 刀具信息完全一致：保留原行、不记录变更。
                        filtered.append(line)
                        skip_tools.add(parsed[0].upper())
                        continue
                    # 有变化或新刀具列表不含该号：移除该行（有变化者稍后按新值重写）。
                    continue
            filtered.append(line)
        header = filtered
    fields = info.fields(program)
    changes: List[str] = []
    # FR-04.2.4: repeated keys keep the first record but must surface as
    # warnings so the GUI validation table and report warning counts show them.
    issues: List[Issue] = []
    seen: Dict[str, int] = {}
    # FR-04.2.5: 头部已有 MSG 行的缩进作为新增/替换行的缩进参考，避免丢失缩进。
    msg_indent = ""
    for line in header:
        if _parse_msg(line):
            msg_indent = line[:len(line) - len(line.lstrip())]
            break
    semicolon = any(l.rstrip().endswith(";") for l in header if _parse_msg(l))
    if not semicolon and not any(_parse_msg(l) for l in header):
        semicolon = any(l.rstrip().endswith(";") for l in body[:20] if l.strip())
    for idx, line in enumerate(header):
        parsed = _parse_msg(line)
        if not parsed:
            continue
        key, value = parsed
        upper = key.upper()
        if upper in fields or re.match(r"^T\d+$", upper):
            if upper in seen:
                changes.append(f"重复头部字段 {key}（第 {idx + 1} 行）")
                issues.append(Issue(
                    filename,
                    idx + 1,
                    line.strip(),
                    "duplicate-msg-field",
                    "warning",
                    f"MSG 字段 {key} 出现多次，已保留第一条有效记录，请确认是否合并或删除重复项",
                ))
            else:
                seen[upper] = idx
            if upper in fields:
                new_value = fields[upper]
                # WP-B2：NC MACHINE/CONTROL SYSTEM 已有非空值永久保护；DATE 由
                # build_plan/reprocess_file 层按“程序发生变更”自动更新；PROGRAM
                # 不保护，程序名修改统一走「修改程序名」流程，勾选覆盖时头部与程序名对齐。
                protect_existing = upper in ("NC MACHINE", "CONTROL SYSTEM", "DATE") and bool(value.strip())
                if not protect_existing and (not value.strip() or config.overwrite_fields) and new_value != value:
                    indent = line[:len(line) - len(line.lstrip())]
                    header[idx] = indent + _msg_line(key, new_value, line.rstrip().endswith(";"))
                    changes.append(f"补全/更新 {upper}")
    field_insert: List[str] = []
    for key, _label, _required in FIELD_ORDER:
        if key not in seen:
            value = fields[key]
            if value or key in config.required_fields:
                field_insert.append(msg_indent + _msg_line(key, value, semicolon))
                changes.append(f"插入 {key}")
    tool_insert: List[str] = []
    for tool in sorted(info.tools, key=lambda t: t.number):
        payload = tool.to_msg()
        if not payload:
            continue
        key = f"T{tool.number}"
        if key.upper() in skip_tools:
            continue
        line = msg_indent + _msg_line(key, payload.split(":", 1)[1], semicolon)
        if key in seen:
            # Replace only when caller supplied tool information; values remain editable.
            header[seen[key]] = line
            changes.append(f"更新刀具 {key}")
        elif key.upper() in existing_tool_keys:
            # 原有刀具被替换后重新写入：记录为更新而非重复插入。
            tool_insert.append(line)
            changes.append(f"更新刀具 {key}")
        else:
            tool_insert.append(line)
            changes.append(f"插入刀具 {key}")
    if field_insert:
        # Keep % as the first line for HASS. Existing header rows stay in original order.
        pos = 1 if header and re.match(r"^%\s*;?$", header[0].strip()) else 0
        header[pos:pos] = field_insert
    if tool_insert:
        # Tool MSG rows are always the final header information rows.
        header.extend(tool_insert)
    while header and not header[-1].strip():
        header.pop()
    result_lines = header + body
    if had_trailing:
        result_lines.append("")
    return newline.join(result_lines), changes, issues


def update_header_date(text: str, date_value: str) -> Tuple[str, bool]:
    """Replace the header DATE MSG row with the given value (WP-B2).

    Returns ``(text, changed)``.  The original text is returned unchanged when
    the DATE row already carries the requested value or no DATE row exists.
    """
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    end = _header_end(lines)
    newline = "\r\n" if "\r\n" in text else "\n"
    for idx in range(end):
        parsed = _parse_msg(lines[idx])
        if parsed and parsed[0].upper() == "DATE":
            if parsed[1].strip() == date_value:
                return text, False
            indent = lines[idx][:len(lines[idx]) - len(lines[idx].lstrip())]
            semicolon = lines[idx].rstrip().endswith(";")
            lines[idx] = indent + _msg_line("DATE", date_value, semicolon)
            return newline.join(lines), True
    return text, False


def _find_body_start(text: str) -> int:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return _header_end(lines)


def add_initial_tool_change(text: str, tools: Sequence[ToolInfo], config: Config) -> Tuple[str, bool, str]:
    """Insert a canonical first-tool change immediately after the MSG header.

    The option is intentionally disabled by default.  When enabled, the
    lowest configured tool number is used as the program's initial tool. Any
    existing T-number references are corrected, and standalone tool-change
    rows are consolidated into one ``TnM6`` row at the beginning of the body.
    """
    if not config.auto_tool_change or not tools:
        return text, False, ""
    numbers = [tool.number for tool in tools if tool.number > 0]
    if not numbers:
        return text, False, ""
    number = min(numbers)
    newline = _effective_newline(text, config)
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    had_trailing = bool(lines and lines[-1] == "")
    if had_trailing:
        lines = lines[:-1]
    start = _header_end(lines)
    header = lines[:start]
    body = lines[start:]
    while body and not body[0].strip():
        body.pop(0)
    # WP-A2：多刀程序不具备自动添加换刀指令条件，跳过改写并给出明确提示。
    if len(tools) > 1:
        return text, False, "程序包含多把刀具，不具备自动添加换刀指令条件，已跳过生成，请人工确认换刀流程"
    referenced = set()
    for line in body:
        code = code_part(line)
        referenced.update(int(match.group(1)) for match in TOOL_CALL_RE.finditer(code))
    if len(referenced) > 1:
        return (
            text, False,
            "程序引用多把刀具（" + "、".join("T" + str(number) for number in sorted(referenced)) +
            "），不具备自动添加换刀指令条件，已跳过生成，请人工确认换刀流程",
        )

    corrected = []
    for line in body:
        if STANDALONE_CHANGE_RE.match(line):
            continue
        # 只替换真实代码部分的 T 号：括号注释（如 (T2 备用)）与分号后注释
        # （HASS 的 ;T99 备用）中的 T 号一律保持原样。
        code_segment, semicolon_sep, semicolon_tail = line.partition(";")
        segments = re.split(r"(\(.*?\))", code_segment)
        replaced = "".join(
            segment if segment.startswith("(") else TOOL_REF_RE.sub("T" + str(number), segment)
            for segment in segments
        )
        corrected.append(replaced + (semicolon_sep + semicolon_tail if semicolon_sep else ""))

    semicolon = any(line.rstrip().endswith(";") for line in corrected[:30] if line.strip())
    command = "T{}M6{}".format(number, ";" if semicolon else "")
    result_lines = header + [command] + corrected
    if had_trailing:
        result_lines.append("")
    result = newline.join(result_lines)
    return result, result != text, "在程序正文首行添加/更新换刀指令 " + command


def add_m03(text: str, config: Config) -> Tuple[str, bool, str]:
    if not config.auto_m03:
        return text, False, ""
    newline = _effective_newline(text, config)
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    start = _header_end(lines)
    for line in lines[start:]:
        if _parse_msg(line) or line.strip() in ("", "%"):
            continue
        code = code_part(line)
        if M03_RE.search(code):
            return text, False, ""
        if M04_RE.search(code):
            # WP-A1：正文以 M04 反转启动主轴时，禁止自动补写 M03（正反转冲突）。
            return text, False, ""
    if config.m03_position == "standalone":
        return _insert_standalone_m03(text, lines, start, newline)
    for idx in range(start, len(lines)):
        line = lines[idx]
        if _parse_msg(line):
            continue
        # Search only the code part: an S value inside a parenthetical
        # comment is not a spindle command and must not capture M03.
        code = code_part(line)
        if not S_RE.search(code):
            continue
        if ";" in code:
            before, after = line.split(";", 1)
            lines[idx] = before + "M03;" + after
        else:
            lines[idx] = line + "M03"
        return newline.join(lines), True, f"第 {idx + 1} 行 S 指令后补写 M03"
    for idx in range(start, len(lines)):
        stripped = lines[idx].strip()
        if stripped and stripped != "%" and not stripped.startswith("(") and not stripped.startswith(";"):
            lines.insert(idx, "M03;" if any(l.rstrip().endswith(";") for l in lines[start:] if l.strip()) else "M03")
            return newline.join(lines), True, f"第 {idx + 1} 行前插入独立 M03"
    return text, False, "无法确定 M03 插入位置"


def _insert_standalone_m03(text: str, lines: Sequence[str], start: int, newline: str) -> Tuple[str, bool, str]:
    """独立行策略：在第一条切削/运动指令（G1/G2/G3 或 X/Y/Z）前插入独立 M03 行。

    找不到切削/运动指令时回退到第一条指令行前插入，与 after-s 无 S 时的行为一致。
    """
    semicolon = any(line.rstrip().endswith(";") for line in lines[start:] if line.strip())
    command = "M03;" if semicolon else "M03"
    for idx in range(start, len(lines)):
        code = code_part(lines[idx])
        if _parse_msg(lines[idx]) or not code.strip():
            continue
        if MOTION_ANY_RE.search(code):
            lines.insert(idx, command)
            return newline.join(lines), True, f"第 {idx + 1} 行前插入独立 M03"
    for idx in range(start, len(lines)):
        stripped = lines[idx].strip()
        if stripped and stripped != "%" and not stripped.startswith("(") and not stripped.startswith(";"):
            lines.insert(idx, command)
            return newline.join(lines), True, f"第 {idx + 1} 行前插入独立 M03"
    return text, False, "无法确定 M03 插入位置"


def calculate_stats(text: str) -> Stats:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    start = _header_end(lines)
    keys = "FSXYZ"
    counts = {k: 0 for k in keys}
    # Only F and S require a distinct-value list. Tracking every X/Y/Z value
    # creates large dictionaries for toolpaths and is unnecessary for the
    # requested count/min/max statistics.
    distinct_values: Dict[str, Dict[str, float]] = {"F": {}, "S": {}}
    mins: Dict[str, Optional[float]] = {k: None for k in keys}
    maxs: Dict[str, Optional[float]] = {k: None for k in keys}
    g00_count = 0
    for line in lines[start:]:
        stripped = line.strip()
        if not stripped or stripped == "%":
            continue
        code = code_part(line)
        g00_count += sum(1 for _match in G00_RE.finditer(code))
        for m in ADDR_RE.finditer(code):
            key, raw = m.group(1).upper(), m.group(2)
            if key not in keys:
                continue
            try:
                value = float(raw)
            except ValueError:
                continue
            counts[key] += 1
            if key in distinct_values:
                distinct_values[key].setdefault(raw, value)
            if mins[key] is None or value < mins[key]:
                mins[key] = value
            if maxs[key] is None or value > maxs[key]:
                maxs[key] = value
    distinct = {key: list(distinct_values.get(key, {}).keys()) for key in keys}
    return Stats(counts, distinct, mins, maxs, g00_count)


def _new_stats() -> Stats:
    keys = "FSXYZ"
    return Stats(
        {key: 0 for key in keys},
        {key: [] for key in keys},
        {key: None for key in keys},
        {key: None for key in keys},
        0,
    )


def _base_motion_feature(changes: int, net: Optional[float], d_in: Optional[float],
                         d_out: Optional[float], g00: bool, at_retract: bool,
                         prev1_z: Optional[float], move_level: float) -> str:
    """按 Z 运动判定动作特征（不含绝对 F 数值，2026-08-07 用户标注 98.9% 对齐）。

    判定顺序（±4 行窗口，方向变化次数 ≥ FEED_OSC_MIN 视为振荡/啄钻）：
      1. G00 或在抬刀平面（含模态 Z）→ move；
      2. 下降进入本行且前一位置在抬刀平面 → move（从抬刀平面快速下刀）；
      3. 单步大幅上升（d_in ≥5）且后续不回跌 → move（退刀）；
      4. 已上升（d_in ≥1）且下一行继续大幅上升（d_out ≥5）→ move（跳刀起点）；
      5. 振荡上下文：下行进入 → 局部最低点（行后回升）cut（钻孔钻入）否则 move
         （啄钻接近）；净上升 → move；否则 cut；
      6. 单向下行（d_in ≤ -1）→ plunge（进刀/下刀，含 Z 字形下刀）；
      7. 窗口净上升 ≥5 且上升已进入本行（d_in ≥1）→ move（净退刀）；
      8. 其余（Z 平稳）→ cut。
    """
    if g00 or at_retract:
        return "move"
    if d_in is not None and d_in <= -1.0 and prev1_z is not None and prev1_z >= move_level:
        return "move"
    if d_in is not None and d_in >= 5.0 and (d_out is None or d_out >= 1.0):
        return "move"
    if d_in is not None and d_in >= 1.0 and d_out is not None and d_out >= 5.0:
        return "move"
    if changes >= FEED_OSC_MIN:
        if d_in is not None and d_in <= -1.0:
            return "cut" if (d_out is not None and d_out >= 1.0) else "move"
        return "move" if (net is not None and net > 0) else "cut"
    if d_in is not None and d_in <= -1.0:
        return "plunge"
    if net is not None and net + 1e-9 >= 5.0 and d_in is not None and d_in >= 1.0:
        return "move"
    return "cut"


def _final_motion_feature(base: str, feed: Optional[float], plunge_feeds: set,
                          move_feeds: set, net: Optional[float],
                          d_in: Optional[float], has_motion: bool) -> str:
    """基础运动分类 + 程序内相对 F 档兜底（F 数值不写死，只做相对关联）。

    - 平稳 cut 且 F 属于程序内进刀档 → plunge（如 A0101 的 F300 平稳行）；
    - 无运动的孤立 F 设定行且 F 属于程序内移动档 → move（如 F6000 设定行）；
    - move 且 F 属于程序内进刀档、净上升但上升未进入本行（退刀前最后一行）
      → plunge（如 F300 慢速离面行）。
    """
    if base == "cut" and feed is not None and feed in plunge_feeds:
        return "plunge"
    if base == "cut" and not has_motion and feed is not None and feed in move_feeds:
        return "move"
    if (base == "move" and feed is not None and feed in plunge_feeds
            and net is not None and net + 1e-9 >= 5.0
            and (d_in is None or d_in < 1.0)):
        return "plunge"
    return base


def _feed_structure_signature(raw_line: str, feature: str, at_retract: bool,
                              z_values: Sequence[Optional[float]]) -> str:
    """Build a structure-only signature for one F episode.

    The signature deliberately ignores all F values.  It captures only the
    coordinate axes, motion family, Z trend, retract state and dominant role.
    """
    axes = set()
    motions = set()
    for value in z_values:
        if value is not None:
            axes.add("Z")
    code = code_part(raw_line)
    for parameter in ADDR_RE.finditer(code):
        key = parameter.group(1).upper()
        if key in "XY":
            axes.add(key)
    for match in MOTION_RE.finditer(code):
        motion = int(match.group(1))
        motions.add("rapid" if motion == 0 else "linear" if motion == 1 else "arc")
    if not motions:
        motions.add("unknown")
    axis_label = "XYZ" if len(axes) == 3 else "XY" if axes == {"X", "Y"} else "Z" if axes == {"Z"} else "none"
    if len(z_values) < 2:
        z_direction = "stable"
    else:
        directions = {1 if b > a else -1 for a, b in zip(z_values, z_values[1:])
                      if a is not None and b is not None and abs(b - a) > 1e-9}
        z_direction = ("mixed" if len(directions) > 1 else
                       "down" if directions == {-1} else
                       "up" if directions == {1} else "stable")
    motion_label = ("cycle" if "cycle" in motions else
                    "arc" if "arc" in motions else
                    "rapid" if "rapid" in motions and "linear" not in motions else
                    "linear" if "linear" in motions else "unknown")
    return "axes={}|motion={}|z={}|retract={}|role={}".format(
        axis_label, motion_label, z_direction, int(bool(at_retract)), feature)


def _episode_geometry(records: Sequence[tuple], start_index: int, end_index: int,
                      motion_trace: Sequence[tuple], z_vals: Sequence[Optional[float]],
                      move_level: float) -> Dict[str, object]:
    """Collect phase facts without reading any F value."""
    segment = records[start_index:end_index]
    z_sequence: List[float] = []
    motion_rows = []
    has_xy_motion = False
    has_g00 = False
    for rec in segment:
        (_line, raw, g00, has_xy, z_value, _lfv, _lfraw, _at_retract,
         has_motion, _mraw, _mval, tid, _feature, _motion_family) = rec
        effective_z = z_vals[tid] if 0 <= tid < len(z_vals) else z_value
        if effective_z is not None:
            z_sequence.append(effective_z)
        has_g00 = has_g00 or bool(g00) or bool(G00_RE.search(code_part(raw)))
        if has_motion:
            motion_rows.append(rec)
            has_xy_motion = has_xy_motion or bool(has_xy)

    first_tid = segment[0][11] if segment else None
    last_tid = segment[-1][11] if segment else None
    start_z = None
    if first_tid is not None and first_tid > 0 and first_tid - 1 < len(z_vals):
        start_z = z_vals[first_tid - 1]
    if start_z is None and z_sequence:
        start_z = z_sequence[0]
    end_z = z_sequence[-1] if z_sequence else start_z
    all_z = ([start_z] if start_z is not None else []) + z_sequence
    directions = {1 if b > a else -1 for a, b in zip(all_z, all_z[1:])
                  if abs(b - a) > 1e-9}
    direction = ("mixed" if len(directions) > 1 else
                 "down" if directions == {-1} else
                 "up" if directions == {1} else "stable")
    starts_below = start_z is not None and start_z < move_level
    reaches_retract = any(value >= move_level for value in all_z)
    safe_positioning = bool(
        (has_g00 and (not has_xy_motion or
                      (all_z and all(value >= move_level for value in all_z))))
        or (all_z and direction != "down" and
            all(value >= move_level for value in all_z))
    )
    # XY motion below the safe plane is a strong cut signal.  A descent that
    # starts at the safe plane remains plunge even when XY is present.
    cut_like = bool(has_xy_motion and direction in ("stable", "down") and
                    not safe_positioning and
                    not (direction == "down" and
                         start_z is not None and start_z >= move_level))
    if not cut_like and direction == "stable" and starts_below and motion_rows:
        cut_like = True
    return {
        "start_z": start_z,
        "end_z": end_z,
        "direction": direction,
        "has_xy_motion": has_xy_motion,
        "has_g00": has_g00,
        "motion_row_count": len(motion_rows),
        "starts_below_retract": starts_below,
        "reaches_retract_plane": reaches_retract,
        "safe_positioning": safe_positioning,
        "cut_like": cut_like,
        "last_tid": last_tid,
    }


def _annotate_feed_episode_phases(episodes: Sequence[FeedEpisode], spans: Sequence[tuple],
                                  records: Sequence[tuple], motion_trace: Sequence[tuple],
                                  z_vals: Sequence[Optional[float]], move_level: float) -> None:
    """Annotate entry/exit phases from trajectory context, never from F values."""
    facts = [
        _episode_geometry(records, span[0], span[1], motion_trace, z_vals, move_level)
        for span in spans
    ]
    seen_cut = False
    seen_exit = False
    for index, episode in enumerate(episodes):
        current = facts[index]
        previous_role = episodes[index - 1].phase_role if index else ""
        next_fact = facts[index + 1] if index + 1 < len(facts) else None
        next_line = episodes[index + 1].line if index + 1 < len(episodes) else None
        role = "transition-uncertain"
        if current["cut_like"]:
            role = "cut"
            seen_cut = True
            seen_exit = False
        elif current["safe_positioning"]:
            role = "move-out" if seen_cut or seen_exit else "move-in"
            if role == "move-out":
                seen_exit = True
        elif current["direction"] == "up":
            if seen_cut or seen_exit or previous_role in ("retreat-near", "retreat-clear"):
                continued_upward = bool(
                    next_fact and (next_fact["direction"] == "up" or
                                   next_fact["safe_positioning"])
                )
                if continued_upward:
                    role = ("retreat-clear" if previous_role in ("retreat-near", "retreat-clear")
                            and (current["reaches_retract_plane"] or current["safe_positioning"])
                            else "retreat-near")
                    seen_exit = True
        elif current["direction"] == "down":
            if not seen_cut:
                if next_fact and next_fact["cut_like"] and not current["safe_positioning"]:
                    role = "approach"
                else:
                    role = "plunge"
            else:
                role = "approach" if next_fact and next_fact["cut_like"] else "transition-uncertain"
                if role != "transition-uncertain":
                    seen_cut = False
                    seen_exit = False
        elif current["direction"] == "stable" and current["cut_like"]:
            role = "cut"
            seen_cut = True
        episode.phase_role = role
        episode.direction = str(current["direction"])
        episode.start_z = current["start_z"]
        episode.end_z = current["end_z"]
        episode.motion_row_count = int(current["motion_row_count"])
        episode.transition_evidence = {
            "start_z": current["start_z"],
            "end_z": current["end_z"],
            "retract_plane": move_level,
            "starts_below_retract": bool(current["starts_below_retract"]),
            "reaches_retract_plane": bool(current["reaches_retract_plane"]),
            "next_episode_line": next_line,
            "next_role": "",
            "continued_upward": current["direction"] == "up",
        }
    for index, episode in enumerate(episodes):
        if index + 1 < len(episodes):
            episode.transition_evidence["next_role"] = episodes[index + 1].phase_role


def _feed_episode_signature(records: Sequence[tuple], start_index: int, end_index: int,
                            feature: str, at_retract: bool,
                            motion_trace: Sequence[tuple], z_vals: Sequence[Optional[float]],
                            phase_role: Optional[str] = None) -> str:
    axes = set()
    motions = set()
    z_values = []
    roles = Counter()
    retract_count = 0
    for rec in records[start_index:end_index]:
        (_line, raw, g00, has_xy, z_value, _lfv, _lfraw, rec_retract,
         has_motion, _mraw, _mval, tid, rec_feature, rec_motion) = rec
        code = code_part(raw)
        if has_xy:
            axes.update(("X", "Y"))
        if z_value is not None:
            axes.add("Z")
        effective_z = z_vals[tid] if 0 <= tid < len(z_vals) else z_value
        if effective_z is not None:
            z_values.append(effective_z)
        if has_motion and rec_motion:
            motions.add(rec_motion)
        if re.search(r"(?<![A-Z])G(?:8[0-9]|76)(?!\d)", code, re.I):
            motions.add("cycle")
        if rec_feature and has_motion:
            roles[rec_feature] += 1
        if rec_retract:
            retract_count += 1
    axis_label = "XYZ" if len(axes) == 3 else "XY" if axes == {"X", "Y"} else "Z" if axes == {"Z"} else "none"
    motion_label = ("cycle" if "cycle" in motions else
                    "arc" if "arc" in motions else
                    "rapid" if "rapid" in motions and "linear" not in motions else
                    "linear" if "linear" in motions else "unknown")
    directions = {1 if b > a else -1 for a, b in zip(z_values, z_values[1:])
                  if a is not None and b is not None and abs(b - a) > 1e-9}
    z_direction = ("mixed" if len(directions) > 1 else
                   "down" if directions == {-1} else
                   "up" if directions == {1} else "stable")
    dominant_role = phase_role or (roles.most_common(1)[0][0] if roles else feature)
    retract = int(retract_count * 2 >= max(1, len(records[start_index:end_index])))
    return "axes={}|motion={}|z={}|retract={}|role={}".format(
        axis_label, motion_label, z_direction, retract, dominant_role)

def validate_program(text: str, filename: str, program: str, info: ProgramInfo, config: Config,
                     stats: Optional[Stats] = None,
                     apt_meta: Optional[AptMeta] = None,
                     retract_plane: Optional[float] = None,
                     feed_outlier: Optional[FeedOutlierData] = None) -> List[Issue]:
    issues: List[Issue] = []
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    start = _header_end(lines)
    header_keys: Dict[str, Tuple[int, str]] = {}
    for i, line in enumerate(lines[:start]):
        parsed = _parse_msg(line)
        if parsed:
            key, value = parsed
            header_keys.setdefault(key.upper(), (i + 1, value))
    for key, _label, _required in FIELD_ORDER:
        if key in config.required_fields and (key not in header_keys or not header_keys[key][1].strip()):
            issues.append(Issue(filename, header_keys.get(key, (start + 1, ""))[0], "", "required-field", "error", f"填写 MSG {key}"))
    if header_keys.get("PROGRAM", (0, ""))[1].strip() not in ("", program):
        ln, raw = header_keys["PROGRAM"]
        issues.append(Issue(filename, ln, raw, "program-mismatch", "error", f"应为 {program}"))
    previous_n = None
    has_end = False
    has_s = False
    has_m03 = False
    has_m06 = False
    tool_numbers = set()
    header_tool_numbers = set()
    for key in header_keys:
        header_match = re.match(r"^T(\d+)$", key)
        if header_match:
            header_tool_numbers.add(int(header_match.group(1)))
    spindle_values: List[Tuple[int, str, float, str]] = []
    # WP-P1：F 离群阶段收集并入主遍历（原第二遍循环删除）。
    # WP-A9 修订：F 是模态指令——一个 F 档位生效期间的多行运动都使用该进给。
    # 档位归属按数控加工一般逻辑：无 XY 且 Z 下降且 F 未达移动档位下限（慢速下刀）
    # = 进刀档；Z ≥ 抬刀平面或快速接近（无 XY 且 F 达移动档位下限）= 移动档；
    # 其余（Z 不变的平面/曲面切削）= 切削档。显式 F 行不足 3 个时改用模态行分布。
    # 主遍历收集每行（模态 F 及其动作特征）与显式 F 行，遍历结束后先确定各
    # F 档位归属，再按模态 F 分组统计分布，离群只检查显式 F 行。
    row_feeds: List[Tuple[int, str, float, str, str, bool]] = []  # (行号, 模态F原文, 模态F值, 原始行, 动作特征, 是否在抬刀平面)
    explicit_feeds: List[Tuple[int, str, float, str, str, bool]] = []  # (行号, F原文, F值, 原始行, 动作特征, 是否在抬刀平面)
    # 主遍历先收集每行解析结果，动作特征在遍历结束后两遍判定（需全程序统计）。
    row_records: List[Tuple[int, str, int, bool, Optional[float], List[float], List[str], bool, bool, str, Optional[float], int]] = []
    modal_feed_raw = ""
    modal_feed_value: Optional[float] = None
    current_z: Optional[float] = None
    move_level = retract_plane if retract_plane is not None else config.retract_z_threshold
    first_cut: Optional[int] = None
    m03_pos: Optional[int] = None
    m04_pos: Optional[int] = None
    m05_pos: Optional[int] = None
    m08_pos: Optional[int] = None
    m09_pos: Optional[int] = None
    end_pos: Optional[int] = None
    # 预扫描：记录每行有效 Z（含模态继承），供动作特征按"相邻 Z 趋势"判定
    # （当前行及前后各 2 行，2026-08-07 用户建议）。Z 趋势比单行 z_delta 稳健：
    # 行内无显式 Z、或恰好上升 10 的浮点边界都不会再误判。
    z_trace: List[Tuple[int, Optional[float]]] = []
    _pre_z: Optional[float] = None
    for _i, _raw in enumerate(lines[start:], start=start + 1):
        if not _raw.strip():
            continue
        _zv: Optional[float] = None
        for _p in ADDR_RE.finditer(code_part(_raw)):
            if _p.group(1).upper() == "Z":
                try:
                    _zv = float(_p.group(2))
                except ValueError:
                    pass
                break
        if _zv is not None:
            _pre_z = _zv
        z_trace.append((_i, _pre_z))
    # 预扫描扩展：每行 ±4 行窗口的运动信号（方向变化次数/净变化/进入下行/进入上行）。
    # 动作特征完全由这些 Z 运动信号判定，不依赖绝对 F 数值（用户标注对齐 98.9%）。
    _z_vals = [z for _ln, z in z_trace]
    motion_trace: List[Tuple[int, Optional[float], Optional[float], Optional[float]]] = []
    for _ti in range(len(_z_vals)):
        _dirs = []
        for _k in range(-4, 4):
            _a = _z_vals[_ti + _k] if 0 <= _ti + _k < len(_z_vals) else None
            _b = _z_vals[_ti + _k + 1] if 0 <= _ti + _k + 1 < len(_z_vals) else None
            if _a is not None and _b is not None and abs(_b - _a) > 1e-9:
                _dirs.append(1 if _b > _a else -1)
        _changes = sum(1 for _i in range(1, len(_dirs)) if _dirs[_i] != _dirs[_i - 1])
        _win = _z_vals[max(0, _ti - 4): _ti + 5]
        _vals = [z for z in _win if z is not None]
        _net = (_vals[-1] - _vals[0]) if len(_vals) >= 2 else None
        _cur = _z_vals[_ti]
        _d_in = (_cur - _z_vals[_ti - 1]) if (_cur is not None and _ti >= 1 and _z_vals[_ti - 1] is not None) else None
        _d_out = (_z_vals[_ti + 1] - _cur) if (_cur is not None and _ti + 1 < len(_z_vals) and _z_vals[_ti + 1] is not None) else None
        motion_trace.append((_changes, _net, _d_in, _d_out))
    trace_idx = 0
    for i, raw_line in enumerate(lines[start:], start=start + 1):
        line = raw_line.strip()
        if not line:
            continue
        trace_idx += 1
        code = code_part(raw_line)
        # 只检查代码部分：括号注释与分号后注释内的引号不参与未闭合引号判定。
        if '"' in code and code.count('"') % 2:
            issues.append(Issue(filename, i, raw_line, "unclosed-quote", "error", "补全或删除未闭合引号"))
        if CONTROL_CHAR_RE.search(raw_line):
            issues.append(Issue(filename, i, raw_line, "control-character", "error", "删除异常控制字符"))
        upper_code = code.upper()
        if not has_end and (line.startswith("%") and END_LINE_RE.match(line) or "M" in upper_code and END_CODE_RE.search(code)):
            has_end = True
            if end_pos is None:
                end_pos = i
        nm = N_RE.match(raw_line)
        if nm:
            n = int(nm.group(1))
            if previous_n is not None and n <= previous_n:
                issues.append(Issue(filename, i, raw_line, "block-number", "warning", "N 号应递增且不重复"))
            previous_n = n
        g00_matches = sum(1 for _match in G00_RE.finditer(code)) if "G0" in upper_code else 0
        if stats is not None:
            stats.g00_count += g00_matches
        if g00_matches:
            level = config.g00_level.lower()
            if level != "allow":
                issues.append(Issue(filename, i, raw_line, "G00", level, "按设置移除 G00/G0 或改为警告"))
        parameters = list(ADDR_RE.finditer(code))
        z_value = None
        has_xy = False
        for parameter in parameters:
            parameter_key = parameter.group(1).upper()
            if parameter_key == "Z":
                try:
                    z_value = float(parameter.group(2))
                except ValueError:
                    pass
            elif parameter_key in "XY":
                has_xy = True
        if retract_plane is not None:
            move_level = retract_plane
        else:
            move_level = config.retract_z_threshold
        # 模态 Z：当前行无显式 Z 时沿用已生效的 Z 高度。
        effective_z = z_value if z_value is not None else current_z
        # 行是否处于抬刀平面（含模态 Z）：发生在抬刀高度上的一定是移动。
        at_retract = effective_z is not None and effective_z >= move_level
        line_feed_values = [
            float(parameter.group(2))
            for parameter in parameters if parameter.group(1).upper() == "F"
        ]
        line_feed_raws = [
            parameter.group(2)
            for parameter in parameters if parameter.group(1).upper() == "F"
        ]
        if z_value is not None:
            current_z = z_value
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
                    modal_feed_raw = raw_value
                    modal_feed_value = value
            else:
                spindle_values.append((i, raw_value, value, raw_line))
                has_s = True
                if config.spindle_min is not None and value < config.spindle_min:
                    issues.append(Issue(filename, i, raw_line, "spindle-range", "error", f"S 值 {raw_value} 低于下限 {config.spindle_min:g}"))
                if config.spindle_max is not None and value > config.spindle_max:
                    issues.append(Issue(filename, i, raw_line, "spindle-range", "error", f"S 值 {raw_value} 超过上限 {config.spindle_max:g}"))
            if value < 0:
                issues.append(Issue(filename, i, raw_line, "negative-parameter", "error", f"{key} 不得为负数"))
        if not has_m03 and "M" in upper_code and M03_RE.search(code):
            has_m03 = True
            if m03_pos is None:
                m03_pos = i
        if not has_m06 and "M" in upper_code and M06_RE.search(code):
            has_m06 = True
        if m04_pos is None and "M" in upper_code and M04_RE.search(code):
            m04_pos = i
        if first_cut is None and CUT_RE.search(code):
            first_cut = i
        if "M" in upper_code:
            if m05_pos is None and M05_RE.search(code):
                m05_pos = i
            if m08_pos is None and M08_RE.search(code):
                m08_pos = i
            if m09_pos is None and M09_RE.search(code):
                m09_pos = i
            if M03_RE.search(code) and M05_RE.search(code):
                issues.append(Issue(filename, i, raw_line, "mutually-exclusive-m", "error", "同一程序段同时包含 M03 与 M05，主轴正转与停止互斥"))
            if M03_RE.search(code) and M04_RE.search(code):
                issues.append(Issue(filename, i, raw_line, "mutually-exclusive-m", "error", "同一程序段同时包含 M03 与 M04，主轴正转与反转互斥"))
            if M08_RE.search(code) and M09_RE.search(code):
                issues.append(Issue(filename, i, raw_line, "mutually-exclusive-m", "error", "同一程序段同时包含 M08 与 M09，冷却开启与关闭互斥"))
        if "T" in upper_code:
            for tm in TOOL_CALL_RE.finditer(code):
                tool_numbers.add(int(tm.group(1)))
        if "G" in upper_code:
            motion_codes = {int(match.group(1)) for match in MOTION_RE.finditer(code)}
            if len(motion_codes) > 1:
                issues.append(Issue(filename, i, raw_line, "conflicting-motion", "error", "同一程序段只能使用一种运动 G 指令"))
        # Address tokens must have a numeric value unless special code is configured (not in MVP).
        for token in INVALID_ADDR_RE.finditer(code):
            issues.append(Issue(filename, i, raw_line, "invalid-address", "error", f"地址 {token.group(1)} 后缺少数值"))
        # 收集本行解析结果，动作特征在遍历结束后按"Z 运动 + 程序内相对 F 档"
        # 两遍判定（需要全程序统计，无法在线单遍完成）。
        row_records.append((
            i, raw_line, g00_matches, has_xy, z_value,
            line_feed_values, line_feed_raws, at_retract,
            bool(has_xy or z_value is not None or MOTION_ANY_RE.search(code)),
            modal_feed_raw, modal_feed_value, trace_idx - 1,
        ))
    # WP-A9 修订（2026-08-07）：动作特征判定——
    #   第一遍：按 Z 运动基础规则分类，收集程序内"进刀档"（下行 ≥5 次）与
    #   "移动档"（move ≥5 次）的 F 值集合（程序内相对，不写死数值）；
    #   第二遍：基础分类 + F 兜底（平稳行用进刀档→plunge、孤立设定行用移动档
    #   →move、退刀前最后一行用进刀档→plunge），构建 row_feeds/explicit_feeds。
    from collections import Counter as _Counter
    _down_f = _Counter()
    _stable_f = _Counter()
    _move_f = _Counter()
    classified_records = []
    for _rec in row_records:
        (_i, _raw, _g00, _hxy, _zv, _lfv, _lfraw, _atr, _hm, _mraw, _mval, _tid) = _rec
        if not _lfv:
            continue
        _ch, _net, _din, _dout = motion_trace[_tid]
        _prev1 = _z_vals[_tid - 1] if _tid >= 1 else None
        _base = _base_motion_feature(_ch, _net, _din, _dout, bool(_g00), _atr, _prev1, move_level)
        if _base == "plunge":
            _down_f[_lfv[-1]] += 1
        elif _base == "cut":
            _stable_f[_lfv[-1]] += 1
        elif _base == "move":
            _move_f[_lfv[-1]] += 1
    _plunge_feeds = {f for f, n in _down_f.items() if n >= 5}
    _move_feeds = {f for f, n in _move_f.items() if n >= 5}
    _modal_motion = "unknown"
    for _rec in row_records:
        (_i, _raw, _g00, _hxy, _zv, _lfv, _lfraw, _atr, _hm, _mraw, _mval, _tid) = _rec
        _motion_matches = list(MOTION_RE.finditer(code_part(_raw)))
        if _motion_matches:
            _motion_code = int(_motion_matches[-1].group(1))
            _modal_motion = ("rapid" if _motion_code == 0 else
                             "linear" if _motion_code == 1 else "arc")
        _ch, _net, _din, _dout = motion_trace[_tid]
        _prev1 = _z_vals[_tid - 1] if _tid >= 1 else None
        _base = _base_motion_feature(_ch, _net, _din, _dout, bool(_g00), _atr, _prev1, move_level)
        _feat = _final_motion_feature(_base, _lfv[-1] if _lfv else None,
                                      _plunge_feeds, _move_feeds, _net, _din, _hm)
        classified_records.append(_rec + (_feat, _modal_motion))
        if _lfv:
            explicit_feeds.append((_i, _lfraw[-1], _lfv[-1], _raw, _feat, _atr))
        if _mval is not None and _hm:
            row_feeds.append((_i, _mraw or "", _mval, _raw, _feat, _atr))
    if config.require_end_marker and not has_end:
        issues.append(Issue(filename, len(lines), "", "end-marker", "error", "添加 %、M30 或 M02 结束标记"))
    if config.require_spindle_speed and not has_s:
        issues.append(Issue(filename, start + 1, "", "spindle-speed", "error", "切削前应有 S 转速"))
    if not has_m03:
        if config.auto_m03:
            # WP-A1：正文以 M04 反转启动时禁止补写 M03（正反转冲突），按错误阻止输出；
            # 否则仍按 FR-05.6 报告补写失败。
            if m04_pos is not None:
                issues.append(Issue(filename, m04_pos, lines[m04_pos - 1], "spindle-direction", "error",
                                    "正文以 M04 反转启动主轴，已禁止自动补写 M03，请人工确认旋转方向与主轴指令"))
            else:
                issues.append(Issue(filename, start + 1, "", "spindle-start", "error", "自动补写 M03 失败：正文缺少可插入 M03 的指令位置，请手动补写 M03"))
        else:
            issues.append(Issue(filename, start + 1, "", "spindle-start", "warning", "正文中未找到 M03"))
    if config.require_m06 and tool_numbers and not has_m06:
        issues.append(Issue(filename, start + 1, "", "tool-change", "error", "存在刀具调用但缺少 M06"))
    # WP-A2：启用自动添加换刀时，多刀程序无法自动生成首刀换刀指令，按警告提示人工确认。
    configured_tools = getattr(info, "tools", None) or []
    if config.auto_tool_change and (len(tool_numbers) > 1 or len(configured_tools) > 1):
        issues.append(Issue(filename, start + 1, "", "auto-tool-change-skipped", "warning",
                            "程序引用多把刀具，自动添加换刀指令已对该程序禁用并跳过，请人工确认换刀逻辑"))
    for number in sorted(tool_numbers - header_tool_numbers):
        issues.append(Issue(filename, start + 1, "", "tool-number-missing", "warning", f"正文调用 T{number} 但头部没有对应的 T{number} MSG 刀具信息，请确认"))
    # 辅助指令顺序规则（仅当相关指令都出现且顺序错误时报告）。
    aux = set(config.aux_checks)
    if aux:
        if "m03-before-motion" in aux and first_cut is not None and m03_pos is not None and m03_pos > first_cut:
            issues.append(Issue(filename, m03_pos, lines[m03_pos - 1], "aux-order", "error", "M03 出现在首次切削运动之后，应在切削前启动主轴"))
        if "m05-before-end" in aux and m05_pos is not None and end_pos is not None and end_pos < m05_pos:
            issues.append(Issue(filename, m05_pos, lines[m05_pos - 1], "aux-order", "warning", "M05 出现在程序结束指令之后，主轴停止指令无效"))
        if "m08-before-cut" in aux and m08_pos is not None and first_cut is not None and first_cut < m08_pos:
            issues.append(Issue(filename, m08_pos, lines[m08_pos - 1], "aux-order", "warning", "M08 出现在首次切削之后，首刀无冷却"))
        if "m09-before-end" in aux and m09_pos is not None and end_pos is not None and end_pos < m09_pos:
            issues.append(Issue(filename, m09_pos, lines[m09_pos - 1], "aux-order", "warning", "M09 出现在程序结束指令之后，冷却液未及时关闭"))
    # F 离群当前仅使用显式 episode 与结构 peer group 的相对参照。
    # 不建立固定合法 F 表；APT FEDRAT 只保留为辅助上下文，硬边界独立处理。
    stage_labels = {"move": "移动/退刀", "plunge": "进刀", "cut": "切削"}
    # 每个显式 F 只计一个 episode；模态继承行只补充结构，不增加样本权重。
    apt_feeds = [float(feed) for feed, _units in apt_meta.feeds] if apt_meta and apt_meta.feeds else []

    def matches_apt_feed(value: float) -> bool:
        return any(abs(value - feed) <= max(feed * 0.10, 1.0) for feed in apt_feeds)

    def report_feed_outlier(line_no, raw_value, raw_line, suggestion, reason,
                            peer_group="", sample_count=0, confidence="medium",
                            evidence=None):
        issues.append(Issue(filename, line_no, raw_line, "feed-outlier", "warning", suggestion))
        emit_event("warning", "feed_outlier",
                   f"F 离群识别：{filename} 第 {line_no} 行 F{raw_value}",
                   detail=f"{raw_line.strip()}\n{suggestion}")
        if feed_outlier is not None:
            feed_outlier.outliers.append({
                "line": line_no, "value": float(raw_value), "reason": reason,
                "in_apt": matches_apt_feed(float(raw_value)), "text": raw_line,
                "peer_group": peer_group, "sample_count": sample_count,
                "confidence": confidence, "evidence": evidence or {},
            })

    # Event-level structural comparison.  Each explicit F starts one episode;
    # inherited modal rows enrich its structure but never increase its weight.
    record_indexes = {rec[0]: index for index, rec in enumerate(classified_records)}
    episodes: List[FeedEpisode] = []
    episode_spans: List[tuple] = []
    for index, (line_no, raw_value, value, raw_line, feature, at_retract) in enumerate(explicit_feeds):
        start_index = record_indexes.get(line_no)
        if start_index is None:
            continue
        if index + 1 < len(explicit_feeds):
            next_line = explicit_feeds[index + 1][0]
            end_index = record_indexes.get(next_line, len(classified_records))
        else:
            end_index = len(classified_records)
        signature = _feed_episode_signature(
            classified_records, start_index, end_index, feature, at_retract,
            motion_trace, _z_vals)
        episodes.append(FeedEpisode(
            line_no, raw_value, value, raw_line, signature, feature, at_retract))
        episode_spans.append((start_index, end_index))

    _annotate_feed_episode_phases(
        episodes, episode_spans, classified_records, motion_trace, _z_vals, move_level)
    for episode, span in zip(episodes, episode_spans):
        episode.signature = _feed_episode_signature(
            classified_records, span[0], span[1], episode.feature, episode.at_retract,
            motion_trace, _z_vals, phase_role=episode.phase_role)
    if feed_outlier is not None:
        feed_outlier.episodes = [
            {
                "line": episode.line,
                "value": episode.value,
                "raw_value": episode.raw_value,
                "text": episode.raw_line,
                "signature": episode.signature,
                "feature": episode.feature,
                "phase_role": episode.phase_role,
                "direction": episode.direction,
                "start_z": episode.start_z,
                "end_z": episode.end_z,
                "motion_row_count": episode.motion_row_count,
                "transition_evidence": dict(episode.transition_evidence),
            }
            for episode in episodes
        ]

    episode_groups: Dict[str, List[FeedEpisode]] = {}
    for episode in episodes:
        episode_groups.setdefault(episode.signature, []).append(episode)
    def coarse_signature(signature: str) -> str:
        parts = [part for part in signature.split("|")
                 if not part.startswith(("z=", "retract=", "shape=", "zscale="))]
        return "|".join(parts)

    def compatible_signature(signature: str) -> str:
        """Return a structure-only parent key for compatible phase variants."""
        fields = {}
        for part in signature.split("|"):
            if "=" in part:
                key, value = part.split("=", 1)
                fields[key] = value
        role = fields.get("role", "")
        if role in ("plunge", "approach", "retreat-near", "retreat-clear"):
            fields["axes"] = "transition"
        return "|".join("{}={}".format(key, fields[key]) for key in
                        ("axes", "motion", "z", "retract", "role") if key in fields)

    compatible_groups: Dict[str, List[FeedEpisode]] = {}
    for episode in episodes:
        compatible_groups.setdefault(compatible_signature(episode.signature), []).append(episode)

    coarse_counts: Dict[str, Counter] = {}
    global_episode_counts = Counter()
    for episode in episodes:
        if episode.value > 0:
            global_episode_counts[episode.value] += 1
            coarse_counts.setdefault(coarse_signature(episode.signature), Counter())[episode.value] += 1

    min_count = max(1, config.feed_outlier_min_count)
    ratio = max(1.0, config.feed_outlier_ratio)
    all_common = set()
    stage_common: Dict[str, set] = {}
    phase_common: Dict[str, set] = {}
    legacy_stage_counts: Dict[str, Counter] = {}
    for episode in episodes:
        legacy_stage_counts.setdefault(episode.feature, Counter())[episode.value] += 1
    for stage, counts in legacy_stage_counts.items():
        stage_common[stage] = {
            value for value, count in counts.items() if count >= min_count
        }
    peer_group_data: Dict[str, dict] = {}
    for signature, group in episode_groups.items():
        counts = Counter(episode.value for episode in group if episode.value > 0)
        common_values = sorted(value for value, count in counts.items() if count >= min_count)
        mode_stable = len(common_values) == 1
        all_common.update(common_values)
        for episode in group:
            if episode.value in common_values:
                phase_common.setdefault(episode.phase_role, set()).add(episode.value)
        peer_group_data[signature] = {
            "sample_count": len(group),
            "feed_counts": {str(value): count for value, count in sorted(counts.items())},
            "common_feeds": common_values,
            "mode_stable": mode_stable,
            "lines": [episode.line for episode in group],
            "phase_roles": sorted({episode.phase_role for episode in group}),
            "compatible_group": compatible_signature(signature),
        }

    compatible_peer_group_data: Dict[str, dict] = {}
    for signature, group in compatible_groups.items():
        counts = Counter(episode.value for episode in group if episode.value > 0)
        common_values = sorted(value for value, count in counts.items() if count >= min_count)
        compatible_peer_group_data[signature] = {
            "sample_count": len(group),
            "feed_counts": {str(value): count for value, count in sorted(counts.items())},
            "common_feeds": common_values,
            "mode_stable": len(common_values) == 1,
            "lines": [episode.line for episode in group],
        }

    common_feeds = sorted(all_common)
    stage_common_feeds = {
        stage: sorted(values) for stage, values in sorted(stage_common.items())
    }
    if feed_outlier is not None:
        feed_outlier.apt_feeds = apt_feeds
        feed_outlier.min_count = min_count
        feed_outlier.ratio = ratio
        feed_outlier.low_ratio = config.feed_outlier_low_ratio
        feed_outlier.high_ratio = config.feed_outlier_high_ratio
        feed_outlier.common_feeds = common_feeds
        feed_outlier.stage_common_feeds = stage_common_feeds
        feed_outlier.phase_common_feeds = {
            phase: sorted(values) for phase, values in sorted(phase_common.items())
        }
        feed_outlier.peer_groups = peer_group_data
        feed_outlier.compatible_peer_groups = compatible_peer_group_data
        if common_feeds:
            feed_outlier.envelope = [
                common_feeds[0] * config.feed_outlier_low_ratio,
                common_feeds[-1] * config.feed_outlier_high_ratio,
            ]

    coverage = {
        "total_episodes": len(episodes),
        "compared_episodes": 0,
        "uncompared_episodes": 0,
        "insufficient_groups": 0,
    }

    def add_insufficient_summary(signature: str, group: Sequence[FeedEpisode], reason: str,
                                 counts: Counter) -> None:
        if feed_outlier is None:
            return
        representative = [
            {
                "line": episode.line,
                "value": episode.value,
                "in_apt": matches_apt_feed(episode.value),
                "text": episode.raw_line,
            }
            for episode in group[:5]
        ]
        feed_outlier.insufficient_evidence.append({
            "peer_group": signature,
            "sample_count": len(group),
            "reason": reason,
            "episode_lines": [episode.line for episode in group],
            "feed_counts": {str(value): count for value, count in sorted(counts.items())},
            "representative_lines": representative,
            "in_apt_values": sorted({
                episode.value for episode in group if matches_apt_feed(episode.value)
            }),
        })

    for signature, group in episode_groups.items():
        counts = Counter(episode.value for episode in group if episode.value > 0)
        common_values = sorted(value for value, count in counts.items() if count >= min_count)
        mode_stable = len(common_values) == 1
        if len(group) < 3 or not common_values:
            parent_signature = compatible_signature(signature)
            parent_group = compatible_groups.get(parent_signature, [])
            parent_counts = Counter(episode.value for episode in parent_group if episode.value > 0)
            parent_common = sorted(value for value, count in parent_counts.items()
                                   if count >= min_count)
            if len(parent_group) >= 3 and len(parent_common) == 1:
                reference = parent_common[0]
                coverage["compared_episodes"] += len(group)
                for episode in group:
                    value = episode.value
                    if value <= 0 or parent_counts.get(value, 0) >= min_count:
                        continue
                    if global_episode_counts.get(value, 0) >= 2:
                        continue
                    if coarse_counts.get(coarse_signature(signature), Counter()).get(value, 0) >= min_count:
                        continue
                    relative_ratio = max(value / reference, reference / value)
                    outside_reference_tolerance = (
                        value < reference * config.feed_outlier_low_ratio or
                        value > reference * config.feed_outlier_high_ratio)
                    if relative_ratio <= ratio and not outside_reference_tolerance:
                        continue
                    direction = "低于" if value < reference else "高于"
                    report_feed_outlier(
                        episode.line, episode.raw_value, episode.raw_line,
                        "F{} 在兼容结构组中明显{}重复参照 F{}（{} 次，倍率 {:.3g}），请确认".format(
                            episode.raw_value, direction, reference,
                            parent_counts[reference], relative_ratio),
                        "compatible-peer-outlier", parent_signature, len(parent_group),
                        "medium", {
                            "reference_feed": reference,
                            "reference_count": parent_counts[reference],
                            "candidate_count": parent_counts.get(value, 0),
                            "relative_ratio": relative_ratio,
                            "compatible_group": parent_signature,
                            "in_apt": matches_apt_feed(value),
                        })
                continue
            coverage["uncompared_episodes"] += len(group)
            continue
        if not mode_stable:
            coverage["uncompared_episodes"] += len(group)
            rare_candidates = []
            for value, count in counts.items():
                if count >= min_count or global_episode_counts.get(value, 0) >= 2:
                    continue
                if coarse_counts.get(coarse_signature(signature), Counter()).get(value, 0) >= min_count:
                    continue
                reference = min(
                    common_values, key=lambda item: abs(log(item) - log(value)))
                relative_ratio = max(value / reference, reference / value)
                outside_reference_tolerance = (
                    value < reference * config.feed_outlier_low_ratio or
                    value > reference * config.feed_outlier_high_ratio)
                if relative_ratio > ratio or outside_reference_tolerance:
                    rare_candidates.append(value)
            if rare_candidates:
                coverage["insufficient_groups"] += 1
                add_insufficient_summary(
                    signature, group, "unstable-peer-mode", counts)
            continue
        coverage["compared_episodes"] += len(group)
        for episode in group:
            value = episode.value
            # Upper hard boundaries have already produced feed-range and must
            # not be duplicated as statistical warnings.
            if value <= 0 or (config.feed_max is not None and value > config.feed_max):
                if feed_outlier is not None:
                    feed_outlier.boundary_errors.append({
                        "line": episode.line, "value": value,
                        "reason": "out-of-range", "in_apt": matches_apt_feed(value),
                        "text": episode.raw_line,
                    })
                continue
            if counts.get(value, 0) >= min_count:
                continue
            if global_episode_counts.get(value, 0) >= 2:
                continue
            # A value repeatedly used by a related structural family is a
            # valid in-program reference even when this narrow episode shape
            # occurs only once (for example, the same cut feed at another Z
            # level).  This prevents normal modal transitions from becoming
            # outliers merely because the local shape is more specific.
            if coarse_counts.get(coarse_signature(signature), Counter()).get(value, 0) >= min_count:
                continue
            reference = min(common_values, key=lambda item: abs(log(item) - log(value)))
            relative_ratio = max(value / reference, reference / value)
            far = relative_ratio > ratio
            outside_reference_tolerance = (
                value < reference * config.feed_outlier_low_ratio or
                value > reference * config.feed_outlier_high_ratio)
            if not far and (not mode_stable or not outside_reference_tolerance):
                continue
            confidence = "high" if counts[reference] >= 4 and counts.get(value, 0) == 1 and relative_ratio >= ratio else "medium"
            direction = "低于" if value < reference else "高于"
            evidence = {
                "reference_feed": reference,
                "reference_count": counts[reference],
                "candidate_count": counts.get(value, 0),
                "relative_ratio": relative_ratio,
                "log_distance": abs(log(value) - log(reference)),
                "in_apt": matches_apt_feed(value),
            }
            report_feed_outlier(
                episode.line, episode.raw_value, episode.raw_line,
                "F{} 在同结构动作中明显{}重复参照 F{}（{} 次，倍率 {:.3g}），请确认".format(
                    episode.raw_value, direction, reference, counts[reference], relative_ratio),
                "episode-peer-outlier", signature, len(group), confidence, evidence)

    if feed_outlier is not None:
        feed_outlier.coverage = coverage

    # Cross-role review is also learned from this program.  It is deliberately
    # weaker than peer-group detection and never relies on fixed F numbers.
    cut_references = stage_common_feeds.get("cut", [])
    if cut_references:
        for episode in episodes:
            if episode.feature != "move" or episode.value <= 0:
                continue
            line_code = code_part(episode.raw_line)
            confirmed_move = episode.at_retract or bool(G00_RE.search(line_code))
            if not confirmed_move or matches_apt_feed(episode.value):
                continue
            reference = min(cut_references,
                            key=lambda item: abs(log(item) - log(episode.value)))
            relative_ratio = max(episode.value / reference, reference / episode.value)
            if episode.value >= reference or relative_ratio <= ratio:
                continue
            issues.append(Issue(
                filename, episode.line, episode.raw_line,
                "feed-context-review", "info",
                "移动/定位动作使用的 F{} 明显低于程序内重复切削参照 F{}，请人工复核".format(
                    episode.raw_value, reference)))
            if feed_outlier is not None:
                feed_outlier.context_reviews.append({
                    "line": episode.line, "value": episode.value,
                    "reason": "move-below-cut-reference",
                    "reference_feed": reference,
                    "relative_ratio": relative_ratio,
                    "in_apt": matches_apt_feed(episode.value),
                    "text": episode.raw_line,
                })

    # Keep spindle validation in the active path; the legacy F block below is
    # intentionally bypassed while report readers remain backward compatible.
    if config.multiple_spindle_warn:
        distinct_spindle_now: Dict[float, Tuple[int, str, str]] = {}
        for line_no, raw_value, value, raw_line in spindle_values:
            distinct_spindle_now.setdefault(value, (line_no, raw_value, raw_line))
        if len(distinct_spindle_now) > 1:
            values = ", ".join(raw for _line, raw, _text in distinct_spindle_now.values())
            first = next(iter(distinct_spindle_now.values()))
            issues.append(Issue(filename, first[0], first[2], "multiple-spindle-speeds", "warning",
                                f"程序包含多个不同 S 值（{values}），请确认转速切换是否符合工艺要求"))
    return issues

    # Unreachable legacy F-role detector below is retained only as historical
    # source context; the return above is the sole active path.
    feed_counts = Counter(value for _l, _r, value, _t, _f, _a in row_feeds)
    stage_feed_counts: Dict[str, Counter] = {}
    for _l, _r, value, _t, _f, _a in row_feeds:
        stage_feed_counts.setdefault(_f, Counter())[value] += 1
    # 非标准档位值的"少见"按显式 F 出现次数统计：模态继承只放大合法档位频率，
    # 不能把一次可疑写入（如注入 F450 后被后续长切削段继承）洗成常见值。
    stage_explicit_counts: Dict[str, Counter] = {}
    for _l, _r, value, _t, _f, _a in explicit_feeds:
        stage_explicit_counts.setdefault(_f, Counter())[value] += 1
    total_f = sum(feed_counts.values())
    min_count = max(config.feed_outlier_min_count, int(total_f * 0.005)) if total_f else config.feed_outlier_min_count
    stage_common_feeds: Dict[str, List[float]] = {}
    for _stage, _counts in stage_feed_counts.items():
        _total = sum(_counts.values())
        _min_count = max(config.feed_outlier_min_count, int(_total * 0.005)) if _total else config.feed_outlier_min_count
        stage_common_feeds[_stage] = sorted(
            value for value, count in _counts.items() if count >= _min_count
        )
    common_feeds = sorted({value for values in stage_common_feeds.values() for value in values})
    ratio = config.feed_outlier_ratio
    if feed_outlier is not None:
        feed_outlier.apt_feeds = apt_feeds
        feed_outlier.min_count = min_count
        feed_outlier.ratio = ratio
        feed_outlier.common_feeds = common_feeds
        feed_outlier.stage_common_feeds = stage_common_feeds
        if common_feeds:
            feed_outlier.envelope = [
                common_feeds[0] * config.feed_outlier_low_ratio,
                common_feeds[-1] * config.feed_outlier_high_ratio,
            ]
    # 上下文角色复核只针对该动作特征中少见的 F 值（≤2 次），避免常见档位
    # 在移动/切削间合法复用（如 B1001 F300 大量用于移动）时刷屏提示。
    context_counts = Counter((_f, value) for _l, _r, value, _t, _f, _a in row_feeds)

    for line_no, raw_value, value, raw_line, feature, at_retract in explicit_feeds:
        # 上下文复核只针对真正带运动（X/Y/Z 或运动 G 码）的行；孤立 F/S 设定行
        # （如 N14F6000）不产生"切削用抬刀大档"类复核。
        line_code = code_part(raw_line)
        has_motion = any(parameter.group(1).upper() in "XYZ" for parameter in
                         ADDR_RE.finditer(line_code)) or bool(MOTION_ANY_RE.search(line_code))
        # 确认的移动行：行在抬刀平面（含模态 Z）或 G00 快速定位；层四"移动用
        # 下刀小档"复核只针对这些确认移动的行，Z 上升/快速下刀/XY 高速平移等
        # 启发式移动不产生复核（可能是工件内合法的慢速退刀/定位）。
        confirmed_move = at_retract or bool(G00_RE.search(line_code))
        role_common_feeds = stage_common_feeds.get(feature, [])
        common_set = set(role_common_feeds)
        role_explicit_count = stage_explicit_counts.get(feature, Counter()).get(value, 0)
        if not common_set:
            # 程序 F 太少，无统计意义：跳过第二层，只保留硬边界与上下文复核。
            apt_planned = apt_feeds and matches_apt_feed(value)
            rare_overall = role_explicit_count <= 2
            if (has_motion and feature == "cut" and not at_retract
                    and any(abs(value - g) < 1.0 for g in FEED_HIGH_GEARS)
                    and not apt_planned and rare_overall):
                issues.append(Issue(filename, line_no, raw_line, "feed-context-review", "info",
                                    f"切削运动中使用抬刀/定位大档 F{raw_value}，请复核是否误输"))
                if feed_outlier is not None:
                    feed_outlier.context_reviews.append({
                        "line": line_no, "value": value, "reason": "cut-high-gear",
                        "in_apt": matches_apt_feed(value),
                        "text": raw_line,
                    })
            elif (has_motion and feature == "move" and confirmed_move
                    and any(abs(value - g) < 1.0 for g in FEED_LOW_GEARS)
                    and not apt_planned and rare_overall):
                issues.append(Issue(filename, line_no, raw_line, "feed-context-review", "info",
                                    f"快速移动/定位中使用钻入/下刀小档 F{raw_value}，请复核是否误输"))
                if feed_outlier is not None:
                    feed_outlier.context_reviews.append({
                        "line": line_no, "value": value, "reason": "move-low-gear",
                        "in_apt": matches_apt_feed(value),
                        "text": raw_line,
                    })
            continue
        if value <= 0 or (config.feed_max is not None and value > config.feed_max):
            if feed_outlier is not None:
                feed_outlier.boundary_errors.append({
                    "line": line_no, "value": value, "reason": "out-of-range",
                    "in_apt": matches_apt_feed(value),
                    "text": raw_line,
                })
            continue
        rare = role_explicit_count <= 2
        if rare:
            reference_feeds = [gear for gear in role_common_feeds if gear != value]
            if not reference_feeds:
                continue
            reference_set = set(reference_feeds)
            nearest = min(reference_set, key=lambda v: abs(log(v) - log(value)))
            far = value > nearest * ratio or value < nearest / ratio
            ref_lo = reference_feeds[0] * config.feed_outlier_low_ratio
            ref_hi = reference_feeds[-1] * config.feed_outlier_high_ratio
            outside_reference_range = value < reference_feeds[0] or value > reference_feeds[-1]
            outer_tolerated = (
                outside_reference_range and not far and (
                    (value > reference_feeds[-1] and config.feed_outlier_high_ratio >= 1.2)
                    or (value < reference_feeds[0] and config.feed_outlier_low_ratio <= 0.8)
                )
            )
            envelope_out = ((value < ref_lo or value > ref_hi) and not outer_tolerated)
            if far:
                if value < nearest:
                    hint = "，且不在 APT 规划进给集合内" if apt_feeds and not matches_apt_feed(value) else ""
                    report_feed_outlier(line_no, raw_value, raw_line,
                                        f"F{raw_value} 为少见档位且明显低于常用档位（最近 {nearest:g}）{hint}，请确认",
                                        "rare-below-common")
                else:
                    hint = "，且不在 APT 规划进给集合内" if apt_feeds and not matches_apt_feed(value) else ""
                    report_feed_outlier(line_no, raw_value, raw_line,
                                        f"F{raw_value} 为少见档位且明显高于常用档位（最近 {nearest:g}）{hint}，请确认",
                                        "rare-above-common")
                continue
            if envelope_out:
                env_hint = ""
                if value < ref_lo:
                    env_hint = f"低于档位包络下限 {ref_lo:g}"
                else:
                    env_hint = f"高于档位包络上限 {ref_hi:g}"
                hint = "，且不在 APT 规划进给集合内" if apt_feeds and not matches_apt_feed(value) else ""
                report_feed_outlier(line_no, raw_value, raw_line,
                                    f"F{raw_value} {env_hint}（当前{stage_labels.get(feature, feature)}角色常用档位 {role_common_feeds[0]:g}~{role_common_feeds[-1]:g}）{hint}，请确认",
                                    "envelope-out")
                continue
            near_common = any(
                max(value / gear, gear / value) <= 1.2
                for gear in reference_set if value > 0 and gear > 0
            )
            near_known_gear = any(
                abs(log(value) - log(gear)) <= log(1.1)
                for gear in KNOWN_FEED_GEARS if value > 0 and gear > 0
            )
            # 若该值因模态继承行数很多而进入 common_set，但显式写入本身很少，
            # 仍按显式事件检查，避免一次 F450 被后续长段继承后洗成常用档位。
            modal_promoted = value in common_set
            if (not near_common and not near_known_gear
                    and (not outside_reference_range or modal_promoted)):
                hint = "，且不在 APT 规划进给集合内" if apt_feeds and not matches_apt_feed(value) else ""
                report_feed_outlier(
                    line_no, raw_value, raw_line,
                    f"F{raw_value} 为当前{stage_labels.get(feature, feature)}角色非标准档位值（最近 {nearest:g}）{hint}，请确认",
                    "non-role-gear-value")
                continue
        # ③ 上下文角色复核（不阻止输出，仅提示人工确认）。
        # APT 已规划该档位（权威白名单）或该值全程序常见（显式 >2 次）时，
        # 不属于"突然出现"，不产生复核；复核明细带 in_apt 供界面一致展示。
        apt_planned = bool(apt_feeds) and matches_apt_feed(value)
        rare_overall = role_explicit_count <= 2
        if (has_motion and feature == "cut" and not at_retract
                and any(abs(value - g) < 1.0 for g in FEED_HIGH_GEARS)
                and context_counts.get((feature, value), 0) <= 2
                and not apt_planned and rare_overall):
            issues.append(Issue(filename, line_no, raw_line, "feed-context-review", "info",
                                f"切削运动中使用抬刀/定位大档 F{raw_value}，请复核是否误输"))
            if feed_outlier is not None:
                feed_outlier.context_reviews.append({
                    "line": line_no, "value": value, "reason": "cut-high-gear",
                    "in_apt": matches_apt_feed(value),
                    "text": raw_line,
                })
        elif (has_motion and feature == "move" and confirmed_move
                and any(abs(value - g) < 1.0 for g in FEED_LOW_GEARS)
                and context_counts.get((feature, value), 0) <= 2
                and not apt_planned and rare_overall):
            issues.append(Issue(filename, line_no, raw_line, "feed-context-review", "info",
                                f"快速移动/定位中使用钻入/下刀小档 F{raw_value}，请复核是否误输"))
            if feed_outlier is not None:
                feed_outlier.context_reviews.append({
                    "line": line_no, "value": value, "reason": "move-low-gear",
                    "in_apt": matches_apt_feed(value),
                    "text": raw_line,
                })
    distinct_spindle: Dict[float, Tuple[int, str, str]] = {}
    if config.multiple_spindle_warn:
        for line_no, raw_value, value, raw_line in spindle_values:
            distinct_spindle.setdefault(value, (line_no, raw_value, raw_line))
        if len(distinct_spindle) > 1:
            values = ", ".join(raw for _line, raw, _text in distinct_spindle.values())
            first = next(iter(distinct_spindle.values()))
            issues.append(Issue(filename, first[0], first[2], "multiple-spindle-speeds", "warning", f"程序包含多个不同 S 值（{values}），请确认转速切换是否符合工艺要求"))
    return issues


def analyze_program(text: str, filename: str, program: str, info: ProgramInfo, config: Config,
                    apt_meta: Optional[AptMeta] = None,
                    retract_plane: Optional[float] = None) -> Tuple[Stats, List[Issue], FeedOutlierData]:
    """Calculate statistics and validation issues in one body traversal."""
    stats = _new_stats()
    feed_outlier = FeedOutlierData()
    issues = validate_program(text, filename, program, info, config, stats=stats,
                              apt_meta=apt_meta, retract_plane=retract_plane,
                              feed_outlier=feed_outlier)
    return stats, issues, feed_outlier


def align_lines(left: str, right: str) -> List[Tuple[str, str, str, str]]:
    """Align two program texts line by line for a side-by-side comparison.

    Each returned row is ``(left_text, left_tag, right_text, right_tag)``.
    Tags are ``removed`` (line exists only on the left), ``added`` (only on
    the right) or ``changed`` (paired differing lines); equal lines carry an
    empty tag.
    """
    left_lines = left.replace("\r\n", "\n").replace("\r", "\n").splitlines()
    right_lines = right.replace("\r\n", "\n").replace("\r", "\n").splitlines()
    matcher = difflib.SequenceMatcher(a=left_lines, b=right_lines, autojunk=False)
    rows: List[Tuple[str, str, str, str]] = []
    for opcode, a1, a2, b1, b2 in matcher.get_opcodes():
        if opcode == "equal":
            for offset in range(a2 - a1):
                rows.append((left_lines[a1 + offset], "", right_lines[b1 + offset], ""))
        elif opcode == "delete":
            for offset in range(a2 - a1):
                rows.append((left_lines[a1 + offset], "removed", "", ""))
        elif opcode == "insert":
            for offset in range(b2 - b1):
                rows.append(("", "", right_lines[b1 + offset], "added"))
        else:
            length = max(a2 - a1, b2 - b1)
            for offset in range(length):
                left_text = left_lines[a1 + offset] if a1 + offset < a2 else ""
                right_text = right_lines[b1 + offset] if b1 + offset < b2 else ""
                rows.append((left_text, "changed", right_text, "changed"))
    return rows


def reprocess_file(f: FilePlan, info: ProgramInfo, config: Config, *, tools: Sequence[ToolInfo] = ()) -> None:
    """Re-run header/tool/M03/analysis for one MPF plan in memory.

    Used by the GUI to re-review a program after the operator manually edits
    its source code.  Only the in-memory plan is updated; nothing is written
    to disk until process_plan runs.
    """
    if f.kind != "mpf" or not f.program or f.original_text is None:
        return
    effective = program_defaults(f.original_text, info)
    # WP-A4：有 APT 时 DATE 优先采用 APT 生成时间。
    apt_date = ""
    if f.apt_meta and f.apt_meta.generated_at:
        apt_time = parse_apt_generated(f.apt_meta.generated_at)
        if apt_time is not None:
            apt_date = format_nc_date(apt_time)
    if apt_date:
        effective.date = apt_date
    if tools:
        effective.tools = list(tools)
    elif f.parsed_tools:
        effective.tools = list(f.parsed_tools)
    else:
        effective.tools = extract_tools(f.original_text)
    f.output_text, f.changes, header_issues = apply_header(f.original_text, f.program, effective, config, replace_tools=True, filename=f.source)
    f.output_text, tool_changed, tool_note = add_initial_tool_change(f.output_text, effective.tools, config)
    if tool_note:
        f.changes.append(tool_note)
        if not tool_changed:
            f.auto_tool_change_skipped = tool_note
    f.output_text, m03_changed, m03_note = add_m03(f.output_text, config)
    if m03_changed:
        f.changes.append(m03_note)
    # WP-B2/A4：程序发生实际变更时 DATE 更新——有 APT 用 APT 生成时间，无 APT 用变更发生时间。
    if f.changes:
        f.output_text, date_changed = update_header_date(f.output_text, apt_date or format_nc_date())
        if date_changed:
            f.changes.append("更新 DATE")
    f.stats, validation_issues, f.feed_outlier = analyze_program(
        f.output_text, f.source, f.program, effective, config, apt_meta=f.apt_meta,
        retract_plane=f.apt_toolpath.retract_plane if f.apt_toolpath else None)
    f.issues = header_issues + validation_issues
    # WP-A4：APT 规划 ↔ MPF 执行交叉校验。
    if f.apt_meta is not None:
        f.issues.extend(crosscheck_apt(f.output_text, f.apt_meta, f.source, config, apt_tools=effective.tools))


def _parallel_apply(items, function, workers: int):
    """Apply a function with lightweight worker threads.

    concurrent.futures imports its process-pool implementation as part of the
    package, which makes PyInstaller bundle multiprocessing and XML support
    even though this application only uses threads.  This small worker loop
    provides the required bounded parallelism without those unused runtimes.
    """
    iterator = iter(items)
    lock = threading.Lock()

    def run():
        while True:
            with lock:
                try:
                    item = next(iterator)
                except StopIteration:
                    return
            function(item)

    threads = [threading.Thread(target=run, name=f"nc-mpf-{index + 1}") for index in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()


def build_plan(scan: ScanResult, info: Optional[ProgramInfo] = None, config: Optional[Config] = None, tool_overrides: Optional[Dict[str, List[ToolInfo]]] = None) -> ScanResult:
    info = info or ProgramInfo()
    config = config or Config()
    tool_overrides = tool_overrides or {}
    directory = Path(scan.input_dir)
    # Build automatic tool defaults by matching aptsource files to programs.
    # Keep only the newest APT result for each program.  A directory can
    # contain several generations of the same source file; mtime is the
    # least surprising definition of "最新 APT" for the operator.
    auto_tools: Dict[str, Tuple[float, List[ToolInfo]]] = {}
    latest_apt: Dict[str, Tuple[float, FilePlan]] = {}
    for f in scan.files:
        if f.kind == "aptsource" and f.program:
            try:
                mtime = f.modified_time or (directory / f.source).stat().st_mtime
                previous = latest_apt.get(f.program)
                if previous is None or mtime >= previous[0]:
                    latest_apt[f.program] = (mtime, f)
            except Exception:
                pass
    for program, (mtime, apt_plan) in latest_apt.items():
        try:
            tools = list(apt_plan.parsed_tools)
            if not tools:
                if apt_plan.original_text:
                    tools = extract_tools(apt_plan.original_text)
                else:
                    tools = _extract_apt_tools_from_path(directory / apt_plan.source, config.encoding)
                apt_plan.parsed_tools = list(tools)
            # WP-A2：元数据与轨迹统计挂到最新 APT 计划，供 MPF 复制引用。
            apt_plan.apt_meta = _extract_apt_meta_cached(directory / apt_plan.source, config.encoding)
            apt_plan.apt_toolpath = _extract_apt_toolpath_cached(directory / apt_plan.source, config.encoding)
            auto_tools[program] = (mtime, tools)
        except Exception:
            apt_plan.apt_meta = None
            apt_plan.apt_toolpath = None
            auto_tools[program] = (mtime, [])
    emit_event("info", "plan_built",
               f"生成处理计划：{len(scan.files)} 个文件，MPF {sum(f.kind == 'mpf' for f in scan.files)} 个，"
               f"APTSOURCE {sum(f.kind == 'aptsource' for f in scan.files)} 个")
    def process_mpf(f: FilePlan):
        if f.kind == "mpf" and f.program and f.original_text is not None:
            try:
                effective_info = program_defaults(f.original_text, info)
                # WP-A2/A4：复制最新 APT 的元数据/轨迹/源路径到 MPF 计划（线程内只读引用）；
                # 有 APT 时 DATE 优先采用 APT 生成时间（无 APT 才按变更时刻自动维护）。
                apt_plan = latest_apt.get(f.program)
                if apt_plan:
                    f.apt_meta = apt_plan[1].apt_meta
                    f.apt_toolpath = apt_plan[1].apt_toolpath
                    f.apt_source_path = str(directory / apt_plan[1].source)
                    f.apt_encoding = apt_plan[1].encoding or ""
                apt_date = ""
                if f.apt_meta and f.apt_meta.generated_at:
                    apt_time = parse_apt_generated(f.apt_meta.generated_at)
                    if apt_time is not None:
                        apt_date = format_nc_date(apt_time)
                if apt_date:
                    effective_info.date = apt_date
                replace_tools = f.program in tool_overrides
                if f.program in auto_tools and auto_tools[f.program][1]:
                    # APT describes the actual generated cutter geometry and
                    # takes precedence over both stale Tn rows in the MPF and
                    # saved special_tools.json values.
                    effective_info.tools = list(auto_tools[f.program][1])
                elif replace_tools:
                    effective_info.tools = list(tool_overrides[f.program])
                elif not effective_info.tools:
                    # With no current APT, fall back to saved special-tool
                    # values (passed as tool_overrides), then MPF rows.
                    effective_info.tools = list(tool_overrides.get(f.program, [])) or extract_tools(f.original_text)
                new, changes, header_issues = apply_header(f.original_text, f.program, effective_info, config, replace_tools=replace_tools, filename=f.source)
                new, tool_changed, tool_note = add_initial_tool_change(new, effective_info.tools, config)
                if tool_note:
                    changes.append(tool_note)
                    if not tool_changed:
                        f.auto_tool_change_skipped = tool_note
                new, m03_changed, m03_note = add_m03(new, config)
                if m03_changed:
                    changes.append(m03_note)
                # WP-B2/A4：程序发生实际变更时 DATE 更新——有 APT 用 APT 生成时间，无 APT 用变更发生时间。
                if changes:
                    new, date_changed = update_header_date(new, apt_date or format_nc_date())
                    if date_changed:
                        changes.append("更新 DATE")
                f.output_text, f.changes = new, changes
                f.stats, validation_issues, f.feed_outlier = analyze_program(
                    new, f.source, f.program, info, config, apt_meta=f.apt_meta,
                    retract_plane=f.apt_toolpath.retract_plane if f.apt_toolpath else None)
                f.target = str(directory / (f.program + config.program_output_extension))
                # 缓存本次生效的刀具信息，供 reprocess_file/应用所选回退，避免刷掉刀具。
                f.parsed_tools = list(effective_info.tools)
                f.issues.extend(header_issues)
                f.issues.extend(validation_issues)
                # WP-A4：APT 规划 ↔ MPF 执行交叉校验。
                if f.apt_meta is not None:
                    f.issues.extend(crosscheck_apt(
                        new, f.apt_meta, f.source, config,
                        apt_tools=auto_tools[f.program][1] if f.program in auto_tools else (),
                    ))
                if Path(f.source).name != Path(f.target).name:
                    f.changes.append(f"重命名为 {Path(f.target).name}")
                # 识别过程数据入运行日志：刀具识别结果。
                if effective_info.tools:
                    tool_parts = []
                    for tool in effective_info.tools:
                        fields = [f"T{tool.number}"]
                        if tool.dia.strip():
                            fields.append(f"DIA={tool.dia.strip()}")
                        if tool.tool_coner.strip():
                            fields.append(f"圆角={tool.tool_coner.strip()}")
                        if tool.tool_angle.strip():
                            fields.append(f"单边角={tool.tool_angle.strip()}")
                        if tool.tool_type.strip():
                            fields.append(f"类型={tool.tool_type.strip()}")
                        tool_parts.append("(" + "，".join(fields) + ")")
                    emit_event("info", "tool_recognized",
                               f"刀具识别：{f.source}（{len(effective_info.tools)} 把）",
                               detail="；".join(tool_parts))
                # 识别过程数据入运行日志：异常与错误识别汇总。
                error_issues = [i for i in f.issues if i.severity == "error"]
                warning_issues = [i for i in f.issues if i.severity == "warning"]
                if error_issues or warning_issues:
                    kind_list = sorted({i.kind for i in f.issues})
                    emit_event("warning" if error_issues else "info", "issues_found",
                               f"识别异常与错误：{f.source}（错误 {len(error_issues)} 条、警告 {len(warning_issues)} 条）",
                               detail="、".join(kind_list))
            except Exception as e:
                emit_event("error", "error", f"处理文件失败：{f.source}", detail=traceback.format_exc())
                f.issues.append(Issue(f.source, 1, "", "processing", "error", str(e)))

    mpf_items = [f for f in scan.files if f.kind == "mpf" and f.program and f.original_text is not None]
    if len(mpf_items) > 1 and config.parallel_workers > 1:
        workers = min(config.parallel_workers, len(mpf_items))
        _parallel_apply(mpf_items, process_mpf, workers)
    else:
        for item in mpf_items:
            process_mpf(item)

    for f in scan.files:
        if f.kind == "aptsource" and f.program and config.save_aptsource:
            stamp = scan.archive_stamp or datetime.now().strftime("%Y%m%d_%H%M%S")
            f.action = "move"
            f.target = str(directory / config.aptsource_dir / stamp / (f.program + ".aptsource"))
        elif f.kind == "aptsource" and config.save_aptsource:
            f.action = "move"
            f.issues = [issue for issue in f.issues if issue.kind != "program-name"]
            f.issues.append(Issue(f.source, 1, "", "program-name", "warning", "无法配对程序名，按原文件名归档"))
            stamp = scan.archive_stamp or datetime.now().strftime("%Y%m%d_%H%M%S")
            f.target = str(directory / config.aptsource_dir / stamp / Path(f.source).name)
        elif f.kind == "aptsource":
            f.action = "delete"
            f.target = None
            f.issues = [issue for issue in f.issues if issue.kind != "program-name"]
    # Resolve target collisions by modification time. The latest source wins;
    # older duplicates are removed only after the winner is written
    # successfully during process_plan.
    target_groups: Dict[str, List[FilePlan]] = {}
    for f in scan.files:
        f.issues = [issue for issue in f.issues if issue.kind != "duplicate-target"]
        if f.action == "duplicate":
            f.action = "move" if f.kind == "aptsource" else "keep"
        f.overwrite_target = False
        f.duplicate_winner = ""
        f.duplicate_target = ""
        if f.target and f.action in ("keep", "move"):
            key = os.path.normcase(os.path.abspath(f.target))
            target_groups.setdefault(key, []).append(f)
    for items in target_groups.values():
        if len(items) < 2:
            continue
        winner = max(items, key=lambda item: (item.modified_time, item.source.lower()))
        winner.overwrite_target = True
        for loser in items:
            if loser is winner:
                continue
            loser.action = "duplicate"
            loser.duplicate_winner = winner.source
            loser.duplicate_target = winner.target or ""
            loser.issues.append(Issue(
                loser.source,
                1,
                "",
                "duplicate-target",
                "warning",
                f"目标文件名重复；默认采用最新文件 {winner.source} 覆盖 {Path(winner.target or '').name}，成功后清理此较旧文件",
            ))
            winner.issues.append(Issue(
                winner.source,
                1,
                "",
                "duplicate-target",
                "warning",
                f"将覆盖重复目标 {Path(winner.target or '').name}；较旧文件：{loser.source}",
            ))
    return scan


def _atomic_write(path: Path, text: str, encoding: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    os.close(fd)
    tmp_path = Path(tmp)
    try:
        tmp_path.write_bytes(text.encode(encoding))
        os.replace(str(tmp_path), str(path))
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def _app_version() -> str:
    """延迟读取包版本号，避免 core 与 __init__ 相互导入造成循环依赖。"""
    try:
        from . import __version__ as version
        return str(version)
    except Exception:
        return ""


def _iso_seconds_delta(start: str, end: str) -> float:
    """计算两个 ISO 8601 时间戳（seconds 精度）之间的秒数；解析失败返回 0。"""
    try:
        return (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds()
    except (TypeError, ValueError):
        return 0.0


def _config_snapshot(config: Config) -> dict:
    """导出处理时生效的关键配置（不含编制/审核等业务数据），保证结果可复现。"""
    return {
        "encoding": config.encoding,
        "recursive": config.recursive,
        "save_aptsource": config.save_aptsource,
        "overwrite_fields": config.overwrite_fields,
        "g00_level": config.g00_level,
        "auto_m03": config.auto_m03,
        "auto_tool_change": config.auto_tool_change,
        "m03_position": config.m03_position,
        "feed_min": config.feed_min,
        "feed_max": config.feed_max,
        "spindle_min": config.spindle_min,
        "spindle_max": config.spindle_max,
        "newline": config.newline,
        "required_fields": list(config.required_fields),
        "aux_checks": sorted(config.aux_checks),
    "feed_outlier_min_count": config.feed_outlier_min_count,
    "feed_outlier_ratio": config.feed_outlier_ratio,
    "feed_outlier_low_ratio": config.feed_outlier_low_ratio,
    "feed_outlier_high_ratio": config.feed_outlier_high_ratio,
        "multiple_spindle_warn": config.multiple_spindle_warn,
        "require_end_marker": config.require_end_marker,
        "require_m06": config.require_m06,
        "require_spindle_speed": config.require_spindle_speed,
        "max_file_size": config.max_file_size,
        "max_files": config.max_files,
        "retract_z_threshold": config.retract_z_threshold,
        "ask_backup": config.ask_backup,
    }


def _plan_process_summary(f: FilePlan) -> str:
    """运行日志 detail：单文件处理的关键过程数据（不含完整程序正文，便于快速定位）。"""
    parts = [f"动作={f.action}", f"程序名={f.program or '（未识别）'}"]
    if f.target:
        parts.append(f"目标={f.target}")
    if f.changes:
        parts.append(f"变更 {len(f.changes)} 项")
    issue_counts = Counter(x.severity for x in f.issues)
    if issue_counts:
        parts.append("问题 " + "、".join(f"{severity} {count}" for severity, count in sorted(issue_counts.items())))
    if f.stats is not None:
        parts.append(f"统计 F={f.stats.counts.get('F', 0)} 次、S={f.stats.counts.get('S', 0)} 次")
    return "；".join(parts)


_MONTHS_EN = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def parse_nc_date(text: str) -> Optional[datetime]:
    """解析 NC 头部 DATE（英文月份格式 MMM dd HH:mm:ss yyyy）为 datetime；失败返回 None。"""
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

    主轴方向不一致为 error（CLW→M03、CCLW→M04，含 M03+M04 双方向取舍建议）；
    S/F 数值容差、冷却液、刀具装夹/几何参数、程序名冲突为 warning。
    APT 为规划值，后处理可能取整/倍率，故一律容差。
    """
    issues: List[Issue] = []
    lines = mpf_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    start = _header_end(lines)
    s_values = []
    f_values = []
    has_m03 = has_m04 = has_m08 = has_m09 = False
    t_calls = set()
    s_lines = {}
    f_lines = {}
    m03_line = m04_line = m08_line = m09_line = ""
    t_lines = []
    first_code_line = ""
    for i, raw_line in enumerate(lines[start:], start=start + 1):
        code = code_part(raw_line)
        if code.strip() and not first_code_line:
            first_code_line = raw_line
        if M03_RE.search(code):
            has_m03 = True
            if not m03_line:
                m03_line = raw_line
        if M04_RE.search(code):
            has_m04 = True
            if not m04_line:
                m04_line = raw_line
        if M08_RE.search(code):
            has_m08 = True
            if not m08_line:
                m08_line = raw_line
        if M09_RE.search(code):
            has_m09 = True
            if not m09_line:
                m09_line = raw_line
        for match in TOOL_CALL_RE.finditer(code):
            number = int(match.group(1))
            t_calls.add(number)
            t_lines.append((number, raw_line))
        for parameter in ADDR_RE.finditer(code):
            key = parameter.group(1).upper()
            if key == "S":
                value = float(parameter.group(2))
                s_values.append(value)
                s_lines.setdefault(value, raw_line)
            elif key == "F":
                value = float(parameter.group(2))
                f_values.append(value)
                f_lines.setdefault(value, raw_line)

    def first_t_call_line():
        return t_lines[0][1] if t_lines else first_code_line

    def tolerance(reference, ratio=0.01, minimum=1.0):
        return max(reference * ratio, minimum)

    spindle_records = ["SPINDL/ %s,%s,%s" % (speed, units, direction) for speed, units, direction in meta.spindles]
    feed_records = ["FEDRAT/ %s,%s" % (value, units) for value, units in meta.feeds]
    coolant_values = [value.upper() for value in meta.coolant]
    load_records = ["LOADTL/%s" % number for number in meta.tool_loads]

    if meta.spindles:
        directions = {direction.upper() for _speed, _units, direction in meta.spindles}
        # 主轴方向：原始文本显示 MPF 侧指令行，建议列写 APT 规划方向。
        if "CLW" in directions and has_m04 and not has_m03:
            issues.append(Issue(filename, start + 1, m04_line, "apt-spindle-direction", "error",
                                "APT 规划主轴正转（CLW）应配 M03，正文使用 M04 反转，请核对旋转方向"))
        if "CCLW" in directions and has_m03 and not has_m04:
            issues.append(Issue(filename, start + 1, m03_line, "apt-spindle-direction", "error",
                                "APT 规划主轴反转（CCLW）应配 M04，正文使用 M03 正转，请核对旋转方向"))
        if has_m03 and has_m04 and directions:
            direction = next(iter(directions))
            keep, drop = ("M03", "M04") if direction == "CLW" else ("M04", "M03")
            issues.append(Issue(filename, start + 1, m03_line or m04_line, "apt-spindle-direction", "error",
                                "正文同时含 M03 与 M04；APT 规划方向为 %s，建议保留 %s 并删除 %s" % (direction, keep, drop)))
        # 转速：MPF 有 S 且与 APT 不符 → 显示 MPF 行；APT 有转速而 MPF 无 S → 显示 APT 记录。
        # 符合性提示（info）：除加工参数不符外，其余均不升级为 warning。
        apt_speeds = [float(speed) for speed, _units, _direction in meta.spindles]
        if s_values:
            mismatch_s = next((value for value in s_values if not any(abs(value - speed) <= tolerance(speed) for speed in apt_speeds)), None)
            if mismatch_s is not None:
                issues.append(Issue(filename, start + 1, s_lines.get(mismatch_s, ""), "apt-spindle-mismatch", "warning",
                                    "APT 规划转速：%s；正文 S 值不在规划集合 ±1%% 内，请核对" % "、".join(spindle_records)))
        else:
            issues.append(Issue(filename, start + 1, "、".join(spindle_records), "apt-spindle-mismatch", "warning",
                                "APT 规划转速，但程序正文未找到 S 指令，请核对"))

    if meta.feeds:
        apt_feeds = [float(feed) for feed, _units in meta.feeds]
        if f_values:
            mismatch_f = next((value for value in f_values if not any(abs(value - feed) <= tolerance(feed, 0.10, 1.0) for feed in apt_feeds)), None)
            if mismatch_f is not None:
                issues.append(Issue(filename, start + 1, f_lines.get(mismatch_f, ""), "apt-feed-mismatch", "warning",
                                    "APT 规划进给：%s；正文 F 值不在规划集合 ±10%% 内，请核对" % "、".join(feed_records)))
        else:
            issues.append(Issue(filename, start + 1, "、".join(feed_records), "apt-feed-mismatch", "warning",
                                "APT 规划进给，但程序正文未找到 F 指令，请核对"))

    # 冷却：APT 有 ON 而正文无 M08 → 显示 APT 记录；APT 有 OFF 且正文有 M08 无 M09 → 显示 MPF 行。
    if "ON" in coolant_values and not has_m08:
        issues.append(Issue(filename, start + 1, "COOLNT/ON", "apt-coolant-missing", "info",
                            "APT 规划冷却液开启（COOLNT/ON）应配 M08，但程序正文未找到 M08，请核对"))
    if "OFF" in coolant_values and has_m08 and not has_m09:
        issues.append(Issue(filename, start + 1, m08_line, "apt-coolant-missing", "info",
                            "APT 规划冷却液关闭（COOLNT/OFF）应配 M09，正文未找到 M09，请核对"))

    # 装夹：APT 有 LOADTL 而正文未调用 → 显示 APT 记录；正文有调用但缺部分 → 显示 MPF 首条调用行。
    if meta.tool_loads and not set(meta.tool_loads).issubset(t_calls):
        missing = sorted(set(meta.tool_loads) - t_calls)
        text = "、".join(load_records) if not t_calls else first_t_call_line()
        issues.append(Issue(filename, start + 1, text, "apt-tool-load-mismatch", "info",
                            "APT 规划装夹刀具 T%s 未在正文调用（提示，请确认换刀序列是否符合实际加工）" % "、".join(str(n) for n in missing)))

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
                        tool_line = next((line for line in lines[:start] if 'T%d:' % apt_tool.number in line.upper()), "")
                        issues.append(Issue(filename, start + 1, tool_line, "apt-tool-param-mismatch", "warning",
                                            "T%d 的 %s MPF=%.3f 与 APT 规划 %.3f 不一致，请核对" % (
                                                apt_tool.number, attr, float(mpf_value), float(apt_value))))
                        break
                except ValueError:
                    continue

    if meta.program_name:
        header_program = extract_header_fields(mpf_text).get("PROGRAM", "").strip()
        if header_program and header_program != meta.program_name:
            program_line = next((line for line in lines[:start] if 'MSG("PROGRAM' in line.upper()), "")
            issues.append(Issue(filename, start + 1, program_line, "apt-program-name-conflict", "warning",
                                "MPF 的 PROGRAM 字段为 %s，与 APT 程序名 %s 不一致，请核对" % (header_program, meta.program_name)))

    if meta.generated_at:
        apt_time = parse_apt_generated(meta.generated_at)
        header_date = extract_header_fields(mpf_text).get("DATE", "").strip()
        mpf_date = parse_nc_date(header_date) if header_date else None
        if apt_time and mpf_date and mpf_date < apt_time:
            date_line = next((line for line in lines[:start] if 'MSG("DATE' in line.upper()), "")
            issues.append(Issue(filename, start + 1, date_line, "apt-date-stale", "info",
                                "APT 生成时间为 %s，MPF 头部 DATE 早于该时间，文件可能在后处理之后被修改" % apt_time.strftime("%Y-%m-%d %H:%M:%S")))
    return issues


def _exec_duplicate(f, item, report, src_dir, dst_dir, config, scan, same_tree, successful_targets, confirm_cleanup):
    """处理重复目标文件：最新文件已成功写入后，按确认口径清理较旧源文件。"""
    source = src_dir / f.source
    planned = Path(f.duplicate_target)
    if f.kind == "mpf":
        target = (dst_dir / planned.name) if not same_tree else planned
    else:
        target = (dst_dir / config.aptsource_dir / scan.archive_stamp / planned.name) if not same_tree else planned
    target_key = os.path.normcase(os.path.abspath(str(target)))
    if target_key not in successful_targets:
        report.skipped += 1
        item["status"] = "duplicate-retained"
        item["runtime_error"] = "最新文件未成功写入，较旧重复文件已保留"
    elif not confirm_cleanup or not same_tree:
        report.skipped += 1
        item["status"] = "duplicate-retained"
        item["target"] = str(target)
    elif source.resolve() == target.resolve():
        # The winner has already atomically replaced this path.
        item["status"] = "duplicate-overwritten"
        item["target"] = str(target)
    elif source.exists():
        source.unlink()
        report.deleted += 1
        item["status"] = "duplicate-removed"
    else:
        item["status"] = "duplicate-resolved"
        item["target"] = str(target)


def _exec_mpf(f, item, report, src_dir, dst_dir, config, same_tree, errors, successful_targets):
    """写入 MPF：有错误拦截为失败，无输出/目标跳过，否则原子写入并计数。"""
    source = src_dir / f.source
    if errors:
        report.failed += 1
        item["status"] = "failed"
    elif f.output_text is None or not f.target:
        report.skipped += 1
        item["status"] = "skipped"
    else:
        planned = Path(f.target)
        target = (dst_dir / planned.name) if not same_tree else planned
        item["target"] = str(target)
        if target.exists() and target.resolve() != source.resolve() and not (config.overwrite_existing or f.overwrite_target):
            raise FileExistsError(f"目标已存在: {target.name}")
        _, enc, _ = read_text(source, config.encoding)
        _atomic_write(target, f.output_text, enc)
        if same_tree and target.resolve() != source.resolve() and source.exists():
            source.unlink()
        report.success += 1
        item["status"] = "success"
        successful_targets.add(os.path.normcase(os.path.abspath(str(target))))


def _exec_aptsource(f, item, report, src_dir, dst_dir, config, scan, same_tree, successful_targets, confirm_cleanup):
    """处理 APTSOURCE：删除或归档（时间戳子目录），未确认时跳过。"""
    source = src_dir / f.source
    if f.action == "delete":
        if confirm_cleanup and same_tree and source.exists():
            source.unlink()
            report.deleted += 1
            item["status"] = "deleted"
        else:
            report.skipped += 1
            item["status"] = "skipped"
        return
    if not confirm_cleanup:
        report.skipped += 1
        item["status"] = "skipped"
        return
    planned = Path(f.target)
    target = (dst_dir / config.aptsource_dir / scan.archive_stamp / planned.name) if not same_tree else planned
    item["target"] = str(target)
    if target.exists() and not (config.overwrite_existing or f.overwrite_target):
        raise FileExistsError(f"目标已存在: {target.name}")
    target.parent.mkdir(parents=True, exist_ok=True)
    if same_tree:
        if source.resolve() != target.resolve():
            if f.overwrite_target:
                os.replace(str(source), str(target))
            else:
                shutil.move(str(source), str(target))
    else:
        shutil.copy2(str(source), str(target))
    report.moved += 1
    item["status"] = "moved"
    successful_targets.add(os.path.normcase(os.path.abspath(str(target))))


def _exec_delete(f, item, report, src_dir, same_tree, confirm_cleanup):
    """删除中间文件（LOG/MOAPTIndexes 等），未确认时跳过。"""
    source = src_dir / f.source
    if confirm_cleanup and same_tree and source.exists():
        source.unlink()
        report.deleted += 1
        item["status"] = "deleted"
    else:
        report.skipped += 1
        item["status"] = "skipped"


def process_plan(scan: ScanResult, output_dir: Optional[str] = None, config: Optional[Config] = None, *, confirm_cleanup: bool = True, progress_callback=None, backup: bool = False, generator: str = "", confirmations: Sequence[str] = ()) -> ProcessReport:
    config = config or Config()
    src_dir = Path(scan.input_dir).resolve()
    dst_dir = Path(output_dir or scan.input_dir).resolve()
    same_tree = src_dir == dst_dir
    report = ProcessReport(str(src_dir), str(dst_dir), datetime.now().isoformat(timespec="seconds"))
    report.app_version = _app_version()
    report.generator = generator
    report.config_snapshot = _config_snapshot(config)
    report.user_confirmations = list(confirmations)
    report.scan_warnings = list(scan.warnings)
    report.archive_stamp = scan.archive_stamp
    dst_dir.mkdir(parents=True, exist_ok=True)
    backup_root = ""
    if backup:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_root = str(dst_dir / "backup" / stamp)
        backup_path = Path(backup_root)
        backup_path.mkdir(parents=True, exist_ok=True)
        for plan_file in scan.files:
            source = src_dir / plan_file.source
            if source.is_file():
                destination = backup_path / plan_file.source
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(source), str(destination))
        report.backup_dir = backup_root
        emit_event("info", "backup_created", f"处理前备份已创建：{backup_root}（{len(scan.files)} 个文件）")
    successful_targets = set()
    ordered_files = sorted(scan.files, key=lambda item: item.action == "duplicate")
    total = len(ordered_files)
    emit_event("info", "process_start",
               f"开始处理：{total} 个文件（备份：{backup_root if backup else '未启用'}）")
    for index, f in enumerate(ordered_files, start=1):
        if progress_callback is not None:
            progress_callback(index, total, f.source)
        emit_event("info", "process_file", f"处理文件：{f.source}（{index}/{total}）", detail=_plan_process_summary(f))
        if f.stats is None and f.output_text is not None:
            f.stats = calculate_stats(f.output_text)
        diff = []
        if f.original_text is not None and f.output_text is not None and f.original_text != f.output_text:
            diff = list(difflib.unified_diff(f.original_text.splitlines(), f.output_text.splitlines(), fromfile=f.source + " (before)", tofile=(Path(f.target).name if f.target else f.source) + " (after)", lineterm=""))
        item = {"file": f.source, "action": f.action, "program": f.program, "encoding": f.encoding, "target": f.target or "", "program_name_source": f.program_name_source or "", "changes": f.changes, "diff": diff, "issues": [asdict(x) for x in f.issues], "stats": f.stats.as_dict() if f.stats else None, "apt_meta": f.apt_meta.to_dict() if f.apt_meta else None, "toolpath_stats": f.apt_toolpath.to_dict() if f.apt_toolpath else None, "feed_outlier": f.feed_outlier.to_dict() if f.feed_outlier else None}
        errors = [x for x in f.issues if x.severity == "error"]
        report.warnings += sum(x.severity == "warning" for x in f.issues)
        report.errors += len(errors)
        try:
            if f.action == "duplicate":
                _exec_duplicate(f, item, report, src_dir, dst_dir, config, scan, same_tree, successful_targets, confirm_cleanup)
            elif f.kind == "mpf":
                _exec_mpf(f, item, report, src_dir, dst_dir, config, same_tree, errors, successful_targets)
            elif f.kind == "aptsource":
                _exec_aptsource(f, item, report, src_dir, dst_dir, config, scan, same_tree, successful_targets, confirm_cleanup)
            elif f.action == "delete":
                _exec_delete(f, item, report, src_dir, same_tree, confirm_cleanup)
            else:
                report.skipped += 1; item["status"] = "review"
        except UnicodeError as e:
            report.failed += 1
            item["status"] = "failed"
            item["error_kind"] = "encoding"
            item.setdefault("runtime_error", str(e))
            emit_event("error", "error", f"处理文件失败：{f.source}", detail=f"{_plan_process_summary(f)}\n{traceback.format_exc()}")
        except PermissionError as e:
            report.failed += 1
            item["status"] = "failed"
            item["error_kind"] = "permission"
            item.setdefault("runtime_error", str(e))
            emit_event("error", "error", f"处理文件失败：{f.source}", detail=f"{_plan_process_summary(f)}\n{traceback.format_exc()}")
        except OSError as e:
            report.failed += 1
            item["status"] = "failed"
            item["error_kind"] = "io"
            item.setdefault("runtime_error", str(e))
            emit_event("error", "error", f"处理文件失败：{f.source}", detail=f"{_plan_process_summary(f)}\n{traceback.format_exc()}")
        except Exception as e:
            report.failed += 1
            item["status"] = "failed"
            item["error_kind"] = "other"
            item.setdefault("runtime_error", str(e))
            emit_event("error", "error", f"处理文件失败：{f.source}", detail=f"{_plan_process_summary(f)}\n{traceback.format_exc()}")
        report.files.append(item)
    # WP-A3：报告级 APT 全局摘要（按程序名去重——MPF 与其配对 APT 共享同一程序；
    # 只存去重/计数聚合值）。
    apt_programs = {}
    for plan_file in scan.files:
        if plan_file.apt_meta and plan_file.program:
            apt_programs.setdefault(plan_file.program, plan_file.apt_meta)
    report.apt_summary = {
        "machines": sorted({meta.machine for meta in apt_programs.values() if meta.machine}),
        "spindle_speeds": sorted({float(speed) for meta in apt_programs.values() for speed, _units, _direction in meta.spindles}),
        "tool_loads": sorted({number for meta in apt_programs.values() for number in meta.tool_loads}),
        "operations": sorted({name for meta in apt_programs.values() for name in meta.operations}),
        "tool_usage": {
            number: sum(1 for meta in apt_programs.values() if number in meta.tool_loads)
            for number in sorted({n for meta in apt_programs.values() for n in meta.tool_loads})
        },
    }
    report.finished_at = datetime.now().isoformat(timespec="seconds")
    report.elapsed_seconds = _iso_seconds_delta(report.started_at, report.finished_at)
    emit_event("info", "process_finish",
               f"处理完成：成功 {report.success}，失败 {report.failed}，移动 {report.moved}，删除 {report.deleted}，跳过 {report.skipped}")
    report.refresh_runtime_log()
    return report
