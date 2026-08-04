"""CATIA NC post-processing utility."""

from .core import (
    Config,
    ProgramInfo,
    ToolInfo,
    Issue,
    FilePlan,
    ScanResult,
    ProcessReport,
    scan_directory,
    build_plan,
    process_plan,
    extract_tools,
    save_timestamped_report,
)

__version__ = "1.0.0"

__all__ = [
    "Config", "ProgramInfo", "ToolInfo", "Issue", "FilePlan", "ScanResult",
    "ProcessReport", "scan_directory", "build_plan", "process_plan", "extract_tools", "save_timestamped_report",
]
