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
from collections import Counter, OrderedDict, deque
from dataclasses import asdict, dataclass, field
from datetime import datetime
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
# 《F值异常检测方法》（2026-08-06 设计稿）参数：
#   抬刀平面判定容差（mm）、全程序“罕见”次数上限、基准相对差距容差。
# 容差固定 30%（2026-08-07 决策：不随刀具尺寸放大，避免 20mm 刀 60% 容差
# 漏掉 F15000/F8888/F450 等文档示例异常，见技术文档 §7）。
SAFE_PLANE_TOL = 1.0
FEED_RARE_MAX = 2
FEED_BASE_TOL = 0.30
# 英文月份常量（头部 DATE 格式与解析共用，避免重复定义）。
_MONTHS_EN = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
MSG_RE = re.compile(r'^\s*MSG\s*\(\s*["\'](.*?)["\']\s*\)\s*;?\s*$', re.I)
PPRINT_RE = re.compile(r"\bPPRINT\s+PROGNAME\s+([A-Za-z0-9_-]+)", re.I)
NUM = r"[+-]?(?:(?:\d+(?:\.\d*)?)|(?:\.\d+))(?:[Ee][+-]?\d+)?"
# 地址解析只保留统计/校验实际消费的 F/S/X/Y/Z（validate 的统计与检查、
# crosscheck 的 S 仅读这四键）；G/T/M 地址不再解析，省去无用的 float 与存储。
ADDR_RE = re.compile(r"(?<![A-Za-z])([FSXYZ])\s*(" + NUM + r")", re.I)
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
_SCAN_TEXT_CACHE: "OrderedDict[str, Tuple[int, int, str, Tuple[str, str, str]]]" = OrderedDict()
_APT_TOOL_CACHE: "OrderedDict[str, Tuple[int, int, str, List[ToolInfo]]]" = OrderedDict()
# 按 (路径, mtime, size, 编码) 缓存逐行解析记录，供 analyze_program 的
# 校验/F 检测/APT 交叉校验共享；文件变化后自动失效。
_ROWS_CACHE: "OrderedDict[str, Tuple[int, int, str, List[dict]]]" = OrderedDict()

# 单文件分析结果缓存：键覆盖源文件内容、程序名、头部信息、配置、配对 APT、
# 跨程序参照与刀具覆盖；命中时跳过整条分析管线（重复扫描接近瞬时）。
_ANALYSIS_CACHE: "OrderedDict[str, dict]" = OrderedDict()
_ANALYSIS_CACHE_MAX = 4000


def _cache_get(cache: OrderedDict, key: str):
    """LRU 读：命中时移到末尾（保持插入序 = 最近使用序）。"""
    value = cache.get(key)
    if value is not None:
        cache.move_to_end(key)
    return value


def _cache_put(cache: OrderedDict, key: str, value, max_entries: int) -> None:
    """LRU 写：超限逐出最久未使用条目，不再整表清空（清空会让全部缓存值同时失效）。"""
    cache[key] = value
    cache.move_to_end(key)
    while len(cache) > max_entries:
        try:
            cache.popitem(last=False)
        except KeyError:
            break


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
    return f"{_MONTHS_EN[value.month - 1]} {value.day:02d} {value:%H:%M:%S %Y}"


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
class FeedOutlierData:
    """F 离群检测过程数据（《F值异常检测方法》抬刀平面分段对比法）。

    - safe_plane：本程序抬刀平面（最大 Z 簇，单个超高点不参与）；
    - tolerance：相对差距容差（固定 30%，决策稿不随刀具尺寸放大）；
    - segments：抬刀平面切出的“来回”段（行区间 + 段内运动行 F 计数）；
    - outliers：罕见且远离其他段所有 F 的值（warning/review 两级，
      含行号、次数、最小差距、APT 辅助上下文）；
    - distribution：单段程序无段间参照时的 F 值分布表（值 × 次数 × 首行）；
    - boundary_errors：硬边界（超 F/S 上下限，与 issues 中 feed-range 对应）；
    - apt_feeds：配对 APT 的 FEDRAT 集合（仅辅助上下文，不是合法值白名单）。
    """
    safe_plane: Optional[float] = None
    tolerance: float = FEED_BASE_TOL
    segments: List[dict] = field(default_factory=list)
    outliers: List[dict] = field(default_factory=list)
    distribution: List[dict] = field(default_factory=list)
    boundary_errors: List[dict] = field(default_factory=list)
    apt_feeds: List[float] = field(default_factory=list)
    # 单段程序跨程序参照用到的常见档位数（0 = 未使用参照）。
    reference_count: int = 0

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
    # 渐进分析上下文（build_plan 轻量阶段填充，供 GUI 后台逐文件复用）。
    analyze_context: Dict[str, object] = field(default_factory=dict)


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
    # 2026-08-08 报告完善：运行环境（platform/python 版本/位数）与扫描分类统计。
    environment: dict = field(default_factory=dict)
    scan_stats: dict = field(default_factory=dict)

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


# 模块级共享事件源：core 内部埋点直接调用 emit_event；GUI/CLI 启动时 attach 磁盘日志。
_runtime_log = RuntimeLog()


def runtime_log() -> RuntimeLog:
    return _runtime_log


def reset_runtime_log() -> RuntimeLog:
    """清空并替换共享事件源（测试隔离用；GUI/CLI 启动时模块级实例本就是新的）。"""
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
    cached = _cache_get(_SCAN_TEXT_CACHE, key)
    if cached and cached[:3] == signature:
        return cached[3]
    result = read_text(path, encoding)
    _cache_put(_SCAN_TEXT_CACHE, key, signature + (result,), 1000)
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
    cached = _cache_get(_APT_TOOL_CACHE, key)
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
    _cache_put(_APT_TOOL_CACHE, key, (stat.st_mtime_ns, stat.st_size, encoding, list(tools)), 1000)
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


_APT_DATA_CACHE: "OrderedDict[str, Tuple[int, int, str, Tuple[AptMeta, ToolpathStats, List[ToolInfo]]]]" = OrderedDict()


def _extract_apt_data(path: Path, encoding: str = "auto") -> Tuple[AptMeta, ToolpathStats, List[ToolInfo]]:
    """单遍解析 APT：元数据 + 轨迹统计 + 刀具规格（避免三次全文件扫描）。"""
    meta = AptMeta()
    stats = ToolpathStats()
    seen_ops = set()
    seen_spindles = set()
    seen_feeds = set()
    seen_coolant = set()
    seen_loads = set()
    transform = []
    current_operation = ""
    tool_lines = []
    take_tool_continuation = False
    nums = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?")
    z_values: List[float] = []
    initialized = False
    native_retract = 0
    data = path.read_bytes()
    try:
        text = _decode(data, encoding)[0]
    except (UnicodeDecodeError, ValueError):
        return meta, stats, []
    for raw_line in text.splitlines():
        # 轨迹统计（ASCII 关键字）
        stripped_line = raw_line.lstrip()
        if stripped_line.startswith("RAPID") or "GOHOME" in raw_line:
            native_retract += 1
        elif raw_line.startswith("GOTO"):
            values = [float(value) for value in nums.findall(raw_line)]
            if len(values) >= 3:
                x, y, z = values[0], values[1], values[2]
                if not initialized:
                    stats.min_x = stats.max_x = x
                    stats.min_y = stats.max_y = y
                    stats.min_z = stats.max_z = z
                    initialized = True
                else:
                    # 比较代替 min/max 内置调用：GOTO 行数量大（单文件数千行），
                    # 逐行六次内置调用在 25 文件实测占约 0.18s（2026-08-08）。
                    if x < stats.min_x:
                        stats.min_x = x
                    if x > stats.max_x:
                        stats.max_x = x
                    if y < stats.min_y:
                        stats.min_y = y
                    if y > stats.max_y:
                        stats.max_y = y
                    if z < stats.min_z:
                        stats.min_z = z
                    if z > stats.max_z:
                        stats.max_z = z
                stats.goto_count += 1
                z_values.append(z)
        elif "CIRCLE" in raw_line:
            stats.arc_count += 1
        # 元数据与刀具
        upper_line = raw_line.upper()
        is_toolno = "TOOLNO" in upper_line and "/" in upper_line
        if take_tool_continuation or ("CUTTER" in upper_line and "/" in upper_line) or is_toolno:
            tool_lines.append(raw_line)
        take_tool_continuation = is_toolno
        if len(raw_line) > 400:
            line = raw_line[:400].strip()
        else:
            line = raw_line.strip()
        if not line:
            continue
        # 快速路径：只有 $$ 注释头行或含 SPINDL/FEDRAT/COOLNT/LOADTL 关键字的行
        # 才跑元数据正则（GOTO 轨迹行等绝大多数行直接跳过）。
        if (line.startswith("$$") or "SPINDL" in upper_line or "FEDRAT" in upper_line
                or "COOLNT" in upper_line or "LOADTL" in upper_line):
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
    if native_retract:
        stats.retract_count = native_retract
    else:
        plane = _adaptive_retract_plane(z_values)
        if plane is not None:
            stats.retract_plane = plane
            stats.retract_count = _retract_runs(z_values, plane, max(5.0, plane * 0.05))
    try:
        stat = path.stat()
        _cache_put(_APT_TRACE_CACHE, str(path.resolve()),
                   (stat.st_mtime_ns, stat.st_size, encoding, z_values), 1000)
    except OSError:
        pass
    if transform:
        meta.transform = transform
    tools: List[ToolInfo] = []
    if tool_lines:
        tool_text = "\n".join(tool_lines)
        meta.tools = [
            {"number": tool.number, "dia": tool.dia, "tool_coner": tool.tool_coner,
             "tool_type": tool.tool_type, "tool_angle": tool.tool_angle}
            for tool in extract_tools(tool_text)
        ]
        tools = [ToolInfo(tool["number"], tool["dia"], tool["tool_coner"],
                          tool["tool_type"], tool["tool_angle"]) for tool in meta.tools]
    return meta, stats, tools


def _extract_apt_data_cached(path: Path, encoding: str = "auto") -> Tuple[AptMeta, ToolpathStats, List[ToolInfo]]:
    stat = path.stat()
    key = str(path.resolve())
    cached = _cache_get(_APT_DATA_CACHE, key)
    if cached and cached[:3] == (stat.st_mtime_ns, stat.st_size, encoding):
        return cached[3]
    result = _extract_apt_data(path, encoding)
    _cache_put(_APT_DATA_CACHE, key, (stat.st_mtime_ns, stat.st_size, encoding, result), 1000)
    return result


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


_APT_TRACE_CACHE: "OrderedDict[str, Tuple[int, int, str, List[float]]]" = OrderedDict()


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


def recount_retracts(path: Path, height: float, encoding: str = "auto") -> int:
    """按指定抬刀高度重算抬刀次数（优先使用轨迹缓存；无缓存时重新流式读取）。"""
    stat = path.stat()
    key = str(path.resolve())
    cached = _cache_get(_APT_TRACE_CACHE, key)
    if not cached or cached[:3] != (stat.st_mtime_ns, stat.st_size, encoding):
        z_values = _stream_z_values(path, encoding)
        _cache_put(_APT_TRACE_CACHE, key,
                   (stat.st_mtime_ns, stat.st_size, encoding, z_values), 1000)
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


def _iter_files(directory: Path, recursive: bool) -> Iterable[Path]:
    iterator = directory.rglob("*") if recursive else directory.glob("*")
    return (p for p in iterator if p.is_file())


def extract_drawing_candidates(text: str) -> List[Tuple[str, str]]:
    """Extract drawing numbers advertised by an APT source header.

    CATIA APT exports commonly contain either of these records::

        $$ FILENAME      D0354F31311-201.CATProcess
        $$ PRODUCTNAME   NCSetup_M-D0354F31311-201_11.47.18

    FILENAME yields ``D0354F31311-201``；PRODUCTNAME 保留 `NCSetup_` 之后的
    完整标识（如 `M-D0354F31311-201`，含 `M-` 前缀）。结果保留来源标签，
    同一来源内重复合并、不同来源各自保留（文件顺序不变）。
    """
    candidates: List[Tuple[str, str]] = []
    seen = set()

    def add(label: str, value: str) -> None:
        value = value.strip().strip('"\'')
        if not value:
            return
        key = (label, value)
        if key in seen:
            return
        # Drawing numbers are deliberately permissive (letters, digits,
        # underscores and dashes are all seen in production data).
        if not re.match(r"^[A-Za-z0-9][A-Za-z0-9_-]*$", value):
            return
        seen.add(key)
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
            pm = re.search(r"NCSetup_(.+?)(?:_[0-9]+(?:\.[0-9]+){1,3})?$", product, re.I)
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
                    # 保留 APT 来源标签（APT FILENAME / APT PRODUCTNAME），
                    # 同一图号的两个来源都作为独立候选展示。
                    if (label, value) not in drawing_seen:
                        drawing_seen.add((label, value))
                        drawing_candidates.append((label, value))
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


def add_initial_tool_change(text: str, tools: Sequence[ToolInfo], config: Config) -> Tuple[str, bool, str]:
    """Insert or correct the initial tool change for single-tool programs.

    The option is intentionally disabled by default.  When enabled:

    - If the program already contains a correct standalone ``TnM6`` (T number
      matches and M6 is present), the whole program is left untouched.
    - If a standalone tool-change row exists but is wrong (T number mismatch
      or missing M6), it is corrected in place - the row is not deleted and a
      new row is not prepended.  Other T references in the body are corrected
      to keep the program consistent.
    - Only when no standalone tool-change row exists is a canonical ``TnM6``
      row inserted at the beginning of the body.
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
            "程序引用多把刀具（" + "、".join("T" + str(num) for num in sorted(referenced)) +
            "），不具备自动添加换刀指令条件，已跳过生成，请人工确认换刀流程",
        )

    def fix_t_refs(line: str) -> str:
        """Replace T numbers in the code part only; comments stay untouched."""
        code_segment, semicolon_sep, semicolon_tail = line.partition(";")
        segments = re.split(r"(\(.*?\))", code_segment)
        replaced = "".join(
            segment if segment.startswith("(") else TOOL_REF_RE.sub("T" + str(number), segment)
            for segment in segments
        )
        return replaced + (semicolon_sep + semicolon_tail if semicolon_sep else "")

    standalone_tn_re = re.compile(r"^\s*(?:N\d+\s*)?T\d+\s*;?\s*$", re.I)
    change_index = None
    change_has_m6 = False
    for idx, line in enumerate(body):
        if STANDALONE_CHANGE_RE.match(line):
            change_index, change_has_m6 = idx, True
            break
        if standalone_tn_re.match(line):
            change_index, change_has_m6 = idx, False
            break

    if change_index is None:
        # No standalone tool-change row: insert a canonical row at the top of
        # the body and correct every T reference for consistency.
        corrected = []
        for line in body:
            if STANDALONE_CHANGE_RE.match(line):
                continue
            corrected.append(fix_t_refs(line))
        semicolon = any(line.rstrip().endswith(";") for line in corrected[:30] if line.strip())
        command = "T{}M6{}".format(number, ";" if semicolon else "")
        result_lines = header + [command] + corrected
        if had_trailing:
            result_lines.append("")
        result = newline.join(result_lines)
        return result, result != text, "在程序正文首行添加换刀指令 " + command

    # A standalone tool-change row exists: correct it in place.
    fixed = fix_t_refs(body[change_index])
    if not change_has_m6:
        fixed = re.sub(
            r"(?<![A-Z])T\d+(?!\d)", "T%dM6" % number, fixed, count=1, flags=re.I)
    if fixed == body[change_index]:
        # Already correct: leave the whole program untouched.
        return text, False, "程序已有正确换刀指令 T%dM6，未修改" % number
    corrected = [fix_t_refs(line) if idx != change_index else fixed
                 for idx, line in enumerate(body)]
    result_lines = header + corrected
    if had_trailing:
        result_lines.append("")
    result = newline.join(result_lines)
    return result, result != text, "修正换刀指令为 T%dM6（在原行修改，未删除重加）" % number


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


# --- F 离群检测：《F值异常检测方法》抬刀平面分段对比（2026-08-07 决策稿）---

def _feed_safe_plane(rows) -> Optional[float]:
    """抬刀平面：程序自身最大 Z 簇（单个超高点不参与，避免干扰切段）。"""
    z_values = [z for _ln, _f, _ef, z, _xy, _m in rows if z is not None]
    return _adaptive_retract_plane(z_values) if z_values else None


def _feed_segment_ranges(rows, safe_plane: float,
                         tolerance: float = SAFE_PLANE_TOL) -> List[Tuple[int, int]]:
    """按抬刀平面穿越切段，返回互不重叠的 (首行, 末行) 行区间。

    Z 从抬刀平面以下“穿越”回抬刀平面（≥ 平面−容差）即结束当前段；
    无 Z 运动行不改变穿越状态，按行号归属所在段；下一段从穿越行的下一行开始。
    """
    bounds = []
    if not rows:
        return bounds
    first_line = rows[0][0]
    motion_lines = [ln for ln, _f, _ef, z, _xy, is_motion in rows if is_motion or z is not None]
    last_line = max(motion_lines) if motion_lines else rows[-1][0]
    seg_start = first_line
    below = False
    for ln, z in ((ln, z) for ln, _f, _ef, z, _xy, _m in rows if z is not None):
        at_safe = z >= safe_plane - tolerance
        if below and at_safe:
            bounds.append((seg_start, ln))
            seg_start = ln + 1
        below = not at_safe
    if seg_start <= last_line:
        bounds.append((seg_start, last_line))
    return bounds


def _segment_feed_counts(rows, bounds) -> List[Counter]:
    """每段运动行的有效 F 计数（含模态继承）。

    行按行号有序、段区间互不重叠：单遍指针归段，避免 O(段数 × 行数)。
    """
    counts_list = [Counter() for _ in bounds]
    seg_index = 0
    for ln, feed, _ef, _z, _xy, is_motion in rows:
        while seg_index < len(bounds) and ln > bounds[seg_index][1]:
            seg_index += 1
        if seg_index >= len(bounds):
            break
        if is_motion and feed is not None and ln >= bounds[seg_index][0]:
            counts_list[seg_index][feed] += 1
    return counts_list


def _feed_gap(first: float, second: float) -> float:
    """相对差距：|f − g| / max(f, g)。"""
    return abs(first - second) / max(first, second)


def _feed_z_runs(rows) -> List[List[Tuple[int, Optional[float], Optional[float]]]]:
    """连续下降的 Z 序列（忽略无 Z 行，不打断）；返回 [[(行号, Z, 有效F), ...]]。"""
    runs: List[List[Tuple[int, Optional[float], Optional[float]]]] = []
    cur: List[Tuple[int, Optional[float], Optional[float]]] = []
    prev_z = None
    for ln, feed, _ef, z, _xy, _m in rows:
        if z is None:
            continue
        if prev_z is not None and z < prev_z:
            cur.append((ln, z, feed))
        else:
            if cur:
                runs.append(cur)
            cur = [(ln, z, feed)]
        prev_z = z
    if cur:
        runs.append(cur)
    return runs


def _violates_axial_order(runs, value: float) -> bool:
    """策略1「越深越慢」：罕见值出现在下降序列中，若浅处比深处慢、
    或最深点不是该序列最慢值，则视为违反轴向次序（异常信号）。"""
    for run in runs:
        deepest = min(run, key=lambda item: item[1])
        for _ln, z, feed in run:
            if feed != value:
                continue
            if z > deepest[1] and feed < deepest[2]:
                return True
            if z == deepest[1] and feed > min(item[2] for item in run):
                return True
    return False


def _violates_repeat_consistency(runs, value: float, depth_tol: float = 0.5) -> bool:
    """策略2「同深同速」：罕见值作为某下降序列最深点，但其它序列在相同深度
    （±tol）使用不同 F → 违反重复一致性（异常信号）。"""
    for run in runs:
        deepest = min(run, key=lambda item: item[1])
        if deepest[2] != value:
            continue
        for other in runs:
            if other is run:
                continue
            other_deepest = min(other, key=lambda item: item[1])
            if abs(other_deepest[1] - deepest[1]) <= depth_tol and other_deepest[2] != value:
                return True
    return False


def _axial_feed_exempt(rows, value: float, plane: float, runs=None) -> bool:
    """轴向豁免（值无关）：罕见值的所有出现行都是纯 Z 运动且位于抬刀平面以下
    （下刀/钻入类；平面上的定位/抬刀行不豁免），并通过「越深越慢」「同深同速」
    一致性校验。任何一条不满足都不豁免。runs 由调用方缓存（_feed_z_runs 全行
    扫描，多个罕见值时只算一遍）。"""
    occurrences = [(z, has_xy) for _ln, feed, _ef, z, has_xy, is_motion in rows
                   if is_motion and feed == value]
    if not occurrences or not all(z is not None and not has_xy and z < plane
                                  for z, has_xy in occurrences):
        return False
    if runs is None:
        runs = _feed_z_runs(rows)
    if _violates_axial_order(runs, value) or _violates_repeat_consistency(runs, value):
        return False
    return True


FEED_VALUE_RE = re.compile(r"(?<![A-Za-z])F\s*(" + NUM + r")", re.I)


def _explicit_feed_set(text: str) -> frozenset:
    """提取正文显式 F 值集合（供跨程序常见档位参照）。

    用专用 F 正则（仅匹配 F 地址），比全量 ADDR 解析快约 2~3 倍。
    """
    feeds = set()
    lines = text.replace("\r\n", "\n").split("\n")
    start = _header_end(lines)
    for raw_line in lines[start:]:
        for match in FEED_VALUE_RE.finditer(code_part(raw_line)):
            try:
                feeds.add(float(match.group(1)))
            except ValueError:
                pass
    return frozenset(feeds)


# 显式 F 集按 (path, mtime, size, encoding) 缓存：跨程序参照在每次扫描都会
# 重建（在单文件分析缓存命中之前），直接重解析全部 MPF 正文是热扫描的主要开销
# （2026-08-08 实测热扫描 0.19s 中占 0.15s）；文件变化后按 stat 自动失效。
_FEED_SET_CACHE: "OrderedDict[str, Tuple[int, int, str, frozenset]]" = OrderedDict()


def _explicit_feed_set_cached(path: Path, encoding: str = "auto") -> frozenset:
    """按 (path, mtime, size, encoding) 缓存显式 F 集，复用 _read_text_cached 的文本缓存。"""
    text, used_encoding, _ = _read_text_cached(path, encoding)
    stat = path.stat()
    key = str(path.resolve())
    cached = _cache_get(_FEED_SET_CACHE, key)
    if cached and cached[:3] == (stat.st_mtime_ns, stat.st_size, used_encoding):
        return cached[3]
    result = _explicit_feed_set(text)
    _cache_put(_FEED_SET_CACHE, key,
               (stat.st_mtime_ns, stat.st_size, used_encoding, result), 1000)
    return result


def build_feed_reference(feed_sets: Sequence[Tuple[str, Sequence[float]]],
                         min_programs: int = 2) -> Dict[str, frozenset]:
    """单段程序的跨程序常见档位参照：对每个源文件，取其它源文件中显式出现
    次数 ≥ min_programs 的 F 值并集（排除自身）。单点异常值不会进入参照，
    避免同目录多处异常互相污染。"""
    by_source = {source: set(feeds) for source, feeds in feed_sets}
    result: Dict[str, frozenset] = {}
    for source in by_source:
        counts = Counter()
        for other, feeds in by_source.items():
            if other != source:
                counts.update(feeds)
        result[source] = frozenset(value for value, count in counts.items()
                                   if count >= min_programs)
    return result


def detect_feed_outliers(text: str, filename: str, config: Config,
                         apt_feeds: Sequence[float] = (),
                         reference_feeds: Sequence[float] = (),
                         rows: Optional[Sequence[dict]] = None) -> Tuple[List[Issue], FeedOutlierData]:
    """《F值异常检测方法》抬刀平面分段对比检测。

    - 抬刀平面取程序自身最大 Z 簇，Z 穿越平面（±1mm）切段；
    - 每段统计运动行有效 F（含模态继承）；
    - 轴向豁免（值无关）：罕见值所有出现行都是抬刀平面以下的纯 Z 下刀/钻入行，
      并通过「越深越慢」「同深同速」一致性校验才豁免；平面上的定位/抬刀行不豁免；
    - 其余罕见值与其他段所有 F 的相对差距都超过 30% 时输出：
      差距 >60% → 警告（feed-outlier），否则复核（feed-review）；
    - 单段程序：有跨程序常见档位参照（reference_feeds）时与之对比；
      无参照时输出 F 分布表（不产生告警），供人工检查。
    """
    issues: List[Issue] = []
    feed_outlier = FeedOutlierData(apt_feeds=[float(feed) for feed in apt_feeds])
    lines = text.replace("\r\n", "\n").split("\n")
    start = _header_end(lines)
    if rows is None:
        # 测试直接调用时无共享行记录，用共享解析器构建；生产路径 analyze_program
        # 总是传入 rows（解析只做一遍，复用跨校验/F 检测/交叉校验）。
        rows = _parse_code_rows(text, start)
    # 共享解析器（_parse_code_rows）的行记录转成 feed 检测使用的元组格式。
    rows = [(rec["line"], rec["feed"], rec["explicit_f"], rec["z"], rec["xy"], rec["motion"])
            for rec in rows]
    if not rows:
        return issues, feed_outlier
    safe_plane = _feed_safe_plane(rows)
    if safe_plane is None:
        return issues, feed_outlier
    line_text = {line_number: raw_line
                 for line_number, raw_line in enumerate(lines[start:], start=start + 1)}
    feed_outlier.safe_plane = safe_plane
    bounds = _feed_segment_ranges(rows, safe_plane)
    segment_counts = _segment_feed_counts(rows, bounds)
    # 每段“显式写入过”的 F 集合：模态继承跨越段边界的值（写在穿越行、被下一段
    # 继承）只在其写入段拥有归属；比较时从纯模态出现的段中剔除该值，避免
    # 自身跨段导致的 gap=0 跳过（如抬刀行写 F10000 被下一段继承两行）。
    segment_explicit_feeds = [
        {explicit_f for ln, _f, explicit_f, _z, _xy, _m in rows
         if explicit_f is not None and seg_start <= ln <= seg_end}
        for seg_start, seg_end in bounds
    ]
    feed_outlier.segments = [
        {
            "index": index + 1,
            "first_line": bounds[index][0],
            "last_line": bounds[index][1],
            "feed_counts": {f"{value:g}": count for value, count in sorted(counts.items())},
            "feeds": [value for value in sorted(counts)],
        }
        for index, counts in enumerate(segment_counts)
    ]
    global_counts = Counter(
        feed for _ln, feed, _ef, _z, _xy, is_motion in rows if is_motion and feed is not None)
    # 罕见按“显式写入次数”统计：模态继承只计入段 F 集合（参与段间比较），
    # 不稀释罕见判定——一次误写被后续多行继承（真实事故形态）仍应被识别。
    explicit_counts = Counter(
        explicit_f for _ln, _f, explicit_f, _z, _xy, _m in rows if explicit_f is not None)
    # _feed_z_runs 是全行扫描：懒计算一次，多个罕见候选值共用（豁免检查复用）。
    z_runs_cache = []

    def axial_exempt(value: float) -> bool:
        if not z_runs_cache:
            z_runs_cache.append(_feed_z_runs(rows))
        return _axial_feed_exempt(rows, value, safe_plane, runs=z_runs_cache[0])

    def matches_apt_feed(value: float) -> bool:
        return any(abs(value - apt) <= max(apt * 0.10, 1.0) for apt in feed_outlier.apt_feeds)

    def report_outlier(value: float, count: int, level: str, min_gap: float,
                       others: Sequence[float], reason: str, context_label: str,
                       segment_index: int) -> None:
        kind = "feed-outlier" if level == "warning" else "feed-review"
        severity = "warning" if level == "warning" else "info"
        raw_value = f"{value:g}"
        for ln, _feed, explicit_f, _z, _xy, _m in rows:
            if explicit_f != value:
                continue
            raw_line = line_text.get(ln, "")
            verb = "请确认" if level == "warning" else "请复核"
            suggestion = (
                f"F{raw_value} 全程序仅 {count} 次且与{context_label}差距"
                f"{'过大' if level == 'warning' else '较大'}"
                f"（最小差距 {min_gap:.1%}），{verb}")
            issues.append(Issue(filename, ln, raw_line, kind, severity, suggestion))
            feed_outlier.outliers.append({
                "line": ln,
                "value": value,
                "raw_value": raw_value,
                "text": raw_line,
                "count": count,
                "level": level,
                "reason": reason,
                "gap": round(min_gap, 4),
                "axial_only": False,
                "in_apt": matches_apt_feed(value),
                "segment_index": segment_index,
                "other_segment_feeds": sorted(others),
            })
            emit_event(severity, "feed_outlier",
                       f"F 离群识别：{filename} 第 {ln} 行 F{raw_value}",
                       detail=f"{raw_line.strip()}\n{suggestion}")

    if len(segment_counts) >= 2:
        segment_sets = [set(counts) for counts in segment_counts]
        for index, counts in enumerate(segment_counts):
            for value in sorted(counts):
                count = explicit_counts[value]
                if count > FEED_RARE_MAX:
                    continue
                if axial_exempt(value):
                    continue
                others = set()
                for other_index, other_set in enumerate(segment_sets):
                    if other_index == index:
                        continue
                    if value in other_set and value not in segment_explicit_feeds[other_index]:
                        # 该值在其它段仅由模态继承出现（非显式写入）：从参照中剔除，
                        # 避免跨越段边界的同一写入被 gap=0 跳过。
                        others |= (other_set - {value})
                    else:
                        others |= other_set
                gaps = [_feed_gap(value, other) for other in others]
                if not gaps or not all(gap > FEED_BASE_TOL for gap in gaps):
                    continue
                level = "warning" if all(gap > 2 * FEED_BASE_TOL for gap in gaps) else "review"
                report_outlier(value, count, level, min(gaps), others,
                               "segment-gap", "其他段", index + 1)
    else:
        reference = set(reference_feeds)
        if reference:
            feed_outlier.reference_count = len(reference)
            for value in sorted(segment_counts[0]):
                count = explicit_counts[value]
                if count > FEED_RARE_MAX:
                    continue
                if axial_exempt(value):
                    continue
                gaps = [_feed_gap(value, other) for other in reference]
                if not gaps or not all(gap > FEED_BASE_TOL for gap in gaps):
                    continue
                level = "warning" if all(gap > 2 * FEED_BASE_TOL for gap in gaps) else "review"
                report_outlier(value, count, level, min(gaps), reference,
                               "cross-program-gap", "同目录其他程序", 1)
        else:
            feed_outlier.distribution = [
                {
                    "value": value,
                    "count": global_counts[value],
                    "first_line": min(
                        ln for ln, feed, _ef, _z, _xy, is_motion in rows
                        if is_motion and feed == value),
                    "note": "仅出现一次，请人工确认" if global_counts[value] == 1 else "",
                }
                for value in sorted(global_counts)
            ]

    # 硬边界：显式 F 越界（与 issues 中 feed-range 对应，仅记录，不重复告警）。
    for ln, _feed, explicit_f, _z, _xy, is_motion in rows:
        if explicit_f is None or not is_motion:
            continue
        if (explicit_f <= 0
                or (config.feed_min is not None and explicit_f < config.feed_min)
                or (config.feed_max is not None and explicit_f > config.feed_max)):
            feed_outlier.boundary_errors.append({
                "line": ln,
                "value": explicit_f,
                "reason": "out-of-range",
                "in_apt": matches_apt_feed(explicit_f),
                "text": line_text.get(ln, ""),
            })
    return issues, feed_outlier


def _parse_code_rows(text: str, start: int) -> List[dict]:
    """单遍解析正文行，供校验/F 检测/交叉校验共用（避免三次重复 ADDR 解析）。

    只做共享且昂贵的部分：code_part、地址解析（含 float）与模态 F；
    各消费方按需再跑轻量 M/G/T/结束标记等检查。
    """
    rows = []
    lines = text.replace("\r\n", "\n").split("\n")
    modal_feed = None
    for i, raw_line in enumerate(lines[start:], start=start + 1):
        code = code_part(raw_line)
        if not code.strip():
            continue
        upper_code = code.upper()
        addresses = []
        z_value = None
        has_xy = False
        explicit_f = None
        for parameter in ADDR_RE.finditer(code):
            key = parameter.group(1).upper()
            raw_value = parameter.group(2)
            value = float(raw_value)
            addresses.append((key, raw_value, value))
            if key == "Z":
                z_value = value
            elif key in "XY":
                has_xy = True
            elif key == "F":
                explicit_f = value
        if explicit_f is not None:
            modal_feed = explicit_f
        rows.append({
            "line": i,
            "raw": raw_line,
            "code": code,
            "upper": upper_code,
            "addr": addresses,
            "z": z_value,
            "xy": has_xy,
            "explicit_f": explicit_f,
            "feed": modal_feed,
            "motion": bool(has_xy or z_value is not None or MOTION_ANY_RE.search(code)),
        })
    return rows


def _parse_code_rows_cached(path: Path, encoding: str = "auto") -> List[dict]:
    """Parse code rows once per (path, mtime, size, encoding) for on-disk
    programs; reused across validation/F detection/APT cross-check and
    invalidated automatically when the file changes."""
    text, used_encoding, _ = _read_text_cached(path, encoding)
    stat = path.stat()
    key = str(path.resolve())
    cached = _cache_get(_ROWS_CACHE, key)
    if cached and cached[:3] == (stat.st_mtime_ns, stat.st_size, used_encoding):
        return cached[3]
    start = _header_end(text.replace("\r\n", "\n").split("\n"))
    rows = _parse_code_rows(text, start)
    _cache_put(_ROWS_CACHE, key,
               (stat.st_mtime_ns, stat.st_size, used_encoding, rows), 1000)
    return rows


def validate_program(text: str, filename: str, program: str, info: ProgramInfo, config: Config,
                     stats: Optional[Stats] = None, rows: Optional[List[dict]] = None) -> List[Issue]:
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
    first_cut: Optional[int] = None
    m03_pos: Optional[int] = None
    m04_pos: Optional[int] = None
    m05_pos: Optional[int] = None
    m08_pos: Optional[int] = None
    m09_pos: Optional[int] = None
    end_pos: Optional[int] = None
    for rec in rows if rows is not None else _parse_code_rows(text, start):
        i = rec["line"]
        raw_line = rec["raw"]
        code = rec["code"]
        upper_code = rec["upper"]
        line = raw_line.strip()
        # 只检查代码部分：括号注释与分号后注释内的引号不参与未闭合引号判定。
        if '"' in code and code.count('"') % 2:
            issues.append(Issue(filename, i, raw_line, "unclosed-quote", "error", "补全或删除未闭合引号"))
        if CONTROL_CHAR_RE.search(raw_line):
            issues.append(Issue(filename, i, raw_line, "control-character", "error", "删除异常控制字符"))
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
        for key, raw_value, value in rec["addr"]:
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
    if config.require_end_marker and not has_end:
        issues.append(Issue(filename, len(lines), "", "end-marker", "error", "添加 %、M30 或 M02 结束标记"))
    if config.require_spindle_speed and not has_s:
        issues.append(Issue(filename, start + 1, "", "spindle-speed", "error", "切削前应有 S 转速"))
    if not has_m03:
        if config.auto_m03:
            # 正文以 M04 反转启动时禁止补写 M03（正反转冲突），按错误阻止输出；
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
    # 启用自动添加换刀时，多刀程序无法自动生成首刀换刀指令，按警告提示人工确认。
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
    # F 离群检测由 analyze_program 调用 detect_feed_outliers 独立完成，本函数不参与。
    if config.multiple_spindle_warn:
        distinct_spindle: Dict[float, Tuple[int, str, str]] = {}
        for line_no, raw_value, value, raw_line in spindle_values:
            distinct_spindle.setdefault(value, (line_no, raw_value, raw_line))
        if len(distinct_spindle) > 1:
            values = ", ".join(raw for _line, raw, _text in distinct_spindle.values())
            first = next(iter(distinct_spindle.values()))
            issues.append(Issue(filename, first[0], first[2], "multiple-spindle-speeds", "warning",
                                f"程序包含多个不同 S 值（{values}），请确认转速切换是否符合工艺要求"))
    return issues

def analyze_program(text: str, filename: str, program: str, info: ProgramInfo, config: Config,
                    apt_meta: Optional[AptMeta] = None,
                    feed_reference: Sequence[float] = (),
                    rows: Optional[List[dict]] = None) -> Tuple[Stats, List[Issue], FeedOutlierData]:
    """Calculate statistics and validation issues in one body traversal.

    F 离群检测独立于基础校验：统计与语法/参数检查走 validate_program，
    分段对比检测由 detect_feed_outliers 完成，两者都只读正文不写盘。
    """
    stats = _new_stats()
    parsed_rows = rows if rows is not None else _parse_code_rows(
        text, _header_end(text.replace("\r\n", "\n").split("\n")))
    issues = validate_program(text, filename, program, info, config, stats=stats, rows=parsed_rows)
    apt_feeds = [float(feed) for feed, _units in apt_meta.feeds] if apt_meta and apt_meta.feeds else []
    feed_issues, feed_outlier = detect_feed_outliers(
        text, filename, config, apt_feeds=apt_feeds, reference_feeds=feed_reference,
        rows=parsed_rows)
    issues.extend(feed_issues)
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
    parsed_rows = _parse_code_rows(
        f.output_text, _header_end(f.output_text.replace("\r\n", "\n").split("\n")))
    f.stats, validation_issues, f.feed_outlier = analyze_program(
        f.output_text, f.source, f.program, effective, config, apt_meta=f.apt_meta,
        rows=parsed_rows)
    f.issues = header_issues + validation_issues
    # WP-A4：APT 规划 ↔ MPF 执行交叉校验。
    if f.apt_meta is not None:
        f.issues.extend(crosscheck_apt(
            f.output_text, f.apt_meta, f.source, config, apt_tools=effective.tools,
            rows=parsed_rows))


def _config_cache_signature(config: Config) -> str:
    """配置签名：所有字段归一化排序，保证任一配置变化都失效缓存。"""
    items = []
    for key in sorted(config.__dict__):
        value = config.__dict__[key]
        if isinstance(value, (set, frozenset)):
            value = tuple(sorted(value))
        elif isinstance(value, list):
            value = tuple(value)
        items.append((key, value))
    return repr(items)


def _info_cache_signature(info: ProgramInfo) -> str:
    """头部信息签名：编制/审核/图号/版次/机床/控制系统/日期 + 刀具列表。"""
    tools = tuple((tool.number, tool.dia, tool.tool_coner, tool.tool_type, tool.tool_angle)
                  for tool in info.tools)
    return repr((info.bianzhi, info.shenhe, info.drawing_number, info.part_version,
                 info.nc_machine, info.control_system, info.date, tools))



def _analysis_cache_key(directory: Path, f: FilePlan, info: ProgramInfo, config: Config,
                        latest_apt: Dict[str, Tuple[float, FilePlan]],
                        auto_tools: Dict[str, Tuple[float, List[ToolInfo]]],
                        feed_reference: Dict[str, frozenset],
                        tool_overrides: Dict[str, List[ToolInfo]]) -> str:
    """Single-file analysis cache key.

    Covers the source identity (path/mtime/size/encoding), program name,
    header info, config, the paired APT state, the cross-program reference
    and tool overrides, so any relevant change invalidates the entry.
    """
    try:
        source_path = directory / f.source
        stat = source_path.stat()
        source_id = (str(source_path.resolve()), stat.st_mtime_ns, stat.st_size, f.encoding)
    except OSError:
        source_id = (f.source, 0, 0, f.encoding)
    apt_plan = latest_apt.get(f.program)
    apt_id = None
    if apt_plan:
        try:
            apt_stat = apt_plan[1].modified_time or Path(apt_plan[1].source).stat().st_mtime
        except OSError:
            apt_stat = 0.0
        apt_id = (apt_plan[1].source, apt_stat, apt_plan[1].encoding)
    tools_id = tuple(
        (program, tuple((tool.number, tool.dia, tool.tool_coner, tool.tool_type, tool.tool_angle)
                        for tool in tools))
        for program, (_mtime, tools) in sorted(auto_tools.items()))
    reference_id = tuple(sorted(
        (source, tuple(sorted(feeds)))
        for source, feeds in feed_reference.items()
        if isinstance(feeds, frozenset) and feeds))
    overrides_id = tuple(
        (program, tuple((tool.number, tool.dia, tool.tool_coner, tool.tool_type, tool.tool_angle)
                        for tool in tools))
        for program, tools in sorted(tool_overrides.items()))
    return repr((source_id, f.program, _info_cache_signature(info),
                 _config_cache_signature(config), apt_id, tools_id, reference_id, overrides_id))


def _restore_cached_analysis(f: FilePlan, cached: dict) -> None:
    """Apply a cached analysis result to an MPF plan in place."""
    f.output_text = cached["output_text"]
    f.changes = list(cached["changes"])
    f.stats = cached["stats"]
    f.issues = list(cached["issues"])
    f.feed_outlier = cached["feed_outlier"]
    f.target = cached["target"]
    f.parsed_tools = list(cached["parsed_tools"])
    f.auto_tool_change_skipped = cached.get("auto_tool_change_skipped", "")
    f.apt_meta = cached.get("apt_meta")
    f.apt_toolpath = cached.get("apt_toolpath")
    f.apt_source_path = cached.get("apt_source_path")
    f.apt_encoding = cached.get("apt_encoding", "")



def analyze_plan_file(f: FilePlan, directory: Path, info: ProgramInfo, config: Config,
                      latest_apt: Dict[str, Tuple[float, FilePlan]],
                      auto_tools: Dict[str, Tuple[float, List[ToolInfo]]],
                      feed_reference: Dict[str, frozenset],
                      tool_overrides: Dict[str, List[ToolInfo]],
                      mpf_sources: Optional[Sequence[FilePlan]] = None) -> None:
    """Single-file deep analysis: header/tool/M03/validation/F detection/APT cross-check.

    Shared by build_plan full processing and the GUI progressive background
    analysis.  A matching APT's metadata/toolpath/tools are parsed on demand
    (cached), so the light plan phase stays fast.
    """
    if f.kind != "mpf" or not f.program or f.original_text is None:
        return
    if not feed_reference.get("__ready__"):
        # Build the cross-program common-gear reference once per scan: the
        # light plan defers this so the file list appears immediately.
        sources = mpf_sources or []
        if len(sources) > 1:
            def feed_set_for(item):
                try:
                    return _explicit_feed_set_cached(directory / item.source, config.encoding)
                except OSError:
                    # 计划可能来自内存构造（如测试/手动计划），源文件不存在：用原文兜底。
                    return _explicit_feed_set(item.original_text)
            feed_reference.update(build_feed_reference([
                (item.source, feed_set_for(item)) for item in sources
            ]))
        feed_reference["__ready__"] = True
    cache_key = _analysis_cache_key(directory, f, info, config, latest_apt, auto_tools,
                                    feed_reference, tool_overrides)
    cached = _cache_get(_ANALYSIS_CACHE, cache_key)
    if cached is not None:
        _restore_cached_analysis(f, cached)
        return
    apt_plan = latest_apt.get(f.program)
    if apt_plan and (f.program not in auto_tools or apt_plan[1].apt_meta is None):
        # Progressive mode: the newest paired APT was not pre-parsed, so
        # parse it on demand here (the per-file cache absorbs repeats).
        try:
            meta, toolpath, tools = _extract_apt_data_cached(
                directory / apt_plan[1].source, config.encoding)
            apt_plan[1].apt_meta = meta
            apt_plan[1].apt_toolpath = toolpath
            apt_plan[1].parsed_tools = list(tools)
            auto_tools[f.program] = (apt_plan[0], list(tools))
        except Exception:
            pass
    try:
        effective_info = program_defaults(f.original_text, info)
        # Copy the newest APT metadata/toolpath/source path onto the MPF
        # plan (read-only references shared across worker threads); when an
        # APT exists, DATE prefers the APT generation time.
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
            # APT describes the actual generated cutter geometry and takes
            # precedence over both stale Tn rows in the MPF and saved
            # special_tools.json values.
            effective_info.tools = list(auto_tools[f.program][1])
        elif replace_tools:
            effective_info.tools = list(tool_overrides[f.program])
        elif not effective_info.tools:
            # With no current APT, fall back to saved special-tool values
            # (passed as tool_overrides), then MPF rows.
            effective_info.tools = list(tool_overrides.get(f.program, [])) or extract_tools(f.original_text)
        new, changes, header_issues = apply_header(
            f.original_text, f.program, effective_info, config,
            replace_tools=replace_tools, filename=f.source)
        new, tool_changed, tool_note = add_initial_tool_change(new, effective_info.tools, config)
        if tool_note:
            changes.append(tool_note)
            if not tool_changed:
                f.auto_tool_change_skipped = tool_note
        new, m03_changed, m03_note = add_m03(new, config)
        if m03_changed:
            changes.append(m03_note)
        # WP-B2/A4: DATE is refreshed only when the program actually changed
        # (APT generation time when available, otherwise change time).
        if changes:
            new, date_changed = update_header_date(new, apt_date or format_nc_date())
            if date_changed:
                changes.append("更新 DATE")
        f.output_text, f.changes = new, changes
        if new == f.original_text:
            # No header/tool/M03 changes: reuse the source rows parsed once
            # per scan (cached by path/mtime/size) instead of re-parsing.
            parsed_rows = _parse_code_rows_cached(directory / f.source, config.encoding)
        else:
            parsed_rows = _parse_code_rows(
                new, _header_end(new.replace("\r\n", "\n").split("\n")))
        f.stats, validation_issues, f.feed_outlier = analyze_program(
            new, f.source, f.program, info, config, apt_meta=f.apt_meta,
            feed_reference=feed_reference.get(f.source, frozenset()),
            rows=parsed_rows)
        f.target = str(directory / (f.program + config.program_output_extension))
        # Cache the effective tools for reprocess_file/apply-selected so a
        # re-run does not drop the cutter rows.
        f.parsed_tools = list(effective_info.tools)
        f.issues.extend(header_issues)
        f.issues.extend(validation_issues)
        # WP-A4: APT plan -> MPF execution cross-check.
        if f.apt_meta is not None:
            f.issues.extend(crosscheck_apt(
                new, f.apt_meta, f.source, config,
                apt_tools=auto_tools[f.program][1] if f.program in auto_tools else (),
                rows=parsed_rows))
        if Path(f.source).name != Path(f.target).name:
            f.changes.append(f"重命名为 {Path(f.target).name}")
        # Recognition process data goes to the runtime log: tool results.
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
        # Recognition process data goes to the runtime log: issues summary.
        error_issues = [i for i in f.issues if i.severity == "error"]
        warning_issues = [i for i in f.issues if i.severity == "warning"]
        if error_issues or warning_issues:
            kind_list = sorted({i.kind for i in f.issues})
            emit_event("warning" if error_issues else "info", "issues_found",
                       f"识别异常与错误：{f.source}（错误 {len(error_issues)} 条、警告 {len(warning_issues)} 条）",
                       detail="、".join(kind_list))
        _cache_put(_ANALYSIS_CACHE, cache_key, {
            "output_text": f.output_text,
            "changes": list(f.changes),
            "stats": f.stats,
            "issues": list(f.issues),
            "feed_outlier": f.feed_outlier,
            "target": f.target,
            "parsed_tools": list(f.parsed_tools),
            "auto_tool_change_skipped": f.auto_tool_change_skipped,
            "apt_meta": f.apt_meta,
            "apt_toolpath": f.apt_toolpath,
            "apt_source_path": f.apt_source_path,
            "apt_encoding": f.apt_encoding,
        }, _ANALYSIS_CACHE_MAX)
    except Exception as e:
        emit_event("error", "error", f"处理文件失败：{f.source}", detail=traceback.format_exc())
        f.issues.append(Issue(f.source, 1, "", "processing", "error", str(e)))


def _prepare_plan_context(scan: ScanResult, config: Config) -> Dict[str, object]:
    """Collect the newest APT per program, automatic tool defaults, and the
    cross-program feed reference used by single-segment programs.

    APT metadata/toolpath parsing is always deferred to analyze_plan_file,
    which parses a matching APT on demand (merged single pass, cached by
    mtime/size).  The light plan phase therefore stays fast in both modes,
    and full analysis runs inside the same worker pool instead of a serial
    pre-pass (2026-08-08 perf: cold full scan 5.5s -> ~4.1s on 25 programs).
    """
    directory = Path(scan.input_dir)
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
                    tools = _extract_apt_tools_from_path(
                        directory / apt_plan.source, config.encoding)
                apt_plan.parsed_tools = list(tools)
            auto_tools[program] = (mtime, tools)
        except Exception:
            apt_plan.apt_meta = None
            apt_plan.apt_toolpath = None
            auto_tools[program] = (mtime, [])
    # Cross-program common-gear reference for single-segment programs is
    # computed lazily by analyze_plan_file (first call) so the light plan
    # phase stays as fast as possible.
    feed_reference: Dict[str, frozenset] = {}
    return {
        "directory": directory,
        "latest_apt": latest_apt,
        "auto_tools": auto_tools,
        "feed_reference": feed_reference,
        "mpf_sources": [f for f in scan.files
                        if f.kind == "mpf" and f.original_text is not None],
    }


def _resolve_plan_targets(scan: ScanResult, config: Config) -> None:
    """Resolve APTSOURCE archive/delete actions and target collisions.

    Runs identically in light and full modes so the plan shown before deep
    analysis already matches what process_plan will execute.
    """
    directory = Path(scan.input_dir)
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


def build_plan(scan: ScanResult, info: Optional[ProgramInfo] = None,
               config: Optional[Config] = None,
               tool_overrides: Optional[Dict[str, List[ToolInfo]]] = None,
               analyze: bool = True) -> ScanResult:
    """Build the processing plan for a scanned directory.

    analyze=False performs a light plan: file lists, APTSOURCE targets and
    duplicate resolution are ready, but the per-file deep analysis
    (header/tool/M03/validation/F detection) is deferred.  The light result
    carries analyze_context so the GUI can run analyze_plan_file per file in
    the background and keep the UI responsive.  In full mode (analyze=True)
    the same on-demand APT path is used, so both modes share one analysis
    pipeline and the whole directory analysis stays inside the worker pool.
    """
    info = info or ProgramInfo()
    config = config or Config()
    tool_overrides = tool_overrides or {}
    context = _prepare_plan_context(scan, config)
    emit_event("info", "plan_built",
               f"生成处理计划：{len(scan.files)} 个文件，MPF {sum(f.kind == 'mpf' for f in scan.files)} 个，"
               f"APTSOURCE {sum(f.kind == 'aptsource' for f in scan.files)} 个"
               + ("" if analyze else "（轻量计划，深度分析待后台完成）"))
    if analyze:
        latest_apt = context["latest_apt"]
        auto_tools = context["auto_tools"]
        feed_reference = context["feed_reference"]

        def process_mpf(f: FilePlan):
            analyze_plan_file(f, context["directory"], info, config, latest_apt,
                              auto_tools, feed_reference, tool_overrides,
                              context["mpf_sources"])

        # GIL 下纯计算线程并行无收益且引入调度开销（2026-08-08 实测：
        # 4 线程并行区 3.96s > 串行等价 3.45s），统一串行逐文件分析。
        for item in scan.files:
            if item.kind == "mpf" and item.program and item.original_text is not None:
                process_mpf(item)
    _resolve_plan_targets(scan, config)
    scan.analyze_context = {
        "tool_overrides": tool_overrides,
    }
    scan.analyze_context.update(context)
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
        "overwrite_existing": config.overwrite_existing,
        "delete_extensions": sorted(config.delete_extensions),
        "program_extensions": sorted(config.program_extensions),
        "program_output_extension": config.program_output_extension,
        "aptsource_dir": config.aptsource_dir,
        "allowed_name_pattern": config.allowed_name_pattern,
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
                   apt_tools: Sequence[ToolInfo] = (),
                   rows: Optional[List[dict]] = None) -> List[Issue]:
    """APT 规划信息与 MPF 执行指令交叉校验。

    主轴方向不一致为 error（CLW→M03、CCLW→M04，含 M03+M04 双方向取舍建议）；
    S/F 数值容差、冷却液、刀具装夹/几何参数、程序名冲突为 warning。
    APT 为规划值，后处理可能取整/倍率，故一律容差。
    """
    issues: List[Issue] = []
    lines = mpf_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    start = _header_end(lines)
    parsed_rows = rows if rows is not None else _parse_code_rows(
        mpf_text, start)
    s_values = []
    f_values = []
    has_m03 = has_m04 = has_m08 = has_m09 = False
    t_calls = set()
    s_lines = {}
    f_lines = {}
    m03_line = m04_line = m08_line = m09_line = ""
    t_lines = []
    first_code_line = ""
    for rec in parsed_rows:
        raw_line = rec["raw"]
        code = rec["code"]
        has_m = "M" in rec["upper"]
        if not first_code_line:
            first_code_line = raw_line
        if has_m and M03_RE.search(code):
            has_m03 = True
            if not m03_line:
                m03_line = raw_line
        if has_m and M04_RE.search(code):
            has_m04 = True
            if not m04_line:
                m04_line = raw_line
        if has_m and M08_RE.search(code):
            has_m08 = True
            if not m08_line:
                m08_line = raw_line
        if has_m and M09_RE.search(code):
            has_m09 = True
            if not m09_line:
                m09_line = raw_line
        if "T" in rec["upper"]:
            for match in TOOL_CALL_RE.finditer(code):
                number = int(match.group(1))
                t_calls.add(number)
                t_lines.append((number, raw_line))
        for key, _raw_value, value in rec["addr"]:
            if key == "S":
                s_values.append(value)
                s_lines.setdefault(value, raw_line)
            elif key == "F":
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
    # 2026-08-08 报告完善：运行环境（纯 sys 实现，不引入新依赖）与扫描分类统计。
    report.environment = {
        "platform": sys.platform,
        "python_version": ".".join(str(part) for part in sys.version_info[:3]),
        "machine": "64" if sys.maxsize > 2 ** 32 else "32",
    }
    report.scan_stats = {
        "total": len(scan.files),
        "mpf": sum(f.kind == "mpf" for f in scan.files),
        "aptsource": sum(f.kind == "aptsource" for f in scan.files),
        "intermediate": sum(f.kind == "intermediate" for f in scan.files),
    }
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
        item = {"file": f.source, "action": f.action, "program": f.program, "encoding": f.encoding, "target": f.target or "", "program_name_source": f.program_name_source or "", "changes": f.changes, "diff": diff, "issues": [asdict(x) for x in f.issues], "stats": f.stats.as_dict() if f.stats else None, "apt_meta": f.apt_meta.to_dict() if f.apt_meta else None, "toolpath_stats": f.apt_toolpath.to_dict() if f.apt_toolpath else None, "feed_outlier": f.feed_outlier.to_dict() if f.feed_outlier else None, "header": extract_header_fields(f.output_text or ""), "auto_tool_change_skipped": f.auto_tool_change_skipped or "", "duplicate_winner": f.duplicate_winner or "", "duplicate_target": f.duplicate_target or ""}
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
