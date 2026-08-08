import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ncodeprocessreportviewer.viewer import (
    apt_meta_rows,
    apt_summary_rows,
    chart_number,
    discover_reports,
    file_issue_counts,
    issues_csv_rows,
    iter_stats_rows,
    load_report,
    log_event_detail,
    report_summary,
    runtime_log_events,
    window_geometry_for_screen,
)


class ReportViewerTests(unittest.TestCase):
    def make_dir(self):
        return Path(tempfile.mkdtemp(prefix="nc-report-viewer-"))

    def sample_report(self):
        return {
            "input_dir": "input",
            "output_dir": "output",
            "started_at": "2026-08-03T10:00:00",
            "finished_at": "2026-08-03T10:00:01",
            "success": 1,
            "failed": 0,
            "skipped": 0,
            "moved": 0,
            "deleted": 2,
            "warnings": 1,
            "errors": 0,
            "files": [{
                "file": "P.MPF",
                "program": "P",
                "action": "keep",
                "issues": [{"severity": "warning", "line": 8, "kind": "feed", "text": "F10", "suggestion": "检查 F 值"}],
                "stats": {
                    "counts": {"F": 3, "S": 1, "X": 2},
                    "minimum": {"F": 10.0, "S": 2000.0, "X": -1.5},
                    "maximum": {"F": 3000.0, "S": 2000.0, "X": 4.0},
                    "g00_count": 1,
                },
                "changes": ["补全程序头"],
                "diff": ["--- before", "+++ after", "@@ -1 +1 @@", "-OLD", "+NEW"],
            }],
        }

    def test_discovers_reports_in_current_and_data_directories(self):
        root = self.make_dir()
        data_dir = root / "NCodeProcessData"
        data_dir.mkdir()
        legacy_data_dir = root / "NCPostProcessData"
        legacy_data_dir.mkdir()
        first = root / "ncodeprocess-report-20260803_100000.json"
        second = data_dir / "ncodeprocess-report-20260803_100001.json"
        legacy = legacy_data_dir / "ncpostprocess-report-20260803_090000.json"
        first.write_text("{}", encoding="utf-8")
        second.write_text("{}", encoding="utf-8")
        legacy.write_text("{}", encoding="utf-8")
        (root / "unrelated.json").write_text("{}", encoding="utf-8")
        self.assertEqual(set(discover_reports(root)), {first.resolve(), second.resolve(), legacy.resolve()})

    def test_loads_and_validates_report(self):
        root = self.make_dir()
        path = root / "report.json"
        path.write_text(json.dumps(self.sample_report(), ensure_ascii=False), encoding="utf-8")
        loaded = load_report(path)
        self.assertEqual(loaded["files"][0]["program"], "P")
        path.write_text("[]", encoding="utf-8")
        with self.assertRaises(ValueError):
            load_report(path)

    def test_summary_issue_and_parameter_rows(self):
        report = self.sample_report()
        self.assertIn(("成功", "1"), report_summary(report))
        self.assertEqual(file_issue_counts(report["files"][0]), (0, 1, 1))
        rows = list(iter_stats_rows(report))
        self.assertIn(("P.MPF", "F", "3", "10", "3000", "否"), rows)
        self.assertIn(("P.MPF", "G00", "1", "", "", "发现"), rows)

    def test_runtime_log_rows_filter_and_tolerate_missing_or_bad_entries(self):
        self.assertEqual(list(runtime_log_events(self.sample_report())), [])
        data = self.sample_report()
        data["runtime_log"] = [
            {"time": "2026-08-05T09:30:01", "level": "info", "event": "scan_start", "message": "开始扫描目录"},
            "not-a-dict",
            {"time": "2026-08-05T09:30:02", "level": "error", "event": "error", "message": "读取文件失败"},
        ]
        events = list(runtime_log_events(data))
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["event"], "scan_start")
        self.assertEqual(events[1]["level"], "error")
        filtered = list(runtime_log_events(data, event_filter="error"))
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["message"], "读取文件失败")
        filtered = list(runtime_log_events(data, event_filter="missing"))
        self.assertEqual(filtered, [])

    def test_log_event_detail_combines_message_and_multiline_detail(self):
        # WP-F2 配套：详情展示文本 = 消息 + detail（含多行 traceback 与关键数据）。
        self.assertEqual(log_event_detail({"message": "处理文件：P.MPF", "detail": ""}), "处理文件：P.MPF")
        self.assertEqual(log_event_detail({"message": "", "detail": "Traceback: boom"}), "Traceback: boom")
        combined = log_event_detail({
            "message": "处理文件失败：A.MPF",
            "detail": "动作=keep\nTraceback (most recent call last):\n  boom",
        })
        self.assertIn("处理文件失败：A.MPF", combined)
        self.assertIn("动作=keep", combined)
        self.assertIn("Traceback (most recent call last)", combined)

    def test_chart_number_fallback(self):
        # WP-F3：柱状图数值容错——非数值/缺失回退 0，防止异常报告数据导致崩溃。
        self.assertEqual(chart_number(5), 5.0)
        self.assertEqual(chart_number("3"), 3.0)
        self.assertEqual(chart_number("abc"), 0)
        self.assertEqual(chart_number(None), 0)

    def test_apt_meta_rows_expand_fields(self):
        # WP-A5：文件项 apt_meta/toolpath_stats 展开为键值行（含操作级工艺）。
        item = {"apt_meta": {
            "machine": "3-axis Machine.1",
            "operations": ["Roughing.3"],
            "spindles": [["5000.0000", "RPM", "CLW"]],
            "feeds": [["3000.0000", "MMPM"]],
            "coolant": ["ON"],
            "tools": [
                {"number": 1, "dia": "20.000", "tool_coner": "3.000", "tool_type": "平底立铣刀", "tool_angle": ""},
                {"number": 2, "dia": "20.000", "tool_coner": "3.000", "tool_type": "平底立铣刀", "tool_angle": ""},
            ],
            "operation_feeds": {"Roughing.3": [["3000.0000", "MMPM"]]},
        }, "toolpath_stats": {"goto_count": 2, "arc_count": 1, "retract_count": 1,
                              "retract_plane": 100.0, "min_x": 0.0, "max_x": 10.0,
                              "min_y": 0.0, "max_y": 5.0, "min_z": -2.0, "max_z": 100.0}}
        rows = dict(apt_meta_rows(item))
        self.assertEqual(rows["机床型号"], "3-axis Machine.1")
        self.assertIn("Roughing.3", rows["操作清单"])
        self.assertEqual(rows["主轴规划"], "5000.0000RPM CLW")
        self.assertEqual(rows["刀具 T1、T2"], "Ø20.000，R3.000，平底立铣刀")
        self.assertEqual(rows["GOTO 点数"], "2")
        self.assertEqual(rows["Z 行程"], "-2.000 ~ 100.000")
        self.assertIn("操作进给", "".join(rows))
        self.assertEqual(apt_meta_rows({}), [])

    def test_apt_summary_rows_aggregate(self):
        # WP-A5：报告级 apt_summary 与跨文件轨迹汇总展开。
        data = {
            "apt_summary": {
                "machines": ["3-axis Machine.1"],
                "spindle_speeds": [1000.0, 5000.0],
                "tool_loads": [1, 2],
                "operations": ["Roughing.3"],
                "tool_usage": {"1": 1, "2": 2},
            },
            "files": [
                {"program": "P1", "apt_meta": {"operations": ["Roughing.3"], "tools": [
                    {"number": 1, "dia": "20.000", "tool_coner": "3.000", "tool_type": "平底立铣刀", "tool_angle": ""}]}},
                {"program": "P2", "apt_meta": {"operations": ["Roughing.3", "Finishing.1"], "tools": [
                    {"number": 1, "dia": "20.000", "tool_coner": "3.000", "tool_type": "平底立铣刀", "tool_angle": ""},
                    {"number": 2, "dia": "10.000", "tool_coner": "0.000", "tool_type": "球头立铣刀", "tool_angle": ""}]}},
                {"toolpath_stats": {"goto_count": 10, "arc_count": 1, "retract_count": 2}},
                {"toolpath_stats": {"goto_count": 5, "arc_count": 2, "retract_count": 1}},
            ],
        }
        rows = dict(apt_summary_rows(data))
        self.assertEqual(rows["机床"], "3-axis Machine.1")
        self.assertEqual(rows["刀具 Ø20.000，R3.000，平底立铣刀"], "T1×2")
        self.assertEqual(rows["刀具 Ø10.000，R0.000，球头立铣刀"], "T2×1")
        self.assertEqual(rows["操作 · P1"], "Roughing.3")
        self.assertEqual(rows["操作 · P2"], "Finishing.1、Roughing.3")
        self.assertEqual(rows["轨迹汇总"], "GOTO 15 点 / 圆弧 3 / 抬刀 3 次")

    def test_issues_csv_rows_expands_all_files(self):
        # WP-15：问题 CSV 行 = 表头 + 全部文件 issues 逐条展开。
        data = {"files": [
            {"file": "P.MPF", "issues": [{"line": 5, "severity": "error", "kind": "G00", "text": "G0", "suggestion": "移除"}]},
            {"file": "Q.MPF", "issues": [{"line": 8, "severity": "warning", "kind": "feed-outlier", "text": "F20", "suggestion": "确认"}]},
        ]}
        rows = issues_csv_rows(data)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0], ("文件", "行号", "级别", "类型", "原始文本", "建议"))
        self.assertEqual(rows[1][0], "P.MPF")
        self.assertEqual(rows[1][2], "error")
        self.assertEqual(rows[2][4], "F20")


class LayoutMetricTests(unittest.TestCase):
    def test_supported_screen_geometry(self):
        self.assertEqual(window_geometry_for_screen(1366, 768), (1250, 680, 1250, 680))
        self.assertEqual(window_geometry_for_screen(1920, 1080), (1500, 800, 1250, 680))

    def test_smaller_screen_does_not_request_more_than_screen(self):
        width, height, min_width, min_height = window_geometry_for_screen(1024, 600)
        self.assertLessEqual(width, 1024)
        self.assertLessEqual(height, 600)
        self.assertEqual(min_width, width)
        self.assertEqual(min_height, height)


class ReportViewerLayoutTests(unittest.TestCase):
    def _build_viewer(self, width, height):
        import tkinter as tk

        from ncodeprocessreportviewer.viewer import ReportViewer

        root = tk.Tk()
        root.withdraw()
        app = ReportViewer(root)
        root.geometry(f"{width}x{height}")
        root.deiconify()
        root.update_idletasks()
        root.update()
        root.update_idletasks()
        return root, app

    def test_default_window_shows_all_table_headers_without_horizontal_scroll(self):
        # 1920x1080 下默认窗口约 1290x720，所有表头应直接可见、无需拖动。
        root, app = self._build_viewer(1290, 720)
        try:
            self.assertEqual(app.notebook.index(app.notebook.select()), 0)  # 默认展示概览与可视化
            self.assertGreaterEqual(float(app.report_table.xview()[1]), 0.999, "报告列表")
            self.assertGreaterEqual(float(app.program_table.xview()[1]), 0.999, "程序列表")
            # 文件明细/APT 信息/运行日志表故意保留横向滚动条（长内容可滚动查看），不要求初始无溢出。
            for index, table, label in (
                (3, app.stats_table, "参数统计"),
                (4, app.issue_table, "校验问题"),
            ):
                app.notebook.select(index)
                root.update_idletasks()
                self.assertGreaterEqual(float(table.xview()[1]), 0.999, label)
        finally:
            root.destroy()

    def test_log_page_shows_events_and_log_path_hint(self):
        root, app = self._build_viewer(1290, 720)
        try:
            app.report_data = {
                "runtime_log": [
                    {"time": "2026-08-05T09:30:01", "level": "info", "event": "scan_start", "message": "开始扫描目录", "detail": ""},
                    {"time": "2026-08-05T09:30:02", "level": "error", "event": "error", "message": "读取文件失败", "detail": "Traceback: OSError"},
                ],
                "log_path": "C:\\nonexistent\\logs\\ncodeprocess-20260805.log",
            }
            app.file_items = []
            app._update_views()
            self.assertEqual(len(app.log_table.get_children()), 2)
            values = app.log_table.item("1", "values")
            self.assertEqual(values[1], "error")
            self.assertEqual(values[4], "Traceback: OSError")
            # WP-R4：日志内嵌报告，不再生成磁盘日志文件。
            self.assertIn("运行日志已内嵌本报告", app.log_path_label.cget("text"))
        finally:
            root.destroy()

    def test_log_detail_preview_shows_selected_event_full_content(self):
        # WP-F2 配套：运行日志页下方详情区展示选中事件的完整消息与多行 detail。
        root, app = self._build_viewer(1290, 720)
        try:
            app.report_data = {
                "runtime_log": [
                    {"time": "2026-08-05T09:30:01", "level": "info", "event": "process_file",
                     "message": "处理文件：P.MPF（1/1）",
                     "detail": "动作=keep；程序名=P；目标=D:\\NC\\P.MPF；统计 F=1 次"},
                    {"time": "2026-08-05T09:30:02", "level": "error", "event": "error",
                     "message": "处理文件失败：A.MPF",
                     "detail": "Traceback (most recent call last):\n  boom"},
                ],
            }
            app.file_items = []
            app._update_views()
            # 自动选中首行：详情区展示完整消息 + 关键数据。
            text = app.log_detail_text.get("1.0", "end").strip()
            self.assertIn("处理文件：P.MPF（1/1）", text)
            self.assertIn("动作=keep", text)
            # 选择 error 行：多行 traceback 完整可见。
            app.log_table.selection_set("1")
            app._on_log_row_selected()
            text = app.log_detail_text.get("1.0", "end").strip()
            self.assertIn("处理文件失败：A.MPF", text)
            self.assertIn("Traceback (most recent call last)", text)
            self.assertIn("boom", text)
        finally:
            root.destroy()

    def test_overview_meta_and_file_table_show_section12_fields(self):
        root, app = self._build_viewer(1290, 720)
        try:
            app.report_data = {
                "input_dir": "D:\\NC",
                "output_dir": "D:\\NC",
                "started_at": "2026-08-05T09:30:00",
                "finished_at": "2026-08-05T09:30:05",
                "backup_dir": "D:\\NC\\backup\\20260805_093000",
                "app_version": "1.0.0",
                "generator": "cli",
                "report_schema_version": 1,
                "elapsed_seconds": 5.0,
                "archive_stamp": "20260805_093000",
                "user_confirmations": ["已确认：执行目录处理"],
                "scan_warnings": ["目录中未找到 MPF"],
                "files": [
                    {"file": "x_P.MPF", "program": "P", "program_name_source": "MSG", "status": "success", "target": "D:\\NC\\P.MPF", "issues": []},
                    {"file": "y_Q.MPF", "program": "Q", "program_name_source": "文件名", "status": "success", "target": "D:\\NC\\Q.MPF", "issues": []},
                ],
            }
            app.file_items = app.report_data["files"]
            app._populate_files()
            app._update_views()
            values = app.file_table.item("0", "values")
            self.assertEqual(values[0], "P（MSG）")
            self.assertEqual(values[3], "D:\\NC\\P.MPF")
            meta = app.meta_text.get()
            self.assertIn("工具版本：1.0.0", meta)
            self.assertIn("报告来源：cli", meta)
            self.assertIn("报告结构版本：1", meta)
            self.assertIn("处理耗时：5.0 秒", meta)
            self.assertIn("备份目录：D:\\NC\\backup\\20260805_093000", meta)
            self.assertIn("已确认：执行目录处理", meta)
            self.assertIn("目录中未找到 MPF", meta)
        finally:
            root.destroy()

    def test_program_table_filters_file_details(self):
        # 左侧程序列表含「全部程序」与各程序行（含未配对文件）；点击后右侧文件明细按程序联动过滤。
        root, app = self._build_viewer(1500, 800)
        try:
            app.report_data = {"files": [
                {"file": "x_P.MPF", "program": "P", "status": "success", "issues": []},
                {"file": "y_P.MPF", "program": "P", "status": "success", "issues": [{"severity": "warning"}]},
                {"file": "Q.MPF", "program": "Q", "status": "failed", "issues": [{"severity": "error"}]},
                {"file": "a.LOG", "program": "", "status": "deleted", "issues": []},
            ]}
            app.file_items = app.report_data["files"]
            app._populate_programs()
            labels = [app.program_table.item(i, "values")[0] for i in app.program_table.get_children()]
            self.assertEqual(labels, ["全部程序", "P", "Q", "未配对文件"])
            self.assertEqual(app.program_table.item("1", "values")[1], "0 错 / 1 警")
            # 默认「全部程序」→ 文件明细含汇总行 + 全部 4 个文件
            self.assertEqual(len(app.file_table.get_children()), 5)
            # 选择程序 P → 只显示 P 相关 2 个文件（无汇总行）
            app.program_table.selection_set("1")
            app._on_program_selected()
            rows = app.file_table.get_children()
            self.assertEqual(len(rows), 2)
            self.assertIn("P", app.file_table.item(rows[0], "values")[0])
            # 选择「未配对文件」→ 只显示无程序名文件
            app.program_table.selection_set("3")
            app._on_program_selected()
            rows = app.file_table.get_children()
            self.assertEqual(len(rows), 1)
            self.assertIn("a.LOG", app.file_table.item(rows[0], "values")[0])
        finally:
            root.destroy()

    def test_non_numeric_summary_values_do_not_crash_charts(self):
        # WP-F3：汇总字段为非数值（异常/篡改报告）时，柱状图不崩溃。
        root, app = self._build_viewer(1500, 800)
        try:
            app.report_data = {
                "success": "abc", "failed": "x", "skipped": None,
                "moved": 1, "deleted": 2, "warnings": "?", "errors": "boom",
                "files": [],
            }
            app.file_items = []
            app._update_views()   # 不应抛异常
        finally:
            root.destroy()

    def test_issue_filter_filters_rows(self):
        # WP-15：校验问题页按级别筛选（全部/error/warning/info）。
        root, app = self._build_viewer(1500, 800)
        try:
            app.report_data = {"files": [
                {"file": "P.MPF", "issues": [{"severity": "error", "kind": "G00"}, {"severity": "warning", "kind": "feed-outlier"}, {"severity": "info", "kind": "apt-coolant-missing"}]},
                {"file": "Q.MPF", "issues": [{"severity": "warning", "kind": "block-number"}]},
            ]}
            app.issue_filter_var.set("error")
            app._fill_issues(None)
            self.assertEqual(len(app.issue_table.get_children()), 1)
            app.issue_filter_var.set("warning")
            app._fill_issues(None)
            self.assertEqual(len(app.issue_table.get_children()), 2)
            app.issue_filter_var.set("info")
            app._fill_issues(None)
            self.assertEqual(len(app.issue_table.get_children()), 1)
            app.issue_filter_var.set("全部")
            app._fill_issues(None)
            self.assertEqual(len(app.issue_table.get_children()), 4)
        finally:
            root.destroy()

    def test_changes_page_shows_summary_and_side_by_side_diff(self):
        # 修改与差异页：修改摘要表格 + 左右对照（删除行在左、新增行在右）。
        root, app = self._build_viewer(1500, 800)
        try:
            app.report_data = {"files": [
                {"file": "P.MPF", "changes": ["补全程序头", "重命名"],
                 "diff": ["--- before", "+++ after", "@@ -1 +1 @@", "-OLD", "+NEW", " KEEP"]},
                {"file": "Q.MPF", "changes": ["补全 M03"], "diff": ["--- b", "+++ a", "@@ -1 +1 @@", "+M03"]},
                {"file": "R.MPF", "changes": [], "diff": []},
            ]}
            app._fill_changes(None)
            # 摘要表只含有效果的文件（P/Q），R 无变化不出现。
            rows = app.change_summary_table.get_children()
            self.assertEqual(len(rows), 2)
            self.assertEqual(app.change_summary_table.item(rows[0], "values")[0], "P.MPF")
            self.assertIn("补全程序头", app.change_summary_table.item(rows[0], "values")[1])
            # 默认展示第一个文件的左右对照。
            left = app.change_left.get("1.0", "end")
            right = app.change_right.get("1.0", "end")
            self.assertIn("OLD", left)
            self.assertIn("NEW", right)
            self.assertNotIn("NEW", left)
            self.assertNotIn("OLD", right)
            # 删除行打 removed tag、新增行打 added tag。
            self.assertEqual(len(app.change_left.tag_ranges("removed")), 2)
            self.assertEqual(len(app.change_right.tag_ranges("added")), 2)
            # 点击摘要第二行切换到 Q.MPF。
            app.change_summary_table.selection_set(rows[1])
            app._on_change_summary_selected()
            self.assertIn("M03", app.change_right.get("1.0", "end"))
        finally:
            root.destroy()

    def test_report_load_shows_loading_state(self):
        # WP-14：报告加载期间状态栏先显示「正在加载报告」。
        root, app = self._build_viewer(1500, 800)
        try:
            captured = {}

            def fake_load(_path):
                captured["label_during_load"] = app.report_label.get()
                return {"files": [{"file": f"P{i}.MPF", "program": f"P{i}", "issues": []} for i in range(10)]}

            report_path = Path(tempfile.mkdtemp(prefix="nc-report-load-")) / "ncodeprocess-report-20260806_120000.json"
            report_path.write_text("{}", encoding="utf-8")
            with patch("ncodeprocessreportviewer.viewer.load_report", side_effect=fake_load):
                app._load_report(report_path)
            self.assertIn("正在加载报告", captured["label_during_load"])
            self.assertIn("当前报告", app.report_label.get())
        finally:
            root.destroy()

    def test_apt_page_shows_summary_and_file_rows(self):
        # WP-A5：「APT 信息」页签展示全局摘要（全部文件）与单文件 apt_meta/toolpath_stats。
        root, app = self._build_viewer(1500, 800)
        try:
            app.report_data = {
                "apt_summary": {"machines": ["3-axis Machine.1"]},
                "files": [
                    {"file": "P.MPF", "program": "P",
                     "apt_meta": {"machine": "3-axis Machine.1", "tools": [
                         {"number": 1, "dia": "20.000", "tool_coner": "3.000", "tool_type": "平底立铣刀", "tool_angle": ""}]},
                     "toolpath_stats": {"goto_count": 2, "arc_count": 1, "retract_count": 1,
                                        "retract_plane": 100.0, "min_x": 0.0, "max_x": 1.0,
                                        "min_y": 0.0, "max_y": 1.0, "min_z": 0.0, "max_z": 100.0}},
                    {"file": "Q.MPF", "program": "Q"},
                ],
            }
            app.file_items = app.report_data["files"]
            app._populate_files()
            rows = dict((app.apt_table.item(i, "values")[0], app.apt_table.item(i, "values")[1]) for i in app.apt_table.get_children())
            self.assertIn("机床", rows)
            self.assertIn("刀具 Ø20.000，R3.000，平底立铣刀", rows)
            app.file_table.selection_set("0")
            app._on_file_selected()
            rows = dict((app.apt_table.item(i, "values")[0], app.apt_table.item(i, "values")[1]) for i in app.apt_table.get_children())
            self.assertIn("机床型号", rows)
            self.assertEqual(rows["GOTO 点数"], "2")
        finally:
            root.destroy()

    def test_issues_page_shows_feed_outlier_detail(self):
        # F 抬刀平面分段对比明细：显示段统计、离群/复核、边界错误与分布表。
        root, app = self._build_viewer(1500, 800)
        try:
            app.report_data = {"files": [
                {"file": "P.MPF", "feed_outlier": {
                    "safe_plane": 100.0,
                    "tolerance": 0.3,
                    "segments": [
                        {"index": 1, "first_line": 1, "last_line": 5,
                         "feed_counts": {"300": 1, "1800": 3, "6000": 1},
                         "feeds": [300.0, 1800.0, 6000.0]},
                        {"index": 2, "first_line": 6, "last_line": 9,
                         "feed_counts": {"300": 1, "1500": 1, "8888": 1, "6000": 1},
                         "feeds": [300.0, 1500.0, 8888.0, 6000.0]},
                    ],
                    "outliers": [
                        {"line": 7, "value": 1500.0, "raw_value": "1500",
                         "text": "N7G1X70F1500", "count": 1, "level": "warning",
                         "reason": "segment-gap", "gap": 0.7, "axial_only": False,
                         "in_apt": False, "segment_index": 2,
                         "other_segment_feeds": [300.0, 1800.0, 6000.0]},
                        {"line": 8, "value": 8888.0, "raw_value": "8888",
                         "text": "N8G1X80F8888", "count": 1, "level": "review",
                         "reason": "segment-gap", "gap": 0.325, "axial_only": False,
                         "in_apt": True, "segment_index": 2,
                         "other_segment_feeds": [300.0, 1800.0, 6000.0]},
                    ],
                    "boundary_errors": [
                        {"line": 9, "value": 20000.0, "reason": "out-of-range",
                         "in_apt": False, "text": "N9G1X90F20000"},
                    ],
                    "distribution": [],
                    "apt_feeds": [300.0],
                }},
                {"file": "Q.MPF", "feed_outlier": {
                    "safe_plane": 100.0, "tolerance": 0.3, "segments": [
                        {"index": 1, "first_line": 1, "last_line": 4,
                         "feed_counts": {"300": 1, "1800": 2, "6000": 1},
                         "feeds": [300.0, 1800.0, 6000.0]},
                    ], "outliers": [], "boundary_errors": [],
                    "distribution": [
                        {"value": 300.0, "count": 1, "first_line": 2,
                         "note": "仅出现一次，请人工确认"},
                        {"value": 1800.0, "count": 2, "first_line": 3, "note": ""},
                    ], "apt_feeds": []},
                },
            ]}
            app.file_items = app.report_data["files"]
            app._populate_files()
            app.file_table.selection_set("0")
            app._on_file_selected()
            content = app.feed_outlier_text.get("1.0", "end")
            self.assertIn("APT 进给参考：300", content)
            self.assertIn("抬刀平面 100，2 段，容差 30%", content)
            self.assertIn("警告 1，复核 1，边界错误 1", content)
            self.assertIn("第 7 行 F1500（离群告警", content)
            self.assertIn("最小差距 70.0%，不在 APT 档位内", content)
            self.assertIn("第 8 行 F8888（复核提示", content)
            self.assertIn("在 APT 档位内", content)
            self.assertIn("N9G1X90F20000", content)
            app.file_table.selection_set("1")
            app._on_file_selected()
            content = app.feed_outlier_text.get("1.0", "end")
            self.assertIn("单段程序无段间参照", content)
            self.assertIn("F 范围：300 ~ 1800", content)
            self.assertIn("F 分布：300 × 1 次（仅出现一次", content)
            self.assertIn("F 分布：1800 × 2 次", content)
        finally:
            root.destroy()

    def test_feed_outlier_without_apt_shows_no_apt_reference(self):
        # 无配对 APT 时，APT 状态写“无 APT 参考”，不写“不在 APT 档位内”。
        root, app = self._build_viewer(1500, 800)
        try:
            app.report_data = {"files": [
                {"file": "P.MPF", "feed_outlier": {
                    "safe_plane": 100.0, "tolerance": 0.3,
                    "segments": [{"index": 1, "first_line": 1, "last_line": 5,
                                  "feed_counts": {"300": 1, "66": 1, "6000": 1},
                                  "feeds": [66.0, 300.0, 6000.0]}],
                    "outliers": [{"line": 4, "value": 66.0, "raw_value": "66",
                                  "text": "N4G1X4Y4F66", "count": 1, "level": "warning",
                                  "reason": "segment-gap", "gap": 0.9,
                                  "axial_only": False, "in_apt": False,
                                  "segment_index": 1,
                                  "other_segment_feeds": [300.0, 6000.0]}],
                    "boundary_errors": [], "distribution": [], "apt_feeds": [],
                }},
            ]}
            app.file_items = app.report_data["files"]
            app._populate_files()
            app.file_table.selection_set("0")
            app._on_file_selected()
            content = app.feed_outlier_text.get("1.0", "end")
            self.assertIn("无 APT 参考", content)
            self.assertNotIn("不在 APT 档位内", content)
        finally:
            root.destroy()

    def test_feed_outlier_tab_has_dedicated_tables(self):
        # F 离群检测独立页签：汇总/证据/分布三张表格展示，支持“仅检出异常”筛选。
        root, app = self._build_viewer(1500, 800)
        try:
            tabs = [app.notebook.tab(i, "text") for i in range(app.notebook.index("end"))]
            self.assertIn("F 离群检测", tabs)
            app.report_data = {"files": [
                {"file": "P.MPF", "feed_outlier": {
                    "safe_plane": 100.0, "tolerance": 0.3,
                    "segments": [{"index": 1, "first_line": 1, "last_line": 4,
                                  "feed_counts": {"300": 1, "66": 1, "6000": 1},
                                  "feeds": [66.0, 300.0, 6000.0]}],
                    "outliers": [{"line": 4, "value": 66.0, "raw_value": "66",
                                  "text": "N4G1X4Y4F66", "count": 1, "level": "warning",
                                  "reason": "segment-gap", "gap": 0.9,
                                  "axial_only": False, "in_apt": False,
                                  "segment_index": 1,
                                  "other_segment_feeds": [300.0, 6000.0]}],
                    "boundary_errors": [], "distribution": [], "apt_feeds": [],
                }},
                {"file": "Q.MPF", "feed_outlier": {
                    "safe_plane": 100.0, "tolerance": 0.3,
                    "segments": [{"index": 1, "first_line": 1, "last_line": 3,
                                  "feed_counts": {"300": 1, "6000": 1},
                                  "feeds": [300.0, 6000.0]}],
                    "outliers": [], "boundary_errors": [], "distribution": [], "apt_feeds": [],
                }},
            ]}
            app.file_items = app.report_data["files"]
            app._populate_files()
            app.file_table.selection_set("all")
            app._on_file_selected()
            # 汇总表与证据表按全部文件填充。
            self.assertEqual(len(app.feed_summary_table.get_children()), 2)
            self.assertEqual(len(app.feed_evidence_table.get_children()), 1)
            values = app.feed_evidence_table.item(app.feed_evidence_table.get_children()[0], "values")
            self.assertEqual(values[0], "P.MPF")
            self.assertEqual(values[3], "离群告警")
            self.assertEqual(values[7], "无 APT 参考")
            # “仅检出异常”筛选：无证据的 Q.MPF 从汇总表移除。
            app.feed_filter_var.set("仅检出异常")
            app._fill_feed_outlier(app._selected_item())
            rows = app.feed_summary_table.get_children()
            self.assertEqual(len(rows), 1)
            self.assertEqual(app.feed_summary_table.item(rows[0], "values")[0], "P.MPF")
        finally:
            root.destroy()

    def test_cell_tooltip_truncation_detection(self):
        # 查看器悬停浮窗：超长单元格判定为截断（显示提示），短内容不提示。
        root, app = self._build_viewer(1290, 720)
        try:
            app.notebook.select(3)  # 参数统计页
            root.update_idletasks()
            long_value = "很长很长的文件名_" * 20
            iid = app.stats_table.insert("", "end", values=(long_value, "F", "1", "10", "20", "否"))
            root.update_idletasks()
            self.assertTrue(app._cell_truncated(app.stats_table, iid, "#1", long_value))
            self.assertFalse(app._cell_truncated(app.stats_table, iid, "#1", "短"))
        finally:
            root.destroy()

    def test_overview_shows_environment_and_config_snapshot(self):
        # 2026-08-08 报告完善：概览展示运行环境与处理配置快照。
        root, app = self._build_viewer(1290, 720)
        try:
            app.report_data = {
                "environment": {"platform": "win32", "python_version": "3.8.19", "machine": "64"},
                "config_snapshot": {
                    "encoding": "auto", "recursive": False, "save_aptsource": False,
                    "overwrite_existing": True, "delete_extensions": [".log", ".moaptindexes"],
                    "program_extensions": [".mpf"], "program_output_extension": ".MPF",
                    "aptsource_dir": "aptsource", "allowed_name_pattern": "^[A-Za-z0-9_一-鿿-]+$",
                    "g00_level": "error", "auto_m03": True, "auto_tool_change": False,
                    "m03_position": "after-s", "feed_min": 20.0, "feed_max": 10000.0,
                    "spindle_min": 500.0, "spindle_max": 12000.0, "newline": "auto",
                    "required_fields": ["BIANZHI"], "aux_checks": ["m03-before-motion"],
                    "multiple_spindle_warn": True, "require_end_marker": True,
                    "require_m06": False, "require_spindle_speed": False,
                    "max_file_size": 0, "max_files": 0, "retract_z_threshold": 20.0,
                    "ask_backup": True,
                },
                "files": [],
            }
            app.file_items = []
            app._update_views()
            meta = app.meta_text.get()
            self.assertIn("运行环境：win32 / Python 3.8.19 / 64 位", meta)
            rows = app.config_table.get_children()
            labels = {app.config_table.item(iid, "values")[0]: app.config_table.item(iid, "values")[1]
                      for iid in rows}
            self.assertEqual(labels.get("文件编码"), "auto")
            self.assertEqual(labels.get("允许覆盖目标"), "是")
            self.assertEqual(labels.get("待删除扩展名"), ".log、.moaptindexes")
            self.assertEqual(labels.get("必填 MSG 字段"), "BIANZHI")
            # 配置项列按内容缩窄不伸展，宽度留给值列（可手动拖拽调整）。
            self.assertFalse(app.config_table.column("key", "stretch"))
            self.assertTrue(app.config_table.column("value", "stretch"))
        finally:
            root.destroy()

    def test_file_table_shows_failure_reason(self):
        # 2026-08-08 报告完善：文件明细展示失败原因（error_kind + runtime_error）。
        root, app = self._build_viewer(1290, 720)
        try:
            app.report_data = {
                "files": [
                    {"file": "P.MPF", "program": "P", "status": "success", "issues": []},
                    {"file": "Q.MPF", "program": "Q", "status": "failed",
                     "error_kind": "io", "runtime_error": "无法写入文件", "issues": []},
                ],
            }
            app.file_items = app.report_data["files"]
            app._populate_files()
            failure = app.file_table.item("1", "values")[4]
            self.assertIn("io: 无法写入文件", failure)
            self.assertEqual(app.file_table.item("0", "values")[4], "")
        finally:
            root.destroy()

    def test_changes_page_shows_decision_summary(self):
        # 2026-08-08 报告完善：摘要并入换刀跳过/重复关系，重复裁决文件（无 changes/diff）也进入摘要。
        root, app = self._build_viewer(1290, 720)
        try:
            app.report_data = {
                "files": [
                    {"file": "P.MPF", "program": "P", "status": "success", "issues": [],
                     "changes": ["补全头部"],
                     "auto_tool_change_skipped": "程序包含多把刀具，已跳过自动换刀",
                     "duplicate_winner": "", "duplicate_target": ""},
                    {"file": "Q.MPF", "program": "Q", "status": "duplicate-removed", "issues": [],
                     "changes": [],
                     "duplicate_winner": "P.MPF", "duplicate_target": "D:\\NC\\Q.MPF"},
                ],
            }
            app.file_items = app.report_data["files"]
            app._populate_files()   # 选中首行触发 _update_views → _fill_changes
            summary_row = app.change_summary_table.item("0", "values")[1]
            self.assertIn("换刀跳过：程序包含多把刀具", summary_row)
            self.assertEqual(len(app.change_summary_table.get_children()), 2)
            duplicate_row = app.change_summary_table.item("1", "values")[1]
            self.assertIn("重复：采用 P.MPF", duplicate_row)
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()
