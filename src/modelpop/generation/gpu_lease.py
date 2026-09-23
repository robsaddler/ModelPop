"""One large model on the GPU at a time.

The card has 16 GB. A single image-to-3D model wants most of it, and two at once
does not fail cleanly - it fails as an out-of-memory error partway through,
after the user has waited a minute, and sometimes it takes the driver with it.

So generation holds a lease. Two guards, because there are two ways to collide:

**Within one app**, a lock, since a user can click twice or a prompt and an
image can be queued together.

**Across apps**, a lock file naming the process that holds it. Someone running
ModelPop twice, or leaving a training script going in another window, is not a
strange thing to do. A stale file from a process that has died is ignored rather
than blocking forever, which is the failure mode of every naive lock file.
"""

from __future__ import annotations

import json
import os
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

__all__ = ["GpuBusyError", "GpuLease"]

# Long enough that a slow first run - which loads weights from disk - does not
# look abandoned, short enough that a crash does not block the feature until a
# reboot.
STALE_AFTER_SECONDS = 1800.0


class GpuBusyError(RuntimeError):
    """The card is already in use.

    An exception rather than a ``Result`` because it is raised inside a
    context manager, where there is nothing to return. Callers turn it into a
    message at the boundary.
    """


@dataclass
class GpuLease:
    """Exclusive use of the graphics card, for as long as it is held."""

    path: Path
    """The lock file. One per machine, beside the application's own data."""

    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @classmethod
    def beside(cls, directory: Path) -> GpuLease:
        """A lease kept in a given directory."""
        return cls(path=directory / "gpu.lock")

    @property
    def holder(self) -> int | None:
        """The process id holding the lease, or ``None`` if it is free.

        A lease whose process no longer exists, or which is older than the
        staleness window, reads as free. Both happen: a crash leaves the file
        behind, and a process id gets reused.
        """
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

        pid = raw.get("pid")
        taken = raw.get("at")
        if not isinstance(pid, int) or not isinstance(taken, int | float):
            return None
        if time.time() - taken > STALE_AFTER_SECONDS:
            return None
        if not _is_running(pid):
            return None
        return pid

    @property
    def is_free(self) -> bool:
        """Whether a generation could start right now."""
        return self.holder is None

    def describe(self) -> str:
        """Why the card is unavailable, in words."""
        holder = self.holder
        if holder is None:
            return "The graphics card is free."
        if holder == os.getpid():
            return "A generation is already running."
        return f"Another program is using the graphics card (process {holder})."

    @contextmanager
    def held(self) -> Iterator[None]:
        """Hold the lease for the duration of a block.

        Raises:
            GpuBusyError: if something else already holds it.
        """
        if not self._lock.acquire(blocking=False):
            raise GpuBusyError("A generation is already running.")
        try:
            holder = self.holder
            if holder is not None and holder != os.getpid():
                raise GpuBusyError(self.describe())

            self._take()
            try:
                yield
            finally:
                self._release()
        finally:
            self._lock.release()

    def _take(self) -> None:
        """Write the lock file, best effort.

        A lease that cannot be written is not a reason to refuse to generate.
        The in-process lock still holds, which covers the common case; the file
        only guards against a second copy of the application.
        """
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps({"pid": os.getpid(), "at": time.time()}), encoding="utf-8"
            )
        except OSError:
            pass

    def _release(self) -> None:
        """Remove the lock file, best effort."""
        try:
            if self.holder == os.getpid() or self.path.exists():
                self.path.unlink(missing_ok=True)
        except OSError:
            pass


def _is_running(pid: int) -> bool:
    """Whether a process id is alive.

    ``os.kill`` with signal zero is the portable check and does not signal
    anything. A permission error means it exists and belongs to someone else,
    which still counts as running.
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True
