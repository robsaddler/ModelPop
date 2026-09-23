"""Where ModelPop keeps things on this machine.

Deliberately outside every layer. Two different adapters need to know where the
application's own data lives - the licensing record and the picture generator -
and having one import the other to find out would make them depend on each other
for no reason beyond a shared constant.

Nothing here touches the file system. It answers "where would that go", which is
a question about the platform, not about any feature.
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = ["app_data_dir"]


def app_data_dir(windows: bool | None = None) -> Path:
    """Where ModelPop keeps per-user data.

    ``LOCALAPPDATA`` on Windows, ``XDG_DATA_HOME`` or its documented default
    elsewhere. Resolved at call time rather than at import so a test can point
    it somewhere harmless.

    Args:
        windows: which convention to follow. Detected from the platform when
            not given. It is a parameter because patching ``os.name`` for a
            test also changes which ``Path`` subclass gets built, which fails
            the test for a reason that has nothing to do with the code.
    """
    on_windows = os.name == "nt" if windows is None else windows
    if on_windows:
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / "ModelPop"
    else:
        xdg = os.environ.get("XDG_DATA_HOME")
        if xdg:
            return Path(xdg) / "modelpop"
    return Path.home() / ".local" / "share" / "modelpop"
