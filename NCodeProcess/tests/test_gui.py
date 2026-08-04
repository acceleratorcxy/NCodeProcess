import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import ncodeprocess.gui as gui
from ncodeprocess.core import FilePlan, ProcessReport, ProgramInfo, ScanResult
from ncodeprocess.gui import (
    App,
    compact_diff_rows,
    merge_drawing_choices,
    needs_detailed_confirmation,
)


class DiffViewTests(unittest.TestCase):
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

    def test_fit_column_widths_shrinks_to_available_width(self):
        self._require_layout_interface("fit_column_widths")
        specs = (("fixed", 100, 80, False), ("stretch", 220, 120, True))
        result = gui.fit_column_widths(300, specs)
        self.assertEqual(sum(result.values()), 300)
        for name, initial, minimum, _stretch in specs:
            self.assertGreaterEqual(result[name], minimum)
            self.assertLessEqual(result[name], initial)

    def test_fit_column_widths_shrinks_stretch_before_fixed(self):
        self._require_layout_interface("fit_column_widths")
        specs = (("fixed", 100, 80, False), ("stretch", 220, 120, True))
        result = gui.fit_column_widths(300, specs)
        self.assertEqual(result, {"fixed": 100, "stretch": 200})

    def test_fit_column_widths_shrinks_fixed_after_stretch_reaches_minimum(self):
        self._require_layout_interface("fit_column_widths")
        specs = (("fixed", 100, 80, False), ("stretch", 220, 120, True))
        result = gui.fit_column_widths(210, specs)
        self.assertEqual(result, {"fixed": 90, "stretch": 120})

    def test_fit_column_widths_returns_minimums_at_minimum_total(self):
        self._require_layout_interface("fit_column_widths")
        specs = (("fixed", 100, 80, False), ("stretch", 220, 120, True))
        result = gui.fit_column_widths(200, specs)
        self.assertEqual(result, {"fixed": 80, "stretch": 120})

    def test_fit_column_widths_returns_minimums_below_minimum_total(self):
        self._require_layout_interface("fit_column_widths")
        specs = (("fixed", 100, 80, False), ("stretch", 220, 120, True))
        result = gui.fit_column_widths(180, specs)
        self.assertEqual(result, {"fixed": 80, "stretch": 120})

    def test_fit_column_widths_keeps_initials_for_non_positive_space(self):
        self._require_layout_interface("fit_column_widths")
        specs = (("fixed", 100, 80, False), ("stretch", 220, 120, True))
        self.assertEqual(gui.fit_column_widths(0, specs), {"fixed": 100, "stretch": 220})
        self.assertEqual(gui.fit_column_widths(-1, specs), {"fixed": 100, "stretch": 220})

    def test_fit_column_widths_assigns_extra_space_only_to_stretch_columns(self):
        self._require_layout_interface("fit_column_widths")
        specs = (("fixed", 100, 80, False), ("stretch", 220, 120, True))
        result = gui.fit_column_widths(400, specs)
        self.assertEqual(result["fixed"], 100)
        self.assertEqual(result["stretch"], 300)

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

    def test_choose_ui_font_family_returns_each_available_candidate(self):
        self._require_font_layout_interface("choose_ui_font_family")
        for family in ("Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", "Tahoma"):
            with self.subTest(family=family):
                self.assertEqual(gui.choose_ui_font_family({family}), family)

    def test_choose_ui_font_family_uses_defined_priority_for_overlapping_candidates(self):
        self._require_font_layout_interface("choose_ui_font_family")
        cases = (
            ({"Tahoma", "Microsoft YaHei"}, "Microsoft YaHei"),
            ({"Microsoft YaHei", "Segoe UI"}, "Microsoft YaHei"),
            ({"Segoe UI", "Tahoma"}, "Segoe UI"),
            ({"Microsoft YaHei UI", "Microsoft YaHei"}, "Microsoft YaHei UI"),
            (
                {"Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", "Tahoma"},
                "Microsoft YaHei UI",
            ),
        )
        for available, expected in cases:
            with self.subTest(available=available):
                self.assertEqual(gui.choose_ui_font_family(available), expected)

    def test_choose_ui_font_family_has_stable_fallback(self):
        self._require_font_layout_interface("choose_ui_font_family")
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


class LayoutWidgetTests(unittest.TestCase):
    def _build_app(self, width, height):
        root = tk.Tk()
        root.withdraw()
        with patch.object(App, "scan", lambda _self: None):
            app = App(root)
        root.geometry(f"{width}x{height}")
        root.deiconify()
        root.update_idletasks()
        root.update()
        root.update_idletasks()
        return root, app

    @staticmethod
    def _column_total(table, columns):
        return sum(int(table.column(column, "width")) for column in columns)

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
            self.assertEqual(app.keep_table_menu.entrycget(1, "state"), "disabled")
            app.compare_selected_programs()
            self.assertIsNone(app.program_compare_window)

            # Exactly two selected: compare enabled and opens the window.
            app.keep_table.selection_set(("0", "1"))
            app._refresh_keep_menu_states()
            self.assertEqual(app.keep_table_menu.entrycget(1, "state"), "normal")
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
        # Each comparison pane numbers its own program's lines: an added line
        # on one side leaves the other side's gutter blank at that row.
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
            self.assertEqual(app.program_compare_left_gutter.get("1.0", "end-1c"), "1\n\n2")
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
                root.update()
                self.assertTrue(app.cell_tooltip.window.winfo_viewable())
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
                root.update()
                self.assertFalse(app.cell_tooltip.window.winfo_viewable())
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
                root.update()
                self.assertTrue(app.cell_tooltip.window.winfo_viewable())
                app.keep_table.event_generate("<Leave>", when="tail")
                root.update()
                self.assertFalse(app.cell_tooltip.window.winfo_viewable())
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

            def x_relative_to_root(widget):
                x = 0
                current = widget
                while current is not root:
                    x += current.winfo_x()
                    current = current.master
                return x

            controls = (
                app.process_info_frame,
                app.folder_choice_combo,
                app.drawing_choice_button,
            )
            for control in controls:
                self.assertTrue(control.winfo_ismapped())
                self.assertGreater(control.winfo_width(), 0)
                self.assertLessEqual(
                    x_relative_to_root(control) + control.winfo_width(),
                    root.winfo_width(),
                )

            self.assertLessEqual(
                app.drawing_choice_button.winfo_reqwidth(),
                app.drawing_choice_button.winfo_width(),
            )

            def collect_buttons(widget):
                buttons = []
                for child in widget.winfo_children():
                    if child.winfo_class() == "TButton":
                        buttons.append(child)
                    buttons.extend(collect_buttons(child))
                return buttons

            for button in collect_buttons(app.process_info_frame):
                self.assertLessEqual(button.winfo_reqwidth(), button.winfo_width())
        finally:
            root.destroy()

    def test_custom_tool_controls_stay_adjacent_and_visible_at_1286_width(self):
        root, app = self._build_app(1286, 668)
        try:
            def coordinate_relative_to_root(widget, axis):
                coordinate = 0
                current = widget
                while current is not root:
                    coordinate += getattr(current, f"winfo_{axis}")()
                    current = current.master
                return coordinate

            entry = app.custom_tool_type_entry
            button = app.add_tool_type_button
            for control in (entry, button):
                self.assertTrue(control.winfo_ismapped())
                self.assertGreaterEqual(control.winfo_width(), control.winfo_reqwidth())
                self.assertGreaterEqual(control.winfo_height(), control.winfo_reqheight())

            entry_right = coordinate_relative_to_root(entry, "x") + entry.winfo_width()
            button_left = coordinate_relative_to_root(button, "x")
            self.assertGreaterEqual(button_left - entry_right, 0)
            self.assertLessEqual(button_left - entry_right, 8)

            button_right = button_left + button.winfo_width()
            frame_right = (
                coordinate_relative_to_root(app.process_info_frame, "x")
                + app.process_info_frame.winfo_width()
            )
            self.assertLessEqual(button_right, frame_right)
            self.assertLessEqual(button_right, root.winfo_width())

            def descendants(widget):
                for child in widget.winfo_children():
                    yield child
                    yield from descendants(child)

            g00_combo = next(
                child
                for child in descendants(app.process_info_frame)
                if child.winfo_class() == "TCombobox"
                and str(child.cget("textvariable")) == str(app.g00_level)
            )
            self.assertTrue(g00_combo.winfo_ismapped())
            g00_y = coordinate_relative_to_root(g00_combo, "y")
            entry_y = coordinate_relative_to_root(entry, "y")
            self.assertLessEqual(abs(g00_y - entry_y), 2)
            self.assertLess(g00_y, entry_y + entry.winfo_height())
            self.assertLess(entry_y, g00_y + g00_combo.winfo_height())
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


class SettingsDialogTests(LayoutWidgetTests):
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

    def test_settings_dialog_opens_and_confirm_applies(self):
        root, app = self._build_app(1286, 668)
        try:
            with patch.object(App, "scan") as scan_mock:
                app.open_settings()
                self.assertIsNotNone(app.settings_window)
                app.encoding_var.set("gb18030")
                app.delete_extensions_var.set(".log")
                app.require_m06_var.set(True)
                app.require_end_marker_var.set(False)
                app._confirm_settings()
                self.assertIsNone(app.settings_window)
                config = app.config()
                self.assertEqual(config.encoding, "gb18030")
                self.assertEqual(config.delete_extensions, {".log"})
                self.assertTrue(config.require_m06)
                self.assertFalse(config.require_end_marker)
                scan_mock.assert_called_once_with()
        finally:
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

    def test_config_injects_all_new_settings(self):
        root, app = self._build_app(1286, 668)
        try:
            app.encoding_var.set("gb18030")
            app.delete_extensions_var.set(".log")
            app.allowed_name_pattern_var.set(r"^[A-Za-z0-9]+$")
            app.aptsource_dir_var.set("archive")
            app.require_end_marker_var.set(False)
            app.require_m06_var.set(True)
            app.require_spindle_speed_var.set(True)
            config = app.config()
            self.assertEqual(config.encoding, "gb18030")
            self.assertEqual(config.delete_extensions, {".log"})
            self.assertEqual(config.allowed_name_pattern, r"^[A-Za-z0-9]+$")
            self.assertEqual(config.aptsource_dir, "archive")
            self.assertFalse(config.require_end_marker)
            self.assertTrue(config.require_m06)
            self.assertTrue(config.require_spindle_speed)
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

    def test_settings_dialog_fits_1286_and_controls_visible(self):
        root, app = self._build_app(1286, 668)
        try:
            app.open_settings()
            win = app.settings_window
            win.update_idletasks()
            self.assertLessEqual(win.winfo_reqwidth(), 640)
            self.assertLessEqual(win.winfo_reqheight(), 420)

            def collect_buttons(widget):
                buttons = []
                for child in widget.winfo_children():
                    if child.winfo_class() == "TButton":
                        buttons.append(child)
                    buttons.extend(collect_buttons(child))
                return buttons

            texts = {button.cget("text") for button in collect_buttons(win)}
            self.assertIn("确定", texts)
            self.assertIn("取消", texts)
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


if __name__ == "__main__":
    unittest.main()
