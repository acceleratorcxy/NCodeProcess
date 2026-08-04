import json
import tempfile
import unittest
from pathlib import Path

from ncodeprocessreportviewer.viewer import (
    discover_reports,
    file_issue_counts,
    iter_stats_rows,
    load_report,
    report_summary,
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


class LayoutMetricTests(unittest.TestCase):
    def test_supported_screen_geometry(self):
        self.assertEqual(window_geometry_for_screen(1366, 768), (1206, 640, 1160, 640))
        self.assertEqual(window_geometry_for_screen(1920, 1080), (1290, 720, 1160, 640))

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
            for table, label in (
                (app.report_table, "报告列表"),
                (app.file_table, "文件明细"),
            ):
                self.assertGreaterEqual(float(table.xview()[1]), 0.999, label)
            for index, table, label in (
                (1, app.stats_table, "参数统计"),
                (2, app.issue_table, "校验问题"),
            ):
                app.notebook.select(index)
                root.update_idletasks()
                self.assertGreaterEqual(float(table.xview()[1]), 0.999, label)
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()
