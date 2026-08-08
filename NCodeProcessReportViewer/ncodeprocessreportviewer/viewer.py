from __future__ import annotations

import csv
import json
import re
import sys
import tkinter as tk
import tkinter.font as tkfont
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Iterable, List, Optional, Tuple


REPORT_PATTERNS = ("ncodeprocess-report-*.json", "ncpostprocess-report-*.json")
DATA_DIR_NAMES = ("NCodeProcessData", "NCPostProcessData")
PARAMETERS = ("F", "S", "X", "Y", "Z")
# 鼠标悬停在单元格上多久后弹出内容提示（毫秒）。
CELL_TOOLTIP_DELAY_MS = 1500
# 2026-08-08 报告完善：config_snapshot 键 → 中文标签（顺序即报告输出顺序）。
CONFIG_SNAPSHOT_LABELS = (
    ("encoding", "文件编码"),
    ("recursive", "递归扫描"),
    ("save_aptsource", "保存 APTSOURCE"),
    ("overwrite_fields", "覆盖已有字段"),
    ("overwrite_existing", "允许覆盖目标"),
    ("delete_extensions", "待删除扩展名"),
    ("program_extensions", "主程序扩展名"),
    ("program_output_extension", "输出扩展名"),
    ("aptsource_dir", "APTSOURCE 归档目录"),
    ("allowed_name_pattern", "程序名允许字符"),
    ("g00_level", "G00 级别"),
    ("auto_m03", "自动补写 M03"),
    ("auto_tool_change", "自动添加换刀"),
    ("m03_position", "M03 补写位置"),
    ("feed_min", "F 下限"),
    ("feed_max", "F 上限"),
    ("spindle_min", "S 下限"),
    ("spindle_max", "S 上限"),
    ("newline", "换行策略"),
    ("required_fields", "必填 MSG 字段"),
    ("aux_checks", "辅助指令顺序"),
    ("multiple_spindle_warn", "多 S 值警告"),
    ("require_end_marker", "要求结束标记"),
    ("require_m06", "要求 M06"),
    ("require_spindle_speed", "要求 S 转速"),
    ("max_file_size", "单文件大小上限"),
    ("max_files", "扫描文件数量上限"),
    ("retract_z_threshold", "抬刀高度阈值"),
    ("ask_backup", "处理前询问备份"),
)


def _config_value_text(value) -> str:
    """配置值展示：布尔转是/否，列表转顿号分隔，空值显示为空。"""
    if value is None or value == "":
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (list, tuple)):
        return "、".join(str(item) for item in value) if value else ""
    return str(value)


class CellTooltip:
    """A small always-on-top window that shows a cell's full content."""

    def __init__(self, master):
        self.window = tk.Toplevel(master)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.label = ttk.Label(
            self.window,
            background="#ffffe0",
            foreground="#333333",
            relief="solid",
            borderwidth=1,
            padding=(4, 2),
            justify="left",
        )
        self.label.pack()

    def show(self, text, x, y):
        """Position the window next to the cursor and make it visible."""
        self.label.configure(text=text)
        self.window.update_idletasks()
        width = self.window.winfo_reqwidth()
        height = self.window.winfo_reqheight()
        screen_w = self.window.winfo_screenwidth()
        screen_h = self.window.winfo_screenheight()
        x = min(x + 12, screen_w - width - 4)
        y = min(y + 14, screen_h - height - 4)
        self.window.geometry(f"+{max(4, x)}+{max(4, y)}")
        self.window.deiconify()
        self.window.lift()

    def hide(self):
        self.window.withdraw()


def application_directory() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def discover_reports(directory: Path) -> List[Path]:
    """Find reports beside the viewer and in the standard data directory."""
    directory = Path(directory)
    candidates = set()
    folders = (directory,) + tuple(directory / name for name in DATA_DIR_NAMES)
    for folder in folders:
        if folder.is_dir():
            for pattern in REPORT_PATTERNS:
                candidates.update(path.resolve() for path in folder.glob(pattern) if path.is_file())
    return sorted(candidates, key=lambda path: (path.stat().st_mtime, path.name), reverse=True)


def load_report(path: Path) -> dict:
    """Load and minimally validate one report JSON document."""
    path = Path(path)
    with path.open("r", encoding="utf-8-sig") as stream:
        data = json.load(stream)
    if not isinstance(data, dict):
        raise ValueError("报告根节点必须是 JSON 对象")
    files = data.get("files", [])
    if not isinstance(files, list):
        raise ValueError("报告中的 files 必须是数组")
    data["files"] = [item for item in files if isinstance(item, dict)]
    return data


def report_summary(data: dict) -> List[Tuple[str, str]]:
    labels = (
        ("成功", "success"), ("失败", "failed"), ("跳过", "skipped"),
        ("移动", "moved"), ("删除", "deleted"), ("警告", "warnings"), ("错误", "errors"),
    )
    return [(label, str(data.get(key, 0) or 0)) for label, key in labels]


def file_issue_counts(item: dict) -> Tuple[int, int, int]:
    issues = item.get("issues") or []
    errors = sum(issue.get("severity") == "error" for issue in issues if isinstance(issue, dict))
    warnings = sum(issue.get("severity") == "warning" for issue in issues if isinstance(issue, dict))
    return errors, warnings, len(issues)


def iter_stats_rows(data: dict, selected_file: Optional[dict] = None) -> Iterable[Tuple[str, str, str, str, str, str]]:
    """Yield display rows for per-file parameter statistics."""
    items = [selected_file] if selected_file is not None else data.get("files", [])
    for item in items:
        if not isinstance(item, dict) or not item.get("stats"):
            continue
        stats = item.get("stats") or {}
        counts = stats.get("counts") or {}
        minimum = stats.get("minimum") or {}
        maximum = stats.get("maximum") or {}
        name = str(item.get("file") or item.get("program") or "")
        for parameter in PARAMETERS:
            if parameter not in counts and parameter not in minimum and parameter not in maximum:
                continue
            yield (
                name,
                parameter,
                str(counts.get(parameter, 0)),
                format_number(minimum.get(parameter)),
                format_number(maximum.get(parameter)),
                "否",
            )
        yield (name, "G00", str(stats.get("g00_count", 0) or 0), "", "", "发现" if stats.get("g00_count", 0) else "未发现")


def format_number(value) -> str:
    if value is None or value == "":
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return f"{number:.3f}".rstrip("0").rstrip(".")


def chart_number(value, default=0):
    """柱状图数值容错：非数值/缺失回退 0，防止异常报告数据导致崩溃。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def runtime_log_events(data: dict, event_filter: str = "") -> List[dict]:
    """返回安全可展示的运行日志条目（非对象项过滤，缺字段回退空串）。"""
    entries = []
    for entry in data.get("runtime_log") or []:
        if not isinstance(entry, dict):
            continue
        event = str(entry.get("event") or "")
        if event_filter and event != event_filter:
            continue
        entries.append({
            "time": str(entry.get("time") or ""),
            "level": str(entry.get("level") or "info"),
            "event": event,
            "message": str(entry.get("message") or ""),
            "detail": str(entry.get("detail") or ""),
        })
    return entries


def issues_csv_rows(data: dict) -> List[tuple]:
    """导出问题清单 CSV 行（表头 + 全部文件 issues 逐条展开，缺字段回退空串）。"""
    rows = [("文件", "行号", "级别", "类型", "原始文本", "建议")]
    for file_item in data.get("files") or []:
        if not isinstance(file_item, dict):
            continue
        for issue in file_item.get("issues") or []:
            if not isinstance(issue, dict):
                continue
            rows.append((
                str(file_item.get("file") or ""),
                str(issue.get("line") or ""),
                str(issue.get("severity") or ""),
                str(issue.get("kind") or ""),
                str(issue.get("text") or ""),
                str(issue.get("suggestion") or ""),
            ))
    return rows


def feed_evidence_csv_rows(data: dict) -> List[tuple]:
    """导出 F 离群检测证据 CSV 行（表头 + 离群/复核 + 边界错误逐条展开）。"""
    level_labels = {"warning": "离群告警", "review": "复核提示"}
    reason_labels = {"segment-gap": "与其它段差距过大",
                     "cross-program-gap": "与同目录程序差距过大",
                     "boundary-error": "超上下限"}
    rows = [("文件", "行号", "F值", "状态", "原因", "全程次数", "最小差距",
             "APT参考", "原始文本")]
    for file_item in data.get("files") or []:
        if not isinstance(file_item, dict):
            continue
        feed = file_item.get("feed_outlier")
        if not isinstance(feed, dict) or feed.get("safe_plane") is None:
            continue
        has_apt = bool(feed.get("apt_feeds"))
        for item in feed.get("outliers") or []:
            if not isinstance(item, dict):
                continue
            level = str(item.get("level") or "")
            apt_note = ("在 APT 档位内" if item.get("in_apt")
                        else ("不在 APT 档位内" if has_apt else "无 APT 参考"))
            gap = item.get("gap")
            rows.append((
                str(file_item.get("file") or ""),
                str(item.get("line") or ""),
                str(item.get("value") or ""),
                level_labels.get(level, level),
                reason_labels.get(str(item.get("reason") or ""), str(item.get("reason") or "")),
                str(item.get("count") or ""),
                f"{gap:.1%}" if isinstance(gap, (int, float)) else "",
                apt_note,
                str(item.get("text") or ""),
            ))
        for item in feed.get("boundary_errors") or []:
            if not isinstance(item, dict):
                continue
            apt_note = ("在 APT 档位内" if item.get("in_apt")
                        else ("不在 APT 档位内" if has_apt else "无 APT 参考"))
            rows.append((
                str(file_item.get("file") or ""),
                str(item.get("line") or ""),
                str(item.get("value") or ""),
                "边界错误",
                reason_labels["boundary-error"],
                "",
                "",
                apt_note,
                str(item.get("text") or ""),
            ))
    return rows



def apt_meta_rows(item: dict) -> List[Tuple[str, str]]:
    """把文件项的 apt_meta/toolpath_stats 展开为 (键, 值) 展示行（缺数据返回空）。"""
    meta = item.get("apt_meta") or {}
    stats = item.get("toolpath_stats") or {}
    if not meta and not stats:
        return []
    rows = []
    rows.append(("机床型号", str(meta.get("machine") or "")))
    rows.append(("后处理表", str(meta.get("pp_table") or "")))
    rows.append(("CATIA APT 版本", str(meta.get("catia_version") or "")))
    rows.append(("生成时间", str(meta.get("generated_at") or "")))
    rows.append(("程序名", str(meta.get("program_name") or "")))
    rows.append(("操作清单", "、".join(str(v) for v in (meta.get("operations") or []))))
    spindles = ["%s%s %s" % (speed, units, direction) for speed, units, direction in (meta.get("spindles") or [])]
    rows.append(("主轴规划", "、".join(spindles)))
    feeds = ["%s%s" % (value, units) for value, units in (meta.get("feeds") or [])]
    rows.append(("进给规划", "、".join(feeds)))
    rows.append(("冷却液", "、".join(str(v) for v in (meta.get("coolant") or []))))
    rows.extend(_tool_spec_rows(meta))
    op_feeds = meta.get("operation_feeds") or {}
    for op_name in sorted(op_feeds):
        rows.append(("操作进给 · %s" % op_name,
                     "、".join("%s%s" % (value, units) for value, units in op_feeds[op_name])))
    op_spindles = meta.get("operation_spindles") or {}
    for op_name in sorted(op_spindles):
        rows.append(("操作主轴 · %s" % op_name,
                     "、".join("%s%s %s" % (speed, units, direction) for speed, units, direction in op_spindles[op_name])))
    if stats:
        rows.append(("GOTO 点数", str(stats.get("goto_count", 0))))
        rows.append(("圆弧数", str(stats.get("arc_count", 0))))
        rows.append(("抬刀次数", str(stats.get("retract_count", 0))))
        rows.append(("抬刀平面", str(stats.get("retract_plane") or "")))
        rows.append(("X 行程", "%.3f ~ %.3f" % (stats.get("min_x", 0), stats.get("max_x", 0))))
        rows.append(("Y 行程", "%.3f ~ %.3f" % (stats.get("min_y", 0), stats.get("max_y", 0))))
        rows.append(("Z 行程", "%.3f ~ %.3f" % (stats.get("min_z", 0), stats.get("max_z", 0))))
    return rows


def _tool_spec_rows(meta: dict) -> List[Tuple[str, str]]:
    """按规格+种类合并刀具并给出具体规格；无规格数据时回退装夹刀具号列表。"""
    tools = meta.get("tools") or []
    if tools:
        groups = {}
        for tool in tools:
            spec = (str(tool.get("dia") or ""), str(tool.get("tool_coner") or ""),
                    str(tool.get("tool_angle") or ""), str(tool.get("tool_type") or ""))
            groups.setdefault(spec, []).append(int(tool.get("number")))
        rows = []
        for spec, numbers in sorted(groups.items(), key=lambda pair: min(pair[1])):
            dia, coner, angle, tool_type = spec
            parts = []
            if dia:
                parts.append("Ø" + dia)
            if coner:
                parts.append("R" + coner)
            if angle:
                parts.append("单边角 " + angle)
            if tool_type:
                parts.append(tool_type)
            spec_text = "，".join(parts) if parts else "未标注规格"
            rows.append(("刀具 " + "、".join("T%d" % n for n in numbers), spec_text))
        return rows
    tool_loads = meta.get("tool_loads") or []
    if tool_loads:
        return [("装夹刀具", "、".join("T%d" % n for n in tool_loads))]
    return []


def apt_summary_rows(data: dict) -> List[Tuple[str, str]]:
    """报告级 apt_summary + 跨文件轨迹汇总 → (键, 值) 展示行。"""
    summary = data.get("apt_summary") or {}
    rows = []
    machines = summary.get("machines") or []
    if machines:
        rows.append(("机床", "、".join(str(m) for m in machines)))
    spindles = summary.get("spindle_speeds") or []
    if spindles:
        rows.append(("主轴转速", "、".join("%.0f" % float(s) for s in spindles)))
    rows.extend(_tool_spec_summary_rows(data, summary))
    rows.extend(_operation_rows_by_program(data))
    goto_total = arc_total = retract_total = 0
    for item in data.get("files") or []:
        if not isinstance(item, dict):
            continue
        stats = item.get("toolpath_stats") or {}
        goto_total += int(stats.get("goto_count") or 0)
        arc_total += int(stats.get("arc_count") or 0)
        retract_total += int(stats.get("retract_count") or 0)
    if goto_total or arc_total or retract_total:
        rows.append(("轨迹汇总", "GOTO %d 点 / 圆弧 %d / 抬刀 %d 次" % (goto_total, arc_total, retract_total)))
    return rows


def _tool_spec_summary_rows(data: dict, summary: dict) -> List[Tuple[str, str]]:
    """全局刀具按规格+种类合并，给出具体规格与各刀使用程序数；无规格时回退 apt_summary 装夹刀具。"""
    spec_groups = {}
    for item in data.get("files") or []:
        if not isinstance(item, dict):
            continue
        meta = item.get("apt_meta") or {}
        program = str(item.get("program") or "")
        for tool in meta.get("tools") or []:
            spec = (str(tool.get("dia") or ""), str(tool.get("tool_coner") or ""),
                    str(tool.get("tool_angle") or ""), str(tool.get("tool_type") or ""))
            group = spec_groups.setdefault(spec, {})
            number = int(tool.get("number"))
            group.setdefault(number, set()).add(program)
    rows = []
    if spec_groups:
        for spec, numbers in sorted(spec_groups.items(), key=lambda pair: min(pair[1])):
            dia, coner, angle, tool_type = spec
            parts = []
            if dia:
                parts.append("Ø" + dia)
            if coner:
                parts.append("R" + coner)
            if angle:
                parts.append("单边角 " + angle)
            if tool_type:
                parts.append(tool_type)
            spec_text = "，".join(parts) if parts else "未标注规格"
            usage = "、".join("T%d×%d" % (n, len(numbers[n])) for n in sorted(numbers))
            rows.append(("刀具 " + spec_text, usage))
        return rows
    tools = summary.get("tool_loads") or []
    if tools:
        return [("装夹刀具", "、".join("T%d" % int(t) for t in tools))]
    return []


def _operation_rows_by_program(data: dict) -> List[Tuple[str, str]]:
    """操作清单按程序分别列出。"""
    per_program = {}
    for item in data.get("files") or []:
        if not isinstance(item, dict):
            continue
        meta = item.get("apt_meta") or {}
        operations = meta.get("operations") or []
        if not operations:
            continue
        program = str(item.get("program") or str(item.get("file") or ""))
        per_program.setdefault(program, set()).update(str(op) for op in operations)
    rows = []
    for program in sorted(per_program):
        rows.append(("操作 · " + program, "、".join(sorted(per_program[program]))))
    return rows


def log_event_detail(entry: dict) -> str:
    """拼接运行日志事件的完整展示文本（消息 + 详情，含多行 traceback 与关键数据）。"""
    message = str(entry.get("message") or "")
    detail = str(entry.get("detail") or "")
    if detail:
        return f"{message}\n{detail}" if message else detail
    return message


def window_geometry_for_screen(screen_width, screen_height):
    """Return the centered default size and minimum size for a screen.

    Larger screens open wider (up to 1500x800) so report details and tables
    stay fully readable, while 1366x768 still fits comfortably.
    """
    supported = screen_width >= 1366 and screen_height >= 768
    if supported:
        width = min(1500, max(1250, screen_width - 120))
        height = min(800, max(680, screen_height - 120))
        return width, height, 1250, 680
    width = min(screen_width, min(1400, max(900, screen_width - 60)))
    height = min(screen_height, min(800, max(560, screen_height - 80)))
    return width, height, width, height


@dataclass
class ReportSelection:
    report_path: Optional[Path] = None
    file_index: Optional[int] = None


class ReportViewer(ttk.Frame):
    def __init__(self, master):
        super().__init__(master, padding=8)
        self.master.title("NCodeProcessReportViewer")
        self._configure_window()
        self.pack(fill="both", expand=True)
        self.base_dir = application_directory()
        self.report_paths: List[Path] = []
        self.report_data: Optional[dict] = None
        self.selection = ReportSelection()
        self.file_items: List[dict] = []
        self.cell_tooltip = CellTooltip(self.master)
        self._cell_tip_key = None
        self._cell_tip_after = None
        self._cell_tip_text = ""
        self._treeview_font = tkfont.Font(root=self.master, family="Microsoft YaHei UI", size=9)
        self._build()
        self.refresh_reports()

    def _configure_window(self):
        screen_width = self.master.winfo_screenwidth()
        screen_height = self.master.winfo_screenheight()
        width, height, min_width, min_height = window_geometry_for_screen(
            screen_width, screen_height
        )
        x = max(0, (screen_width - width) // 2)
        y = max(0, (screen_height - height) // 2)
        self.master.geometry(f"{width}x{height}+{x}+{y}")
        self.master.minsize(min_width, min_height)

    def _build(self):
        toolbar = ttk.Frame(self)
        toolbar.pack(fill="x", pady=(0, 7))
        ttk.Button(toolbar, text="打开报告文件", command=self.open_report).pack(side="left")
        ttk.Button(toolbar, text="重新扫描报告", command=self.refresh_reports).pack(side="left", padx=6)
        self.report_label = tk.StringVar(value="未加载报告")
        ttk.Label(toolbar, textvariable=self.report_label).pack(side="left", padx=10)

        split = ttk.Panedwindow(self, orient="horizontal")
        split.pack(fill="both", expand=True)
        left = ttk.Frame(split, padding=(0, 0, 7, 0))
        right = ttk.Frame(split, padding=(7, 0, 0, 0))
        split.add(left, weight=3)
        split.add(right, weight=7)
        # 左侧窗格固定最小宽度：右侧长内容表列宽总和超出可视区以激活横向滚动条时，
        # 分隔条保持在 440px，不挤压报告列表与程序列表（ttk.Panedwindow 无 minsize，用 Configure 重设）。
        def _keep_left_pane(_event=None):
            try:
                split.sashpos(0, 440)
            except tk.TclError:
                pass
        self.master.bind("<Configure>", _keep_left_pane, add="+")
        left.rowconfigure(0, weight=1)
        left.rowconfigure(1, weight=2)
        left.columnconfigure(0, weight=1)

        report_box = ttk.LabelFrame(left, text="报告列表")
        report_box.grid(row=0, column=0, sticky="nsew", pady=(0, 7))
        report_box.rowconfigure(0, weight=1)
        report_box.columnconfigure(0, weight=1)
        self.report_table = self._table(report_box, ("time", "name"), ("时间", "报告文件"), (135, 260))
        self.report_table._container.grid(row=0, column=0, sticky="nsew")
        self.report_table.bind("<<TreeviewSelect>>", self._on_report_selected)

        program_box = ttk.LabelFrame(left, text="程序列表")
        program_box.grid(row=1, column=0, sticky="nsew")
        program_box.rowconfigure(0, weight=1)
        program_box.columnconfigure(0, weight=1)
        # 程序名列按最长 12 位字母数字实测宽度；校验列拉伸占满，初始无横向溢出。
        program_name_width = self._treeview_font.measure("W" * 12) + 24
        self.program_table = self._table(program_box, ("program", "issue"), ("程序", "校验"), (program_name_width, 130))
        self.program_table._container.grid(row=0, column=0, sticky="nsew")
        self.program_table.column("program", width=program_name_width, minwidth=program_name_width, stretch=False)
        self.program_table.bind("<<TreeviewSelect>>", self._on_program_selected)

        self.notebook = ttk.Notebook(right)
        self.notebook.pack(fill="both", expand=True)
        self.overview_page = ttk.Frame(self.notebook, padding=8)
        self.files_page = ttk.Frame(self.notebook)
        self.apt_page = ttk.Frame(self.notebook)
        self.stats_page = ttk.Frame(self.notebook)
        self.issues_page = ttk.Frame(self.notebook)
        self.changes_page = ttk.Frame(self.notebook)
        self.log_page = ttk.Frame(self.notebook)
        self.raw_page = ttk.Frame(self.notebook)
        self.notebook.add(self.overview_page, text="概览与可视化")
        self.notebook.add(self.files_page, text="文件明细")
        self.notebook.add(self.apt_page, text="APT 信息")
        self.notebook.add(self.stats_page, text="参数统计")
        self.notebook.add(self.issues_page, text="校验问题")
        self.feed_page = ttk.Frame(self.notebook)
        self.notebook.add(self.feed_page, text="F 离群检测")
        self.notebook.add(self.changes_page, text="修改与差异")
        self.notebook.add(self.log_page, text="运行日志")
        self.notebook.add(self.raw_page, text="原始 JSON")
        self._build_files()
        self._build_overview()
        self._build_apt()
        self._build_stats()
        self._build_issues()
        self._build_feed()
        self._build_changes()
        self._build_log()
        self._build_raw()
        for tree in (self.report_table, self.program_table, self.file_table, self.apt_table, self.stats_table, self.issue_table, self.log_table):
            self._bind_cell_tooltip(tree)

    def _bind_cell_tooltip(self, tree):
        """Show a floating hint with the full cell content after a hover delay.

        The hint only appears when the cell's text is cut off by the column
        width, i.e. when the visible part is not the whole value.
        """

        def on_motion(event):
            row = tree.identify_row(event.y)
            column = tree.identify_column(event.x)
            value = ""
            if row and column != "#0":
                value = tree.set(row, column) or ""
            if not value or not self._cell_truncated(tree, row, column, value):
                self._cancel_cell_tooltip()
                return
            key = (id(tree), row, column)
            if key == self._cell_tip_key:
                return
            self._cancel_cell_tooltip()
            self._cell_tip_key = key
            self._cell_tip_text = value
            self._cell_tip_after = self.master.after(
                CELL_TOOLTIP_DELAY_MS,
                lambda: self.cell_tooltip.show(self._cell_tip_text, event.x_root, event.y_root),
            )

        def on_leave(_event):
            self._cancel_cell_tooltip()

        tree.bind("<Motion>", on_motion, add="+")
        tree.bind("<Leave>", on_leave, add="+")
        for sequence in ("<ButtonPress-1>", "<ButtonPress-3>", "<MouseWheel>", "<Button-4>", "<Button-5>"):
            tree.bind(sequence, lambda _event: self._cancel_cell_tooltip(), add="+")

    def _cell_truncated(self, tree, row, column, value):
        """True when the cell value is wider than the column's visible area."""
        try:
            bbox = tree.bbox(row, column)
        except tk.TclError:
            return False
        if not bbox:
            return False
        return self._treeview_font.measure(value) > bbox[2] - 6

    def _cancel_cell_tooltip(self):
        self._cell_tip_key = None
        if self._cell_tip_after is not None:
            self.master.after_cancel(self._cell_tip_after)
            self._cell_tip_after = None
        if self.cell_tooltip is not None:
            self.cell_tooltip.hide()

    def _table(self, parent, columns, headings, widths):
        frame = ttk.Frame(parent)
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        table = ttk.Treeview(frame, columns=columns, show="headings", selectmode="browse")
        for column, heading, width in zip(columns, headings, widths):
            table.heading(column, text=heading)
            table.column(column, width=width, minwidth=45, anchor="w", stretch=True)
        ybar = ttk.Scrollbar(frame, orient="vertical", command=table.yview)
        xbar = ttk.Scrollbar(frame, orient="horizontal", command=table.xview)
        table.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        table.grid(row=0, column=0, sticky="nsew")
        ybar.grid(row=0, column=1, sticky="ns")
        xbar.grid(row=1, column=0, sticky="ew")
        table._container = frame
        return table

    def _build_files(self):
        self.files_page.rowconfigure(0, weight=1)
        self.files_page.columnconfigure(0, weight=1)
        # 动作列按最长动作词实测宽度、校验列按「999 错 / 999 警」实测宽度，且固定不随窗口拉伸。
        action_width = max(self._treeview_font.measure(text) for text in ("keep", "move", "delete", "duplicate", "review")) + 20
        issue_width = self._treeview_font.measure("999 错 / 999 警") + 20
        # 程序/文件列按真实数据中最长单元格（未配对中间文件完整源文件名，约 43 字符）实测宽度。
        program_width = self._treeview_font.measure("D0354F31311-201_AG6D311A0101_I.MOAPTIndexes") + 24
        # 2026-08-08 报告完善：失败原因列（error_kind: runtime_error，仅失败文件非空）。
        self.file_table = self._table(self.files_page, ("program", "action", "issue", "target", "failure"), ("程序/文件", "动作", "校验", "目标", "失败原因"), (program_width, action_width, issue_width, 700, 260))
        self.file_table.column("program", width=program_width, minwidth=140, stretch=False)
        self.file_table.column("action", width=action_width, minwidth=action_width, stretch=False)
        self.file_table.column("issue", width=issue_width, minwidth=issue_width, stretch=False)
        # 目标列固定且总和超过可视区：横向滚动条初始即激活，可直接滚动查看长路径。
        self.file_table.column("target", width=700, minwidth=200, stretch=False)
        self.file_table.column("failure", width=260, minwidth=120, stretch=False)
        self.file_table._container.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        self.file_table.bind("<<TreeviewSelect>>", self._on_file_selected)

    def _build_apt(self):
        self.apt_page.rowconfigure(1, weight=1)
        self.apt_page.columnconfigure(0, weight=1)
        self.apt_hint_var = tk.StringVar(value="APT 规划信息")
        ttk.Label(self.apt_page, textvariable=self.apt_hint_var, foreground="#57606a").grid(row=0, column=0, sticky="w", padx=6, pady=(6, 2))
        self.apt_table = self._table(self.apt_page, ("key", "value"), ("项目", "APT 规划值"), (150, 560))
        # 值列固定且总和超过可视区：横向滚动条初始即激活，可直接滚动查看长内容。
        self.apt_table.column("key", width=150, minwidth=150, stretch=False)
        self.apt_table.column("value", width=920, minwidth=300, stretch=False)
        self.apt_table._container.grid(row=1, column=0, sticky="nsew", padx=6, pady=(0, 6))

    def _build_overview(self):
        self.overview_page.columnconfigure(0, weight=1)
        self.overview_page.rowconfigure(2, weight=1)
        self.summary_frame = ttk.Frame(self.overview_page)
        self.summary_frame.grid(row=0, column=0, sticky="ew")
        self.summary_vars = {}
        for index, key in enumerate(("成功", "失败", "跳过", "移动", "删除", "警告", "错误")):
            self.summary_frame.columnconfigure(index, weight=1)
            box = ttk.LabelFrame(self.summary_frame, text=key)
            box.grid(row=0, column=index, sticky="ew", padx=2)
            variable = tk.StringVar(value="-")
            self.summary_vars[key] = variable
            ttk.Label(box, textvariable=variable, font=("Microsoft YaHei UI", 16, "bold"), anchor="center").pack(fill="x", padx=8, pady=10)
        self.meta_text = tk.StringVar(value="")
        ttk.Label(self.overview_page, textvariable=self.meta_text, justify="left").grid(row=1, column=0, sticky="ew", pady=10)
        chart_frame = ttk.LabelFrame(self.overview_page, text="处理结果可视化")
        chart_frame.grid(row=2, column=0, sticky="nsew")
        chart_frame.columnconfigure(0, weight=1)
        chart_frame.columnconfigure(1, weight=1)
        # 2026-08-08 报告完善：处理配置快照（config_snapshot）中文标签键值表格。
        config_box = ttk.LabelFrame(self.overview_page, text="处理配置")
        config_box.grid(row=3, column=0, sticky="ew", padx=6, pady=(8, 0))
        config_box.columnconfigure(0, weight=1)
        # 配置项列按最长中文标签实测宽度缩窄，其余宽度留给值列；列宽可手动拖拽调整。
        key_width = max(self._treeview_font.measure(label) for _key, label in CONFIG_SNAPSHOT_LABELS) + 24
        self.config_table = self._table(config_box, ("key", "value"), ("配置项", "值"), (key_width, 900))
        self.config_table.column("key", width=key_width, minwidth=key_width, stretch=False)
        self.config_table.column("value", width=900, minwidth=200, stretch=True)
        self.config_table.configure(height=9)
        self._bind_cell_tooltip(self.config_table)
        self.config_table._container.pack(fill="both", expand=True, padx=4, pady=4)
        chart_frame.rowconfigure(0, weight=1)
        self.result_canvas = tk.Canvas(chart_frame, background="white", highlightthickness=1, highlightbackground="#d0d7de")
        self.result_canvas.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        self.issue_canvas = tk.Canvas(chart_frame, background="white", highlightthickness=1, highlightbackground="#d0d7de")
        self.issue_canvas.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        self.result_canvas.bind("<Configure>", lambda _event: self._draw_charts())
        self.issue_canvas.bind("<Configure>", lambda _event: self._draw_charts())

    def _build_stats(self):
        self.stats_page.rowconfigure(0, weight=1)
        self.stats_page.columnconfigure(0, weight=1)
        self.stats_table = self._table(self.stats_page, ("file", "parameter", "count", "minimum", "maximum", "g00"), ("文件/程序", "参数", "次数", "最小值", "最大值", "G00"), (240, 90, 80, 110, 110, 100))
        self.stats_table._container.grid(row=0, column=0, sticky="nsew")

    def _build_issues(self):
        self.issues_page.rowconfigure(1, weight=1)
        self.issues_page.columnconfigure(0, weight=1)
        filter_bar = ttk.Frame(self.issues_page)
        filter_bar.grid(row=0, column=0, sticky="ew", padx=6, pady=(6, 4))
        ttk.Label(filter_bar, text="级别筛选：").pack(side="left")
        self.issue_filter_var = tk.StringVar(value="全部")
        self.issue_filter_combo = ttk.Combobox(filter_bar, textvariable=self.issue_filter_var, state="readonly", width=12, values=("全部", "error", "warning", "info"))
        self.issue_filter_combo.pack(side="left", padx=(4, 0))
        self.issue_filter_combo.bind("<<ComboboxSelected>>", lambda _event: self._fill_issues(self._selected_item()))
        ttk.Button(filter_bar, text="导出问题 CSV", command=self.export_issues_csv).pack(side="right")
        self.issue_table = self._table(self.issues_page, ("file", "line", "severity", "kind", "text", "suggestion"), ("文件", "行", "级别", "类型", "原始文本", "建议"), (145, 45, 60, 90, 220, 190))
        self.issue_table._container.grid(row=1, column=0, sticky="nsew", padx=6)
        self.issue_table.tag_configure("error", foreground="#b42318", font=("Microsoft YaHei UI", 9, "bold"))
        self.issue_table.tag_configure("warning", foreground="#b54708", font=("Microsoft YaHei UI", 9, "bold"))

    def _build_feed(self):
        """F 离群检测独立页签：文件汇总 + 检测证据/F 分布子页签。

        汇总表固定高度置顶；下方子页签分别展示检测证据与 F 分布表，
        各自占满剩余空间，空数据时显示占位提示，不再出现空白区域。
        """
        self.feed_page.columnconfigure(0, weight=1)
        toolbar = ttk.Frame(self.feed_page)
        toolbar.grid(row=0, column=0, sticky="ew", padx=6, pady=(6, 4))
        ttk.Label(toolbar, text="文件筛选：").pack(side="left")
        self.feed_filter_var = tk.StringVar(value="全部文件")
        self.feed_filter_combo = ttk.Combobox(
            toolbar, textvariable=self.feed_filter_var, state="readonly", width=14,
            values=("全部文件", "仅检出异常"))
        self.feed_filter_combo.pack(side="left", padx=(4, 0))
        self.feed_filter_combo.bind(
            "<<ComboboxSelected>>", lambda _event: self._fill_feed_outlier(self._selected_item()))
        ttk.Button(toolbar, text="导出证据 CSV", command=self.export_feed_evidence_csv).pack(side="right")

        summary_box = ttk.LabelFrame(self.feed_page, text="文件汇总")
        summary_box.grid(row=1, column=0, sticky="ew", padx=6)
        summary_box.columnconfigure(0, weight=1)
        summary_box.rowconfigure(0, weight=1)
        self.feed_summary_table = self._table(
            summary_box,
            ("file", "plane", "segments", "warning", "review", "boundary", "distribution"),
            ("文件/程序", "抬刀平面", "段数", "离群告警", "复核提示", "边界错误", "F 分布项"),
            (220, 90, 60, 90, 90, 90, 90),
        )
        self.feed_summary_table.configure(height=6)
        self.feed_summary_table._container.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)
        summary_box.configure(height=6)

        self.feed_sub_notebook = ttk.Notebook(self.feed_page)
        self.feed_sub_notebook.grid(row=2, column=0, sticky="nsew", padx=6, pady=(4, 0))
        self.feed_page.rowconfigure(2, weight=1)

        evidence_page = ttk.Frame(self.feed_sub_notebook)
        evidence_page.columnconfigure(0, weight=1)
        evidence_page.rowconfigure(0, weight=1)
        self.feed_sub_notebook.add(evidence_page, text="检测证据（离群/复核/边界错误）")
        self.feed_evidence_table = self._table(
            evidence_page,
            ("file", "line", "value", "status", "reason", "count", "gap", "apt", "text"),
            ("文件", "行", "F值", "状态", "原因", "次数", "最小差距", "APT参考", "原始文本"),
            (140, 45, 70, 85, 130, 55, 80, 105, 260),
        )
        self.feed_evidence_table.tag_configure("warning", foreground="#b42318",
                                               font=("Microsoft YaHei UI", 9, "bold"))
        self.feed_evidence_table.tag_configure("review", foreground="#b54708")
        self.feed_evidence_table.tag_configure("boundary", foreground="#7d3c98")
        self.feed_evidence_table.tag_configure("empty", foreground="#9aa0a6")
        self.feed_evidence_table._container.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)

        distribution_page = ttk.Frame(self.feed_sub_notebook)
        distribution_page.columnconfigure(0, weight=1)
        distribution_page.rowconfigure(0, weight=1)
        self.feed_sub_notebook.add(distribution_page, text="F 分布表（单段程序人工判定）")
        self.feed_distribution_table = self._table(
            distribution_page,
            ("file", "value", "count", "first_line", "note"),
            ("文件", "F值", "次数", "首次行号", "说明"),
            (220, 90, 70, 90, 320),
        )
        self.feed_distribution_table.tag_configure("empty", foreground="#9aa0a6")
        self.feed_distribution_table._container.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)

        detail_bar = ttk.Frame(self.feed_page)
        detail_bar.grid(row=3, column=0, sticky="ew", padx=6, pady=(4, 2))
        self.feed_detail_toggle = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            detail_bar, text="显示文本明细", variable=self.feed_detail_toggle,
            command=self._toggle_feed_detail,
        ).pack(side="left")
        self.feed_detail_frame = ttk.Frame(self.feed_page)
        self.feed_detail_frame.grid(row=4, column=0, sticky="nsew", padx=6, pady=(0, 4))
        self.feed_page.rowconfigure(4, weight=1)
        self.feed_outlier_text = tk.Text(
            self.feed_detail_frame, wrap="none", state="disabled",
            font=("Consolas", 9), background="#fbfbfb", relief="flat", padx=6, pady=4,
        )
        feed_ybar = ttk.Scrollbar(self.feed_detail_frame, orient="vertical",
                                  command=self.feed_outlier_text.yview)
        self.feed_outlier_text.configure(yscrollcommand=feed_ybar.set)
        feed_ybar.pack(side="right", fill="y")
        self.feed_outlier_text.pack(fill="both", expand=True)
        self.feed_detail_frame.grid_remove()


    def _toggle_feed_detail(self):
        if self.feed_detail_toggle.get():
            self.feed_detail_frame.grid()
        else:
            self.feed_detail_frame.grid_remove()


    def _fill_feed_outlier(self, selected):
        """F 离群检测页：汇总/证据/分布三张表格 + 文本明细（选中文件或汇总）。

        表格展示为主，文本明细保留供复制；均按“全部文件 / 仅检出异常”筛选。
        """
        for table in (self.feed_summary_table, self.feed_evidence_table,
                      self.feed_distribution_table):
            for item in table.get_children():
                table.delete(item)
        self.feed_outlier_text.configure(state="normal")
        self.feed_outlier_text.delete("1.0", "end")
        files = [selected] if selected is not None else (self.report_data or {}).get("files", [])
        filter_value = self.feed_filter_var.get()
        reason_labels = {"segment-gap": "与其它段差距过大",
                         "cross-program-gap": "与同目录程序差距过大",
                         "boundary-error": "超上下限"}
        level_labels = {"warning": "离群告警", "review": "复核提示"}
        shown = 0
        for file_item in files:
            data = file_item.get("feed_outlier") if isinstance(file_item, dict) else None
            if not isinstance(data, dict) or data.get("safe_plane") is None:
                continue
            outliers = data.get("outliers") or []
            boundary_errors = data.get("boundary_errors") or []
            distribution = data.get("distribution") or []
            warning = sum(1 for item in outliers if item.get("level") == "warning")
            review = sum(1 for item in outliers if item.get("level") == "review")
            if filter_value == "仅检出异常" and not (outliers or boundary_errors):
                continue
            shown += 1
            file_name = str(file_item.get("file") or "")
            safe_plane = data.get("safe_plane")
            self.feed_summary_table.insert("", "end", values=(
                file_name,
                f"{safe_plane:g}" if isinstance(safe_plane, (int, float)) else "-",
                len(data.get("segments") or []),
                warning, review, len(boundary_errors), len(distribution),
            ))
            has_apt = bool(data.get("apt_feeds"))
            evidence_rows = []
            for item in outliers:
                level = str(item.get("level") or "")
                apt_note = ("在 APT 档位内" if item.get("in_apt")
                            else ("不在 APT 档位内" if has_apt else "无 APT 参考"))
                gap = item.get("gap")
                evidence_rows.append((
                    item, level_labels.get(level, level), level,
                    reason_labels.get(str(item.get("reason") or ""), str(item.get("reason") or "")),
                    f"{gap:.1%}" if isinstance(gap, (int, float)) else "-", apt_note))
            for item in boundary_errors:
                apt_note = ("在 APT 档位内" if item.get("in_apt")
                            else ("不在 APT 档位内" if has_apt else "无 APT 参考"))
                evidence_rows.append((
                    item, "边界错误", "boundary", reason_labels["boundary-error"], "-", apt_note))
            for item, status, tag, reason, gap_s, apt_note in evidence_rows:
                value = item.get("value")
                value_s = f"{value:g}" if isinstance(value, (int, float)) else str(value)
                self.feed_evidence_table.insert(
                    "", "end", tags=(tag,),
                    values=(file_name, item.get("line", ""), value_s, status, reason,
                            item.get("count", "-"), gap_s, apt_note,
                            (item.get("text") or "").strip()))
            for row in distribution:
                value = row.get("value")
                value_s = f"{value:g}" if isinstance(value, (int, float)) else str(value)
                self.feed_distribution_table.insert(
                    "", "end",
                    values=(file_name, value_s, row.get("count", 0),
                            row.get("first_line", ""), str(row.get("note") or "")))
            header = f"【{file_item.get('file') or ''}】"
            self.feed_outlier_text.insert("end", header + "\n")
            apt_feeds = data.get("apt_feeds") or []
            if apt_feeds:
                feeds = "、".join(f"{v:g}" for v in apt_feeds)
                self.feed_outlier_text.insert("end", f"  APT 进给参考：{feeds}（仅辅助上下文，不是合法值白名单）\n")
            else:
                self.feed_outlier_text.insert("end", "  APT 进给参考：无（仅按程序自身结构比较）\n")
            segments = data.get("segments") or []
            if len(segments) <= 1:
                if data.get("reference_count"):
                    single_note = (
                        f"单段参照同目录其他程序 {data.get('reference_count')} 个常见档位")
                else:
                    single_note = "单段程序无段间参照，输出 F 分布表供人工检查"
                self.feed_outlier_text.insert(
                    "end",
                    f"  分段统计：抬刀平面 {safe_plane:g}，{len(segments)} 段，"
                    f"容差 {data.get('tolerance', 0.3):.0%}；{single_note}\n")
            else:
                self.feed_outlier_text.insert(
                    "end",
                    f"  分段统计：抬刀平面 {safe_plane:g}，{len(segments)} 段，"
                    f"容差 {data.get('tolerance', 0.3):.0%}\n")
            conclusion = (
                f"  检测结论：警告 {warning}，复核 {review}，边界错误 {len(boundary_errors)}"
                + (f"；F 分布表 {len(distribution)} 项" if distribution else ""))
            self.feed_outlier_text.insert("end", conclusion + "\n")
            if evidence_rows:
                self.feed_outlier_text.insert("end", "  检测证据明细：\n")
                for item, status, _tag, reason, gap_s, apt_note in evidence_rows:
                    value = item.get("value")
                    value_s = f"{value:g}" if isinstance(value, (int, float)) else str(value)
                    line_text = (item.get("text") or "").strip()
                    self.feed_outlier_text.insert(
                        "end",
                        f"    第 {item.get('line')} 行 F{value_s}（{status}，{reason}）"
                        f"全程 {item.get('count', '-')} 次，最小差距 {gap_s}，{apt_note}）：{line_text}\n")
            else:
                self.feed_outlier_text.insert("end", "  检测证据明细：无\n")
            dist_values = [row.get("value") for row in distribution
                           if isinstance(row.get("value"), (int, float))]
            if dist_values:
                self.feed_outlier_text.insert(
                    "end",
                    f"  F 范围：{min(dist_values):g} ~ {max(dist_values):g}（最小值/ 最大值）\n")
            for row in distribution:
                note = "（" + str(row.get("note")) + "）" if row.get("note") else ""
                value = row.get("value")
                value_s = f"{value:g}" if isinstance(value, (int, float)) else str(value)
                self.feed_outlier_text.insert(
                    "end", f"  F 分布：{value_s} × {row.get('count', 0)} 次{note}\n")
        if not self.feed_summary_table.get_children():
            self.feed_summary_table.insert(
                "", "end", tags=("empty",),
                values=("当前报告无 F 离群检测数据", "-", "-", "-", "-", "-", "-"))
        if not self.feed_evidence_table.get_children():
            self.feed_evidence_table.insert(
                "", "end", tags=("empty",),
                values=("", "", "", "无检测证据", "", "", "", "", ""))
        if not self.feed_distribution_table.get_children():
            self.feed_distribution_table.insert(
                "", "end", tags=("empty",),
                values=("", "", "", "", "无 F 分布数据（单段程序才输出分布表）"))
        if shown == 0:
            self.feed_outlier_text.insert("end", "当前报告无 F 离群检测数据\n")
        self.feed_outlier_text.configure(state="disabled")

    def export_feed_evidence_csv(self):
        """导出 F 离群检测证据（离群/复核/边界错误）为 UTF-8 BOM CSV。"""
        rows = feed_evidence_csv_rows(self.report_data or {})
        if len(rows) <= 1:
            messagebox.showinfo("无检测证据", "当前报告没有可导出的 F 离群检测证据。", parent=self.master)
            return
        path = filedialog.asksaveasfilename(
            title="导出 F 离群检测证据 CSV",
            initialdir=str(self.base_dir),
            defaultextension=".csv",
            filetypes=(("CSV 文件", "*.csv"),),
            initialfile="ncodeprocess-feed-evidence.csv",
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerows(rows)
        except OSError as exc:
            messagebox.showerror("导出失败", f"无法写入 CSV：\n{exc}", parent=self.master)
            return
        messagebox.showinfo("导出完成", f"已导出 {len(rows) - 1} 条检测证据：\n{path}", parent=self.master)


    def _build_changes(self):
        """修改与差异页：修改摘要表格 + 带行号的左右对照 diff 视图。"""
        self.changes_page.columnconfigure(0, weight=1)
        self.changes_page.rowconfigure(1, weight=1)
        summary_box = ttk.LabelFrame(self.changes_page, text="修改摘要")
        summary_box.grid(row=0, column=0, sticky="ew", padx=6, pady=(6, 4))
        summary_box.columnconfigure(0, weight=1)
        summary_box.rowconfigure(0, weight=1)
        self.change_summary_table = self._table(
            summary_box, ("file", "summary"), ("文件", "修改内容"), (260, 740))
        self.change_summary_table.configure(height=5)
        self.change_summary_table._container.pack(fill="both", expand=True, padx=4, pady=4)
        self.change_summary_table.bind("<<TreeviewSelect>>", self._on_change_summary_selected)
        self._change_summary_map = {}

        diff_box = ttk.LabelFrame(self.changes_page, text="差异对照（左：处理前　右：处理后）")
        diff_box.grid(row=1, column=0, sticky="nsew", padx=6, pady=(0, 6))
        diff_box.columnconfigure(1, weight=1)
        diff_box.columnconfigure(3, weight=1)
        diff_box.rowconfigure(0, weight=1)
        gutter_font = ("Consolas", 9)
        self.change_left_gutter = tk.Text(
            diff_box, width=6, wrap="none", state="disabled", takefocus=0,
            font=gutter_font, background="#eef1f4", foreground="#57606a",
            relief="flat", padx=4, pady=4, cursor="arrow",
        )
        self.change_right_gutter = tk.Text(
            diff_box, width=6, wrap="none", state="disabled", takefocus=0,
            font=gutter_font, background="#eef1f4", foreground="#57606a",
            relief="flat", padx=4, pady=4, cursor="arrow",
        )
        self.change_left = tk.Text(
            diff_box, wrap="none", state="disabled", takefocus=0,
            font=("Consolas", 9), background="#fafafa", relief="flat", padx=6, pady=4,
        )
        self.change_right = tk.Text(
            diff_box, wrap="none", state="disabled", takefocus=0,
            font=("Consolas", 9), background="#fafafa", relief="flat", padx=6, pady=4,
        )
        self.change_left.tag_configure("removed", foreground="#b42318", background="#ffebe9")
        self.change_left.tag_configure("header", foreground="#0969da", font=("Consolas", 9, "bold"))
        self.change_left.tag_configure("context", foreground="#57606a")
        self.change_right.tag_configure("added", foreground="#137333", background="#e6f4ea")
        self.change_right.tag_configure("header", foreground="#0969da", font=("Consolas", 9, "bold"))
        self.change_right.tag_configure("context", foreground="#57606a")
        self.change_left_gutter.tag_configure("removed", foreground="#b42318")
        self.change_left_gutter.tag_configure("header", foreground="#0969da")
        self.change_right_gutter.tag_configure("added", foreground="#137333")
        self.change_right_gutter.tag_configure("header", foreground="#0969da")
        ybar = ttk.Scrollbar(diff_box, orient="vertical", command=self._sync_change_scroll)
        xbar_l = ttk.Scrollbar(diff_box, orient="horizontal", command=self.change_left.xview)
        xbar_r = ttk.Scrollbar(diff_box, orient="horizontal", command=self.change_right.xview)
        for widget in (self.change_left_gutter, self.change_right_gutter,
                       self.change_left, self.change_right):
            widget.configure(yscrollcommand=lambda first, last: ybar.set(first, last))
        self.change_left.configure(xscrollcommand=xbar_l.set)
        self.change_right.configure(xscrollcommand=xbar_r.set)
        self.change_left_gutter.grid(row=0, column=0, sticky="ns")
        self.change_left.grid(row=0, column=1, sticky="nsew")
        self.change_right_gutter.grid(row=0, column=2, sticky="ns")
        self.change_right.grid(row=0, column=3, sticky="nsew")
        ybar.grid(row=0, column=4, sticky="ns")
        xbar_l.grid(row=1, column=0, columnspan=2, sticky="ew")
        xbar_r.grid(row=1, column=2, columnspan=2, sticky="ew")

    def _sync_change_scroll(self, *args):
        self.change_left_gutter.yview(*args)
        self.change_left.yview(*args)
        self.change_right_gutter.yview(*args)
        self.change_right.yview(*args)


    def _on_change_summary_selected(self, _event=None):
        selection = self.change_summary_table.selection()
        if not selection:
            return
        item = self._change_summary_map.get(selection[0])
        if item is not None:
            self._fill_change_diff(item)

    def _fill_change_diff(self, file_item):
        """把文件的 unified diff 渲染为左右对照，含左右行号（解析 @@ 起始行）。"""
        for widget in (self.change_left_gutter, self.change_right_gutter,
                       self.change_left, self.change_right):
            widget.configure(state="normal")
            widget.delete("1.0", "end")
        diff = file_item.get("diff") or [] if isinstance(file_item, dict) else []
        if not diff:
            self.change_left.insert("end", "（无差异）\n", "context")
            self.change_right.insert("end", "（无差异）\n", "context")
            for widget in (self.change_left_gutter, self.change_right_gutter,
                           self.change_left, self.change_right):
                widget.configure(state="disabled")
            return
        left_no = right_no = None
        for line in diff:
            if line.startswith("@@ -"):
                match = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
                if match:
                    left_no = int(match.group(1))
                    right_no = int(match.group(2))
                self.change_left_gutter.insert("end", "\n", "header")
                self.change_right_gutter.insert("end", "\n", "header")
                self.change_left.insert("end", line + "\n", "header")
                self.change_right.insert("end", line + "\n", "header")
            elif line.startswith("---") or line.startswith("+++"):
                self.change_left_gutter.insert("end", "\n", "header")
                self.change_right_gutter.insert("end", "\n", "header")
                self.change_left.insert("end", line + "\n", "header")
                self.change_right.insert("end", line + "\n", "header")
            elif line.startswith("-") and not line.startswith("---"):
                self.change_left_gutter.insert(
                    "end", (str(left_no) + "\n") if left_no is not None else "\n", "removed")
                self.change_right_gutter.insert("end", "\n", "header")
                self.change_left.insert("end", line[1:] + "\n", "removed")
                self.change_right.insert("end", "\n", "context")
                if left_no is not None:
                    left_no += 1
            elif line.startswith("+") and not line.startswith("+++"):
                self.change_left_gutter.insert("end", "\n", "header")
                self.change_right_gutter.insert(
                    "end", (str(right_no) + "\n") if right_no is not None else "\n", "added")
                self.change_left.insert("end", "\n", "context")
                self.change_right.insert("end", line[1:] + "\n", "added")
                if right_no is not None:
                    right_no += 1
            elif line.startswith(" "):
                self.change_left_gutter.insert(
                    "end", (str(left_no) + "\n") if left_no is not None else "\n", "context")
                self.change_right_gutter.insert(
                    "end", (str(right_no) + "\n") if right_no is not None else "\n", "context")
                self.change_left.insert("end", line[1:] + "\n", "context")
                self.change_right.insert("end", line[1:] + "\n", "context")
                if left_no is not None:
                    left_no += 1
                if right_no is not None:
                    right_no += 1
            elif line.startswith("\\"):
                continue
            else:
                self.change_left_gutter.insert("end", "\n", "header")
                self.change_right_gutter.insert("end", "\n", "header")
                self.change_left.insert("end", line + "\n", "context")
                self.change_right.insert("end", "\n", "context")
        for widget in (self.change_left_gutter, self.change_right_gutter,
                       self.change_left, self.change_right):
            widget.configure(state="disabled")



    def _build_log(self):
        self.log_page.columnconfigure(0, weight=1)
        self.log_page.rowconfigure(1, weight=1)
        filter_bar = ttk.Frame(self.log_page)
        filter_bar.grid(row=0, column=0, sticky="ew", padx=6, pady=(6, 4))
        ttk.Label(filter_bar, text="事件筛选：").pack(side="left")
        self.log_filter_var = tk.StringVar(value="全部")
        self.log_filter_combo = ttk.Combobox(filter_bar, textvariable=self.log_filter_var, state="readonly", width=22)
        self.log_filter_combo.pack(side="left", padx=(4, 0))
        self.log_filter_combo.bind("<<ComboboxSelected>>", lambda _event: self._fill_log(self._selected_item()))
        # 时间/级别/事件按内容实测宽度固定；消息与详情各占一半剩余宽度并支持拉伸。
        time_width = self._treeview_font.measure("2026-08-05T09:30:01") + 20
        level_width = self._treeview_font.measure("warning") + 20
        event_width = max(self._treeview_font.measure(text) for text in ("process_file", "tool_recognized", "issues_found", "scan_finish", "backup_created")) + 20
        # 时间/级别/事件按内容实测宽度固定；消息与详情各占一半剩余宽度，总和超过可视区，
        # 横向滚动条初始即激活（可直接滚动查看长内容，仍可拖宽调整）。
        self.log_table = self._table(self.log_page, ("time", "level", "event", "message", "detail"), ("时间", "级别", "事件", "消息", "详情"), (time_width, level_width, event_width, 400, 400))
        for column, width in (("time", time_width), ("level", level_width), ("event", event_width)):
            self.log_table.column(column, width=width, minwidth=45, stretch=False)
        self.log_table.column("message", width=400, minwidth=100, stretch=False)
        self.log_table.column("detail", width=400, minwidth=100, stretch=False)
        self.log_table._container.grid(row=1, column=0, sticky="nsew", padx=6)
        self.log_table.bind("<<TreeviewSelect>>", lambda _event: self._on_log_row_selected())
        self.log_table.tag_configure("error", foreground="#b42318", font=("Microsoft YaHei UI", 9, "bold"))
        self.log_table.tag_configure("warning", foreground="#b54708", font=("Microsoft YaHei UI", 9, "bold"))
        detail_header = ttk.Label(self.log_page, text="选中事件详情：", foreground="#57606a")
        detail_header.grid(row=2, column=0, sticky="w", padx=6, pady=(6, 2))
        detail_frame = ttk.Frame(self.log_page)
        detail_frame.grid(row=3, column=0, sticky="nsew", padx=6)
        detail_frame.rowconfigure(0, weight=1)
        detail_frame.columnconfigure(0, weight=1)
        self.log_detail_text = tk.Text(detail_frame, wrap="word", height=6, undo=False)
        detail_bar = ttk.Scrollbar(detail_frame, orient="vertical", command=self.log_detail_text.yview)
        self.log_detail_text.configure(yscrollcommand=detail_bar.set)
        self.log_detail_text.grid(row=0, column=0, sticky="nsew")
        detail_bar.grid(row=0, column=1, sticky="ns")
        self.log_detail_text.configure(state="disabled")
        self.log_path_label = ttk.Label(self.log_page, text="", foreground="#57606a", wraplength=700, justify="left")
        self.log_path_label.grid(row=4, column=0, sticky="ew", padx=6, pady=4)

    def _build_raw(self):
        self.raw_page.rowconfigure(0, weight=1)
        self.raw_page.columnconfigure(0, weight=1)
        self.raw_text = self._text(self.raw_page)

    def _text(self, parent):
        frame = ttk.Frame(parent)
        frame.grid(row=0, column=0, sticky="nsew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        text = tk.Text(frame, wrap="none", undo=False)
        ybar = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
        xbar = ttk.Scrollbar(frame, orient="horizontal", command=text.xview)
        text.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        text.grid(row=0, column=0, sticky="nsew")
        ybar.grid(row=0, column=1, sticky="ns")
        xbar.grid(row=1, column=0, sticky="ew")
        return text

    def refresh_reports(self):
        self.report_paths = discover_reports(self.base_dir)
        for item in self.report_table.get_children():
            self.report_table.delete(item)
        for index, path in enumerate(self.report_paths):
            stamp = path.stat().st_mtime
            time_text = datetime.fromtimestamp(stamp).strftime("%Y-%m-%d %H:%M:%S")
            self.report_table.insert("", "end", iid=str(index), values=(time_text, path.name))
        if self.report_paths:
            self.report_table.selection_set("0")
            self.report_table.focus("0")
            self._load_report(self.report_paths[0])
        else:
            self._clear_view("未找到报告。请将报告放入当前目录、NCodeProcessData 或兼容的旧版数据目录，或点击“打开报告文件”。")

    def open_report(self):
        path = filedialog.askopenfilename(
            title="打开 NC 处理报告",
            initialdir=str(self.base_dir),
            filetypes=(("JSON 报告", "ncodeprocess-report-*.json ncpostprocess-report-*.json"), ("JSON 文件", "*.json")),
        )
        if path:
            self._load_report(Path(path))

    def _on_report_selected(self, _event=None):
        selection = self.report_table.selection()
        if selection:
            self._load_report(self.report_paths[int(selection[0])])

    def _load_report(self, path: Path):
        try:
            file_size = path.stat().st_size
        except OSError:
            file_size = 0
        # WP-14：大报告加载状态提示（解析同步进行，先刷新状态栏再读取）。
        loading_text = "报告较大，正在加载，请稍候……" if file_size > 5 * 1024 * 1024 else "正在加载报告……"
        self.report_label.set(loading_text)
        self.master.update_idletasks()
        try:
            data = load_report(path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            messagebox.showerror("报告读取失败", f"无法读取报告：\n{path}\n\n{exc}", parent=self.master)
            return
        self.report_data = data
        self.selection = ReportSelection(path, None)
        self.file_items = data.get("files", [])
        self.report_label.set(f"当前报告：{path}")
        self._populate_programs()
        self._update_views()

    def _populate_programs(self):
        """聚合程序列表（含「全部程序」与「未配对文件」行），并按默认「全部程序」填充文件明细。"""
        for item in self.file_table.get_children():
            self.file_table.delete(item)
        for item in self.program_table.get_children():
            self.program_table.delete(item)
        program_groups = {}
        unmatched = []
        for item in self.file_items:
            program = str(item.get("program") or "").strip()
            if program:
                program_groups.setdefault(program, []).append(item)
            else:
                unmatched.append(item)
        rows = [("全部程序", "__all__", True)]
        rows += [(program, program, False) for program in sorted(program_groups)]
        if unmatched:
            rows.append(("未配对文件", "__none__", False))
        self._program_rows = rows
        for index, (label, _filter, _is_all) in enumerate(rows):
            group = [item for item in self.file_items if self._matches_filter(item, _filter)]
            errors = warnings = 0
            for item in group:
                file_errors, file_warnings, _total = file_issue_counts(item)
                errors += file_errors
                warnings += file_warnings
            issue_text = f"{errors} 错 / {warnings} 警" if (errors or warnings) else "无问题"
            self.program_table.insert("", "end", iid=str(index), values=(label, issue_text))
        if rows:
            self.program_table.selection_set("0")
            self.program_table.focus("0")
            self._on_program_selected()

    @staticmethod
    def _matches_filter(item: dict, program_filter: str) -> bool:
        program = str(item.get("program") or "").strip()
        if program_filter == "__all__":
            return True
        if program_filter == "__none__":
            return not program
        return program == program_filter

    def _on_program_selected(self, _event=None):
        rows = getattr(self, "_program_rows", [])
        selection = self.program_table.selection()
        if not selection:
            return
        try:
            _label, program_filter, _is_all = rows[int(selection[0])]
        except (IndexError, TypeError, ValueError):
            return
        self._populate_files(program_filter)

    def _populate_files(self, program_filter="__all__"):
        for item in self.file_table.get_children():
            self.file_table.delete(item)
        if not self.file_items:
            return
        if program_filter == "__all__":
            self.file_table.insert("", "end", iid="all", values=("全部文件", "汇总", "", "", ""))
        for index, item in enumerate(self.file_items):
            if not self._matches_filter(item, program_filter):
                continue
            errors, warnings, total = file_issue_counts(item)
            issue_text = f"{errors} 错 / {warnings} 警" if total else "无"
            display_program = item.get("program") or item.get("file") or ""
            source = item.get("program_name_source") or ""
            program_cell = f"{display_program}（{source}）" if source else display_program
            target = item.get("target") or ""
            runtime_error = item.get("runtime_error") or ""
            failure = f"{item.get('error_kind') or 'error'}: {runtime_error}" if runtime_error else ""
            self.file_table.insert("", "end", iid=str(index), values=(program_cell, item.get("status") or item.get("action") or "", issue_text, target, failure), tags=(("error",) if errors else (("warning",) if warnings else ())))
        self.file_table.tag_configure("error", foreground="#b42318", font=("Microsoft YaHei UI", 9, "bold"))
        self.file_table.tag_configure("warning", foreground="#b54708", font=("Microsoft YaHei UI", 9, "bold"))
        children = self.file_table.get_children()
        if children:
            self.file_table.selection_set(children[0])
            self.file_table.focus(children[0])
            self._on_file_selected()

    def _on_file_selected(self, _event=None):
        selection = self.file_table.selection()
        if not selection:
            return
        self.selection.file_index = None if selection[0] == "all" else int(selection[0])
        self._update_views()

    def _selected_item(self) -> Optional[dict]:
        if self.selection.file_index is None or self.selection.file_index >= len(self.file_items):
            return None
        return self.file_items[self.selection.file_index]

    def _update_views(self):
        data = self.report_data
        if not data:
            return
        selected = self._selected_item()
        for label, value in report_summary(data):
            if label in self.summary_vars:
                self.summary_vars[label].set(value)
        meta_parts = [
            "输入目录：{0}".format(data.get("input_dir", "")),
            "输出目录：{0}".format(data.get("output_dir", "")),
            "开始时间：{0}    完成时间：{1}".format(data.get("started_at", ""), data.get("finished_at", "")),
        ]
        extra = []
        if data.get("app_version"):
            extra.append("工具版本：{0}".format(data.get("app_version")))
        if data.get("generator"):
            extra.append("报告来源：{0}".format(data.get("generator")))
        if data.get("report_schema_version") is not None:
            extra.append("报告结构版本：{0}".format(data.get("report_schema_version")))
        elapsed = data.get("elapsed_seconds")
        if elapsed not in (None, ""):
            try:
                extra.append("处理耗时：{0:.1f} 秒".format(float(elapsed)))
            except (TypeError, ValueError):
                extra.append("处理耗时：{0}".format(elapsed))
        if data.get("archive_stamp"):
            extra.append("APTSOURCE 归档时间戳：{0}".format(data.get("archive_stamp")))
        if data.get("backup_dir"):
            extra.append("备份目录：{0}".format(data.get("backup_dir")))
        if extra:
            meta_parts.append("　　".join(extra))
        confirmations = [str(item) for item in (data.get("user_confirmations") or [])]
        if confirmations:
            meta_parts.append("用户确认项：" + "；".join(confirmations))
        warnings = [str(item) for item in (data.get("scan_warnings") or [])]
        if warnings:
            meta_parts.append("扫描警告：" + "；".join(warnings))
        env = data.get("environment") or {}
        if env:
            meta_parts.append("运行环境：{platform} / Python {python_version} / {machine} 位".format(
                platform=env.get("platform", ""),
                python_version=env.get("python_version", ""),
                machine=env.get("machine", ""),
            ))
        self.meta_text.set("\n".join(meta_parts))
        self._fill_config_snapshot(data.get("config_snapshot"))
        self._draw_charts()
        self._fill_apt(selected)
        self._fill_stats(selected)
        self._fill_issues(selected)
        self._fill_feed_outlier(selected)
        self._fill_changes(selected)
        self._fill_log(selected)
        self.raw_text.configure(state="normal")
        self.raw_text.delete("1.0", "end")
        self.raw_text.insert("1.0", json.dumps(data, ensure_ascii=False, indent=2))
        self.raw_text.configure(state="disabled")

    def _fill_stats(self, selected):
        for item in self.stats_table.get_children():
            self.stats_table.delete(item)
        for row in iter_stats_rows(self.report_data or {}, selected):
            self.stats_table.insert("", "end", values=row)

    def _fill_issues(self, selected):
        for item in self.issue_table.get_children():
            self.issue_table.delete(item)
        filter_value = self.issue_filter_var.get()
        items = [selected] if selected else (self.report_data or {}).get("files", [])
        for file_item in items:
            if not isinstance(file_item, dict):
                continue
            for issue in file_item.get("issues") or []:
                if not isinstance(issue, dict):
                    continue
                severity = str(issue.get("severity", "info"))
                if filter_value != "全部" and severity != filter_value:
                    continue
                self.issue_table.insert("", "end", values=(file_item.get("file", ""), issue.get("line", ""), severity, issue.get("kind", ""), issue.get("text", ""), issue.get("suggestion", "")), tags=(severity,) if severity in ("error", "warning") else ())

    def _fill_apt(self, selected):
        """APT 信息页签：选中文件显示其 apt_meta/toolpath_stats；全部文件显示报告 apt_summary。"""
        for item in self.apt_table.get_children():
            self.apt_table.delete(item)
        if selected is None:
            rows = apt_summary_rows(self.report_data or {})
            self.apt_hint_var.set("APT 全局摘要（全部文件）" if rows else "当前报告无 APT 规划数据")
        else:
            rows = apt_meta_rows(selected)
            self.apt_hint_var.set("APT 规划信息：" + str(selected.get("file") or "") if rows else "该文件无 APT 规划数据")
        for key, value in rows:
            self.apt_table.insert("", "end", values=(key, value))

    def export_issues_csv(self):
        """导出当前报告的全部校验问题为 UTF-8 BOM CSV。"""
        data = self.report_data or {}
        rows = issues_csv_rows(data)
        if len(rows) <= 1:
            messagebox.showinfo("无问题", "当前报告没有可导出的校验问题。", parent=self.master)
            return
        path = filedialog.asksaveasfilename(
            title="导出校验问题 CSV",
            initialdir=str(self.base_dir),
            defaultextension=".csv",
            filetypes=(("CSV 文件", "*.csv"),),
            initialfile="ncodeprocess-issues.csv",
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerows(rows)
        except OSError as exc:
            messagebox.showerror("导出失败", f"无法写入 CSV：\n{exc}", parent=self.master)
            return
        messagebox.showinfo("导出完成", f"已导出 {len(rows) - 1} 条校验问题：\n{path}", parent=self.master)

    def _fill_changes(self, selected):
        """填充修改摘要表并展示第一个有差异文件的对照视图。"""
        for item in self.change_summary_table.get_children():
            self.change_summary_table.delete(item)
        self._change_summary_map = {}
        items = [selected] if selected is not None else (self.report_data or {}).get("files", [])
        first_item = None
        for idx, file_item in enumerate(items):
            if not isinstance(file_item, dict):
                continue
            changes = file_item.get("changes") or []
            diff = file_item.get("diff") or []
            # 重复文件通常无 changes/diff，但裁决关系（duplicate_winner）仍应进入摘要。
            if not changes and not diff and not file_item.get("duplicate_winner"):
                continue
            summary = "、".join(str(change) for change in changes[:8])
            if len(changes) > 8:
                summary += "…"
            # 2026-08-08 报告完善：摘要并入换刀跳过原因与重复目标裁决关系。
            extra = []
            skipped = file_item.get("auto_tool_change_skipped") or ""
            if skipped:
                extra.append("换刀跳过：" + str(skipped))
            winner = file_item.get("duplicate_winner") or ""
            if winner:
                extra.append("重复：采用 " + str(winner))
            if extra:
                summary = (summary + "；" if summary else "") + "；".join(extra)
            iid = str(idx)
            self.change_summary_table.insert(
                "", "end", iid=iid,
                values=(file_item.get("file", ""), summary))
            self._change_summary_map[iid] = file_item
            if first_item is None:
                first_item = file_item
        if first_item is not None:
            self.change_summary_table.selection_set(self.change_summary_table.get_children()[0])
            self._fill_change_diff(first_item)
        else:
            self._fill_change_diff({})



    def _fill_log(self, selected):
        for item in self.log_table.get_children():
            self.log_table.delete(item)
        data = self.report_data or {}
        events = runtime_log_events(data)
        event_names = sorted({entry["event"] for entry in events})
        current = self.log_filter_var.get()
        self.log_filter_combo.configure(values=["全部"] + event_names)
        if current not in ("", "全部") and current not in event_names:
            self.log_filter_var.set("全部")
        filter_value = "" if self.log_filter_var.get() == "全部" else self.log_filter_var.get()
        rows = runtime_log_events(data, event_filter=filter_value)
        self._log_entries = rows
        for index, entry in enumerate(rows):
            tags = (entry["level"],) if entry["level"] in ("error", "warning") else ()
            self.log_table.insert("", "end", iid=str(index),
                                  values=(entry["time"], entry["level"], entry["event"], entry["message"], entry["detail"]),
                                  tags=tags)
        if rows:
            self.log_table.selection_set("0")
            self.log_table.focus("0")
            self._on_log_row_selected()
        else:
            self._set_log_detail("")
        log_path = str(data.get("log_path") or "")
        if not events and not log_path:
            self.log_path_label.config(text="当前报告不包含运行日志（runtime_log）")
        else:
            # WP-R4：运行日志完整内嵌报告，不再生成磁盘日志文件；表格可横向滚动查看长内容。
            self.log_path_label.config(text="运行日志已内嵌本报告（runtime_log），不再生成磁盘日志文件；长内容可通过横向滚动与悬停查看完整值")

    def _on_log_row_selected(self, _event=None):
        """在下方详情区展示选中运行日志事件的完整消息与 detail（支持多行 traceback）。"""
        entries = getattr(self, "_log_entries", [])
        entry = None
        selection = self.log_table.selection()
        if selection:
            try:
                entry = entries[int(selection[0])]
            except (IndexError, TypeError, ValueError):
                entry = None
        self._set_log_detail(log_event_detail(entry) if entry else "")

    def _set_log_detail(self, text):
        self.log_detail_text.configure(state="normal")
        self.log_detail_text.delete("1.0", "end")
        if text:
            self.log_detail_text.insert("1.0", text)
        self.log_detail_text.configure(state="disabled")

    def _clear_view(self, message):
        self.report_data = None
        self.file_items = []
        self.report_label.set(message)

    def _fill_config_snapshot(self, snapshot):
        """概览页处理配置表：config_snapshot 键值按中文标签逐行展示（缺失键回退）。"""
        if not isinstance(snapshot, dict):
            snapshot = {}
        for item in self.config_table.get_children():
            self.config_table.delete(item)
        for key, label in CONFIG_SNAPSHOT_LABELS:
            if key not in snapshot:
                continue
            value = _config_value_text(snapshot.get(key))
            self.config_table.insert("", "end", values=(label, value))
        if not self.config_table.get_children():
            self.config_table.insert("", "end", values=("配置快照", "当前报告无配置快照"))

    def _draw_charts(self):
        data = self.report_data
        self._draw_bar_chart(self.result_canvas, "文件处理结果", [("成功", data.get("success", 0)), ("失败", data.get("failed", 0)), ("跳过", data.get("skipped", 0)), ("移动", data.get("moved", 0)), ("删除", data.get("deleted", 0))] if data else [], ["#2da44e", "#cf222e", "#8c959f", "#0969da", "#8250df"])
        self._draw_bar_chart(self.issue_canvas, "校验问题", [("错误", data.get("errors", 0)), ("警告", data.get("warnings", 0))] if data else [], ["#cf222e", "#bf8700"])

    def _draw_bar_chart(self, canvas, title, values, colors):
        canvas.delete("all")
        width = max(canvas.winfo_width(), 300)
        height = max(canvas.winfo_height(), 220)
        canvas.create_text(16, 18, text=title, anchor="w", font=("Microsoft YaHei UI", 11, "bold"))
        if not values:
            canvas.create_text(width // 2, height // 2, text="暂无数据", fill="#6e7781")
            return
        maximum = max([chart_number(value) for _label, value in values] + [1])
        base_y = height - 42
        chart_height = max(height - 88, 80)
        slot = max((width - 50) / len(values), 45)
        bar_width = min(54, slot * 0.58)
        for index, ((label, value), color) in enumerate(zip(values, colors)):
            center = 28 + slot * index + slot / 2
            bar_height = chart_height * chart_number(value) / maximum
            canvas.create_rectangle(center - bar_width / 2, base_y - bar_height, center + bar_width / 2, base_y, fill=color, outline="")
            canvas.create_text(center, base_y + 16, text=label, anchor="n", fill="#24292f")
            canvas.create_text(center, base_y - bar_height - 8, text=str(value), anchor="s", fill="#24292f")
        canvas.create_line(24, base_y, width - 20, base_y, fill="#8c959f")


def _set_window_icon(root):
    """Set the Tk window icon (title bar/taskbar) from the bundled .ico.

    PyInstaller extracts datas under sys._MEIPASS; in development the icon
    lives next to the package (../assets).  Failures are ignored so a missing
    icon never prevents the app from starting.
    """
    try:
        if getattr(sys, "frozen", False):
            base = Path(sys._MEIPASS)
        else:
            base = Path(__file__).resolve().parent.parent
        icon_path = base / "assets" / "NCodeProcessReportViewer_icon.ico"
        if icon_path.exists():
            root.iconbitmap(str(icon_path))
    except (tk.TclError, OSError):
        pass


def main():
    root = tk.Tk()
    _set_window_icon(root)
    try:
        style = ttk.Style()
        style.theme_use("vista")
        style.configure("Treeview", rowheight=24, font=("Microsoft YaHei UI", 9))
        style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 9, "bold"))
        style.configure("TButton", padding=(8, 4))
    except tk.TclError:
        pass
    ReportViewer(root)
    root.mainloop()


if __name__ == "__main__":
    main()
