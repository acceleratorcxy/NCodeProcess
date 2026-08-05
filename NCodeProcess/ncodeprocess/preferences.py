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
    ask_backup              处理前询问备份    "1"
    required_bianzhi/shenhe/drawing/part  必填 MSG 字段  "1"
    m03_position            M03 补写位置      "after-s"
    feed_min/feed_max       F 上下限          ""
    spindle_min/spindle_max S 上下限          ""
    newline                 换行策略          "auto"
    aux_m03/m05/m08/m09_before_*  辅助指令顺序  "1"
    feed_outlier_iqr_factor F 离群 IQR 倍数   "3"
    feed_outlier_low_ratio  F 离群低值比例    "0.1"
    feed_outlier_high_ratio F 离群高值倍数    "3"
    multiple_spindle_warn   多 S 值警告       "1"
    storage_backend         保存位置          "registry"

统一操作：
  load_all()   读取全部已持久化的值；按「存在性检测」定位后端——注册表→appdata→home 顺序，
               第一个有已持久化值的位置即为保存位置（哪个位置有配置，哪个位置就是保存位置）
  save_all()   写入传入的值（只覆盖传入的值名）；backend 显式指定时切换保存位置并清空其他两处残留；返回 (backend, location)
  clear_all()  删除全部值（注册表、appdata、用户主目录三处一起清除）
  storage_backend()  查询当前保存设置将使用的后端（registry / appdata / home）

读取编制/审核时兼容旧键 HKCU\\Software\\NCPostProcess（仅针对默认键生效）。
主窗口快捷开关（递归扫描、保存 APTSOURCE、G00 级别等）不持久化，仅本次运行生效。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Dict, Optional

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
    "ask_backup": "1",
    "required_bianzhi": "1",
    "required_shenhe": "1",
    "required_drawing": "1",
    "required_part": "1",
    "m03_position": "after-s",
    "feed_min": "",
    "feed_max": "",
    "spindle_min": "",
    "spindle_max": "",
    "newline": "auto",
    "aux_m03_before_motion": "1",
    "aux_m05_before_end": "1",
    "aux_m08_before_cut": "1",
    "aux_m09_before_end": "1",
    "feed_outlier_iqr_factor": "3",
    "feed_outlier_low_ratio": "0.1",
    "feed_outlier_high_ratio": "3",
    "multiple_spindle_warn": "1",
    # WP-C1：单文件大小上限（字节）与单次扫描文件数上限（留空 = 不限制）。
    "max_file_size": "",
    "max_files": "",
    # WP-C9：抬刀高度阈值（Z 达到该值视为移动/退刀阶段）。
    "retract_z_threshold": "20",
    # 用户显式选择的保存位置：registry / appdata / home；无此键时按可用性降级。
    "storage_backend": "registry",
}
REGISTRY_KEYS = tuple(REGISTRY_DEFAULTS)
BACKENDS = ("registry", "appdata", "home")


def _registry_paths(key: str) -> tuple:
    """默认键附带旧版编制/审核键用于兼容读取；自定义键只读自身。"""
    return (key,) + (LEGACY_KEYS if key == KEY else ())


def _backend_file(backend: str, key: str):
    """返回指定后端对应的设置文件路径；registry 返回 None。"""
    if backend == "registry":
        return None
    candidates = _settings_file_candidates(key)
    return candidates[0] if backend == "appdata" else candidates[-1]


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


def _write_settings_file_exact(values: Dict[str, str], path: Path) -> None:
    """覆盖式写入指定设置文件（保留文件已有值，只覆盖传入值名）。"""
    data = _read_settings_file(path)
    for name in REGISTRY_KEYS:
        if name in values:
            data[name] = str(values[name])
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def _load_registry(key: str) -> Dict[str, str]:
    """读取注册表中的全部值（含遗留键兼容）。"""
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
    return values


def _read_backend(backend: str, key: str) -> Dict[str, str]:
    """读取单个后端的全部已持久化值。"""
    if backend == "registry":
        return _load_registry(key)
    path = _backend_file(backend, key)
    return _read_settings_file(path) if path is not None else {}


def _selected_backend(key: str) -> str:
    """按存在性检测保存位置：注册表→appdata→home，第一个「有已持久化值」的后端即保存位置。

    显式切换（save_all backend=...）会在目标后端写入全部键（含 storage_backend），因此该
    后端必然被检测到；多后端同时残留（历史遗留/外部放置）时按此顺序优先，与「哪个位置
    有配置，哪个位置就是保存位置」的要求一致。
    """
    for backend in BACKENDS:
        values = _read_backend(backend, key)
        if values:
            return backend
    return "registry"


def _clear_backend(backend: str, key: str) -> None:
    """清空单个后端的全部已持久化值（含 storage_backend）。"""
    if backend == "registry":
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
    else:
        path = _backend_file(backend, key)
        if path is not None:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


def _appdata_writable(key: str) -> bool:
    """探测 %APPDATA%\\NCodeProcess 是否可写（用于降级选择）。"""
    path = _backend_file("appdata", key)
    if path is None:
        return False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        probe = path.with_name(path.name + ".probe")
        probe.write_text("", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


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
    """返回当前保存设置将使用的后端：(backend, location)。"""
    selected = _selected_backend(key)
    if selected == "registry" and not _registry_write_works(key):
        selected = "appdata" if _appdata_writable(key) else "home"
    if selected == "registry":
        return ("registry", None)
    return (selected, str(_backend_file(selected, key)))


def load_all(key: str = KEY) -> Dict[str, str]:
    """读取全部已持久化的值：按存在性检测选中的后端读取（单选，不跨后端合并）。"""
    return _read_backend(_selected_backend(key), key)


def save_all(values: Dict[str, str], key: str = KEY, backend: Optional[str] = None):
    """写入传入的值；只覆盖属于 REGISTRY_KEYS 的值名。

    backend 显式指定时切换保存位置并清空另外两处的残留配置（含 storage_backend）；
    未指定时沿用当前选择，无显式选择时按可用性降级（注册表 → appdata → home）。
    返回 (backend, location)。
    """
    explicit = backend is not None and backend in BACKENDS
    target = backend if explicit else _selected_backend(key)
    if not explicit and target == "registry" and not _registry_write_works(key):
        target = "appdata" if _appdata_writable(key) else "home"
    if explicit:
        for other in BACKENDS:
            if other != target:
                _clear_backend(other, key)
    payload = {name: str(values[name]) for name in REGISTRY_KEYS if name in values}
    if explicit:
        payload["storage_backend"] = target
    if target == "registry":
        if not _registry_write_works(key):
            return ("appdata" if _appdata_writable(key) else "home", str(_write_settings_file(payload, key)))
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key) as registry_key:
            for name in payload:
                winreg.SetValueEx(registry_key, name, 0, winreg.REG_SZ, str(payload[name]))
        # 注册表写成功后清除历史后备文件，避免旧会话遗留文件覆盖注册表值
        for candidate in _settings_file_candidates(key):
            try:
                candidate.unlink(missing_ok=True)
            except OSError:
                pass
        return ("registry", None)
    path = _backend_file(target, key)
    _write_settings_file_exact(payload, path)
    return (target, str(path))


def clear_all(key: str = KEY) -> None:
    """删除全部已持久化的值（注册表、appdata 与用户主目录三处一起清除）。"""
    for backend in BACKENDS:
        _clear_backend(backend, key)
