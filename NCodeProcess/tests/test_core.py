import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from ncodeprocess.core import Config, FilePlan, ProcessReport, ProgramInfo, ToolInfo, add_m03, align_lines, apply_header, build_plan, calculate_stats, extract_drawing_candidates, extract_header_fields, extract_tools, process_plan, program_defaults, reprocess_file, save_timestamped_report, scan_directory, validate_program


class CoreTests(unittest.TestCase):
    def make_dir(self):
        return Path(tempfile.mkdtemp(prefix="ncodeprocess-"))

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
        mpf = next(f for f in plan.files if f.kind == "mpf")
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
        info = ProgramInfo("A", "B", "D", "V", "M", "C", "DATE")
        cfg = Config(g00_level="allow")
        plan = build_plan(scan_directory(str(root), cfg), info, cfg)
        out = next(f for f in plan.files if f.kind == "mpf").output_text
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
        issues = validate_program(text, "P.MPF", "P", ProgramInfo("A", "B", "D", "V", "M", "C", "DATE"), Config(auto_m03=True))
        spindle = [i for i in issues if i.kind == "spindle-start"]
        self.assertEqual(len(spindle), 1)
        self.assertEqual(spindle[0].severity, "error")

    def test_missing_m03_is_warning_when_auto_insert_disabled(self):
        # With auto insertion disabled the missing M03 stays a warning so the
        # user can decide how to handle the program.
        text = '%\nMSG("PROGRAM:P")\nN1G1X10\nN2M30\n%\n'
        issues = validate_program(text, "P.MPF", "P", ProgramInfo("A", "B", "D", "V", "M", "C", "DATE"), Config(auto_m03=False))
        spindle = [i for i in issues if i.kind == "spindle-start"]
        self.assertEqual(len(spindle), 1)
        self.assertEqual(spindle[0].severity, "warning")

    def test_reprocess_file_revalidates_after_manual_edit(self):
        # After the operator manually fixes the NC code, reprocessing the
        # in-memory plan must regenerate output and clear the previous error.
        f = FilePlan("x_P.MPF", "mpf", "P", "P.MPF", "keep")
        f.original_text = '%\nMSG("PROGRAM:P")\n(ONLY COMMENT)\n%\n'
        info = ProgramInfo("A", "B", "D", "V", "M", "C", "DATE")
        cfg = Config(g00_level="allow", auto_m03=True)
        reprocess_file(f, info, cfg)
        self.assertTrue(any(i.kind == "spindle-start" and i.severity == "error" for i in f.issues))
        # The operator adds a real instruction line; the next review must
        # auto-insert M03 and drop the spindle-start error.
        f.original_text = '%\nMSG("PROGRAM:P")\nN1G1X10\nN2M30\n%\n'
        reprocess_file(f, info, cfg)
        self.assertIn("M03", f.output_text)
        self.assertFalse(any(i.kind == "spindle-start" for i in f.issues))

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
        cfg = Config(g00_level="allow")
        plan = build_plan(scan_directory(str(root), cfg), ProgramInfo("A", "B", "D", "V", "M", "C", "DATE"), cfg)
        out = next(f for f in plan.files if f.kind == "mpf").output_text
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
        self.assertEqual(tool.tool_type, "普通立铣刀")
        self.assertEqual(tool.tool_angle, "")

    def test_center_drill_detected_independent_of_diameter(self):
        text = (
            "CUTTER/ 7.250000, 0.000000, 3.625000, 2.000000, 31.000000,$\n"
            "         0.000000, 11.000000\n"
            "TOOLNO/13, 7.250000,, 118.000000, 120.000000,$\n"
            "    5.000000,, 11.000000,, 0.000000,NOTE\n"
        )
        tool = extract_tools(text)[0]
        self.assertEqual(tool.dia, "7.250")
        self.assertEqual(tool.tool_type, "中心钻")
        self.assertEqual(tool.tool_angle, "")
        self.assertNotIn("TOOL_ANGLE", tool.to_msg())

    def test_drill_detected_independent_of_diameter(self):
        text = (
            "CUTTER/ 8.750000, 0.000000, 4.375000, 2.500000, 30.000000,$\n"
            "         0.000000, 35.000000\n"
            "TOOLNO/10, 8.750000,, 120.000000, 120.000000,$\n"
            "   45.000000, 2.500000, 35.000000,2, 0.000000,NOTE\n"
        )
        tool = extract_tools(text)[0]
        self.assertEqual(tool.dia, "8.750")
        self.assertEqual(tool.tool_type, "钻头")
        self.assertEqual(tool.tool_angle, "")
        self.assertNotIn("TOOL_ANGLE", tool.to_msg())

    def test_special_tool_is_written_to_mpf_from_paired_apt(self):
        root = self.make_dir()
        (root / "x_P.MPF").write_text("N1T5M06\nN2S100M03\nN3M30\n", encoding="utf-8")
        (root / "x_P_I.aptsource").write_text(
            "CUTTER/ 16.000000, 3.000000\nTOOLNO/5, 13.178000, 3.000000, -6.000000,$\n",
            encoding="utf-8",
        )
        cfg = Config(g00_level="allow")
        plan = build_plan(scan_directory(str(root), cfg), ProgramInfo("A", "B", "D", "V", "M", "C", "DATE"), cfg)
        out = next(f for f in plan.files if f.kind == "mpf").output_text
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
        cfg = Config(g00_level="allow")
        plan = build_plan(
            scan_directory(str(root), cfg),
            ProgramInfo("A", "B", "D", "V", "M", "C", "DATE"),
            cfg,
            {"P": [ToolInfo(5, "88", "", "配置旧信息")]},
        )
        out = next(f for f in plan.files if f.kind == "mpf").output_text
        self.assertIn('MSG("T5:DIA=16.000,TOOL_CONER=3.000,TOOL_TYPE=普通立铣刀")', out)
        self.assertNotIn("DIA=99", out)
        self.assertNotIn("DIA=88", out)

    def test_newest_apt_generation_wins_over_older_apt(self):
        root = self.make_dir()
        (root / "x_P.MPF").write_text("N1T5M06\nN2S100M03\nN3M30\n", encoding="utf-8")
        old = root / "old_P_I.aptsource"
        new = root / "new_P_I.aptsource"
        old.write_text("CUTTER/ 10.000000, 3.000000\nTOOLNO/5, 10.000000, 3.000000,, 120.000000,$\n", encoding="utf-8")
        new.write_text("CUTTER/ 16.000000, 3.000000\nTOOLNO/5, 16.000000, 3.000000,, 120.000000,$\n", encoding="utf-8")
        import os
        os.utime(old, (1000, 1000))
        os.utime(new, (2000, 2000))
        cfg = Config(g00_level="allow")
        plan = build_plan(scan_directory(str(root), cfg), ProgramInfo("A", "B", "D", "V", "M", "C", "DATE"), cfg)
        out = next(f for f in plan.files if f.kind == "mpf").output_text
        self.assertIn('MSG("T5:DIA=16.000,TOOL_CONER=3.000,TOOL_TYPE=普通立铣刀")', out)
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
        cfg = Config(g00_level="allow")
        plan = build_plan(scan_directory(str(root), cfg), ProgramInfo("A", "B", "D", "V", "M", "C", "DATE"), cfg)
        out = next(f for f in plan.files if f.kind == "mpf").output_text
        self.assertIn('MSG("T1:DIA=10.000,TOOL_CONER=3.000,TOOL_TYPE=普通立铣刀")', out)
        self.assertIn('MSG("T2:DIA=6.000,TOOL_CONER=1.000,TOOL_TYPE=普通立铣刀")', out)

    def test_program_tool_override_replaces_existing_tool_rows(self):
        root = self.make_dir()
        (root / "x_AG6D311A0101.MPF").write_text('MSG("PROGRAM:AG6D311A0101")\nMSG("T1:DIA=20")\nMSG("T2:DIA=10")\nN1S100M03\nN2M30\n', encoding="utf-8")
        cfg = Config(g00_level="allow")
        info = ProgramInfo("A", "B", "D", "V", "M", "C", "DATE")
        plan = build_plan(scan_directory(str(root), cfg), info, cfg, {"AG6D311A0101": [ToolInfo(1, "8", "", "自定义刀")]})
        out = next(f for f in plan.files if f.kind == "mpf").output_text
        self.assertIn('MSG("T1:DIA=8.000,TOOL_TYPE=自定义刀")', out)
        self.assertNotIn('MSG("T2:', out)

    def test_tool_rows_are_last_header_lines(self):
        text = 'MSG("PROGRAM:P")\nMSG("DRAWING NUMBER:D")\nMSG("PART VERSION:V")\nN1S100M03\nN2M30\n'
        cfg = Config(g00_level="allow")
        info = ProgramInfo("A", "B", "D", "V", "M", "C", "DATE", [ToolInfo(1, "8", "", "钻头")])
        out, _, _ = apply_header(text, "P", info, cfg, replace_tools=True)
        lines = out.splitlines()
        tool_index = next(i for i, line in enumerate(lines) if 'MSG("T1:' in line)
        body_index = next(i for i, line in enumerate(lines) if line.startswith("N1"))
        self.assertEqual(tool_index, body_index - 1)
        self.assertNotIn("\n\nN", out)

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
        plan = build_plan(scan_directory(str(root), cfg), ProgramInfo("A", "B", "D", "V", "M", "C", "DATE"), cfg)
        out = next(f for f in plan.files if f.kind == "mpf").output_text
        lines = out.splitlines()
        body_index = next(i for i, line in enumerate(lines) if line.startswith("T1M6"))
        self.assertTrue(lines[body_index - 1].startswith('MSG("T1:'))
        self.assertEqual(out.count("T1M6"), 1)
        self.assertIn("N3T1;", out)
        self.assertNotIn("T5", out)

    def test_separate_output_keeps_input(self):
        root = self.make_dir(); out = self.make_dir()
        src = root / "x_AG6D311A0101.MPF"
        src.write_text('MSG("PROGRAM:AG6D311A0101")\nN1S100M03\nN2M30\n', encoding="utf-8")
        cfg = Config(g00_level="allow")
        plan = build_plan(scan_directory(str(root), cfg), ProgramInfo("A", "B", "D", "V", "M", "C", "DATE"), cfg)
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
        import os
        os.utime(old, (1000, 1000))
        os.utime(new, (2000, 2000))
        cfg = Config(g00_level="allow")
        plan = build_plan(scan_directory(str(root), cfg), ProgramInfo("A", "B", "D", "V", "M", "C", "DATE"), cfg)
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
        issues = validate_program(text, "P.MPF", "P", info, Config(g00_level="allow"))
        kinds = {issue.kind for issue in issues}
        self.assertIn("feed-outlier", kinds)
        self.assertIn("multiple-spindle-speeds", kinds)
        stats = calculate_stats(text)
        self.assertEqual(stats.g00_count, 1)

    def test_zero_feed_is_error(self):
        text = "MSG(\"PROGRAM:P\")\nF0\nM30\n"
        issues = validate_program(text, "P.MPF", "P", ProgramInfo("A", "B", "D", "V", "M", "S", "DATE"), Config(g00_level="allow"))
        zero = [issue for issue in issues if issue.kind == "feed-zero"]
        self.assertEqual(len(zero), 1)
        self.assertEqual(zero[0].severity, "error")

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
        cfg = Config(g00_level="allow")
        plan = build_plan(scan_directory(str(root), cfg), ProgramInfo("A", "B", "D", "V", "M", "C", "DATE"), cfg)
        mpf = next(f for f in plan.files if f.kind == "mpf")
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
            text, "P", ProgramInfo("A", "B", "D", "V", "M", "C", "DATE"), Config(g00_level="allow")
        )
        duplicates = [issue for issue in issues if issue.kind == "duplicate-msg-field"]
        self.assertEqual(len(duplicates), 1)
        self.assertEqual(duplicates[0].severity, "warning")
        self.assertIn("T1", duplicates[0].suggestion)
        self.assertTrue(any("重复头部字段 T1" in change for change in changes))


if __name__ == "__main__":
    unittest.main()
