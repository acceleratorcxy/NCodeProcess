"""程序统一注册表偏好存储（不写配置文件到 NC 目录）。

统一位置：HKCU\\Software\\NCodeProcess
统一格式：全部为 REG_SZ 字符串（布尔值存 "1"/"0"）

所有会写入注册表的值（名称 -> 默认值）：

  编制 / 审核：
    bianzhi  编制人姓名        ""
    shenhe   审核/校对姓名      ""

  程序设置（程序设置对话框）：
    encoding                文件编码          "auto"
    delete_extensions       待删除扩展名      ".log, .moaptindexes"
    allowed_name_pattern    程序名允许字符    "^[A-Za-z0-9_一-鿿-]+$"
    aptsource_dir           APTSOURCE 归档子目录 "aptsource"
    program_extensions      主程序扩展名      ".mpf"
    program_output_extension 输出扩展名       ".MPF"
    require_end_marker      要求结束标记      "1"
    require_m06             要求 M06          "0"
    require_spindle_speed   要求 S 转速       "0"

统一操作：
  load_all()   读取全部已写入的值（仅返回注册表中存在的项）
  save_all()   写入传入的值（只覆盖传入的值名，不影响同键下其他值）
  clear_all()  删除全部值（编制/审核与程序设置一起清除）

读取编制/审核时兼容旧键 HKCU\\Software\\NCPostProcess（仅针对默认键生效）。
主窗口快捷开关（递归扫描、保存 APTSOURCE、G00 级别等）不持久化，仅本次运行生效。
"""

from __future__ import annotations

import sys
from typing import Dict

KEY = r"Software\NCodeProcess"
LEGACY_KEYS = (r"Software\NCPostProcess",)

# 全部会写入注册表的值及默认值（统一模型）。
REGISTRY_DEFAULTS = {
    "bianzhi": "",
    "shenhe": "",
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
REGISTRY_KEYS = tuple(REGISTRY_DEFAULTS)


def _registry_paths(key: str) -> tuple:
    """默认键附带旧版编制/审核键用于兼容读取；自定义键只读自身。"""
    return (key,) + (LEGACY_KEYS if key == KEY else ())


def load_all(key: str = KEY) -> Dict[str, str]:
    """读取注册表中已写入的全部项；未写入的值不包含在结果中。"""
    values: Dict[str, str] = {}
    if sys.platform != "win32":
        return values
    import winreg
    for key_path in _registry_paths(key):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as registry_key:
                for name in REGISTRY_KEYS:
                    if name in values:
                        continue
                    try:
                        values[name] = str(winreg.QueryValueEx(registry_key, name)[0])
                    except FileNotFoundError:
                        pass
        except OSError:
            continue
    return values


def save_all(values: Dict[str, str], key: str = KEY) -> None:
    """写入传入的值；仅覆盖属于 REGISTRY_KEYS 的值名，不影响同键下其他值。"""
    if sys.platform != "win32":
        return
    try:
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key) as registry_key:
            for name in REGISTRY_KEYS:
                if name in values:
                    winreg.SetValueEx(registry_key, name, 0, winreg.REG_SZ, str(values[name]))
    except OSError:
        pass


def clear_all(key: str = KEY) -> None:
    """删除注册表中的全部值（编制/审核与程序设置一起清除）。"""
    if sys.platform != "win32":
        return
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as registry_key:
            for name in REGISTRY_KEYS:
                try:
                    winreg.DeleteValue(registry_key, name)
                except FileNotFoundError:
                    pass
    except OSError:
        pass
