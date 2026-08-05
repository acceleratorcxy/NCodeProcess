from __future__ import annotations

import json
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


def window_geometry_for_screen(screen_width, screen_height):
    """Return the centered default size and minimum size for a screen.

    Larger screens (1600x900, 1920x1080) open at roughly 1290x720 instead of
    nearly full-screen, while 1366x768 keeps a comfortably smaller window.
    """
    supported = screen_width >= 1366 and screen_height >= 768
    if supported:
        width = min(1290, max(1180, screen_width - 160))
        height = min(720, max(640, screen_height - 140))
        return width, height, 1160, 640
    width = min(screen_width, min(1290, max(900, screen_width - 60)))
    height = min(screen_height, min(720, max(560, screen_height - 80)))
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
        left.rowconfigure(0, weight=1)
        left.rowconfigure(1, weight=2)
        left.columnconfigure(0, weight=1)

        report_box = ttk.LabelFrame(left, text="报告列表")
        report_box.grid(row=0, column=0, sticky="nsew", pady=(0, 7))
        report_box.rowconfigure(0, weight=1)
        report_box.columnconfigure(0, weight=1)
        self.report_table = self._table(report_box, ("time", "name"), ("时间", "报告文件"), (135, 180))
        self.report_table._container.grid(row=0, column=0, sticky="nsew")
        self.report_table.bind("<<TreeviewSelect>>", self._on_report_selected)

        file_box = ttk.LabelFrame(left, text="报告文件明细")
        file_box.grid(row=1, column=0, sticky="nsew")
        file_box.rowconfigure(0, weight=1)
        file_box.columnconfigure(0, weight=1)
        self.file_table = self._table(file_box, ("program", "action", "issue", "target"), ("程序/文件", "动作", "校验", "目标"), (115, 62, 70, 105))
        self.file_table._container.grid(row=0, column=0, sticky="nsew")
        self.file_table.bind("<<TreeviewSelect>>", self._on_file_selected)

        self.notebook = ttk.Notebook(right)
        self.notebook.pack(fill="both", expand=True)
        self.overview_page = ttk.Frame(self.notebook, padding=8)
        self.stats_page = ttk.Frame(self.notebook)
        self.issues_page = ttk.Frame(self.notebook)
        self.changes_page = ttk.Frame(self.notebook)
        self.log_page = ttk.Frame(self.notebook)
        self.raw_page = ttk.Frame(self.notebook)
        self.notebook.add(self.overview_page, text="概览与可视化")
        self.notebook.add(self.stats_page, text="参数统计")
        self.notebook.add(self.issues_page, text="校验问题")
        self.notebook.add(self.changes_page, text="修改与差异")
        self.notebook.add(self.log_page, text="运行日志")
        self.notebook.add(self.raw_page, text="原始 JSON")
        self._build_overview()
        self._build_stats()
        self._build_issues()
        self._build_changes()
        self._build_log()
        self._build_raw()
        for tree in (self.report_table, self.file_table, self.stats_table, self.issue_table, self.log_table):
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
        self.issues_page.rowconfigure(0, weight=1)
        self.issues_page.columnconfigure(0, weight=1)
        self.issue_table = self._table(self.issues_page, ("file", "line", "severity", "kind", "text", "suggestion"), ("文件", "行", "级别", "类型", "原始文本", "建议"), (145, 45, 60, 90, 220, 190))
        self.issue_table._container.grid(row=0, column=0, sticky="nsew")
        self.issue_table.tag_configure("error", foreground="#b42318", font=("Microsoft YaHei UI", 9, "bold"))
        self.issue_table.tag_configure("warning", foreground="#b54708", font=("Microsoft YaHei UI", 9, "bold"))

    def _build_changes(self):
        self.changes_page.rowconfigure(0, weight=1)
        self.changes_page.columnconfigure(0, weight=1)
        self.change_text = self._text(self.changes_page)
        self.change_text.tag_configure("removed", foreground="#b42318", background="#ffebe9")
        self.change_text.tag_configure("added", foreground="#137333", background="#e6f4ea")
        self.change_text.tag_configure("header", foreground="#0969da")
        self.change_text.tag_configure("hidden", foreground="#0969da")

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
        self.log_table = self._table(self.log_page, ("time", "level", "event", "message", "detail"), ("时间", "级别", "事件", "消息", "详情"), (140, 55, 120, 220, 160))
        self.log_table._container.grid(row=1, column=0, sticky="nsew", padx=6)
        self.log_table.tag_configure("error", foreground="#b42318", font=("Microsoft YaHei UI", 9, "bold"))
        self.log_table.tag_configure("warning", foreground="#b54708", font=("Microsoft YaHei UI", 9, "bold"))
        self.log_path_label = ttk.Label(self.log_page, text="", foreground="#57606a", wraplength=700, justify="left")
        self.log_path_label.grid(row=2, column=0, sticky="ew", padx=6, pady=4)

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
            data = load_report(path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            messagebox.showerror("报告读取失败", f"无法读取报告：\n{path}\n\n{exc}", parent=self.master)
            return
        self.report_data = data
        self.selection = ReportSelection(path, None)
        self.file_items = data.get("files", [])
        self.report_label.set(f"当前报告：{path}")
        self._populate_files()
        self._update_views()

    def _populate_files(self):
        for item in self.file_table.get_children():
            self.file_table.delete(item)
        if not self.file_items:
            return
        self.file_table.insert("", "end", iid="all", values=("全部文件", "汇总", "", ""))
        for index, item in enumerate(self.file_items):
            errors, warnings, total = file_issue_counts(item)
            issue_text = f"{errors} 错 / {warnings} 警" if total else "无"
            program = item.get("program") or item.get("file") or ""
            source = item.get("program_name_source") or ""
            program_cell = f"{program}（{source}）" if source else program
            target = item.get("target") or ""
            self.file_table.insert("", "end", iid=str(index), values=(program_cell, item.get("status") or item.get("action") or "", issue_text, target), tags=(("error",) if errors else (("warning",) if warnings else ())))
        self.file_table.tag_configure("error", foreground="#b42318", font=("Microsoft YaHei UI", 9, "bold"))
        self.file_table.tag_configure("warning", foreground="#b54708", font=("Microsoft YaHei UI", 9, "bold"))
        self.file_table.selection_set("all")

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
        self.meta_text.set("\n".join(meta_parts))
        self._draw_charts()
        self._fill_stats(selected)
        self._fill_issues(selected)
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
        items = [selected] if selected else (self.report_data or {}).get("files", [])
        for file_item in items:
            if not isinstance(file_item, dict):
                continue
            for issue in file_item.get("issues") or []:
                severity = str(issue.get("severity", "info"))
                self.issue_table.insert("", "end", values=(file_item.get("file", ""), issue.get("line", ""), severity, issue.get("kind", ""), issue.get("text", ""), issue.get("suggestion", "")), tags=(severity,) if severity in ("error", "warning") else ())

    def _fill_changes(self, selected):
        self.change_text.configure(state="normal")
        self.change_text.delete("1.0", "end")
        items = [selected] if selected else (self.report_data or {}).get("files", [])
        for file_item in items:
            if not isinstance(file_item, dict):
                continue
            title = file_item.get("file", "未命名文件")
            changes = file_item.get("changes") or []
            diff = file_item.get("diff") or []
            if not changes and not diff:
                continue
            self.change_text.insert("end", f"【{title}】\n", "header")
            for change in changes:
                self.change_text.insert("end", f"修改：{change}\n")
            for line in diff:
                tag = "added" if line.startswith("+") and not line.startswith("+++") else ("removed" if line.startswith("-") and not line.startswith("---") else ("header" if line.startswith("@@") or line.startswith("---") or line.startswith("+++") else ""))
                self.change_text.insert("end", line + "\n", tag)
            self.change_text.insert("end", "\n")
        self.change_text.configure(state="disabled")

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
        for index, entry in enumerate(rows):
            tags = (entry["level"],) if entry["level"] in ("error", "warning") else ()
            self.log_table.insert("", "end", iid=str(index),
                                  values=(entry["time"], entry["level"], entry["event"], entry["message"], entry["detail"]),
                                  tags=tags)
        log_path = str(data.get("log_path") or "")
        if not events and not log_path:
            self.log_path_label.config(text="当前报告不包含运行日志（runtime_log）")
        else:
            # WP-R4：运行日志完整内嵌报告，不再生成磁盘日志文件。
            self.log_path_label.config(text="运行日志已内嵌本报告（runtime_log），不再生成磁盘日志文件")

    def _clear_view(self, message):
        self.report_data = None
        self.file_items = []
        self.report_label.set(message)

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
        maximum = max([value for _label, value in values] + [1])
        base_y = height - 42
        chart_height = max(height - 88, 80)
        slot = max((width - 50) / len(values), 45)
        bar_width = min(54, slot * 0.58)
        for index, ((label, value), color) in enumerate(zip(values, colors)):
            center = 28 + slot * index + slot / 2
            bar_height = chart_height * float(value) / maximum
            canvas.create_rectangle(center - bar_width / 2, base_y - bar_height, center + bar_width / 2, base_y, fill=color, outline="")
            canvas.create_text(center, base_y + 16, text=label, anchor="n", fill="#24292f")
            canvas.create_text(center, base_y - bar_height - 8, text=str(value), anchor="s", fill="#24292f")
        canvas.create_line(24, base_y, width - 20, base_y, fill="#8c959f")


def main():
    root = tk.Tk()
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
