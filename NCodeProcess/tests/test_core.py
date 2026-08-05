import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from ncodeprocess.core import Config, FIELD_ORDER, FilePlan, ProcessReport, ProgramInfo, RuntimeLog, ToolInfo, _decode, add_initial_tool_change, add_m03, align_lines, apply_header, build_plan, calculate_stats, code_part, emit_event, extract_drawing_candidates, extract_header_fields, extract_tools, format_nc_date, process_plan, program_defaults, reprocess_file, reset_runtime_log, runtime_log, save_timestamped_report, scan_directory, validate_program

# 绝大多数测试共用的编制/审核/图号/版次/机床/控制系统/日期默认值。
DEFAULT_INFO = ProgramInfo("A", "B", "D", "V", "M", "C", "DATE")


class CoreTests(unittest.TestCase):
    def make_dir(self):
        return Path(tempfile.mkdtemp(prefix="ncodeprocess-"))

    @staticmethod
    def _cfg(**overrides):
        """默认放开 G00 检查，并按需覆盖其它配置。"""
        return Config(g00_level="allow", **overrides)

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
        self.assertIn('MSG("NC MACHINE:2500B")', mpf.output_text)
        self.assertIn('MSG("CONTROL SYSTEM:SIE840D")', mpf.output_text)
        report = process_plan(plan, str(root), cfg)
        self.assertEqual(report.success, 1)
        self.assertEqual(report.moved, 1)
        self.assertEqual(report.deleted, 1)
        self.assertTrue((root / "AG6D311A0101.MPF").exists())
        self.assertTrue(list((root / "aptsource").glob("*/*AG6D311A0101.aptsource")))

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
        lines = out.splitlines()
        body_index = next(i for i, line in enumerate(lines) if line.startswith("T1M6"))
        self.assertTrue(lines[body_index - 1].startswith('MSG("T1:'))
        self.assertEqual(out.count("T1M6"), 1)
        self.assertIn("N3T1;", out)
        self.assertNotIn("T5", out)

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
        src.write_text('MSG("PROGRAM:AG6D311A0101")\nN1S100M03\nN2M30\n', encoding="utf-8")
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
        old.write_text("N1X1S100M03\nN2M30\n", encoding="utf-8")
        new.write_text("N1X9S100M03\nN2M30\n", encoding="utf-8")
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
        self.assertEqual(extract_drawing_candidates(text), [("APT FILENAME", "D0354F31311-201")])

    def test_scan_exposes_apt_drawing_candidates_without_applying(self):
        root = self.make_dir()
        (root / "x_P.aptsource").write_text(
            "$$ FILENAME  D0354F31311-201.CATProcess\n$$ PRODUCTNAME NCSetup_M-D0354F31311-201_11.47.18\n",
            encoding="utf-8",
        )
        result = scan_directory(str(root), Config())
        self.assertEqual(result.drawing_candidates, [("APT提取", "D0354F31311-201")])

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
        text = "MSG(\"PROGRAM:P\")\nF2000\nF2500\nF1800\nF25\nS5000\nS6000\nG00 X1\nM30\n"
        info = ProgramInfo("A", "B", "D", "V", "M", "S", "DATE")
        issues = validate_program(text, "P.MPF", "P", info, self._cfg())
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

    def test_feed_outlier_high_and_low_detection(self):
        def body(feed):
            return "\n".join(f"N{i}G1X{i}F{feed}" for i in range(1, 6))

        cases = (
            (body(3000) + "\nN6G1X60F15000\nN7M30\n", 1),   # 高值检出
            (body(20000) + "\nN6G1X60F25000\nN7M30\n", 0),  # 主体本身上万不误报
            (body(300) + "\nN6G1X60F5\nN7M30\n", 1),        # 小进给程序低值检出
            (body(300) + "\nN6G1X60F1500\nN7M30\n", 1),     # 小进给程序高值检出
        )
        for text, expected in cases:
            with self.subTest(feed=text.split("F")[1].split("\n")[0]):
                issues = validate_program(text, "P.MPF", "P", DEFAULT_INFO, self._cfg())
                self.assertEqual(len([i for i in issues if i.kind == "feed-outlier"]), expected)

    def test_feed_outlier_ignores_high_frequency_second_mode(self):
        # 样例多模态：抬刀档位 F5000 出现多次属正常，不因相对中位数偏高误报；
        # 单次出现且超出常见档位范围的值才报。
        body = [900] * 70 + [5000] * 10
        lines = [f"N{i}G1X{i}F{v}" for i, v in enumerate(body, start=1)]
        text = "\n".join(lines) + "\nN99G1X99F3500\nN100M30\n"
        issues = validate_program(text, "P.MPF", "P", DEFAULT_INFO, self._cfg())
        self.assertFalse(any(issue.kind == "feed-outlier" for issue in issues))  # 3500 落在常见档位范围内

        text = "\n".join(lines) + "\nN99G1X99F20000\nN100M30\n"
        issues = validate_program(text, "P.MPF", "P", DEFAULT_INFO, self._cfg())
        self.assertTrue(any(issue.kind == "feed-outlier" for issue in issues))  # 20000 超出常见档位 1.5 倍

        text = "\n".join(lines) + "\nN99G1X99F20\nN100M30\n"
        issues = validate_program(text, "P.MPF", "P", DEFAULT_INFO, self._cfg())
        self.assertTrue(any(issue.kind == "feed-outlier" for issue in issues))  # 20 低于常见档位 0.03 倍

    def test_feed_outlier_detects_low_value_in_cut_stage(self):
        # 300~6000 场景：进刀 F300、切削 F900、移动 F5000 均高频属正常；
        # 切削段孤立 F20 应检出（全体统计会因最低常见档位 300 而漏报）。
        lines, idx = [], 1
        for _ in range(20):
            lines.append(f"N{idx}G1X1Y1Z-10F900")
            idx += 1
        for _ in range(10):
            lines.append(f"N{idx}Z100F5000")
            idx += 1
        for _ in range(8):
            lines.append(f"N{idx}G1Z-5F300")
            idx += 1
        text = "\n".join(lines) + f"\nN{idx}G1X1Y1Z-10F20\nN{idx+1}M30\n"
        issues = validate_program(text, "P.MPF", "P", DEFAULT_INFO, self._cfg())
        outliers = [issue for issue in issues if issue.kind == "feed-outlier"]
        self.assertEqual(len(outliers), 1)
        self.assertIn("F20", outliers[0].text)

    def test_feed_outlier_low_ratio_configurable(self):
        # 低值离群按主体中位数比例（默认 10%）动态判定，比例可在 Config 调整。
        body = "\n".join(f"N{i}G1X{i}F3000" for i in range(1, 6))
        text = body + "\nN6G1X60F50\nN7M30\n"
        issues = validate_program(text, "P.MPF", "P", DEFAULT_INFO, self._cfg())
        self.assertEqual(len([i for i in issues if i.kind == "feed-outlier"]), 1)  # 50 < 3000×0.1
        relaxed = self._cfg(feed_outlier_low_ratio=0.01)
        issues = validate_program(text, "P.MPF", "P", DEFAULT_INFO, relaxed)
        self.assertFalse(any(issue.kind == "feed-outlier" for issue in issues))  # 50 > 3000×0.01

    def test_feed_outlier_ratio_configurable(self):
        body = "\n".join(f"N{i}G1X{i}F3000" for i in range(1, 6))
        text = body + "\nN6G1X60F50\nN7M30\n"       # 50 < 3000×0.1
        self.assertEqual(len([i for i in validate_program(text, "P.MPF", "P", DEFAULT_INFO, self._cfg()) if i.kind == "feed-outlier"]), 1)
        relaxed = self._cfg(feed_outlier_low_ratio=0.01)
        self.assertFalse(any(i.kind == "feed-outlier" for i in validate_program(text, "P.MPF", "P", DEFAULT_INFO, relaxed)))

        text = body + "\nN6G1X60F15000\nN7M30\n"    # 15000 ≥ 3000×3
        self.assertEqual(len([i for i in validate_program(text, "P.MPF", "P", DEFAULT_INFO, self._cfg()) if i.kind == "feed-outlier"]), 1)
        raised = self._cfg(feed_outlier_high_ratio=6.0)
        self.assertFalse(any(i.kind == "feed-outlier" for i in validate_program(text, "P.MPF", "P", DEFAULT_INFO, raised)))

    def test_feed_outlier_uses_iqr_for_dispersed_distributions(self):
        # IQR 箱线图法：分布集中时即使未达中位数 3 倍也检出；均匀分散时不再误报。
        concentrated = "\n".join(f"N{i}G1X{i}F{1000 + i * 100}" for i in range(1, 6))  # 1100~1500
        text = concentrated + "\nN6G1X60F3000\nN7M30\n"
        issues = validate_program(text, "P.MPF", "P", DEFAULT_INFO, self._cfg())
        outliers = [issue for issue in issues if issue.kind == "feed-outlier"]
        self.assertEqual(len(outliers), 1)  # IQR 上界 1700，3000 为离群（中位数 3 倍比例法会漏报）

        spread = "\n".join(f"N{i}G1X{i}F{i * 1000}" for i in range(1, 6))  # 1000~5000
        text = spread + "\nN6G1X60F5500\nN7M30\n"
        issues = validate_program(text, "P.MPF", "P", DEFAULT_INFO, self._cfg())
        self.assertFalse(any(issue.kind == "feed-outlier" for issue in issues))  # 均匀分布内不误报

        text = concentrated + "\nN6G1X60F100\nN7M30\n"
        issues = validate_program(text, "P.MPF", "P", DEFAULT_INFO, self._cfg())
        outliers = [issue for issue in issues if issue.kind == "feed-outlier"]
        self.assertEqual(len(outliers), 1)  # IQR 下界 900，100 为离群

    def test_feed_outlier_iqr_factor_configurable(self):
        # IQR 倍数（默认 1.5）可在 Config 调整，放大后不再误报。
        concentrated = "\n".join(f"N{i}G1X{i}F{1000 + i * 100}" for i in range(1, 6))
        text = concentrated + "\nN6G1X60F3000\nN7M30\n"
        issues = validate_program(text, "P.MPF", "P", DEFAULT_INFO, self._cfg())
        self.assertTrue(any(issue.kind == "feed-outlier" for issue in issues))
        widened = self._cfg(feed_outlier_iqr_factor=10.0)
        issues = validate_program(text, "P.MPF", "P", DEFAULT_INFO, widened)
        self.assertFalse(any(issue.kind == "feed-outlier" for issue in issues))  # 上界 3400 > 3000

    def test_feed_outlier_tolerates_wide_normal_swing_ranges(self):
        # 用户真实数据：300~6000、800~8000、30~300 的正常波动不得误报；
        # 只有明显超出波动范围的值才提示（Tukey 极端值标准 k=3 + 分位数兜底）。
        def program(values):
            lines = [f"N{i}G1X{i}F{value}" for i, value in enumerate(values, start=1)]
            return "\n".join(lines) + "\nN99M30\n"

        wide = list(range(300, 6001, 300))   # 300~6000
        issues = validate_program(program(wide + [7000]), "P.MPF", "P", DEFAULT_INFO, self._cfg())
        self.assertFalse(any(issue.kind == "feed-outlier" for issue in issues))
        issues = validate_program(program(wide + [20000]), "P.MPF", "P", DEFAULT_INFO, self._cfg())
        self.assertTrue(any(issue.kind == "feed-outlier" for issue in issues))

        mid = list(range(800, 8001, 800))    # 800~8000
        issues = validate_program(program(mid + [9000]), "P.MPF", "P", DEFAULT_INFO, self._cfg())
        self.assertFalse(any(issue.kind == "feed-outlier" for issue in issues))
        issues = validate_program(program(mid + [20000]), "P.MPF", "P", DEFAULT_INFO, self._cfg())
        self.assertTrue(any(issue.kind == "feed-outlier" for issue in issues))

        small = list(range(30, 301, 30))     # 30~300
        issues = validate_program(program(small + [500]), "P.MPF", "P", DEFAULT_INFO, self._cfg())
        self.assertFalse(any(issue.kind == "feed-outlier" for issue in issues))
        issues = validate_program(program(small + [900]), "P.MPF", "P", DEFAULT_INFO, self._cfg())
        self.assertTrue(any(issue.kind == "feed-outlier" for issue in issues))

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
        (root / "x_P.MPF").write_text('MSG("PROGRAM:P")\nN1S100M03\nN2M30\n', encoding="utf-8")
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
        # 未配置上下限（None）时不产生范围类问题，锁定默认行为。
        text = '%\nMSG("PROGRAM:P")\nN1G1X10F3S5000M03\nN2M30\n%\n'
        issues = validate_program(text, "P.MPF", "P", DEFAULT_INFO,
                                  self._cfg())
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
        from ncodeprocess.core import extract_program_name
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
                self.assertEqual(extract_program_name(path, text), expected)

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

    def test_retract_z_threshold_configurable(self):
        # WP-C9：抬刀高度阈值可配置；默认 20，低于阈值的正 Z 归切削阶段。
        body = "\n".join(f"N{i}G1X{i}Y{i}Z-10F1000" for i in range(1, 9))
        text = body + "\nN9G1X1Y1Z12F8000\nN10M30\n"
        # 默认阈值 20：Z12 归切削阶段，8000 为孤立高值 → 报警告。
        issues = validate_program(text, "P.MPF", "P", DEFAULT_INFO, self._cfg())
        self.assertTrue(any(i.kind == "feed-outlier" for i in issues))
        # 阈值 10：Z12 归移动阶段，移动组仅 1 个样本（跳过）→ 不报。
        issues = validate_program(text, "P.MPF", "P", DEFAULT_INFO, self._cfg(retract_z_threshold=10.0))
        self.assertFalse(any(i.kind == "feed-outlier" for i in issues))


class RuntimeLogTests(unittest.TestCase):
    def make_dir(self):
        return Path(tempfile.mkdtemp(prefix="ncodeprocess-log-"))

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


class ReportMetadataTests(unittest.TestCase):
    """报告内容规范第 12 节建议新增字段（顶层元数据 + files[].target/program_name_source）。"""

    def make_dir(self):
        return Path(tempfile.mkdtemp(prefix="ncodeprocess-meta-"))

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
        for key in ("encoding", "g00_level", "m03_position", "newline", "aux_checks", "feed_outlier_iqr_factor"):
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


if __name__ == "__main__":
    unittest.main()
