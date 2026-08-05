import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from ncodeprocess.core import Config, FIELD_ORDER, FilePlan, ProcessReport, ProgramInfo, ToolInfo, _decode, add_initial_tool_change, add_m03, align_lines, apply_header, build_plan, calculate_stats, code_part, extract_drawing_candidates, extract_header_fields, extract_tools, process_plan, program_defaults, reprocess_file, save_timestamped_report, scan_directory, validate_program

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
        text = (
            '%\n'
            'MSG("PROGRAM:P")\n'
            'N1G1X10 (FEED S5000 OK)\n'
            'N2M30\n'
            '%\n'
        )
        out, changed, note = add_m03(text, Config())
        self.assertTrue(changed)
        self.assertNotIn("(FEED S5000 OK)M03", out)
        self.assertIn("M03\nN1G1X10 (FEED S5000 OK)", out)

    def test_m03_attaches_to_real_spindle_block_not_comment_mention(self):
        # A comment mentioning S must not capture the M03 insertion; the real
        # S instruction later in the body is the one that receives M03.
        text = (
            '%\n'
            'MSG("PROGRAM:P")\n'
            'N1G1X10 (FEED S5000 MAX)\n'
            'N2G1X20S1000\n'
            'N3M30\n'
            '%\n'
        )
        out, changed, note = add_m03(text, Config())
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

    def test_align_lines_tags_changed_and_equal_rows(self):
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

    def test_align_lines_tags_added_and_removed_rows(self):
        inserted = align_lines("A\nC", "A\nB\nC")
        self.assertIn(("", "", "B", "added"), inserted)
        removed = align_lines("A\nB\nC", "A\nC")
        self.assertIn(("B", "removed", "", ""), removed)

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

    def test_matching_cutter_and_toolno_is_ordinary_end_mill(self):
        text = "CUTTER/ 16.000000, 3.000000\nTOOLNO/5, 16.000000, 3.000000,, 120.000000,$\n"
        tool = extract_tools(text)[0]
        self.assertEqual(tool.dia, "16.000")
        self.assertEqual(tool.tool_coner, "3.000")
        self.assertEqual(tool.tool_type, "圆鼻立铣刀")
        self.assertEqual(tool.tool_angle, "")

    def test_ordinary_mill_requires_matching_corner_radius(self):
        # FR-4.3.16: 直径与圆角都一致才判普通立铣刀族；圆角不一致不得误判。
        text = "CUTTER/10,2\nTOOLNO/1,10,1,,\n"
        tools = extract_tools(text)
        self.assertEqual(tools[0].tool_type, "")

    def test_round_nose_split_into_ball_flat_and_round_nose(self):
        # 普通立铣刀按 R 与 D 的关系细分：球头 R=D/2、平底 R=0、其余圆鼻。
        cases = (
            ("CUTTER/ 10.000000,  5.000000\nTOOLNO/1, 10.000000, 5.000000,, 120.000000,$\n", "球头立铣刀"),
            ("CUTTER/ 10.000000,  0.000000\nTOOLNO/2, 10.000000, 0.000000,, 120.000000,$\n", "平底立铣刀"),
            ("CUTTER/ 20.000000,  3.000000\nTOOLNO/3, 20.000000, 3.000000,, 120.000000,$\n", "圆鼻立铣刀"),
        )
        for text, expected in cases:
            with self.subTest(text=text.splitlines()[0]):
                tool = extract_tools(text)[0]
                self.assertEqual(tool.tool_type, expected)

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

    def test_sample_apt_round_nose_ball_and_flat_mills_are_ordinary(self):
        # 样例刀具说明：圆鼻/球头/平底立铣刀（D20R3/D10R5/D10R0）→ 细分类型。
        # 使用样例真实格式：CUTTER 5 参 + TOOLNO 第 3 参圆角 + 第 5 参 120。
        cases = (
            ("CUTTER/ 20.000000,  3.000000,  7.000000,  3.000000,  0.000000,$\nTOOLNO/1,   20.000000,    3.000000,,  120.000000,$\n", "20.000", "3.000", "圆鼻立铣刀"),
            ("CUTTER/ 10.000000,  5.000000,  0.000000,  5.000000,  0.000000,$\nTOOLNO/6,   10.000000,    5.000000,,  120.000000,$\n", "10.000", "5.000", "球头立铣刀"),
            ("CUTTER/ 10.000000,  0.000000,  5.000000,  0.000000,  0.000000,$\nTOOLNO/14,   10.000000,    0.000000,,  120.000000,$\n", "10.000", "0.000", "平底立铣刀"),
        )
        for text, dia, coner, tool_type in cases:
            with self.subTest(text=text.splitlines()[0]):
                tool = extract_tools(text)[0]
                self.assertEqual(tool.dia, dia)
                self.assertEqual(tool.tool_coner, coner)
                self.assertEqual(tool.tool_type, tool_type)
                self.assertEqual(tool.tool_angle, "")

    def test_sample_apt_reverse_taper_mill_with_angle(self):
        # 样例 D12R3A2 反锥立铣刀：CUTTER 直径 12、TOOLNO 名义直径 10.467、包含角 -4。
        text = ("CUTTER/ 12.000000,  3.000000,  3.000000,  3.000000,  0.000000,$\n"
                "TOOLNO/4,   10.467000,    3.000000,   -4.000000,  120.000000,$\n")
        tool = extract_tools(text)[0]
        self.assertEqual(tool.dia, "12.000")
        self.assertEqual(tool.tool_coner, "3.000")
        self.assertEqual(tool.tool_type, "反锥立铣刀")
        self.assertEqual(tool.tool_angle, "-2.000")

    def test_sample_apt_drill_and_center_drill_with_continuation(self):
        # 样例钻头（D5.2）与中心钻（D2.5）：TOOLNO 第 4 参为 118/120（>100 触发钻类），
        # 续行第 2 参为钻尖高度、第 4 参为刀具类型码；两者皆空时为中心钻。
        drill = ("CUTTER/  5.200000,  0.000000,  2.600000,  1.501111, 30.000000,$\n"
                 "TOOLNO/9,    5.200000,,  120.000000,  120.000000,$\n"
                 "45.000000,    1.501000,   35.000000,2,    0.000000,NOTE\n")
        tool = extract_tools(drill)[0]
        self.assertEqual(tool.tool_type, "钻头")
        self.assertEqual(tool.tool_angle, "")
        self.assertEqual(tool.dia, "5.200")

        center = ("CUTTER/  2.500000,  0.000000,  1.250000,  0.751076, 31.000000,$\n"
                  "TOOLNO/13,    2.500000,,  118.000000,  120.000000,$\n"
                  "5.000000,,   11.000000,,    0.000000,NOTE\n")
        tool = extract_tools(center)[0]
        self.assertEqual(tool.tool_type, "中心钻")
        self.assertEqual(tool.tool_angle, "")
        self.assertEqual(tool.dia, "2.500")

    def test_code_part_strips_parenthesised_comment(self):
        self.assertEqual(code_part("N1G1X10 (comment)"), "N1G1X10 ")
        self.assertEqual(code_part("N1G1X10"), "N1G1X10")

    def test_drill_types_detected_independent_of_diameter(self):
        # 钻类判定仅依赖 APT 参数规律（TOOLNO 角度/续行），不限制直径规格。
        center = (
            "CUTTER/ 7.250000, 0.000000, 3.625000, 2.000000, 31.000000,$\n"
            "         0.000000, 11.000000\n"
            "TOOLNO/13, 7.250000,, 118.000000, 120.000000,$\n"
            "    5.000000,, 11.000000,, 0.000000,NOTE\n"
        )
        drill = (
            "CUTTER/ 8.750000, 0.000000, 4.375000, 2.500000, 30.000000,$\n"
            "         0.000000, 35.000000\n"
            "TOOLNO/10, 8.750000,, 120.000000, 120.000000,$\n"
            "   45.000000, 2.500000, 35.000000,2, 0.000000,NOTE\n"
        )
        for text, dia, expected_type in (
            (center, "7.250", "中心钻"),
            (drill, "8.750", "钻头"),
        ):
            with self.subTest(tool_type=expected_type):
                tool = extract_tools(text)[0]
                self.assertEqual(tool.dia, dia)
                self.assertEqual(tool.tool_type, expected_type)
                self.assertEqual(tool.tool_angle, "")
                self.assertNotIn("TOOL_ANGLE", tool.to_msg())

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

    def test_isolated_feed_parameter_line_warns(self):
        # FR-07.2: 无运动/坐标/辅助指令的孤立 F/S 参数行提示。
        text = "N1G1X10F1000\nN2F3000\nN3M30\n"
        issues = validate_program(text, "P.MPF", "P", DEFAULT_INFO, self._cfg())
        isolated = [issue for issue in issues if issue.kind == "isolated-parameter"]
        self.assertEqual(len(isolated), 1)
        self.assertIn("F3000", isolated[0].text)

    def test_motion_line_with_feed_is_not_isolated(self):
        text = "N1G1X10Y20F3000S5000M03\nN2M30\n"
        issues = validate_program(text, "P.MPF", "P", DEFAULT_INFO, self._cfg())
        self.assertFalse(any(issue.kind == "isolated-parameter" for issue in issues))

    def test_mutually_exclusive_m_codes_in_same_block_error(self):
        text = "N1M03M05\nN2G1X10F1000\nN3M30\n"
        issues = validate_program(text, "P.MPF", "P", DEFAULT_INFO, self._cfg())
        conflicts = [issue for issue in issues if issue.kind == "mutually-exclusive-m"]
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0].severity, "error")

    def test_feed_outlier_high_value_warns(self):
        # 主体 F 在千位范围，突然出现上万 F 时警告。
        body = "\n".join(f"N{i}G1X{i}F3000" for i in range(1, 6))
        text = body + "\nN6G1X60F15000\nN7M30\n"
        issues = validate_program(text, "P.MPF", "P", DEFAULT_INFO, self._cfg())
        outliers = [issue for issue in issues if issue.kind == "feed-outlier"]
        self.assertEqual(len(outliers), 1)
        self.assertIn("F15000", outliers[0].text)

    def test_feed_outlier_high_value_thresholds(self):
        # 上万但不足主体 3 倍不报；主体本身上万不报。
        body = "\n".join(f"N{i}G1X{i}F3000" for i in range(1, 6))
        text = body + "\nN6G1X60F9000\nN7M30\n"
        issues = validate_program(text, "P.MPF", "P", DEFAULT_INFO, self._cfg())
        self.assertFalse(any(issue.kind == "feed-outlier" for issue in issues))

        body = "\n".join(f"N{i}G1X{i}F20000" for i in range(1, 6))
        text = body + "\nN6G1X60F25000\nN7M30\n"
        issues = validate_program(text, "P.MPF", "P", DEFAULT_INFO, self._cfg())
        self.assertFalse(any(issue.kind == "feed-outlier" for issue in issues))

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

    def test_newline_force_lf_converts_crlf_source(self):
        # 强制 LF：CRLF 源文件处理后输出为 LF，不保留源 CRLF。
        root = self.make_dir()
        (root / "x_P.MPF").write_bytes('MSG("PROGRAM:P")\r\nN1S1000M03\r\nN2M30\r\n'.encode("utf-8"))
        cfg = Config(g00_level="allow", newline="lf")
        plan = build_plan(scan_directory(str(root), cfg), DEFAULT_INFO, cfg)
        report = process_plan(plan, str(root), cfg)
        self.assertEqual(report.success, 1)
        data = (root / "P.MPF").read_bytes()
        self.assertNotIn(b"\r\n", data)
        self.assertIn(b"\n", data)

    def test_newline_force_crlf_converts_lf_source(self):
        # 强制 CRLF：LF 源文件处理后输出为 CRLF。
        root = self.make_dir()
        (root / "x_P.MPF").write_text('MSG("PROGRAM:P")\nN1S1000M03\nN2M30\n', encoding="utf-8")
        cfg = Config(g00_level="allow", newline="crlf")
        plan = build_plan(scan_directory(str(root), cfg), DEFAULT_INFO, cfg)
        report = process_plan(plan, str(root), cfg)
        self.assertEqual(report.success, 1)
        data = (root / "P.MPF").read_bytes()
        self.assertIn(b"\r\n", data)
        self.assertNotIn(b"\n", data.replace(b"\r\n", b""))

    def test_newline_auto_preserves_source_style(self):
        # 默认 auto：CRLF 源保持 CRLF（锁定现行为）。
        root = self.make_dir()
        (root / "x_P.MPF").write_bytes('MSG("PROGRAM:P")\r\nN1S1000M03\r\nN2M30\r\n'.encode("utf-8"))
        cfg = self._cfg()
        plan = build_plan(scan_directory(str(root), cfg), DEFAULT_INFO, cfg)
        report = process_plan(plan, str(root), cfg)
        self.assertEqual(report.success, 1)
        self.assertIn(b"\r\n", (root / "P.MPF").read_bytes())

    def test_aux_m03_rule_reports_only_when_violated(self):
        # m03-before-motion：M03 出现在首次切削运动之后 → error；之前 → 无问题。
        info = DEFAULT_INFO
        violated = '%\nMSG("PROGRAM:P")\nN1G1X10\nN2M03\nN3M30\n%\n'
        ok = '%\nMSG("PROGRAM:P")\nN1M03\nN2G1X10\nN3M30\n%\n'
        issues = validate_program(violated, "P.MPF", "P", info,
                                  self._cfg(aux_checks={"m03-before-motion"}))
        self.assertTrue(any(i.kind == "aux-order" and i.severity == "error" for i in issues))
        issues = validate_program(ok, "P.MPF", "P", info,
                                  self._cfg(aux_checks={"m03-before-motion"}))
        self.assertFalse(any(i.kind == "aux-order" for i in issues))

    def test_aux_m05_after_end_is_warning(self):
        text = '%\nMSG("PROGRAM:P")\nN1M03\nN2M30\nN3M05\n%\n'
        issues = validate_program(text, "P.MPF", "P", DEFAULT_INFO,
                                  Config(g00_level="allow", aux_checks={"m05-before-end"}))
        self.assertTrue(any(i.kind == "aux-order" and i.severity == "warning" for i in issues))

    def test_aux_m08_after_first_cut_is_warning(self):
        text = '%\nMSG("PROGRAM:P")\nN1G1X10\nN2M08\nN3M30\n%\n'
        issues = validate_program(text, "P.MPF", "P", DEFAULT_INFO,
                                  Config(g00_level="allow", aux_checks={"m08-before-cut"}))
        self.assertTrue(any(i.kind == "aux-order" and i.severity == "warning" for i in issues))

    def test_aux_m09_absent_produces_no_warning(self):
        # M09 未出现时不提示 m09-before-end；出现且晚于结束指令时才提示。
        text = '%\nMSG("PROGRAM:P")\nN1M03\nN2M30\n%\n'
        issues = validate_program(text, "P.MPF", "P", DEFAULT_INFO,
                                  Config(g00_level="allow", aux_checks={"m09-before-end"}))
        self.assertFalse(any(i.kind == "aux-order" for i in issues))

    def test_aux_m09_after_end_is_warning(self):
        text = '%\nMSG("PROGRAM:P")\nN1M03\nN2M30\nN3M09\n%\n'
        issues = validate_program(text, "P.MPF", "P", DEFAULT_INFO,
                                  Config(g00_level="allow", aux_checks={"m09-before-end"}))
        self.assertTrue(any(i.kind == "aux-order" and i.severity == "warning" for i in issues))

    def test_aux_checks_empty_disables_all(self):
        # 未启用任何顺序规则时（默认）不产生 aux-order，锁定默认行为。
        text = '%\nMSG("PROGRAM:P")\nN1G1X10\nN2M30\nN3M05\nN4M08\n%\n'
        issues = validate_program(text, "P.MPF", "P", DEFAULT_INFO,
                                  self._cfg())
        self.assertFalse(any(i.kind == "aux-order" for i in issues))


if __name__ == "__main__":
    unittest.main()
