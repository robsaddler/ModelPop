"""The application's own icon, and getting Windows to use it.

Two separate problems, and only the first is obvious.

Setting a window icon is one line. But on Windows the *taskbar* groups windows
by an application identity, and a Python process that never claims one inherits
the interpreter's - so the taskbar button, the alt-tab entry and any pinned
shortcut all show ``pythonw.exe``'s icon no matter what the window says. The
identity has to be claimed before the first window appears.

The icon itself is drawn by ``tools/make_icon.py`` rather than being a binary
nobody can edit.
"""

from __future__ import annotations

import contextlib
import sys
from functools import cache
from pathlib import Path

from PySide6.QtGui import QIcon

__all__ = ["APP_ID", "claim_the_taskbar", "icon", "icon_file"]

# Vendor.Product.Component.Version, which is the shape Windows documents. It
# only has to be stable and unique: change it and pinned shortcuts stop
# matching the running application.
APP_ID = "ModelPop.ModelPop.Desktop.1"

_RESOURCES = Path(__file__).resolve().parent / "resources"


def icon_file() -> Path:
    """Where the icon lives.

    ``.ico`` on Windows, which holds every size in one file and is what the
    taskbar wants; the ``.png`` elsewhere.
    """
    windows = _RESOURCES / "modelpop.ico"
    if sys.platform == "win32" and windows.is_file():
        return windows
    return _RESOURCES / "modelpop.png"


@cache
def icon() -> QIcon:
    """The application icon, or an empty one if it is missing.

    Cached: Qt reads and decodes every size in the file, and this is asked for
    once per window. An empty ``QIcon`` is a perfectly good fallback - a
    missing icon is not a reason to fail to start.
    """
    path = icon_file()
    return QIcon(str(path)) if path.is_file() else QIcon()


def claim_the_taskbar(app_id: str = APP_ID) -> bool:
    """Tell Windows this process is its own application, not Python.

    Must happen before any window is shown. Returns whether it worked, which
    is only of interest to a test - nothing about the application depends on
    it, and on anything but Windows there is nothing to do.
    """
    if sys.platform != "win32":
        return False

    import ctypes

    with contextlib.suppress(AttributeError, OSError):
        shell = ctypes.windll.shell32
        shell.SetCurrentProcessExplicitAppUserModelID(app_id)
        return True
    return False
