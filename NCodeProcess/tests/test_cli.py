import unittest
from unittest.mock import patch

import ncodeprocess.cli as cli
from ncodeprocess.core import reset_runtime_log, runtime_log


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
