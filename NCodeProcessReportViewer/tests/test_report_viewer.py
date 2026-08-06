import json
import tempfile
import unittest
from pathlib import Path

from ncodeprocessreportviewer.viewer import (
    discover_reports,
    file_issue_counts,
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
            for index, table, label in (
                (1, app.file_table, "文件明细"),
                (2, app.stats_table, "参数统计"),
                (3, app.issue_table, "校验问题"),
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
            self.assertEqual(values[2], "error")
            # WP-R1：运行日志页展示 detail（error 事件 traceback）。
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

    def test_cell_tooltip_truncation_detection(self):
        # 查看器悬停浮窗：超长单元格判定为截断（显示提示），短内容不提示。
        root, app = self._build_viewer(1290, 720)
        try:
            app.notebook.select(2)  # 参数统计页
            root.update_idletasks()
            long_value = "很长很长的文件名_" * 20
            iid = app.stats_table.insert("", "end", values=(long_value, "F", "1", "10", "20", "否"))
            root.update_idletasks()
            self.assertTrue(app._cell_truncated(app.stats_table, iid, "#1", long_value))
            self.assertFalse(app._cell_truncated(app.stats_table, iid, "#1", "短"))
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()
