import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from ncodeprocess.core import AptMeta, Config, FIELD_ORDER, FilePlan, ProcessReport, ProgramInfo, RuntimeLog, ToolInfo, _axial_feed_exempt, _decode, _extract_apt_data_cached, add_initial_tool_change, add_m03, align_lines, analyze_program, apply_header, build_plan, build_feed_reference, calculate_stats, code_part, crosscheck_apt, detect_feed_outliers, emit_event, extract_drawing_candidates, extract_header_fields, extract_tools, format_nc_date, process_plan, program_defaults, recount_retracts, reprocess_file, reset_runtime_log, runtime_log, save_timestamped_report, scan_directory, validate_program

# 绝大多数测试共用的编制/审核/图号/版次/机床/控制系统/日期默认值。
DEFAULT_INFO = ProgramInfo("A", "B", "D", "V", "M", "C", "DATE")


class CoreTestBase(unittest.TestCase):
    """各主题测试类共用的临时目录与配置 fixture（去重：make_dir/_cfg 曾四处重复）。"""

    def make_dir(self, prefix="ncodeprocess-"):
        return Path(tempfile.mkdtemp(prefix=prefix))

    @staticmethod
    def _cfg(**overrides):
        """默认放开 G00 检查，并按需覆盖其它配置。"""
        return Config(g00_level="allow", **overrides)


class CoreTests(CoreTestBase):
    def setUp(self):
        # 隔离运行日志：避免跨用例的事件累积污染断言（WP-F2）。
        reset_runtime_log()

    @staticmethod
    def _mpf(plan):
        return next(f for f in plan.files if f.kind == "mpf")

    def test_v5_header_m03_stats_and_cleanup(self):
        root = self.make_dir()
        (root / "prefix_AG6D311A0101.MPF").write_bytes(
            ('MSG("BIANZHI:")\r\nMSG("PROGRAM:AG6D311A0101")\r\n'
             'N1G1X-2.5Y3Z0F100S2000\r\nN3X4.0F50\r\nN5M30\r\n').encode("utf-8"))
        (root / "prefix_AG6D311A0101_I.aptsource").write_text("APT", encoding="utf-8")
        (root / "a.LOG").write_text("log", encoding="utf-8")
        info = ProgramInfo("A", "B", "D", "V", "M", "C", "", [ToolInfo(1, "10", "", "")])
        cfg = Config(g00_level="allow", save_aptsource=True)
        plan = build_plan(scan_directory(str(root), cfg), info, cfg)
        mpf = self._mpf(plan)
        self.assertIn("M03", mpf.output_text)
        self.assertEqual(mpf.output_text.count("M03"), 1)
        self.assertEqual(mpf.stats.minimum["X"], -2.5)
        self.assertEqual(mpf.stats.maximum["X"], 4.0)
        self.assertIn('MSG("T1:DIA=10.000")', mpf.output_text)

    def test_light_plan_skips_deep_analysis_but_resolves_targets(self):
        # 两阶段扫描：analyze=False 只做轻量计划（文件/动作/目标），深度分析推迟。
        root = self.make_dir()
        (root / "x_P.MPF").write_text(
            'MSG("PROGRAM:P")\nN1G1Z100F6000\nN2G1Z5F300\nN3G1X1Y1F1800\nN4G1Z100F6000\n',
            encoding="utf-8")
        (root / "x_P_I.aptsource").write_text("$$ MACHIN 3-axis Machine.1\n", encoding="utf-8")
        cfg = self._cfg(save_aptsource=True)
        scan = scan_directory(str(root), cfg)
        light = build_plan(scan, DEFAULT_INFO, cfg, analyze=False)
        mpf = self._mpf(light)
        # 深度分析结果尚未生成。
        self.assertIsNone(mpf.output_text)
        self.assertIsNone(mpf.stats)
        self.assertIsNone(mpf.feed_outlier)
        # 轻量阶段仍完成 APTSOURCE 动作与目标解析。
        apt = next(f for f in light.files if f.kind == "aptsource")
        self.assertEqual(apt.action, "move")
        self.assertIn("aptsource", apt.target or "")
        # 渐进分析上下文可供 GUI 后台逐文件复用。
        self.assertIn("directory", light.analyze_context)
        self.assertIn("latest_apt", light.analyze_context)
        self.assertIn("auto_tools", light.analyze_context)
        self.assertIn("feed_reference", light.analyze_context)
        self.assertIn("tool_overrides", light.analyze_context)

    def test_analyze_plan_file_completes_single_file_after_light_plan(self):
        # 渐进模式：轻量计划后逐文件调用 analyze_plan_file 能补齐深度结果。
        root = self.make_dir()
        (root / "x_P.MPF").write_text(
            'MSG("PROGRAM:P")\nN1G1Z100F6000\nN2G1Z5F300\nN3G1X1Y1F1800\nN4G1Z100F6000\n',
            encoding="utf-8")
        (root / "x_P_I.aptsource").write_text(
            "$$ MACHIN 3-axis Machine.1\nFEDRAT/ 1800.0000,MMPM\n", encoding="utf-8")
        cfg = self._cfg()
        light = build_plan(scan_directory(str(root), cfg), DEFAULT_INFO, cfg, analyze=False)
        mpf = self._mpf(light)
        context = light.analyze_context
        from ncodeprocess.core import analyze_plan_file
        analyze_plan_file(
            mpf, context["directory"], DEFAULT_INFO, cfg,
            context["latest_apt"], context["auto_tools"],
            context["feed_reference"], context["tool_overrides"])
        self.assertIsNotNone(mpf.output_text)
        self.assertIsNotNone(mpf.stats)
        self.assertIsNotNone(mpf.feed_outlier)
        self.assertEqual(mpf.feed_outlier.safe_plane, 100.0)
        self.assertIsNotNone(mpf.apt_meta)
        self.assertIsNotNone(mpf.apt_source_path)
        self.assertTrue((mpf.apt_source_path or "").endswith("x_P_I.aptsource"))

    def test_analyze_plan_file_reuses_cached_result(self):
        # 单文件分析结果缓存：相同输入重复分析时跳过 APT 解析与分析管线。
        from ncodeprocess.core import _ANALYSIS_CACHE, analyze_plan_file
        _ANALYSIS_CACHE.clear()
        root = self.make_dir()
        (root / "x_P.MPF").write_text(
            'MSG("PROGRAM:P")\nN1G1Z100F6000\nN2G1Z5F300\nN3G1X1Y1F1800\nN4G1Z100F6000\n',
            encoding="utf-8")
        (root / "x_P_I.aptsource").write_text(
            "$$ MACHIN 3-axis Machine.1\nFEDRAT/ 1800.0000,MMPM\n", encoding="utf-8")
        cfg = self._cfg()
        light = build_plan(scan_directory(str(root), cfg), DEFAULT_INFO, cfg, analyze=False)
        mpf = self._mpf(light)
        context = light.analyze_context
        calls = {"apt": 0}
        import ncodeprocess.core as core_module
        original = core_module._extract_apt_data_cached

        def counting_extract(*args, **kwargs):
            calls["apt"] += 1
            return original(*args, **kwargs)

        with patch("ncodeprocess.core._extract_apt_data_cached", side_effect=counting_extract):
            analyze_plan_file(
                mpf, context["directory"], DEFAULT_INFO, cfg,
                context["latest_apt"], context["auto_tools"],
                context["feed_reference"], context["tool_overrides"])
            first_output = mpf.output_text
            self.assertEqual(calls["apt"], 1)
            # 第二次分析相同文件：直接命中缓存，不再解析 APT。
            analyze_plan_file(
                mpf, context["directory"], DEFAULT_INFO, cfg,
                context["latest_apt"], context["auto_tools"],
                context["feed_reference"], context["tool_overrides"])
            self.assertEqual(calls["apt"], 1)
            self.assertEqual(mpf.output_text, first_output)

    def test_hass_percent_and_existing_m03(self):
        root = self.make_dir()
        (root / "x_AG6D311A0101.MPF").write_bytes("%;\r\nN1G01X1S5000M03;\r\nN2M30;\r\n%;\r\n".encode("utf-8"))
        info = DEFAULT_INFO
        cfg = self._cfg()
        plan = build_plan(scan_directory(str(root), cfg), info, cfg)
        out = self._mpf(plan).output_text
        self.assertTrue(out.startswith("%;\r\nMSG("))
        self.assertEqual(out.count("M03"), 1)
        self.assertNotIn("\n\nN", out)
        self.assertIn('MSG("NC MACHINE:HASS");', out)
        self.assertIn('MSG("CONTROL SYSTEM:SIE840D");', out)

    def test_m03_ignores_spindle_mention_inside_comment(self):
        # An S value that only appears inside a parenthetical comment is not
        # a real spindle command: M03 must be inserted as a standalone line
        # before the first body instruction, never appended to the comment.
        comment_only = '%\nMSG("PROGRAM:P")\nN1G1X10 (FEED S5000 OK)\nN2M30\n%\n'
        out, changed, _note = add_m03(comment_only, Config())
        self.assertTrue(changed)
        self.assertNotIn("(FEED S5000 OK)M03", out)
        self.assertIn("M03\nN1G1X10 (FEED S5000 OK)", out)

        # A comment mentioning S must not capture the M03 insertion; the real
        # S instruction later in the body is the one that receives M03.
        real_spindle = '%\nMSG("PROGRAM:P")\nN1G1X10 (FEED S5000 MAX)\nN2G1X20S1000\nN3M30\n%\n'
        out, changed, _note = add_m03(real_spindle, Config())
        self.assertTrue(changed)
        self.assertNotIn("(FEED S5000 MAX)M03", out)
        self.assertIn("N2G1X20S1000M03", out)

    def test_missing_m03_is_error_when_auto_insert_enabled(self):
        # FR-05.6: when automatic M03 insertion is enabled but the program
        # still has no M03 afterwards, the failed insertion must be reported
        # as an error requiring user confirmation, not a silent warning.
        text = '%\nMSG("PROGRAM:P")\nN1G1X10\nN2M30\n%\n'
        issues = validate_program(text, "P.MPF", "P", DEFAULT_INFO, Config(auto_m03=True))
        spindle = [i for i in issues if i.kind == "spindle-start"]
        self.assertEqual(len(spindle), 1)
        self.assertEqual(spindle[0].severity, "error")

    def test_missing_m03_is_warning_when_auto_insert_disabled(self):
        # With auto insertion disabled the missing M03 stays a warning so the
        # user can decide how to handle the program.
        text = '%\nMSG("PROGRAM:P")\nN1G1X10\nN2M30\n%\n'
        issues = validate_program(text, "P.MPF", "P", DEFAULT_INFO, Config(auto_m03=False))
        spindle = [i for i in issues if i.kind == "spindle-start"]
        self.assertEqual(len(spindle), 1)
        self.assertEqual(spindle[0].severity, "warning")

    def test_reprocess_file_revalidates_after_manual_edit(self):
        # After the operator manually fixes the NC code, reprocessing the
        # in-memory plan must regenerate output and clear the previous error.
        f = FilePlan("x_P.MPF", "mpf", "P", "P.MPF", "keep")
        f.original_text = '%\nMSG("PROGRAM:P")\n(ONLY COMMENT)\n%\n'
        info = DEFAULT_INFO
        cfg = Config(g00_level="allow", auto_m03=True)
        reprocess_file(f, info, cfg)
        self.assertTrue(any(i.kind == "spindle-start" and i.severity == "error" for i in f.issues))
        # The operator adds a real instruction line; the next review must
        # auto-insert M03 and drop the spindle-start error.
        f.original_text = '%\nMSG("PROGRAM:P")\nN1G1X10\nN2M30\n%\n'
        reprocess_file(f, info, cfg)
        self.assertIn("M03", f.output_text)
        self.assertFalse(any(i.kind == "spindle-start" for i in f.issues))

    def test_reprocess_file_keeps_existing_tool_rows_when_none_supplied(self):
        # 应用所选/重审时若不提供刀具，应保留 MPF 已有 Tn 或 APT 解析结果，不得刷空。
        text = 'MSG("PROGRAM:P")\nMSG("T1:DIA=10.000,TOOL_TYPE=圆鼻立铣刀")\nN1G1X10F1000S5000M03\nM30\n'
        plan = FilePlan("P.MPF", "mpf", "P", "P.MPF", "keep")
        plan.original_text = text
        reprocess_file(plan, DEFAULT_INFO, self._cfg(), tools=[])
        self.assertIn('MSG("T1:DIA=10.000,TOOL_TYPE=圆鼻立铣刀")', plan.output_text or "")

    def test_align_lines_tags(self):
        rows = align_lines("A\nB\nC\nD", "A\nB\nX\nD")
        self.assertEqual(
            rows,
            [
                ("A", "", "A", ""),
                ("B", "", "B", ""),
                ("C", "changed", "X", "changed"),
                ("D", "", "D", ""),
            ],
        )
        self.assertIn(("", "", "B", "added"), align_lines("A\nC", "A\nB\nC"))
        self.assertIn(("B", "removed", "", ""), align_lines("A\nB\nC", "A\nC"))

    def test_tool_type_omitted_and_apt_defaults_detected(self):
        root = self.make_dir()
        (root / "x_AG6D311A0101.MPF").write_text("%;\nN1T1M06;\nN2S100M03;\nN3M30;\n%;\n", encoding="utf-8")
        (root / "x_AG6D311A0101_I.aptsource").write_text("PPRINT PROGNAME AG6D311A0101\nTOOLNO/1, 20.0, 3.0,, 120.0,$\n", encoding="utf-8")
        cfg = self._cfg()
        plan = build_plan(scan_directory(str(root), cfg), DEFAULT_INFO, cfg)
        out = self._mpf(plan).output_text
        self.assertIn('MSG("T1:DIA=20.000,TOOL_CONER=3.000");', out)
        self.assertNotIn("TOOL_TYPE=", out)
        apt = next(f for f in plan.files if f.kind == "aptsource")
        self.assertEqual(apt.action, "delete")
        self.assertIsNone(apt.target)

    def test_aptsource_is_not_saved_by_default(self):
        root = self.make_dir()
        apt_path = root / "x_P_I.aptsource"
        apt_path.write_text("PPRINT PROGNAME P\n", encoding="utf-8")
        cfg = Config()
        plan = build_plan(scan_directory(str(root), cfg), ProgramInfo(), cfg)
        apt = next(f for f in plan.files if f.kind == "aptsource")
        self.assertEqual(apt.action, "delete")
        self.assertIsNone(apt.target)
        report = process_plan(plan, str(root), cfg)
        self.assertEqual(report.deleted, 1)
        self.assertFalse(apt_path.exists())
        self.assertFalse((root / "aptsource").exists())

    def test_special_tool_detected_from_cutter_and_toolno(self):
        text = (
            "CUTTER/ 16.000000,  3.000000,  5.000000,  3.000000,  0.000000,$\n"
            "        -3.000000, 30.000000\n"
            "TOOLNO/5,   13.178000,    3.000000,   -6.000000,  120.000000,$\n"
        )
        tools = extract_tools(text)
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0].number, 5)
        self.assertEqual(tools[0].dia, "16.000")
        self.assertEqual(tools[0].tool_coner, "3.000")
        self.assertEqual(tools[0].tool_type, "反锥立铣刀")
        self.assertEqual(tools[0].tool_angle, "-3.000")
        self.assertEqual(tools[0].to_msg(), "T5:DIA=16.000,TOOL_CONER=3.000,TOOL_ANGLE=-3.000,TOOL_TYPE=反锥立铣刀")

    def test_positive_special_tool_is_pencil_mill(self):
        text = "CUTTER/ 16.000000, 3.000000\nTOOLNO/5, 13.178000, 3.000000, 6.000000,$\n"
        tool = extract_tools(text)[0]
        self.assertEqual(tool.tool_type, "铅笔铣刀")
        self.assertEqual(tool.tool_angle, "3.000")

    def test_ordinary_mill_family_detection(self):
        cases = (
            ("CUTTER/ 16.000000, 3.000000\nTOOLNO/5, 16.000000, 3.000000,, 120.000000,$\n", "16.000", "3.000", "圆鼻立铣刀"),
            ("CUTTER/ 10.000000,  5.000000\nTOOLNO/1, 10.000000, 5.000000,, 120.000000,$\n", "10.000", "5.000", "球头立铣刀"),
            ("CUTTER/ 10.000000,  0.000000\nTOOLNO/2, 10.000000, 0.000000,, 120.000000,$\n", "10.000", "0.000", "平底立铣刀"),
            ("CUTTER/ 20.000000,  3.000000\nTOOLNO/3, 20.000000, 3.000000,, 120.000000,$\n", "20.000", "3.000", "圆鼻立铣刀"),
            ("CUTTER/10,2\nTOOLNO/1,10,1,,\n", "10.000", "2.000", ""),
        )
        for text, dia, coner, tool_type in cases:
            with self.subTest(tool_type=tool_type or "none"):
                tool = extract_tools(text)[0]
                self.assertEqual(tool.dia, dia)
                self.assertEqual(tool.tool_coner, coner)
                self.assertEqual(tool.tool_type, tool_type)

    def test_t_slot_mill_recognized_when_diameter_ratio_large(self):
        # 刀具说明：T 形刀直径与切削刃差异很大、无锥度角度。
        # 初步规则：直径比值 >= 2 且无包含角 → T 形刀。
        text = "CUTTER/ 30.000000,  1.000000\nTOOLNO/1, 10.000000, 1.000000,, 120.000000,$\n"
        tool = extract_tools(text)[0]
        self.assertEqual(tool.tool_type, "T形刀")

    def test_reverse_taper_not_mistaken_for_t_slot_mill(self):
        # 反锥直径差小（12 vs 10.467，比值约 1.15）且带负角度，不得判为 T 形刀。
        text = "CUTTER/ 12.000000,  3.000000\nTOOLNO/4, 10.467000, 3.000000, -4.000000, 120.000000,$\n"
        tool = extract_tools(text)[0]
        self.assertEqual(tool.tool_type, "反锥立铣刀")
        self.assertEqual(tool.tool_angle, "-2.000")

    def test_sample_apt_reverse_taper_mill_with_angle(self):
        # 样例 D12R3A2 反锥立铣刀：CUTTER 直径 12、TOOLNO 名义直径 10.467、包含角 -4。
        text = ("CUTTER/ 12.000000,  3.000000,  3.000000,  3.000000,  0.000000,$\n"
                "TOOLNO/4,   10.467000,    3.000000,   -4.000000,  120.000000,$\n")
        tool = extract_tools(text)[0]
        self.assertEqual(tool.dia, "12.000")
        self.assertEqual(tool.tool_coner, "3.000")
        self.assertEqual(tool.tool_type, "反锥立铣刀")
        self.assertEqual(tool.tool_angle, "-2.000")

    def test_drill_and_center_drill_detection(self):
        sample_drill = ("CUTTER/  5.200000,  0.000000,  2.600000,  1.501111, 30.000000,$\n"
                        "TOOLNO/9,    5.200000,,  120.000000,  120.000000,$\n"
                        "45.000000,    1.501000,   35.000000,2,    0.000000,NOTE\n")
        sample_center = ("CUTTER/  2.500000,  0.000000,  1.250000,  0.751076, 31.000000,$\n"
                         "TOOLNO/13,    2.500000,,  118.000000,  120.000000,$\n"
                         "5.000000,,   11.000000,,    0.000000,NOTE\n")
        size_independent = (
            ("CUTTER/ 7.250000, 0.000000, 3.625000, 2.000000, 31.000000,$\n         0.000000, 11.000000\nTOOLNO/13, 7.250000,, 118.000000, 120.000000,$\n    5.000000,, 11.000000,, 0.000000,NOTE\n", "7.250", "中心钻"),
            ("CUTTER/ 8.750000, 0.000000, 4.375000, 2.500000, 30.000000,$\n         0.000000, 35.000000\nTOOLNO/10, 8.750000,, 120.000000, 120.000000,$\n   45.000000, 2.500000, 35.000000,2, 0.000000,NOTE\n", "8.750", "钻头"),
        )
        cases = (
            (sample_drill, "5.200", "钻头"),
            (sample_center, "2.500", "中心钻"),
            *size_independent,
        )
        for text, dia, expected_type in cases:
            with self.subTest(tool_type=expected_type):
                tool = extract_tools(text)[0]
                self.assertEqual(tool.dia, dia)
                self.assertEqual(tool.tool_type, expected_type)
                self.assertEqual(tool.tool_angle, "")
                self.assertNotIn("TOOL_ANGLE", tool.to_msg())

    def test_code_part_strips_parenthesised_comment(self):
        self.assertEqual(code_part("N1G1X10 (comment)"), "N1G1X10 ")
        self.assertEqual(code_part("N1G1X10"), "N1G1X10")

    def test_special_tool_is_written_to_mpf_from_paired_apt(self):
        root = self.make_dir()
        (root / "x_P.MPF").write_text("N1T5M06\nN2S100M03\nN3M30\n", encoding="utf-8")
        (root / "x_P_I.aptsource").write_text(
            "CUTTER/ 16.000000, 3.000000\nTOOLNO/5, 13.178000, 3.000000, -6.000000,$\n",
            encoding="utf-8",
        )
        cfg = self._cfg()
        plan = build_plan(scan_directory(str(root), cfg), DEFAULT_INFO, cfg)
        out = self._mpf(plan).output_text
        self.assertIn('MSG("T5:DIA=16.000,TOOL_CONER=3.000,TOOL_ANGLE=-3.000,TOOL_TYPE=反锥立铣刀")', out)

    def test_paired_apt_tools_take_priority_over_existing_mpf_tools(self):
        root = self.make_dir()
        (root / "x_P.MPF").write_text(
            'MSG("PROGRAM:P")\nMSG("T5:DIA=99,TOOL_TYPE=旧信息")\nN1T5M06\nN2S100M03\nN3M30\n',
            encoding="utf-8",
        )
        (root / "x_P_I.aptsource").write_text(
            "CUTTER/ 16.000000, 3.000000\nTOOLNO/5, 16.000000, 3.000000,, 120.000000,$\n",
            encoding="utf-8",
        )
        cfg = self._cfg()
        plan = build_plan(
            scan_directory(str(root), cfg),
            DEFAULT_INFO,
            cfg,
            {"P": [ToolInfo(5, "88", "", "配置旧信息")]},
        )
        out = self._mpf(plan).output_text
        self.assertIn('MSG("T5:DIA=16.000,TOOL_CONER=3.000,TOOL_TYPE=圆鼻立铣刀")', out)
        self.assertNotIn("DIA=99", out)
        self.assertNotIn("DIA=88", out)

    def test_newest_apt_generation_wins_over_older_apt(self):
        root = self.make_dir()
        (root / "x_P.MPF").write_text("N1T5M06\nN2S100M03\nN3M30\n", encoding="utf-8")
        old = root / "old_P_I.aptsource"
        new = root / "new_P_I.aptsource"
        old.write_text("CUTTER/ 10.000000, 3.000000\nTOOLNO/5, 10.000000, 3.000000,, 120.000000,$\n", encoding="utf-8")
        new.write_text("CUTTER/ 16.000000, 3.000000\nTOOLNO/5, 16.000000, 3.000000,, 120.000000,$\n", encoding="utf-8")
        os.utime(old, (1000, 1000))
        os.utime(new, (2000, 2000))
        cfg = self._cfg()
        plan = build_plan(scan_directory(str(root), cfg), DEFAULT_INFO, cfg)
        out = self._mpf(plan).output_text
        self.assertIn('MSG("T5:DIA=16.000,TOOL_CONER=3.000,TOOL_TYPE=圆鼻立铣刀")', out)
        self.assertNotIn("DIA=10.000000", out)

    def test_streamed_apt_scan_keeps_multiple_tool_records(self):
        root = self.make_dir()
        (root / "x_P.MPF").write_text("N1S100M03\nN2M30\n", encoding="utf-8")
        middle = "\n".join("GOTO/1,2,3" for _ in range(2000))
        (root / "x_P_I.aptsource").write_text(
            "CUTTER/ 10.000000, 3.000000\nTOOLNO/1, 10.000000, 3.000000,, 120.000000,$\n"
            + middle
            + "\nCUTTER/ 6.000000, 1.000000\nTOOLNO/2, 6.000000, 1.000000,, 120.000000,$\n",
            encoding="utf-8",
        )
        cfg = self._cfg()
        plan = build_plan(scan_directory(str(root), cfg), DEFAULT_INFO, cfg)
        out = self._mpf(plan).output_text
        self.assertIn('MSG("T1:DIA=10.000,TOOL_CONER=3.000,TOOL_TYPE=圆鼻立铣刀")', out)
        self.assertIn('MSG("T2:DIA=6.000,TOOL_CONER=1.000,TOOL_TYPE=圆鼻立铣刀")', out)

    def test_program_tool_override_replaces_existing_tool_rows(self):
        root = self.make_dir()
        (root / "x_AG6D311A0101.MPF").write_text('MSG("PROGRAM:AG6D311A0101")\nMSG("T1:DIA=20")\nMSG("T2:DIA=10")\nN1S100M03\nN2M30\n', encoding="utf-8")
        cfg = self._cfg()
        info = DEFAULT_INFO
        plan = build_plan(scan_directory(str(root), cfg), info, cfg, {"AG6D311A0101": [ToolInfo(1, "8", "", "自定义刀")]})
        out = self._mpf(plan).output_text
        self.assertIn('MSG("T1:DIA=8.000,TOOL_TYPE=自定义刀")', out)
        self.assertNotIn('MSG("T2:', out)

    def test_tool_rows_are_last_header_lines(self):
        text = 'MSG("PROGRAM:P")\nMSG("DRAWING NUMBER:D")\nMSG("PART VERSION:V")\nN1S100M03\nN2M30\n'
        cfg = self._cfg()
        info = ProgramInfo("A", "B", "D", "V", "M", "C", "DATE", [ToolInfo(1, "8", "", "钻头")])
        out, _, _ = apply_header(text, "P", info, cfg, replace_tools=True)
        lines = out.splitlines()
        tool_index = next(i for i, line in enumerate(lines) if 'MSG("T1:' in line)
        body_index = next(i for i, line in enumerate(lines) if line.startswith("N1"))
        self.assertEqual(tool_index, body_index - 1)
        self.assertNotIn("\n\nN", out)

    def test_process_mpf_error_emits_runtime_event(self):
        # WP-F2：build_plan 内 MPF 处理异常必须进入运行日志并携带 traceback。
        root = self.make_dir()
        (root / "A.MPF").write_text('MSG("PROGRAM:A")\nN1S1000M03\nN2M30\n', encoding="utf-8")
        cfg = self._cfg()
        scan = scan_directory(str(root), cfg)
        with patch("ncodeprocess.core.apply_header", side_effect=RuntimeError("boom")):
            build_plan(scan, DEFAULT_INFO, cfg)
        events = runtime_log().snapshot()
        self.assertTrue(any(e["event"] == "error" and "A.MPF" in e["message"] and "Traceback" in e["detail"] for e in events))

    def test_recognition_events_emitted_for_tools_and_issues(self):
        # WP-F2 扩展：刀具识别 / F 离群识别 / 异常与错误识别结果进入运行日志。
        root = self.make_dir()
        (root / "P.MPF").write_text(
            'MSG("PROGRAM:P")\nMSG("T1:DIA=10.,TOOL_TYPE=平底立铣刀")\n'
            "N1G1Z100F3000\nN2G1Z10F300\nN3G1X1F500\nN4G1X2F500\nN5G1X3F500\n"
            "N6G1X4F500\nN7G1X5F500\nN8G1Z100F3000\nN9G1Z5F300\nN10G1X6F8000\nN11M30\n",
            encoding="utf-8")
        cfg = self._cfg()
        build_plan(scan_directory(str(root), cfg), DEFAULT_INFO, cfg)
        events = runtime_log().snapshot()
        self.assertTrue(any(e["event"] == "tool_recognized" and "P.MPF" in e["message"] and "T1" in e["detail"] for e in events))
        self.assertTrue(any(e["event"] == "feed_outlier" and "F8000" in e["message"] and "P.MPF" in e["message"] for e in events))
        self.assertTrue(any(e["event"] == "issues_found" and "P.MPF" in e["message"] and "feed-outlier" in e["detail"] for e in events))

    def test_semicolon_after_code_is_trailing_comment(self):
        # WP-F4：HASS 分号视为块终止符，分号后内容按行内注释处理，不参与统计/校验/补写。
        text = 'MSG("PROGRAM:P")\nN10 S5000;S9000\nN20 M30\n%\n'
        issues = validate_program(text, "P.MPF", "P", DEFAULT_INFO, self._cfg(auto_m03=False))
        self.assertFalse(any(i.kind == "multiple-spindle-speeds" for i in issues))
        stats = calculate_stats(text)
        self.assertEqual(stats.counts["S"], 1)
        out, changed, _note = add_m03('MSG("PROGRAM:P")\nN10 S5000;说明\nN20 M30\n', self._cfg())
        self.assertTrue(changed)
        self.assertIn("S5000M03;说明", out)

    def test_initial_tool_change_preserves_comment_tool_refs(self):
        # WP-F4：自动换刀改写只作用于代码部分，括号与分号后注释中的 T 号保持原样。
        tools = [ToolInfo(1, "10", "", "平底立铣刀")]
        text = 'MSG("PROGRAM:P")\nN10 T2;T99 备用刀具\nN20 (T8 备用) T2\nN30 M30\n'
        out, changed, _note = add_initial_tool_change(text, tools, self._cfg(auto_tool_change=True))
        self.assertTrue(changed)
        self.assertIn("T1M6", out)
        self.assertIn("N10 T1;T99 备用刀具", out)
        self.assertIn("N20 (T8 备用) T1", out)

    def test_unclosed_quote_inside_comment_is_not_flagged(self):
        # WP-F4：括号与分号注释内的引号不参与未闭合引号检查。
        text = 'MSG("PROGRAM:P")\nN10 G1 X10 (说明 "引号)\nN20 M30;注释 "引号\n'
        issues = validate_program(text, "P.MPF", "P", DEFAULT_INFO, self._cfg())
        self.assertFalse(any(i.kind == "unclosed-quote" for i in issues))

    def test_m03_fallback_skips_comment_only_lines(self):
        # WP-F4：无 S 时 M03 独立行插入跳过括号/分号注释行，落在第一条真实指令前。
        text = 'MSG("PROGRAM:P")\n;说明注释\n(另一注释)\nN1 G1 X10 F500\nN2 M30\n'
        out, changed, _note = add_m03(text, self._cfg())
        self.assertTrue(changed)
        self.assertLess(out.index("M03"), out.index("N1 G1 X10 F500"))
        self.assertGreater(out.index("M03"), out.index(";说明注释"))

    def test_apply_header_marks_existing_tool_as_update_not_insert(self):
        # 已有 T1 刀具被替换时记录为「更新刀具 T1」而非「插入刀具 T1」，
        # 避免执行确认列表出现“重复插入刀具”。
        text = 'MSG("PROGRAM:P")\nMSG("T1:DIA=10.000,TOOL_TYPE=圆鼻立铣刀")\nN1G1X10F1000S5000M03\nM30\n'
        info = ProgramInfo("A", "B", "D", "V", "HASS", "SIE840D", "DATE")
        info.tools = [ToolInfo(1, "12", "2", "球头立铣刀", "")]
        _out, changes, _issues = apply_header(text, "P", info, self._cfg(), replace_tools=True)
        self.assertIn("更新刀具 T1", changes)
        self.assertFalse(any("插入刀具 T1" in change for change in changes))
        # 新增刀具仍记录为插入。
        info.tools.append(ToolInfo(2, "6", "0", "平底立铣刀", ""))
        _out, changes, _issues = apply_header(text, "P", info, self._cfg(), replace_tools=True)
        self.assertIn("插入刀具 T2", changes)

    def test_apply_header_skips_unchanged_tool_rows(self):
        # 刀具信息与头部已有 Tn 完全一致时：保留原行、不记录任何刀具变更。
        text = 'MSG("PROGRAM:P")\nMSG("T1:DIA=10.000,TOOL_CONER=3.000,TOOL_TYPE=圆鼻立铣刀")\nN1G1X10F1000S5000M03\nM30\n'
        info = ProgramInfo("A", "B", "D", "V", "HASS", "SIE840D", "DATE")
        info.tools = [ToolInfo(1, "10", "3", "圆鼻立铣刀", "")]
        _out, changes, _issues = apply_header(text, "P", info, self._cfg(), replace_tools=True)
        self.assertFalse(any("刀具" in change for change in changes), f"不应记录刀具变更: {changes}")
        self.assertIn('MSG("T1:DIA=10.000,TOOL_CONER=3.000,TOOL_TYPE=圆鼻立铣刀")', _out)
        # 刀具值有变化时仍记录更新。
        info.tools[0] = ToolInfo(1, "12", "3", "圆鼻立铣刀", "")
        _out, changes, _issues = apply_header(text, "P", info, self._cfg(), replace_tools=True)
        self.assertIn("更新刀具 T1", changes)
        self.assertIn('MSG("T1:DIA=12.000', _out)

    def test_existing_reprocessing_header_values_are_preserved(self):
        text = (
            'MSG("PROGRAM:OLD_PROGRAM")\n'
            'MSG("PART VERSION:V1")\n'
            'MSG("NC MACHINE:CUSTOM_MACHINE")\n'
            'MSG("CONTROL SYSTEM:CUSTOM_CONTROL")\n'
            'MSG("DATE:OLD_DATE")\n'
            'N1S100M03\nN2M30\n'
        )
        info = ProgramInfo("A", "B", "D", "V2", "NEW_MACHINE", "NEW_CONTROL", "NEW_DATE")
        defaults = program_defaults(text, info)
        self.assertEqual(defaults.nc_machine, "CUSTOM_MACHINE")
        self.assertEqual(defaults.control_system, "CUSTOM_CONTROL")
        self.assertEqual(defaults.date, "OLD_DATE")
        out, _changes, _issues = apply_header(text, "OLD_PROGRAM", defaults, Config(overwrite_fields=True), replace_tools=False)
        fields = extract_header_fields(out)
        self.assertEqual(fields["PROGRAM"], "OLD_PROGRAM")
        self.assertEqual(fields["NC MACHINE"], "CUSTOM_MACHINE")
        self.assertEqual(fields["CONTROL SYSTEM"], "CUSTOM_CONTROL")
        self.assertEqual(fields["DATE"], "OLD_DATE")
        # PART VERSION is a preselected/editable parameter, so an explicit
        # overwrite can still update it.
        self.assertEqual(fields["PART VERSION"], "V2")

    def test_apply_header_preserves_existing_msg_indent(self):
        # FR-4.2.5：替换已有 MSG 行时保留原行缩进。
        text = '  MSG("PROGRAM:P")\n    MSG("BIANZHI:OLD")\nN1S100M03\nN2M30\n'
        info = ProgramInfo("NEW", "B", "D", "V", "M", "C", "DATE")
        out, _changes, _issues = apply_header(text, "P", info, self._cfg(overwrite_fields=True))
        self.assertIn('  MSG("PROGRAM:P")', out)
        self.assertIn('    MSG("BIANZHI:NEW")', out)

    def test_apply_header_uses_existing_indent_for_inserted_fields(self):
        # FR-4.2.5：插入缺失字段时沿用头部已有 MSG 行的缩进。
        text = '    MSG("PROGRAM:P")\nN1S100M03\nN2M30\n'
        info = ProgramInfo("A", "B", "D", "V", "M", "C", "DATE")
        out, _changes, _issues = apply_header(text, "P", info, self._cfg())
        self.assertIn('    MSG("DRAWING NUMBER:D")', out)
        self.assertIn('    MSG("BIANZHI:A")', out)

    def test_optional_initial_tool_change_is_inserted_and_corrected(self):
        root = self.make_dir()
        (root / "x_P.MPF").write_text("N1T5M06;\nN2S100M03;\nN3T5;\nN4M30;\n", encoding="utf-8")
        (root / "x_P_I.aptsource").write_text(
            "CUTTER/ 10.000000, 3.000000\nTOOLNO/1, 10.000000, 3.000000,, 120.000000,$\n",
            encoding="utf-8",
        )
        cfg = Config(g00_level="allow", auto_tool_change=True)
        plan = build_plan(scan_directory(str(root), cfg), DEFAULT_INFO, cfg)
        out = self._mpf(plan).output_text
        # 已有换刀指令行 T5M06：在原行修正为 T1M06，不删除也不重新插入。
        self.assertIn("N1T1M06;", out)
        self.assertEqual(out.count("T1M06"), 1)
        self.assertIn("N3T1;", out)
        self.assertNotIn("T5", out)

    def test_existing_correct_tool_change_is_unchanged(self):
        # 已有正确的 TnM6：勾选自动添加换刀指令也不修改程序。
        text = 'MSG("PROGRAM:P")\nN1T1M06;\nN2S100M03;\nN3M30;\n'
        tools = [ToolInfo(1, "10")]
        out, changed, note = add_initial_tool_change(text, tools, self._cfg(auto_tool_change=True))
        self.assertFalse(changed)
        self.assertEqual(out, text)
        self.assertIn("未修改", note)

    def test_existing_wrong_tool_change_fixed_in_place(self):
        # 已有 TnM6 但刀具号不对：在原行把 T 号修正，不删除不重新插入。
        text = 'MSG("PROGRAM:P")\nN1T5M06;\nN2S100M03;\nN3M30;\n'
        tools = [ToolInfo(1, "10")]
        out, changed, note = add_initial_tool_change(text, tools, self._cfg(auto_tool_change=True))
        self.assertTrue(changed)
        self.assertIn("N1T1M06;", out)
        self.assertNotIn("T5", out)
        self.assertEqual(out.count("T1M06"), 1)
        self.assertIn("修正", note)

    def test_existing_tool_call_without_m6_gets_m6_in_place(self):
        # 已有 Tn 但缺 M6：在原行补 M6，不删除不重新插入。
        text = 'MSG("PROGRAM:P")\nN1T1\nN2S100M03\nN3M30\n'
        tools = [ToolInfo(1, "10")]
        out, changed, note = add_initial_tool_change(text, tools, self._cfg(auto_tool_change=True))
        self.assertTrue(changed)
        self.assertIn("N1T1M6", out)
        self.assertNotIn("\nT1M6", "\n" + out)
        self.assertIn("T1M6", note)

    def test_initial_tool_change_ignores_comment_tool_refs(self):
        text = "%\nN2T2M06\n(T2 备用)\nN4G1X10\nM30\n"
        config = Config(auto_tool_change=True, g00_level="allow", require_end_marker=False)
        result, changed, note = add_initial_tool_change(text, [ToolInfo(1, "10")], config)
        self.assertTrue(changed)
        self.assertIn("(T2 备用)", result)
        self.assertNotIn("(T1 备用)", result)

    def test_separate_output_keeps_input(self):
        root = self.make_dir(); out = self.make_dir()
        src = root / "x_AG6D311A0101.MPF"
        src.write_text('MSG("PROGRAM:AG6D311A0101")\nN1S1000M03\nN2M30\n', encoding="utf-8")
        cfg = self._cfg()
        plan = build_plan(scan_directory(str(root), cfg), DEFAULT_INFO, cfg)
        report = process_plan(plan, str(out), cfg)
        self.assertEqual(report.success, 1)
        self.assertTrue(src.exists())
        self.assertTrue((out / "AG6D311A0101.MPF").exists())

    def test_duplicate_target_uses_newest_mpf_and_removes_older_source(self):
        root = self.make_dir()
        old = root / "old_P.MPF"
        new = root / "new_P.MPF"
        old.write_text("N1X1S1000M03\nN2M30\n", encoding="utf-8")
        new.write_text("N1X9S1000M03\nN2M30\n", encoding="utf-8")
        os.utime(old, (1000, 1000))
        os.utime(new, (2000, 2000))
        cfg = self._cfg()
        plan = build_plan(scan_directory(str(root), cfg), DEFAULT_INFO, cfg)
        older = next(f for f in plan.files if f.source == old.name)
        newest = next(f for f in plan.files if f.source == new.name)
        self.assertEqual(older.action, "duplicate")
        self.assertTrue(newest.overwrite_target)
        report = process_plan(plan, str(root), cfg)
        self.assertEqual(report.failed, 0)
        self.assertFalse(old.exists())
        self.assertFalse(new.exists())
        output = (root / "P.MPF").read_text(encoding="utf-8")
        self.assertIn("X9", output)
        self.assertNotIn("X1S", output)

    def test_reports_keep_latest_three(self):
        root = self.make_dir()
        for index in range(5):
            report = ProcessReport(str(root), str(root), "start")
            save_timestamped_report(report, root, now=datetime(2026, 1, 1, 0, 0, index))
        reports = sorted(root.glob("ncodeprocess-report-*.json"))
        self.assertEqual(len(reports), 3)
        self.assertEqual([p.name for p in reports], [
            "ncodeprocess-report-20260101_000002.json",
            "ncodeprocess-report-20260101_000003.json",
            "ncodeprocess-report-20260101_000004.json",
        ])

    def test_process_plan_reports_progress(self):
        root = self.make_dir()
        (root / "A.MPF").write_text("%\nN1G1X0Y0Z0F1000S5000\nM30\n", encoding="utf-8")
        config = self._cfg(require_end_marker=False)
        scan = build_plan(scan_directory(str(root), config), DEFAULT_INFO, config)
        progress = []
        process_plan(scan, str(root), config, progress_callback=lambda done, total, name: progress.append((done, total, name)))
        self.assertEqual(progress[-1][0], progress[-1][1])
        self.assertEqual(progress[-1][2], "A.MPF")
        self.assertEqual([done for done, _total, _name in progress], list(range(1, len(progress) + 1)))

    def test_process_plan_backup_preserves_original_files(self):
        root = self.make_dir()
        original_mpf = "%\nN1G1X0Y0Z0F1000S5000M03\nM30\n"
        (root / "A.MPF").write_text(original_mpf, encoding="utf-8")
        (root / "a.LOG").write_text("log-data", encoding="utf-8")
        config = self._cfg(require_end_marker=False)
        scan = build_plan(scan_directory(str(root), config), DEFAULT_INFO, config)
        report = process_plan(scan, str(root), config, backup=True)
        backups = list((root / "backup").rglob("*"))
        backup_mpf = next(p for p in backups if p.name == "A.MPF")
        backup_log = next(p for p in backups if p.name == "a.LOG")
        self.assertEqual(backup_mpf.read_text(encoding="utf-8"), original_mpf)
        self.assertEqual(backup_log.read_text(encoding="utf-8"), "log-data")
        self.assertTrue(report.backup_dir)
        self.assertTrue(Path(report.backup_dir).is_dir())

    def test_process_plan_without_backup_creates_no_backup_dir(self):
        root = self.make_dir()
        (root / "A.MPF").write_text("%\nN1G1X0Y0Z0F1000S5000M03\nM30\n", encoding="utf-8")
        config = self._cfg(require_end_marker=False)
        scan = build_plan(scan_directory(str(root), config), DEFAULT_INFO, config)
        report = process_plan(scan, str(root), config, backup=False)
        self.assertEqual(report.backup_dir, "")
        self.assertFalse((root / "backup").exists())

    def test_scan_directory_warns_when_directory_is_readonly(self):
        root = self.make_dir()
        (root / "A.MPF").write_text("%\nM30\n", encoding="utf-8")
        with patch("ncodeprocess.core.os.access", return_value=False):
            scan = scan_directory(str(root), self._cfg(require_end_marker=False))
        self.assertTrue(any("只读" in warning for warning in scan.warnings))

    def test_decode_identifies_gb2312_gbk_and_gb18030(self):
        # GB2312 只含常用汉字；GBK 覆盖更多汉字；GB18030 支持 4 字节扩展区。
        self.assertEqual(_decode("你好".encode("gb2312"))[1], "gb2312")
        self.assertEqual(_decode("镕".encode("gbk"))[1], "gbk")
        self.assertEqual(_decode("\U00020000".encode("gb18030"))[1], "gb18030")
        # é 后接空格不构成合法 GBK/GB2312 双字节，应回退到 cp1252
        self.assertEqual(_decode("é ".encode("cp1252"))[1], "cp1252")

    def test_decode_rejects_nul_bytes(self):
        with self.assertRaises(UnicodeDecodeError):
            _decode(b"%\x00N1G1X0Y0\nM30\n")

    def test_forced_gbk_and_gb2312_decode(self):
        text, used = _decode("你好".encode("gb2312"), forced="gbk")
        self.assertEqual(text, "你好")
        self.assertEqual(used, "gbk")
        text, used = _decode("你好".encode("gb2312"), forced="gb2312")
        self.assertEqual(text, "你好")
        self.assertEqual(used, "gb2312")

    def test_scan_records_detected_encoding(self):
        root = self.make_dir()
        (root / "A.MPF").write_bytes('MSG("PROGRAM:A")\n镕\nM30\n'.encode("gbk"))
        scan = scan_directory(str(root), self._cfg())
        plan = self._mpf(scan)
        self.assertEqual(plan.encoding, "gbk")

    def test_scan_marks_nul_mpf_as_encoding_error(self):
        root = self.make_dir()
        (root / "A.MPF").write_bytes(b"%\x00N1G1X0\nM30\n")
        scan = scan_directory(str(root), self._cfg())
        plan = self._mpf(scan)
        self.assertTrue(any(issue.kind == "encoding" for issue in plan.issues))

    def test_scan_classifies_permission_and_io_errors(self):
        root = self.make_dir()
        (root / "A.MPF").write_text("%\nM30\n", encoding="utf-8")
        with patch("ncodeprocess.core._read_text_cached", side_effect=PermissionError("denied")):
            scan = scan_directory(str(root), self._cfg(require_end_marker=False))
        plan = self._mpf(scan)
        self.assertEqual(plan.issues[0].kind, "permission")
        with patch("ncodeprocess.core._read_text_cached", side_effect=OSError("io fail")):
            scan = scan_directory(str(root), self._cfg(require_end_marker=False))
        plan = self._mpf(scan)
        self.assertEqual(plan.issues[0].kind, "io")

    def test_process_plan_records_error_kind(self):
        root = self.make_dir()
        (root / "A.MPF").write_text("%\nN1G1X0Y0Z0F1000S5000M03\nM30\n", encoding="utf-8")
        config = self._cfg(require_end_marker=False)
        scan = build_plan(scan_directory(str(root), config), DEFAULT_INFO, config)
        with patch("ncodeprocess.core._atomic_write", side_effect=PermissionError("denied")):
            report = process_plan(scan, str(root), config)
        self.assertEqual(report.files[0]["status"], "failed")
        self.assertEqual(report.files[0].get("error_kind"), "permission")

    def test_report_records_file_encoding(self):
        root = self.make_dir()
        (root / "A.MPF").write_text("%\nN1G1X0Y0Z0F1000S5000M03\nM30\n", encoding="utf-8")
        config = self._cfg(require_end_marker=False)
        scan = build_plan(scan_directory(str(root), config), DEFAULT_INFO, config)
        report = process_plan(scan, str(root), config)
        self.assertEqual(report.files[0].get("encoding"), "utf-8")

    def test_apt_drawing_candidates_from_filename_and_productname(self):
        text = (
            "$$ FILENAME  D0354F31311-201.CATProcess\n"
            "$$ PRODUCTNAME    NCSetup_M-D0354F31311-201_11.47.18\n"
        )
        self.assertEqual(extract_drawing_candidates(text), [
            ("APT FILENAME", "D0354F31311-201"),
            ("APT PRODUCTNAME", "M-D0354F31311-201"),
        ])

    def test_apt_meta_extracts_header_and_process_records(self):
        # WP-A1：APT 头部元数据（机床/后处理表/版本/操作）与加工参数（冷却/主轴/进给/装夹）。
        root = self.make_dir()
        apt = root / "x.aptsource"
        apt.write_text(
            "$$     Generated on 2026年7月31日 9:30:05\n"
            "$$     CATIA APT VERSION 1.0\n"
            "$$ PP-TABLE : HPM1150U.PPTable\n"
            "$$ FILENAME  D0354F31311-201.CATProcess\n"
            "$$ OPERATE   Part Operation.1\n"
            "$$ MACHIN    3-axis Machine.1\n"
            "PPRINT PROGNAME AG6D311A0101\n"
            "COOLNT/ON\n"
            "$$ OPERATION NAME : Tool Change.1\n"
            "$$ OPERATION NAME : Roughing.3\n"
            "CUTTER/ 20.000000, 3.000000\n"
            "TOOLNO/1, 20.000000, 3.000000,, 120.000000,$\n"
            "LOADTL/1,1\n"
            "SPINDL/ 5000.0000,RPM,CLW\n"
            "FEDRAT/ 3000.0000,MMPM\n"
            "FEDRAT/ 6000.0000,MMPM\n",
            encoding="utf-8",
        )
        meta = _extract_apt_data_cached(apt)[0]
        self.assertEqual(meta.machine, "3-axis Machine.1")
        self.assertEqual(meta.pp_table, "HPM1150U.PPTable")
        self.assertEqual(meta.catia_version, "1.0")
        self.assertEqual(meta.operations, ["Tool Change.1", "Roughing.3"])
        self.assertEqual(meta.coolant, ["ON"])
        self.assertEqual(meta.spindles, [("5000.0000", "RPM", "CLW")])
        self.assertEqual(meta.feeds, [("3000.0000", "MMPM"), ("6000.0000", "MMPM")])
        self.assertEqual(meta.tool_loads, [1])
        self.assertTrue(any(tool["number"] == 1 for tool in meta.tools))
        self.assertTrue(any("20.000" in (tool.get("dia") or "") for tool in meta.tools))

    def test_apt_meta_parses_transform_matrix(self):
        # WP-A1：$$ 位姿矩阵行解析为浮点序列。
        text = "$$    -0.99863    -0.05232     0.00137 18984.32985\n"
        apt = self.make_dir() / "m.aptsource"
        apt.write_text(text, encoding="utf-8")
        meta = _extract_apt_data_cached(apt)[0]
        self.assertEqual(len(meta.transform or []), 4)
        self.assertAlmostEqual(meta.transform[3], 18984.32985)

    def test_apt_meta_operation_grouping_and_program_name(self):
        # WP-A1 扩展：$$ 程序名行提取；SPINDL/FEDRAT 按 OPERATION NAME 上下文分组。
        apt = self.make_dir() / "g.aptsource"
        apt.write_text(
            "$$ AG6D311A0101\n"
            "$$ OPERATION NAME : Roughing.3\n"
            "SPINDL/ 5000.0000,RPM,CLW\n"
            "FEDRAT/ 3000.0000,MMPM\n"
            "FEDRAT/ 6000.0000,MMPM\n"
            "$$ OPERATION NAME : Finishing.1\n"
            "SPINDL/ 8000.0000,RPM,CLW\n",
            encoding="utf-8",
        )
        meta = _extract_apt_data_cached(apt)[0]
        self.assertEqual(meta.program_name, "AG6D311A0101")
        self.assertEqual(meta.operation_feeds["Roughing.3"], [("3000.0000", "MMPM"), ("6000.0000", "MMPM")])
        self.assertEqual(meta.operation_spindles["Finishing.1"], [("8000.0000", "RPM", "CLW")])

    def test_toolpath_stats_streaming(self):
        # WP-A2：GOTO 点数/XYZ 行程/圆弧数/抬刀次数（自适应平面）。
        apt = self.make_dir() / "t.aptsource"
        apt.write_text(
            "GOTO / 0.0, 0.0, 100.0\n"
            "GOTO / 1.0, 1.0, -1.0\n"
            "GOTO / 2.0, 1.0, -2.0\n"
            "GOTO / 3.0, 2.0, 100.0\n"
            "GOTO / 4.0, 2.0, -1.0\n"
            "TLON,GOFWD/ (CIRCLE/ 1.0, 2.0, 3.0,$\n",
            encoding="utf-8",
        )
        stats = _extract_apt_data_cached(apt)[1]
        self.assertEqual(stats.goto_count, 5)
        self.assertAlmostEqual(stats.min_x, 0.0)
        self.assertAlmostEqual(stats.max_x, 4.0)
        self.assertAlmostEqual(stats.min_z, -2.0)
        self.assertAlmostEqual(stats.max_z, 100.0)
        self.assertEqual(stats.arc_count, 1)
        self.assertAlmostEqual(stats.retract_plane, 100.0)
        self.assertEqual(stats.retract_count, 2)  # 两段连续高 Z

    def test_retract_plane_uses_most_frequent_high_plane(self):
        # WP-A2：抬刀平面 = 最高重复面邻带内出现最多的 Z 值（孤立高点不参与）。
        apt = self.make_dir() / "p.aptsource"
        apt.write_text(
            "GOTO / 0,0,110\n" +
            "GOTO / 0,0,100\n" * 3 +
            "GOTO / 0,0,0\n" * 4,
            encoding="utf-8",
        )
        stats = _extract_apt_data_cached(apt)[1]
        self.assertAlmostEqual(stats.retract_plane, 100.0)

    def test_toolpath_native_rapid_priority(self):
        # WP-A2：含 RAPID/GOHOME 原生标记时按标记计数。
        apt = self.make_dir() / "r.aptsource"
        apt.write_text(
            "RAPID\nGOTO / 0,0,100\nGOTO / 0,0,0\n"
            "RAPID\nGOTO / 1,1,100\nGOHOME\n",
            encoding="utf-8",
        )
        stats = _extract_apt_data_cached(apt)[1]
        self.assertEqual(stats.retract_count, 3)

    def test_recount_retracts_custom_height(self):
        # WP-A2：按自定义抬刀高度重算次数（供手动修订）。
        apt = self.make_dir() / "h.aptsource"
        apt.write_text(
            "GOTO / 0,0,100\nGOTO / 0,0,100\nGOTO / 0,0,20\n"
            "GOTO / 0,0,-1\nGOTO / 0,0,100\n",
            encoding="utf-8",
        )
        self.assertEqual(recount_retracts(apt, 100.0), 2)
        self.assertEqual(recount_retracts(apt, 50.0), 2)
        self.assertEqual(recount_retracts(apt, 150.0), 0)

    def test_apt_toolpath_cached_by_mtime(self):
        # WP-A2：轨迹统计按 (mtime, size, encoding) 缓存，文件变化后重新解析。
        # 缓存入口为合并单遍解析 _extract_apt_data_cached（元数据/轨迹/刀具共用）。
        apt = self.make_dir() / "c.aptsource"
        apt.write_text("GOTO / 0,0,10\nGOTO / 0,0,20\n", encoding="utf-8")
        first = _extract_apt_data_cached(apt)
        second = _extract_apt_data_cached(apt)
        self.assertIs(first, second)
        apt.write_text("GOTO / 0,0,10\n", encoding="utf-8")
        third = _extract_apt_data_cached(apt)
        self.assertEqual(third[1].goto_count, 1)

    def test_build_plan_attaches_apt_toolpath(self):
        # WP-A2：build_plan 把最新 APT 的元数据/轨迹/源路径挂到对应 MPF 计划。
        root = self.make_dir()
        (root / "x_P.MPF").write_text('MSG("PROGRAM:P")\nN1S1000M03\nN2M30\n', encoding="utf-8")
        (root / "x_P_I.aptsource").write_text(
            "$$ MACHIN 3-axis Machine.1\n"
            "GOTO / 0.0, 0.0, 10.0\n"
            "GOTO / 10.0, 0.0, -1.0\n",
            encoding="utf-8",
        )
        cfg = self._cfg()
        plan = build_plan(scan_directory(str(root), cfg), DEFAULT_INFO, cfg)
        mpf = self._mpf(plan)
        self.assertIsNotNone(mpf.apt_toolpath)
        self.assertEqual(mpf.apt_toolpath.goto_count, 2)
        self.assertIsNotNone(mpf.apt_meta)
        self.assertEqual(mpf.apt_meta.machine, "3-axis Machine.1")
        self.assertTrue((mpf.apt_source_path or "").endswith("x_P_I.aptsource"))

    def test_report_apt_summary_aggregation(self):
        # WP-A3：报告顶层 apt_summary 聚合机床/转速/刀具/操作/使用次数，files[] 含 APT 字段。
        root = self.make_dir()
        (root / "x_P.MPF").write_text('MSG("PROGRAM:P")\nN1S1000M03\nN2M30\n', encoding="utf-8")
        (root / "x_P_I.aptsource").write_text(
            "$$ MACHIN 3-axis Machine.1\n$$ OPERATION NAME : Roughing.3\n"
            "SPINDL/ 1000.0000,RPM,CLW\nFEDRAT/ 500.0000,MMPM\nLOADTL/1\n"
            "GOTO / 0.0, 0.0, 10.0\nGOTO / 10.0, 0.0, -1.0\n",
            encoding="utf-8",
        )
        cfg = self._cfg()
        plan = build_plan(scan_directory(str(root), cfg), DEFAULT_INFO, cfg)
        report = process_plan(plan, str(root), cfg)
        self.assertEqual(report.apt_summary["machines"], ["3-axis Machine.1"])
        self.assertEqual(report.apt_summary["spindle_speeds"], [1000.0])
        self.assertEqual(report.apt_summary["tool_loads"], [1])
        self.assertIn("Roughing.3", report.apt_summary["operations"])
        self.assertEqual(report.apt_summary["tool_usage"][1], 1)
        item = report.files[0]
        self.assertEqual(item["apt_meta"]["machine"], "3-axis Machine.1")
        self.assertEqual(item["toolpath_stats"]["goto_count"], 2)

    def test_crosscheck_spindle_direction_error(self):
        # WP-A4：APT CLW 规划 + 正文 M04 → 主轴方向 error。
        meta = AptMeta(spindles=[("5000.0000", "RPM", "CLW")])
        issues = crosscheck_apt('MSG("PROGRAM:P")\nN1S5000M04\nN2M30\n', meta, "P.MPF", self._cfg(auto_m03=False))
        direction = [i for i in issues if i.kind == "apt-spindle-direction" and i.severity == "error"]
        self.assertTrue(direction)
        self.assertIn("M04", direction[0].text)

    def test_crosscheck_dual_direction_suggests_keep(self):
        # WP-A4：正文同时含 M03/M04 时按 APT 方向给出保留/删除建议。
        meta = AptMeta(spindles=[("5000.0000", "RPM", "CLW")])
        issues = crosscheck_apt('MSG("PROGRAM:P")\nN1S5000M03M04\nN2M30\n', meta, "P.MPF", self._cfg(auto_m03=False))
        direction = [i for i in issues if i.kind == "apt-spindle-direction"]
        self.assertTrue(direction)
        self.assertIn("保留 M03", direction[0].suggestion)

    def test_crosscheck_tolerances_and_missing(self):
        # WP-A4：全部匹配不报 apt-*；S/F 越界、冷却缺失、装夹缺失报 warning。
        meta = AptMeta(spindles=[("5000.0000", "RPM", "CLW")], feeds=[("3000.0000", "MMPM")],
                       coolant=["ON"], tool_loads=[1, 2])
        issues = crosscheck_apt(
            'MSG("PROGRAM:P")\nN1T1M06\nN2T2M06\nN3S5000M03M08\nN4G1X10F3000\nN5M30\n',
            meta, "P.MPF", self._cfg(auto_m03=False))
        self.assertFalse(any(i.kind.startswith("apt-") for i in issues))
        issues = crosscheck_apt('MSG("PROGRAM:P")\nN1S9000M03\nN2G1X10F9999\nN3M30\n',
                                meta, "P.MPF", self._cfg(auto_m03=False))
        kinds = {i.kind for i in issues}
        self.assertTrue({"apt-spindle-mismatch", "apt-feed-mismatch", "apt-coolant-missing", "apt-tool-load-mismatch"} <= kinds)
        feed = next(i for i in issues if i.kind == "apt-feed-mismatch")
        self.assertIn("F9999", feed.text)
        # 加工参数（S/F）不符为 warning，冷却/装夹等符合性不符为提示（info）。
        self.assertEqual(next(i for i in issues if i.kind == "apt-spindle-mismatch").severity, "warning")
        self.assertEqual(next(i for i in issues if i.kind == "apt-feed-mismatch").severity, "warning")
        self.assertEqual(next(i for i in issues if i.kind == "apt-coolant-missing").severity, "info")
        self.assertEqual(next(i for i in issues if i.kind == "apt-tool-load-mismatch").severity, "info")

    def test_crosscheck_tool_param_program_name_and_date(self):
        # WP-A4：刀具几何参数、程序名冲突、DATE 过期均报 warning。
        meta = AptMeta(program_name="P", generated_at="2026年7月31日 9:30:05")
        issues = crosscheck_apt(
            'MSG("PROGRAM:Q")\nMSG("DATE:Jul 30 09:00:00 2026")\nMSG("T1:DIA=10.000,TOOL_CONER=1.000")\nN1T1M06\nN2M30\n',
            meta, "P.MPF", self._cfg(auto_m03=False),
            apt_tools=[ToolInfo(1, "12.000", "1.000")],
        )
        kinds = {i.kind for i in issues}
        self.assertTrue({"apt-program-name-conflict", "apt-date-stale", "apt-tool-param-mismatch"} <= kinds)
        program = next(i for i in issues if i.kind == "apt-program-name-conflict")
        self.assertIn("PROGRAM", program.text)
        date = next(i for i in issues if i.kind == "apt-date-stale")
        self.assertIn("DATE", date.text)
        tool = next(i for i in issues if i.kind == "apt-tool-param-mismatch")
        self.assertIn("T1", tool.text)
        self.assertEqual(tool.severity, "warning")
        self.assertEqual(program.severity, "warning")
        self.assertEqual(date.severity, "info")

    def test_crosscheck_missing_side_shows_apt_records(self):
        # WP-A4：APT 有而 MPF 没有时，原始文本显示 APT 规划记录，建议列写 APT 做法。
        meta = AptMeta(spindles=[("5000.0000", "RPM", "CLW")], feeds=[("3000.0000", "MMPM")],
                       coolant=["ON"], tool_loads=[1])
        issues = crosscheck_apt('MSG("PROGRAM:P")\nN1G1X10\nN2M30\n', meta, "P.MPF", self._cfg(auto_m03=False))
        by_kind = {i.kind: i for i in issues}
        self.assertIn("SPINDL", by_kind["apt-spindle-mismatch"].text)
        self.assertIn("APT 规划", by_kind["apt-spindle-mismatch"].suggestion)
        self.assertIn("FEDRAT", by_kind["apt-feed-mismatch"].text)
        self.assertIn("COOLNT", by_kind["apt-coolant-missing"].text)
        self.assertIn("LOADTL", by_kind["apt-tool-load-mismatch"].text)

    def test_date_prefers_apt_generated_time(self):
        # WP-A4：有 APT 时头部 DATE 采用 APT 生成时间（即使程序发生变更也保持）。
        root = self.make_dir()
        (root / "P.MPF").write_text('%\nN1S5000M03\nN2M30\n%\n', encoding="utf-8")
        (root / "x_P_I.aptsource").write_text(
            "$$     Generated on 2026年7月31日 9:30:05\nSPINDL/ 5000.0000,RPM,CLW\nGOTO / 0,0,10\n",
            encoding="utf-8",
        )
        cfg = self._cfg()
        plan = build_plan(scan_directory(str(root), cfg), DEFAULT_INFO, cfg)
        mpf = self._mpf(plan)
        self.assertIn('MSG("DATE:Jul 31 09:30:05 2026")', mpf.output_text or "")

    def test_apt_meta_cached_by_mtime(self):
        # WP-A1：元数据按 (mtime, size, encoding) 缓存，文件变化后重新解析。
        # 缓存入口为合并单遍解析 _extract_apt_data_cached（元数据/轨迹/刀具共用）。
        root = self.make_dir()
        apt = root / "c.aptsource"
        apt.write_text("$$ MACHIN  A\n", encoding="utf-8")
        first = _extract_apt_data_cached(apt)
        second = _extract_apt_data_cached(apt)
        self.assertIs(first, second)
        apt.write_text("$$ MACHIN  B\n", encoding="utf-8")
        third = _extract_apt_data_cached(apt)
        self.assertEqual(third[0].machine, "B")

    def test_scan_exposes_apt_drawing_candidates_without_applying(self):
        root = self.make_dir()
        (root / "x_P.aptsource").write_text(
            "$$ FILENAME  D0354F31311-201.CATProcess\n$$ PRODUCTNAME NCSetup_M-D0354F31311-201_11.47.18\n",
            encoding="utf-8",
        )
        result = scan_directory(str(root), Config())
        self.assertEqual(result.drawing_candidates, [
            ("APT FILENAME", "D0354F31311-201"),
            ("APT PRODUCTNAME", "M-D0354F31311-201"),
        ])

    def test_scan_exposes_existing_mpf_drawing_as_candidate(self):
        root = self.make_dir()
        (root / "P.MPF").write_text(
            'MSG("PROGRAM:P")\nMSG("DRAWING NUMBER:D0354F31311-201")\nN1S100M03\nN2M30\n',
            encoding="utf-8",
        )
        result = scan_directory(str(root), Config())
        self.assertIn(("MPF提取", "D0354F31311-201"), result.drawing_candidates)

    def test_files_outside_supported_four_extensions_are_ignored(self):
        root = self.make_dir()
        (root / "x_P.MPF").write_text("N1S100M03\nN2M30\n", encoding="utf-8")
        legacy_data = root / "NCPostProcessData"
        legacy_data.mkdir()
        (legacy_data / "ignored.MPF").write_text("N1M30\n", encoding="utf-8")
        (root / "run.bat").write_text("echo ignored", encoding="utf-8")
        (root / "notes.txt").write_text("ignored", encoding="utf-8")
        (root / "NCodeProcess.exe").write_bytes(b"ignored")
        result = scan_directory(str(root), Config())
        self.assertEqual([Path(item.source).suffix.lower() for item in result.files], [".mpf"])

    def test_feed_and_spindle_validation_and_g00_stats(self):
        # 分段对比：F1800 在两个段内多次出现，F25 罕见且远离其他段 → 离群警告。
        text = ("MSG(\"PROGRAM:P\")\nG1Z100F3000\nG1Z10F300\nG1X1F1800\nG1X2F1800\n"
                "G1X3F1800\nG1X4F1800\nG1Z100F3000\nG1Z5F300\nG1X5F25\n"
                "S5000\nS6000\nG00 X1\nM30\n")
        info = ProgramInfo("A", "B", "D", "V", "M", "S", "DATE")
        _stats, issues, _feed = analyze_program(text, "P.MPF", "P", info, self._cfg())
        kinds = {issue.kind for issue in issues}
        self.assertIn("feed-outlier", kinds)
        self.assertIn("multiple-spindle-speeds", kinds)
        stats = calculate_stats(text)
        self.assertEqual(stats.g00_count, 1)

    def test_zero_feed_is_error(self):
        text = "MSG(\"PROGRAM:P\")\nF0\nM30\n"
        issues = validate_program(text, "P.MPF", "P", ProgramInfo("A", "B", "D", "V", "M", "S", "DATE"), self._cfg())
        zero = [issue for issue in issues if issue.kind == "feed-zero"]
        self.assertEqual(len(zero), 1)
        self.assertEqual(zero[0].severity, "error")

    def test_tool_call_without_header_tool_number_warns(self):
        # FR-07.2: 正文 T 调用应对应头部 Tn MSG；头部无定义时警告。
        text = 'MSG("PROGRAM:P")\nMSG("T1:DIA=10.000")\nN2T5M06\nN4G1X10F1000S5000M03\nN6M30\n'
        issues = validate_program(text, "P.MPF", "P", DEFAULT_INFO, self._cfg())
        missing = [issue for issue in issues if issue.kind == "tool-number-missing"]
        self.assertEqual(len(missing), 1)
        self.assertIn("T5", missing[0].suggestion)
        self.assertEqual(missing[0].severity, "warning")

    def test_defined_tool_number_does_not_warn(self):
        text = 'MSG("PROGRAM:P")\nMSG("T1:DIA=10.000")\nN2T1M06\nN4G1X10F1000S5000M03\nN6M30\n'
        issues = validate_program(text, "P.MPF", "P", DEFAULT_INFO, self._cfg())
        self.assertFalse(any(issue.kind == "tool-number-missing" for issue in issues))

    def test_isolated_feed_parameter_line_is_not_flagged(self):
        # 2026-08-05 用户决定：一行内只有 F/S 而无运动/辅助指令属正常设定行，不再报告。
        text = "N1G1X10F1000\nN2F3000\nN3S5000\nN4M30\n"
        issues = validate_program(text, "P.MPF", "P", DEFAULT_INFO, self._cfg())
        self.assertFalse(any(issue.kind == "isolated-parameter" for issue in issues))

    def test_mutually_exclusive_m_codes_in_same_block_error(self):
        text = "N1M03M05\nN2G1X10F1000\nN3M30\n"
        issues = validate_program(text, "P.MPF", "P", DEFAULT_INFO, self._cfg())
        conflicts = [issue for issue in issues if issue.kind == "mutually-exclusive-m"]
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0].severity, "error")


    def test_multiple_spindle_warn_can_be_disabled(self):
        # WP-10：多 S 值警告默认开启，可在 Config 中关闭。
        text = "MSG(\"PROGRAM:P\")\nN1G1X10F1000S5000M03\nN2X20S6000\nN3M30\n"
        issues = validate_program(text, "P.MPF", "P", DEFAULT_INFO, self._cfg())
        self.assertTrue(any(issue.kind == "multiple-spindle-speeds" for issue in issues))
        disabled = self._cfg(multiple_spindle_warn=False)
        issues = validate_program(text, "P.MPF", "P", DEFAULT_INFO, disabled)
        self.assertFalse(any(issue.kind == "multiple-spindle-speeds" for issue in issues))

    def test_duplicate_msg_field_is_reported_as_warning(self):
        # FR-04.2.4: when the same MSG key appears more than once, the first
        # record is kept and every duplicate must surface as a warning in the
        # issue list (visible in the GUI validation table and counted in the
        # report), not only as a textual change note.
        root = self.make_dir()
        (root / "x_P.MPF").write_text(
            'MSG("PROGRAM:P")\n'
            'MSG("PROGRAM:OTHER")\n'
            'N1G1X10S100M03\n'
            'N2M30\n'
            '%\n',
            encoding="utf-8",
        )
        cfg = self._cfg()
        plan = build_plan(scan_directory(str(root), cfg), DEFAULT_INFO, cfg)
        mpf = self._mpf(plan)
        duplicate = [issue for issue in mpf.issues if issue.kind == "duplicate-msg-field"]
        self.assertEqual(len(duplicate), 1)
        self.assertEqual(duplicate[0].severity, "warning")
        self.assertIn("PROGRAM", duplicate[0].suggestion)
        # The first occurrence is kept; the later duplicate is not applied.
        self.assertEqual(extract_header_fields(mpf.output_text)["PROGRAM"], "P")
        report = process_plan(plan, str(root), cfg)
        self.assertEqual(report.warnings, 1)

    def test_duplicate_tool_msg_field_detected_when_rows_not_replaced(self):
        # FR-04.2.4 duplicate detection also covers repeated tool MSG rows.
        # When the caller does not replace tools (no APT / no override), the
        # repeated Tn rows stay in the file and must be reported as warnings.
        text = (
            'MSG("PROGRAM:P")\n'
            'MSG("T1:DIA=10")\n'
            'MSG("T1:DIA=12")\n'
            'N1S100M03\n'
            'N2M30\n'
        )
        out, changes, issues = apply_header(
            text, "P", DEFAULT_INFO, self._cfg()
        )
        duplicates = [issue for issue in issues if issue.kind == "duplicate-msg-field"]
        self.assertEqual(len(duplicates), 1)
        self.assertEqual(duplicates[0].severity, "warning")
        self.assertIn("T1", duplicates[0].suggestion)
        self.assertTrue(any("重复头部字段 T1" in change for change in changes))

    def test_forced_encoding_reads_gb18030_file(self):
        root = self.make_dir()
        path = root / "x_P.MPF"
        path.write_bytes('MSG("PROGRAM:P")\nN1X1S100M03\nN2M30\n'.encode("gb18030"))
        plan = build_plan(scan_directory(str(root), Config(encoding="gb18030")),
                          DEFAULT_INFO,
                          Config(encoding="gb18030"))
        self.assertEqual(self._mpf(plan).program, "P")

    def test_delete_extensions_config_filters_cleanup_plan(self):
        root = self.make_dir()
        (root / "x_P.MPF").write_text("N1S100M03\nN2M30\n", encoding="utf-8")
        (root / "a.LOG").write_text("log", encoding="utf-8")
        (root / "b.MOAPTIndexes").write_text("idx", encoding="utf-8")
        result = scan_directory(str(root), Config(delete_extensions={".log"}))
        kinds = {f.kind for f in result.files}
        self.assertIn("intermediate", kinds)
        sources = [f.source for f in result.files if f.kind == "intermediate"]
        self.assertEqual(sources, ["a.LOG"])
        self.assertNotIn("b.MOAPTIndexes", [f.source for f in result.files])

    def test_end_marker_check_can_be_disabled(self):
        text = "MSG(\"PROGRAM:P\")\nN1S100M03\n"
        issues = validate_program(text, "P.MPF", "P", DEFAULT_INFO,
                                  Config(g00_level="allow", require_end_marker=False))
        self.assertFalse(any(i.kind == "end-marker" for i in issues))

    def test_m06_requirement_can_be_enabled(self):
        text = "MSG(\"PROGRAM:P\")\nN1T1\nN2S100M03\nN3M30\n"
        issues = validate_program(text, "P.MPF", "P", DEFAULT_INFO,
                                  Config(g00_level="allow", require_m06=True))
        self.assertTrue(any(i.kind == "tool-change" and i.severity == "error" for i in issues))

    def test_spindle_speed_requirement_can_be_enabled(self):
        text = "MSG(\"PROGRAM:P\")\nN1G1X1\nN2M30\n"
        issues = validate_program(text, "P.MPF", "P", DEFAULT_INFO,
                                  Config(g00_level="allow", require_spindle_speed=True, auto_m03=False))
        self.assertTrue(any(i.kind == "spindle-speed" and i.severity == "error" for i in issues))

    def test_allowed_name_pattern_controls_program_extraction(self):
        root = self.make_dir()
        path = root / "程序_P.MPF"
        path.write_text('MSG("PROGRAM:P")\nN1S100M03\nN2M30\n', encoding="utf-8")
        strict = Config(allowed_name_pattern=r"^[A-Za-z0-9]+$")
        plan = build_plan(scan_directory(str(root), strict), DEFAULT_INFO, strict)
        mpf = self._mpf(plan)
        self.assertEqual(mpf.program, "P")
        self.assertEqual(Path(mpf.target).name, "P.MPF")

    def test_custom_program_extensions_recognized(self):
        # 主程序扩展名可自定义：.NC/.txt 与 .MPF 一样被识别为主程序文件。
        root = self.make_dir()
        (root / "x_P.NC").write_text('MSG("PROGRAM:P")\nN1S100M03\nN2M30\n', encoding="utf-8")
        (root / "x_Q.txt").write_text('MSG("PROGRAM:Q")\nN1S200M03\nN2M30\n', encoding="utf-8")
        cfg = Config(g00_level="allow", program_extensions={".mpf", ".nc", ".txt"})
        plan = build_plan(scan_directory(str(root), cfg), DEFAULT_INFO, cfg)
        programs = sorted(f.program for f in plan.files if f.kind == "mpf")
        self.assertEqual(programs, ["P", "Q"])
        targets = sorted(Path(f.target).name for f in plan.files if f.kind == "mpf")
        self.assertEqual(targets, ["P.MPF", "Q.MPF"])

    def test_custom_program_extensions_default_only_mpf(self):
        root = self.make_dir()
        (root / "x_P.NC").write_text("N1S100M03\nN2M30\n", encoding="utf-8")
        result = scan_directory(str(root), Config())
        self.assertFalse(any(f.source.endswith(".NC") for f in result.files))

    def test_custom_output_extension_applied(self):
        root = self.make_dir()
        (root / "x_P.MPF").write_text('MSG("PROGRAM:P")\nN1S1000M03\nN2M30\n', encoding="utf-8")
        cfg = Config(g00_level="allow", program_output_extension=".NC")
        plan = build_plan(scan_directory(str(root), cfg), DEFAULT_INFO, cfg)
        mpf = self._mpf(plan)
        self.assertEqual(Path(mpf.target).name, "P.NC")
        report = process_plan(plan, str(root), cfg)
        self.assertEqual(report.success, 1)
        self.assertTrue((root / "P.NC").exists())
        self.assertFalse((root / "P.MPF").exists())

    def test_required_fields_can_omit_shhenhe(self):
        # SHENHE 从必填列表移除后，缺失的 SHENHE 头部不再报 required-field；
        # 默认配置（全部必填）下缺失仍报错，锁定现行为。
        text = 'MSG("PROGRAM:P")\nN1S100M03\nN2M30\n'
        info = DEFAULT_INFO
        omit_shenhe = [key for key, _label, _required in FIELD_ORDER if key != "SHENHE"]
        issues = validate_program(text, "P.MPF", "P", info, Config(g00_level="allow", required_fields=omit_shenhe))
        self.assertFalse(any(i.kind == "required-field" and "SHENHE" in i.suggestion for i in issues))
        default_issues = validate_program(text, "P.MPF", "P", info, self._cfg())
        self.assertTrue(any(i.kind == "required-field" and "SHENHE" in i.suggestion for i in default_issues))

    def test_required_fields_drive_header_insertion(self):
        # 字段移出必填列表且值为空时，apply_header 不插入空 MSG 行；
        # 默认配置下仍插入空 SHENHE 行，锁定现行为。
        text = 'MSG("PROGRAM:P")\nN1S100M03\nN2M30\n'
        info = ProgramInfo("A", "", "D", "V", "M", "C", "DATE")
        omit_shenhe = [key for key, _label, _required in FIELD_ORDER if key != "SHENHE"]
        out, _changes, _issues = apply_header(text, "P", info, Config(g00_level="allow", required_fields=omit_shenhe))
        self.assertNotIn('MSG("SHENHE:', out)
        out_default, _changes, _issues = apply_header(text, "P", info, self._cfg())
        self.assertIn('MSG("SHENHE:")', out_default)

    def test_standalone_m03_position_inserts_independent_row_with_s_present(self):
        # m03_position="standalone"：即使存在 S 转速，也只把独立 M03 行插到
        # 第一条切削/运动指令之前，不把 M03 附加到 S 所在的程序块。
        text = '%\nMSG("PROGRAM:P")\nN1T1\nN2G1X10S1000\nN3M30\n%\n'
        out, changed, _note = add_m03(text, Config(g00_level="allow", m03_position="standalone"))
        self.assertTrue(changed)
        self.assertNotIn("S1000M03", out)
        self.assertIn("M03\nN2G1X10S1000", out)
        self.assertEqual(out.count("M03"), 1)

    def test_after_s_position_is_default_and_attaches(self):
        # 默认 m03_position="after-s"：M03 紧贴首个 S 转速之后（锁定现行为）。
        text = '%\nMSG("PROGRAM:P")\nN1G1X10S1000\nN2M30\n%\n'
        out, changed, _note = add_m03(text, self._cfg())
        self.assertTrue(changed)
        self.assertIn("S1000M03", out)
        self.assertNotIn("\nM03\n", out)

    def test_standalone_m03_falls_back_when_no_motion_instruction(self):
        # 正文没有切削/运动指令时，独立行策略回退到第一条指令行前插入，
        # 且不把 M03 附加到 S 指令。
        text = '%\nMSG("PROGRAM:P")\nN1S1000\nN2M30\n%\n'
        out, changed, _note = add_m03(text, Config(g00_level="allow", m03_position="standalone"))
        self.assertTrue(changed)
        self.assertIn("M03\nN1S1000", out)
        self.assertNotIn("S1000M03", out)

    def test_feed_limits_check_both_ends(self):
        # F 值低于 feed_min 或高于 feed_max 都报 feed-range error。
        info = DEFAULT_INFO
        below = '%\nMSG("PROGRAM:P")\nN1G1X10F3S1000M03\nN2M30\n%\n'
        above = '%\nMSG("PROGRAM:P")\nN1G1X10F30000S1000M03\nN2M30\n%\n'
        with self.subTest(direction="below-min"):
            issues = validate_program(below, "P.MPF", "P", info, self._cfg(feed_min=100.0))
            self.assertTrue(any(i.kind == "feed-range" and i.severity == "error" for i in issues))
        with self.subTest(direction="above-max"):
            issues = validate_program(above, "P.MPF", "P", info, self._cfg(feed_max=20000.0))
            self.assertTrue(any(i.kind == "feed-range" and i.severity == "error" for i in issues))

    def test_spindle_limits_check_both_ends(self):
        text = '%\nMSG("PROGRAM:P")\nN1G1X10F100S5000M03\nN2M30\n%\n'
        info = DEFAULT_INFO
        below = validate_program(text, "P.MPF", "P", info, Config(g00_level="allow", spindle_min=6000.0))
        self.assertTrue(any(i.kind == "spindle-range" and i.severity == "error" for i in below))
        above = validate_program(text, "P.MPF", "P", info, Config(g00_level="allow", spindle_max=4000.0))
        self.assertTrue(any(i.kind == "spindle-range" and i.severity == "error" for i in above))

    def test_limits_none_do_not_report(self):
        # 未配置上下限（None）时不产生范围类问题，锁定可选关闭行为。
        text = '%\nMSG("PROGRAM:P")\nN1G1X10F3S5000M03\nN2M30\n%\n'
        issues = validate_program(text, "P.MPF", "P", DEFAULT_INFO,
                                  self._cfg(feed_min=None, feed_max=None, spindle_min=None, spindle_max=None))
        self.assertFalse(any(i.kind in ("feed-range", "spindle-range") for i in issues))

    def test_newline_policy_converts_and_preserves_source_style(self):
        def run(payload_bytes, cfg):
            root = self.make_dir()
            (root / "x_P.MPF").write_bytes(payload_bytes)
            plan = build_plan(scan_directory(str(root), cfg), DEFAULT_INFO, cfg)
            report = process_plan(plan, str(root), cfg)
            self.assertEqual(report.success, 1)
            return (root / "P.MPF").read_bytes()

        crlf = b'MSG("PROGRAM:P")\r\nN1S1000M03\r\nN2M30\r\n'
        lf = b'MSG("PROGRAM:P")\nN1S1000M03\nN2M30\n'
        with self.subTest(mode="lf-from-crlf"):
            data = run(crlf, Config(g00_level="allow", newline="lf"))
            self.assertNotIn(b"\r\n", data)
            self.assertIn(b"\n", data)
        with self.subTest(mode="crlf-from-lf"):
            data = run(lf, Config(g00_level="allow", newline="crlf"))
            self.assertIn(b"\r\n", data)
            self.assertNotIn(b"\n", data.replace(b"\r\n", b""))
        with self.subTest(mode="auto-preserves-crlf"):
            data = run(crlf, self._cfg())
            self.assertIn(b"\r\n", data)

    def test_aux_order_rules(self):
        cases = (
            ("m03-before-motion", "N1G1X10\nN2M03\nN3M30\n", True, "error"),
            ("m03-before-motion", "N1M03\nN2G1X10\nN3M30\n", False, None),
            ("m05-before-end", "N1M03\nN2M30\nN3M05\n", True, "warning"),
            ("m08-before-cut", "N1G1X10\nN2M08\nN3M30\n", True, "warning"),
            ("m09-before-end", "N1M03\nN2M30\nN3M09\n", True, "warning"),
            ("m09-before-end", "N1M03\nN2M30\n", False, None),
        )
        for rule, body, expected, severity in cases:
            with self.subTest(rule=rule, body=body[:12]):
                text = '%\nMSG("PROGRAM:P")\n' + body + '%\n'
                issues = validate_program(text, "P.MPF", "P", DEFAULT_INFO,
                                          Config(g00_level="allow", aux_checks={rule}))
                found = [i for i in issues if i.kind == "aux-order"]
                if expected:
                    self.assertTrue(any(i.severity == severity for i in found))
                else:
                    self.assertFalse(found)

    def test_aux_checks_empty_disables_all(self):
        # 未启用任何顺序规则时（默认）不产生 aux-order，锁定默认行为。
        text = '%\nMSG("PROGRAM:P")\nN1G1X10\nN2M30\nN3M05\nN4M08\n%\n'
        issues = validate_program(text, "P.MPF", "P", DEFAULT_INFO,
                                  self._cfg())
        self.assertFalse(any(i.kind == "aux-order" for i in issues))

    def test_m04_blocks_auto_m03_and_reports_direction_error(self):
        # WP-A1：正文以 M04 反转启动主轴时，禁止自动补写 M03，并报方向错误。
        cfg = self._cfg(auto_m03=True, m03_position="after-s")
        text = "N10 S5000 M04;\nN20 X10 Y10 F500;\n"
        out, changed, note = add_m03(text, cfg)
        self.assertFalse(changed)
        self.assertNotIn("M03", out)
        issues = validate_program(out, "t.MPF", "T", DEFAULT_INFO, cfg)
        direction = [i for i in issues if i.kind == "spindle-direction"]
        self.assertTrue(direction)
        self.assertEqual(direction[0].severity, "error")

    def test_m03_and_m04_same_block_is_mutually_exclusive(self):
        # WP-A1：同一程序段同时包含 M03 与 M04，主轴正转与反转互斥。
        cfg = self._cfg(auto_m03=False)
        issues = validate_program("N10 S5000 M03 M04;\n", "t.MPF", "T", DEFAULT_INFO, cfg)
        self.assertTrue(any(i.kind == "mutually-exclusive-m" for i in issues))

    def test_initial_tool_change_skipped_when_multiple_tools_configured(self):
        # WP-A2：刀具列表 >1 时跳过自动换刀改写，并返回跳过提示。
        tools = [ToolInfo(1, "10"), ToolInfo(2, "10")]
        cfg = self._cfg(auto_tool_change=True)
        text = "N10 G90;\nT2M6;\nN20 X10 Y10 F500;\nN30 M30;\n"
        out, changed, note = add_initial_tool_change(text, tools, cfg)
        self.assertFalse(changed)
        self.assertNotIn("T1M6", out)
        self.assertIn("多把刀具", note)

    def test_auto_tool_change_skipped_warning_when_multiple_t_references(self):
        # WP-A2：正文引用多个 T 号时，启用自动换刀会给出警告。
        cfg = self._cfg(auto_tool_change=True)
        text = '%\nMSG("PROGRAM:P")\nN10 T1M6\nN20 G1X10F500\nN30 T2M6\nN40 M30\n%\n'
        issues = validate_program(text, "P.MPF", "P", DEFAULT_INFO, cfg)
        self.assertTrue(any(i.kind == "auto-tool-change-skipped" and i.severity == "warning" for i in issues))

    def test_build_plan_marks_auto_tool_change_skipped_reason(self):
        # WP-A2：build_plan 记录多刀跳过原因，note 进入 changes（预览/报告可见）。
        root = self.make_dir()
        (root / "P.MPF").write_bytes(
            'MSG("PROGRAM:P")\nMSG("T1:DIA=10.")\nMSG("T2:DIA=12.")\n'
            'N1T1M6\nN2G1X10F500\nN3T2M6\nN4M30\n'.encode("utf-8"))
        cfg = self._cfg(auto_tool_change=True)
        plan = build_plan(scan_directory(str(root), cfg), DEFAULT_INFO, cfg)
        mpf = self._mpf(plan)
        self.assertTrue(mpf.auto_tool_change_skipped)
        self.assertTrue(any("多把刀具" in change for change in mpf.changes))

    def test_m06_inside_comment_does_not_satisfy_requirement(self):
        # WP-B3：括号注释与分号注释中的 M06 均不满足 require_m06 检查。
        cfg = self._cfg(require_m06=True)
        for body in (
            "N10 T1;\nN20 (M06);\nN30 M30;\n",   # 括号注释
            "N10 T1;\nN20 ; M06\nN30 M30;\n",    # 分号注释
        ):
            with self.subTest(body=body):
                issues = validate_program(body, "t.MPF", "T", DEFAULT_INFO, cfg)
                self.assertTrue(any(i.kind == "tool-change" for i in issues))

    def test_real_m06_outside_comment_satisfies_requirement(self):
        cfg = self._cfg(require_m06=True)
        issues = validate_program("N10 T1;\nN20 M06;\nN30 M30;\n", "t.MPF", "T", DEFAULT_INFO, cfg)
        self.assertFalse(any(i.kind == "tool-change" for i in issues))

    def test_write_csv_emits_header_and_issue_rows(self):
        report = ProcessReport("in", "out", "start")
        report.files = [{
            "file": "A.MPF",
            "action": "keep",
            "issues": [{"file": "A.MPF", "line": 3, "text": "N3", "kind": "feed-zero", "severity": "error", "suggestion": "修正 F0"}],
        }]
        root = self.make_dir()
        path = root / "report.csv"
        report.write_csv(path)
        content = path.read_text(encoding="utf-8-sig")
        lines = content.splitlines()
        self.assertEqual(lines[0], "file,line,text,kind,severity,suggestion")
        self.assertIn("A.MPF,3,N3,feed-zero,error,修正 F0", lines[1])

    def test_extract_program_name_priority_and_suffix_rules(self):
        from ncodeprocess.core import _program_name_and_source
        root = self.make_dir()
        cases = (
            ('MSG("PROGRAM:FROM_MSG")\n', "from-msg.MPF", "FROM_MSG"),
            ("PPRINT PROGNAME FROM_PPRINT\n", "x.MPF", "FROM_PPRINT"),
            ("", "prefix_AG6D311A0101.MPF", "AG6D311A0101"),
            ("", "AG6D311A0101_I.MPF", "AG6D311A0101"),
        )
        for text, name, expected in cases:
            with self.subTest(name=name):
                path = root / name
                path.write_text(text, encoding="utf-8")
                self.assertEqual(_program_name_and_source(path, text)[0], expected)

    def test_program_field_updates_when_overwrite_enabled(self):
        # WP-B2：PROGRAM 不保护，勾选覆盖时头部 PROGRAM 与程序名对齐。
        text = 'MSG("PROGRAM:OLD")\nMSG("NC MACHINE:2500B")\nN1S100M03\nN2M30\n'
        info = ProgramInfo("A", "B", "D", "V", "2500B", "SIE840D", "DATE")
        out, _changes, _issues = apply_header(text, "NEW", info, Config(overwrite_fields=True))
        self.assertIn('MSG("PROGRAM:NEW")', out)

    def test_nc_machine_and_control_system_never_overwritten(self):
        # WP-B2：NC MACHINE/CONTROL SYSTEM 已有非空值即使勾选覆盖也不改。
        text = 'MSG("NC MACHINE:CUSTOM")\nMSG("CONTROL SYSTEM:CUSTOM_CTRL")\nN1S100M03\nN2M30\n'
        info = ProgramInfo("A", "B", "D", "V", "NEW_MACHINE", "NEW_CTRL", "DATE")
        out, _changes, _issues = apply_header(text, "P", info, Config(overwrite_fields=True))
        self.assertIn('MSG("NC MACHINE:CUSTOM")', out)
        self.assertIn('MSG("CONTROL SYSTEM:CUSTOM_CTRL")', out)

    def test_date_auto_updates_when_program_changes(self):
        # WP-B2：程序发生实际变更时 DATE 自动更新为变更发生时间。
        root = self.make_dir()
        (root / "P.MPF").write_bytes(
            'MSG("PROGRAM:P")\nMSG("DATE:OLD_DATE")\nMSG("BIANZHI:")\nN1S100M03\nN2M30\n'.encode("utf-8"))
        cfg = self._cfg()
        plan = build_plan(scan_directory(str(root), cfg), DEFAULT_INFO, cfg)
        mpf = self._mpf(plan)
        self.assertIn("更新 DATE", mpf.changes)
        self.assertIn(format_nc_date(), mpf.output_text)

    def test_date_unchanged_when_no_program_changes(self):
        # WP-B2：程序无变更时 DATE 保留原值。
        root = self.make_dir()
        (root / "P.MPF").write_bytes(
            'MSG("PROGRAM:P")\nMSG("BIANZHI:A")\nMSG("SHENHE:B")\nMSG("DRAWING NUMBER:D")\nMSG("PART VERSION:V")\n'
            'MSG("NC MACHINE:M")\nMSG("CONTROL SYSTEM:C")\nMSG("DATE:OLD_DATE")\nN1S100M03\nN2M30\n'.encode("utf-8"))
        cfg = self._cfg()
        plan = build_plan(scan_directory(str(root), cfg), DEFAULT_INFO, cfg)
        mpf = self._mpf(plan)
        self.assertNotIn("更新 DATE", mpf.changes)
        self.assertIn("OLD_DATE", mpf.output_text)

    def test_max_files_limit_stops_scan(self):
        # WP-C1：扫描文件数量超过上限时停止并提示。
        root = self.make_dir()
        for index in range(4):
            (root / f"P{index}.MPF").write_bytes(f'MSG("PROGRAM:P{index}")\nN1M30\n'.encode("utf-8"))
        cfg = Config(g00_level="allow", max_files=2)
        result = scan_directory(str(root), cfg)
        self.assertLessEqual(len(result.files), 2)
        self.assertTrue(any("文件数量超过上限" in warning for warning in result.warnings))

    def test_max_file_size_skips_oversized_mpf(self):
        # WP-C1：超过单文件大小上限的 MPF 跳过并报 file-too-large。
        root = self.make_dir()
        (root / "BIG.MPF").write_bytes(b"X" * 2000)
        (root / "OK.MPF").write_bytes('MSG("PROGRAM:OK")\nN1M30\n'.encode("utf-8"))
        cfg = Config(g00_level="allow", max_file_size=1024)
        result = scan_directory(str(root), cfg)
        big = next(f for f in result.files if f.source == "BIG.MPF")
        self.assertEqual(big.action, "error")
        self.assertTrue(any(i.kind == "file-too-large" for i in big.issues))
        self.assertTrue(any(f.source == "OK.MPF" for f in result.files))

    def test_recursive_scan_skips_nested_aptsource_directories(self):
        # WP-C5：递归扫描时任意深度的 aptsource/NCodeProcessData 目录均被忽略。
        root = self.make_dir()
        (root / "P.MPF").write_bytes('MSG("PROGRAM:P")\nN1M30\n'.encode("utf-8"))
        nested = root / "sub" / "aptsource"
        nested.mkdir(parents=True)
        (nested / "Q.MPF").write_bytes('MSG("PROGRAM:Q")\nN1M30\n'.encode("utf-8"))
        data = root / "a" / "b" / "NCodeProcessData"
        data.mkdir(parents=True)
        (data / "R.MPF").write_bytes('MSG("PROGRAM:R")\nN1M30\n'.encode("utf-8"))
        cfg = Config(g00_level="allow", recursive=True)
        result = scan_directory(str(root), cfg)
        sources = {f.source for f in result.files}
        self.assertIn("P.MPF", sources)
        self.assertNotIn(str((nested / "Q.MPF").relative_to(root)), sources)
        self.assertNotIn(str((data / "R.MPF").relative_to(root)), sources)

class RuntimeLogTests(CoreTestBase):
    def test_emit_records_event_with_level_event_and_message(self):
        reset_runtime_log()
        emit_event("info", "scan_start", "开始扫描目录：测试目录")
        snapshot = runtime_log().snapshot()
        self.assertEqual(len(snapshot), 1)
        entry = snapshot[0]
        self.assertEqual(entry["level"], "info")
        self.assertEqual(entry["event"], "scan_start")
        self.assertEqual(entry["message"], "开始扫描目录：测试目录")
        self.assertTrue(entry["time"])
        self.assertIn("detail", entry)

    def test_ring_buffer_keeps_latest_and_appends_truncation_notice(self):
        log = RuntimeLog(max_events=5)
        for index in range(7):
            log.emit("info", "process_file", f"文件 {index}")
        snapshot = log.snapshot()
        self.assertEqual(len(snapshot), 6)  # 5 条保留 + 1 条截断说明
        self.assertEqual(snapshot[0]["message"], "文件 2")
        notice = snapshot[-1]
        self.assertEqual(notice["level"], "warning")
        self.assertEqual(notice["event"], "warning")
        self.assertIn("已截断", notice["message"])

    def test_snapshot_truncation_warning_appears_once(self):
        # WP-F2：截断说明仅在丢弃数变化时追加一次，多次 snapshot/导出不重复。
        log = RuntimeLog(max_events=2)
        for index in range(5):
            log.emit("info", "event", f"事件 {index}")
        first = log.snapshot()
        second = log.snapshot()
        count = lambda entries: sum(1 for e in entries if "已截断" in e["message"])
        self.assertEqual(count(first), 1)      # 首次 snapshot 上报截断
        self.assertEqual(count(second), 0)     # 已上报，第二次不再重复追加
        self.assertEqual(count(first) + count(second), 1)  # 全程仅出现一次

    def test_process_file_event_includes_key_data(self):
        # WP-F2：process_file 事件 detail 携带关键运行过程数据（动作/程序名/目标/统计）。
        reset_runtime_log()
        root = self.make_dir()
        (root / "P.MPF").write_bytes('MSG("PROGRAM:P")\nN1G1X1F100\nN2M30\n'.encode("utf-8"))
        cfg = Config(g00_level="allow")
        plan = build_plan(scan_directory(str(root), cfg), DEFAULT_INFO, cfg)
        process_plan(plan, str(root), cfg)
        entries = [e for e in runtime_log().snapshot() if e["event"] == "process_file"]
        self.assertTrue(entries)
        detail = entries[0]["detail"]
        self.assertIn("动作=keep", detail)
        self.assertIn("程序名=P", detail)
        self.assertIn("目标=", detail)

    def test_pipeline_emits_expected_events(self):
        reset_runtime_log()
        root = self.make_dir()
        (root / "P.MPF").write_bytes('MSG("PROGRAM:P")\nN1G1X1F100\nN2M30\n'.encode("utf-8"))
        cfg = Config(g00_level="allow")
        scan = scan_directory(str(root), cfg)
        plan = build_plan(scan, DEFAULT_INFO, cfg)
        process_plan(plan, str(root), cfg, backup=True)
        events = [entry["event"] for entry in runtime_log().snapshot()]
        for expected in ("scan_start", "scan_finish", "plan_built", "process_start",
                         "process_file", "process_finish", "backup_created"):
            self.assertIn(expected, events)

    def test_scan_warning_event_emitted_when_no_mpf(self):
        reset_runtime_log()
        root = self.make_dir()
        scan_directory(str(root), Config())
        events = [entry["event"] for entry in runtime_log().snapshot()]
        self.assertIn("scan_warning", events)

    def test_report_embeds_runtime_log_and_log_path(self):
        # WP-R4：报告内嵌 runtime_log，log_path 恒为空（不再生成磁盘日志文件）。
        reset_runtime_log()
        root = self.make_dir()
        (root / "P.MPF").write_bytes('MSG("PROGRAM:P")\nN1G1X1F100\nN2M30\n'.encode("utf-8"))
        cfg = Config(g00_level="allow")
        plan = build_plan(scan_directory(str(root), cfg), DEFAULT_INFO, cfg)
        report = process_plan(plan, str(root), cfg)
        self.assertTrue(report.runtime_log)
        self.assertEqual(report.log_path, "")

    def test_save_timestamped_report_emits_export_events(self):
        reset_runtime_log()
        report = ProcessReport("in", "out", "2026-08-05T10:00:00")
        save_timestamped_report(report, self.make_dir())
        events = [entry["event"] for entry in runtime_log().snapshot()]
        self.assertIn("export_start", events)
        self.assertIn("export_finish", events)

    def test_export_report_does_not_create_log_file(self):
        # WP-R4：导出报告只生成单个 JSON，不生成 logs 目录与磁盘日志文件。
        reset_runtime_log()
        report = ProcessReport("in", "out", "2026-08-05T10:00:00")
        root = self.make_dir()
        save_timestamped_report(report, root)
        self.assertEqual(report.log_path, "")
        self.assertFalse((root / "logs").exists())


class ReportMetadataTests(CoreTestBase):
    """报告内容规范第 12 节建议新增字段（顶层元数据 + files[].target/program_name_source）。"""

    def test_report_includes_section12_metadata(self):
        reset_runtime_log()
        root = self.make_dir()
        (root / "x_P.MPF").write_bytes('MSG("PROGRAM:P")\nN1G1X1F100\nN2M30\n'.encode("utf-8"))
        cfg = Config(g00_level="allow")
        scan = scan_directory(str(root), cfg)
        plan = build_plan(scan, DEFAULT_INFO, cfg)
        report = process_plan(plan, str(root), cfg, generator="cli", confirmations=["已确认：执行目录处理"])
        self.assertTrue(report.app_version)
        self.assertEqual(report.report_schema_version, 1)
        self.assertEqual(report.generator, "cli")
        self.assertGreaterEqual(report.elapsed_seconds, 0.0)
        self.assertEqual(report.scan_warnings, scan.warnings)
        self.assertEqual(report.archive_stamp, scan.archive_stamp)
        self.assertEqual(report.user_confirmations, ["已确认：执行目录处理"])
        for key in ("encoding", "g00_level", "m03_position", "newline", "aux_checks",
                    "multiple_spindle_warn", "require_end_marker",
                    "max_file_size", "max_files", "retract_z_threshold"):
            self.assertIn(key, report.config_snapshot)

    def test_config_snapshot_includes_wp_c1_c9_keys(self):
        # WP-R1：config_snapshot 补全 WP-C1/C9 新增配置键，保证结果可复现。
        root = self.make_dir()
        (root / "P.MPF").write_bytes('MSG("PROGRAM:P")\nN1G1X1F100\nN2M30\n'.encode("utf-8"))
        cfg = Config(g00_level="allow", max_file_size=2048, max_files=500,
                     retract_z_threshold=12.0, ask_backup=False)
        plan = build_plan(scan_directory(str(root), cfg), DEFAULT_INFO, cfg)
        report = process_plan(plan, str(root), cfg)
        for key in ("max_file_size", "max_files", "retract_z_threshold", "ask_backup"):
            self.assertIn(key, report.config_snapshot)
        self.assertEqual(report.config_snapshot["max_file_size"], 2048)
        self.assertEqual(report.config_snapshot["max_files"], 500)
        self.assertEqual(report.config_snapshot["retract_z_threshold"], 12.0)
        self.assertFalse(report.config_snapshot["ask_backup"])

    def test_report_files_include_target_and_program_name_source(self):
        root = self.make_dir()
        (root / "x_P.MPF").write_bytes('MSG("PROGRAM:P")\nN1G1X1F100\nN2M30\n'.encode("utf-8"))
        (root / "y_Q.MPF").write_bytes('N1G1X1F100\nN2M30\n'.encode("utf-8"))
        (root / "P.aptsource").write_text("APT", encoding="utf-8")
        cfg = Config(g00_level="allow", save_aptsource=True)
        plan = build_plan(scan_directory(str(root), cfg), DEFAULT_INFO, cfg)
        report = process_plan(plan, str(root), cfg)
        by_name = {item["program"]: item for item in report.files}
        self.assertEqual(by_name["P"]["program_name_source"], "MSG")
        self.assertTrue(by_name["P"]["target"].endswith("P.MPF"))
        self.assertEqual(by_name["Q"]["program_name_source"], "文件名")
        apt = next(item for item in report.files if item["action"] == "move")
        self.assertTrue(apt["target"].endswith(".aptsource"))

    def test_program_name_source_detects_pprint(self):
        root = self.make_dir()
        (root / "apt_named.MPF").write_bytes('PPRINT PROGNAME Z99\nN1G1X1F100\nN2M30\n'.encode("utf-8"))
        cfg = Config(g00_level="allow")
        plan = build_plan(scan_directory(str(root), cfg), DEFAULT_INFO, cfg)
        mpf = next(f for f in plan.files if f.kind == "mpf")
        self.assertEqual(mpf.program, "Z99")
        self.assertEqual(mpf.program_name_source, "PPRINT")

    def test_report_includes_environment_and_scan_stats(self):
        # 2026-08-08 报告完善：运行环境与扫描分类统计进入顶层。
        root = self.make_dir()
        (root / "x_P.MPF").write_bytes('MSG("PROGRAM:P")\nN1G1X1F100\nN2M30\n'.encode("utf-8"))
        (root / "P.aptsource").write_text("APT", encoding="utf-8")
        (root / "x.LOG").write_text("log", encoding="utf-8")
        cfg = Config(g00_level="allow")
        plan = build_plan(scan_directory(str(root), cfg), DEFAULT_INFO, cfg)
        report = process_plan(plan, str(root), cfg)
        self.assertIn("platform", report.environment)
        self.assertIn("python_version", report.environment)
        self.assertIn("machine", report.environment)
        self.assertEqual(report.scan_stats["mpf"], 1)
        self.assertEqual(report.scan_stats["aptsource"], 1)
        self.assertEqual(report.scan_stats["intermediate"], 1)
        self.assertEqual(report.scan_stats["total"], 3)

    def test_config_snapshot_includes_file_type_and_overwrite_keys(self):
        # 2026-08-08 报告完善：config_snapshot 补齐文件类型/覆盖/命名规则等缺失键。
        root = self.make_dir()
        (root / "P.MPF").write_bytes('MSG("PROGRAM:P")\nN1G1X1F100\nN2M30\n'.encode("utf-8"))
        cfg = Config(g00_level="allow", overwrite_existing=True,
                     delete_extensions={".log", ".tmp"},
                     program_extensions={".mpf", ".nc"},
                     program_output_extension=".NC",
                     aptsource_dir="archive",
                     allowed_name_pattern=r"^[A-Za-z0-9]+$")
        plan = build_plan(scan_directory(str(root), cfg), DEFAULT_INFO, cfg)
        report = process_plan(plan, str(root), cfg)
        snapshot = report.config_snapshot
        self.assertTrue(snapshot["overwrite_existing"])
        self.assertEqual(snapshot["delete_extensions"], [".log", ".tmp"])
        self.assertEqual(snapshot["program_extensions"], [".mpf", ".nc"])
        self.assertEqual(snapshot["program_output_extension"], ".NC")
        self.assertEqual(snapshot["aptsource_dir"], "archive")
        self.assertEqual(snapshot["allowed_name_pattern"], r"^[A-Za-z0-9]+$")

    def test_report_files_include_header_and_decision_fields(self):
        # 2026-08-08 报告完善：files[] 含处理后 MSG 头部快照与换刀/重复裁决信息。
        root = self.make_dir()
        (root / "x_P.MPF").write_bytes(
            'MSG("PROGRAM:P")\nMSG("DRAWING NUMBER:D-1")\nN1G1X1F100\nN2M30\n'.encode("utf-8"))
        cfg = Config(g00_level="allow")
        plan = build_plan(scan_directory(str(root), cfg), DEFAULT_INFO, cfg)
        report = process_plan(plan, str(root), cfg)
        item = report.files[0]
        self.assertEqual(item["header"].get("PROGRAM"), "P")
        self.assertEqual(item["header"].get("DRAWING NUMBER"), "D-1")
        self.assertEqual(item["auto_tool_change_skipped"], "")
        self.assertEqual(item["duplicate_winner"], "")
        self.assertEqual(item["duplicate_target"], "")


if __name__ == "__main__":
    unittest.main()


class FeedSegmentDetectionTests(CoreTestBase):
    """《F值异常检测方法》抬刀平面分段对比检测（2026-08-07 决策稿）。"""

    def _program(self, body, plane=100.0):
        """构造以 Z<plane> 为抬刀平面的多段程序文本。"""
        return body + "\nN99M30\n"

    def test_feed_segment_splits_by_retract_crossing(self):
        text = self._program(
            "N1G1Z100F6000\nN2G1Z10F300\nN3G1X1Y1F1800\nN4G1X2Y2\n"
            "N5G1Z100F6000\nN6G1Z5F300\nN7G1X3Y3F1800\nN8G1X4Y4\nN9G1Z100F6000\n")
        _issues, feed = detect_feed_outliers(text, "P.MPF", self._cfg())
        self.assertEqual(feed.safe_plane, 100.0)
        self.assertEqual([(seg["first_line"], seg["last_line"]) for seg in feed.segments],
                         [(1, 5), (6, 9)])

    def test_feed_segment_first_positioning_belongs_to_first_segment(self):
        text = self._program("N1G1Z100F6000\nN2G1Z10F300\nN3G1X1Y1F1800\nN4G1Z100F6000\n")
        _issues, feed = detect_feed_outliers(text, "P.MPF", self._cfg())
        self.assertEqual(len(feed.segments), 1)
        self.assertEqual(feed.segments[0]["first_line"], 1)

    def test_feed_rare_high_value_on_xy_row_is_warning(self):
        text = self._program(
            "N1G1Z100F6000\nN2G1Z10F300\nN3G1X1Y1F1800\nN4G1X2Y2F1800\n"
            "N5G1X3Y3F1800\nN6G1Z100F6000\nN7G1Z5F300\nN8G1X4Y4F66\nN9G1Z100F6000\n")
        issues, feed = detect_feed_outliers(text, "P.MPF", self._cfg())
        outliers = [i for i in issues if i.kind == "feed-outlier"]
        reviews = [i for i in issues if i.kind == "feed-review"]
        self.assertEqual([i.line for i in outliers], [8])
        self.assertEqual(outliers[0].severity, "warning")
        self.assertIn("F66", outliers[0].suggestion)
        self.assertEqual(reviews, [])
        self.assertEqual(feed.outliers[0]["level"], "warning")
        self.assertEqual(feed.outliers[0]["line"], 8)
        self.assertGreater(feed.outliers[0]["gap"], 0.6)

    def test_feed_moderate_gap_is_review_not_warning(self):
        text = self._program(
            "N1G1Z100F6000\nN2G1Z10F300\nN3G1X1Y1F1800\nN4G1X2Y2F1800\n"
            "N5G1X3Y3F1800\nN6G1Z100F6000\nN7G1Z5F300\nN8G1X4Y4F8888\nN9G1Z100F6000\n")
        issues, feed = detect_feed_outliers(text, "P.MPF", self._cfg())
        reviews = [i for i in issues if i.kind == "feed-review"]
        warnings = [i for i in issues if i.kind == "feed-outlier"]
        self.assertEqual([i.line for i in reviews], [8])
        self.assertEqual(reviews[0].severity, "info")
        self.assertIn("F8888", reviews[0].suggestion)
        self.assertEqual(warnings, [])
        self.assertEqual(feed.outliers[0]["level"], "review")
        self.assertLess(feed.outliers[0]["gap"], 0.6)

    def test_feed_axial_only_value_is_exempt(self):
        # F66 全部出现在纯 Z 运动行（有 Z、无 X/Y）→ 值无关豁免，不报异常。
        text = self._program(
            "N1G1Z100F6000\nN2G1Z10F300\nN3G1X1Y1F1800\nN4G1X2Y2F1800\n"
            "N5G1X3Y3F1800\nN6G1Z100F6000\nN7G1Z5F300\nN8G1Z-5F66\nN9G1Z-8\nN10G1Z100F6000\n")
        issues, feed = detect_feed_outliers(text, "P.MPF", self._cfg())
        self.assertFalse(any(i.kind in ("feed-outlier", "feed-review") for i in issues))
        self.assertEqual(feed.outliers, [])

    def test_feed_common_value_not_flagged(self):
        text = self._program(
            "N1G1Z100F6000\nN2G1Z10F300\nN3G1X1Y1F1800\nN4G1X2Y2F1800\n"
            "N5G1Z100F6000\nN6G1Z5F300\nN7G1X3Y3F1800\nN8G1X4Y4F1800\nN9G1Z100F6000\n")
        issues, feed = detect_feed_outliers(text, "P.MPF", self._cfg())
        self.assertFalse(any(i.kind in ("feed-outlier", "feed-review") for i in issues))
        self.assertEqual(feed.outliers, [])

    def test_feed_modal_inherited_rows_count_into_segment_and_global(self):
        # 模态继承计入段 F 集合（参与段间比较）；罕见按显式写入计数：
        # F450 只显式写一次，即使被后续行继承，仍按候选复核。
        text = self._program(
            "N1G1Z100F6000\nN2G1Z10F300\nN3G1X1Y1F1800\nN4G1X2Y2F1800\n"
            "N5G1X3Y3F1800\nN6G1Z100F6000\nN7G1Z5F300\nN8G1X4Y4F450\nN9G1X5Y5\n"
            "N10G1X6Y6\nN11G1Z100F6000\n")
        issues, feed = detect_feed_outliers(text, "P.MPF", self._cfg())
        reviews = [i for i in issues if i.kind == "feed-review"]
        self.assertEqual([i.line for i in reviews], [8])
        self.assertTrue(any(450.0 in seg["feeds"] for seg in feed.segments))

    def test_feed_inherited_anomaly_flagged_by_explicit_write_count(self):
        # 一次误写 F66 被后续多行继承：罕见按显式写入计 1 次，仍应告警（写入行）。
        text = self._program(
            "N1G1Z100F6000\nN2G1Z10F300\nN3G1X1Y1F1800\nN4G1X2Y2F1800\n"
            "N5G1X3Y3F1800\nN6G1Z100F6000\nN7G1Z5F300\nN8G1X4Y4F66\n"
            "N9G1X5Y5\nN10G1X6Y6\nN11G1X7Y7\nN12G1Z100F6000\n")
        issues, feed = detect_feed_outliers(text, "P.MPF", self._cfg())
        outliers = [i for i in issues if i.kind == "feed-outlier"]
        self.assertEqual([i.line for i in outliers], [8])
        self.assertEqual(feed.outliers[0]["count"], 1)

    def test_feed_value_straddling_segments_via_modal_inheritance_flagged(self):
        # 异常值写在抬刀平面穿越行、被下一段模态继承（真实事故形态）：
        # 从纯模态出现的段中剔除该值后仍应检出，报告定位到写入行。
        text = self._program(
            "N1G1Z100F3000\nN2G1Z10F300\nN3G1X1Y1F1800\nN4G1X2Y2F1800\n"
            "N5G1X3Y3F1800\nN6G1Z100F66\nN7G1X4Y4\nN8G1X5Y5\nN9G1Z100F6000\n")
        issues, feed = detect_feed_outliers(text, "P.MPF", self._cfg())
        outliers = [i for i in issues if i.kind == "feed-outlier"]
        self.assertEqual([i.line for i in outliers], [6])
        self.assertEqual(len(feed.segments), 2)

    def test_feed_rare_value_explicit_in_two_segments_not_flagged(self):
        # 合法罕见值在两个段都显式写入：参照中保留该值，gap=0 直接跳过，不误报。
        text = self._program(
            "N1G1Z100F6000\nN2G1Z10F300\nN3G1X1Y1F900\nN4G1Z100F6000\n"
            "N5G1Z5F300\nN6G1X3Y3F900\nN7G1Z100F6000\n")
        issues, feed = detect_feed_outliers(text, "P.MPF", self._cfg())
        self.assertFalse(any(i.kind in ("feed-outlier", "feed-review") for i in issues))
        self.assertEqual(feed.outliers, [])

    def test_feed_single_segment_cross_reference_flags_rare_far_value(self):
        # 单段程序有跨程序参照：平面行误写 F9000（非豁免）且远离常见档位 → 复核。
        text = self._program(
            "N1G1Z100F6000\nN2G1Z10F300\nN3G1X1Y1F900\nN4G1X2Y2F900\nN5G1Z100F9000\n")
        reference = [300.0, 600.0, 900.0, 1500.0, 3000.0, 5000.0, 6000.0]
        issues, feed = detect_feed_outliers(text, "P.MPF", self._cfg(),
                                            reference_feeds=reference)
        reviews = [i for i in issues if i.kind == "feed-review"]
        self.assertEqual([i.line for i in reviews], [5])
        self.assertEqual(feed.outliers[0]["reason"], "cross-program-gap")
        self.assertEqual(feed.reference_count, len(reference))

    def test_feed_single_segment_reference_common_value_not_flagged(self):
        # 单段程序的值本身是常见档位（在参照内）→ 不提示。
        text = self._program(
            "N1G1Z100F6000\nN2G1Z10F300\nN3G1X1Y1F900\nN4G1X2Y2F900\nN5G1Z100F6000\n")
        issues, feed = detect_feed_outliers(text, "P.MPF", self._cfg(),
                                            reference_feeds=[300.0, 900.0, 6000.0])
        self.assertFalse(any(i.kind in ("feed-outlier", "feed-review") for i in issues))
        self.assertEqual(feed.reference_count, 3)

    def test_feed_axial_plane_row_not_exempt(self):
        # 抬刀平面上的纯 Z 定位/抬刀行不属于下刀/钻入，不豁免。
        rows = [
            (1, 6000.0, 6000.0, 100.0, False, True),
            (2, 300.0, 300.0, 10.0, False, True),
            (3, 300.0, 300.0, 0.0, False, True),
            (4, 300.0, 300.0, -10.0, False, True),
            (5, 9000.0, 9000.0, 100.0, False, True),
        ]
        self.assertFalse(_axial_feed_exempt(rows, 9000.0, 100.0))
        self.assertTrue(_axial_feed_exempt(rows, 300.0, 100.0))

    def test_feed_axial_consistency_violation_not_exempt(self):
        # 浅处比深处慢（违反“越深越慢”）→ 轴向豁免不成立。
        rows_bad = [
            (1, 6000.0, 6000.0, 100.0, False, True),
            (2, 300.0, 300.0, 10.0, False, True),
            (3, 300.0, 300.0, 0.0, False, True),
            (4, 66.0, 66.0, -5.0, False, True),
            (5, 300.0, 300.0, -10.0, False, True),
        ]
        self.assertFalse(_axial_feed_exempt(rows_bad, 66.0, 100.0))
        # 同深不同速（违反“同深同速”）→ 轴向豁免不成立。
        rows_rep = [
            (1, 300.0, 300.0, 10.0, False, True),
            (2, 300.0, 300.0, -10.0, False, True),
            (3, 66.0, 66.0, -10.0, False, True),
        ]
        self.assertFalse(_axial_feed_exempt(rows_rep, 66.0, 100.0))

    def test_feed_build_reference_excludes_self_and_single_anomalies(self):
        # 跨程序参照：排除自身；只在单个程序出现的异常值不进参照。
        result = build_feed_reference([
            ("a.MPF", [300.0, 900.0, 6000.0]),
            ("b.MPF", [300.0, 1500.0, 6000.0]),
            ("c.MPF", [900.0, 1500.0, 9000.0]),
        ])
        self.assertEqual(result["a.MPF"], frozenset({1500.0}))
        self.assertEqual(result["b.MPF"], frozenset({900.0}))
        self.assertEqual(result["c.MPF"], frozenset({300.0, 6000.0}))
        self.assertNotIn(9000.0, result["a.MPF"])
        self.assertNotIn(300.0, result["a.MPF"])

    def test_feed_value_used_twice_is_still_candidate(self):
        text = self._program(
            "N1G1Z100F6000\nN2G1Z10F300\nN3G1X1Y1F1800\nN4G1X2Y2F1800\n"
            "N5G1X3Y3F1800\nN6G1Z100F6000\nN7G1Z5F300\nN8G1X4Y4F66\nN9G1X5Y5F66\nN10G1Z100F6000\n")
        issues, _feed = detect_feed_outliers(text, "P.MPF", self._cfg())
        outliers = [i for i in issues if i.kind == "feed-outlier"]
        self.assertEqual([i.line for i in outliers], [8, 9])

    def test_feed_single_segment_produces_distribution(self):
        text = self._program("N1G1Z100F6000\nN2G1Z10F300\nN3G1X1Y1F1800\nN4G1X2Y2\n")
        issues, feed = detect_feed_outliers(text, "P.MPF", self._cfg())
        self.assertFalse(any(i.kind in ("feed-outlier", "feed-review") for i in issues))
        self.assertEqual(len(feed.segments), 1)
        rows = {row["value"]: row for row in feed.distribution}
        self.assertEqual(rows[300.0]["count"], 1)
        self.assertEqual(rows[300.0]["first_line"], 2)
        self.assertIn("仅出现一次", rows[300.0]["note"])
        self.assertEqual(rows[1800.0]["count"], 2)
        self.assertEqual(rows[1800.0]["note"], "")

    def test_feed_safe_plane_uses_max_z_cluster_not_single_high(self):
        # 单个超高 Z1000 不参与抬刀平面；重复出现的 Z100 才是平面。
        text = self._program(
            "N1G1Z100F6000\nN2G1Z10F300\nN3G1X1Y1F1800\nN4G1Z1000F6000\nN5G1Z100F6000\n"
            "N6G1Z5F300\nN7G1X3Y3F1800\nN8G1Z100F6000\n")
        _issues, feed = detect_feed_outliers(text, "P.MPF", self._cfg())
        self.assertEqual(feed.safe_plane, 100.0)

    def test_feed_no_z_rows_returns_empty_data(self):
        text = "N1G1X1F500\nN2G1X2F500\nN3M30\n"
        issues, feed = detect_feed_outliers(text, "P.MPF", self._cfg())
        self.assertEqual(issues, [])
        self.assertIsNone(feed.safe_plane)
        self.assertEqual(feed.segments, [])
        self.assertEqual(feed.outliers, [])

    def test_feed_boundary_errors_collected_with_issues(self):
        text = self._program(
            "N1G1Z100F6000\nN2G1Z10F300\nN3G1X1Y1F1800\nN4G1X2Y2F1800\n"
            "N5G1X3Y3F1800\nN6G1Z100F6000\nN7G1Z5F300\nN8G1X4Y4F20000\nN9G1Z100F6000\n")
        _issues, feed = detect_feed_outliers(text, "P.MPF", self._cfg())
        self.assertTrue(any(item["value"] == 20000.0 for item in feed.boundary_errors))

    def test_feed_outlier_keeps_apt_context_flag(self):
        text = self._program(
            "N1G1Z100F6000\nN2G1Z10F300\nN3G1X1Y1F1800\nN4G1X2Y2F1800\n"
            "N5G1X3Y3F1800\nN6G1Z100F6000\nN7G1Z5F300\nN8G1X4Y4F15000\n"
            "N9G1X5Y5F1800\nN10G1Z100F6000\n")
        issues, feed = detect_feed_outliers(text, "P.MPF", self._cfg(),
                                            apt_feeds=[15000.0])
        reviews = [i for i in issues if i.kind == "feed-review"]
        self.assertEqual(len(reviews), 1)
        self.assertTrue(feed.outliers[0]["in_apt"])

    def test_feed_tolerance_is_fixed_thirty_percent(self):
        text = self._program("N1G1Z100F6000\nN2G1Z10F300\nN3G1X1Y1F1800\nN4G1Z100F6000\n")
        _issues, feed = detect_feed_outliers(text, "P.MPF", self._cfg())
        self.assertEqual(feed.tolerance, 0.30)

    def test_feed_normal_multi_gear_program_has_no_false_positive(self):
        # 多个合法档位在段内交替（各值全程序出现多次）→ 不报任何离群/复核。
        text = self._program(
            "N1G1Z100F6000\nN2G1Z10F300\nN3G1X1Y1F900\nN4G1X2Y2F1500\nN5G1X3Y3F900\n"
            "N6G1X4Y4F1500\nN7G1Z100F6000\nN8G1Z5F300\nN9G1X5Y5F900\nN10G1X6Y6F1500\n"
            "N11G1X7Y7F900\nN12G1Z100F6000\n")
        issues, feed = detect_feed_outliers(text, "P.MPF", self._cfg())
        self.assertFalse(any(i.kind in ("feed-outlier", "feed-review") for i in issues))
        self.assertEqual(feed.outliers, [])


    def test_feed_outlier_exposed_in_plan_and_report(self):
        # 过程数据进入报告：build_plan/process_plan 产出新的分段字段。
        text = self._program(
            "N1G1Z100F6000\nN2G1Z10F300\nN3G1X1Y1F1800\nN4G1X2Y2F1800\n"
            "N5G1X3Y3F1800\nN6G1Z100F6000\nN7G1Z5F300\nN8G1X4Y4F66\nN9G1Z100F6000\n")
        root = self.make_dir()
        (root / "x_P.MPF").write_text(text, encoding="utf-8")
        cfg = self._cfg()
        plan = build_plan(scan_directory(str(root), cfg), DEFAULT_INFO, cfg)
        plan_file = next(item for item in plan.files if item.kind == "mpf")
        self.assertEqual(plan_file.feed_outlier.safe_plane, 100.0)
        self.assertEqual(len(plan_file.feed_outlier.segments), 2)
        report = process_plan(plan, str(root), cfg)
        item = next(item for item in report.files if item["file"] == "x_P.MPF")
        data = item["feed_outlier"]
        self.assertEqual(data["safe_plane"], 100.0)
        self.assertEqual(data["outliers"][0]["value"], 66.0)
