"""Runtime hardening hook for the frozen NCodeProcessReportViewer executable."""

from __future__ import annotations

import ctypes
import os
import sys
import threading


_EXIT_CODE = 0x5A


def _debugger_attached() -> bool:
    if os.name != "nt":
        return False
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        if bool(kernel32.IsDebuggerPresent()):
            return True
        remote = ctypes.c_int(0)
        check_remote = kernel32.CheckRemoteDebuggerPresent
        check_remote.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
        check_remote.restype = ctypes.c_int
        return bool(check_remote(kernel32.GetCurrentProcess(), ctypes.byref(remote)) and remote.value)
    except Exception:
        return False


def _install() -> None:
    if not getattr(sys, "frozen", False):
        return
    for name in ("PYTHONPATH", "PYTHONHOME", "PYTHONINSPECT", "PYTHONBREAKPOINT"):
        os.environ.pop(name, None)

    def _blocked(*_args, **_kwargs):
        return None

    sys.settrace(None)
    sys.setprofile(None)
    sys.settrace = _blocked
    sys.setprofile = _blocked
    try:
        threading.settrace(None)
        threading.setprofile(None)
    except Exception:
        pass

    def _watchdog() -> None:
        while True:
            if _debugger_attached():
                try:
                    ctypes.WinDLL("user32", use_last_error=True).MessageBoxW(
                        None, "应用程序无法在调试环境中运行。", "NCodeProcessReportViewer", 0x10
                    )
                except Exception:
                    pass
                os._exit(_EXIT_CODE)
            threading.Event().wait(3.0)

    threading.Thread(target=_watchdog, name="security-watchdog", daemon=True).start()


_install()
