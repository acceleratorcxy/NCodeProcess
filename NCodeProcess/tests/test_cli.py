import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ncodeprocess.cli import main
import ncodeprocess.cli as cli
from ncodeprocess.core import reset_runtime_log, runtime_log


FULL_HEADER = (
    'MSG("BIANZHI:A")\n'
    'MSG("SHENHE:B")\n'
    'MSG("PROGRAM:P")\n'
    'MSG("DRAWING NUMBER:D")\n'
    'MSG("PART VERSION:V")\n'
    'MSG("NC MACHINE:M")\n'
    'MSG("CONTROL SYSTEM:SIE840D")\n'
    'MSG("DATE:Jul 31 09:38:23 2026")\n'
)


class CliTests(unittest.TestCase):
    def make_dir(self):
        return Path(tempfile.mkdtemp(prefix="ncodeprocess-cli-"))

    @staticmethod
    def _run(argv):
        buffer = io.StringIO()
        with patch("ncodeprocess.cli.load_all", return_value={}), contextlib.redirect_stdout(buffer):
            code = main(argv)
        return code, buffer.getvalue()

    def test_preview_without_yes_never_writes(self):
        root = self.make_dir()
        (root / "x_P.MPF").write_text(FULL_HEADER + "N1S1000M03\nN2M30\n", encoding="utf-8")
        code, _out = self._run(["--input", str(root)])
        self.assertEqual(code, 0)
        self.assertFalse((root / "P.MPF").exists())
        self.assertFalse((root / "NCodeProcessData").exists())

    def test_yes_requires_drawing_and_part_version(self):
        root = self.make_dir()
        (root / "x_P.MPF").write_text(FULL_HEADER + "N1S1000M03\nN2M30\n", encoding="utf-8")
        code, _out = self._run(["--input", str(root), "--yes"])
        self.assertEqual(code, 2)

    def test_yes_executes_and_writes_report_with_json_report(self):
        root = self.make_dir()
        (root / "x_P.MPF").write_text(FULL_HEADER + "N1S1000M03\nN2M30\n", encoding="utf-8")
        json_path = root / "report.json"
        code, _out = self._run(["--input", str(root), "--yes", "--drawing-number", "D", "--part-version", "V",
                                "--json-report", str(json_path)])
        self.assertEqual(code, 0)
        self.assertTrue((root / "P.MPF").exists())
        self.assertTrue(json_path.exists())
        data = json.loads(json_path.read_text(encoding="utf-8"))
        self.assertEqual(data.get("success"), 1)
        # WP-R4：导出仅生成单个 JSON，不生成日志文件。
        self.assertFalse((root / "logs").exists())

    def test_csv_report_written(self):
        root = self.make_dir()
        (root / "x_P.MPF").write_text(FULL_HEADER + "N1S1000M03\nN2M30\n", encoding="utf-8")
        csv_path = root / "issues.csv"
        code, _out = self._run(["--input", str(root), "--yes", "--drawing-number", "D", "--part-version", "V",
                                "--csv-report", str(csv_path)])
        self.assertEqual(code, 0)
        content = csv_path.read_text(encoding="utf-8-sig")
        self.assertTrue(content.startswith("file,line,text,kind,severity,suggestion"))


class CliConfigTests(unittest.TestCase):
    def test_cli_newline_flag_applied(self):
        # WP-C3：显式参数覆盖默认/偏好。
        args = cli.build_parser().parse_args(["--input", "dummy", "--newline", "lf"])
        cfg = cli._config_from_args(args)
        self.assertEqual(cfg.newline, "lf")
        self.assertEqual(cfg.m03_position, "after-s")

    def test_cli_loads_persisted_preferences(self):
        # WP-C3：未显式传参时读取持久化偏好作为默认。
        with patch("ncodeprocess.cli.load_all", return_value={
            "newline": "crlf",
            "m03_position": "standalone",
            "feed_max": "5000",
            "multiple_spindle_warn": "0",
            "aux_m03_before_motion": "0",
            "retract_z_threshold": "30",
        }):
            args = cli.build_parser().parse_args(["--input", "dummy"])
            cfg = cli._config_from_args(args)
        self.assertEqual(cfg.newline, "crlf")
        self.assertEqual(cfg.m03_position, "standalone")
        self.assertEqual(cfg.feed_max, 5000.0)
        self.assertFalse(cfg.multiple_spindle_warn)
        self.assertNotIn("m03-before-motion", cfg.aux_checks)
        self.assertEqual(cfg.retract_z_threshold, 30.0)

    def test_cli_emits_settings_loaded_event(self):
        # WP-R1：CLI 加载持久化偏好时写入运行日志 settings_loaded 事件。
        reset_runtime_log()
        with patch("ncodeprocess.cli.load_all", return_value={"newline": "crlf"}):
            args = cli.build_parser().parse_args(["--input", "dummy"])
            cli._config_from_args(args)
        events = [entry["event"] for entry in runtime_log().snapshot()]
        self.assertIn("settings_loaded", events)


if __name__ == "__main__":
    unittest.main()
