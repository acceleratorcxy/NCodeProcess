from __future__ import annotations

import argparse
import json
from pathlib import Path

from .core import Config, ProgramInfo, ToolInfo, build_plan, emit_event, process_plan, scan_directory
from .preferences import load_all


AUX_RULES = (
    ("m03-before-motion", "aux_m03_before_motion", "m03"),
    ("m05-before-end", "aux_m05_before_end", "m05"),
    ("m08-before-cut", "aux_m08_before_cut", "m08"),
    ("m09-before-end", "aux_m09_before_end", "m09"),
)


def _pref_bool(prefs, key, default):
    value = prefs.get(key)
    if value is None:
        return default
    return str(value) == "1"


def _pref_optional_float(prefs, key):
    value = prefs.get(key)
    if value is None or str(value).strip() == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _pref_float(prefs, key, default):
    value = _pref_optional_float(prefs, key)
    return default if value is None else value


def _pref_int(prefs, key, default):
    value = prefs.get(key)
    if value is None or str(value).strip() == "":
        return default
    try:
        return int(float(value))
    except ValueError:
        return default


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
    # WP-C3：与 GUI 设置持久化对齐的参数（显式传参覆盖持久化偏好）。
    p.add_argument("--m03-position", choices=["after-s", "standalone"], default="",
                   help="M03 补写位置：after-s（S 所在块尾）/ standalone（独立行）；缺省读持久化偏好")
    p.add_argument("--newline", choices=["auto", "crlf", "lf"], default="",
                   help="换行策略：auto/crlf/lf；缺省读持久化偏好")
    p.add_argument("--feed-min", type=float, default=None, help="F 下限（不检查请留空）")
    p.add_argument("--feed-max", type=float, default=None, help="F 上限")
    p.add_argument("--spindle-min", type=float, default=None, help="S 下限")
    p.add_argument("--spindle-max", type=float, default=None, help="S 上限")
    for _rule, _key, name in AUX_RULES:
        group = p.add_mutually_exclusive_group()
        group.add_argument("--aux-" + name, dest="aux_" + name, action="store_true", default=None,
                           help="启用辅助指令顺序规则 " + name)
        group.add_argument("--no-aux-" + name, dest="aux_" + name, action="store_false",
                           help="禁用辅助指令顺序规则 " + name)
    p.add_argument("--feed-outlier-iqr", type=float, default=None, help="F 离群 IQR 倍数（回退判定）")
    p.add_argument("--feed-outlier-low", type=float, default=None, help="F 离群低值比例")
    p.add_argument("--feed-outlier-high", type=float, default=None, help="F 离群高值倍数")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--multiple-spindle", dest="multiple_spindle", action="store_true", default=None,
                       help="启用多 S 值警告")
    group.add_argument("--no-multiple-spindle", dest="multiple_spindle", action="store_false",
                       help="禁用多 S 值警告")
    p.add_argument("--max-file-size", type=int, default=None, help="单文件大小上限（字节，0=不限制）")
    p.add_argument("--max-files", type=int, default=None, help="扫描文件数量上限（0=不限制）")
    p.add_argument("--retract-z-threshold", type=float, default=None, help="抬刀高度阈值（默认 20）")
    return p


def _config_from_args(args) -> Config:
    """构建 CLI 配置：未显式传参的项读取持久化偏好（与 GUI 一致）。"""
    prefs = load_all()
    emit_event("info", "settings_loaded", "命令行模式加载持久化偏好")
    aux_checks = set()
    for rule, key, name in AUX_RULES:
        value = getattr(args, "aux_" + name)
        if value is None:
            value = _pref_bool(prefs, key, True)
        if value:
            aux_checks.add(rule)
    return Config(
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
        m03_position=args.m03_position or prefs.get("m03_position", "after-s"),
        newline=args.newline or prefs.get("newline", "auto"),
        feed_min=args.feed_min if args.feed_min is not None else _pref_optional_float(prefs, "feed_min"),
        feed_max=args.feed_max if args.feed_max is not None else _pref_optional_float(prefs, "feed_max"),
        spindle_min=args.spindle_min if args.spindle_min is not None else _pref_optional_float(prefs, "spindle_min"),
        spindle_max=args.spindle_max if args.spindle_max is not None else _pref_optional_float(prefs, "spindle_max"),
        aux_checks=aux_checks,
        feed_outlier_iqr_factor=args.feed_outlier_iqr if args.feed_outlier_iqr is not None
        else _pref_float(prefs, "feed_outlier_iqr_factor", 3.0),
        feed_outlier_low_ratio=args.feed_outlier_low if args.feed_outlier_low is not None
        else _pref_float(prefs, "feed_outlier_low_ratio", 0.1),
        feed_outlier_high_ratio=args.feed_outlier_high if args.feed_outlier_high is not None
        else _pref_float(prefs, "feed_outlier_high_ratio", 3.0),
        multiple_spindle_warn=args.multiple_spindle if args.multiple_spindle is not None
        else _pref_bool(prefs, "multiple_spindle_warn", True),
        max_file_size=args.max_file_size if args.max_file_size is not None else _pref_int(prefs, "max_file_size", 0),
        max_files=args.max_files if args.max_files is not None else _pref_int(prefs, "max_files", 0),
        retract_z_threshold=args.retract_z_threshold if args.retract_z_threshold is not None
        else _pref_float(prefs, "retract_z_threshold", 20.0),
    )


def main(argv=None):
    args = build_parser().parse_args(argv)
    # WP-R3：启动不自动创建 NCodeProcessData/logs；磁盘日志仅在显式导出报告时落盘。
    emit_event("info", "startup", "命令行模式启动")
    config = _config_from_args(args)
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
    report = process_plan(scan, args.output, config, confirm_cleanup=True, generator="cli", confirmations=["命令行 --yes 确认执行"])
    if args.json_report:
        # WP-R4：导出报告不生成磁盘日志文件，日志完整内嵌报告 runtime_log。
        emit_event("info", "export_start", f"开始导出报告：{Path(args.json_report)}")
        report.write_json(Path(args.json_report))
        emit_event("info", "export_finish", f"报告已导出：{Path(args.json_report).name}")
        report_path = Path(args.json_report)
    else:
        # WP-R3：未显式指定报告路径时，不自动生成报告文件（仅打印摘要）。
        report_path = None
    if args.csv_report:
        report.write_csv(Path(args.csv_report))
    print(json.dumps({k: v for k, v in report.to_dict().items() if k != "files"}, ensure_ascii=False, indent=2))
    print("报告:", report_path if report_path else "未生成（使用 --json-report 指定路径导出报告）")
    emit_event("info", "shutdown", "命令行模式退出")
    return 1 if report.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
