"""程序统一设置存储：优先 Windows 注册表，注册表不可写时回退到设置文件。

统一位置：
  - 注册表：HKCU\\Software\\NCodeProcess（环境允许写注册表时）
  - 后备文件：%APPDATA%\\NCodeProcess\\settings.json（注册表不可写时；
    若 %APPDATA% 也不可用，则回退到用户主目录下的同名 JSON 文件）

统一格式：全部为 REG_SZ 字符串（布尔值存 "1"/"0"）；文件存储为 JSON，键值同为字符串。

所有会持久化的值（名称 -> 默认值）：

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
  load_all()   读取全部已持久化的值（仅返回已写入的项，注册表优先，后备文件覆盖）
  save_all()   写入传入的值（只覆盖传入的值名）；返回 (backend, location)
  clear_all()  删除全部值（注册表与后备文件一起清除）
  storage_backend()  查询当前保存设置将使用的后端（注册表 / 设置文件）

读取编制/审核时兼容旧键 HKCU\\Software\\NCPostProcess（仅针对默认键生效）。
主窗口快捷开关（递归扫描、保存 APTSOURCE、G00 级别等）不持久化，仅本次运行生效。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Dict

KEY = r"Software\NCodeProcess"
LEGACY_KEYS = (r"Software\NCPostProcess",)

# 全部会持久化的值及默认值（统一模型）。
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


def _settings_file_candidates(key: str):
    """后备设置文件候选位置：%APPDATA%\\NCodeProcess，其次用户主目录。"""
    if key == KEY:
        filename = "settings.json"
    else:
        basename = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in key.rstrip("\\").split("\\")[-1])
        filename = basename + ".json"
    candidates = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        candidates.append(Path(appdata) / "NCodeProcess" / filename)
    candidates.append(Path.home() / filename)
    return candidates


def _read_settings_file(path) -> Dict[str, str]:
    """读取单个设置文件；缺失或损坏时返回空字典。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {name: str(data[name]) for name in REGISTRY_KEYS if name in data}


def _load_settings_file(key: str) -> Dict[str, str]:
    """读取后备设置文件中的全部值；按候选顺序取第一个存在的文件。"""
    for candidate in _settings_file_candidates(key):
        values = _read_settings_file(candidate)
        if values:
            return values
    return {}


def _write_settings_file(values: Dict[str, str], key: str) -> Path:
    """把值写入后备设置文件：优先 %APPDATA%，失败时回退用户主目录。"""
    data: Dict[str, str] = {}
    for existing in _settings_file_candidates(key):
        data.update(_read_settings_file(existing))
    for name in REGISTRY_KEYS:
        if name in values:
            data[name] = str(values[name])
    for candidate in _settings_file_candidates(key):
        try:
            candidate.parent.mkdir(parents=True, exist_ok=True)
            temp = candidate.with_name(candidate.name + ".tmp")
            temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            temp.replace(candidate)
            return candidate
        except OSError:
            continue
    raise OSError("无法写入设置文件：" + str(_settings_file_candidates(key)[0]))


def _registry_write_works(key: str) -> bool:
    """探测 HKCU\\<key> 是否真的可写（写入并删除探测值）。非 Windows 恒为 False。"""
    if sys.platform != "win32":
        return False
    try:
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key) as registry_key:
            winreg.SetValueEx(registry_key, "__ncodeprocess_probe__", 0, winreg.REG_SZ, "")
            winreg.DeleteValue(registry_key, "__ncodeprocess_probe__")
        return True
    except OSError:
        return False


def storage_backend(key: str = KEY):
    """返回当前保存设置将使用的后端：("registry", None) 或 ("file", 设置文件路径)。"""
    if _registry_write_works(key):
        return ("registry", None)
    return ("file", str(_settings_file_candidates(key)[0]))


def load_all(key: str = KEY) -> Dict[str, str]:
    """读取全部已持久化的值；注册表优先，后备文件覆盖（仅注册表不可写时才有文件）。"""
    values: Dict[str, str] = {}
    if sys.platform == "win32":
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
    values.update(_load_settings_file(key))
    return values


def save_all(values: Dict[str, str], key: str = KEY):
    """写入传入的值；只覆盖属于 REGISTRY_KEYS 的值名。

    优先写注册表；注册表不可写时写后备设置文件。
    返回 (backend, location)：("registry", None) 或 ("file", 设置文件路径)。
    """
    if _registry_write_works(key):
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key) as registry_key:
            for name in REGISTRY_KEYS:
                if name in values:
                    winreg.SetValueEx(registry_key, name, 0, winreg.REG_SZ, str(values[name]))
        # 注册表写成功后清除历史后备文件，避免旧会话遗留文件覆盖注册表值
        for candidate in _settings_file_candidates(key):
            try:
                candidate.unlink(missing_ok=True)
            except OSError:
                pass
        return ("registry", None)
    return ("file", str(_write_settings_file(values, key)))


def clear_all(key: str = KEY) -> None:
    """删除全部已持久化的值（注册表与后备设置文件一起清除）。

    默认键同时清除遗留键（如旧版 NCPostProcess）中的对应值，
    避免清除后旧值在下次启动时经兼容读取“复活”。
    """
    if sys.platform == "win32":
        import winreg
        for key_path in _registry_paths(key):
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as registry_key:
                    for name in REGISTRY_KEYS:
                        try:
                            winreg.DeleteValue(registry_key, name)
                        except FileNotFoundError:
                            pass
            except OSError:
                continue
    for candidate in _settings_file_candidates(key):
        try:
            candidate.unlink(missing_ok=True)
        except OSError:
            pass
