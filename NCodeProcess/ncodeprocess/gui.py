from __future__ import annotations

import difflib
import json
import re
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk
from typing import List, NamedTuple

from . import __version__
from .core import (
    Config,
    FIELD_ORDER,
    ProgramInfo,
    ToolInfo,
    align_lines,
    build_plan,
    calculate_stats,
    extract_header_fields,
    extract_tools,
    format_nc_date,
    process_plan,
    reprocess_file,
    save_timestamped_report,
    scan_directory,
    validate_program,
)
from .preferences import (
    KEY as PREFERENCES_KEY,
    REGISTRY_DEFAULTS,
    clear_all,
    load_all,
    save_all,
    storage_backend,
)


DEFAULT_TOOL_TYPES = ["普通立铣刀", "圆鼻立铣刀", "球头立铣刀", "平底立铣刀", "反锥立铣刀", "铅笔铣刀", "T形刀", "钻头", "中心钻"]


def parse_extension_list(raw: str) -> set:
    """Normalize a comma/semicolon/whitespace separated extension list (lowercase)."""
    parts = [p.strip().lower() for p in re.split(r"[,;，；\s]+", raw or "") if p.strip()]
    for part in parts:
        if not re.match(r"^\.[a-z0-9]+$", part):
            raise ValueError(f"扩展名格式无效：{part}（应为 .mpf 形式，逗号分隔）")
    return set(parts)


def parse_delete_extensions(raw: str) -> set:
    """待删除扩展名与主程序扩展名共用同一解析规则。"""
    return parse_extension_list(raw)


def parse_output_extension(raw: str) -> str:
    """校验单个输出扩展名，保留原大小写（如 .MPF 或 .nc）。"""
    value = (raw or "").strip()
    if not re.match(r"^\.[A-Za-z0-9]+$", value):
        raise ValueError(f"输出扩展名格式无效：{value or '空'}（应为 .MPF 形式）")
    return value

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


KEEP_COLUMN_SPECS = (
    ("action", 58, 52, False),
    ("program", 112, 96, False),
    ("source", 205, 130, True),
    ("target", 150, 105, True),
)
TOOL_COLUMN_SPECS = (
    ("number", 50, 48, False),
    ("dia", 50, 48, False),
    ("coner", 82, 76, True),
    ("angle", 84, 78, True),
    ("type", 104, 88, True),
)
KEEP_TABLE_HEADINGS = ("动作", "程序名", "MPF 源文件", "目标")
TOOL_TABLE_HEADINGS = ("刀具号", "DIA", "TOOL_CONER", "TOOL_ANGLE", "TOOL_TYPE")

UI_FONT_CANDIDATES = ("Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", "Tahoma")


class FontLayoutProfile(NamedTuple):
    keep_specs: tuple
    tool_specs: tuple
    validation_width: int


def choose_ui_font_family(available_families):
    """Return the first compatible UI font available on this system."""
    available = set(available_families)
    for family in UI_FONT_CANDIDATES:
        if family in available:
            return family
    return "TkDefaultFont"


def font_layout_profile(measure):
    """Create deterministic table dimensions from a Treeview font measure."""
    scale = max(1.0, min(1.5, float(measure("程序名")) / 36.0))

    def scale_specs(specs):
        return tuple(
            (name, round(initial * scale), round(minimum * scale), stretch)
            for name, initial, minimum, stretch in specs
        )

    keep_specs = scale_specs(KEEP_COLUMN_SPECS)
    tool_specs = list(scale_specs(TOOL_COLUMN_SPECS))
    number_index = next(index for index, spec in enumerate(tool_specs) if spec[0] == "number")
    name, initial, minimum, stretch = tool_specs[number_index]
    number_width = measure("刀具号") + 20
    tool_specs[number_index] = (
        name,
        max(initial, number_width),
        max(minimum, number_width),
        stretch,
    )
    validation_width = max(round(82 * scale), measure("999 错 / 999 警") + 20)
    return FontLayoutProfile(keep_specs, tuple(tool_specs), validation_width)


def ensure_heading_widths(specs, headings, measure, padding):
    """Ensure each runtime column can render its heading with padding."""
    if len(specs) != len(headings):
        raise ValueError("column specifications and headings must have equal lengths")
    return tuple(
        (
            name,
            max(initial, measure(heading) + padding),
            max(minimum, measure(heading) + padding),
            stretch,
        )
        for (name, initial, minimum, stretch), heading in zip(specs, headings)
    )


def window_geometry_for_screen(screen_width, screen_height):
    supported = screen_width >= 1366 and screen_height >= 768
    if supported:
        # Cap the default window at roughly 1280x720 so larger screens
        # (1600x900, 1920x1080) no longer open nearly full-screen.
        width = min(1290, max(1180, screen_width - 80))
        height = min(720, max(650, screen_height - 100))
        return width, height, 1180, 650

    width = min(screen_width, min(1600, max(900, screen_width - 40)))
    height = min(screen_height, min(900, max(560, screen_height - 60)))
    return width, height, width, height


def centered_position(parent_x, parent_y, parent_w, parent_h, width, height, screen_w, screen_h):
    """Return the top-left position that centers a child window on its parent.

    The result is clamped so the child window never starts outside the screen.
    """
    x = max(0, min(parent_x + (parent_w - width) // 2, screen_w - width))
    y = max(0, min(parent_y + (parent_h - height) // 2, screen_h - height))
    return x, y


def fit_column_widths(available_width, specs):
    """Fit ordered column specifications into an available pixel width."""
    widths = {name: initial for name, initial, _minimum, _stretch in specs}
    if available_width <= 0:
        return widths

    initial_total = sum(widths.values())
    if available_width >= initial_total:
        extra = available_width - initial_total
        stretch_names = [name for name, _initial, _minimum, stretch in specs if stretch]
        if stretch_names:
            share, remainder = divmod(extra, len(stretch_names))
            for index, name in enumerate(stretch_names):
                widths[name] += share + (1 if index < remainder else 0)
        return widths

    minimums = {name: minimum for name, _initial, minimum, _stretch in specs}
    if available_width < sum(minimums.values()):
        return minimums

    remaining = initial_total - available_width
    for names in (
        [name for name, _initial, _minimum, stretch in specs if stretch],
        [name for name, _initial, _minimum, _stretch in specs],
    ):
        while remaining:
            adjustable = [name for name in names if widths[name] > minimums[name]]
            if not adjustable:
                break
            share, remainder = divmod(remaining, len(adjustable))
            reduced = 0
            for index, name in enumerate(adjustable):
                reduction = min(widths[name] - minimums[name], share + (index < remainder))
                widths[name] -= reduction
                reduced += reduction
            remaining -= reduced
        if not remaining:
            break
    return widths


def _display_with_gap(value):
    """Return the table-only representation used to keep path text readable."""
    return f" {value}" if value else ""


def folder_drawing_choices(directory: Path):
    choices = []
    current = directory.resolve()
    for level in range(4):
        label = ("当前目录" if level == 0 else "上" + str(level) + "层") + "：" + current.name
        choices.append((label, current.name))
        current = current.parent
    return choices


def merge_drawing_choices(folder_choices, apt_candidates):
    """Merge duplicate values within each source method."""
    choices = list(folder_choices)
    for label, value in apt_candidates or []:
        display = label + "：" + value
        if (display, value) not in choices:
            choices.append((display, value))
    return choices


def compact_diff_rows(before, after, context=3):
    """Return aligned diff rows with only the requested unchanged context.

    Each row is ``(before_no, before_text, before_tag, after_no,
    after_text, after_tag)``. Unchanged records outside the context window are
    omitted and returned as a single count for the blue footer.
    """
    before_lines = before.replace("\r\n", "\n").replace("\r", "\n").splitlines()
    after_lines = after.replace("\r\n", "\n").replace("\r", "\n").splitlines()
    matcher = difflib.SequenceMatcher(a=before_lines, b=after_lines, autojunk=False)
    records = []
    for opcode, a1, a2, b1, b2 in matcher.get_opcodes():
        if opcode == "equal":
            for offset, (left, right) in enumerate(zip(before_lines[a1:a2], after_lines[b1:b2])):
                records.append((a1 + offset + 1, left, "", b1 + offset + 1, right, "", False))
        elif opcode == "delete":
            for offset, left in enumerate(before_lines[a1:a2]):
                records.append((a1 + offset + 1, left, "removed", None, "", "", True))
        elif opcode == "insert":
            for offset, right in enumerate(after_lines[b1:b2]):
                records.append((None, "", "", b1 + offset + 1, right, "added", True))
        else:
            length = max(a2 - a1, b2 - b1)
            for offset in range(length):
                has_left = a1 + offset < a2
                has_right = b1 + offset < b2
                records.append((
                    a1 + offset + 1 if has_left else None,
                    before_lines[a1 + offset] if has_left else "",
                    "removed" if has_left else "",
                    b1 + offset + 1 if has_right else None,
                    after_lines[b1 + offset] if has_right else "",
                    "added" if has_right else "",
                    True,
                ))
    changed = [index for index, row in enumerate(records) if row[6]]
    visible = set()
    for index in changed:
        visible.update(range(max(0, index - context), min(len(records), index + context + 1)))
    rows = [row[:6] for index, row in enumerate(records) if index in visible]
    hidden = sum(1 for index, row in enumerate(records) if index not in visible and not row[6])
    return rows, hidden


def needs_detailed_confirmation(lines, max_items=10, max_characters=1200):
    """Return True when a native message box would become impractically tall."""
    return len(lines) > max_items or sum(len(line) for line in lines) > max_characters


def application_directory() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


class App(ttk.Frame):
    def __init__(self, master, settings_registry_key=None):
        super().__init__(master, padding=8)
        self.master.title("NCodeProcess " + __version__)
        self.settings_registry_key = settings_registry_key or PREFERENCES_KEY
        self._loaded_settings = load_all(self.settings_registry_key)
        self._configure_window_size()
        self.pack(fill="both", expand=True)
        self.workdir = application_directory()
        self.folder_choices = folder_drawing_choices(self.workdir)
        self.data_dir = self.workdir / "NCodeProcessData"
        self.special_tools_path = self.data_dir / "special_tools.json"
        self.legacy_special_tools_path = self.workdir / "NCPostProcessData" / "special_tools.json"
        self.scan_result = None
        self._scan_generation = 0
        self._processing = False
        self._process_progress = None
        self._process_progress_lock = threading.Lock()
        self.report = None
        self.info_vars = {}
        self.info_defaults = {key: "" for key in ("bianzhi", "shenhe", "drawing", "version", "date")}
        self.applied_info = ProgramInfo()
        self.program_header_values = {}
        self.program_tools = {}
        self.current_program = None
        self.detail_notebook = None
        self.stats_page = None
        self.all_stats_window = None
        self.program_editor_window = None
        self.program_editor_text = None
        self.program_editor_gutter = None
        self.program_compare_window = None
        self.program_compare_left = None
        self.program_compare_right = None
        self.program_compare_left_gutter = None
        self.program_compare_right_gutter = None
        self.keep_table_menu = None
        self._syncing_keep_selection = False
        self._startup_after_ids = set()
        self._column_profile_states = []
        self.cell_tooltip = CellTooltip(self.master)
        self._cell_tip_key = None
        self._cell_tip_after = None
        self._cell_tip_text = ""
        self.bind("<Destroy>", self._cancel_startup_callbacks, add="+")
        self.tool_types = list(DEFAULT_TOOL_TYPES)
        self._configure_typography()
        self._build()
        self.load_saved_fields()
        self.load_special_tools()
        self._schedule_startup_callback(50, self._present_window)
        self._schedule_startup_callback(120, self.scan)

    def _configure_typography(self):
        """Set compatible native fonts and derive layout from Treeview metrics."""
        default_font = tkfont.Font(root=self.master, name="TkDefaultFont", exists=True)
        selected_family = choose_ui_font_family(tkfont.families(self.master))
        family = (
            default_font.actual("family")
            if selected_family == "TkDefaultFont"
            else selected_family
        )

        for font_name in ("TkDefaultFont", "TkTextFont", "TkHeadingFont", "TkMenuFont"):
            try:
                tkfont.Font(root=self.master, name=font_name, exists=True).configure(
                    family=family, size=9
                )
            except tk.TclError:
                pass

        self._treeview_font = tkfont.Font(root=self.master, family=family, size=9)
        actual_family = self._treeview_font.actual("family")
        style = ttk.Style(self.master)
        style.configure("Treeview", font=self._treeview_font,
                        rowheight=max(22, self._treeview_font.metrics("linespace") + 6))
        self.tree_heading_font = tkfont.Font(
            root=self.master, family=actual_family, size=8, weight="bold"
        )
        self.tree_heading_padding = 8
        style.configure("Treeview.Heading", font=self.tree_heading_font)

        profile = font_layout_profile(self._treeview_font.measure)
        self.keep_column_specs = ensure_heading_widths(
            profile.keep_specs,
            KEEP_TABLE_HEADINGS,
            self.tree_heading_font.measure,
            self.tree_heading_padding,
        )
        self.tool_column_specs = ensure_heading_widths(
            profile.tool_specs,
            TOOL_TABLE_HEADINGS,
            self.tree_heading_font.measure,
            self.tree_heading_padding,
        )
        self.validation_column_width = profile.validation_width
        self.ui_font_family = actual_family

    def _safe_after(self, ms, callback):
        """线程回调安全出口：窗口销毁后 Tk 的 after 会抛 TclError，统一吞掉。"""
        try:
            return self.after(ms, callback)
        except tk.TclError:
            return None

    def _show_centered(self, window, width=None, height=None, min_width=0, min_height=0):
        """Place a Toplevel child window centered on the main window.

        The window is kept hidden while its requested size is measured, then
        sized/clamped to the screen and revealed. This avoids a visible flash
        at the wrong location/size, so call this after the window's widgets
        have been built. Width/height default to the window's requested size;
        explicit sizes are also raised to the minimums and clamped to the
        screen so the window is always fully displayable.
        """
        window.withdraw()
        window.update_idletasks()
        if width is None or height is None:
            width = width or window.winfo_reqwidth()
            height = height or window.winfo_reqheight()
        screen_w = window.winfo_screenwidth()
        screen_h = window.winfo_screenheight()
        width = max(width, min_width)
        height = max(height, min_height)
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
        window.deiconify()

    def _schedule_startup_callback(self, delay, callback):
        """Schedule a startup callback that can be cancelled with this App."""
        after_id = None

        def run():
            self._startup_after_ids.discard(after_id)
            callback()

        after_id = self.after(delay, run)
        self._startup_after_ids.add(after_id)

    def _cancel_startup_callbacks(self, event):
        """Cancel pending startup work when this frame is destroyed."""
        if event.widget is not self:
            return
        for after_id in self._startup_after_ids:
            try:
                self.after_cancel(after_id)
            except tk.TclError:
                pass
        self._startup_after_ids.clear()
        for state in self._column_profile_states:
            after_id = state.get("after_id")
            if after_id is None:
                continue
            try:
                self.after_cancel(after_id)
            except tk.TclError:
                pass
            state["after_id"] = None

    def _bind_column_profile(self, table, specs, available_width_callback):
        """Keep a Treeview's declared column profile fitted to its viewport."""
        state = {"after_id": None, "destroyed": False, "manual_widths": {}}
        self._column_profile_states.append(state)
        table._column_profile_state = state

        def update_widths():
            state["after_id"] = None
            if state["destroyed"]:
                return
            try:
                available_width = int(available_width_callback())
                manual_widths = state["manual_widths"]
                automatic_specs = tuple(spec for spec in specs if spec[0] not in manual_widths)
                automatic_available = available_width - sum(manual_widths.values())
                if automatic_available <= 0:
                    widths = {
                        name: minimum
                        for name, _initial, minimum, _stretch in automatic_specs
                    }
                else:
                    widths = fit_column_widths(automatic_available, automatic_specs)
                for name, _initial, minimum, stretch in specs:
                    if name in manual_widths:
                        manual_width = manual_widths[name]
                        table.column(
                            name,
                            width=manual_width,
                            minwidth=minimum,
                            stretch=False,
                        )
                    else:
                        table.column(name, width=widths[name], minwidth=minimum, stretch=stretch)
            except tk.TclError:
                state["destroyed"] = True

        def schedule(_event=None):
            if state["destroyed"] or state["after_id"] is not None:
                return
            try:
                state["after_id"] = self.after_idle(update_widths)
            except tk.TclError:
                state["destroyed"] = True

        def cancel(event):
            if event.widget is not table:
                return
            state["destroyed"] = True
            state["manual_widths"].clear()
            after_id = state["after_id"]
            if after_id is not None:
                try:
                    self.after_cancel(after_id)
                except tk.TclError:
                    pass
                state["after_id"] = None

        table.bind("<Configure>", schedule, add="+")
        table.bind("<Destroy>", cancel, add="+")
        schedule()

    @staticmethod
    def _treeview_available_width(table):
        """Allow for native Treeview borders before fitting declared columns."""
        return max(0, table.winfo_width() - 8)

    @staticmethod
    def _preserve_manual_treeview_width(table):
        """Let a user-resized column create horizontal overflow when needed."""
        columns = tuple(table["columns"])
        state = table._column_profile_state

        def start_resize(event):
            if table.identify_region(event.x, event.y) != "separator":
                return
            column_id = table.identify_column(event.x)
            try:
                column = columns[int(column_id[1:]) - 1]
            except (IndexError, ValueError):
                return
            state["resizing_column"] = column
            table.column(column, stretch=False)

        def finish_resize(_event):
            column = state.pop("resizing_column", None)
            if column is None or state["destroyed"]:
                return
            try:
                width = int(table.column(column, "width"))
                state["manual_widths"][column] = width
                table.column(column, width=width, stretch=False)
            except tk.TclError:
                state["destroyed"] = True

        table.bind("<ButtonPress-1>", start_resize, add="+")
        table.bind("<ButtonRelease-1>", finish_resize, add="+")

    def _present_window(self):
        """Realize the layout before showing the configured window."""
        self.master.update_idletasks()
        self.master.deiconify()

    def _configure_window_size(self):
        """Choose a centered, non-maximized default (~1280x720 on 1080p)."""
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
        top = ttk.LabelFrame(self, text="程序运行配置")
        top.pack(fill="x")
        ttk.Label(top, text="扫描应用程序所在目录：").pack(side="left", padx=(8, 2), pady=6)
        ttk.Label(top, text=str(self.workdir)).pack(side="left", padx=2, pady=6)
        self.settings_button = ttk.Button(top, text="程序设置…", command=lambda: self.open_settings())
        self.recursive = tk.BooleanVar(value=False)
        self.save_aptsource = tk.BooleanVar(value=False)
        # 右侧控件按 pack 逆序显示为：[重新扫描目录][保存 APTSOURCE][包含子目录][程序设置…]
        self.settings_button.pack(side="right", padx=5)
        self.recursive_checkbox = ttk.Checkbutton(top, text="包含子目录", variable=self.recursive)
        self.recursive_checkbox.pack(side="right", padx=5)
        ttk.Checkbutton(top, text="保存 APTSOURCE（按时间归档）", variable=self.save_aptsource, command=self.scan).pack(side="right", padx=8)
        ttk.Button(top, text="重新扫描目录", command=self.scan).pack(side="right", padx=5)

        self.process_info_frame = ttk.LabelFrame(self, text="程序信息")
        self.process_info_frame.pack(fill="x", pady=(7, 0))
        self.process_info_frame.columnconfigure(0, weight=1)
        info = self.process_info_frame

        basic_info = ttk.Frame(info)
        basic_info.grid(row=0, column=0, sticky="ew", padx=4, pady=(2, 0))
        fields = (("编制 BIANZHI", "bianzhi"), ("审核/校对 SHENHE", "shenhe"), ("图号", "drawing"), ("版次", "version"), ("日期", "date"))
        for col, (label, key) in enumerate(fields):
            basic_info.columnconfigure(col, weight=1, uniform="program_info_field")
            field = ttk.Frame(basic_info)
            field.grid(row=0, column=col, padx=3, sticky="ew")
            field.columnconfigure(1, weight=1)
            ttk.Label(field, text=label).grid(row=0, column=0, padx=(0, 3), sticky="w")
            var = tk.StringVar()
            self.info_vars[key] = var
            ttk.Entry(field, textvariable=var, width=10).grid(row=0, column=1, sticky="ew")
        self.overwrite_fields = tk.BooleanVar(value=False)
        self.auto_m03 = tk.BooleanVar(value=True)
        self.auto_tool_change = tk.BooleanVar(value=False)
        self.g00_level = tk.StringVar(value="error")
        loaded = self._loaded_settings
        self.encoding_var = tk.StringVar(value=loaded.get("encoding", "auto"))
        self.delete_extensions_var = tk.StringVar(value=loaded.get("delete_extensions", ".log, .moaptindexes"))
        self.allowed_name_pattern_var = tk.StringVar(value=loaded.get("allowed_name_pattern", r"^[A-Za-z0-9_一-鿿-]+$"))
        self.aptsource_dir_var = tk.StringVar(value=loaded.get("aptsource_dir", "aptsource"))
        self.program_extensions_var = tk.StringVar(value=loaded.get("program_extensions", ".mpf"))
        self.program_output_extension_var = tk.StringVar(value=loaded.get("program_output_extension", ".MPF"))
        self.require_end_marker_var = tk.BooleanVar(value=loaded.get("require_end_marker", "1") == "1")
        self.require_m06_var = tk.BooleanVar(value=loaded.get("require_m06", "0") == "1")
        self.require_spindle_speed_var = tk.BooleanVar(value=loaded.get("require_spindle_speed", "0") == "1")
        self.ask_backup_var = tk.BooleanVar(value=loaded.get("ask_backup", "1") == "1")
        # 必填 MSG 字段（Batch 2，仅本次运行生效）：默认全部必填；
        # 程序/机床/控制系统固定必填，此处仅暴露 4 个可配置项。
        self.required_bianzhi_var = tk.BooleanVar(value=True)
        self.required_shenhe_var = tk.BooleanVar(value=True)
        self.required_drawing_var = tk.BooleanVar(value=True)
        self.required_part_var = tk.BooleanVar(value=True)
        # M03 补写位置策略（Batch 2，仅本次运行生效）：after-s / standalone。
        self.m03_position_var = tk.StringVar(value="after-s")
        # F/S 上下限（Batch 2，仅本次运行生效）：留空 = 不检查。
        self.feed_min_var = tk.StringVar(value="")
        self.feed_max_var = tk.StringVar(value="")
        self.spindle_min_var = tk.StringVar(value="")
        self.spindle_max_var = tk.StringVar(value="")
        # 换行策略（Batch 2，仅本次运行生效）：auto / crlf / lf。
        self.newline_var = tk.StringVar(value="auto")
        # 辅助指令顺序规则（Batch 2，仅本次运行生效）：默认全部启用。
        self.aux_m03_before_motion_var = tk.BooleanVar(value=True)
        self.aux_m05_before_end_var = tk.BooleanVar(value=True)
        self.aux_m08_before_cut_var = tk.BooleanVar(value=True)
        self.aux_m09_before_end_var = tk.BooleanVar(value=True)

        options = ttk.Frame(info)
        options.grid(row=1, column=0, sticky="ew", padx=4)
        options.columnconfigure(7, weight=1)
        ttk.Button(options, text="全部应用", command=self.apply_info).grid(row=0, column=0, padx=3, sticky="w")
        ttk.Button(options, text="应用所选", command=self.apply_selected).grid(row=0, column=1, padx=3, sticky="w")
        ttk.Button(options, text="保存编制/校对", command=self.save_fields).grid(row=0, column=2, padx=3, sticky="w")
        overwrite_box = ttk.Frame(options)
        overwrite_box.grid(row=0, column=3, padx=3, sticky="w")
        ttk.Checkbutton(overwrite_box, text="覆盖已有非空 MSG 字段", variable=self.overwrite_fields).pack(side="left")
        overwrite_help = ttk.Label(overwrite_box, text="?", cursor="question_arrow", foreground="#1565c0",
                                   font=("TkDefaultFont", 9, "bold"))
        overwrite_help.pack(side="left", padx=(2, 0))
        overwrite_help.bind("<Button-1>", lambda _event: self._show_overwrite_help())
        ttk.Checkbutton(options, text="自动补写 M03", variable=self.auto_m03).grid(row=0, column=4, padx=3, sticky="w")
        ttk.Checkbutton(options, text="自动添加换刀指令", variable=self.auto_tool_change).grid(row=0, column=5, padx=3, sticky="w")
        ttk.Label(options, text="机床：自动 HASS/2500B；控制系统：SIE840D").grid(row=0, column=6, padx=(8, 3), sticky="e")

        # G00 级别已移入程序设置对话框；此处仅保留自定义刀具类型一行。
        custom_type_row = ttk.Frame(info)
        custom_type_row.grid(row=3, column=0, sticky="ew", padx=4)
        ttk.Label(custom_type_row, text="自定义刀具类型").pack(side="left", padx=(0, 4))
        self.new_type_var = tk.StringVar()
        self.custom_tool_type_entry = ttk.Entry(custom_type_row, textvariable=self.new_type_var, width=20)
        self.custom_tool_type_entry.pack(side="left", padx=(0, 4))
        self.add_tool_type_button = ttk.Button(custom_type_row, text="添加类型", command=self.add_tool_type)
        self.add_tool_type_button.pack(side="left")

        drawing_choices = ttk.Frame(info)
        drawing_choices.grid(row=2, column=0, sticky="ew", padx=4, pady=(0, 2))
        drawing_choices.columnconfigure(1, weight=1)
        self.folder_choice_var = tk.StringVar(value=self.folder_choices[3][0])
        ttk.Label(drawing_choices, text="图号候选").grid(row=0, column=0, padx=(0, 4), sticky="w")
        self.folder_choice_combo = ttk.Combobox(drawing_choices, textvariable=self.folder_choice_var, values=[item[0] for item in self.folder_choices], state="readonly", width=8)
        self.folder_choice_combo.grid(row=0, column=1, padx=4, sticky="ew")
        self.drawing_choice_button = ttk.Button(drawing_choices, text="选取此项作为图号", command=self.use_folder_as_drawing)
        self.drawing_choice_button.grid(row=0, column=2, padx=(4, 0), sticky="e")

        actions = ttk.Frame(self)
        actions.pack(fill="x", pady=7)
        actions.columnconfigure(1, weight=1)
        self.all_stats_button = ttk.Button(actions, text="查看全部程序信息", command=self.show_all_program_stats, state="disabled")
        self.all_stats_button.grid(row=0, column=0, padx=3, sticky="w")
        self.status = tk.StringVar(value="正在扫描……")
        ttk.Label(actions, textvariable=self.status).grid(row=0, column=1, padx=12, sticky="w")
        self.export_button = ttk.Button(actions, text="导出报告", command=self.export_report, state="disabled")
        self.export_button.grid(row=0, column=2, padx=3, sticky="e")
        self.process_button = ttk.Button(actions, text="确认并执行处理", command=self.process, state="disabled")
        self.process_button.grid(row=0, column=3, padx=3, sticky="e")

        content = ttk.Frame(self)
        content.pack(fill="both", expand=True)
        content.rowconfigure(0, weight=1)
        content.columnconfigure(0, weight=1, uniform="main")
        content.columnconfigure(1, weight=1, uniform="main")
        left = ttk.Notebook(content)
        right = ttk.Frame(content)
        self.left_notebook = left
        self.right_detail_frame = right
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 3))
        right.grid(row=0, column=1, sticky="nsew", padx=(3, 0))
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1, uniform="detail")
        right.rowconfigure(1, weight=1, uniform="detail")

        keep_frame = ttk.Frame(left)
        apt_frame = ttk.Frame(left)
        delete_frame = ttk.Frame(left)
        left.add(keep_frame, text="保留 / 归档文件")
        left.add(apt_frame, text="APTSOURCE 文件")
        left.add(delete_frame, text="待删除文件")
        self.keep_table, self.keep_issue_table = self._keep_tables(keep_frame)
        self.apt_table = self._table(apt_frame, ("action", "program", "source", "target"), ("处理方式", "程序名", "APTSOURCE 文件", "处理目标"), (100, 125, 250, 240))
        for column, width in (("action", 80), ("program", 100), ("source", 200), ("target", 190)):
            self.apt_table.column(column, width=width, anchor="w")
        self.apt_table.pack(fill="both", expand=True)
        self.delete_table = self._table(delete_frame, ("kind", "action", "source", "reason"), ("类型", "动作", "文件", "说明"), (90, 80, 320, 210))
        for column, width in (("kind", 70), ("action", 70), ("source", 240), ("reason", 180)):
            self.delete_table.column(column, width=width, anchor="w")
        self.delete_table.pack(fill="both", expand=True)

        notebook = ttk.Notebook(right)
        self.detail_notebook = notebook
        notebook.grid(row=0, column=0, sticky="nsew")
        tool_frame = ttk.LabelFrame(right, text="当前程序刀具信息（可编辑）")
        tool_frame.grid(row=1, column=0, sticky="nsew", pady=(4, 0))
        self.tool_frame = tool_frame
        info_page = ttk.Frame(notebook)
        issue_page = ttk.Frame(notebook)
        stats_page = ttk.Frame(notebook)
        self.stats_page = stats_page
        self.info_table = self._table(info_page, ("key", "value"), ("字段 / 解析项目", "当前值"), (180, 560))
        self.issue_table = self._table(issue_page, ("line", "kind", "severity", "text", "suggestion"), ("行", "类型", "级别", "原始文本", "建议"), (45, 90, 60, 220, 300))
        # The statistics page is intentionally compact: one row per
        # program/parameter, with only the requested min/max and G00 check.
        # This makes the all-file overview readable without a horizontal
        # scrollbar at the default window size.
        self.stats_table = self._table(stats_page, ("program", "param", "count", "min", "max", "g00"), ("程序", "参数", "出现次数", "最小值", "最大值", "G00 检查"), (175, 65, 75, 105, 105, 150))
        for column, width in (("key", 140), ("value", 410)):
            self.info_table.column(column, width=width, anchor="w")
        for column, width in (("line", 40), ("kind", 70), ("severity", 55), ("text", 160), ("suggestion", 220)):
            self.issue_table.column(column, width=width, anchor="w")
        for column, width in (("program", 130), ("param", 55), ("count", 65), ("min", 85), ("max", 85), ("g00", 115)):
            self.stats_table.column(column, width=width, anchor="w")
        self.info_table.pack(fill="both", expand=True)
        self.issue_table.pack(fill="both", expand=True)
        self.stats_table.pack(fill="both", expand=True)
        diff_frame = ttk.Frame(notebook)
        diff_frame.rowconfigure(0, weight=1)
        diff_frame.columnconfigure(0, weight=1, uniform="diff")
        diff_frame.columnconfigure(1, weight=1, uniform="diff")
        diff_split = diff_frame
        before_frame = ttk.LabelFrame(diff_split, text="修改前")
        after_frame = ttk.LabelFrame(diff_split, text="修改后")
        before_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 2))
        after_frame.grid(row=0, column=1, sticky="nsew", padx=(2, 0))
        self.diff_before = self._text_with_scrollbars(before_frame)
        self.diff_after = self._text_with_scrollbars(after_frame)
        for widget in (self.diff_before, self.diff_after):
            widget.tag_configure("removed", background="#ffd6d6", foreground="#8b0000")
            widget.tag_configure("added", background="#d9f2d9", foreground="#176b17")
            widget.tag_configure("changed", background="#fff0c2")
            widget.tag_configure("collapsed", background="#e7f1ff", foreground="#1565c0", font=("Consolas", 9, "bold"))
        notebook.add(info_page, text="解析信息")
        notebook.add(issue_page, text="校验问题")
        notebook.add(stats_page, text="参数统计")
        notebook.add(diff_frame, text="修改差异")

        # The editor reserves one grid column; the table and both scrollbars
        # remain packed inside their own child frame.
        tool_frame.rowconfigure(0, weight=1)
        tool_frame.columnconfigure(0, weight=1)
        tool_frame.columnconfigure(1, weight=0, minsize=230)
        table_area = ttk.Frame(tool_frame)
        table_area.grid(row=0, column=0, sticky="nsew", padx=(2, 1))
        editor = ttk.Frame(tool_frame)
        editor.grid(row=0, column=1, sticky="nsew")
        self.tool_editor = editor
        self.tool_table_area = table_area
        editor.columnconfigure(1, weight=1)
        tool_frame = table_area
        self.tool_table = self._table(
            tool_frame,
            tuple(spec[0] for spec in self.tool_column_specs),
            TOOL_TABLE_HEADINGS,
            tuple(spec[2] for spec in self.tool_column_specs),
        )
        for column, _initial, minimum, stretch in self.tool_column_specs:
            self.tool_table.column(
                column, width=minimum, minwidth=minimum, stretch=stretch, anchor="w"
            )
        self.tool_table.pack(side="left", fill="both", expand=True, padx=(2, 0), pady=0)
        self._bind_column_profile(
            self.tool_table,
            self.tool_column_specs,
            lambda: self._treeview_available_width(self.tool_table),
        )
        self._preserve_manual_treeview_width(self.tool_table)
        self.tool_table.bind("<<TreeviewSelect>>", self.load_tool_editor)
        self.tool_vars = {key: tk.StringVar() for key in ("number", "dia", "coner", "angle", "type")}
        for row, (label, key) in enumerate((("刀具号", "number"), ("DIA", "dia"), ("TOOL_CONER", "coner"), ("TOOL_ANGLE", "angle"))):
            ttk.Label(editor, text=label).grid(row=row, column=0, padx=3, pady=1, sticky="e")
            ttk.Entry(editor, textvariable=self.tool_vars[key], width=12).grid(row=row, column=1, padx=3, pady=1, sticky="ew")
        ttk.Label(editor, text="TOOL_TYPE").grid(row=4, column=0, padx=3, pady=1, sticky="e")
        self.tool_type_combo = ttk.Combobox(editor, textvariable=self.tool_vars["type"], values=self.tool_types, width=10)
        self.tool_type_combo.grid(row=4, column=1, padx=3, pady=1, sticky="ew")
        self.upsert_tool_button = ttk.Button(editor, text="新增/更新刀具信息", command=self.upsert_tool)
        self.upsert_tool_button.grid(row=5, column=0, columnspan=2, sticky="ew", pady=1)
        self.delete_tool_button = ttk.Button(editor, text="删除所选刀具", command=self.delete_tool)
        self.delete_tool_button.grid(row=6, column=0, sticky="ew", pady=1)
        self.clear_tool_editor_button = ttk.Button(editor, text="清除编辑内容", command=self.clear_tool_editor)
        self.clear_tool_editor_button.grid(row=6, column=1, sticky="ew", pady=1)

        for table in (
            self.keep_table,
            self.keep_issue_table,
            self.apt_table,
            self.delete_table,
            self.info_table,
            self.issue_table,
            self.stats_table,
            self.tool_table,
        ):
            self._bind_cell_tooltip(table)

    @staticmethod
    def _table(parent, columns, headings, widths, height=1):
        table = ttk.Treeview(parent, columns=columns, show="headings", height=height)
        for col, title, width in zip(columns, headings, widths):
            table.heading(col, text=title)
            # Keep declared widths stable so the horizontal scrollbar has a
            # real overflow range instead of columns silently shrinking.
            table.column(col, width=width, anchor="w")
        ybar = ttk.Scrollbar(parent, orient="vertical", command=table.yview)
        xbar = ttk.Scrollbar(parent, orient="horizontal", command=table.xview)
        table.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        ybar.pack(side="right", fill="y")
        xbar.pack(side="bottom", fill="x")
        return table

    def _keep_tables(self, parent):
        """Build the keep table as two synchronized views.

        Tk Treeview tags are row-scoped, so a second narrow Treeview is used
        for the validation column. This allows only the validation cell to be
        rendered red/bold while the program metadata remains normal.
        """
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        main = ttk.Treeview(
            frame,
            columns=tuple(spec[0] for spec in self.keep_column_specs),
            show="headings",
            height=1,
            selectmode="extended",
        )
        issue = ttk.Treeview(frame, columns=("issues",), show="headings", selectmode="none", height=1)
        headings = tuple(zip((spec[0] for spec in self.keep_column_specs), KEEP_TABLE_HEADINGS))
        for col, title in headings:
            main.heading(col, text=title)
        for column, _initial, minimum, stretch in self.keep_column_specs:
            main.column(
                column, width=minimum, minwidth=minimum, stretch=stretch, anchor="w"
            )
        issue.heading("issues", text="校验")
        issue.column("issues", width=self.validation_column_width, minwidth=self.validation_column_width, stretch=False, anchor="w")
        issue.tag_configure("validation-error", foreground="#c62828", font=(self.ui_font_family, 9, "bold"))
        issue.tag_configure("validation-warning", foreground="#c62828", font=(self.ui_font_family, 9, "bold"))
        ybar = ttk.Scrollbar(frame, orient="vertical")
        xbar = ttk.Scrollbar(frame, orient="horizontal", command=main.xview)
        def move_y(*args):
            main.yview(*args)
        ybar.configure(command=move_y)

        def main_scroll(first, last):
            ybar.set(first, last)
            issue.yview_moveto(first)

        def issue_scroll(first, last):
            # The main tree is authoritative for the scrollbar thumb.
            pass

        main.configure(yscrollcommand=main_scroll, xscrollcommand=xbar.set)
        issue.configure(yscrollcommand=issue_scroll)
        main.grid(row=0, column=0, sticky="nsew")
        issue.grid(row=0, column=1, sticky="ns")
        ybar.grid(row=0, column=2, sticky="ns")
        xbar.grid(row=1, column=0, columnspan=2, sticky="ew")
        main._keep_ybar = ybar
        main._keep_xbar = xbar
        self._bind_column_profile(
            main,
            self.keep_column_specs,
            lambda: self._treeview_available_width(main),
        )
        self._preserve_manual_treeview_width(main)

        def select_issue_row(event):
            selected = issue.identify_row(event.y)
            if selected:
                main.selection_set(selected)
                main.focus(selected)
                main.see(selected)
            self.show_selected()
            return "break"

        main.bind("<<TreeviewSelect>>", self.show_selected)
        issue.bind("<Button-1>", select_issue_row)
        self.keep_table_menu = tk.Menu(self.master, tearoff=0)
        self.keep_table_menu.add_command(label="编辑程序代码", command=self.edit_program_code)
        self.keep_table_menu.add_command(label="修改程序名", command=self.rename_selected_program)
        self.keep_table_menu.add_command(label="对比所选两条程序", command=self.compare_selected_programs)
        main.bind("<Button-3>", self._open_keep_table_menu)
        issue.bind("<Button-3>", self._open_keep_table_menu)
        for tree in (main, issue):
            tree.bind("<MouseWheel>", lambda event: (move_y("scroll", int(-event.delta / 120), "units"), "break")[1])
            tree.bind("<Button-4>", lambda _event: (move_y("scroll", -1, "units"), "break")[1])
            tree.bind("<Button-5>", lambda _event: (move_y("scroll", 1, "units"), "break")[1])
        main._keep_frame = frame
        issue._keep_frame = frame
        return main, issue

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

    @staticmethod
    def _text_with_scrollbars(parent):
        text = tk.Text(parent, wrap="none", font=("Consolas", 9), undo=False, width=1, height=1)
        ybar = ttk.Scrollbar(parent, orient="vertical", command=text.yview)
        xbar = ttk.Scrollbar(parent, orient="horizontal", command=text.xview)
        text.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        ybar.pack(side="right", fill="y")
        xbar.pack(side="bottom", fill="x")
        text.pack(side="left", fill="both", expand=True)
        return text

    def load_saved_fields(self):
        values = load_all(self.settings_registry_key)
        for key, value in values.items():
            if key in self.info_vars:
                self.info_vars[key].set(value)
                self.info_defaults[key] = value
        self.info_vars["date"].set(format_nc_date())

    def save_fields(self):
        backend, location = save_all({"bianzhi": self.info_vars["bianzhi"].get().strip(), "shenhe": self.info_vars["shenhe"].get().strip()}, self.settings_registry_key)
        if backend == "registry":
            self.status.set("编制和审核/校对已保存到当前 Windows 用户设置（注册表）。")
        else:
            self.status.set(f"编制和审核/校对已保存到设置文件：{location}")

    def load_special_tools(self):
        source_path = self.special_tools_path
        if not source_path.exists() and self.legacy_special_tools_path.exists():
            source_path = self.legacy_special_tools_path
        if not source_path.exists():
            return
        try:
            data = json.loads(source_path.read_text(encoding="utf-8"))
            for value in data.get("tool_types", []):
                if value and value not in self.tool_types:
                    self.tool_types.append(str(value))
            for program, rows in data.get("program_tools", {}).items():
                tools = []
                for row in rows:
                    try:
                        tools.append(ToolInfo(int(row.get("number", 0)), str(row.get("dia", "")), str(row.get("tool_coner", "")), str(row.get("tool_type", "")), str(row.get("tool_angle", ""))))
                    except (TypeError, ValueError):
                        pass
                if tools:
                    self.program_tools[program] = sorted(tools, key=lambda x: x.number)
            self.tool_type_combo.configure(values=self.tool_types)
        except (OSError, ValueError, TypeError):
            self.status.set("特殊刀具配置读取失败，将使用自动识别结果。")

    def save_special_tools(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "tool_types": self.tool_types,
            "program_tools": {
                program: [{"number": tool.number, "dia": tool.dia, "tool_coner": tool.tool_coner, "tool_type": tool.tool_type, "tool_angle": tool.tool_angle} for tool in tools]
                for program, tools in self.program_tools.items()
            },
        }
        temp = self.special_tools_path.with_suffix(".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.special_tools_path)

    def use_folder_as_drawing(self):
        selected = self.folder_choice_var.get()
        name = next((value for label, value in self.folder_choices if label == selected), "")
        self.info_vars["drawing"].set(name)
        self.info_defaults["drawing"] = name
        self.status.set("已选取图号候选；点击“应用设置”后才会用于处理。")

    def _set_drawing_choice(self, label):
        self.folder_choice_var.set(label)

    def open_settings(self):
        if getattr(self, "settings_window", None) is not None and self.settings_window.winfo_exists():
            self.settings_window.lift()
            return
        self._settings_snapshot = {
            "encoding": self.encoding_var.get(),
            "delete_extensions": self.delete_extensions_var.get(),
            "allowed_name_pattern": self.allowed_name_pattern_var.get(),
            "aptsource_dir": self.aptsource_dir_var.get(),
            "program_extensions": self.program_extensions_var.get(),
            "program_output_extension": self.program_output_extension_var.get(),
            "require_end_marker": self.require_end_marker_var.get(),
            "require_m06": self.require_m06_var.get(),
            "require_spindle_speed": self.require_spindle_speed_var.get(),
            "ask_backup": self.ask_backup_var.get(),
            "required_bianzhi": self.required_bianzhi_var.get(),
            "required_shenhe": self.required_shenhe_var.get(),
            "required_drawing": self.required_drawing_var.get(),
            "required_part": self.required_part_var.get(),
            "m03_position": self.m03_position_var.get(),
            "feed_min": self.feed_min_var.get(),
            "feed_max": self.feed_max_var.get(),
            "spindle_min": self.spindle_min_var.get(),
            "spindle_max": self.spindle_max_var.get(),
            "newline": self.newline_var.get(),
            "g00_level": self.g00_level.get(),
            "aux_m03_before_motion": self.aux_m03_before_motion_var.get(),
            "aux_m05_before_end": self.aux_m05_before_end_var.get(),
            "aux_m08_before_cut": self.aux_m08_before_cut_var.get(),
            "aux_m09_before_end": self.aux_m09_before_end_var.get(),
        }
        win = tk.Toplevel(self.master)
        win.title("程序设置")
        win.transient(self.master)
        win.resizable(False, False)
        notebook = ttk.Notebook(win)
        notebook.pack(fill="both", expand=True, padx=10, pady=(10, 0))
        basic = ttk.Frame(notebook, padding=8)
        rules = ttk.Frame(notebook, padding=8)
        notebook.add(basic, text="基本设置")
        notebook.add(rules, text="校验规则")
        self.settings_notebook = notebook
        self.settings_pages = (basic, rules)

        def labeled(page, row, text, widget):
            ttk.Label(page, text=text).grid(row=row, column=0, sticky="w", padx=(0, 6), pady=3)
            widget.grid(row=row, column=1, sticky="w", pady=3)

        # ── 基本设置：编码 / 扩展名 / 允许字符 / APTSOURCE ──
        encoding_combo = ttk.Combobox(basic, textvariable=self.encoding_var, state="readonly", width=16,
                                      values=("auto", "utf-8", "utf-8-sig", "gb18030", "gbk", "gb2312", "cp1252"))
        labeled(basic, 0, "文件编码", encoding_combo)
        ttk.Label(basic, text="自动识别或强制指定").grid(row=0, column=2, sticky="w", padx=(6, 0))

        delete_entry = ttk.Entry(basic, textvariable=self.delete_extensions_var, width=24)
        labeled(basic, 1, "待删除扩展名", delete_entry)
        ttk.Button(basic, text="恢复默认", command=lambda: self.delete_extensions_var.set(".log, .moaptindexes")).grid(row=1, column=2, padx=(6, 0))
        ttk.Label(basic, text="逗号分隔，如 .log,.moaptindexes；留空则全部保留").grid(row=2, column=1, columnspan=2, sticky="w")

        pattern_entry = ttk.Entry(basic, textvariable=self.allowed_name_pattern_var, width=24)
        labeled(basic, 3, "程序名允许字符", pattern_entry)
        ttk.Button(basic, text="恢复默认", command=lambda: self.allowed_name_pattern_var.set(r"^[A-Za-z0-9_一-鿿-]+$")).grid(row=3, column=2, padx=(6, 0))

        apt_entry = ttk.Entry(basic, textvariable=self.aptsource_dir_var, width=24)
        labeled(basic, 4, "APTSOURCE 归档子目录", apt_entry)

        program_ext_entry = ttk.Entry(basic, textvariable=self.program_extensions_var, width=24)
        labeled(basic, 5, "主程序扩展名", program_ext_entry)
        ttk.Label(basic, text="逗号分隔，如 .mpf,.nc,.txt").grid(row=5, column=2, sticky="w", padx=(6, 0))

        output_ext_entry = ttk.Entry(basic, textvariable=self.program_output_extension_var, width=24)
        labeled(basic, 6, "输出扩展名", output_ext_entry)
        ttk.Label(basic, text="如 .MPF 或 .nc").grid(row=6, column=2, sticky="w", padx=(6, 0))
        ttk.Checkbutton(basic, text="处理前询问备份（关闭则不询问也不备份）", variable=self.ask_backup_var).grid(row=7, column=1, sticky="w", pady=3)

        # ── 校验规则：G00 / 必填字段 / M03 / S/F / 换行 / 辅助顺序 ──
        ttk.Checkbutton(rules, text="要求程序结束标记（%/M30/M02）", variable=self.require_end_marker_var).grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 0))
        ttk.Checkbutton(rules, text="要求刀具调用包含 M06", variable=self.require_m06_var).grid(row=1, column=0, columnspan=4, sticky="w")
        ttk.Checkbutton(rules, text="要求切削前有 S 转速", variable=self.require_spindle_speed_var).grid(row=2, column=0, columnspan=4, sticky="w", pady=(0, 4))

        g00_combo = ttk.Combobox(rules, textvariable=self.g00_level, state="readonly", width=10,
                                 values=("error", "warning", "allow"))
        labeled(rules, 3, "G00 级别", g00_combo)
        ttk.Label(rules, text="error 报错 / warning 提示 / allow 放行").grid(row=3, column=2, sticky="w", padx=(6, 0))

        ttk.Label(rules, text="必填 MSG 字段").grid(row=4, column=0, sticky="w", padx=(0, 6), pady=(4, 3))
        required_frame = ttk.Frame(rules)
        required_frame.grid(row=4, column=1, columnspan=3, sticky="w", pady=(4, 3))
        for text, var in (
            ("编制", self.required_bianzhi_var),
            ("审核", self.required_shenhe_var),
            ("图号", self.required_drawing_var),
            ("版次", self.required_part_var),
        ):
            ttk.Checkbutton(required_frame, text=text, variable=var).pack(side="left", padx=8)
        ttk.Label(rules, text="程序/机床/控制系统固定必填").grid(row=5, column=1, columnspan=3, sticky="w", pady=(0, 3))

        m03_combo = ttk.Combobox(rules, textvariable=self.m03_position_var, state="readonly", width=14,
                                 values=("after-s", "standalone"))
        labeled(rules, 6, "M03 补写位置", m03_combo)
        ttk.Label(rules, text="紧贴 S 数值后 / 独立行").grid(row=6, column=2, sticky="w", padx=(6, 0))

        ttk.Label(rules, text="F 上下限").grid(row=7, column=0, sticky="w", padx=(0, 6), pady=(6, 3))
        feed_frame = ttk.Frame(rules)
        feed_frame.grid(row=7, column=1, columnspan=3, sticky="w", pady=(6, 3))
        ttk.Entry(feed_frame, textvariable=self.feed_min_var, width=8).pack(side="left")
        ttk.Label(feed_frame, text="~").pack(side="left", padx=4)
        ttk.Entry(feed_frame, textvariable=self.feed_max_var, width=8).pack(side="left")
        ttk.Label(rules, text="S 上下限").grid(row=8, column=0, sticky="w", padx=(0, 6), pady=(6, 3))
        spindle_frame = ttk.Frame(rules)
        spindle_frame.grid(row=8, column=1, columnspan=3, sticky="w", pady=(6, 3))
        ttk.Entry(spindle_frame, textvariable=self.spindle_min_var, width=8).pack(side="left")
        ttk.Label(spindle_frame, text="~").pack(side="left", padx=4)
        ttk.Entry(spindle_frame, textvariable=self.spindle_max_var, width=8).pack(side="left")
        ttk.Label(rules, text="留空 = 不检查").grid(row=9, column=1, columnspan=3, sticky="w", pady=(0, 3))

        newline_combo = ttk.Combobox(rules, textvariable=self.newline_var, state="readonly", width=14,
                                     values=("auto", "crlf", "lf"))
        labeled(rules, 10, "换行策略", newline_combo)
        ttk.Label(rules, text="auto 跟随源文件；crlf/lf 强制").grid(row=10, column=2, sticky="w", padx=(6, 0))

        ttk.Label(rules, text="辅助指令顺序").grid(row=11, column=0, sticky="w", padx=(0, 6), pady=(6, 3))
        aux_rows = (
            (("M03 先于切削", self.aux_m03_before_motion_var), ("M05 先于结束", self.aux_m05_before_end_var)),
            (("M08 先于切削", self.aux_m08_before_cut_var), ("M09 先于结束", self.aux_m09_before_end_var)),
        )
        for row_offset, row_items in enumerate(aux_rows):
            for col, (text, var) in enumerate(row_items):
                ttk.Checkbutton(rules, text=text, variable=var).grid(
                    row=12 + row_offset, column=1 + col, sticky="w", pady=(6 if row_offset == 0 else 0, 3))

        actions = ttk.Frame(win, padding=(10, 0, 10, 10))
        actions.pack(fill="x")
        ttk.Button(actions, text="恢复默认", command=self._restore_default_settings).pack(side="left")
        ttk.Button(actions, text="清除注册表", command=self._clear_registry_settings).pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="确定", command=self._confirm_settings).pack(side="right")
        ttk.Button(actions, text="取消", command=self._cancel_settings).pack(side="right", padx=(0, 8))
        win.bind("<Return>", lambda _event: self._confirm_settings())
        win.bind("<Escape>", lambda _event: self._cancel_settings())
        self._show_centered(win)
        self.settings_window = win

    def _parsed_delete_extensions(self):
        return parse_delete_extensions(self.delete_extensions_var.get())

    def _parsed_program_extensions(self):
        return parse_extension_list(self.program_extensions_var.get())

    def _parsed_output_extension(self):
        return parse_output_extension(self.program_output_extension_var.get())

    @staticmethod
    def _parsed_limit(raw):
        """解析可选数值上限/下限：留空 → None；非法或负数 → ValueError。"""
        value = (raw or "").strip()
        if not value:
            return None
        parsed = float(value)
        if parsed < 0:
            raise ValueError(f"上下限不得为负数：{value}")
        return parsed

    def _confirm_settings(self):
        try:
            self._parsed_delete_extensions()
            re.compile(self.allowed_name_pattern_var.get().strip())
            self._parsed_program_extensions()
            self._parsed_output_extension()
            for var in (self.feed_min_var, self.feed_max_var, self.spindle_min_var, self.spindle_max_var):
                self._parsed_limit(var.get())
        except (ValueError, re.error) as error:
            messagebox.showerror("程序设置无效", str(error), parent=self.settings_window)
            return
        self._save_settings_values()
        self.settings_window.destroy()
        self.settings_window = None
        self.scan()

    def _save_settings_values(self):
        save_all({
            "encoding": self.encoding_var.get().strip(),
            "delete_extensions": self.delete_extensions_var.get().strip(),
            "allowed_name_pattern": self.allowed_name_pattern_var.get().strip(),
            "aptsource_dir": self.aptsource_dir_var.get().strip() or "aptsource",
            "program_extensions": self.program_extensions_var.get().strip(),
            "program_output_extension": self._parsed_output_extension(),
            "require_end_marker": "1" if self.require_end_marker_var.get() else "0",
            "require_m06": "1" if self.require_m06_var.get() else "0",
            "require_spindle_speed": "1" if self.require_spindle_speed_var.get() else "0",
            "ask_backup": "1" if self.ask_backup_var.get() else "0",
        }, self.settings_registry_key)

    def _apply_settings_defaults(self):
        defaults = REGISTRY_DEFAULTS
        self.encoding_var.set(defaults["encoding"])
        self.delete_extensions_var.set(defaults["delete_extensions"])
        self.allowed_name_pattern_var.set(defaults["allowed_name_pattern"])
        self.aptsource_dir_var.set(defaults["aptsource_dir"])
        self.program_extensions_var.set(defaults["program_extensions"])
        self.program_output_extension_var.set(defaults["program_output_extension"])
        self.require_end_marker_var.set(defaults["require_end_marker"] == "1")
        self.require_m06_var.set(defaults["require_m06"] == "1")
        self.require_spindle_speed_var.set(defaults["require_spindle_speed"] == "1")
        self.ask_backup_var.set(defaults["ask_backup"] == "1")
        # 必填 MSG 字段恢复默认：全部必填（Batch 2 仅本次运行生效）
        self.required_bianzhi_var.set(True)
        self.required_shenhe_var.set(True)
        self.required_drawing_var.set(True)
        self.required_part_var.set(True)
        self.m03_position_var.set("after-s")
        self.feed_min_var.set("")
        self.feed_max_var.set("")
        self.spindle_min_var.set("")
        self.spindle_max_var.set("")
        self.newline_var.set("auto")
        self.g00_level.set("error")
        self.aux_m03_before_motion_var.set(True)
        self.aux_m05_before_end_var.set(True)
        self.aux_m08_before_cut_var.set(True)
        self.aux_m09_before_end_var.set(True)
        # 统一恢复/清除：编制与审核（主窗口表单）一并回到默认（空）
        self.info_vars["bianzhi"].set("")
        self.info_vars["shenhe"].set("")
        self.info_defaults["bianzhi"] = ""
        self.info_defaults["shenhe"] = ""

    def _restore_default_settings(self):
        """恢复全部默认值并立即写入注册表（含编制/审核）。"""
        self._apply_settings_defaults()
        save_all(dict(REGISTRY_DEFAULTS), self.settings_registry_key)
        self.settings_window.destroy()
        self.settings_window = None
        self.scan()

    def _clear_registry_settings(self):
        """删除全部持久化的设置值（含编制/审核）并回到默认值。"""
        backend, location = storage_backend(self.settings_registry_key)
        if backend == "registry":
            question = "将删除 HKCU\\Software\\NCodeProcess 下的全部程序设置值（含编制/审核），确定？"
        else:
            question = f"将删除设置文件 {location} 中的全部程序设置值（含编制/审核），确定？"
        if not messagebox.askyesno(
            "清除设置",
            question,
            parent=self.settings_window,
        ):
            return
        clear_all(self.settings_registry_key)
        self._apply_settings_defaults()
        self.settings_window.destroy()
        self.settings_window = None
        self.scan()

    def _cancel_settings(self):
        snapshot = getattr(self, "_settings_snapshot", {})
        self.encoding_var.set(snapshot.get("encoding", self.encoding_var.get()))
        self.delete_extensions_var.set(snapshot.get("delete_extensions", self.delete_extensions_var.get()))
        self.allowed_name_pattern_var.set(snapshot.get("allowed_name_pattern", self.allowed_name_pattern_var.get()))
        self.aptsource_dir_var.set(snapshot.get("aptsource_dir", self.aptsource_dir_var.get()))
        self.program_extensions_var.set(snapshot.get("program_extensions", self.program_extensions_var.get()))
        self.program_output_extension_var.set(snapshot.get("program_output_extension", self.program_output_extension_var.get()))
        self.require_end_marker_var.set(snapshot.get("require_end_marker", self.require_end_marker_var.get()))
        self.require_m06_var.set(snapshot.get("require_m06", self.require_m06_var.get()))
        self.require_spindle_speed_var.set(snapshot.get("require_spindle_speed", self.require_spindle_speed_var.get()))
        self.ask_backup_var.set(snapshot.get("ask_backup", self.ask_backup_var.get()))
        self.required_bianzhi_var.set(snapshot.get("required_bianzhi", self.required_bianzhi_var.get()))
        self.required_shenhe_var.set(snapshot.get("required_shenhe", self.required_shenhe_var.get()))
        self.required_drawing_var.set(snapshot.get("required_drawing", self.required_drawing_var.get()))
        self.required_part_var.set(snapshot.get("required_part", self.required_part_var.get()))
        self.m03_position_var.set(snapshot.get("m03_position", self.m03_position_var.get()))
        self.feed_min_var.set(snapshot.get("feed_min", self.feed_min_var.get()))
        self.feed_max_var.set(snapshot.get("feed_max", self.feed_max_var.get()))
        self.spindle_min_var.set(snapshot.get("spindle_min", self.spindle_min_var.get()))
        self.spindle_max_var.set(snapshot.get("spindle_max", self.spindle_max_var.get()))
        self.newline_var.set(snapshot.get("newline", self.newline_var.get()))
        self.g00_level.set(snapshot.get("g00_level", self.g00_level.get()))
        self.aux_m03_before_motion_var.set(snapshot.get("aux_m03_before_motion", self.aux_m03_before_motion_var.get()))
        self.aux_m05_before_end_var.set(snapshot.get("aux_m05_before_end", self.aux_m05_before_end_var.get()))
        self.aux_m08_before_cut_var.set(snapshot.get("aux_m08_before_cut", self.aux_m08_before_cut_var.get()))
        self.aux_m09_before_end_var.set(snapshot.get("aux_m09_before_end", self.aux_m09_before_end_var.get()))
        self.settings_window.destroy()
        self.settings_window = None

    def apply_info(self):
        v = self.info_vars
        if not v["drawing"].get().strip() or not v["version"].get().strip():
            messagebox.showerror("信息不完整", "图号和版次为必填项。未应用设置，也不会修改任何 MPF 文件。", parent=self.master)
            return
        self.applied_info = ProgramInfo(v["bianzhi"].get().strip(), v["shenhe"].get().strip(), v["drawing"].get().strip(), v["version"].get().strip(), "", "SIE840D", v["date"].get().strip())
        self.info_defaults.update({key: v[key].get().strip() for key in self.info_defaults})
        if self.scan_result is not None:
            for plan_file in self.scan_result.files:
                if plan_file.kind == "mpf" and plan_file.program:
                    self.program_header_values[plan_file.program] = {
                        "bianzhi": v["bianzhi"].get().strip(),
                        "shenhe": v["shenhe"].get().strip(),
                        "drawing": v["drawing"].get().strip(),
                        "version": v["version"].get().strip(),
                        "date": v["date"].get().strip(),
                    }
        self.status.set("程序信息已应用，正在刷新预览……")
        self.scan()

    def _show_overwrite_help(self):
        messagebox.showinfo(
            "覆盖已有非空 MSG 字段",
            "该选项仅更新可编辑头部字段：编制（BIANZHI）、审核/校对（SHENHE）、图号（DRAWING NUMBER）与版次（PART VERSION）；"
            "程序名、机床、控制系统与日期等受保护字段保持不变。\n\n"
            "勾选后：点击“应用所选”将更改直接写入所选文件；点击“全部应用”生成预览后，再点击“确认并执行处理”将更改写入全部文件。\n"
            "未勾选时：应用操作仅生成并展示预览，不写入文件。",
            parent=self.master,
        )

    def apply_selected(self):
        """Apply the program-info fields only to the selected MPF rows."""
        v = self.info_vars
        if not v["drawing"].get().strip() or not v["version"].get().strip():
            messagebox.showerror("信息不完整", "图号和版次为必填项。未应用设置，也不会修改任何 MPF 文件。", parent=self.master)
            return
        selection = self.keep_table.selection()
        if not selection:
            messagebox.showwarning("未选择程序", "请先在保留/归档文件表选择程序（可多选）。", parent=self.master)
            return
        self.applied_info = ProgramInfo(v["bianzhi"].get().strip(), v["shenhe"].get().strip(), v["drawing"].get().strip(), v["version"].get().strip(), "", "SIE840D", v["date"].get().strip())
        self.info_defaults.update({key: v[key].get().strip() for key in self.info_defaults})
        preview_config = self.config()   # 覆盖勾选时按表单新值覆盖可编辑字段，未勾选时按默认逻辑（保留已有值）
        applied_plans = []
        for iid in selection:
            try:
                plan_file = self.scan_result.files[int(iid)]
            except (IndexError, TypeError, ValueError):
                continue
            if plan_file.kind == "mpf" and plan_file.program and plan_file.original_text is not None:
                reprocess_file(plan_file, self.info(), preview_config, tools=self.program_tools.get(plan_file.program, []))
                self.program_header_values[plan_file.program] = {
                    "bianzhi": v["bianzhi"].get().strip(),
                    "shenhe": v["shenhe"].get().strip(),
                    "drawing": v["drawing"].get().strip(),
                    "version": v["version"].get().strip(),
                    "date": v["date"].get().strip(),
                }
                applied_plans.append(plan_file)
        if not applied_plans:
            return
        mode = "覆盖修改" if self.overwrite_fields.get() else "按默认逻辑（保留已有值）"
        self.status.set(f"已生成 {len(applied_plans)} 个程序的预览（{mode}）。确认无误后点击“确认并执行处理”写入文件。")
        # 立即用内存预览刷新表格与右侧信息（含新的头部/刀具）。
        self.populate_file_tables()
        for plan_file in applied_plans:
            row = next((str(i) for i, item in enumerate(self.scan_result.files) if item is plan_file), None)
            if row is not None and self.keep_table.exists(row):
                self.keep_table.selection_add(row)
        self.show_selected()

    def add_tool_type(self):
        value = self.new_type_var.get().strip()
        if value and value not in self.tool_types:
            self.tool_types.append(value)
            self.tool_type_combo.configure(values=self.tool_types)
            self.new_type_var.set("")
            self.save_special_tools()

    def config(self):
        try:
            delete_extensions = self._parsed_delete_extensions()
            program_extensions = self._parsed_program_extensions()
            program_output_extension = self._parsed_output_extension()
            feed_min = self._parsed_limit(self.feed_min_var.get())
            feed_max = self._parsed_limit(self.feed_max_var.get())
            spindle_min = self._parsed_limit(self.spindle_min_var.get())
            spindle_max = self._parsed_limit(self.spindle_max_var.get())
        except ValueError:
            delete_extensions = {".log", ".moaptindexes"}
            program_extensions = {".mpf"}
            program_output_extension = ".MPF"
            feed_min = feed_max = spindle_min = spindle_max = None
        required_flags = {
            "BIANZHI": self.required_bianzhi_var.get(),
            "SHENHE": self.required_shenhe_var.get(),
            "DRAWING NUMBER": self.required_drawing_var.get(),
            "PART VERSION": self.required_part_var.get(),
        }
        aux_flags = {
            "m03-before-motion": self.aux_m03_before_motion_var.get(),
            "m05-before-end": self.aux_m05_before_end_var.get(),
            "m08-before-cut": self.aux_m08_before_cut_var.get(),
            "m09-before-end": self.aux_m09_before_end_var.get(),
        }
        return Config(
            recursive=self.recursive.get(),
            save_aptsource=self.save_aptsource.get(),
            aptsource_dir=self.aptsource_dir_var.get().strip() or "aptsource",
            overwrite_fields=self.overwrite_fields.get(),
            auto_m03=self.auto_m03.get(),
            auto_tool_change=self.auto_tool_change.get(),
            defer_stats=False,
            g00_level=self.g00_level.get(),
            delete_extensions=delete_extensions,
            allowed_name_pattern=self.allowed_name_pattern_var.get().strip(),
            encoding=self.encoding_var.get().strip(),
            require_end_marker=self.require_end_marker_var.get(),
            require_m06=self.require_m06_var.get(),
            require_spindle_speed=self.require_spindle_speed_var.get(),
            program_extensions=program_extensions,
            program_output_extension=program_output_extension,
            required_fields=[key for key, _label, _required in FIELD_ORDER if required_flags.get(key, True)],
            m03_position=self.m03_position_var.get(),
            feed_min=feed_min,
            feed_max=feed_max,
            spindle_min=spindle_min,
            spindle_max=spindle_max,
            newline=self.newline_var.get(),
            aux_checks={name for name, enabled in aux_flags.items() if enabled},
            ask_backup=self.ask_backup_var.get(),
        )

    def info(self):
        return ProgramInfo(self.applied_info.bianzhi, self.applied_info.shenhe, self.applied_info.drawing_number, self.applied_info.part_version, "", "SIE840D", self.applied_info.date)

    def scan(self, *, overwrite_fields=None):
        self.process_button.configure(state="disabled")
        self.all_stats_button.configure(state="disabled")
        if self.all_stats_window is not None and self.all_stats_window.winfo_exists():
            self.all_stats_window.destroy()
            self.all_stats_window = None
        self.status.set("正在扫描 EXE 所在目录……")
        self._scan_generation += 1
        generation = self._scan_generation
        config = self.config()
        if overwrite_fields is not None:
            # 预览模式：始终按表单值覆盖可编辑字段生成预览（显示修改效果）。
            config.overwrite_fields = overwrite_fields
        info = self.info()
        def work():
            result = build_plan(scan_directory(str(self.workdir), config), info, config, self.program_tools)
            self._safe_after(0, lambda: self.finish_scan(result, generation))
        threading.Thread(target=work, daemon=True).start()

    def finish_scan(self, result, generation=None):
        if generation is not None and generation != self._scan_generation:
            return
        if result.warnings:
            messagebox.showwarning("扫描提示", "\n".join(result.warnings), parent=self.master)
        unresolved = [f for f in result.files if f.kind == "mpf" and not f.program]
        changed = False
        pattern = self.allowed_name_pattern_var.get().strip()
        if len(unresolved) == 1:
            f = unresolved[0]
            value = simpledialog.askstring("确认程序名", "无法确定程序名：" + f.source, parent=self.master)
            if value and re.match(pattern, value.strip()):
                f.program = value.strip()
                f.issues = [i for i in f.issues if i.kind != "program-name"]
                changed = True
        elif len(unresolved) > 1:
            values = self._confirm_program_names(unresolved)
            if values:
                for f in unresolved:
                    name = values.get(f.source, "")
                    if name and re.match(pattern, name):
                        f.program = name
                        f.issues = [i for i in f.issues if i.kind != "program-name"]
                        changed = True
        if changed:
            result = build_plan(result, self.info(), self.config(), self.program_tools)
        # 记住当前选中程序，扫描完成后恢复选中并刷新右侧预览。
        previous_program = None
        if self.scan_result is not None and self.keep_table.selection():
            try:
                previous_program = self.scan_result.files[int(self.keep_table.selection()[0])].program
            except (IndexError, TypeError, ValueError):
                previous_program = None
        self.scan_result = result
        # A single existing PART VERSION/DRAWING NUMBER is a safe
        # preselection for the next processing pass. Both remain editable and
        # still require Apply before any MPF modification.
        if not self.applied_info.part_version and not self.info_vars["version"].get().strip():
            versions = sorted({
                extract_header_fields(f.original_text or "").get("PART VERSION", "").strip()
                for f in result.files if f.kind == "mpf"
            } - {""})
            if len(versions) == 1:
                self.info_vars["version"].set(versions[0])
                self.info_defaults["version"] = versions[0]
        if not self.applied_info.drawing_number and not self.info_vars["drawing"].get().strip():
            drawings = sorted({
                extract_header_fields(f.original_text or "").get("DRAWING NUMBER", "").strip()
                for f in result.files if f.kind == "mpf"
            } - {""})
            if len(drawings) == 1:
                self.info_vars["drawing"].set(drawings[0])
                self.info_defaults["drawing"] = drawings[0]
        # APT headers can provide an authoritative drawing number.  Add those
        # values to the same chooser as folder names, but leave the entry
        # field untouched until the user clicks the read button.
        self.folder_choices = merge_drawing_choices(folder_drawing_choices(self.workdir), result.drawing_candidates)
        self.folder_choice_combo.configure(values=[item[0] for item in self.folder_choices])
        if self.folder_choice_var.get() not in [item[0] for item in self.folder_choices]:
            self._set_drawing_choice(self.folder_choices[3][0])
        self.populate_file_tables()
        restored = False
        if previous_program:
            for iid in self.keep_table.get_children():
                try:
                    if self.scan_result.files[int(iid)].program == previous_program:
                        self.keep_table.selection_set(iid)
                        self.keep_table.focus(iid)
                        restored = True
                        break
                except (IndexError, TypeError, ValueError):
                    continue
        if restored:
            self.show_selected()
        mpfs = sum(f.kind == "mpf" for f in result.files)
        self.status.set(f"扫描完成：{len(result.files)} 个文件，{mpfs} 个 MPF；从保留/归档表选择 MPF 查看解析信息。")
        self.process_button.configure(state="normal" if result.files else "disabled")
        self.all_stats_button.configure(state="normal" if mpfs else "disabled")

    def _confirm_program_names(self, unresolved):
        """List-style batch confirmation for unnamed programs.

        Returns {source: program_name} on confirm, or None when cancelled.
        """
        result = {"values": None}
        window = tk.Toplevel(self.master)
        window.title("确认程序名")
        window.transient(self.master)
        container = ttk.Frame(window, padding=12)
        container.pack(fill="both", expand=True)
        container.columnconfigure(0, weight=1)
        container.rowconfigure(1, weight=1)
        ttk.Label(
            container,
            text="以下程序无法自动确定程序名，请逐一填写（留空跳过，非法字符将忽略）：",
            wraplength=720,
        ).grid(row=0, column=0, sticky="w", pady=(0, 10))
        detail = ttk.Frame(container)
        detail.grid(row=1, column=0, sticky="nsew")
        canvas = tk.Canvas(detail, highlightthickness=0)
        ybar = ttk.Scrollbar(detail, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.columnconfigure(1, weight=1)
        inner_id = canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=ybar.set)
        ybar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        def _update_scrollregion(_event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _stretch_inner(event):
            canvas.itemconfigure(inner_id, width=event.width)

        def _on_wheel(event):
            canvas.yview_scroll(-int(event.delta / 120), "units")
            return "break"

        inner.bind("<Configure>", _update_scrollregion)
        canvas.bind("<Configure>", _stretch_inner)
        canvas.bind("<MouseWheel>", _on_wheel)
        vars_by_source = {}
        for row, plan_file in enumerate(unresolved):
            ttk.Label(inner, text=plan_file.source).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=2)
            var = tk.StringVar()
            ttk.Entry(inner, textvariable=var).grid(row=row, column=1, sticky="ew", pady=2)
            vars_by_source[plan_file.source] = var
        buttons = ttk.Frame(container)
        buttons.grid(row=2, column=0, sticky="e", pady=(12, 0))

        def close(confirmed=False):
            if confirmed:
                result["values"] = {source: var.get().strip() for source, var in vars_by_source.items()}
            window.destroy()

        ttk.Button(buttons, text="取消", command=lambda: close(False)).pack(side="right", padx=(8, 0))
        ttk.Button(buttons, text="确定", command=lambda: close(True)).pack(side="right")
        window.protocol("WM_DELETE_WINDOW", lambda: close(False))
        self._show_centered(window, 720, 480, min_width=600, min_height=360)
        window.grab_set()
        window.wait_window()
        return result["values"]

    def populate_file_tables(self):
        self._cancel_cell_tooltip()
        for table in (self.keep_table, self.keep_issue_table, self.apt_table, self.delete_table):
            for item in table.get_children():
                table.delete(item)
        # 未命名 MPF 置顶显示，便于先行确认；iid 仍与 scan_result.files 索引一致。
        order = sorted(
            range(len(self.scan_result.files)),
            key=lambda index: (not (self.scan_result.files[index].kind == "mpf" and self.scan_result.files[index].program is None), index),
        )
        for idx in order:
            f = self.scan_result.files[idx]
            errors = sum(i.severity == "error" for i in f.issues)
            warnings = sum(i.severity == "warning" for i in f.issues)
            if f.kind == "mpf":
                issue_text = f"{errors} 错 / {warnings} 警"
                tag = "validation-error" if errors else ("validation-warning" if warnings else "")
                self.keep_table.insert("", "end", iid=str(idx), values=(
                    f.action,
                    f.program or "待确认",
                    _display_with_gap(f.source),
                    _display_with_gap(Path(f.target).name if f.target else ""),
                ))
                self.keep_issue_table.insert("", "end", iid=str(idx), values=(issue_text,), tags=(tag,) if tag else ())
            elif f.kind == "aptsource":
                action = "保存并归档" if f.action == "move" else "不保存（删除）"
                target = self.display_target(f.target) if f.target else "执行确认后删除"
                self.apt_table.insert("", "end", iid=str(idx), values=(action, f.program or "未识别", f.source, target))
            else:
                reason = "按规则清理 LOG/MOAPTIndexes"
                self.delete_table.insert("", "end", iid=str(idx), values=(f.kind, f.action, f.source, reason))

    def display_target(self, target):
        if not target:
            return ""
        try:
            return str(Path(target).resolve().relative_to(self.workdir.resolve()))
        except ValueError:
            return str(target)

    def selected_plan(self):
        selection = self.keep_table.selection()
        if not selection or not self.scan_result:
            return None
        return self.scan_result.files[int(selection[0])]

    @staticmethod
    def clear_table(table):
        for item in table.get_children():
            table.delete(item)

    def show_selected(self, _event=None):
        f = self.selected_plan()
        if not f:
            return
        self.clear_table(self.info_table)
        self.clear_table(self.issue_table)
        self.clear_table(self.stats_table)
        self.render_diff("", "")
        self.info_table.insert("", "end", values=("源文件", f.source))
        self.info_table.insert("", "end", values=("规范程序名", f.program or ""))
        self.info_table.insert("", "end", values=("目标文件", f.target or ""))
        self.info_table.insert("", "end", values=("文件编码", f.encoding or ""))
        existing_fields = extract_header_fields(f.original_text or "")
        header_mapping = {
            "bianzhi": "BIANZHI",
            "shenhe": "SHENHE",
            "drawing": "DRAWING NUMBER",
            "version": "PART VERSION",
            "date": "DATE",
        }
        applied_override = self.program_header_values.get(f.program or "", {})
        for key, header_key in header_mapping.items():
            if key in applied_override and applied_override[key]:
                # 该程序已应用过顶部信息：保持显示更改后的值，不被文件旧值刷回。
                self.info_vars[key].set(applied_override[key])
            else:
                existing_value = existing_fields.get(header_key, "").strip()
                fallback = self.info_defaults.get(key, "").strip()
                if key == "date" and not fallback:
                    fallback = format_nc_date()
                self.info_vars[key].set(existing_value or fallback)
        self.add_msg_rows(f.original_text, "已有/")
        self.add_msg_rows(f.output_text, "处理后/")
        if f.kind != "mpf":
            self.current_program = None
            self.refresh_tool_table([])
            return
        # The processed preview already contains the selected source's tool
        # rows.  Reading it back keeps the table aligned with the newest APT
        # result; special_tools.json is only visible when no APT tools exist.
        tools = extract_tools(f.output_text or f.original_text or "") if f.program else []
        self.current_program = f.program
        self.refresh_tool_table(tools)
        for issue in f.issues:
            self.issue_table.insert("", "end", values=(issue.line, issue.kind, issue.severity, issue.text, issue.suggestion))
        if f.stats is None and f.output_text is not None:
            f.stats = calculate_stats(f.output_text)
        if f.stats:
            self._insert_stats_rows(f.program or "", f.stats)
        if f.original_text is not None and f.output_text is not None:
            self.render_diff(f.original_text, f.output_text)

    @staticmethod
    def _stat_value(value):
        return "无数据" if value is None else (f"{value:.3f}" if isinstance(value, float) else str(value))

    def _insert_stats_rows(self, program, stats):
        for key in "FSXYZ":
            self.stats_table.insert(
                "", "end",
                values=(program, key, stats.counts.get(key, 0), self._stat_value(stats.minimum.get(key)), self._stat_value(stats.maximum.get(key)), "发现" + str(stats.g00_count) + " 处" if stats.g00_count else "未发现"),
            )

    def show_all_program_stats(self):
        """Open an independent all-program overview window."""
        if not self.scan_result:
            return
        if self.all_stats_window is not None and self.all_stats_window.winfo_exists():
            self.all_stats_window.deiconify()
            self.all_stats_window.lift()
            return
        window = tk.Toplevel(self.master)
        self.all_stats_window = window
        window.title("全部程序参数统计")
        window.transient(self.master)
        table = self._table(
            window,
            ("program", "f_count", "f_min", "f_max", "s_count", "s_min", "s_max", "x_count", "x_min", "x_max", "y_count", "y_min", "y_max", "z_count", "z_min", "z_max", "g00"),
            ("程序", "F 次数", "F 最小", "F 最大", "S 次数", "S 最小", "S 最大", "X 次数", "X 最小", "X 最大", "Y 次数", "Y 最小", "Y 最大", "Z 次数", "Z 最小", "Z 最大", "G00 检查"),
            (170, 60, 80, 80, 60, 80, 80, 60, 80, 80, 60, 80, 80, 60, 80, 80, 120),
        )
        table.pack(fill="both", expand=True, padx=8, pady=8)
        self._bind_cell_tooltip(table)
        rows = sorted((f for f in self.scan_result.files if f.kind == "mpf" and f.program), key=lambda item: item.program or "")
        for f in rows:
            if f.stats is None and f.output_text is not None:
                f.stats = calculate_stats(f.output_text)
            if f.stats:
                s = f.stats
                value = lambda key: self._stat_value(s.minimum.get(key))
                maximum = lambda key: self._stat_value(s.maximum.get(key))
                table.insert("", "end", values=(
                    f.program or "", s.counts.get("F", 0), value("F"), maximum("F"),
                    s.counts.get("S", 0), value("S"), maximum("S"),
                    s.counts.get("X", 0), value("X"), maximum("X"),
                    s.counts.get("Y", 0), value("Y"), maximum("Y"),
                    s.counts.get("Z", 0), value("Z"), maximum("Z"),
                    "发现 " + str(s.g00_count) + " 处" if s.g00_count else "未发现",
                ))

        def close_window():
            if window.winfo_exists():
                window.destroy()
            self.all_stats_window = None

        screen_width = window.winfo_screenwidth()
        screen_height = window.winfo_screenheight()
        width = min(1500, max(1050, screen_width - 80))
        height = min(720, max(560, screen_height - 100))
        self._show_centered(window, width, height, min_width=1050, min_height=420)
        window.protocol("WM_DELETE_WINDOW", close_window)

    def render_diff(self, before, after):
        """Render changed lines with three surrounding context lines."""
        widgets = (self.diff_before, self.diff_after)
        for widget in widgets:
            widget.configure(state="normal")
            widget.delete("1.0", "end")
        rows, hidden = compact_diff_rows(before, after, context=3)

        def put(widget, number, line, tag=""):
            prefix = "     | " if number is None else f"{number:04d} | "
            widget.insert("end", prefix + line + "\n", tag)

        for before_no, before_line, before_tag, after_no, after_line, after_tag in rows:
            put(self.diff_before, before_no, before_line, before_tag)
            put(self.diff_after, after_no, after_line, after_tag)
        if hidden:
            summary = f"共 {hidden} 行未修改（已折叠）"
            put(self.diff_before, None, summary, "collapsed")
            put(self.diff_after, None, summary, "collapsed")
        for widget in widgets:
            widget.configure(state="disabled")

    def add_msg_rows(self, text, prefix):
        if not text:
            return
        for line in text.splitlines()[:80]:
            match = re.match(r'\s*MSG\(\s*["\'](.*?)["\']\s*\)\s*;?', line, re.I)
            if match:
                key, separator, value = match.group(1).partition(":")
                if separator:
                    self.info_table.insert("", "end", values=(prefix + key, value))

    def refresh_tool_table(self, tools):
        self._cancel_cell_tooltip()
        self.clear_table(self.tool_table)
        for idx, tool in enumerate(sorted(tools, key=lambda x: x.number)):
            self.tool_table.insert("", "end", iid=str(idx), values=(tool.number, tool.dia, tool.tool_coner, tool.tool_angle, tool.tool_type))
        self.clear_tool_editor()

    def load_tool_editor(self, _event=None):
        selected = self.tool_table.selection()
        if not selected:
            return
        values = self.tool_table.item(selected[0], "values")
        for key, value in zip(("number", "dia", "coner", "angle", "type"), values):
            self.tool_vars[key].set(value)

    def clear_tool_editor(self):
        for var in self.tool_vars.values():
            var.set("")

    def current_tool_list(self):
        result = []
        for item in self.tool_table.get_children():
            values = self.tool_table.item(item, "values")
            try:
                result.append(ToolInfo(int(values[0]), values[1], values[2], values[4], values[3]))
            except (ValueError, IndexError):
                pass
        return result

    def upsert_tool(self):
        if not self.current_program:
            messagebox.showwarning("未选择 MPF", "请先从保留/归档文件表选择 MPF。", parent=self.master)
            return
        try:
            number = int(self.tool_vars["number"].get())
            if number <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("刀具号错误", "刀具号必须是正整数。", parent=self.master)
            return
        tools = self.current_tool_list()
        replacement = ToolInfo(number, self.tool_vars["dia"].get().strip(), self.tool_vars["coner"].get().strip(), self.tool_vars["type"].get().strip(), self.tool_vars["angle"].get().strip())
        tools = [tool for tool in tools if tool.number != number]
        tools.append(replacement)
        self.program_tools[self.current_program] = sorted(tools, key=lambda x: x.number)
        self.save_special_tools()
        self.rebuild_selected_preview()

    def delete_tool(self):
        if not self.current_program:
            return
        selected = self.tool_table.selection()
        if not selected:
            return
        number = int(self.tool_table.item(selected[0], "values")[0])
        tools = [tool for tool in self.current_tool_list() if tool.number != number]
        self.program_tools[self.current_program] = tools
        self.save_special_tools()
        self.rebuild_selected_preview()

    def rebuild_selected_preview(self):
        f = self.selected_plan()
        selection = self.keep_table.selection()
        if not f or f.kind != "mpf" or not f.original_text or not f.program:
            return
        reprocess_file(f, self.info(), self.config(), tools=self.program_tools.get(f.program, []))
        self.populate_file_tables()
        if selection and self.keep_table.exists(selection[0]):
            self.keep_table.selection_set(selection[0])
            self.keep_table.focus(selection[0])
        self.show_selected()

    def _refresh_keep_menu_states(self):
        """Enable the edit/rename/compare entries based on the current selection."""
        count = len(self.keep_table.selection())
        self.keep_table_menu.entryconfig(0, state="normal" if count == 1 else "disabled")
        self.keep_table_menu.entryconfig(1, state="normal" if count == 1 else "disabled")
        self.keep_table_menu.entryconfig(2, state="normal" if count == 2 else "disabled")

    def rename_selected_program(self):
        """Rename the selected MPF program (target file + PROGRAM MSG)."""
        f = self.selected_plan()
        if not f or f.kind != "mpf":
            return
        pattern = self.allowed_name_pattern_var.get().strip()
        value = simpledialog.askstring(
            "修改程序名",
            f"当前程序名：{f.program or '未命名'}\n输入新的程序名：",
            parent=self.master,
            initialvalue=f.program or "",
        )
        if not value:
            return
        value = value.strip()
        if not re.match(pattern, value):
            messagebox.showerror("程序名无效", "程序名不符合允许字符规则，未修改。", parent=self.master)
            return
        if value == f.program:
            return
        f.program = value
        f.target = str(Path(self.workdir) / (value + self.config().program_output_extension))
        f.issues = [issue for issue in f.issues if issue.kind != "program-name"]
        # 同步原文本中的 PROGRAM MSG，使重处理后的字段与文件名一致。
        program_msg = re.compile(r'(?i)^(\s*MSG\(\s*["\']PROGRAM:)[^"\']*')
        f.original_text = program_msg.sub(lambda m: m.group(1) + value, f.original_text)
        self.rebuild_selected_preview()

    def _open_keep_table_menu(self, event):
        self._refresh_keep_menu_states()
        try:
            self.keep_table_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.keep_table_menu.grab_release()

    def compare_selected_programs(self):
        """Open a side-by-side diff of exactly two selected MPF programs."""
        selection = self.keep_table.selection()
        if len(selection) != 2 or not self.scan_result:
            return
        plans = []
        for iid in selection:
            try:
                f = self.scan_result.files[int(iid)]
            except (IndexError, TypeError, ValueError):
                return
            if f.kind != "mpf" or f.original_text is None or not f.program:
                return
            plans.append(f)
        left, right = plans[0], plans[1]
        if self.program_compare_window is not None and self.program_compare_window.winfo_exists():
            self.program_compare_window.deiconify()
            self.program_compare_window.lift()
            return

        def compare_label(f):
            """文件名优先，程序名补充：a_P.MPF（P）。"""
            file_name = Path(f.source).name or ""
            if file_name and f.program:
                return f"{file_name}（{f.program}）"
            return file_name or f.program or ""

        window = tk.Toplevel(self.master)
        self.program_compare_window = window
        window.title(f"程序差异对比：{compare_label(left)} vs {compare_label(right)}")
        window.transient(self.master)
        frame = ttk.Frame(window, padding=8)
        frame.pack(fill="both", expand=True)
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1, uniform="compare")
        frame.columnconfigure(1, weight=1, uniform="compare")

        def build_pane(column, title):
            box = ttk.LabelFrame(frame, text=title)
            box.grid(row=0, column=column, sticky="nsew", padx=(0, 2) if column == 0 else (2, 0))
            box.rowconfigure(0, weight=1)
            box.columnconfigure(0, weight=1)
            body = ttk.Frame(box)
            body.grid(row=0, column=0, sticky="nsew")
            body.rowconfigure(0, weight=1)
            body.columnconfigure(1, weight=1)
            gutter = tk.Text(
                body,
                width=5,
                padx=6,
                pady=0,
                takefocus=0,
                borderwidth=0,
                highlightthickness=0,
                background="#f6f8fa",
                foreground="#57606a",
                font=("Consolas", 9),
                state="disabled",
                wrap="none",
            )
            gutter.grid(row=0, column=0, sticky="ns")
            pane = tk.Text(body, wrap="none", font=("Consolas", 9), state="disabled", borderwidth=0, highlightthickness=0, padx=2, pady=0)
            pane.grid(row=0, column=1, sticky="nsew")
            ybar = ttk.Scrollbar(body, orient="vertical", command=pane.yview)
            ybar.grid(row=0, column=2, sticky="ns")
            xbar = ttk.Scrollbar(box, orient="horizontal", command=pane.xview)
            xbar.grid(row=1, column=0, sticky="ew")
            # 每个区域独立滚动：内容与自身行号一起滚，左右两侧互不影响。
            def pane_scroll(*args):
                pane.yview(*args)
                gutter.yview(*args)

            def pane_sync(*args):
                ybar.set(*args)
                gutter.yview_moveto(args[0])

            def on_wheel(event):
                pane.yview_scroll(-int(event.delta / 120), "units")
                return "break"

            ybar.configure(command=pane_scroll)
            pane.configure(yscrollcommand=pane_sync, xscrollcommand=xbar.set)
            gutter.bind("<MouseWheel>", on_wheel)
            # 不同部分红底、相同部分绿底。
            pane.tag_configure("removed", background="#ffd6d6", foreground="#8b0000")
            pane.tag_configure("added", background="#ffd6d6", foreground="#8b0000")
            pane.tag_configure("changed", background="#ffd6d6", foreground="#8b0000")
            pane.tag_configure("equal", background="#d9f2d9", foreground="#176b17")
            return pane, gutter

        left_text, left_gutter = build_pane(0, "程序A：" + compare_label(left))
        right_text, right_gutter = build_pane(1, "程序B：" + compare_label(right))
        self.program_compare_left = left_text
        self.program_compare_right = right_text
        self.program_compare_left_gutter = left_gutter
        self.program_compare_right_gutter = right_gutter

        left_numbers: List[str] = []
        right_numbers: List[str] = []
        left_num = right_num = 0
        for left_row, left_tag, right_row, right_tag in align_lines(left.original_text, right.original_text):
            # 只插入实际内容行，行号连续；对齐产生的空行占位被压缩，
            # 避免短程序一侧出现大片无编号空白。
            if left_row:
                left_text.configure(state="normal")
                left_text.insert("end", left_row + "\n", left_tag or "equal")
                left_text.configure(state="disabled")
                left_num += 1
                left_numbers.append(str(left_num))
            if right_row:
                right_text.configure(state="normal")
                right_text.insert("end", right_row + "\n", right_tag or "equal")
                right_text.configure(state="disabled")
                right_num += 1
                right_numbers.append(str(right_num))
        for gutter, numbers in ((left_gutter, left_numbers), (right_gutter, right_numbers)):
            gutter.configure(state="normal")
            gutter.delete("1.0", "end")
            gutter.insert("1.0", "\n".join(numbers))
            gutter.configure(state="disabled")

        def close():
            window.destroy()
            self.program_compare_window = None

        self._show_centered(window, 1100, 650, min_width=800, min_height=480)
        window.protocol("WM_DELETE_WINDOW", close)

    def edit_program_code(self, _event=None):
        """Open an editable copy of the selected MPF; saving re-reviews it.

        This is the manual escape hatch for files whose M03 could not be
        auto-inserted: the operator fixes the NC code and saving runs the
        whole preview pipeline again before anything is written to disk.
        """
        f = self.selected_plan()
        if not f or f.kind != "mpf" or f.original_text is None or not f.program:
            return
        if self.program_editor_window is not None and self.program_editor_window.winfo_exists():
            self.program_editor_window.deiconify()
            self.program_editor_window.lift()
            return
        window = tk.Toplevel(self.master)
        self.program_editor_window = window
        window.title("编辑程序代码：" + f.program)
        window.transient(self.master)
        container = ttk.Frame(window, padding=8)
        container.pack(fill="both", expand=True)
        container.columnconfigure(0, weight=1)
        container.rowconfigure(0, weight=1)
        editor = ttk.LabelFrame(container, text="NC 程序代码（保存后自动重新审查）")
        editor.grid(row=0, column=0, sticky="nsew")
        editor.rowconfigure(0, weight=1)
        editor.columnconfigure(0, weight=1)
        body = ttk.Frame(editor)
        body.grid(row=0, column=0, sticky="nsew")
        body.rowconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)
        gutter = tk.Text(
            body,
            width=5,
            padx=6,
            pady=0,
            takefocus=0,
            borderwidth=0,
            highlightthickness=0,
            background="#f6f8fa",
            foreground="#57606a",
            font=("Consolas", 10),
            state="disabled",
            wrap="none",
        )
        gutter.grid(row=0, column=0, sticky="ns")
        text = tk.Text(body, wrap="none", font=("Consolas", 10), undo=True, borderwidth=0, highlightthickness=0, padx=2, pady=0)
        text.grid(row=0, column=1, sticky="nsew")
        ybar = ttk.Scrollbar(body, orient="vertical", command=text.yview)
        ybar.grid(row=0, column=2, sticky="ns")
        xbar = ttk.Scrollbar(editor, orient="horizontal", command=text.xview)
        xbar.grid(row=1, column=0, sticky="ew")
        self.program_editor_gutter = gutter
        self.program_editor_text = text

        def update_line_numbers(_event=None):
            gutter.configure(state="normal")
            gutter.delete("1.0", "end")
            count = int(text.index("end-1c").split(".")[0])
            gutter.insert("1.0", "\n".join(str(n) for n in range(1, count + 1)))
            gutter.configure(state="disabled")

        def ysync(*args):
            ybar.set(*args)
            gutter.yview_moveto(args[0])

        def on_wheel(event):
            text.yview_scroll(-int(event.delta / 120), "units")
            return "break"

        text.configure(yscrollcommand=ysync, xscrollcommand=xbar.set)
        text.bind("<<Modified>>", lambda _event: (update_line_numbers(), text.edit_modified(False))[1])
        text.bind("<Configure>", update_line_numbers)
        gutter.bind("<MouseWheel>", on_wheel)
        text.insert("1.0", f.original_text)
        update_line_numbers()
        buttons = ttk.Frame(container)
        buttons.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        ttk.Label(buttons, text="修改保存后将重新生成预览并审查；正式写入仍需执行目录处理。").pack(side="left")

        def save():
            new_text = text.get("1.0", "end-1c")
            window.destroy()
            self.program_editor_window = None
            if new_text == f.original_text:
                return
            f.original_text = new_text
            reprocess_file(f, self.info(), self.config(), tools=self.program_tools.get(f.program, []))
            row = next((str(i) for i, item in enumerate(self.scan_result.files) if item is f), None)
            self.populate_file_tables()
            if row is not None and self.keep_table.exists(row):
                self.keep_table.selection_set(row)
                self.keep_table.focus(row)
            self.show_selected()
            self.status.set(f"已保存 {f.program} 的代码编辑并重新审查。")

        def close():
            window.destroy()
            self.program_editor_window = None

        self.program_editor_save_command = save
        ttk.Button(buttons, text="取消", command=close).pack(side="right", padx=(0, 8))
        ttk.Button(buttons, text="保存并重新审查", command=save).pack(side="right")
        window.protocol("WM_DELETE_WINDOW", close)
        self._show_centered(window, 900, 650, min_width=700, min_height=480)
        text.focus_set()

    def process(self):
        if not self.scan_result:
            return
        if not self.applied_info.drawing_number.strip() or not self.applied_info.part_version.strip():
            messagebox.showerror("信息不完整", "图号和版次未通过“应用设置”提交，已放弃本次修改。", parent=self.master)
            return
        has_changes = any(
            f.changes or f.action in ("delete", "move", "duplicate")
            for f in self.scan_result.files
        )
        if not has_changes:
            messagebox.showinfo(
                "无更改",
                "当前没有需要执行的处理：程序信息没有变化，也没有文件清理、归档或重命名操作。",
                parent=self.master,
            )
            return
        deletes = [f.source for f in self.scan_result.files if f.action == "delete"]
        duplicates = [f for f in self.scan_result.files if f.action == "duplicate"]
        mpfs = sum(f.kind == "mpf" and f.action != "duplicate" for f in self.scan_result.files)
        apt_archives = sum(f.kind == "aptsource" and f.action == "move" for f in self.scan_result.files)
        apt_deletes = sum(f.kind == "aptsource" and f.action == "delete" for f in self.scan_result.files)
        intermediate_deletes = sum(f.kind != "aptsource" and f.action == "delete" for f in self.scan_result.files)
        apt_summary = f"归档 {apt_archives} 个 APTSOURCE" if self.save_aptsource.get() else f"删除 {apt_deletes} 个 APTSOURCE"
        summary = f"将处理 {mpfs} 个 MPF，{apt_summary}，处理 {len(duplicates)} 个重复文件，删除 {intermediate_deletes} 个中间文件。"
        detail_lines = []
        if duplicates:
            detail_lines.append("【重复文件处理】")
            for duplicate in duplicates:
                detail_lines.extend((
                    f"较旧文件：{duplicate.source}",
                    f"采用最新文件：{duplicate.duplicate_winner}",
                    f"目标文件：{Path(duplicate.duplicate_target).name}",
                    "",
                ))
            detail_lines.append("最新文件成功写入后，较旧重复源文件将被清理。")
        if deletes:
            if detail_lines:
                detail_lines.append("")
            detail_lines.append("【待删除文件】")
            detail_lines.extend(deletes)
        modified = [f for f in self.scan_result.files if f.kind == "mpf" and f.action in ("keep", "move") and f.changes and f.program]
        if modified:
            if detail_lines:
                detail_lines.append("")
            detail_lines.append("【将修改的 MPF】")
            for plan_file in modified[:50]:
                detail_lines.append(f"{plan_file.source} → {Path(plan_file.target).name if plan_file.target else ''}")
                for change in plan_file.changes:
                    detail_lines.append(f"  · {change}")
            if len(modified) > 50:
                detail_lines.append(f"  … 其余 {len(modified) - 50} 个文件略")
        if not self.confirm_processing(summary, detail_lines):
            return
        backup = self._backup_requested() if self.config().ask_backup else False
        self.process_button.configure(state="disabled")
        self.status.set("正在处理当前目录……")
        self._processing = True
        self._safe_after(100, self._poll_process_progress)
        def work():
            def report(done, total, name):
                with self._process_progress_lock:
                    self._process_progress = (done, total, name)
            result = process_plan(self.scan_result, str(self.workdir), self.config(), confirm_cleanup=True, progress_callback=report, backup=backup)
            self._safe_after(0, lambda: self.finish_process(result))
        threading.Thread(target=work, daemon=True).start()

    def _backup_requested(self):
        """Ask the operator whether to snapshot files before processing."""
        return messagebox.askyesno(
            "处理前备份",
            "执行前是否先将待处理文件备份到 backup\\时间戳 目录？\n推荐在首次处理正式目录前使用。",
            parent=self.master,
        )

    def _poll_process_progress(self):
        with self._process_progress_lock:
            progress = self._process_progress
        if progress is not None:
            done, total, name = progress
            self.status.set(f"正在处理当前目录……（{done}/{total}）{name}")
        if self._process_progress is not None or self._processing:
            self._safe_after(100, self._poll_process_progress)

    def confirm_processing(self, summary, detail_lines):
        """Confirm processing without allowing long details to hide buttons."""
        if not needs_detailed_confirmation(detail_lines):
            detail = "\n\n" + "\n".join(detail_lines) if detail_lines else ""
            return messagebox.askyesno("确认当前目录处理", summary + detail + "\n\n确定继续吗？", parent=self.master)

        result = {"confirmed": False}
        window = tk.Toplevel(self.master)
        window.title("确认目录处理")
        window.transient(self.master)
        container = ttk.Frame(window, padding=12)
        container.pack(fill="both", expand=True)
        container.columnconfigure(0, weight=1)
        container.rowconfigure(1, weight=1)
        ttk.Label(container, text=summary, font=("Microsoft YaHei UI", 10, "bold"), wraplength=840).grid(row=0, column=0, sticky="ew", pady=(0, 10))
        detail_frame = ttk.LabelFrame(container, text="处理明细")
        detail_frame.grid(row=1, column=0, sticky="nsew")
        text = self._text_with_scrollbars(detail_frame)
        text.insert("1.0", "\n".join(detail_lines))
        text.configure(state="disabled", font=("Microsoft YaHei UI", 9))
        buttons = ttk.Frame(container)
        buttons.grid(row=2, column=0, sticky="e", pady=(12, 0))

        def close(confirmed=False):
            result["confirmed"] = confirmed
            window.destroy()

        ttk.Button(buttons, text="取消", command=lambda: close(False)).pack(side="right", padx=(8, 0))
        ttk.Button(buttons, text="确认执行", command=lambda: close(True)).pack(side="right")
        window.protocol("WM_DELETE_WINDOW", lambda: close(False))
        self._show_centered(window, 900, 650, min_width=700, min_height=460)
        window.grab_set()
        window.wait_window()
        return result["confirmed"]

    def finish_process(self, report):
        self.report = report
        self._processing = False
        with self._process_progress_lock:
            self._process_progress = None
        self.status.set(f"处理完成：成功 {report.success}，失败 {report.failed}，移动 {report.moved}，删除 {report.deleted}。")
        self.export_button.configure(state="normal")
        messagebox.showinfo("处理完成", self.status.get() + "\n报告未自动生成；如有需要，请点击“导出报告”。", parent=self.master)
        self.scan()

    def export_report(self):
        if not self.report:
            return
        try:
            path = save_timestamped_report(self.report, self.data_dir, keep=3)
        except OSError as exc:
            messagebox.showerror("导出失败", f"无法将报告保存到：\n{self.data_dir}\n\n{exc}", parent=self.master)
            return
        messagebox.showinfo("导出完成", f"报告已自动保存到：\n{path}", parent=self.master)


def main():
    root = tk.Tk()
    root.withdraw()
    try:
        style = ttk.Style()
        style.theme_use("vista")
        style.configure("Treeview", rowheight=24, font=("Microsoft YaHei UI", 9))
        style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 9, "bold"))
        style.configure("TButton", padding=(8, 4))
    except tk.TclError:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
