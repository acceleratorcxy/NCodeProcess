"""Small user preference store without a config file in the NC directory.

注册表键值（HKCU\\Software\\NCodeProcess，REG_SZ）：

  编制 / 审核（与旧版本共享，保留原有行为）:
    bianzhi  编制人姓名
    shenhe   审核/校对姓名

  程序设置（程序设置对话框，仅这些设置持久化）:
    encoding                文件编码          "auto"
    delete_extensions       待删除扩展名      ".log, .moaptindexes"
    allowed_name_pattern    程序名允许字符    "^[A-Za-z0-9_一-鿿-]+$"
    aptsource_dir           APTSOURCE 归档子目录 "aptsource"
    program_extensions      主程序扩展名      ".mpf"
    program_output_extension 输出扩展名       ".MPF"
    require_end_marker      要求结束标记      "1"/"0"
    require_m06             要求 M06          "1"/"0"
    require_spindle_speed   要求 S 转速       "1"/"0"

主窗口快捷开关（递归扫描、保存 APTSOURCE、G00 级别等）不持久化，仅本次运行生效。
"""

from __future__ import annotations

import sys
from typing import Dict

KEY = r"Software\NCodeProcess"
LEGACY_KEYS = (r"Software\NCPostProcess",)
FIELDS = ("bianzhi", "shenhe")

# 程序设置对话框各项的默认值（与 gui.py 控件初始值保持一致）。
SETTING_DEFAULTS = {
    "encoding": "auto",
    "delete_extensions": ".log, .moaptindexes",
    "allowed_name_pattern": r"^[A-Za-z0-9_一-鿿-]+$",
    "aptsource_dir": "aptsource",
    "program_extensions": ".mpf",
    "program_output_extension": ".MPF",
    "require_end_marker": "1",
    "require_m06": "0",
    "require_spindle_speed": "0",
}
SETTING_KEYS = tuple(SETTING_DEFAULTS)


def load() -> Dict[str, str]:
    values = {name: "" for name in FIELDS}
    if sys.platform != "win32":
        return values
    import winreg
    for key_path in (KEY,) + LEGACY_KEYS:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
                for name in FIELDS:
                    if values[name]:
                        continue
                    try:
                        values[name] = str(winreg.QueryValueEx(key, name)[0])
                    except FileNotFoundError:
                        pass
        except OSError:
            continue
    return values


def save(values: Dict[str, str]) -> None:
    if sys.platform != "win32":
        return
    try:
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, KEY) as key:
            for name, value in values.items():
                if name in FIELDS:
                    winreg.SetValueEx(key, name, 0, winreg.REG_SZ, str(value))
    except OSError:
        pass


def load_settings(key: str = KEY) -> Dict[str, str]:
    """读取程序设置对话框中已持久化的各项；未写入的值不包含在结果中。"""
    values: Dict[str, str] = {}
    if sys.platform != "win32":
        return values
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as registry_key:
            for name in SETTING_KEYS:
                try:
                    values[name] = str(winreg.QueryValueEx(registry_key, name)[0])
                except FileNotFoundError:
                    pass
    except OSError:
        pass
    return values


def save_settings(values: Dict[str, str], key: str = KEY) -> None:
    """写入程序设置对话框各项；仅覆盖传入的值名，不影响同键下其他值。"""
    if sys.platform != "win32":
        return
    try:
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key) as registry_key:
            for name in SETTING_KEYS:
                if name in values:
                    winreg.SetValueEx(registry_key, name, 0, winreg.REG_SZ, str(values[name]))
    except OSError:
        pass


def clear_settings(key: str = KEY) -> None:
    """删除程序设置对话框的各项注册表值（编制/审核不受影响）。"""
    if sys.platform != "win32":
        return
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as registry_key:
            for name in SETTING_KEYS:
                try:
                    winreg.DeleteValue(registry_key, name)
                except FileNotFoundError:
                    pass
    except OSError:
        pass
