from __future__ import annotations

import ctypes
import difflib
import json
import os
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
    DEFAULT_NAME_PATTERN,
    FIELD_ORDER,
    Issue,
    ProgramInfo,
    ToolInfo,
    align_lines,
    analyze_plan_file,
    build_plan,
    calculate_stats,
    emit_event,
    extract_header_fields,
    extract_tools,
    format_nc_date,
    process_plan,
    recount_retracts,
    reprocess_file,
    save_timestamped_report,
    scan_directory,
)
from .preferences import (
    KEY as PREFERENCES_KEY,
    REGISTRY_DEFAULTS,
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


def parse_positive_default(raw: str, default: float) -> float:
    """解析启发式阈值输入：留空、非法或非正数时回退默认值。"""
    value = (raw or "").strip()
    if not value:
        return default
    try:
        parsed = float(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def parse_non_negative_int(raw: str) -> int:
    """解析整数上限输入：留空、非法或非正数时返回 0（= 不限制）。"""
    value = (raw or "").strip()
    if not value:
        return 0
    try:
        parsed = int(float(value))
    except ValueError:
        return 0
    return parsed if parsed > 0 else 0


def text_changed_ignoring_line_endings(original: str, edited: str) -> bool:
    """比较编辑前后文本，忽略 CRLF/LF 差异，防止仅行尾变化触发重处理。"""
    def normalize(value: str) -> str:
        return value.replace("\r\n", "\n").replace("\r", "\n")
    return normalize(original) != normalize(edited)


_MUTEX_HANDLE = None


def single_instance_mutex_name(anchor_path: str) -> str:
    """基于 EXE/脚本绝对路径生成稳定的命名互斥体名（FNV-1a 64 位，非密码学用途）。

    用 FNV-1a 替代 hashlib.md5，使打包时可排除 hashlib/_hashlib（WP-S1，体积优化）。
    """
    normalized = os.path.normcase(os.path.abspath(anchor_path)).encode("utf-8")
    value = 0xCBF29CE484222325
    for byte in normalized:
        value ^= byte
        value = (value * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    digest = format(value, "016x")
    return "NCodeProcess_" + digest


def acquire_single_instance(anchor_path: str) -> bool:
    """创建命名互斥体；同路径已有实例运行时返回 False（Win32）。"""
    global _MUTEX_HANDLE
    if sys.platform != "win32":
        return True
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.CreateMutexW(None, False, single_instance_mutex_name(anchor_path))
    if not handle:
        return True
    if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        kernel32.CloseHandle(handle)
        return False
    _MUTEX_HANDLE = handle
    return True


def release_single_instance() -> None:
    """释放命名互斥体（测试与退出清理用）。"""
    global _MUTEX_HANDLE
    if _MUTEX_HANDLE and sys.platform == "win32":
        ctypes.windll.kernel32.CloseHandle(_MUTEX_HANDLE)
        _MUTEX_HANDLE = None


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


def fixed_treeview_map(style, option):
    """Work around the Tk 8.6.9 style.map bug that overrides tag colors.

    Filtering out the (!disabled, !selected) mapping entries lets
    tag_configure(foreground/background) render again.  Reference:
    https://core.tcl.tk/tk/info/509cafafae
    """
    return [elm for elm in style.map("Treeview", query_opt=option)
            if elm[:2] != ("!disabled", "!selected")]



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
    validation_width = max(round(82 * scale), measure("E999W999I999") + 20)
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
    level_labels = ("当前", "上一级", "上二级", "上三级")
    for level in range(4):
        label = level_labels[level] + "文件夹名：" + current.name
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


def reprocess_plans(plans, info, config, program_tools):
    """内存重处理一组 FilePlan（后台线程调用，不触碰任何 Tk 对象）。

    返回实际完成重处理的计划列表；仅处理 MPF、有程序名且可读取原始文本的计划。
    """
    applied = []
    for plan in plans:
        if plan.kind == "mpf" and plan.program and plan.original_text is not None:
            reprocess_file(plan, info, config, tools=program_tools.get(plan.program, []))
            applied.append(plan)
    return applied


class App(ttk.Frame):
    def __init__(self, master, settings_registry_key=None):
        super().__init__(master, padding=8)
        self.master.title("NCodeProcess " + __version__)
        self.settings_registry_key = settings_registry_key or PREFERENCES_KEY
        self._loaded_settings = load_all(self.settings_registry_key)
        emit_event("info", "settings_loaded", "程序设置加载完成")
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
        self._scan_running = False
        self._scan_progress = (0, 0)
        self.report = None
        self.info_vars = {}
        self.info_defaults = {key: "" for key in ("bianzhi", "shenhe", "drawing", "version", "date")}
        self.applied_info = ProgramInfo()
        self.program_header_values = {}
        self.program_tools = {}
        # WP-A2：按程序名记忆的手动抬刀高度（本次运行内有效，重新扫描后保留）。
        self.apt_retract_heights = {}
        # 识别数据页抬刀高度输入框的自动填充基准值：失焦时值未变则不提交，
        # 避免“切页即误设抬刀高度”的假象（仅回车/确认按钮真正提交）。
        self._apt_retract_baseline = ""
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
        # 必填 MSG 字段（持久化）：默认全部必填；程序/机床/控制系统固定必填。
        self.required_bianzhi_var = tk.BooleanVar(value=loaded.get("required_bianzhi", "1") == "1")
        self.required_shenhe_var = tk.BooleanVar(value=loaded.get("required_shenhe", "1") == "1")
        self.required_drawing_var = tk.BooleanVar(value=loaded.get("required_drawing", "1") == "1")
        self.required_part_var = tk.BooleanVar(value=loaded.get("required_part", "1") == "1")
        # M03 补写位置策略（持久化）：after-s / standalone。
        self.m03_position_var = tk.StringVar(value=loaded.get("m03_position", "after-s"))
        # F/S 上下限（持久化）：缺失或空白值一律回退默认（20/10000/500/12000），
        # 避免旧版本保存的空值把输入框显示成空白；留空确认后下次仍回默认。
        self.feed_min_var = tk.StringVar(value=loaded.get("feed_min") or "20")
        self.feed_max_var = tk.StringVar(value=loaded.get("feed_max") or "10000")
        self.spindle_min_var = tk.StringVar(value=loaded.get("spindle_min") or "500")
        self.spindle_max_var = tk.StringVar(value=loaded.get("spindle_max") or "12000")
        # 换行策略（持久化）：auto / crlf / lf。
        self.newline_var = tk.StringVar(value=loaded.get("newline", "auto"))
        # 辅助指令顺序规则（持久化）：默认全部启用。
        self.aux_m03_before_motion_var = tk.BooleanVar(value=loaded.get("aux_m03_before_motion", "1") == "1")
        self.aux_m05_before_end_var = tk.BooleanVar(value=loaded.get("aux_m05_before_end", "1") == "1")
        self.aux_m08_before_cut_var = tk.BooleanVar(value=loaded.get("aux_m08_before_cut", "1") == "1")
        self.aux_m09_before_end_var = tk.BooleanVar(value=loaded.get("aux_m09_before_end", "1") == "1")
        self.multiple_spindle_var = tk.BooleanVar(value=loaded.get("multiple_spindle_warn", "1") == "1")
        # WP-C1：文件大小/数量上限（持久化，留空 = 不限制）。
        self.max_file_size_var = tk.StringVar(value=loaded.get("max_file_size", ""))
        self.max_files_var = tk.StringVar(value=loaded.get("max_files", ""))
        # WP-C9：抬刀高度阈值（持久化，默认 20）。
        self.retract_z_threshold_var = tk.StringVar(value=loaded.get("retract_z_threshold", "20"))
        # 配置保存位置（持久化）：registry / appdata / home，默认注册表。
        # WP-R2：启动时按实际存在性检测保存位置（哪个位置有配置即为保存位置）。
        self.storage_backend_var = tk.StringVar(value=storage_backend(self.settings_registry_key)[0])

        options = ttk.Frame(info)
        options.grid(row=1, column=0, sticky="ew", padx=4)
        options.columnconfigure(7, weight=1)
        self.apply_all_button = ttk.Button(options, text="全部应用", command=self.apply_info)
        self.apply_all_button.grid(row=0, column=0, padx=3, sticky="w")
        self.apply_selected_button = ttk.Button(options, text="应用所选", command=self.apply_selected)
        self.apply_selected_button.grid(row=0, column=1, padx=3, sticky="w")
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
        self.scan_progress = ttk.Progressbar(actions, orient="horizontal", mode="determinate",
                                             maximum=100, length=120)
        self.scan_progress.grid(row=0, column=2, padx=3, sticky="w")
        self.scan_progress.grid_remove()
        self.export_button = ttk.Button(actions, text="导出报告", command=self.export_report, state="disabled")
        self.export_button.grid(row=0, column=3, padx=3, sticky="e")
        self.process_button = ttk.Button(actions, text="确认并执行处理", command=self.process, state="disabled")
        self.process_button.grid(row=0, column=4, padx=3, sticky="e")

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
        recog_page = ttk.Frame(notebook)
        self.stats_page = stats_page
        self.info_table = self._table(info_page, ("key", "value"), ("字段 / 解析项目", "当前值"), (180, 560))
        self.issue_table = self._table(issue_page, ("line", "kind", "severity", "text", "suggestion"), ("行", "类型", "级别", "原始文本", "建议"), (45, 90, 60, 220, 300))
        # The statistics page is intentionally compact: one row per
        # program/parameter, with only the requested min/max and G00 check.
        # This makes the all-file overview readable without a horizontal
        # scrollbar at the default window size.
        stats_frame = ttk.Frame(stats_page)
        stats_frame.pack(fill="both", expand=True)
        self.stats_table = self._table(stats_frame, ("program", "param", "count", "min", "max", "g00"), ("程序", "参数", "出现次数", "最小值", "最大值", "G00 检查"), (175, 65, 75, 105, 105, 150))
        for column, width in (("key", 140), ("value", 410)):
            self.info_table.column(column, width=width, anchor="w")
        for column, width in (("line", 40), ("kind", 70), ("severity", 55), ("text", 160), ("suggestion", 220)):
            self.issue_table.column(column, width=width, anchor="w")
        for column, width in (("program", 130), ("param", 55), ("count", 65), ("min", 85), ("max", 85), ("g00", 115)):
            self.stats_table.column(column, width=width, anchor="w")
        self.info_table.pack(fill="both", expand=True)
        self.issue_table.pack(fill="both", expand=True)
        self.stats_table.pack(fill="both", expand=True)
        # 「识别数据」页签：合并 APT 轨迹与 F 离群检测明细，避免占用参数统计/校验问题主区。
        # 页签内部用固定高度 Canvas + 垂直滚动承载两个展示区，请求高度与其他页一致，
        # 避免识别数据内容把整个窗口初始尺寸撑高。
        recog_canvas = tk.Canvas(recog_page, height=40, highlightthickness=0, background="#eef2f7")
        recog_scroll = ttk.Scrollbar(recog_page, orient="vertical", command=recog_canvas.yview)
        recog_canvas.configure(yscrollcommand=recog_scroll.set)
        recog_style = ttk.Style(recog_page)
        try:
            recog_style.configure("Recog.TFrame", background="#eef2f7")
        except tk.TclError:
            pass
        recog_frame = ttk.Frame(recog_canvas, style="Recog.TFrame")
        recog_window = recog_canvas.create_window((0, 0), window=recog_frame, anchor="nw")

        def _on_recog_configure(_event=None):
            recog_canvas.configure(scrollregion=recog_canvas.bbox("all"))
            recog_canvas.itemconfigure(recog_window, width=recog_canvas.winfo_width())

        def _on_recog_wheel(event):
            recog_canvas.yview_scroll(-int(event.delta / 120), "units")
            return "break"

        def _bind_recog_wheel(widget):
            widget.bind("<MouseWheel>", _on_recog_wheel)
            widget.bind("<Button-4>", lambda _event: (recog_canvas.yview_scroll(-1, "units"), "break")[1])
            widget.bind("<Button-5>", lambda _event: (recog_canvas.yview_scroll(1, "units"), "break")[1])
            for child in widget.winfo_children():
                _bind_recog_wheel(child)

        recog_frame.bind("<Configure>", _on_recog_configure)
        recog_canvas.bind("<Configure>", _on_recog_configure)
        _bind_recog_wheel(recog_canvas)
        _bind_recog_wheel(recog_frame)
        recog_canvas.pack(side="left", fill="both", expand=True, padx=(6, 0), pady=4)
        recog_scroll.pack(side="right", fill="y", pady=4)
        # APT 轨迹面板：外框与标题字体保持原生 LabelFrame，仅内部背景与 F 概况区一致。
        apt_frame = ttk.LabelFrame(recog_frame, text="APT 轨迹（来源：最新 APTSOURCE）")
        apt_frame.pack(fill="x", pady=(0, 4))
        self.apt_trace_frame = apt_frame
        apt_body = tk.Frame(apt_frame, bg="#eef2f7")
        apt_body.pack(fill="both", expand=True)
        self.apt_retract_count_var = tk.StringVar(value="-")
        self.apt_xyz_var = tk.StringVar(value="-")
        xyz_row = tk.Frame(apt_body, bg="#eef2f7")
        xyz_row.pack(fill="x", padx=6, pady=(2, 0))
        tk.Label(xyz_row, text="XYZ 行程：", fg="#57606a", bg="#eef2f7").pack(side="left")
        tk.Label(xyz_row, textvariable=self.apt_xyz_var, bg="#eef2f7", anchor="w").pack(side="left")
        height_row = tk.Frame(apt_body, bg="#eef2f7")
        height_row.pack(fill="x", padx=6, pady=(0, 2))
        tk.Label(height_row, text="抬刀高度：", bg="#eef2f7").pack(side="left")
        self.apt_retract_height_var = tk.StringVar(value="")
        self.apt_retract_height_entry = ttk.Entry(height_row, textvariable=self.apt_retract_height_var, width=12)
        self.apt_retract_height_entry.pack(side="left")
        ttk.Button(height_row, text="确认", width=6, command=self._apply_apt_retract_height).pack(side="left", padx=(4, 8))
        self.apt_retract_height_entry.bind("<Return>", self._apply_apt_retract_height)
        self.apt_retract_height_entry.bind(
            "<FocusOut>", lambda event: self._apply_apt_retract_height(event, from_focus_out=True))
        self.apt_retract_auto_var = tk.StringVar(value="自动识别：-")
        tk.Label(height_row, textvariable=self.apt_retract_auto_var, fg="#57606a",
                 bg="#eef2f7").pack(side="left", padx=(0, 10))
        tk.Label(height_row, text="抬刀次数：", bg="#eef2f7").pack(side="left")
        tk.Label(height_row, textvariable=self.apt_retract_count_var, bg="#eef2f7").pack(side="left")
        self.apt_trace_hint_var = tk.StringVar(value="（回车生效并同步报告/全部程序信息）")
        tk.Label(height_row, textvariable=self.apt_trace_hint_var, fg="#57606a",
                 bg="#eef2f7").pack(side="left", padx=(10, 0))
        feed_frame = ttk.LabelFrame(recog_frame, text="F 离群检测")
        feed_frame.pack(fill="x", pady=(2, 0))
        # 概况区：两列网格（字段名右对齐 + 值左对齐），浅色面板让列结构清晰。
        overview = tk.Frame(feed_frame, bg="#eef2f7")
        overview.pack(fill="x", padx=8, pady=(4, 0))
        overview.columnconfigure(0, minsize=72)
        overview.columnconfigure(1, weight=1)
        self.feed_common_var = tk.StringVar(value="抬刀平面 -")
        self.feed_apt_feeds_var = tk.StringVar(value="-")
        self.feed_envelope_var = tk.StringVar(value="-")
        overview_fields = (
            ("分段统计", self.feed_common_var),
            ("APT 参考", self.feed_apt_feeds_var),
            ("检测结论", self.feed_envelope_var),
        )
        for index, (field_text, _variable) in enumerate(overview_fields):
            tk.Label(overview, text=field_text, fg="#57606a", bg="#eef2f7", anchor="e").grid(
                row=index, column=0, sticky="e", padx=(0, 10), pady=3)
        tk.Label(overview, textvariable=self.feed_common_var, bg="#eef2f7", anchor="w").grid(
            row=0, column=1, sticky="w", pady=3)
        tk.Label(overview, textvariable=self.feed_apt_feeds_var, bg="#eef2f7", anchor="w").grid(
            row=1, column=1, sticky="w", pady=3)
        self.feed_envelope_label = tk.Label(overview, textvariable=self.feed_envelope_var,
                                             bg="#eef2f7", anchor="w")
        self.feed_envelope_label.grid(row=2, column=1, sticky="w", pady=3)
        # 证据明细：行/F 值/结论/原因/段/次数/最小差距/参照值/APT 参考/原始行。
        ttk.Label(feed_frame, text="检测证据明细", foreground="#57606a").pack(anchor="w", padx=8, pady=(6, 0))
        self.feed_outlier_table = self._table(
            feed_frame,
            ("line", "value", "status", "reason", "segment", "count", "gap", "reference", "in_apt", "text"),
            ("行", "F 值", "结论", "原因", "段", "次数", "最小差距", "参照值", "APT 参考", "原始行"),
            (45, 60, 70, 125, 40, 45, 70, 150, 80, 260),
            height=3,
        )
        self.feed_outlier_table.pack(fill="x", padx=8, pady=(2, 2))
        self.feed_outlier_table.tag_configure("warning", foreground="#c0392b")
        self.feed_outlier_table.tag_configure("review", foreground="#b9770e")
        self.feed_outlier_table.tag_configure("boundary", foreground="#7d3c98")
        # 单段分布表（无参照、退化为人工判定时展示），含 F 最小值/最大值。
        self.feed_dist_frame = ttk.LabelFrame(feed_frame, text="单段分布表（无参照，人工判定）")
        self.feed_dist_range_var = tk.StringVar(value="F 范围：-")
        ttk.Label(self.feed_dist_frame, textvariable=self.feed_dist_range_var,
                  foreground="#57606a").pack(anchor="w", padx=6, pady=(2, 0))
        self.feed_distribution_table = self._table(
            self.feed_dist_frame,
            ("value", "count", "first_line", "note"),
            ("F 值", "次数", "首次行号", "备注"),
            (70, 60, 80, 320),
            height=3,
        )
        self.feed_distribution_table.pack(fill="x", padx=6, pady=(2, 4))
        # 子控件全部创建后递归绑定滚轮（识别数据页内容区整体可滚动）。
        _bind_recog_wheel(recog_frame)
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
        notebook.add(recog_page, text="识别数据")
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
            self.feed_outlier_table,
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
        # Tk 8.6.9 style.map bug: 应用官方补丁让 tag_configure 颜色恢复生效。
        style = ttk.Style(self.master)
        style.map(
            "Treeview",
            foreground=fixed_treeview_map(style, "foreground"),
            background=fixed_treeview_map(style, "background"),
        )
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
        issue.column("issues", width=self.validation_column_width, minwidth=self.validation_column_width, stretch=False, anchor="center")
        issue.tag_configure("validation-error", foreground="#c62828", font=(self.ui_font_family, 9, "bold"))
        issue.tag_configure("validation-warning", foreground="#b54708", font=(self.ui_font_family, 9, "bold"))
        issue.tag_configure("validation-info", foreground="#1565c0")
        issue.tag_configure("validation-none", foreground="#57606a")
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
        emit_event("info", "settings_saved", "编制/审核设置已保存")
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
            "multiple_spindle": self.multiple_spindle_var.get(),
            "max_file_size": self.max_file_size_var.get(),
            "max_files": self.max_files_var.get(),
            "retract_z_threshold": self.retract_z_threshold_var.get(),
            "storage_backend": self.storage_backend_var.get(),
        }
        win = tk.Toplevel(self.master)
        win.title("程序设置")
        win.transient(self.master)
        win.resizable(False, False)
        notebook = ttk.Notebook(win)
        notebook.pack(fill="both", expand=True, padx=10, pady=(10, 0))
        basic = ttk.Frame(notebook, padding=8)
        rules = ttk.Frame(notebook, padding=8)
        # WP-C1 布局统一：两页列 0 均可拉伸，LabelFrame 占满页面宽度，
        # 保证「基本设置」与「校验规则」页的框宽度一致，切换时不跳动。
        basic.columnconfigure(0, weight=1)
        rules.columnconfigure(0, weight=1)
        notebook.add(basic, text="基本设置")
        notebook.add(rules, text="校验规则")
        self.settings_notebook = notebook
        self.settings_pages = (basic, rules)

        def content_cell(page, row):
            """返回第 row 行的内容容器（col1）：控件 + ? 说明紧邻，行尾放按钮。"""
            cell = ttk.Frame(page)
            cell.grid(row=row, column=1, sticky="ew", pady=3)
            return cell

        # ── 基本设置：文件处理 / 文件类型 / 目录与存储 ──
        file_box = ttk.LabelFrame(basic, text="文件处理", padding=(8, 4))
        file_box.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        file_box.columnconfigure(1, weight=1)
        ttk.Label(file_box, text="文件编码").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=3)
        cell = content_cell(file_box, 0)
        ttk.Combobox(cell, textvariable=self.encoding_var, state="readonly", width=16,
                     values=("auto", "utf-8", "utf-8-sig", "gb18030", "gbk", "gb2312", "cp1252")).pack(side="left")
        self._settings_help_label(cell, "文件编码", "文件编码：auto=自动识别（按 utf-8、gb2312、gbk、gb18030、cp1252 顺序尝试并记录实际编码）；也可显式指定其中一种编码强制解码。识别结果会显示在解析信息页。").pack(side="left", padx=(4, 0))

        ttk.Label(file_box, text="程序名允许字符").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=3)
        cell = content_cell(file_box, 1)
        ttk.Entry(cell, textvariable=self.allowed_name_pattern_var, width=24).pack(side="left")
        self._settings_help_label(cell, "程序名允许字符", "程序名允许字符：用于校验提取到的程序名（正则表达式）。默认允许中文、英文、数字、下划线和连字符；与默认规则不一致时提示手动确认。").pack(side="left", padx=(4, 0))
        ttk.Button(cell, text="恢复默认", command=lambda: self.allowed_name_pattern_var.set(DEFAULT_NAME_PATTERN)).pack(side="right", padx=(12, 0))

        ttk.Label(file_box, text="单文件大小上限（字节）").grid(row=2, column=0, sticky="w", padx=(0, 8), pady=3)
        cell = content_cell(file_box, 2)
        ttk.Entry(cell, textvariable=self.max_file_size_var, width=12).pack(side="left")
        self._settings_help_label(cell, "单文件大小上限", "单文件大小上限：MPF 文件超过该字节数时跳过并报错；留空或 0 表示不限制。用于防止超大文件拖慢扫描。").pack(side="left", padx=(4, 0))

        ttk.Label(file_box, text="扫描文件数量上限").grid(row=3, column=0, sticky="w", padx=(0, 8), pady=3)
        cell = content_cell(file_box, 3)
        ttk.Entry(cell, textvariable=self.max_files_var, width=12).pack(side="left")
        self._settings_help_label(cell, "扫描文件数量上限", "扫描文件数量上限：目录内文件数超过该值时停止扫描并提示；留空或 0 表示不限制。").pack(side="left", padx=(4, 0))

        type_box = ttk.LabelFrame(basic, text="文件类型", padding=(8, 4))
        type_box.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        type_box.columnconfigure(1, weight=1)
        ttk.Label(type_box, text="待删除扩展名").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=3)
        cell = content_cell(type_box, 0)
        ttk.Entry(cell, textvariable=self.delete_extensions_var, width=24).pack(side="left")
        self._settings_help_label(cell, "待删除扩展名", "待删除扩展名：逗号分隔的扩展名列表（如 .log,.moaptindexes），大小写不敏感。扫描到的这些扩展名文件将在执行目录处理时清理；留空表示不清理任何中间文件。").pack(side="left", padx=(4, 0))
        ttk.Button(cell, text="恢复默认", command=lambda: self.delete_extensions_var.set(".log, .moaptindexes")).pack(side="right", padx=(12, 0))

        ttk.Label(type_box, text="主程序扩展名").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=3)
        cell = content_cell(type_box, 1)
        ttk.Entry(cell, textvariable=self.program_extensions_var, width=24).pack(side="left")
        self._settings_help_label(cell, "主程序扩展名", "主程序扩展名：逗号分隔，如 .mpf,.nc,.txt。扫描时按这些扩展名识别数控主程序（MPF），并参与命名规范化与头部处理。").pack(side="left", padx=(4, 0))

        ttk.Label(type_box, text="输出扩展名").grid(row=2, column=0, sticky="w", padx=(0, 8), pady=3)
        cell = content_cell(type_box, 2)
        ttk.Entry(cell, textvariable=self.program_output_extension_var, width=24).pack(side="left")
        self._settings_help_label(cell, "输出扩展名", "输出扩展名：规范化重命名后使用的扩展名，如 .MPF 或 .nc。").pack(side="left", padx=(4, 0))

        store_box = ttk.LabelFrame(basic, text="目录与存储", padding=(8, 4))
        store_box.grid(row=2, column=0, sticky="ew")
        store_box.columnconfigure(1, weight=1)
        ttk.Label(store_box, text="APTSOURCE 归档子目录").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=3)
        cell = content_cell(store_box, 0)
        ttk.Entry(cell, textvariable=self.aptsource_dir_var, width=24).pack(side="left")
        self._settings_help_label(cell, "APTSOURCE 归档子目录", "APTSOURCE 归档子目录：启用「保存 APTSOURCE」后，匹配到的 APT 源文件将归档到该子目录下的时间戳目录（YYYYMMDD_HHMMSS）。").pack(side="left", padx=(4, 0))

        cell = content_cell(store_box, 1)
        ttk.Checkbutton(cell, text="处理前询问备份", variable=self.ask_backup_var).pack(side="left")
        self._settings_help_label(cell, "处理前询问备份", "处理前询问备份：执行目录处理前询问是否先将待处理文件快照到 backup\\时间戳 目录；关闭后执行前不再询问，也不会自动备份。").pack(side="left", padx=(4, 0))

        ttk.Label(store_box, text="配置保存位置").grid(row=2, column=0, sticky="w", padx=(0, 8), pady=3)
        cell = content_cell(store_box, 2)
        ttk.Combobox(cell, textvariable=self.storage_backend_var, state="readonly", width=10,
                     values=("registry", "appdata", "home")).pack(side="left")
        self._settings_help_label(cell, "配置保存位置", "配置保存位置：registry=当前 Windows 用户注册表（默认）；appdata=用户数据目录 %APPDATA%\\NCodeProcess\\settings.json；home=用户主目录 settings.json。切换保存位置后将清空另外两处可能残留的旧配置。").pack(side="left", padx=(4, 0))

        # ── 校验规则：基础检查 / 工艺校验 / F 离群与 S 警告 / 输出格式 ──
        check_box = ttk.LabelFrame(rules, text="基础检查", padding=(8, 4))
        check_box.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Checkbutton(check_box, text="要求程序结束标记（%/M30/M02）", variable=self.require_end_marker_var).pack(side="left")
        ttk.Checkbutton(check_box, text="要求刀具调用包含 M06", variable=self.require_m06_var).pack(side="left", padx=(16, 0))
        ttk.Checkbutton(check_box, text="要求切削前有 S 转速", variable=self.require_spindle_speed_var).pack(side="left", padx=(16, 0))

        rule_box = ttk.LabelFrame(rules, text="工艺校验", padding=(8, 4))
        rule_box.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        rule_box.columnconfigure(1, weight=1)
        ttk.Label(rule_box, text="G00 级别").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=3)
        cell = content_cell(rule_box, 0)
        ttk.Combobox(cell, textvariable=self.g00_level, state="readonly", width=10,
                     values=("error", "warning", "allow")).pack(side="left")
        self._settings_help_label(cell, "G00 级别", "G00/G0 快速定位检查级别：error=作为错误阻止输出；warning=仅提示；allow=不检查。").pack(side="left", padx=(4, 0))

        ttk.Label(rule_box, text="必填 MSG 字段").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=3)
        cell = content_cell(rule_box, 1)
        required_frame = ttk.Frame(cell)
        required_frame.pack(side="left")
        for text, var in (
            ("编制", self.required_bianzhi_var),
            ("审核", self.required_shenhe_var),
            ("图号", self.required_drawing_var),
            ("版次", self.required_part_var),
        ):
            ttk.Checkbutton(required_frame, text=text, variable=var).pack(side="left", padx=8)
        self._settings_help_label(cell, "必填 MSG 字段", "必填 MSG 字段：勾选的字段在程序头部缺失或为空时按错误上报。程序名、机床、控制系统固定必填，不可关闭。").pack(side="left", padx=(4, 0))

        ttk.Label(rule_box, text="M03 补写位置").grid(row=2, column=0, sticky="w", padx=(0, 8), pady=3)
        cell = content_cell(rule_box, 2)
        ttk.Combobox(cell, textvariable=self.m03_position_var, state="readonly", width=14,
                     values=("after-s", "standalone")).pack(side="left")
        self._settings_help_label(cell, "M03 补写位置", "M03 补写位置：after-s=在首个 S 转速所在程序块末尾（分号之前）追加 M03；standalone=在首条切削/运动指令前插入独立 M03 行。").pack(side="left", padx=(4, 0))

        ttk.Label(rule_box, text="F/S 上下限").grid(row=3, column=0, sticky="w", padx=(0, 8), pady=3)
        cell = content_cell(rule_box, 3)
        limit_frame = ttk.Frame(cell)
        limit_frame.pack(side="left")
        ttk.Label(limit_frame, text="F").pack(side="left")
        ttk.Entry(limit_frame, textvariable=self.feed_min_var, width=8).pack(side="left", padx=2)
        ttk.Label(limit_frame, text="~").pack(side="left", padx=4)
        ttk.Entry(limit_frame, textvariable=self.feed_max_var, width=8).pack(side="left", padx=2)
        ttk.Label(limit_frame, text="S").pack(side="left", padx=(10, 0))
        ttk.Entry(limit_frame, textvariable=self.spindle_min_var, width=8).pack(side="left", padx=2)
        ttk.Label(limit_frame, text="~").pack(side="left", padx=4)
        ttk.Entry(limit_frame, textvariable=self.spindle_max_var, width=8).pack(side="left", padx=2)
        self._settings_help_label(cell, "F/S 上下限", "F/S 上下限：F 为进给、S 为主轴转速。默认 F 20~10000、S 500~12000；留空表示不检查对应方向。正文中的 F/S 值低于下限或高于上限时按错误上报（feed-range/spindle-range），用于拦截误输（如 F 多打一位）。").pack(side="left", padx=(4, 0))
        ttk.Button(cell, text="恢复默认", command=self._reset_feed_limits).pack(side="right", padx=(12, 0))

        outlier_box = ttk.LabelFrame(rules, text="F 离群与 S 警告", padding=(8, 4))
        outlier_box.grid(row=2, column=0, sticky="ew", pady=(0, 6))
        outlier_box.columnconfigure(1, weight=1)
        ttk.Label(outlier_box, text="F 离群校验").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=3)
        cell = content_cell(outlier_box, 0)
        ttk.Label(cell, text="抬刀平面分段对比，容差固定 30%（罕见 ≤2 次 + 与其他段差距）").pack(side="left")
        self._settings_help_label(cell, "F 离群校验", "F 离群校验采用抬刀平面分段对比：程序以抬刀平面（最大 Z 簇）切分为多个“来回”段，每段统计运动行的有效 F（含模态继承）。全程序出现 ≤2 次的 F 值若与其他段所有 F 的相对差距都超过 30%，输出提示——差距 >60% 为警告，30%~60% 为复核；所有出现行均为纯 Z 运动（有 Z、无 X/Y）的轴向切入值按规则豁免，不绑定任何具体 F 数值。容差为固定参数，不随刀具尺寸放大。硬边界（F0、负值、上下限）独立校验，APT 档位仅作辅助上下文。").pack(side="left", padx=(4, 0))

        cell = content_cell(outlier_box, 1)
        ttk.Checkbutton(cell, text="多 S 值警告", variable=self.multiple_spindle_var).pack(side="left")
        self._settings_help_label(cell, "多 S 值警告", "多 S 值警告：程序正文包含多个不同 S 转速值时给出警告，提示确认转速切换是否符合工艺要求。").pack(side="left", padx=(4, 0))

        output_box = ttk.LabelFrame(rules, text="输出格式", padding=(8, 4))
        output_box.grid(row=3, column=0, sticky="ew")
        output_box.columnconfigure(1, weight=1)
        ttk.Label(output_box, text="换行策略").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=3)
        cell = content_cell(output_box, 0)
        ttk.Combobox(cell, textvariable=self.newline_var, state="readonly", width=14,
                     values=("auto", "crlf", "lf")).pack(side="left")
        self._settings_help_label(cell, "换行策略", "换行策略：auto=跟随源文件换行风格；crlf=统一使用 CRLF；lf=统一使用 LF。用于老旧控制器对换行风格敏感的目录。").pack(side="left", padx=(4, 0))

        ttk.Label(output_box, text="辅助指令顺序").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=3)
        cell = content_cell(output_box, 1)
        aux_frame = ttk.Frame(cell)
        aux_frame.pack(side="left")
        for text, var in (
            ("M03 先于切削", self.aux_m03_before_motion_var),
            ("M05 先于结束", self.aux_m05_before_end_var),
            ("M08 先于切削", self.aux_m08_before_cut_var),
            ("M09 先于结束", self.aux_m09_before_end_var),
        ):
            ttk.Checkbutton(aux_frame, text=text, variable=var).pack(side="left", padx=8)
        self._settings_help_label(cell, "辅助指令顺序", "辅助指令顺序：勾选的规则在指令顺序异常时提示——M03 先于首次切削（错误）、M05/M09 先于程序结束（警告）、M08 先于首次切削（警告）。").pack(side="left", padx=(4, 0))

        actions = ttk.Frame(win, padding=(10, 0, 10, 10))
        actions.pack(fill="x")
        ttk.Button(actions, text="恢复默认", command=self._restore_default_settings).pack(side="left")
        ttk.Button(actions, text="导出设置…", command=self._export_settings).pack(side="left")
        ttk.Button(actions, text="确定", command=self._confirm_settings).pack(side="right")
        ttk.Button(actions, text="取消", command=self._cancel_settings).pack(side="right", padx=(0, 8))
        win.bind("<Return>", lambda _event: self._confirm_settings())
        win.bind("<Escape>", lambda _event: self._cancel_settings())
        self._show_centered(win)
        self.settings_window = win

    def _settings_help_label(self, parent, title, message):
        """设置对话框的 ? 说明按钮：点击弹出说明，避免长文字挤压排版。"""
        label = ttk.Label(parent, text="?", cursor="question_arrow", foreground="#1565c0",
                          font=("TkDefaultFont", 9, "bold"))
        label.bind("<Button-1>", lambda _event: messagebox.showinfo(title, message, parent=self.master))
        return label

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
        save_all(self._current_settings_values(), self.settings_registry_key, backend=self.storage_backend_var.get())
        emit_event("info", "settings_saved", f"程序设置已保存（保存位置：{self.storage_backend_var.get()}）")

    def _current_settings_values(self) -> dict:
        """收集当前全部设置值（REGISTRY_KEYS → 字符串），供保存与导出复用。"""
        value = self
        return {
            "bianzhi": value.info_vars["bianzhi"].get().strip(),
            "shenhe": value.info_vars["shenhe"].get().strip(),
            "encoding": value.encoding_var.get().strip(),
            "delete_extensions": value.delete_extensions_var.get().strip(),
            "allowed_name_pattern": value.allowed_name_pattern_var.get().strip(),
            "aptsource_dir": value.aptsource_dir_var.get().strip() or "aptsource",
            "program_extensions": value.program_extensions_var.get().strip(),
            "program_output_extension": value._parsed_output_extension(),
            "require_end_marker": "1" if value.require_end_marker_var.get() else "0",
            "require_m06": "1" if value.require_m06_var.get() else "0",
            "require_spindle_speed": "1" if value.require_spindle_speed_var.get() else "0",
            "ask_backup": "1" if value.ask_backup_var.get() else "0",
            "required_bianzhi": "1" if value.required_bianzhi_var.get() else "0",
            "required_shenhe": "1" if value.required_shenhe_var.get() else "0",
            "required_drawing": "1" if value.required_drawing_var.get() else "0",
            "required_part": "1" if value.required_part_var.get() else "0",
            "m03_position": value.m03_position_var.get(),
            "feed_min": value.feed_min_var.get().strip(),
            "feed_max": value.feed_max_var.get().strip(),
            "spindle_min": value.spindle_min_var.get().strip(),
            "spindle_max": value.spindle_max_var.get().strip(),
            "newline": value.newline_var.get(),
            "aux_m03_before_motion": "1" if value.aux_m03_before_motion_var.get() else "0",
            "aux_m05_before_end": "1" if value.aux_m05_before_end_var.get() else "0",
            "aux_m08_before_cut": "1" if value.aux_m08_before_cut_var.get() else "0",
            "aux_m09_before_end": "1" if value.aux_m09_before_end_var.get() else "0",
            "multiple_spindle_warn": "1" if value.multiple_spindle_var.get() else "0",
            "max_file_size": value.max_file_size_var.get().strip(),
            "max_files": value.max_files_var.get().strip(),
            "retract_z_threshold": value.retract_z_threshold_var.get().strip(),
            "storage_backend": value.storage_backend_var.get(),
        }

    def _export_settings(self):
        """选择导出路径，把当前全部设置导出为 JSON 文件。"""
        from tkinter import filedialog
        path = filedialog.asksaveasfilename(
            title="导出程序设置",
            defaultextension=".json",
            filetypes=(("JSON 设置", "*.json"), ("所有文件", "*.*")),
            parent=self.master,
        )
        if not path:
            return
        try:
            Path(path).write_text(json.dumps(self._current_settings_values(), ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as exc:
            messagebox.showerror("导出失败", f"无法写入导出文件：\n{path}\n\n{exc}", parent=self.master)
            return
        messagebox.showinfo("导出完成", f"程序设置已导出到：\n{path}", parent=self.master)

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
        self.required_bianzhi_var.set(defaults["required_bianzhi"] == "1")
        self.required_shenhe_var.set(defaults["required_shenhe"] == "1")
        self.required_drawing_var.set(defaults["required_drawing"] == "1")
        self.required_part_var.set(defaults["required_part"] == "1")
        self.m03_position_var.set(defaults["m03_position"])
        self.feed_min_var.set(defaults["feed_min"])
        self.feed_max_var.set(defaults["feed_max"])
        self.spindle_min_var.set(defaults["spindle_min"])
        self.spindle_max_var.set(defaults["spindle_max"])
        self.newline_var.set(defaults["newline"])
        self.g00_level.set("error")
        self.aux_m03_before_motion_var.set(defaults["aux_m03_before_motion"] == "1")
        self.aux_m05_before_end_var.set(defaults["aux_m05_before_end"] == "1")
        self.aux_m08_before_cut_var.set(defaults["aux_m08_before_cut"] == "1")
        self.aux_m09_before_end_var.set(defaults["aux_m09_before_end"] == "1")
        self.multiple_spindle_var.set(defaults["multiple_spindle_warn"] == "1")
        self.max_file_size_var.set(defaults["max_file_size"])
        self.max_files_var.set(defaults["max_files"])
        self.retract_z_threshold_var.set(defaults["retract_z_threshold"])
        # WP-C8：恢复默认时保存位置回到注册表（默认后端），并清空另外两处残留配置。
        self.storage_backend_var.set("registry")
        # 统一恢复/清除：编制与审核（主窗口表单）一并回到默认（空）
        self.info_vars["bianzhi"].set("")
        self.info_vars["shenhe"].set("")
        self.info_defaults["bianzhi"] = ""
        self.info_defaults["shenhe"] = ""

    def _reset_feed_limits(self):
        """恢复 F/S 上下限默认值（F 20~10000、S 500~12000）。"""
        self.feed_min_var.set("20")
        self.feed_max_var.set("10000")
        self.spindle_min_var.set("500")
        self.spindle_max_var.set("12000")

    def _restore_default_settings(self):
        """恢复全部默认值：保存位置切回注册表，并清空另外两处可能残留的配置（含编制/审核）。"""
        self._apply_settings_defaults()
        save_all(dict(REGISTRY_DEFAULTS), self.settings_registry_key, backend="registry")
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
        self.multiple_spindle_var.set(snapshot.get("multiple_spindle", self.multiple_spindle_var.get()))
        self.max_file_size_var.set(snapshot.get("max_file_size", self.max_file_size_var.get()))
        self.max_files_var.set(snapshot.get("max_files", self.max_files_var.get()))
        self.retract_z_threshold_var.set(snapshot.get("retract_z_threshold", self.retract_z_threshold_var.get()))
        self.storage_backend_var.set(snapshot.get("storage_backend", self.storage_backend_var.get()))
        self.settings_window.destroy()
        self.settings_window = None

    def apply_info(self):
        if self._scan_running:
            messagebox.showinfo("扫描进行中", "扫描进行中，请稍候再应用程序信息。", parent=self.master)
            return
        v = self.info_vars
        if not v["drawing"].get().strip() or not v["version"].get().strip():
            messagebox.showerror("信息不完整", "图号和版次为必填项。未应用设置，也不会修改任何 MPF 文件。", parent=self.master)
            return
        self.applied_info = ProgramInfo(v["bianzhi"].get().strip(), v["shenhe"].get().strip(), v["drawing"].get().strip(), v["version"].get().strip(), "", "SIE840D", v["date"].get().strip())
        self.info_defaults.update({key: v[key].get().strip() for key in self.info_defaults})
        # 主线程捕获配置、程序信息与计划快照，后台线程只做纯逻辑重处理，
        # 避免工作线程读取 Tk 变量；完成后经 _safe_after 回主线程刷新预览。
        preview_config = self.config()
        info = self.info()
        plans = [p for p in (self.scan_result.files if self.scan_result else [])
                 if p.kind == "mpf" and p.program and p.original_text is not None]
        generation = self._scan_generation
        self.status.set("正在应用程序信息并生成预览……")

        def work():
            applied = reprocess_plans(plans, info, preview_config, self.program_tools)
            self._safe_after(0, lambda: self._finish_apply_info(applied, generation))

        threading.Thread(target=work, daemon=True).start()

    def _finish_apply_info(self, applied_plans, generation):
        """后台重处理完成后回到主线程刷新预览（代际防护：旧结果不覆盖新状态）。"""
        if generation != self._scan_generation:
            return
        v = self.info_vars
        for plan_file in applied_plans:
            self.program_header_values[plan_file.program] = {
                "bianzhi": v["bianzhi"].get().strip(),
                "shenhe": v["shenhe"].get().strip(),
                "drawing": v["drawing"].get().strip(),
                "version": v["version"].get().strip(),
                "date": v["date"].get().strip(),
            }
        mode = "覆盖修改" if self.overwrite_fields.get() else "按默认逻辑（保留已有值）"
        self.status.set(f"已生成 {len(applied_plans)} 个程序的预览（{mode}）。确认无误后点击“确认并执行处理”写入文件。")
        # 立即用内存预览刷新表格与右侧信息（含新的头部/刀具）。
        previous_program = None
        if self.keep_table.selection():
            try:
                previous_program = self.scan_result.files[int(self.keep_table.selection()[0])].program
            except (IndexError, TypeError, ValueError):
                previous_program = None
        self.populate_file_tables()
        if previous_program:
            for iid in self.keep_table.get_children():
                try:
                    if self.scan_result.files[int(iid)].program == previous_program:
                        self.keep_table.selection_set(iid)
                        self.keep_table.focus(iid)
                        break
                except (IndexError, TypeError, ValueError):
                    continue
        self.show_selected()
        # WP-P3：全部应用只做内存级重处理并刷新预览，不重新扫描目录——
        # 文件列表、图号候选（文件夹名/APT 头部）与配对关系不会因应用头部信息而改变，
        # 避免触发两阶段重扫带来的“分析中…”闪烁与按钮短暂禁用。

    def _show_overwrite_help(self):
        messagebox.showinfo(
            "覆盖已有非空 MSG 字段",
            "该选项仅允许更新可编辑头部字段：编制（BIANZHI）、审核/校对（SHENHE）、图号（DRAWING NUMBER）与版次（PART VERSION）；"
            "程序名、机床、控制系统与日期等受保护字段保持不变。\n\n"
            "勾选后：点击“应用所选”或“全部应用”按表单新值生成覆盖修改的预览；确认无误后点击“确认并执行处理”将更改写入文件。\n"
            "未勾选时：应用操作仅生成并展示预览，按默认逻辑保留已有非空值、只补全空缺字段；写入同样由“确认并执行处理”完成。\n\n"
            "无论是否勾选，应用操作均不会直接修改文件。",
            parent=self.master,
        )

    def apply_selected(self):
        """Apply the program-info fields only to the selected MPF rows."""
        if self._scan_running:
            messagebox.showinfo("扫描进行中", "扫描进行中，请稍候再应用程序信息。", parent=self.master)
            return
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
            multiple_spindle_warn=self.multiple_spindle_var.get(),
            ask_backup=self.ask_backup_var.get(),
            max_file_size=parse_non_negative_int(self.max_file_size_var.get()),
            max_files=parse_non_negative_int(self.max_files_var.get()),
            retract_z_threshold=parse_positive_default(self.retract_z_threshold_var.get(), 20.0),
        )

    def info(self):
        return ProgramInfo(self.applied_info.bianzhi, self.applied_info.shenhe, self.applied_info.drawing_number, self.applied_info.part_version, "", "SIE840D", self.applied_info.date)

    def scan(self, *, overwrite_fields=None):
        """Two-phase scan: light plan first, deep analysis in the background.

        Phase 1 builds the plan synchronously (file list/actions/duplicates,
        no per-file analysis) so the file tables appear immediately.  Phase 2
        runs analyze_plan_file per MPF on a worker thread and refreshes the
        detail panel/progress as each file completes; the apply/process
        buttons stay disabled until the whole directory is analyzed.
        """
        self._scan_running = True
        self.apply_all_button.configure(state="disabled")
        self.apply_selected_button.configure(state="disabled")
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
        try:
            # Phase 1: lightweight plan.  APTSOURCE actions, duplicate
            # resolution and drawing candidates are ready; per-file analysis
            # is deferred to keep the file list responsive.
            result = build_plan(scan_directory(str(self.workdir), config), info, config,
                                self.program_tools, analyze=False)
        except Exception:
            self._finish_scan_error(generation)
            return
        self.scan_result = result
        self._scan_progress = (0, sum(f.kind == "mpf" for f in result.files))
        self.populate_file_tables()
        self.scan_progress.configure(maximum=max(1, self._scan_progress[1]), value=0)
        self.scan_progress.grid()
        self.status.set(f"已列出 {len(result.files)} 个文件，正在后台分析……")

        def analyze_background():
            context = result.analyze_context
            directory = context["directory"]
            latest_apt = context["latest_apt"]
            auto_tools = context["auto_tools"]
            feed_reference = context["feed_reference"]
            tool_overrides = context["tool_overrides"]
            mpf_items = [item for item in result.files
                         if item.kind == "mpf" and item.program and item.original_text is not None]
            # 优先分析当前选中文件，其余按列表顺序后台补齐。
            selection = self.keep_table.selection()
            if selection:
                try:
                    selected_index = int(selection[0])
                    selected_plan = result.files[selected_index]
                    if selected_plan in mpf_items:
                        mpf_items.remove(selected_plan)
                        mpf_items.insert(0, selected_plan)
                except (IndexError, TypeError, ValueError):
                    pass
            done = 0
            total = len(mpf_items)
            for item in mpf_items:
                try:
                    analyze_plan_file(item, directory, info, config, latest_apt,
                                      auto_tools, feed_reference, tool_overrides,
                                      context["mpf_sources"])
                except Exception:
                    item.issues.append(Issue(item.source, 1, "", "processing", "error",
                                             "后台分析失败"))
                done += 1
                self._safe_after(0, lambda done=done, item=item: self._refresh_analyzed_file(item, done, generation))
            self._safe_after(0, lambda: self.finish_scan(result, generation))

        threading.Thread(target=analyze_background, daemon=True).start()

    def _refresh_analyzed_file(self, item, done, generation):
        """Main-thread refresh after one file finishes background analysis."""
        if generation != self._scan_generation:
            return
        self._scan_progress = (done, self._scan_progress[1] if self._scan_progress else done)
        self.scan_progress.configure(value=done)
        selected = self.selected_plan()
        if selected is item:
            self.show_selected()
        if item.kind == "mpf":
            errors = sum(i.severity == "error" for i in item.issues)
            warnings = sum(i.severity == "warning" for i in item.issues)
            infos = sum(i.severity == "info" for i in item.issues)
            issue_text = f"E{errors} W{warnings} I{infos}"
            tag = ("validation-error" if errors
                   else ("validation-warning" if warnings
                         else ("validation-info" if infos
                               else "validation-none")))
            row_id = None
            for index, plan_file in enumerate(self.scan_result.files):
                if plan_file is item:
                    row_id = str(index)
                    break
            if row_id and self.keep_table.exists(row_id):
                self.keep_table.item(row_id,
                                     values=(item.action, item.program or "待确认",
                                             _display_with_gap(item.source),
                                             _display_with_gap(Path(item.target).name if item.target else "")))
                self.keep_issue_table.item(row_id,
                                           values=(issue_text,), tags=(tag,) if tag else ())
        self.status.set(f"正在后台分析……（{done}/{self._scan_progress[1]}）{item.source}")


    def _finish_scan_error(self, generation):
        """扫描线程异常时恢复界面状态，避免应用按钮永久禁用。"""
        if generation is not None and generation != self._scan_generation:
            return
        self._scan_running = False
        self._scan_progress = (0, 0)
        self.scan_progress.configure(value=0)
        self.scan_progress.grid_remove()
        self.apply_all_button.configure(state="normal")
        self.apply_selected_button.configure(state="normal")
        self.status.set("扫描失败，请重试。")

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
                f.program_name_source = "手动确认"
                f.issues = [i for i in f.issues if i.kind != "program-name"]
                changed = True
        elif len(unresolved) > 1:
            values = self._confirm_program_names(unresolved)
            if values:
                for f in unresolved:
                    name = values.get(f.source, "")
                    if name and re.match(pattern, name):
                        f.program = name
                        f.program_name_source = "手动确认"
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
        self._scan_running = False
        self._scan_progress = (0, 0)
        self.scan_progress.configure(value=0)
        self.scan_progress.grid_remove()
        self.apply_all_button.configure(state="normal")
        self.apply_selected_button.configure(state="normal")

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
        self._keep_order = order
        for idx in order:
            f = self.scan_result.files[idx]
            errors = sum(i.severity == "error" for i in f.issues)
            warnings = sum(i.severity == "warning" for i in f.issues)
            infos = sum(i.severity == "info" for i in f.issues)
            if f.kind == "mpf":
                if f.output_text is None:
                    issue_text = "分析中…"
                    tag = ""
                else:
                    issue_text = f"E{errors} W{warnings} I{infos}"
                    tag = ("validation-error" if errors
                           else ("validation-warning" if warnings
                                 else ("validation-info" if infos
                                       else "validation-none")))
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
        self._show_apt_trace(f)
        self._show_feed_outlier(f)
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

    def _show_feed_outlier(self, f):
        """刷新 F 离群检测证据区：概况 / 证据明细（含段号与参照值）/ 单段分布表。"""
        self.clear_table(self.feed_outlier_table)
        self.clear_table(self.feed_distribution_table)
        self.feed_dist_frame.pack_forget()
        data = getattr(f, "feed_outlier", None) if f.kind == "mpf" else None
        if data is None or data.safe_plane is None:
            self.feed_common_var.set("抬刀平面 -")
            self.feed_apt_feeds_var.set("无检测数据")
            self.feed_envelope_var.set("-")
            return
        segments = data.segments or []
        if len(segments) <= 1:
            if data.reference_count:
                mode = f"｜跨程序参照 {data.reference_count} 个常见档位"
            else:
                mode = "｜单段分布表兜底（人工判定）"
        else:
            mode = "｜段间对比"
        self.feed_common_var.set(
            f"抬刀平面 {data.safe_plane:g}｜{len(segments)} 段｜容差 {data.tolerance:.0%}{mode}")
        if data.apt_feeds:
            feeds = "、".join(f"{v:g}" for v in data.apt_feeds)
            self.feed_apt_feeds_var.set(f"{feeds}（仅辅助上下文，非合法值白名单）")
        else:
            self.feed_apt_feeds_var.set("无（仅按程序自身结构比较）")
        outliers = data.outliers or []
        boundary_errors = data.boundary_errors or []
        distribution = data.distribution or []
        warning = sum(1 for item in outliers if item.get("level") == "warning")
        review = sum(1 for item in outliers if item.get("level") == "review")
        self.feed_envelope_var.set(
            f"警告 {warning}｜复核 {review}｜边界错误 {len(boundary_errors)}")
        if self.feed_envelope_label is not None:
            if warning:
                self.feed_envelope_label.configure(foreground="#c0392b")
            elif review:
                self.feed_envelope_label.configure(foreground="#b9770e")
            else:
                self.feed_envelope_label.configure(foreground="SystemWindowText")
        reason_labels = {
            "segment-gap": "与其他段差距过大",
            "cross-program-gap": "与同目录程序差距过大",
            "boundary-error": "超上下限",
        }
        level_labels = {"warning": "离群告警", "review": "复核提示"}
        rows = []
        for item in outliers:
            tag = item.get("level", "")
            rows.append((item, level_labels.get(tag, tag), tag))
        for item in boundary_errors:
            rows.append((item, "边界错误", "boundary"))
        has_apt = bool(data.apt_feeds)
        for item, status, tag in rows:
            value = item.get("value")
            value_s = f"{value:g}" if isinstance(value, (int, float)) else str(value)
            gap = item.get("gap")
            gap_s = f"{gap:.1%}" if isinstance(gap, (int, float)) else "-"
            reference = item.get("other_segment_feeds") or []
            if reference:
                reference_s = "、".join(f"{v:g}" for v in reference[:6])
                if len(reference) > 6:
                    reference_s += "…"
            else:
                reference_s = "-"
            apt_note = (
                "在 APT 档位内" if item.get("in_apt")
                else ("不在 APT 档位内" if has_apt else "无 APT 参考"))
            self.feed_outlier_table.insert(
                "", "end", tags=(tag,) if tag else (),
                values=(
                    item.get("line", ""),
                    value_s,
                    status,
                    reason_labels.get(item.get("reason", ""), item.get("reason", "")),
                    item.get("segment_index", "-"),
                    item.get("count", "-"),
                    gap_s,
                    reference_s,
                    apt_note,
                    (item.get("text") or "").strip(),
                ),
            )
        if distribution:
            values = [row["value"] for row in distribution]
            if values:
                self.feed_dist_range_var.set(
                    f"F 范围：{min(values):g} ~ {max(values):g}（最小值 / 最大值；单次值需人工确认）")
            for row in distribution:
                self.feed_distribution_table.insert(
                    "", "end",
                    values=(
                        f"{row['value']:g}",
                        row.get("count", 0),
                        row.get("first_line", ""),
                        row.get("note") or "",
                    ),
                )
            self.feed_dist_frame.pack(fill="x", padx=0, pady=(4, 0))

    def _insert_stats_rows(self, program, stats):
        for key in "FSXYZ":
            self.stats_table.insert(
                "", "end",
                values=(program, key, stats.counts.get(key, 0), self._stat_value(stats.minimum.get(key)), self._stat_value(stats.maximum.get(key)), "发现" + str(stats.g00_count) + " 处" if stats.g00_count else "未发现"),
            )

    @staticmethod
    def _trace_fmt(value):
        return f"{value:.3f}"

    def _effective_retract_count(self, f, height):
        """按指定抬刀高度计算抬刀次数（有 APT 源路径时重算；否则回退已挂载值）。"""
        if f.apt_source_path:
            try:
                return recount_retracts(Path(f.apt_source_path), height, f.apt_encoding or "auto")
            except (OSError, ValueError):
                pass
        return f.apt_toolpath.retract_count if f.apt_toolpath else 0

    def _show_apt_trace(self, f):
        """刷新「APT 轨迹」区（含抬刀高度与次数，支持手动修订）。"""
        toolpath = f.apt_toolpath if f.kind == "mpf" else None
        if not toolpath:
            self.apt_trace_frame.configure(text="APT 轨迹（无 APTSOURCE 数据）")
            self.apt_xyz_var.set("-")
            self.apt_retract_height_entry.configure(state="disabled")
            self.apt_retract_height_var.set("")
            self.apt_retract_auto_var.set("自动识别：-")
            self.apt_retract_count_var.set("-")
            self.apt_trace_hint_var.set("无 APTSOURCE 数据")
            return
        source_name = Path(f.apt_source_path).name if f.apt_source_path else f.source
        self.apt_trace_frame.configure(text=f"APT 轨迹（来源：{source_name}）")
        fmt = self._trace_fmt
        self.apt_xyz_var.set(f"X {fmt(toolpath.min_x)} ~ {fmt(toolpath.max_x)}　"
                             f"Y {fmt(toolpath.min_y)} ~ {fmt(toolpath.max_y)}　"
                             f"Z {fmt(toolpath.min_z)} ~ {fmt(toolpath.max_z)}")
        program = f.program or ""
        override = self.apt_retract_heights.get(program)
        auto_plane = toolpath.retract_plane
        self.apt_retract_auto_var.set(f"自动识别：{fmt(auto_plane) if auto_plane is not None else '-'}")
        self.apt_retract_height_entry.configure(state="normal")
        if override is not None:
            self.apt_retract_height_var.set(f"{override:.3f}")
            count = self._effective_retract_count(f, override)
        else:
            self.apt_retract_height_var.set(f"{fmt(auto_plane) if auto_plane is not None else ''}")
            count = toolpath.retract_count
        # 记录本次自动填充/恢复的值：此后失焦未改动时不再当作人工输入提交。
        self._apt_retract_baseline = self.apt_retract_height_var.get()
        self.apt_retract_count_var.set(str(count))
        self.apt_trace_hint_var.set("")

    def _apply_apt_retract_height(self, _event=None, *, from_focus_out=False):
        """提交抬刀高度修订：合法则按程序记忆并重算次数；非法回退自动值。"""
        f = self.selected_plan()
        if not f or not f.apt_toolpath:
            return
        program = f.program or ""
        raw = self.apt_retract_height_var.get().strip()
        if from_focus_out and raw == self._apt_retract_baseline:
            # 用户没有改动输入框（自动填充值原样失焦，例如切换页签/选择）：
            # 不视为人工提交，避免误报“已设置抬刀高度”。
            return
        try:
            height = float(raw)
            if height <= 0:
                raise ValueError
        except ValueError:
            self.apt_retract_heights.pop(program, None)
            self._show_apt_trace(f)
            return
        self.apt_retract_heights[program] = height
        count = self._effective_retract_count(f, height)
        self.apt_retract_count_var.set(str(count))
        if self.all_stats_window is not None and self.all_stats_window.winfo_exists():
            self.all_stats_window.destroy()
            self.all_stats_window = None
            self.show_all_program_stats()
        self.status.set(f"已设置 {program} 抬刀高度 {height:.3f}，抬刀次数 {count}。")

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
            ("program", "f_count", "f_min", "f_max", "s_count", "s_min", "s_max", "x_count", "x_min", "x_max", "y_count", "y_min", "y_max", "z_count", "z_min", "z_max", "g00", "goto", "arc", "retract"),
            ("程序", "F 次数", "F 最小", "F 最大", "S 次数", "S 最小", "S 最大", "X 次数", "X 最小", "X 最大", "Y 次数", "Y 最小", "Y 最大", "Z 次数", "Z 最小", "Z 最大", "G00 检查", "GOTO 点数", "圆弧数", "抬刀次数"),
            (170, 60, 80, 80, 60, 80, 80, 60, 80, 80, 60, 80, 80, 60, 80, 80, 110, 85, 70, 80),
        )
        table.pack(fill="both", expand=True, padx=8, pady=8)
        self._bind_cell_tooltip(table)
        rows = sorted((f for f in self.scan_result.files if f.kind == "mpf" and f.program), key=lambda item: item.program or "")
        for f in rows:
            if f.stats is None and f.output_text is not None:
                f.stats = calculate_stats(f.output_text)
            merged = {}
            if f.stats:
                s = f.stats
                for key in "FSXYZ":
                    merged[key] = (s.counts.get(key, 0), self._stat_value(s.minimum.get(key)), self._stat_value(s.maximum.get(key)))
            toolpath = f.apt_toolpath
            if toolpath:
                override = self.apt_retract_heights.get(f.program or "")
                retract_count = self._effective_retract_count(f, override) if override is not None else toolpath.retract_count
                goto_text, arc_text = str(toolpath.goto_count), str(toolpath.arc_count)
            else:
                goto_text = arc_text = "-"
                retract_count = "-"
            g00_text = "发现 " + str(f.stats.g00_count) + " 处" if f.stats and f.stats.g00_count else "未发现"
            cells = []
            for key in "FSXYZ":
                count, minimum, maximum = merged.get(key, ("-", "-", "-"))
                cells.extend((count, minimum, maximum))
            table.insert("", "end", values=(
                f.program or "",
                cells[0], cells[1], cells[2], cells[3], cells[4], cells[5],
                cells[6], cells[7], cells[8], cells[9], cells[10], cells[11],
                cells[12], cells[13], cells[14],
                g00_text, goto_text, arc_text, retract_count,
            ))

        def close_window():
            if window.winfo_exists():
                window.destroy()
            self.all_stats_window = None

        screen_width = window.winfo_screenwidth()
        screen_height = window.winfo_screenheight()
        width = min(1700, max(1300, screen_width - 80))
        height = min(720, max(560, screen_height - 100))
        self._show_centered(window, width, height, min_width=1300, min_height=420)
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
        f.program_name_source = "手动确认"
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
            # 尾部补换行，使行号栏总行数与内容栏一致（内容栏每行都带 \n，
            # 含末尾空行），避免分数滚动时行号与内容错位一行。
            gutter.insert("1.0", "\n".join(numbers) + "\n")
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
            if not text_changed_ignoring_line_endings(f.original_text, new_text):
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
        tool_skipped = [f for f in self.scan_result.files if f.kind == "mpf" and f.auto_tool_change_skipped]
        if tool_skipped:
            if detail_lines:
                detail_lines.append("")
            detail_lines.append("【自动添加换刀指令已跳过】")
            for plan_file in tool_skipped:
                detail_lines.append(f"[{plan_file.source}] {plan_file.auto_tool_change_skipped}")
        if not self.confirm_processing(summary, detail_lines):
            return
        backup = self._backup_requested() if self.config().ask_backup else False
        # 在主线程捕获配置快照，避免工作线程读取 Tk 变量（非主线程访问 Tk 不安全）。
        cfg = self.config()
        confirmations = []
        if backup:
            confirmations.append("已确认：处理前备份到 backup 时间戳目录")
        if self.save_aptsource.get():
            confirmations.append("已确认：保存并归档 APTSOURCE")
        if self.overwrite_fields.get():
            confirmations.append("已确认：覆盖已有非空 MSG 字段")
        confirmations.append("已确认：执行目录处理（含清理、归档与重复文件处理）")
        # WP-A2：手动抬刀高度在写入前应用到计划（报告 toolpath_stats 使用修订值）。
        for plan_file in self.scan_result.files:
            if plan_file.kind == "mpf" and plan_file.program in self.apt_retract_heights and plan_file.apt_toolpath:
                height = self.apt_retract_heights[plan_file.program]
                plan_file.apt_toolpath.retract_plane = height
                plan_file.apt_toolpath.retract_count = self._effective_retract_count(plan_file, height)
        self.process_button.configure(state="disabled")
        self.status.set("正在处理当前目录……")
        self._processing = True
        self._safe_after(100, self._poll_process_progress)
        def work():
            def report(done, total, name):
                with self._process_progress_lock:
                    self._process_progress = (done, total, name)
            result = process_plan(self.scan_result, str(self.workdir), cfg, confirm_cleanup=True, progress_callback=report, backup=backup, generator="gui", confirmations=confirmations)
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
        icon_path = base / "assets" / "NCodeProcess_icon.ico"
        if icon_path.exists():
            root.iconbitmap(str(icon_path))
    except (tk.TclError, OSError):
        pass


def main():
    anchor = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve()
    if not acquire_single_instance(str(anchor)):
        # 同一目录已有实例在运行：提示后退出，避免双 EXE 竞态写
        # special_tools.json 与报告时间戳。
        if sys.platform == "win32":
            ctypes.windll.user32.MessageBoxW(None, "NCodeProcess 已在本目录运行，请勿重复启动。", "NCodeProcess", 0x40)
        emit_event("warning", "startup", "检测到程序已在本目录运行，本次启动被拒绝")
        return
    try:
        # WP-R3：启动不自动创建 NCodeProcessData/logs；磁盘日志仅在导出报告时落盘。
        emit_event("info", "startup", f"程序启动（版本 {__version__}）")
        root = tk.Tk()
        _set_window_icon(root)
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
        emit_event("info", "shutdown", "程序退出")
    finally:
        release_single_instance()


if __name__ == "__main__":
    main()
