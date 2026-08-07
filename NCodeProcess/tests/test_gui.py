import json
import os
import sys
import tempfile
import threading
import time
import tkinter as tk
from tkinter import ttk
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import ncodeprocess.gui as gui
from ncodeprocess.core import FIELD_ORDER, FeedOutlierData, FilePlan, ProcessReport, ProgramInfo, ScanResult, ToolpathStats, calculate_stats, emit_event, reset_runtime_log, runtime_log
from ncodeprocess.gui import (
    App,
    centered_position,
    compact_diff_rows,
    merge_drawing_choices,
    needs_detailed_confirmation,
)
from ncodeprocess.preferences import clear_all, load_all, save_all

# 独立的注册表测试键，避免污染真实的 HKCU\Software\NCodeProcess。
TEST_SETTINGS_KEY = r"Software\NCodeProcess_UnitTests_Gui"


def _sync_thread(thread_class):
    """把 threading.Thread 替换为同步执行 target 的桩（GUI 线程测试专用）。"""
    class SynchronousThread(thread_class):
        def start(self):
            self._target(*self._args, **self._kwargs)
    return SynchronousThread


class LayoutWidgetMixin:
    """布局/交互/生命周期测试共用的窗口构造、遍历与等待 helper（不含 test_ 用例）。"""

    def _build_app(self, width, height):
        root = tk.Tk()
        root.withdraw()
        with patch.object(App, "scan", lambda _self: None):
            # 使用隔离的测试注册表键，避免本机真实 HKCU\Software\NCodeProcess
            # 中的用户设置（如 require_m06/require_spindle_speed）污染默认值断言。
            app = App(root, settings_registry_key=TEST_SETTINGS_KEY)
        root.geometry(f"{width}x{height}")
        root.deiconify()
        root.update_idletasks()
        root.update()
        root.update_idletasks()
        return root, app

    @staticmethod
    def _descendants(widget):
        for child in widget.winfo_children():
            yield child
            yield from LayoutWidgetMixin._descendants(child)

    @staticmethod
    def _collect_buttons(widget):
        buttons = []
        for child in widget.winfo_children():
            if child.winfo_class() == "TButton":
                buttons.append(child)
            buttons.extend(LayoutWidgetMixin._collect_buttons(child))
        return buttons

    @staticmethod
    def _relative_x_to_root(widget, root):
        x = 0
        current = widget
        while current is not root:
            x += current.winfo_x()
            current = current.master
        return x

    @staticmethod
    def _column_total(table, columns):
        return sum(int(table.column(column, "width")) for column in columns)

    @staticmethod
    def _pump_until(root, predicate, message, timeout_ms=2000):
        """轮询 root.update() 直到条件成立，消除合成事件时序 flake。"""
        deadline = time.time() + timeout_ms / 1000.0
        while time.time() < deadline:
            root.update()
            if predicate():
                return True
        return False


class DiffViewTests(unittest.TestCase):
    def test_text_changed_ignoring_line_endings(self):
        # WP-B1：仅 CRLF/LF 差异不算内容变化，防止编辑器保存触发误重处理。
        from ncodeprocess.gui import text_changed_ignoring_line_endings as changed
        self.assertFalse(changed("N10 X10;\r\nN20 Y20;\r\n", "N10 X10;\nN20 Y20;\n"))
        self.assertFalse(changed("N10 X10;\r\n", "N10 X10;\r\n"))
        self.assertTrue(changed("N10 X10;\r\n", "N10 X11;\r\n"))

    def test_compact_diff_keeps_three_context_lines_and_counts_hidden(self):
        before = "\n".join(f"L{i}" for i in range(1, 16))
        after_lines = before.splitlines()
        after_lines[7] = "CHANGED"
        rows, hidden = compact_diff_rows(before, "\n".join(after_lines), context=3)
        self.assertEqual([row[0] for row in rows], list(range(5, 12)))
        self.assertEqual(hidden, 8)
        self.assertEqual(rows[3][1], "L8")
        self.assertEqual(rows[3][4], "CHANGED")

    def test_compact_diff_counts_all_lines_when_no_changes(self):
        rows, hidden = compact_diff_rows("A\nB\nC", "A\nB\nC", context=3)
        self.assertEqual(rows, [])
        self.assertEqual(hidden, 3)

    def test_drawing_choices_keep_equal_mpf_and_apt_values_as_two_source_groups(self):
        choices = merge_drawing_choices(
            [("当前目录：Project", "Project")],
            [("MPF提取", "D001"), ("APT提取", "D001")],
        )
        matching = [(label, value) for label, value in choices if value == "D001"]
        self.assertEqual(len(matching), 2)
        self.assertIn(("MPF提取：D001", "D001"), matching)
        self.assertIn(("APT提取：D001", "D001"), matching)

    def test_drawing_choices_keep_different_values_as_separate_sources(self):
        choices = merge_drawing_choices(
            [],
            [("MPF提取", "D001"), ("APT提取", "D002")],
        )
        self.assertEqual(len(choices), 2)
        self.assertEqual({value for _label, value in choices}, {"D001", "D002"})

    def test_drawing_choices_merge_same_method_same_value(self):
        choices = merge_drawing_choices(
            [],
            [("MPF提取", "D001"), ("MPF提取", "D001")],
        )
        self.assertEqual(len(choices), 1)
        self.assertEqual(choices[0], ("MPF提取：D001", "D001"))


class ProcessingConfirmationTests(unittest.TestCase):
    def test_short_details_use_native_confirmation(self):
        self.assertFalse(needs_detailed_confirmation(["delete A.LOG", "delete B.LOG"]))

    def test_more_than_ten_detail_lines_use_detailed_window(self):
        self.assertTrue(needs_detailed_confirmation([f"detail {index}" for index in range(11)]))

    def test_long_detail_text_uses_detailed_window(self):
        self.assertTrue(needs_detailed_confirmation(["A" * 1201]))


class LayoutMetricTests(unittest.TestCase):
    def _require_layout_interface(self, *names):
        missing = [name for name in names if not hasattr(gui, name)]
        self.assertFalse(missing, f"missing layout interface(s): {', '.join(missing)}")

    def test_supported_screen_geometry(self):
        self._require_layout_interface("window_geometry_for_screen")
        self.assertEqual(gui.window_geometry_for_screen(1366, 768), (1286, 668, 1180, 650))
        self.assertEqual(gui.window_geometry_for_screen(1920, 1080), (1290, 720, 1180, 650))

    def test_smaller_screen_does_not_request_more_than_screen(self):
        self._require_layout_interface("window_geometry_for_screen")
        width, height, min_width, min_height = gui.window_geometry_for_screen(1024, 600)
        self.assertLessEqual(width, 1024)
        self.assertLessEqual(height, 600)
        self.assertEqual(min_width, width)
        self.assertEqual(min_height, height)

    def test_centered_position_places_window_at_parent_center(self):
        x, y = centered_position(100, 80, 1000, 600, 800, 600, 1366, 768)
        self.assertEqual((x, y), (200, 80))

    def test_centered_position_clamps_inside_screen(self):
        # 父窗口超出屏幕右缘/下缘时，子窗口被限制在屏幕内
        x, y = centered_position(1200, 700, 1000, 600, 800, 600, 1366, 768)
        self.assertLessEqual(x + 800, 1366)
        self.assertLessEqual(y + 600, 768)
        # 父窗口在屏幕左侧之外时，子窗口贴到屏幕左缘
        x, y = centered_position(-500, -300, 400, 300, 800, 600, 1366, 768)
        self.assertEqual((x, y), (0, 0))

    def test_fit_column_widths_cases(self):
        # 各宽度档位下按「多余宽度只给伸缩列、收缩先缩伸缩列、不低于最小宽度」规则分配。
        self._require_layout_interface("fit_column_widths")
        specs = (("fixed", 100, 80, False), ("stretch", 220, 120, True))
        cases = (
            (400, {"fixed": 100, "stretch": 300}),  # 多余宽度只分配给伸缩列
            (300, {"fixed": 100, "stretch": 200}),  # 收缩时优先压缩伸缩列
            (210, {"fixed": 90, "stretch": 120}),   # 伸缩列到最小后压缩固定列
            (200, {"fixed": 80, "stretch": 120}),   # 恰好最小总宽
            (180, {"fixed": 80, "stretch": 120}),   # 低于最小总宽仍取最小值
            (0, {"fixed": 100, "stretch": 220}),    # 非正空间保留初始宽度
            (-1, {"fixed": 100, "stretch": 220}),
        )
        for available, expected in cases:
            with self.subTest(available=available):
                self.assertEqual(gui.fit_column_widths(available, specs), expected)

    def test_ensure_heading_widths_expands_initials_and_minimums_without_changing_flags(self):
        self._require_layout_interface("ensure_heading_widths")
        specs = (("fixed", 50, 40, False), ("stretch", 60, 45, True))
        headings = ("短", "宽标题")
        widths = {"短": 35, "宽标题": 72}
        result = gui.ensure_heading_widths(specs, headings, lambda text: widths[text], padding=8)
        self.assertEqual(result, (("fixed", 50, 43, False), ("stretch", 80, 80, True)))

    def test_table_profiles_fit_minimum_layout(self):
        self._require_layout_interface(
            "window_geometry_for_screen", "KEEP_COLUMN_SPECS", "TOOL_COLUMN_SPECS"
        )
        target_window_width = gui.window_geometry_for_screen(1366, 768)[0]
        root_frame_padding = 16  # 8px on each side of the App root frame.
        center_gap = 6  # The planned left/right grid uses 3px padding on each side.
        available_main_width = target_window_width - root_frame_padding - center_gap
        program_area_budget = available_main_width // 2
        tool_area_budget = available_main_width - program_area_budget

        self.assertLessEqual(sum(item[1] for item in gui.KEEP_COLUMN_SPECS) + 82, program_area_budget)
        self.assertLessEqual(sum(item[1] for item in gui.TOOL_COLUMN_SPECS) + 230, tool_area_budget)


class FontAwareLayoutMetricTests(unittest.TestCase):
    def _require_font_layout_interface(self, *names):
        missing = [name for name in names if not hasattr(gui, name)]
        self.assertFalse(missing, f"missing font-layout interface(s): {', '.join(missing)}")

    def _require_profile_attributes(self, profile):
        missing = [name for name in ("keep_specs", "tool_specs", "validation_width") if not hasattr(profile, name)]
        self.assertFalse(missing, f"font layout profile missing attribute(s): {', '.join(missing)}")

    def test_choose_ui_font_family_priority_and_fallback(self):
        self._require_font_layout_interface("choose_ui_font_family")
        candidates = ("Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", "Tahoma")
        for family in candidates:
            with self.subTest(single=family):
                self.assertEqual(gui.choose_ui_font_family({family}), family)
        cases = (
            ({"Tahoma", "Microsoft YaHei"}, "Microsoft YaHei"),
            ({"Microsoft YaHei", "Segoe UI"}, "Microsoft YaHei"),
            ({"Segoe UI", "Tahoma"}, "Segoe UI"),
            ({"Microsoft YaHei UI", "Microsoft YaHei"}, "Microsoft YaHei UI"),
            (set(candidates), "Microsoft YaHei UI"),
        )
        for available, expected in cases:
            with self.subTest(available=available):
                self.assertEqual(gui.choose_ui_font_family(available), expected)
        self.assertEqual(gui.choose_ui_font_family({"Arial"}), "TkDefaultFont")

    def test_font_layout_profile_expands_win7_style_metrics_and_preserves_column_flags(self):
        self._require_font_layout_interface(
            "font_layout_profile", "KEEP_COLUMN_SPECS", "TOOL_COLUMN_SPECS"
        )
        widths = {
            "程序名": 45,
            "999 错 / 999 警": 105,
            "刀具号": 48,
        }
        profile = gui.font_layout_profile(lambda text: widths.get(text, 40))
        self._require_profile_attributes(profile)

        keep = {spec[0]: spec for spec in profile.keep_specs}
        tool = {spec[0]: spec for spec in profile.tool_specs}
        base_keep = {spec[0]: spec for spec in gui.KEEP_COLUMN_SPECS}
        base_tool = {spec[0]: spec for spec in gui.TOOL_COLUMN_SPECS}

        self.assertGreater(keep["program"][1], base_keep["program"][1])
        self.assertGreater(keep["program"][2], base_keep["program"][2])
        self.assertGreaterEqual(profile.validation_width, 125)
        self.assertGreaterEqual(tool["number"][1], 68)
        self.assertGreaterEqual(tool["number"][2], 68)
        self.assertEqual(
            [(spec[0], spec[3]) for spec in profile.keep_specs],
            [(spec[0], spec[3]) for spec in gui.KEEP_COLUMN_SPECS],
        )
        self.assertEqual(
            [(spec[0], spec[3]) for spec in profile.tool_specs],
            [(spec[0], spec[3]) for spec in gui.TOOL_COLUMN_SPECS],
        )

    def test_font_layout_profile_keeps_baseline_widths_and_caps_extreme_scaling(self):
        self._require_font_layout_interface(
            "font_layout_profile", "KEEP_COLUMN_SPECS", "TOOL_COLUMN_SPECS"
        )
        baseline_measure = lambda text: 36 if text == "程序名" else 40
        baseline = gui.font_layout_profile(baseline_measure)
        extreme = gui.font_layout_profile(lambda text: 360 if text == "程序名" else 40)
        self._require_profile_attributes(baseline)
        self._require_profile_attributes(extreme)

        self.assertEqual(list(baseline.keep_specs), list(gui.KEEP_COLUMN_SPECS))
        baseline_tool = {spec[0]: spec for spec in baseline.tool_specs}
        base_tool = {spec[0]: spec for spec in gui.TOOL_COLUMN_SPECS}
        for actual, original in zip(baseline.tool_specs, gui.TOOL_COLUMN_SPECS):
            if actual[0] == "number":
                expected_width = max(original[1], baseline_measure("刀具号") + 20)
                self.assertEqual(actual[0], original[0])
                self.assertEqual(actual[1], expected_width)
                self.assertEqual(actual[2], expected_width)
                self.assertEqual(actual[3], original[3])
            else:
                self.assertEqual(list(actual), list(original))

        self.assertEqual(
            baseline.validation_width,
            max(82, baseline_measure("999 错 / 999 警") + 20),
        )
        self.assertEqual(baseline_tool["number"][1], max(base_tool["number"][1], 60))
        self.assertEqual(baseline_tool["number"][2], max(base_tool["number"][1], 60))

        undersized = gui.font_layout_profile(lambda text: 18 if text == "程序名" else 40)
        self._require_profile_attributes(undersized)
        self.assertEqual(list(undersized.keep_specs), list(gui.KEEP_COLUMN_SPECS))
        undersized_tool = {spec[0]: spec for spec in undersized.tool_specs}
        self.assertEqual(
            undersized_tool["number"][1:3],
            (max(base_tool["number"][1], 60), max(base_tool["number"][1], 60)),
        )

        extreme_keep = {spec[0]: spec for spec in extreme.keep_specs}
        base_keep = {spec[0]: spec for spec in gui.KEEP_COLUMN_SPECS}
        self.assertEqual(extreme_keep["program"][1], round(base_keep["program"][1] * 1.5))
        self.assertEqual(extreme_keep["program"][2], round(base_keep["program"][2] * 1.5))
        for actual, original in zip(extreme.keep_specs, gui.KEEP_COLUMN_SPECS):
            self.assertLessEqual(actual[1], round(original[1] * 1.5))
            self.assertLessEqual(actual[2], round(original[2] * 1.5))
        for actual, original in zip(extreme.tool_specs, gui.TOOL_COLUMN_SPECS):
            self.assertLessEqual(actual[1], round(original[1] * 1.5))
            self.assertLessEqual(actual[2], round(original[2] * 1.5))
        self.assertLessEqual(extreme.validation_width, round(82 * 1.5))


class LayoutWidgetTests(unittest.TestCase, LayoutWidgetMixin):
    def test_keep_table_uses_compact_profile_without_default_horizontal_overflow(self):
        root, app = self._build_app(1286, 668)
        try:
            columns = ("action", "program", "source", "target")
            main = app.keep_table
            issue = app.keep_issue_table
            total = self._column_total(main, columns)
            # Treeview borders vary slightly between Tk themes; four pixels
            # covers the border without masking a horizontal overflow.
            self.assertLessEqual(total, main.winfo_width() + 4)
            self.assertLessEqual(
                total + issue.winfo_width(),
                main._keep_frame.winfo_width() - main._keep_ybar.winfo_width() + 4,
            )
            self.assertEqual(int(issue.column("issues", "width")), app.validation_column_width)
            self.assertEqual(int(issue.column("issues", "minwidth")), app.validation_column_width)
            self.assertFalse(bool(issue.column("issues", "stretch")))
            for name, initial, minimum, stretch in app.keep_column_specs:
                config = main.column(name)
                self.assertGreaterEqual(int(config["width"]), minimum)
                self.assertEqual(bool(config["stretch"]), stretch)
                if not stretch:
                    self.assertEqual(int(config["width"]), initial)
        finally:
            root.destroy()

    def test_tool_table_keeps_editor_visible_and_fits_default_width(self):
        root, app = self._build_app(1286, 668)
        try:
            columns = ("number", "dia", "coner", "angle", "type")
            table = app.tool_table
            editor = app.tool_editor
            total = self._column_total(table, columns)
            self.assertGreaterEqual(editor.winfo_width(), 230)
            self.assertLessEqual(total, table.winfo_width() + 4)
            self.assertLessEqual(total + editor.winfo_width(), app.tool_frame.winfo_width() + 4)
            for button in (
                app.upsert_tool_button,
                app.delete_tool_button,
                app.clear_tool_editor_button,
            ):
                self.assertLessEqual(button.winfo_reqwidth(), button.winfo_width())
            for name, initial, minimum, stretch in app.tool_column_specs:
                config = table.column(name)
                self.assertGreaterEqual(int(config["width"]), minimum)
                self.assertEqual(bool(config["stretch"]), stretch)
                if not stretch:
                    self.assertEqual(int(config["width"]), initial)
        finally:
            root.destroy()

    def test_default_window_shows_all_table_headers_without_horizontal_scroll(self):
        # 1920x1080 下默认窗口约 1290x720，所有表头应直接可见、无需拖动。
        root, app = self._build_app(1290, 720)
        try:
            def assert_full_view(table, label):
                self.assertGreaterEqual(
                    float(table.xview()[1]),
                    0.999,
                    f"{label} 在默认窗口宽度下发生横向溢出",
                )

            left_pages = (
                (0, app.keep_table, "保留/归档表"),
                (0, app.keep_issue_table, "校验列"),
                (1, app.apt_table, "APTSOURCE 表"),
                (2, app.delete_table, "待删除表"),
            )
            for index, table, label in left_pages:
                app.left_notebook.select(index)
                root.update_idletasks()
                assert_full_view(table, label)
            right_pages = (
                (0, app.info_table, "解析信息表"),
                (1, app.issue_table, "校验问题表"),
                (2, app.stats_table, "参数统计表"),
            )
            for index, table, label in right_pages:
                app.detail_notebook.select(index)
                root.update_idletasks()
                assert_full_view(table, label)
            assert_full_view(app.tool_table, "刀具表")
        finally:
            root.destroy()

    def test_manual_code_editor_save_reprocesses_and_refreshes(self):
        # Double-clicking an MPF opens an editable copy of its code; saving
        # must re-run the preview pipeline and refresh the validation column.
        root, app = self._build_app(1286, 668)
        try:
            plan = FilePlan("x_P.MPF", "mpf", "P", "P.MPF", "keep")
            plan.original_text = '%\nMSG("PROGRAM:P")\n(ONLY COMMENT)\n%\n'
            app.scan_result = ScanResult("root", [plan])
            app.populate_file_tables()
            app.keep_table.selection_set("0")
            app.keep_table.focus("0")
            app.show_selected()

            self.assertTrue(app.keep_table.bind("<Button-3>"))
            app.edit_program_code()
            self.assertTrue(app.program_editor_window is not None and app.program_editor_window.winfo_exists())
            self.assertEqual(app.program_editor_text.get("1.0", "end-1c"), plan.original_text)

            # Simulate the operator having applied the required header fields.
            app.applied_info = ProgramInfo("A", "B", "D", "V", "M", "C", "DATE")
            app.program_editor_text.delete("1.0", "end")
            app.program_editor_text.insert("1.0", '%\nMSG("PROGRAM:P")\nN1G1X10\nN2M30\n%\n')
            app.program_editor_save_command()

            self.assertIn("M03", plan.output_text)
            self.assertFalse(any(i.kind == "spindle-start" for i in plan.issues))
            self.assertEqual(app.keep_issue_table.item("0", "values")[0], "0 错 / 0 警")
            # The row stays selected after the table rebuild and the detail
            # views are refreshed for the re-reviewed program.
            self.assertEqual(app.keep_table.selection(), ("0",))
            self.assertTrue(app.info_table.get_children())
        finally:
            root.destroy()

    def test_manual_code_editor_shows_and_updates_line_numbers(self):
        # The editor shows a read-only line-number gutter aligned with the
        # code, and the gutter follows edits to the program text.
        root, app = self._build_app(1286, 668)
        try:
            plan = FilePlan("x_P.MPF", "mpf", "P", "P.MPF", "keep")
            plan.original_text = "L1\nL2\nL3"
            app.scan_result = ScanResult("root", [plan])
            app.populate_file_tables()
            app.keep_table.selection_set("0")
            app.edit_program_code()
            self.assertIsNotNone(app.program_editor_gutter)
            self.assertEqual(app.program_editor_gutter.get("1.0", "end-1c"), "1\n2\n3")
            app.program_editor_text.insert("end", "\nL4")
            root.update()
            self.assertEqual(app.program_editor_gutter.get("1.0", "end-1c"), "1\n2\n3\n4")
        finally:
            root.destroy()

    def test_compare_menu_requires_exactly_two_selected_programs(self):
        # The context-menu compare option is enabled only when exactly two
        # MPF rows are selected, and the comparison window then shows both
        # programs side by side.
        root, app = self._build_app(1286, 668)
        try:
            plans = [
                FilePlan("a_P.MPF", "mpf", "P", "P.MPF", "keep"),
                FilePlan("b_Q.MPF", "mpf", "Q", "Q.MPF", "keep"),
            ]
            plans[0].original_text = "L1\nL2\nL3"
            plans[1].original_text = "L1\nL2X\nL3"
            app.scan_result = ScanResult("root", plans)
            app.populate_file_tables()

            # Exactly one selected: compare must stay disabled.
            app.keep_table.selection_set("0")
            app._refresh_keep_menu_states()
            self.assertEqual(app.keep_table_menu.entrycget(2, "state"), "disabled")
            app.compare_selected_programs()
            self.assertIsNone(app.program_compare_window)

            # Exactly two selected: compare enabled and opens the window.
            app.keep_table.selection_set(("0", "1"))
            app._refresh_keep_menu_states()
            self.assertEqual(app.keep_table_menu.entrycget(2, "state"), "normal")
            app.compare_selected_programs()
            self.assertTrue(app.program_compare_window is not None and app.program_compare_window.winfo_exists())
            title = app.program_compare_window.title()
            # 标题与两侧面板同时展示文件名（优先）和程序名。
            self.assertIn("a_P.MPF", title)
            self.assertIn("b_Q.MPF", title)
            self.assertIn("（P）", title)
            self.assertIn("（Q）", title)
            self.assertIn("a_P.MPF（P）", app.program_compare_left.master.master.cget("text"))
            self.assertIn("b_Q.MPF（Q）", app.program_compare_right.master.master.cget("text"))
            self.assertIn("L2", app.program_compare_left.get("1.0", "end"))
            self.assertIn("L2X", app.program_compare_right.get("1.0", "end"))
            self.assertTrue(app.program_compare_left.tag_ranges("changed"))
            # Different lines are red, equal lines are green, both sides show
            # line numbers.
            self.assertEqual(app.program_compare_left.tag_cget("changed", "background"), "#ffd6d6")
            self.assertEqual(app.program_compare_left.tag_cget("added", "background"), "#ffd6d6")
            self.assertEqual(app.program_compare_left.tag_cget("equal", "background"), "#d9f2d9")
            self.assertEqual(app.program_compare_left_gutter.get("1.0", "end-1c"), "1\n2\n3")
            self.assertEqual(app.program_compare_right_gutter.get("1.0", "end-1c"), "1\n2\n3")

            # Three selected: compare is disabled again.
            plans.append(FilePlan("c_R.MPF", "mpf", "R", "R.MPF", "keep"))
            plans[2].original_text = "L1\nL2\nL3"
            app.scan_result = ScanResult("root", plans)
            app.populate_file_tables()
            app.keep_table.selection_set(("0", "1", "2"))
            app._refresh_keep_menu_states()
            self.assertEqual(app.keep_table_menu.entrycget(1, "state"), "disabled")
        finally:
            root.destroy()

    def test_compare_gutters_keep_per_side_line_numbers(self):
        # 对比窗格各自为程序行连续编号；对齐产生的空行占位被压缩，
        # 短程序一侧不出现无编号空白（2026-08-05 修复）。
        root, app = self._build_app(1286, 668)
        try:
            plans = [
                FilePlan("a_P.MPF", "mpf", "P", "P.MPF", "keep"),
                FilePlan("b_Q.MPF", "mpf", "Q", "Q.MPF", "keep"),
            ]
            plans[0].original_text = "A\nC"
            plans[1].original_text = "A\nB\nC"
            app.scan_result = ScanResult("root", plans)
            app.populate_file_tables()
            app.keep_table.selection_set(("0", "1"))
            app.compare_selected_programs()
            self.assertEqual(app.program_compare_left_gutter.get("1.0", "end-1c"), "1\n2")
            self.assertEqual(app.program_compare_right_gutter.get("1.0", "end-1c"), "1\n2\n3")
        finally:
            root.destroy()

    def test_compare_panes_scroll_independently(self):
        # 左右两个程序区域滚动互不影响：滚动右侧时，左侧内容与行号都保持不动，
        # 且只有右侧自身的行号跟随内容滚动。
        root, app = self._build_app(1286, 668)
        try:
            plans = [
                FilePlan("a_P.MPF", "mpf", "P", "P.MPF", "keep"),
                FilePlan("b_Q.MPF", "mpf", "Q", "Q.MPF", "keep"),
            ]
            plans[0].original_text = "\n".join(f"L{i}" for i in range(1, 201))
            plans[1].original_text = "\n".join(f"R{i}" for i in range(1, 201))
            app.scan_result = ScanResult("root", plans)
            app.populate_file_tables()
            app.keep_table.selection_set(("0", "1"))
            app.compare_selected_programs()
            left_text, left_gutter = app.program_compare_left, app.program_compare_left_gutter
            right_text, right_gutter = app.program_compare_right, app.program_compare_right_gutter
            root.update_idletasks()
            self.assertAlmostEqual(left_text.yview()[0], 0.0)
            right_text.yview_scroll(20, "units")
            root.update_idletasks()
            right_first = right_text.yview()[0]
            right_gutter_first = right_gutter.yview()[0]
            # 右侧内容滚动了，且右侧行号跟随；左侧内容与行号保持不动。
            self.assertGreater(right_first, 0.0)
            self.assertGreater(right_gutter_first, 0.0)
            self.assertAlmostEqual(left_text.yview()[0], 0.0)
            self.assertAlmostEqual(left_gutter.yview()[0], 0.0)
            # 反之亦然：滚动左侧不影响右侧（右侧保持在原位置）。
            left_text.yview_scroll(30, "units")
            root.update_idletasks()
            self.assertGreater(left_text.yview()[0], 0.0)
            self.assertGreater(left_gutter.yview()[0], 0.0)
            self.assertAlmostEqual(right_text.yview()[0], right_first)
            self.assertAlmostEqual(right_gutter.yview()[0], right_gutter_first)
        finally:
            root.destroy()

    def test_cell_tooltip_shows_truncated_content_after_hover(self):
        # 单元格内容超出列宽时，悬停后弹出浮动窗口显示完整内容。
        with patch.object(gui, "CELL_TOOLTIP_DELAY_MS", 0):
            root, app = self._build_app(1286, 668)
            try:
                long_value = "T" * 300
                app.keep_table.insert("", "end", iid="0", values=("keep", "P", long_value, "T.MPF"))
                root.update_idletasks()
                root.update()
                bbox = app.keep_table.bbox("0", "source")
                self.assertIsNotNone(bbox)
                app.keep_table.event_generate("<Motion>", x=bbox[0] + 5, y=bbox[1] + 5, when="tail")
                self.assertTrue(self._pump_until(root, lambda: app.cell_tooltip.window.winfo_viewable(), "tooltip 应显示"))
                self.assertEqual(app.cell_tooltip.label.cget("text"), long_value)
            finally:
                root.destroy()

    def test_cell_tooltip_stays_hidden_for_fully_visible_cell(self):
        # 单元格内容完整可见时不弹出提示。
        with patch.object(gui, "CELL_TOOLTIP_DELAY_MS", 0):
            root, app = self._build_app(1286, 668)
            try:
                app.keep_table.insert("", "end", iid="0", values=("keep", "P", "SHORT", "T.MPF"))
                root.update_idletasks()
                root.update()
                bbox = app.keep_table.bbox("0", "action")
                self.assertIsNotNone(bbox)
                app.keep_table.event_generate("<Motion>", x=bbox[0] + 5, y=bbox[1] + 5, when="tail")
                self.assertTrue(self._pump_until(root, lambda: not app.cell_tooltip.window.winfo_viewable(), "tooltip 应保持隐藏"))
            finally:
                root.destroy()

    def test_cell_tooltip_hides_on_leave(self):
        # 鼠标移出表格后提示自动隐藏。
        with patch.object(gui, "CELL_TOOLTIP_DELAY_MS", 0):
            root, app = self._build_app(1286, 668)
            try:
                long_value = "T" * 300
                app.keep_table.insert("", "end", iid="0", values=("keep", "P", long_value, "T.MPF"))
                root.update_idletasks()
                root.update()
                bbox = app.keep_table.bbox("0", "source")
                self.assertIsNotNone(bbox)
                app.keep_table.event_generate("<Motion>", x=bbox[0] + 5, y=bbox[1] + 5, when="tail")
                self.assertTrue(self._pump_until(root, lambda: app.cell_tooltip.window.winfo_viewable(), "tooltip 应显示"))
                app.keep_table.event_generate("<Leave>", when="tail")
                self.assertTrue(self._pump_until(root, lambda: not app.cell_tooltip.window.winfo_viewable(), "tooltip 应隐藏"))
            finally:
                root.destroy()

    def test_runtime_columns_fit_the_actual_bold_heading_font(self):
        root, app = self._build_app(1286, 668)
        try:
            cases = (
                (app.keep_table, app.keep_column_specs, ("动作", "程序名", "MPF 源文件", "目标")),
                (app.tool_table, app.tool_column_specs, ("刀具号", "DIA", "TOOL_CONER", "TOOL_ANGLE", "TOOL_TYPE")),
            )
            for table, specs, headings in cases:
                for (name, _initial, minimum, _stretch), heading in zip(specs, headings):
                    with self.subTest(table=str(table), column=name):
                        required = app.tree_heading_font.measure(heading) + app.tree_heading_padding
                        self.assertGreaterEqual(minimum, required)
                        self.assertGreaterEqual(int(table.column(name, "width")), required)
            self.assertGreaterEqual(float(app.keep_table.xview()[1]), 0.999)
            self.assertGreaterEqual(float(app.tool_table.xview()[1]), 0.999)
        finally:
            root.destroy()

    def test_expanded_layout_gives_extra_width_only_to_stretch_columns(self):
        root, app = self._build_app(1286, 668)
        try:
            keep_columns = ("action", "program", "source", "target")
            tool_columns = ("number", "dia", "coner", "angle", "type")
            keep_before = {name: int(app.keep_table.column(name, "width")) for name in keep_columns}
            tool_before = {name: int(app.tool_table.column(name, "width")) for name in tool_columns}

            root.geometry("1600x900")
            root.update_idletasks()
            root.update()
            root.update_idletasks()

            keep_after = {name: int(app.keep_table.column(name, "width")) for name in keep_columns}
            tool_after = {name: int(app.tool_table.column(name, "width")) for name in tool_columns}
            for name, _initial, _minimum, stretch in gui.KEEP_COLUMN_SPECS:
                if stretch:
                    self.assertGreater(keep_after[name], keep_before[name])
                else:
                    self.assertEqual(keep_after[name], keep_before[name])
            for name, _initial, _minimum, stretch in gui.TOOL_COLUMN_SPECS:
                if stretch:
                    self.assertGreater(tool_after[name], tool_before[name])
                else:
                    self.assertEqual(tool_after[name], tool_before[name])
        finally:
            root.destroy()

    def test_runtime_font_profile_is_applied_to_tables(self):
        widths = {"程序名": 45, "999 错 / 999 警": 105, "刀具号": 48}
        profile = gui.font_layout_profile(lambda text: widths.get(text, 40))
        with patch("ncodeprocess.gui.font_layout_profile", return_value=profile):
            root, app = self._build_app(1600, 900)
        try:
            issue = app.keep_issue_table.column("issues")
            number = app.tool_table.column("number")
            self.assertEqual(int(issue["width"]), profile.validation_width)
            self.assertEqual(int(issue["minwidth"]), profile.validation_width)
            self.assertGreaterEqual(profile.validation_width, 125)
            self.assertGreaterEqual(int(number["width"]), 68)
            self.assertGreaterEqual(int(number["minwidth"]), 68)
            for name, _initial, minimum, stretch in profile.keep_specs:
                config = app.keep_table.column(name)
                self.assertGreaterEqual(int(config["minwidth"]), minimum)
                self.assertEqual(bool(config["stretch"]), stretch)
            for name, _initial, minimum, stretch in profile.tool_specs:
                config = app.tool_table.column(name)
                self.assertGreaterEqual(int(config["minwidth"]), minimum)
                self.assertEqual(bool(config["stretch"]), stretch)
            self.assertLessEqual(abs(app.left_notebook.winfo_width() - app.right_detail_frame.winfo_width()), 2)
        finally:
            root.destroy()

    def test_keep_and_tool_tables_fit_then_scroll_after_manual_widening(self):
        root, app = self._build_app(1286, 668)
        try:
            keep_columns = ("action", "program", "source", "target")
            tool_columns = ("number", "dia", "coner", "angle", "type")
            for table in (app.keep_table, app.tool_table):
                self.assertGreaterEqual(float(table.xview()[1]), 0.999)

            keep_before = {name: int(app.keep_table.column(name, "width")) for name in keep_columns}
            tool_before = {name: int(app.tool_table.column(name, "width")) for name in tool_columns}
            root.geometry("1600x900")
            root.update_idletasks()
            root.update()
            root.update_idletasks()
            for name, _initial, _minimum, stretch in app.keep_column_specs:
                width = int(app.keep_table.column(name, "width"))
                self.assertGreater(width, keep_before[name]) if stretch else self.assertEqual(width, keep_before[name])
            for name, _initial, _minimum, stretch in app.tool_column_specs:
                width = int(app.tool_table.column(name, "width"))
                self.assertGreater(width, tool_before[name]) if stretch else self.assertEqual(width, tool_before[name])

            root.geometry("1286x668")
            root.update_idletasks()
            root.update()
            root.update_idletasks()
            resized_before = {
                (app.keep_table, "source"): int(app.keep_table.column("source", "width")),
                (app.tool_table, "type"): int(app.tool_table.column("type", "width")),
            }

            def separator_x(table, column):
                expected = f"#{tuple(table['columns']).index(column) + 1}"
                matches = [
                    x for x in range(table.winfo_width())
                    if table.identify_region(x, 5) == "separator"
                    and table.identify_column(x) == expected
                ]
                self.assertTrue(matches, f"could not find {column} separator")
                return matches[len(matches) // 2]

            for table, column in ((app.keep_table, "source"), (app.tool_table, "type")):
                x = separator_x(table, column)
                table.event_generate("<ButtonPress-1>", x=x, y=5)
                table.event_generate("<B1-Motion>", x=x + 152, y=5)
                table.event_generate("<ButtonRelease-1>", x=x + 152, y=5)
            root.update_idletasks()
            self.assertGreaterEqual(int(app.keep_table.column("source", "width")), resized_before[(app.keep_table, "source")] + 150)
            self.assertGreaterEqual(int(app.tool_table.column("type", "width")), resized_before[(app.tool_table, "type")] + 150)
            keep_wide_width = int(app.keep_table.column("source", "width"))
            tool_wide_width = int(app.tool_table.column("type", "width"))
            for table, column in ((app.keep_table, "source"), (app.tool_table, "type")):
                table.xview_moveto(1)
                root.update_idletasks()
                x = separator_x(table, column)
                table.event_generate("<ButtonPress-1>", x=x, y=5)
                table.event_generate("<B1-Motion>", x=x - 80, y=5)
                table.event_generate("<ButtonRelease-1>", x=x - 80, y=5)
            root.update_idletasks()
            keep_minimum = next(minimum for name, _initial, minimum, _stretch in app.keep_column_specs if name == "source")
            tool_minimum = next(minimum for name, _initial, minimum, _stretch in app.tool_column_specs if name == "type")
            self.assertLess(int(app.keep_table.column("source", "width")), keep_wide_width)
            self.assertLess(int(app.tool_table.column("type", "width")), tool_wide_width)
            self.assertGreaterEqual(int(app.keep_table.column("source", "width")), keep_minimum)
            self.assertGreaterEqual(int(app.tool_table.column("type", "width")), tool_minimum)
            keep_manual_width = int(app.keep_table.column("source", "width"))
            tool_manual_width = int(app.tool_table.column("type", "width"))
            keep_target_before = int(app.keep_table.column("target", "width"))
            tool_coner_before = int(app.tool_table.column("coner", "width"))

            root.geometry("1600x900")
            root.update_idletasks()
            root.update()
            root.update_idletasks()
            self.assertGreaterEqual(int(app.keep_table.column("source", "width")), keep_manual_width)
            self.assertGreaterEqual(int(app.tool_table.column("type", "width")), tool_manual_width)
            self.assertFalse(bool(app.keep_table.column("source", "stretch")))
            self.assertFalse(bool(app.tool_table.column("type", "stretch")))
            self.assertGreater(int(app.keep_table.column("target", "width")), keep_target_before)
            self.assertGreater(int(app.tool_table.column("coner", "width")), tool_coner_before)

            root.geometry("1286x668")
            root.update_idletasks()
            root.update()
            root.update_idletasks()
            self.assertGreaterEqual(int(app.keep_table.column("source", "width")), keep_manual_width)
            self.assertGreaterEqual(int(app.tool_table.column("type", "width")), tool_manual_width)
            self.assertLess(float(app.keep_table.xview()[1]), 1.0)
            self.assertLess(float(app.tool_table.xview()[1]), 1.0)
            app.keep_table.xview_moveto(1)
            app.tool_table.xview_moveto(1)
            root.update_idletasks()
            self.assertGreater(float(app.keep_table.xview()[0]), 0.0)
            self.assertGreater(float(app.tool_table.xview()[0]), 0.0)
        finally:
            root.destroy()

    def test_keep_table_display_adds_gap_without_changing_file_plan_paths(self):
        root, app = self._build_app(1286, 668)
        try:
            file_plan = FilePlan(
                source="source-file.MPF",
                kind="mpf",
                program="PROGRAM",
                target="target-file.MPF",
            )
            app.scan_result = SimpleNamespace(files=[file_plan])
            app.populate_file_tables()
            values = app.keep_table.item("0", "values")
            self.assertEqual(values[0], "keep")
            self.assertEqual(values[1], "PROGRAM")
            self.assertEqual(values[2], " source-file.MPF")
            self.assertEqual(values[3], " target-file.MPF")
            self.assertEqual(file_plan.source, "source-file.MPF")
            self.assertEqual(file_plan.target, "target-file.MPF")
        finally:
            root.destroy()

    def test_process_info_controls_fit_inside_1286_pixel_window(self):
        root = tk.Tk()
        root.withdraw()
        try:
            with patch.object(App, "scan", lambda _self: None):
                app = App(root)
            root.geometry("1286x668")
            root.deiconify()
            root.update()
            root.update_idletasks()

            controls = (
                app.process_info_frame,
                app.folder_choice_combo,
                app.drawing_choice_button,
            )

            for control in controls:
                self.assertTrue(control.winfo_ismapped())
                self.assertGreater(control.winfo_width(), 0)
                self.assertLessEqual(
                    self._relative_x_to_root(control, root) + control.winfo_width(),
                    root.winfo_width(),
                )

            self.assertLessEqual(
                app.drawing_choice_button.winfo_reqwidth(),
                app.drawing_choice_button.winfo_width(),
            )

            for button in self._collect_buttons(app.process_info_frame):
                self.assertLessEqual(button.winfo_reqwidth(), button.winfo_width())
        finally:
            root.destroy()

    def test_custom_tool_controls_stay_adjacent_and_visible_at_1286_width(self):
        # 自定义刀具类型控件位于程序信息区（G00 已移入设置，此处为其独立一行），
        # 输入框与按钮紧邻、不超出程序信息区右边界；程序信息区内不再有 G00 下拉框。
        root, app = self._build_app(1286, 668)
        try:
            entry = app.custom_tool_type_entry
            button = app.add_tool_type_button
            for control in (entry, button):
                self.assertTrue(control.winfo_ismapped())
                self.assertGreaterEqual(control.winfo_width(), control.winfo_reqwidth())
                self.assertGreaterEqual(control.winfo_height(), control.winfo_reqheight())

            ancestors = set()
            current = entry.master
            while current is not None:
                ancestors.add(current)
                current = current.master
            self.assertIn(app.process_info_frame, ancestors)

            g00_in_frame = any(
                child.winfo_class() == "TCombobox"
                and str(child.cget("textvariable")) == str(app.g00_level)
                for child in self._descendants(app.process_info_frame)
            )
            self.assertFalse(g00_in_frame)

            entry_right = self._relative_x_to_root(entry, root) + entry.winfo_width()
            button_left = self._relative_x_to_root(button, root)
            self.assertGreaterEqual(button_left - entry_right, 0)
            self.assertLessEqual(button_left - entry_right, 8)

            button_right = button_left + button.winfo_width()
            frame_right = (
                self._relative_x_to_root(app.process_info_frame, root)
                + app.process_info_frame.winfo_width()
            )
            self.assertLessEqual(button_right, frame_right)
            self.assertLessEqual(button_right, root.winfo_width())
        finally:
            root.destroy()

    def test_minimum_layout_has_no_panedwindow_and_fits_width(self):
        root = tk.Tk()
        root.withdraw()
        try:
            with patch.object(App, "scan", lambda _self: None):
                app = App(root)
            root.geometry("1286x668")
            root.update_idletasks()

            classes = []

            def collect_classes(widget):
                classes.append(widget.winfo_class())
                for child in widget.winfo_children():
                    collect_classes(child)

            collect_classes(app)
            self.assertNotIn("TPanedwindow", classes)
            self.assertLessEqual(app.winfo_reqwidth(), 1286)
            self.assertLessEqual(app.winfo_reqheight(), 668)
        finally:
            root.destroy()


class SettingsDialogTests(unittest.TestCase, LayoutWidgetMixin):
    def test_parse_delete_extensions_normalizes_and_validates(self):
        self.assertEqual(gui.parse_delete_extensions(".LOG, .moaptindexes"), {".log", ".moaptindexes"})
        self.assertEqual(gui.parse_delete_extensions(""), set())
        self.assertEqual(gui.parse_delete_extensions(".log；.txt"), {".log", ".txt"})
        with self.assertRaises(ValueError):
            gui.parse_delete_extensions("log")  # 缺前导点
        with self.assertRaises(ValueError):
            gui.parse_delete_extensions(".bad_ext;")  # 含非法字符

    def test_settings_button_and_vars_exist(self):
        root, app = self._build_app(1286, 668)
        try:
            self.assertTrue(app.settings_button.winfo_ismapped())
            self.assertEqual(app.encoding_var.get(), "auto")
            self.assertEqual(app.delete_extensions_var.get(), ".log, .moaptindexes")
            self.assertEqual(app.allowed_name_pattern_var.get(), r"^[A-Za-z0-9_一-鿿-]+$")
            self.assertEqual(app.aptsource_dir_var.get(), "aptsource")
            self.assertTrue(app.require_end_marker_var.get())
            self.assertFalse(app.require_m06_var.get())
            self.assertFalse(app.require_spindle_speed_var.get())
        finally:
            root.destroy()

    def test_settings_button_right_of_recursive_and_frame_title(self):
        # "程序设置…"按钮位于"包含子目录"复选框右侧，顶部栏更名为"程序运行配置"。
        root, app = self._build_app(1286, 668)
        try:
            self.assertGreater(
                self._relative_x_to_root(app.settings_button, root),
                self._relative_x_to_root(app.recursive_checkbox, root),
            )
            self.assertEqual(app.settings_button.master.cget("text"), "程序运行配置")
        finally:
            root.destroy()

    def test_settings_dialog_confirm_applies_and_persists(self):
        root, app = self._build_app(1286, 668)
        try:
            app.settings_registry_key = TEST_SETTINGS_KEY
            with patch.object(App, "scan") as scan_mock:
                app.open_settings()
                app.encoding_var.set("gb18030")
                app.delete_extensions_var.set(".log")
                app.program_extensions_var.set(".mpf,.nc")
                app.require_m06_var.set(True)
                app.require_end_marker_var.set(False)
                app._confirm_settings()
                self.assertIsNone(app.settings_window)
                config = app.config()
                self.assertEqual(config.encoding, "gb18030")
                self.assertEqual(config.delete_extensions, {".log"})
                self.assertEqual(config.program_extensions, {".mpf", ".nc"})
                self.assertTrue(config.require_m06)
                self.assertFalse(config.require_end_marker)
                scan_mock.assert_called_once_with()
            saved = load_all(TEST_SETTINGS_KEY)
            self.assertEqual(saved["encoding"], "gb18030")
            self.assertEqual(saved["program_extensions"], ".mpf,.nc")
        finally:
            clear_all(TEST_SETTINGS_KEY)
            root.destroy()

    def test_settings_dialog_cancel_discards(self):
        root, app = self._build_app(1286, 668)
        try:
            app.open_settings()
            app.encoding_var.set("gb18030")
            app._cancel_settings()
            self.assertEqual(app.config().encoding, "auto")
        finally:
            root.destroy()

    def test_settings_dialog_rejects_invalid_values(self):
        root, app = self._build_app(1286, 668)
        try:
            app.open_settings()
            with patch("ncodeprocess.gui.messagebox.showerror") as err_mock:
                app.delete_extensions_var.set("txt")
                app._confirm_settings()
                err_mock.assert_called_once()
            self.assertIsNotNone(app.settings_window)  # 对话框未关闭
            self.assertEqual(app.config().delete_extensions, {".log", ".moaptindexes"})

            app.allowed_name_pattern_var.set("[")
            with patch("ncodeprocess.gui.messagebox.showerror") as err_mock:
                app._confirm_settings()
                err_mock.assert_called_once()
            self.assertIsNotNone(app.settings_window)
        finally:
            root.destroy()

    def test_finish_scan_applies_configured_name_pattern(self):
        root, app = self._build_app(1286, 668)
        try:
            app.allowed_name_pattern_var.set(r"^[A-Za-z0-9]+$")
            plan = FilePlan("程序_x.MPF", "mpf", None, None, "keep")
            result = ScanResult("root", [plan])
            with patch("ncodeprocess.gui.simpledialog.askstring", return_value="程序名"):
                app.finish_scan(result)
            self.assertIsNone(plan.program)  # 中文名被收紧后的模式拒绝
            plan.issues = []
            with patch("ncodeprocess.gui.simpledialog.askstring", return_value="PROG1"):
                app.finish_scan(result)
            self.assertEqual(plan.program, "PROG1")
        finally:
            root.destroy()

    def test_settings_dialog_fits_and_controls_visible(self):
        root, app = self._build_app(1286, 668)
        try:
            app.open_settings()
            win = app.settings_window
            win.update_idletasks()
            self.assertLessEqual(win.winfo_reqwidth(), 640)
            self.assertLessEqual(win.winfo_reqheight(), 500)
            self.assertGreaterEqual(win.winfo_reqwidth(), 400)
            self.assertGreaterEqual(win.winfo_reqheight(), 300)
            texts = {button.cget("text") for button in self._collect_buttons(win)}
            self.assertIn("确定", texts)
            self.assertIn("取消", texts)
            self.assertIn("恢复默认", texts)
            self.assertNotIn("清除注册表", texts)
        finally:
            root.destroy()

    def test_settings_dialog_has_max_limits_inputs(self):
        # WP-C1：文件大小/数量上限输入存在，config() 按留空/非法回退 0 解析。
        root, app = self._build_app(1286, 668)
        try:
            app.open_settings()
            self.assertIsNotNone(app.max_file_size_var)
            self.assertIsNotNone(app.max_files_var)
            app.max_file_size_var.set("2048")
            app.max_files_var.set("abc")
            cfg = app.config()
            self.assertEqual(cfg.max_file_size, 2048)
            self.assertEqual(cfg.max_files, 0)
        finally:
            root.destroy()

    def test_settings_pages_frames_share_same_width(self):
        # WP-C1 布局统一：基本设置与校验规则页的 LabelFrame 宽度一致，切换不跳动。
        root, app = self._build_app(1286, 668)
        try:
            app.open_settings()
            win = app.settings_window
            win.update()
            basic, rules = app.settings_pages

            def frame_widths(page):
                return [child.winfo_width() for child in page.winfo_children()
                        if child.winfo_class() == "TLabelframe"]

            app.settings_notebook.select(0)
            win.update()
            basic_widths = frame_widths(basic)
            app.settings_notebook.select(1)
            win.update()
            rules_widths = frame_widths(rules)
            self.assertTrue(basic_widths)
            self.assertTrue(rules_widths)
            self.assertEqual(len(set(basic_widths)), 1)
            self.assertEqual(len(set(rules_widths)), 1)
            self.assertEqual(basic_widths[0], rules_widths[0])
        finally:
            root.destroy()

    def test_settings_dialog_has_retract_z_threshold_input(self):
        # WP-C9：抬刀高度阈值输入存在，config() 按留空/非法回退默认 20 解析。
        root, app = self._build_app(1286, 668)
        try:
            app.open_settings()
            self.assertIsNotNone(app.retract_z_threshold_var)
            app.retract_z_threshold_var.set("abc")
            self.assertEqual(app.config().retract_z_threshold, 20.0)
            app.retract_z_threshold_var.set("12")
            self.assertEqual(app.config().retract_z_threshold, 12.0)
        finally:
            root.destroy()

    def test_parse_extension_list_and_output_extension(self):
        self.assertEqual(gui.parse_extension_list(".MPF, .nc"), {".mpf", ".nc"})
        self.assertEqual(gui.parse_extension_list(""), set())
        with self.assertRaises(ValueError):
            gui.parse_extension_list("mpf")
        self.assertEqual(gui.parse_output_extension(".MPF"), ".MPF")
        self.assertEqual(gui.parse_output_extension(".nc"), ".nc")
        with self.assertRaises(ValueError):
            gui.parse_output_extension("MPF")
        with self.assertRaises(ValueError):
            gui.parse_output_extension("")

    def test_settings_vars_loaded_from_registry(self):
        root = tk.Tk()
        root.withdraw()
        try:
            with patch.object(App, "scan", lambda _self: None):
                save_all({"encoding": "gb18030", "program_extensions": ".mpf,.nc", "require_m06": "1"}, TEST_SETTINGS_KEY)
                app = App(root, settings_registry_key=TEST_SETTINGS_KEY)
            self.assertEqual(app.encoding_var.get(), "gb18030")
            self.assertEqual(app.program_extensions_var.get(), ".mpf,.nc")
            self.assertTrue(app.require_m06_var.get())
            # 未持久化的项使用默认值
            self.assertEqual(app.delete_extensions_var.get(), ".log, .moaptindexes")
        finally:
            clear_all(TEST_SETTINGS_KEY)
            root.destroy()

    def test_restore_defaults_resets_and_persists(self):
        root, app = self._build_app(1286, 668)
        try:
            app.settings_registry_key = TEST_SETTINGS_KEY
            save_all({"encoding": "gb18030", "require_m06": "1", "bianzhi": "张工"}, TEST_SETTINGS_KEY)
            with patch.object(App, "scan") as scan_mock:
                app.open_settings()
                app._restore_default_settings()
            self.assertEqual(app.encoding_var.get(), "auto")
            self.assertFalse(app.require_m06_var.get())
            # 统一恢复默认：编制/审核也回到默认（空）
            self.assertEqual(app.info_vars["bianzhi"].get(), "")
            saved = load_all(TEST_SETTINGS_KEY)
            self.assertEqual(saved["encoding"], "auto")
            self.assertEqual(saved["bianzhi"], "")
            # WP-C8：恢复默认后保存位置回到注册表（并清空另两处残留）。
            self.assertEqual(app.storage_backend_var.get(), "registry")
            self.assertEqual(saved["storage_backend"], "registry")
            scan_mock.assert_called_once_with()
        finally:
            clear_all(TEST_SETTINGS_KEY)
            root.destroy()

    def test_config_injects_vars_and_required_fields(self):
        root, app = self._build_app(1286, 668)
        try:
            for name in ("required_bianzhi_var", "required_shenhe_var", "required_drawing_var", "required_part_var"):
                self.assertTrue(getattr(app, name).get())
            self.assertEqual([key for key, _label, _required in FIELD_ORDER], app.config().required_fields)

            app.encoding_var.set("gb18030")
            app.delete_extensions_var.set(".log")
            app.allowed_name_pattern_var.set(r"^[A-Za-z0-9]+$")
            app.aptsource_dir_var.set("archive")
            app.program_extensions_var.set(".mpf,.nc")
            app.program_output_extension_var.set(".NC")
            app.require_end_marker_var.set(False)
            app.require_m06_var.set(True)
            app.require_spindle_speed_var.set(True)
            config = app.config()
            self.assertEqual(config.encoding, "gb18030")
            self.assertEqual(config.delete_extensions, {".log"})
            self.assertEqual(config.allowed_name_pattern, r"^[A-Za-z0-9]+$")
            self.assertEqual(config.aptsource_dir, "archive")
            self.assertEqual(config.program_extensions, {".mpf", ".nc"})
            self.assertEqual(config.program_output_extension, ".NC")
            self.assertFalse(config.require_end_marker)
            self.assertTrue(config.require_m06)
            self.assertTrue(config.require_spindle_speed)
        finally:
            root.destroy()

    def test_settings_dialog_toggles_required_field(self):
        root, app = self._build_app(1286, 668)
        try:
            app.settings_registry_key = TEST_SETTINGS_KEY
            with patch.object(App, "scan") as scan_mock:
                app.open_settings()
                app.required_shenhe_var.set(False)
                app._confirm_settings()
                scan_mock.assert_called_once_with()
            config = app.config()
            self.assertNotIn("SHENHE", config.required_fields)
            self.assertIn("BIANZHI", config.required_fields)
            # 程序/机床/控制系统为固定必填，对话框不可取消
            self.assertIn("PROGRAM", config.required_fields)
            self.assertIn("NC MACHINE", config.required_fields)
            self.assertIn("CONTROL SYSTEM", config.required_fields)
        finally:
            clear_all(TEST_SETTINGS_KEY)
            root.destroy()

    def test_batch2_var_defaults_and_roundtrip(self):
        root, app = self._build_app(1286, 668)
        try:
            self.assertEqual(app.m03_position_var.get(), "after-s")
            self.assertEqual(app.newline_var.get(), "auto")
            for name in ("feed_min_var", "feed_max_var", "spindle_min_var", "spindle_max_var"):
                self.assertEqual(getattr(app, name).get(), "")

            app.m03_position_var.set("standalone")
            app.newline_var.set("lf")
            app.feed_min_var.set("100")
            config = app.config()
            self.assertEqual(config.m03_position, "standalone")
            self.assertEqual(config.newline, "lf")
            self.assertEqual(config.feed_min, 100.0)
            self.assertIsNone(config.feed_max)
            self.assertIsNone(config.spindle_min)
            self.assertIsNone(config.spindle_max)
        finally:
            root.destroy()

    def test_settings_dialog_rejects_non_numeric_limits(self):
        root, app = self._build_app(1286, 668)
        try:
            app.open_settings()
            with patch("ncodeprocess.gui.messagebox.showerror") as err_mock:
                app.feed_min_var.set("abc")
                app._confirm_settings()
                err_mock.assert_called_once()
            self.assertIsNotNone(app.settings_window)  # 对话框未关闭
        finally:
            root.destroy()

    def test_aux_checks_vars_default_enabled(self):
        root, app = self._build_app(1286, 668)
        try:
            self.assertEqual(
                {"m03-before-motion", "m05-before-end", "m08-before-cut", "m09-before-end"},
                app.config().aux_checks,
            )
        finally:
            root.destroy()

    def test_settings_dialog_toggles_aux_rule(self):
        root, app = self._build_app(1286, 668)
        try:
            app.settings_registry_key = TEST_SETTINGS_KEY
            with patch.object(App, "scan") as scan_mock:
                app.open_settings()
                app.aux_m08_before_cut_var.set(False)
                app._confirm_settings()
                scan_mock.assert_called_once_with()
            self.assertNotIn("m08-before-cut", app.config().aux_checks)
            self.assertIn("m03-before-motion", app.config().aux_checks)
        finally:
            clear_all(TEST_SETTINGS_KEY)
            root.destroy()

    def test_settings_dialog_pages_switch_and_controls_visible(self):
        # 设置对话框为 Notebook 两页：基本设置 / 校验规则，切换页签时互斥显示。
        root, app = self._build_app(1286, 668)
        try:
            app.open_settings()
            notebook = app.settings_notebook
            self.assertEqual(len(notebook.tabs()), 2)
            self.assertEqual(notebook.tab(0, "text"), "基本设置")
            self.assertEqual(notebook.tab(1, "text"), "校验规则")
            basic, rules = app.settings_pages
            notebook.select(0)
            root.update()
            self.assertTrue(basic.winfo_ismapped())
            self.assertFalse(rules.winfo_ismapped())
            notebook.select(1)
            root.update()
            self.assertFalse(basic.winfo_ismapped())
            self.assertTrue(rules.winfo_ismapped())
        finally:
            root.destroy()

    def test_settings_dialog_has_g00_level_control(self):
        # G00 级别下拉框已从主窗口移入设置对话框的校验规则页。
        root, app = self._build_app(1286, 668)
        try:
            app.open_settings()
            rules = app.settings_pages[1]
            combo = next(
                child
                for child in self._descendants(rules)
                if child.winfo_class() == "TCombobox"
                and str(child.cget("textvariable")) == str(app.g00_level)
            )
            self.assertEqual(tuple(combo.cget("values")), ("error", "warning", "allow"))
        finally:
            root.destroy()

    def test_settings_dialog_has_heuristic_threshold_controls(self):
        # WP-10：校验规则页提供 F 离群档位参数（门槛/比值/包络）、多 S 警告开关。
        root, app = self._build_app(1286, 668)
        try:
            app.open_settings()
            rules = app.settings_pages[1]
            target_vars = (
                str(app.feed_outlier_min_count_var),
                str(app.feed_outlier_ratio_var),
                str(app.feed_outlier_low_ratio_var),
                str(app.feed_outlier_high_ratio_var),
            )
            entries = [
                widget for widget in self._descendants(rules)
                if widget.winfo_class() == "TEntry"
                and str(widget.cget("textvariable")) in target_vars
            ]
            self.assertEqual(len(entries), 4)
            checkbuttons = [
                widget for widget in self._descendants(rules)
                if widget.winfo_class() == "TCheckbutton" and widget.cget("text").startswith("多 S 值警告")
            ]
            self.assertEqual(len(checkbuttons), 1)
        finally:
            root.destroy()

    def test_config_reads_heuristic_thresholds(self):
        # WP-10：Config 读取 GUI 输入；留空或非法输入回退默认。
        root, app = self._build_app(1286, 668)
        try:
            app.feed_outlier_min_count_var.set("4")
            app.feed_outlier_ratio_var.set("1.5")
            app.feed_outlier_low_ratio_var.set("0.7")
            app.feed_outlier_high_ratio_var.set("1.4")
            app.multiple_spindle_var.set(False)
            config = app.config()
            self.assertEqual(config.feed_outlier_min_count, 4)
            self.assertEqual(config.feed_outlier_ratio, 1.5)
            self.assertEqual(config.feed_outlier_low_ratio, 0.7)
            self.assertEqual(config.feed_outlier_high_ratio, 1.4)
            self.assertFalse(config.multiple_spindle_warn)

            app.feed_outlier_min_count_var.set("abc")
            app.feed_outlier_ratio_var.set("")
            app.feed_outlier_low_ratio_var.set("")
            app.feed_outlier_high_ratio_var.set("")
            config = app.config()
            self.assertEqual(config.feed_outlier_min_count, 3)
            self.assertEqual(config.feed_outlier_ratio, 2.0)
            self.assertEqual(config.feed_outlier_low_ratio, 0.8)
            self.assertEqual(config.feed_outlier_high_ratio, 1.2)
        finally:
            root.destroy()

    def test_settings_dialog_has_storage_backend_control_and_export_button(self):
        # WP-11：基本设置页提供配置保存位置下拉与导出设置按钮。
        root, app = self._build_app(1286, 668)
        try:
            app.open_settings()
            basic = app.settings_pages[0]
            combo = next(
                child for child in self._descendants(basic)
                if child.winfo_class() == "TCombobox"
                and str(child.cget("textvariable")) == str(app.storage_backend_var)
            )
            self.assertEqual(tuple(combo.cget("values")), ("registry", "appdata", "home"))
            buttons = self._collect_buttons(app.settings_window)
            self.assertIn("导出设置…", {button.cget("text") for button in buttons})
        finally:
            root.destroy()

    def test_settings_loads_from_selected_appdata_backend(self):
        # WP-11：storage_backend=appdata 时，启动从 appdata 文件加载 Batch 2 值。
        import tempfile
        from pathlib import Path as PathType
        import ncodeprocess.preferences as prefs
        with tempfile.TemporaryDirectory(prefix="ncp-gui-prefs-") as temp:
            root_dir = PathType(temp)
            appdata_dir = root_dir / "appdata"
            with patch.dict(os.environ, {"APPDATA": str(appdata_dir)}), \
                 patch.object(prefs.Path, "home", return_value=root_dir / "home"):
                save_all({
                    "feed_min": "100", "m03_position": "standalone",
                    "required_bianzhi": "0", "storage_backend": "appdata",
                }, TEST_SETTINGS_KEY, backend="appdata")
                root = tk.Tk()
                root.withdraw()
                try:
                    with patch.object(App, "scan", lambda _self: None):
                        app = App(root, settings_registry_key=TEST_SETTINGS_KEY)
                    self.assertEqual(app.feed_min_var.get(), "100")
                    self.assertEqual(app.m03_position_var.get(), "standalone")
                    self.assertFalse(app.required_bianzhi_var.get())
                    self.assertEqual(app.storage_backend_var.get(), "appdata")
                finally:
                    root.destroy()
                clear_all(TEST_SETTINGS_KEY)

    def test_confirm_settings_saves_batch2_values(self):
        # WP-11：确定保存后，Batch 2 + WP-10 设置写入选定后端。
        root, app = self._build_app(1286, 668)
        try:
            app.settings_registry_key = TEST_SETTINGS_KEY
            app.open_settings()
            app.feed_min_var.set("150")
            app.feed_outlier_min_count_var.set("4")
            app.required_drawing_var.set(False)
            with patch.object(App, "scan"):
                app._confirm_settings()
            loaded = load_all(TEST_SETTINGS_KEY)
            self.assertEqual(loaded.get("feed_min"), "150")
            self.assertEqual(loaded.get("feed_outlier_min_count"), "4")
            self.assertEqual(loaded.get("required_drawing"), "0")
            self.assertEqual(loaded.get("storage_backend"), "registry")
        finally:
            clear_all(TEST_SETTINGS_KEY)
            root.destroy()

    def test_single_instance_mutex_name_is_stable_and_path_specific(self):
        # WP-12：同一路径生成相同互斥体名，不同路径互不相同。
        first = gui.single_instance_mutex_name(r"C:\dir\NCodeProcess.exe")
        second = gui.single_instance_mutex_name(r"C:\dir\NCodeProcess.exe")
        other = gui.single_instance_mutex_name(r"C:\other\NCodeProcess.exe")
        self.assertEqual(first, second)
        self.assertNotEqual(first, other)
        self.assertTrue(first.startswith("NCodeProcess_"))
        # WP-S1：FNV-1a 64 位输出格式（16 位十六进制），锁定格式防止回归。
        self.assertRegex(first, r"^NCodeProcess_[0-9a-f]{16}$")

    @unittest.skipUnless(sys.platform == "win32", "命名互斥体仅存在于 Windows")
    def test_acquire_single_instance_second_call_fails(self):
        # WP-12：同目录第二个实例获取互斥体失败。
        self.assertTrue(gui.acquire_single_instance(r"C:\dir\NCodeProcess.exe"))
        try:
            self.assertFalse(gui.acquire_single_instance(r"C:\dir\NCodeProcess.exe"))
        finally:
            gui.release_single_instance()

    def test_required_field_checkbuttons_have_equal_spacing(self):
        # 必填 MSG 字段的 4 个勾选项位于同一容器内等间距排列。
        root, app = self._build_app(1286, 668)
        try:
            app.open_settings()
            rules = app.settings_pages[1]
            checkbuttons = [
                widget for widget in self._descendants(rules)
                if widget.winfo_class() == "TCheckbutton"
                and widget.cget("text") in ("编制", "审核", "图号", "版次")
            ]
            self.assertEqual(len(checkbuttons), 4)
            root.update()
            positions = [widget.winfo_x() for widget in checkbuttons]
            deltas = [positions[i + 1] - positions[i] for i in range(len(positions) - 1)]
            self.assertLessEqual(max(deltas) - min(deltas), 2)  # 相邻间距一致（容差 2px）
        finally:
            root.destroy()

    def test_feed_spindle_limit_rows_have_tight_consistent_spacing(self):
        # F/S 上下限合并于同一行容器（F 段与 S 段），每段「输入框 ~ 输入框」
        # 内部紧凑排列：不与共享网格列对齐导致大间距，段内间隙一致且很小。
        root, app = self._build_app(1286, 668)
        try:
            app.open_settings()
            rules = app.settings_pages[1]
            limit_vars = {
                str(app.feed_min_var), str(app.feed_max_var),
                str(app.spindle_min_var), str(app.spindle_max_var),
            }
            limit_entries = [
                widget for widget in self._descendants(rules)
                if widget.winfo_class() == "TEntry"
                and str(widget.cget("textvariable")) in limit_vars
            ]
            self.assertEqual(len(limit_entries), 4)
            frames = {widget.master for widget in limit_entries}
            self.assertEqual(len(frames), 1)
            frame = next(iter(frames))
            children = frame.winfo_children()
            self.assertGreaterEqual(len(children), 7)
            root.update()
            for start in (1, 5):  # F 段与 S 段的 (输入框 ~ 输入框)
                positions = [children[start + i].winfo_x() for i in range(3)]
                widths = [children[start + i].winfo_width() for i in range(3)]
                gaps = [positions[i + 1] - (positions[i] + widths[i]) for i in range(2)]
                self.assertLessEqual(max(gaps) - min(gaps), 2)  # 相邻间隙一致（容差 2px）
                self.assertLessEqual(gaps[0], 6)     # 间隙足够小（紧凑）
        finally:
            root.destroy()


class StartupCallbackTests(unittest.TestCase):
    def test_destroy_cancels_startup_callbacks_before_replacing_root(self):
        root = tk.Tk()
        root.withdraw()
        callback_errors = []
        root.report_callback_exception = lambda *error: callback_errors.append(error)
        try:
            with patch.object(App, "scan", lambda _self: None):
                app = App(root)
            app.destroy()
            self.assertEqual(getattr(app, "_startup_after_ids", {"missing"}), set())
            root.update()
        finally:
            root.destroy()

        replacement = tk.Tk()
        replacement.withdraw()
        replacement_errors = []
        replacement.report_callback_exception = lambda *error: replacement_errors.append(error)
        try:
            replacement.update()
            self.assertEqual(callback_errors, [])
            self.assertEqual(replacement_errors, [])
        finally:
            replacement.destroy()


class ReportExportTests(unittest.TestCase):
    def test_export_report_saves_directly_to_application_data_directory(self):
        data_dir = Path(tempfile.mkdtemp(prefix="ncodeprocess-gui-report-")) / "NCodeProcessData"
        app = SimpleNamespace(
            report=ProcessReport("input", "output", "start"),
            data_dir=data_dir,
            master=None,
        )
        with patch("ncodeprocess.gui.messagebox.showinfo") as showinfo, patch("ncodeprocess.gui.messagebox.showerror") as showerror:
            App.export_report(app)
        reports = list(data_dir.glob("ncodeprocess-report-*.json"))
        self.assertEqual(len(reports), 1)
        showinfo.assert_called_once()
        showerror.assert_not_called()


class ScanLifecycleTests(unittest.TestCase, LayoutWidgetMixin):
    def test_finish_scan_ignores_stale_generation(self):
        root, app = self._build_app(1286, 668)
        try:
            app._scan_generation = 2
            app.scan_result = None
            stale = ScanResult("stale", [], warnings=["stale"])
            with patch("ncodeprocess.gui.messagebox.showwarning"):
                app.finish_scan(stale, 1)   # 旧代结果：应被忽略
                self.assertIsNone(app.scan_result)
                app.finish_scan(stale, 2)   # 当前代结果：应生效
            self.assertIs(app.scan_result, stale)
        finally:
            root.destroy()

    def test_safe_after_does_not_raise_after_destroy(self):
        root, app = self._build_app(1286, 668)
        root.destroy()
        app._safe_after(0, lambda: None)   # 不应抛 tk.TclError
        app._safe_after(50, lambda: None)

    def test_finish_process_clears_progress_state(self):
        root, app = self._build_app(1286, 668)
        try:
            app._processing = True
            app._process_progress = (1, 2, "A.MPF")
            with patch("ncodeprocess.gui.messagebox.showinfo"):
                app.finish_process(ProcessReport("in", "out", "start"))
            self.assertFalse(app._processing)
            self.assertIsNone(app._process_progress)
        finally:
            root.destroy()

    def test_apply_buttons_disabled_while_scan_running(self):
        # WP-C4：扫描期间禁用全部应用/应用所选，并拦截直接调用。
        root, app = self._build_app(1286, 668)
        try:
            app.scan()
            self.assertTrue(app._scan_running)
            self.assertEqual(str(app.apply_all_button.cget("state")), "disabled")
            self.assertEqual(str(app.apply_selected_button.cget("state")), "disabled")
            with patch("ncodeprocess.gui.messagebox.showinfo") as showinfo:
                app.apply_info()
                app.apply_selected()
            self.assertEqual(showinfo.call_count, 2)
            # 扫描结束恢复按钮。
            app._scan_running = False
            app.apply_all_button.configure(state="normal")
            app.apply_selected_button.configure(state="normal")
            self.assertEqual(str(app.apply_all_button.cget("state")), "normal")
        finally:
            root.destroy()

    def test_apply_info_does_not_rescan_whole_directory(self):
        # WP-P3：全部应用改为内存局部重处理并立即刷新预览，不再依赖整目录重扫。
        root, app = self._build_app(1286, 668)
        try:
            plan = FilePlan("P.MPF", "mpf", "P", "P.MPF", "keep")
            plan.original_text = 'MSG("PROGRAM:P")\nMSG("DRAWING NUMBER:OLD")\nN1G1X10F500\nN2M30\n'
            app.scan_result = ScanResult("tmp", [plan])
            app.info_vars["drawing"].set("NEW_D")
            app.info_vars["version"].set("V1")
            app.overwrite_fields.set(True)
            with patch("ncodeprocess.gui.threading.Thread", _sync_thread(threading.Thread)), \
                 patch.object(App, "scan") as scan_mock:
                app.apply_info()
                root.update()   # 同步桩下 after(0) 回调在此刷新预览
            # 内存重处理立即生效（预览刷新），不依赖整目录重扫。
            self.assertIn('MSG("DRAWING NUMBER:NEW_D")', plan.output_text or "")
            self.assertLessEqual(scan_mock.call_count, 1)
        finally:
            root.destroy()

    def test_overwrite_help_describes_preview_then_execute_flow(self):
        """覆盖选项说明必须与当前逻辑一致：应用只预览，写入统一由确认并执行处理完成。"""
        root, app = self._build_app(1286, 668)
        try:
            with patch("ncodeprocess.gui.messagebox.showinfo") as showinfo:
                app._show_overwrite_help()
            title, message = showinfo.call_args.args[0], showinfo.call_args.args[1]
            self.assertEqual(title, "覆盖已有非空 MSG 字段")
            self.assertIn("受保护字段保持不变", message)
            self.assertNotIn("将更改直接写入所选文件", message)  # 旧口径：应用直接写文件
            self.assertIn("确认并执行处理", message)             # 写入统一由确认并执行处理完成
            self.assertIn("不会直接修改文件", message)
        finally:
            root.destroy()

    def test_all_stats_window_uses_screen_fitted_geometry(self):
        root, app = self._build_app(1286, 668)
        try:
            app.scan_result = ScanResult("tmp", [])
            with patch.object(tk.Toplevel, "winfo_screenwidth", return_value=1366), \
                 patch.object(tk.Toplevel, "winfo_screenheight", return_value=768):
                app.show_all_program_stats()
            app.all_stats_window.update_idletasks()
            geometry = app.all_stats_window.geometry()
            width = int(geometry.split("x")[0])
            self.assertLessEqual(width, 1366)
            self.assertGreaterEqual(width, 1050)
            self.assertIn("+", geometry)
            x = int(geometry.split("+")[1])
            y = int(geometry.split("+")[2])
            self.assertGreaterEqual(x, 0)
            self.assertGreaterEqual(y, 0)
        finally:
            root.destroy()

    def test_all_stats_window_shows_ranges_and_trajectory(self):
        # WP-A2：全部程序信息窗口保留 F/S/X/Y/Z 独立次数与极值列，并展示 GOTO/圆弧/抬刀列。
        root, app = self._build_app(1286, 668)
        try:
            plan = FilePlan("P.MPF", "mpf", "P", "P.MPF", "keep")
            plan.original_text = 'MSG("PROGRAM:P")\nN1G1X1F100\nN2M30\n'
            plan.stats = calculate_stats(plan.original_text)
            plan.apt_toolpath = ToolpathStats(goto_count=5, min_x=0.0, max_x=10.0,
                                              min_y=1.0, max_y=2.0, min_z=-2.0, max_z=100.0,
                                              arc_count=1, retract_count=2, retract_plane=100.0)
            app.scan_result = ScanResult("tmp", [plan])
            app.show_all_program_stats()
            table = app.all_stats_window.winfo_children()[0]
            values = table.item(table.get_children()[0], "values")
            self.assertEqual(values[1], "1")    # F 次数
            self.assertEqual(values[2], "100.000")  # F 最小
            self.assertEqual(values[3], "100.000")  # F 最大
            self.assertEqual(values[17], "5")   # GOTO 点数
            self.assertEqual(values[18], "1")   # 圆弧数
            self.assertEqual(values[19], "2")   # 抬刀次数
        finally:
            root.destroy()

    def test_apt_trace_section_shows_and_override(self):
        # WP-A2：参数统计页 APT 轨迹区展示轨迹值；抬刀高度可手动修订并重算次数。
        root, app = self._build_app(1286, 668)
        try:
            plan = FilePlan("P.MPF", "mpf", "P", "P.MPF", "keep")
            plan.original_text = 'MSG("PROGRAM:P")\nN1G1X1F100\nN2M30\n'
            apt_path = Path(tempfile.mkdtemp(prefix="apt-trace-")) / "P.aptsource"
            apt_path.write_text("GOTO / 0,0,100\nGOTO / 0,0,100\nGOTO / 0,0,0\n", encoding="utf-8")
            plan.apt_toolpath = ToolpathStats(goto_count=3, min_x=0.0, max_x=0.0,
                                              min_y=0.0, max_y=0.0, min_z=0.0, max_z=100.0,
                                              arc_count=0, retract_count=1, retract_plane=100.0)
            plan.apt_source_path = str(apt_path)
            app.scan_result = ScanResult("tmp", [plan])
            app.populate_file_tables()
            app.keep_table.selection_set("0")
            app.show_selected()
            self.assertIn("P.aptsource", app.apt_trace_frame.cget("text"))
            self.assertIn("X", app.apt_xyz_var.get())
            self.assertIn("~", app.apt_xyz_var.get())
            self.assertEqual(app.apt_retract_count_var.get(), "1")
            self.assertIn("100", app.apt_retract_auto_var.get())
            app.apt_retract_height_var.set("150")
            app._apply_apt_retract_height()
            self.assertEqual(app.apt_retract_heights.get("P"), 150.0)
            self.assertEqual(app.apt_retract_count_var.get(), "0")
        finally:
            root.destroy()

    def test_recognition_data_page_contains_trace_and_feed_outlier(self):
        # WP-A9 修订：APT 轨迹与 F 离群检测明细合并到「识别数据」页签（参数统计之后），
        # 不再占用参数统计/校验问题主区。
        root, app = self._build_app(1286, 668)
        try:
            tabs = [app.detail_notebook.tab(i, "text") for i in range(app.detail_notebook.index("end"))]
            self.assertEqual(tabs, ["解析信息", "校验问题", "参数统计", "识别数据", "修改差异"])
            recog_index = tabs.index("识别数据")
            recog_page = app.detail_notebook.nametowidget(app.detail_notebook.tabs()[recog_index])
            # APT 轨迹与 F 离群明细控件父级链归属识别数据页签。
            def is_inside(widget, ancestor):
                current = widget.master
                while current is not None and current is not ancestor:
                    current = getattr(current, "master", None)
                return current is ancestor

            self.assertTrue(is_inside(app.apt_trace_frame, recog_page))
            self.assertTrue(is_inside(app.feed_outlier_table, recog_page))
            # 抬刀高度输入框旁应有匹配的确认按钮。
            confirm_buttons = [
                child for child in app.apt_retract_height_entry.master.winfo_children()
                if isinstance(child, ttk.Button) and child.cget("text") == "确认"
            ]
            self.assertEqual(len(confirm_buttons), 1)
            # F 离群明细按界面式填充：APT 档位/阶段行 + 离群明细表格。
            plan = FilePlan("P.MPF", "mpf", "P", "P.MPF", "keep")
            plan.original_text = 'MSG("PROGRAM:P")\nN1G1X1F300\nN2G1X2F300\nN3G1X3F300\nN4G1X4F300\nN5G1X5F1500\nN6M30\n'
            plan.feed_outlier = FeedOutlierData(
                apt_feeds=[300.0],
                common_feeds=[300.0],
                envelope=[240.0, 360.0],
                min_count=3,
                ratio=2.0,
                outliers=[{"line": 5, "value": 1500.0,
                           "reason": "rare-above-common", "in_apt": False, "text": "N5G1X5F1500"}],
            )
            app.scan_result = ScanResult("tmp", [plan])
            app.populate_file_tables()
            app.keep_table.selection_set("0")
            app.show_selected()
            self.assertIn("APT 进给参考：300", app.feed_apt_feeds_var.get())
            self.assertIn("结构参照组：0 组", app.feed_common_var.get())
            self.assertIn("检测结论：离群 1", app.feed_envelope_var.get())
            self.assertEqual(len(app.feed_outlier_table.get_children()), 1)
        finally:
            root.destroy()

    def test_feed_outlier_view_shows_peer_evidence_and_insufficient_evidence(self):
        root, app = self._build_app(1286, 668)
        try:
            plan = FilePlan("P.MPF", "mpf", "P", "P.MPF", "keep")
            plan.original_text = 'MSG("PROGRAM:P")\nN1G1X1F300\nN2G1X2F300\nN3G1X3F300\nN4G1X4F1500\nN5G1X5F900\nN6M30\n'
            plan.feed_outlier = FeedOutlierData(
                apt_feeds=[300.0],
                common_feeds=[300.0],
                envelope=[240.0, 360.0],
                min_count=3,
                ratio=2.0,
                peer_groups={
                    "axis=xy|g=G01|z=0|retract=0|role=cut": {
                        "sample_count": 4,
                        "common_feeds": [300.0],
                        "mode_stable": True,
                    },
                    "axis=z|g=G01|z=1|retract=0|role=plunge": {
                        "sample_count": 1,
                        "common_feeds": [],
                        "mode_stable": False,
                    },
                },
                insufficient_evidence=[{
                    "line": 5,
                    "value": 900.0,
                    "peer_group": "axis=z|g=G01|z=1|retract=0|role=plunge",
                    "sample_count": 1,
                    "reason": "peer-group-too-small",
                    "in_apt": True,
                    "text": "N5G1X5F900",
                }],
                outliers=[{
                    "line": 4,
                    "value": 1500.0,
                    "reason": "episode-peer-outlier",
                    "in_apt": False,
                    "peer_group": "axis=xy|g=G01|z=0|retract=0|role=cut",
                    "sample_count": 4,
                    "confidence": "high",
                    "evidence": {
                        "reference_feed": 300.0,
                        "relative_ratio": 5.0,
                    },
                    "text": "N4G1X4F1500",
                }],
            )
            app.scan_result = ScanResult("tmp", [plan])
            app.populate_file_tables()
            app.keep_table.selection_set("0")
            app.show_selected()
            self.assertIn("结构参照组", app.feed_common_var.get())
            self.assertIn("证据不足", app.feed_envelope_var.get())
            headings = [app.feed_outlier_table.heading(col, "text") for col in app.feed_outlier_table["columns"]]
            self.assertIn("参照 F", headings)
            self.assertIn("置信度", headings)
            self.assertEqual(len(app.feed_outlier_table.get_children()), 2)
            rows = [app.feed_outlier_table.item(item, "values") for item in app.feed_outlier_table.get_children()]
            self.assertTrue(any("高" in str(row) for row in rows))
            self.assertTrue(any("证据不足" in str(row) and "在 APT 档位内" in str(row) for row in rows))
        finally:
            root.destroy()

    def test_settings_feed_help_uses_relative_peer_group_semantics(self):
        root, app = self._build_app(1286, 668)
        try:
            app.open_settings()
            rules = app.settings_pages[1]
            labels = [widget.cget("text") for widget in self._descendants(rules)
                      if widget.winfo_class() == "TLabel"]
            self.assertIn("最小参照数≥", labels)
            self.assertIn("相对倍率×", labels)
            self.assertIn("低容差×", labels)
            self.assertIn("高容差×", labels)
        finally:
            root.destroy()

    def test_settings_window_is_centered(self):
        root, app = self._build_app(1286, 668)
        try:
            app.open_settings()
            app.settings_window.update_idletasks()
            geometry = app.settings_window.geometry()
            self.assertIn("+", geometry)
            x = int(geometry.split("+")[1])
            y = int(geometry.split("+")[2])
            self.assertGreaterEqual(x, 0)
            self.assertGreaterEqual(y, 0)
        finally:
            root.destroy()

    def test_finish_scan_shows_scan_warnings(self):
        root, app = self._build_app(1286, 668)
        try:
            with patch("ncodeprocess.gui.messagebox.showwarning") as showwarning:
                app.finish_scan(ScanResult("tmp", [], warnings=["当前目录只读：处理可能失败"]))
            showwarning.assert_called_once()
        finally:
            root.destroy()

    def test_backup_request_confirmation(self):
        root, app = self._build_app(1286, 668)
        try:
            with patch("ncodeprocess.gui.messagebox.askyesno", return_value=True) as ask:
                self.assertTrue(app._backup_requested())
            ask.assert_called_once()
        finally:
            root.destroy()

    def test_finish_scan_uses_batch_program_name_confirmation(self):
        root, app = self._build_app(1286, 668)
        try:
            plans = [FilePlan(f"{index}.MPF", "mpf", None, None, "keep") for index in range(3)]
            for plan in plans:
                plan.original_text = "%\nM30\n"
            result = ScanResult("tmp", plans)
            with patch.object(app, "_confirm_program_names", return_value={"0.MPF": "P1", "1.MPF": "P2", "2.MPF": "P3"}) as confirm:
                app.finish_scan(result, app._scan_generation)
            confirm.assert_called_once()
            self.assertEqual([plan.program for plan in plans], ["P1", "P2", "P3"])
        finally:
            root.destroy()

    def test_keep_menu_has_rename_program_entry(self):
        root, app = self._build_app(1286, 668)
        try:
            labels = [app.keep_table_menu.entrycget(index, "label") for index in range(app.keep_table_menu.index("end") + 1)]
            self.assertIn("修改程序名", labels)
        finally:
            root.destroy()

    def test_rename_selected_program_updates_plan(self):
        root, app = self._build_app(1286, 668)
        try:
            plan = FilePlan("A.MPF", "mpf", "P", "P.MPF", "keep")
            plan.original_text = 'MSG("PROGRAM:P")\nN1G1X10F1000S5000M03\nM30\n'
            app.scan_result = ScanResult("tmp", [plan])
            app.populate_file_tables()
            app.keep_table.selection_set("0")
            with patch("ncodeprocess.gui.simpledialog.askstring", return_value="Q"):
                app.rename_selected_program()
            self.assertEqual(plan.program, "Q")
            self.assertIn("Q.MPF", plan.target or "")
            self.assertIn('MSG("PROGRAM:Q")', plan.original_text)
            # WP-B2：改名同步到表格显示与重处理后的头部。
            self.assertEqual(app.keep_table.item("0", "values")[1], "Q")
            self.assertIn('MSG("PROGRAM:Q")', plan.output_text or "")
        finally:
            root.destroy()

    def test_unnamed_mpfs_listed_first_in_keep_table(self):
        root, app = self._build_app(1286, 668)
        try:
            plans = [
                FilePlan("B.MPF", "mpf", None, None, "keep"),
                FilePlan("A.MPF", "mpf", "A", "A.MPF", "keep"),
                FilePlan("C.MPF", "mpf", None, None, "keep"),
            ]
            app.scan_result = ScanResult("tmp", plans)
            app.populate_file_tables()
            rows = [app.keep_table.item(item, "values")[1] for item in app.keep_table.get_children()]
            self.assertEqual(rows, ["待确认", "待确认", "A"])
        finally:
            root.destroy()

    def test_apply_selected_never_writes_file_even_with_overwrite(self):
        # 应用所选只生成预览：即使勾选覆盖也不写文件，写入统一由“确认并执行处理”完成。
        root, app = self._build_app(1286, 668)
        try:
            plan = FilePlan("A.MPF", "mpf", "A", "A.MPF", "keep")
            plan.original_text = 'MSG("PROGRAM:A")\nMSG("DRAWING NUMBER:OLD")\nN1G1X10F1000S5000M03\nM30\n'
            app.scan_result = ScanResult("tmp", [plan])
            app.populate_file_tables()
            app.keep_table.selection_set("0")
            app.info_vars["drawing"].set("NEWDRAW")
            app.info_vars["version"].set("V9")
            app.overwrite_fields.set(True)
            with patch.object(app, "scan") as scan_mock, \
                 patch.object(app, "show_selected") as show_mock:
                app.apply_selected()
            scan_mock.assert_not_called()
            show_mock.assert_called_once()
            self.assertIn("NEWDRAW", plan.output_text or "")
            self.assertIn("OLD", plan.original_text)
        finally:
            root.destroy()

    def test_apply_selected_without_overwrite_is_preview_only(self):
        # 未勾选覆盖：程序无旧图号时按默认逻辑插入，预览更新但不写文件。
        root, app = self._build_app(1286, 668)
        try:
            plan = FilePlan("A.MPF", "mpf", "A", "A.MPF", "keep")
            plan.original_text = 'MSG("PROGRAM:A")\nN1G1X10F1000S5000M03\nM30\n'
            app.scan_result = ScanResult("tmp", [plan])
            app.populate_file_tables()
            app.keep_table.selection_set("0")
            app.info_vars["drawing"].set("NEWDRAW")
            app.info_vars["version"].set("V9")
            app.overwrite_fields.set(False)
            with patch.object(app, "scan") as scan_mock, \
                 patch.object(app, "show_selected") as show_mock:
                app.apply_selected()
            scan_mock.assert_not_called()
            show_mock.assert_called_once()
            self.assertIn("NEWDRAW", plan.output_text or "")
        finally:
            root.destroy()

    def test_apply_selected_preview_shows_new_value_with_overwrite(self):
        # 程序已有旧图号，勾选覆盖后预览展示表单新值；未勾选时保留旧值。
        root, app = self._build_app(1286, 668)
        try:
            plan = FilePlan("A.MPF", "mpf", "A", "A.MPF", "keep")
            plan.original_text = 'MSG("PROGRAM:A")\nMSG("DRAWING NUMBER:OLD")\nN1G1X10F1000S5000M03\nM30\n'
            app.scan_result = ScanResult("tmp", [plan])
            app.populate_file_tables()
            app.keep_table.selection_set("0")
            app.info_vars["drawing"].set("NEWDRAW")
            app.info_vars["version"].set("V9")
            app.overwrite_fields.set(True)
            with patch.object(app, "scan"):
                app.apply_selected()
            values = [app.info_table.item(item, "values") for item in app.info_table.get_children()]
            self.assertTrue(
                any(str(value[0]) == "处理后/DRAWING NUMBER" and str(value[1]) == "NEWDRAW" for value in values),
                f"勾选覆盖后预览未显示新图号: {values}",
            )
        finally:
            root.destroy()

    def test_apply_info_records_applied_header_values(self):
        root, app = self._build_app(1286, 668)
        try:
            plan = FilePlan("A.MPF", "mpf", "A", "A.MPF", "keep")
            plan.original_text = 'MSG("PROGRAM:A")\nN1G1X10F1000S5000M03\nM30\n'
            app.scan_result = ScanResult("tmp", [plan])
            app.info_vars["drawing"].set("NEWDRAW")
            app.info_vars["version"].set("V9")
            with patch("ncodeprocess.gui.threading.Thread", _sync_thread(threading.Thread)), \
                 patch.object(app, "scan"):
                app.apply_info()
                root.update()   # 同步桩下 after(0) 回调在此刷新预览
            self.assertEqual(app.program_header_values.get("A", {}).get("drawing"), "NEWDRAW")
        finally:
            root.destroy()

    def test_show_selected_keeps_applied_header_values(self):
        # 程序已有旧图号但已应用过新图号：切换选择后顶部仍显示应用后的值，不被文件旧值刷回。
        root, app = self._build_app(1286, 668)
        try:
            plan = FilePlan("A.MPF", "mpf", "A", "A.MPF", "keep")
            plan.original_text = 'MSG("PROGRAM:A")\nMSG("DRAWING NUMBER:OLD")\nN1G1X10F1000S5000M03\nM30\n'
            app.scan_result = ScanResult("tmp", [plan])
            app.program_header_values["A"] = {"bianzhi": "", "shenhe": "", "drawing": "NEWDRAW", "version": "V9", "date": ""}
            app.populate_file_tables()
            app.keep_table.selection_set("0")
            app.show_selected()
            self.assertEqual(app.info_vars["drawing"].get(), "NEWDRAW")
        finally:
            root.destroy()

    def test_finish_scan_restores_selection_and_refreshes_preview(self):
        root, app = self._build_app(1286, 668)
        try:
            old_plan = FilePlan("A.MPF", "mpf", "A", "A.MPF", "keep")
            old_plan.original_text = 'MSG("PROGRAM:A")\nN1G1X10F1000S5000M03\nM30\n'
            app.scan_result = ScanResult("tmp", [old_plan])
            app.populate_file_tables()
            app.keep_table.selection_set("0")
            new_plan = FilePlan("A.MPF", "mpf", "A", "A.MPF", "keep")
            new_plan.original_text = 'MSG("PROGRAM:A")\nMSG("DRAWING NUMBER:NEW")\nN1G1X10F1000S5000M03\nM30\n'
            with patch.object(app, "show_selected") as show_mock:
                app.finish_scan(ScanResult("tmp", [new_plan]), app._scan_generation)
            show_mock.assert_called_once()
            self.assertEqual(app.keep_table.selection(), ("0",))
        finally:
            root.destroy()

    def test_apply_selected_keeps_existing_tool_rows(self):
        root, app = self._build_app(1286, 668)
        try:
            plan = FilePlan("A.MPF", "mpf", "A", "A.MPF", "keep")
            plan.original_text = 'MSG("PROGRAM:A")\nMSG("T1:DIA=10.000,TOOL_TYPE=圆鼻立铣刀")\nN1G1X10F1000S5000M03\nM30\n'
            app.scan_result = ScanResult("tmp", [plan])
            app.populate_file_tables()
            app.keep_table.selection_set("0")
            app.info_vars["drawing"].set("NEWDRAW")
            app.info_vars["version"].set("V9")
            with patch.object(app, "scan"):
                app.apply_selected()
            self.assertIn('MSG("T1:DIA=10.000,TOOL_TYPE=圆鼻立铣刀")', plan.output_text or "")
        finally:
            root.destroy()

    def test_process_button_label_is_confirm_and_execute(self):
        root, app = self._build_app(1286, 668)
        try:
            self.assertEqual(app.process_button.cget("text"), "确认并执行处理")
        finally:
            root.destroy()

    def test_process_confirmation_includes_modified_file_changes(self):
        root, app = self._build_app(1286, 668)
        try:
            plan = FilePlan("A.MPF", "mpf", "A", "A.MPF", "keep")
            plan.original_text = 'MSG("PROGRAM:A")\nN1G1X10F1000S5000M03\nM30\n'
            plan.changes = ["补全 DRAWING NUMBER", "插入 BIANZHI"]
            app.scan_result = ScanResult("tmp", [plan])
            app.applied_info = ProgramInfo("A", "B", "D", "V", "", "SIE840D", "")
            captured = {}
            with patch.object(app, "confirm_processing", side_effect=lambda summary, detail: captured.update(summary=summary, detail=detail) or False):
                app.process()
            joined = "\n".join(captured.get("detail", []))
            self.assertIn("将修改的 MPF", joined)
            self.assertIn("补全 DRAWING NUMBER", joined)
            self.assertIn("A.MPF", joined)
        finally:
            root.destroy()

    def test_process_aborts_without_changes(self):
        # 没有程序信息变化、也没有清理/归档/重命名操作时，提示“无更改”并停止。
        root, app = self._build_app(1286, 668)
        try:
            plan = FilePlan("A.MPF", "mpf", "A", "A.MPF", "keep")
            plan.original_text = 'MSG("PROGRAM:A")\nN1G1X10F1000S5000M03\nM30\n'
            plan.output_text = plan.original_text
            app.scan_result = ScanResult("tmp", [plan])
            app.applied_info = ProgramInfo("A", "B", "D", "V", "", "SIE840D", "")
            with patch("ncodeprocess.gui.messagebox.showinfo") as showinfo, \
                 patch.object(app, "confirm_processing") as confirm:
                app.process()
            showinfo.assert_called_once()
            confirm.assert_not_called()
        finally:
            root.destroy()

    def test_process_proceeds_when_changes_exist(self):
        root, app = self._build_app(1286, 668)
        try:
            plan = FilePlan("A.MPF", "mpf", "A", "A.MPF", "keep")
            plan.original_text = 'MSG("PROGRAM:A")\nN1G1X10F1000S5000M03\nM30\n'
            plan.output_text = plan.original_text
            plan.changes = ["补全 DRAWING NUMBER"]
            app.scan_result = ScanResult("tmp", [plan])
            app.applied_info = ProgramInfo("A", "B", "D", "V", "", "SIE840D", "")
            with patch("ncodeprocess.gui.messagebox.showinfo") as showinfo, \
                 patch.object(app, "confirm_processing", return_value=False) as confirm:
                app.process()
            showinfo.assert_not_called()
            confirm.assert_called_once()
        finally:
            root.destroy()

    def test_parsed_info_shows_encoding(self):
        root, app = self._build_app(1286, 668)
        try:
            plan = FilePlan("A.MPF", "mpf", "P", "target", "keep")
            plan.original_text = "%\nM30\n"
            plan.output_text = plan.original_text
            plan.encoding = "gbk"
            app.scan_result = ScanResult("tmp", [plan])
            app.populate_file_tables()
            app.keep_table.selection_set("0")
            app.show_selected()
            values = [app.info_table.item(item, "values")[1] for item in app.info_table.get_children()]
            self.assertIn("gbk", values)
        finally:
            root.destroy()

    def test_encoding_combo_includes_gbk_and_gb2312(self):
        root, app = self._build_app(1286, 668)
        try:
            app.open_settings()
            combo_values = set()
            for widget in self._descendants(app.settings_window):
                if widget.winfo_class() == "TCombobox":
                    combo_values.update(widget.cget("values"))
            self.assertIn("gbk", combo_values)
            self.assertIn("gb2312", combo_values)
        finally:
            root.destroy()

    def test_compare_window_does_not_pad_shorter_program(self):
        root, app = self._build_app(1286, 668)
        try:
            left_text = "\n".join(f"N{i}G1X{i}" for i in range(1, 21))
            right_text = "\n".join(f"N{i}G1X{i}" for i in range(1, 401))
            plans = []
            for index, text in enumerate((left_text, right_text)):
                plan = FilePlan(f"{index}.MPF", "mpf", f"P{index}", f"P{index}.MPF", "keep")
                plan.original_text = text
                plans.append(plan)
            app.scan_result = ScanResult("tmp", plans)
            app.populate_file_tables()
            app.keep_table.selection_set("0", "1")
            app.compare_selected_programs()
            left_lines = int(app.program_compare_left.index("end-1c").split(".")[0])
            self.assertLessEqual(left_lines, 21)
        finally:
            root.destroy()


class RuntimeEventTests(unittest.TestCase):
    """WP-C6：GUI 事件埋点（settings_loaded / settings_saved）。"""

    def _build_app(self, width, height):
        root = tk.Tk()
        root.withdraw()
        with patch.object(App, "scan", lambda _self: None):
            app = App(root, settings_registry_key=TEST_SETTINGS_KEY)
        root.geometry(f"{width}x{height}")
        root.deiconify()
        root.update_idletasks()
        root.update()
        root.update_idletasks()
        return root, app

    def test_app_start_emits_settings_loaded_event(self):
        reset_runtime_log()
        root, app = self._build_app(1286, 668)
        try:
            events = [entry["event"] for entry in runtime_log().snapshot()]
            self.assertIn("settings_loaded", events)
        finally:
            root.destroy()

    def test_settings_confirm_emits_settings_saved_event(self):
        reset_runtime_log()
        root, app = self._build_app(1286, 668)
        try:
            app.open_settings()
            with patch.object(App, "scan"):
                app._confirm_settings()
            events = [entry["event"] for entry in runtime_log().snapshot()]
            self.assertIn("settings_saved", events)
        finally:
            clear_all(TEST_SETTINGS_KEY)
            root.destroy()

    def test_save_fields_emits_settings_saved_without_names(self):
        reset_runtime_log()
        root, app = self._build_app(1286, 668)
        try:
            with patch("ncodeprocess.preferences.save_all", return_value=("registry", "HKCU")):
                app.save_fields()
            snapshot = runtime_log().snapshot()
            entries = [entry for entry in snapshot if entry["event"] == "settings_saved"]
            self.assertTrue(entries)
            self.assertNotIn("张工", json.dumps(entries, ensure_ascii=False))
        finally:
            root.destroy()

    def test_process_passes_generator_gui_and_confirmations(self):
        root, app = self._build_app(1286, 668)
        try:
            plan = FilePlan("P.MPF", "mpf", "P", "P.MPF", "keep")
            plan.changes = ["补全头部"]
            app.scan_result = ScanResult("tmp", [plan])
            app.applied_info = ProgramInfo("A", "B", "D", "V", "M", "C", "DATE")
            called = {}
            ready = threading.Event()

            def fake_process_plan(_scan, _output, _config, **_kwargs):
                called.update(_kwargs)
                ready.set()
                return ProcessReport("in", "out", "start")

            with patch.object(App, "confirm_processing", return_value=True), \
                 patch.object(App, "_backup_requested", return_value=False), \
                 patch("ncodeprocess.gui.process_plan", side_effect=fake_process_plan), \
                 patch("ncodeprocess.gui.messagebox"):
                app.process()
            self.assertTrue(ready.wait(2))
            self.assertEqual(called.get("generator"), "gui")
            self.assertTrue(any(str(c).startswith("已确认：执行目录处理") for c in called.get("confirmations", [])))
        finally:
            root.destroy()

    def test_confirm_processing_shows_auto_tool_change_skipped_section(self):
        # WP-A2：最终执行确认页明确列出因多刀无法自动添加换刀指令的程序。
        root, app = self._build_app(1286, 668)
        try:
            plan = FilePlan("P.MPF", "mpf", "P", "P.MPF", "keep")
            plan.changes = ["补全头部"]
            plan.auto_tool_change_skipped = "程序引用多把刀具，不具备自动添加换刀指令条件，已跳过生成，请人工确认换刀流程"
            app.scan_result = ScanResult("tmp", [plan])
            app.applied_info = ProgramInfo("A", "B", "D", "V", "M", "C", "DATE")
            with patch.object(App, "confirm_processing", return_value=False) as confirm:
                app.process()
            _summary, detail_lines = confirm.call_args.args
            joined = "\n".join(detail_lines)
            self.assertIn("【自动添加换刀指令已跳过】", joined)
            self.assertIn("P.MPF", joined)
            self.assertIn("多把刀具", joined)
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()
