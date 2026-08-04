"""Runtime hardening hook for the frozen NCodeProcess executable.

This hook is intentionally conservative: it only activates in a frozen build,
so normal source execution and unit tests remain debuggable.  It blocks the
most common debugger attachment paths and suppresses Python tracing hooks.
It is a deterrent, not a cryptographic guarantee; a determined analyst can
still patch a native process.
"""

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
        if check_remote(kernel32.GetCurrentProcess(), ctypes.byref(remote)) and remote.value:
            return True
    except Exception:
        # Security checks must never prevent the application from starting on
        # an unusual Windows compatibility layer.
        return False
    return False


def _terminate_if_debugged() -> None:
    if not _debugger_attached():
        return
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.MessageBoxW(None, "应用程序无法在调试环境中运行。", "NCodeProcess", 0x10)
    except Exception:
        pass
    os._exit(_EXIT_CODE)


def _disable_tracing() -> None:
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


def _install() -> None:
    if not getattr(sys, "frozen", False):
        return
    # Prevent environment-provided import/debug helpers from influencing the
    # frozen application.  PyInstaller does not need these variables.
    for name in ("PYTHONPATH", "PYTHONHOME", "PYTHONINSPECT", "PYTHONBREAKPOINT"):
        os.environ.pop(name, None)
    _disable_tracing()
    _terminate_if_debugged()

    # Detect a debugger attached after startup as well.  The polling interval
    # is deliberately long enough to be negligible for the GUI workload.
    def _watchdog() -> None:
        while True:
            if _debugger_attached():
                _terminate_if_debugged()
            threading.Event().wait(3.0)

    watcher = threading.Thread(target=_watchdog, name="security-watchdog", daemon=True)
    watcher.start()


_install()
