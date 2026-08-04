"""Small user preference store without a config file in the NC directory."""

from __future__ import annotations

import sys
from typing import Dict

KEY = r"Software\NCodeProcess"
LEGACY_KEYS = (r"Software\NCPostProcess",)
FIELDS = ("bianzhi", "shenhe")


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
