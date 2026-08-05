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
    # F/S 数值上下限（None = 不检查）；越界按 error 上报。
    feed_min: Optional[float] = None
    feed_max: Optional[float] = None
    spindle_min: Optional[float] = None
    spindle_max: Optional[float] = None
    # 换行策略：auto（跟随源文件）/ crlf / lf。
    newline: str = "auto"
    # 辅助指令顺序规则集合（默认空 = 全部关闭）：
    #   m03-before-motion  M03 先于首次切削运动（error）
    #   m05-before-end     M05 先于程序结束（warning）
    #   m08-before-cut     M08 先于首次切削（warning）
    #   m09-before-end     M09 先于程序结束（warning，M09 未出现时不提示）
    aux_checks: set = field(default_factory=set)
    # 启发式校验阈值（WP-10，仅本次运行生效，GUI 校验规则页可调）。
    # F 离群按工艺阶段（移动/进刀/切削）分组检测：组内出现 ≥2 次的值构成常见
    # 进给档位（多模态正常），仅检查出现 1 次的孤立值是否低于最低档 ×
    # low_ratio（默认 0.1）或高于最高档 × high_ratio（默认 3 倍）；
    # 组内无常见档位时回退 IQR 极端值标准（k=3）。
    feed_outlier_iqr_factor: float = 3.0
    # 低值口径：孤立值低于最低常见档位 × low_ratio；IQR 回退时低于 Q1 × low_ratio。
    feed_outlier_low_ratio: float = 0.1
    # 高值口径：孤立值高于最高常见档位 × high_ratio。
    feed_outlier_high_ratio: float = 3.0
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
    - snapshot() 供报告内嵌；发生丢弃时追加一条截断说明（含 log_path）；
    - 不生成任何磁盘日志文件（WP-R4）：最终报告即单个 JSON，运行日志完整内嵌其中。
    """

    def __init__(self, max_events: int = MAX_LOG_EVENTS):
        self._events: "deque[RuntimeEvent]" = deque(maxlen=max_events)
        self._dropped = 0
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
            if self._dropped:
                entries.append({
                    "time": datetime.now().isoformat(timespec="seconds"),
                    "level": "warning",
                    "event": "warning",
                    "message": f"运行日志已截断：报告内嵌日志仅保留最近 {self._events.maxlen} 条事件",
                    "detail": "",
                })
            return entries

    def clear(self) -> None:
        with self._lock:
            self._events.clear()
            self._dropped = 0


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
                pass
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
    emit_event("info", "scan_finish", f"扫描完成：{len(files)} 个文件，MPF {sum(f.kind == 'mpf' for f in files)} 个")
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
        # 只替换括号注释前的代码部分，注释中的 T 号（如 (T2 备用)）保持原样。
        code, separator, comment = line.partition("(")
        corrected.append(TOOL_REF_RE.sub("T" + str(number), code) + (separator + comment if separator else ""))

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
        if stripped and stripped != "%" and not stripped.startswith("("):
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
        if stripped and stripped != "%" and not stripped.startswith("("):
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


def validate_program(text: str, filename: str, program: str, info: ProgramInfo, config: Config, stats: Optional[Stats] = None) -> List[Issue]:
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
    stage_feeds: Dict[str, List[Tuple[int, str, float, str]]] = {"move": [], "plunge": [], "cut": []}
    current_z: Optional[float] = None
    first_cut: Optional[int] = None
    m03_pos: Optional[int] = None
    m04_pos: Optional[int] = None
    m05_pos: Optional[int] = None
    m08_pos: Optional[int] = None
    m09_pos: Optional[int] = None
    end_pos: Optional[int] = None
    for i, raw_line in enumerate(lines[start:], start=start + 1):
        line = raw_line.strip()
        if not line:
            continue
        if '"' in raw_line and raw_line.count('"') % 2:
            issues.append(Issue(filename, i, raw_line, "unclosed-quote", "error", "补全或删除未闭合引号"))
        if CONTROL_CHAR_RE.search(raw_line):
            issues.append(Issue(filename, i, raw_line, "control-character", "error", "删除异常控制字符"))
        code = code_part(raw_line)
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
        if z_value is not None:
            current_z = z_value
        if g00_matches or (current_z is not None and current_z >= config.retract_z_threshold):
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
    # F 离群：按工艺阶段（移动/进刀/切削）分组检测。NC 程序的 F 档位与阶段强相关
    # （切削 900、抬刀 5000、进刀 300 等），把全部 F 混在一起统计会互相污染。
    # 组内出现 ≥2 次的值构成“常见档位”，只检查组内出现 1 次的孤立值是否低于
    # 最低常见档位 × low_ratio 或高于最高常见档位 × high_ratio；
    # 组内无常见档位（F 值连续变化）时回退 IQR 极端值标准（k=3）。
    # 阶段收集已在主遍历完成（WP-P1），此处仅做离群判定。
    stage_labels = {"move": "移动/退刀", "plunge": "进刀", "cut": "切削"}
    for stage, items in stage_feeds.items():
        if len(items) < 3:
            continue
        counts = Counter(item[2] for item in items)
        frequent = sorted(value for value, count in counts.items() if count >= 2)
        if frequent:
            median_feed = frequent[len(frequent) // 2]
            low_bound = frequent[0] * config.feed_outlier_low_ratio
            high_bound = frequent[-1] * config.feed_outlier_high_ratio
            for line_no, raw_value, value, raw_line in items:
                if counts[value] > 1:
                    continue
                if value < low_bound:
                    issues.append(Issue(filename, line_no, raw_line, "feed-outlier", "warning", f"F{raw_value} 明显低于本程序{stage_labels[stage]}进给档位范围（约 {median_feed:g}），请确认"))
                elif value > high_bound:
                    issues.append(Issue(filename, line_no, raw_line, "feed-outlier", "warning", f"F{raw_value} 远高于本程序{stage_labels[stage]}进给档位范围（约 {median_feed:g}），请确认"))
        else:
            sorted_feeds = sorted(item[2] for item in items)
            median_feed = sorted_feeds[len(sorted_feeds) // 2]
            if median_feed <= 0:
                continue
            q1 = _quantile(sorted_feeds, 0.25)
            q3 = _quantile(sorted_feeds, 0.75)
            iqr = q3 - q1
            if iqr > 0:
                low_bound = max(q1 - config.feed_outlier_iqr_factor * iqr, q1 * config.feed_outlier_low_ratio)
                high_bound = q3 + config.feed_outlier_iqr_factor * iqr
            else:
                low_bound = median_feed * config.feed_outlier_low_ratio
                high_bound = median_feed * config.feed_outlier_high_ratio
            for line_no, raw_value, value, raw_line in items:
                if value < low_bound:
                    issues.append(Issue(filename, line_no, raw_line, "feed-outlier", "warning", f"F{raw_value} 明显低于本程序{stage_labels[stage]}进给范围（约 {median_feed:g}），请确认"))
                elif value > high_bound:
                    issues.append(Issue(filename, line_no, raw_line, "feed-outlier", "warning", f"F{raw_value} 远高于本程序{stage_labels[stage]}进给范围（约 {median_feed:g}），请确认"))
    distinct_spindle: Dict[float, Tuple[int, str, str]] = {}
    if config.multiple_spindle_warn:
        for line_no, raw_value, value, raw_line in spindle_values:
            distinct_spindle.setdefault(value, (line_no, raw_value, raw_line))
        if len(distinct_spindle) > 1:
            values = ", ".join(raw for _line, raw, _text in distinct_spindle.values())
            first = next(iter(distinct_spindle.values()))
            issues.append(Issue(filename, first[0], first[2], "multiple-spindle-speeds", "warning", f"程序包含多个不同 S 值（{values}），请确认转速切换是否符合工艺要求"))
    return issues


def analyze_program(text: str, filename: str, program: str, info: ProgramInfo, config: Config) -> Tuple[Stats, List[Issue]]:
    """Calculate statistics and validation issues in one body traversal."""
    stats = _new_stats()
    issues = validate_program(text, filename, program, info, config, stats=stats)
    return stats, issues


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
    # WP-B2：程序发生实际变更时 DATE 自动更新为变更发生时间。
    if f.changes:
        f.output_text, date_changed = update_header_date(f.output_text, format_nc_date())
        if date_changed:
            f.changes.append("更新 DATE")
    f.stats, validation_issues = analyze_program(f.output_text, f.source, f.program, effective, config)
    f.issues = header_issues + validation_issues


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
            auto_tools[program] = (mtime, tools)
        except Exception:
            auto_tools[program] = (mtime, [])
    emit_event("info", "plan_built", f"生成处理计划：{len(scan.files)} 个文件，MPF {sum(f.kind == 'mpf' for f in scan.files)} 个")
    def process_mpf(f: FilePlan):
        if f.kind == "mpf" and f.program and f.original_text is not None:
            try:
                effective_info = program_defaults(f.original_text, info)
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
                # WP-B2：程序发生实际变更时 DATE 自动更新为变更发生时间。
                if changes:
                    new, date_changed = update_header_date(new, format_nc_date())
                    if date_changed:
                        changes.append("更新 DATE")
                f.output_text, f.changes = new, changes
                f.stats, validation_issues = analyze_program(new, f.source, f.program, info, config)
                f.target = str(directory / (f.program + config.program_output_extension))
                # 缓存本次生效的刀具信息，供 reprocess_file/应用所选回退，避免刷掉刀具。
                f.parsed_tools = list(effective_info.tools)
                f.issues.extend(header_issues)
                f.issues.extend(validation_issues)
                if Path(f.source).name != Path(f.target).name:
                    f.changes.append(f"重命名为 {Path(f.target).name}")
            except Exception as e:
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
        "feed_outlier_iqr_factor": config.feed_outlier_iqr_factor,
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
        emit_event("info", "backup_created", f"处理前备份已创建：{backup_root}")
    successful_targets = set()
    ordered_files = sorted(scan.files, key=lambda item: item.action == "duplicate")
    total = len(ordered_files)
    emit_event("info", "process_start", f"开始处理：{total} 个文件")
    for index, f in enumerate(ordered_files, start=1):
        if progress_callback is not None:
            progress_callback(index, total, f.source)
        emit_event("info", "process_file", f"处理文件：{f.source}（{index}/{total}）")
        if f.stats is None and f.output_text is not None:
            f.stats = calculate_stats(f.output_text)
        diff = []
        if f.original_text is not None and f.output_text is not None and f.original_text != f.output_text:
            diff = list(difflib.unified_diff(f.original_text.splitlines(), f.output_text.splitlines(), fromfile=f.source + " (before)", tofile=(Path(f.target).name if f.target else f.source) + " (after)", lineterm=""))
        item = {"file": f.source, "action": f.action, "program": f.program, "encoding": f.encoding, "target": f.target or "", "program_name_source": f.program_name_source or "", "changes": f.changes, "diff": diff, "issues": [asdict(x) for x in f.issues], "stats": f.stats.as_dict() if f.stats else None}
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
            emit_event("error", "error", f"处理文件失败：{f.source}", detail=traceback.format_exc())
        except PermissionError as e:
            report.failed += 1
            item["status"] = "failed"
            item["error_kind"] = "permission"
            item.setdefault("runtime_error", str(e))
            emit_event("error", "error", f"处理文件失败：{f.source}", detail=traceback.format_exc())
        except OSError as e:
            report.failed += 1
            item["status"] = "failed"
            item["error_kind"] = "io"
            item.setdefault("runtime_error", str(e))
            emit_event("error", "error", f"处理文件失败：{f.source}", detail=traceback.format_exc())
        except Exception as e:
            report.failed += 1
            item["status"] = "failed"
            item["error_kind"] = "other"
            item.setdefault("runtime_error", str(e))
            emit_event("error", "error", f"处理文件失败：{f.source}", detail=traceback.format_exc())
        report.files.append(item)
    report.finished_at = datetime.now().isoformat(timespec="seconds")
    report.elapsed_seconds = _iso_seconds_delta(report.started_at, report.finished_at)
    emit_event("info", "process_finish",
               f"处理完成：成功 {report.success}，失败 {report.failed}，移动 {report.moved}，删除 {report.deleted}，跳过 {report.skipped}")
    report.refresh_runtime_log()
    return report
