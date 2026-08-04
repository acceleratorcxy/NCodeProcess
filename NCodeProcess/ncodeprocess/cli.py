from __future__ import annotations

import argparse
import json
from pathlib import Path

from .core import Config, ProgramInfo, ToolInfo, build_plan, process_plan, save_timestamped_report, scan_directory


def _tools(values):
    result = []
    for value in values or []:
        # number,dia,coner,type,angle (type may contain commas only when quoted by the shell)
        parts = value.split(",", 4)
        try:
            number = int(parts[0])
        except (ValueError, IndexError):
            raise argparse.ArgumentTypeError("刀具格式应为 number,dia,tool_coner,tool_type,tool_angle")
        result.append(ToolInfo(number, parts[1] if len(parts) > 1 else "", parts[2] if len(parts) > 2 else "", parts[3] if len(parts) > 3 else "", parts[4] if len(parts) > 4 else ""))
    return result


def build_parser():
    p = argparse.ArgumentParser(description="CATIA 后处理 NC 程序整理工具")
    p.add_argument("--input", "-i", required=True, help="输入目录")
    p.add_argument("--output", "-o", help="输出目录，默认输入目录")
    p.add_argument("--recursive", action="store_true", help="递归扫描子目录")
    p.add_argument("--yes", action="store_true", help="确认执行移动和清理")
    p.add_argument("--overwrite", action="store_true", help="允许覆盖目标文件")
    p.add_argument("--keep-intermediate", action="store_true", help="不删除 LOG/MOAPTIndexes")
    p.add_argument("--save-aptsource", action="store_true", help="保存 APTSOURCE 文件并按时间归档；默认删除")
    p.add_argument("--overwrite-fields", action="store_true", help="覆盖已有非空 MSG 字段")
    p.add_argument("--g00-level", choices=["error", "warning", "allow"], default="error")
    p.add_argument("--no-m03", action="store_true", help="不自动补写 M03")
    p.add_argument("--auto-tool-change", action="store_true", help="自动添加换刀指令 TnM6，并统一已有刀具号")
    p.add_argument("--require-m06", action="store_true")
    p.add_argument("--allow-no-end", action="store_true")
    p.add_argument("--encoding", default="auto", help="auto、utf-8、gb18030 等")
    p.add_argument("--bianzhi", default="")
    p.add_argument("--shenhe", default="")
    p.add_argument("--drawing-number", default="")
    p.add_argument("--part-version", default="")
    p.add_argument("--nc-machine", default="")
    p.add_argument("--control-system", default="")
    p.add_argument("--date", default="")
    p.add_argument("--tool", action="append", help="刀具 number,dia,tool_coner,tool_type,tool_angle，可重复")
    p.add_argument("--json-report", help="报告 JSON 输出路径")
    p.add_argument("--csv-report", help="报告 CSV 输出路径")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    config = Config(
        recursive=args.recursive,
        save_aptsource=args.save_aptsource,
        overwrite_existing=args.overwrite,
        overwrite_fields=args.overwrite_fields,
        g00_level=args.g00_level,
        auto_m03=not args.no_m03,
        auto_tool_change=args.auto_tool_change,
        require_m06=args.require_m06,
        require_end_marker=not args.allow_no_end,
        encoding=args.encoding,
    )
    if args.keep_intermediate:
        config.delete_extensions = set()
    info = ProgramInfo(args.bianzhi, args.shenhe, args.drawing_number, args.part_version, args.nc_machine, args.control_system, args.date, _tools(args.tool))
    scan = build_plan(scan_directory(args.input, config), info, config)
    print(f"扫描: {len(scan.files)} 个文件；MPF {sum(f.kind == 'mpf' for f in scan.files)}，aptsource {sum(f.kind == 'aptsource' for f in scan.files)}")
    for warning in scan.warnings:
        print("警告:", warning)
    for f in scan.files:
        print(f"[{f.action}] {f.source}" + (f" -> {Path(f.target).name}" if f.target else ""))
        for change in f.changes:
            print("  *", change)
        for issue in f.issues:
            print(f"  {issue.severity}: {issue.kind} L{issue.line} {issue.suggestion}")
    if not args.yes:
        print("仅预览。添加 --yes 执行写入、移动和清理。")
        return 0
    if not args.drawing_number.strip() or not args.part_version.strip():
        print("错误：图号和版次为必填项，已放弃修改。")
        return 2
    report = process_plan(scan, args.output, config, confirm_cleanup=True)
    if args.json_report:
        report_path = Path(args.json_report)
        report.write_json(report_path)
    else:
        report_path = save_timestamped_report(report, Path(args.output or args.input) / config.data_dir_name, keep=3)
    if args.csv_report:
        report.write_csv(Path(args.csv_report))
    print(json.dumps({k: v for k, v in report.to_dict().items() if k != "files"}, ensure_ascii=False, indent=2))
    print("报告:", report_path)
    return 1 if report.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
