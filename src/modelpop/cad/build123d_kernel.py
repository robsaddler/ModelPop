"""The CAD kernel: build123d over OCCT.

Satisfies the ``CadKernel`` port. Measured in spike S7: fillets all twelve edges
of a cube in 7 ms, booleans exact to the last digit, native 3MF export - all as
a precompiled wheel, with no C++ toolchain anywhere.

Scripts run in a **subprocess**. Generated code is untrusted by definition: it
may loop forever, exhaust memory, or crash OCCT outright. None of those may take
the application down, and a timeout has to be able to end it.
"""

from __future__ import annotations

import contextlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import numpy as np

from modelpop.application.cad_ports import ScriptResult, SolidMeasurements
from modelpop.domain.mesh import Mesh
from modelpop.domain.result import Result, failure, success
from modelpop.domain.units import Length

__all__ = ["Build123dKernel"]

_WORKER_MODULE = "modelpop.cad._worker"

# Patterns that have no business in a generated CAD script. This is a tripwire,
# not a sandbox: the subprocess boundary is the actual containment. It exists to
# catch a model that has been talked into doing something odd, and to make that
# visible rather than silent.
_FORBIDDEN = (
    "import os",
    "import sys",
    "import subprocess",
    "import socket",
    "import shutil",
    "import requests",
    "import urllib",
    "__import__",
    "eval(",
    "exec(",
    "open(",
    "compile(",
    "globals(",
    "locals(",
)


class Build123dKernel:
    """Runs build123d scripts out of process and measures what they produce."""

    def __init__(self, python: Path | None = None, workspace: Path | None = None) -> None:
        """Create the kernel.

        Args:
            python: interpreter to run scripts with. Defaults to the one running
                the application, which is what has build123d installed.
            workspace: where job folders are created. A temporary directory by
                default.
        """
        self._python = python or Path(sys.executable)
        self._workspace = workspace
        # Both are settled once: starting an interpreter to import OCCT is
        # two seconds, and neither answer changes while the app is running.
        self._available: bool | None = None
        self._described: str | None = None

    # ------------------------------------------------------------ availability

    def is_available(self) -> bool:
        """Whether build123d can be imported by the worker interpreter.

        **Answered once and remembered.** Asking costs an interpreter start and
        a full OCCT import - measured at 1.9 seconds on this machine - and the
        answer cannot change while the application is running.

        Left uncached it was catastrophic rather than merely wasteful. The
        interface asks it through ``can_build`` from several places on every
        refresh, so a single click on *Sphere* spawned **eleven** of these plus
        the one rebuild that did the work: twenty-three seconds, of which
        twenty-one were spent importing the same library over and over. Worse,
        they run on the interface thread, so the window could not repaint and
        the model appeared to draw wrong until it got a turn.
        """
        if self._available is None:
            self._available = self._probe()
        return self._available

    def _probe(self) -> bool:
        """Actually ask, by starting an interpreter and importing."""
        try:
            completed = subprocess.run(
                [str(self._python), "-c", "import build123d"],
                capture_output=True,
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return completed.returncode == 0

    def forget_availability(self) -> None:
        """Ask again next time.

        For a test that installs or removes the kernel underneath a live
        object. Nothing in the application calls it: a kernel that appears
        while the window is open is a restart, not a state change.
        """
        self._available = None

    def describe(self) -> str:
        """Which kernel is in use, for diagnostics.

        Remembered for the same reason as ``is_available``: it starts an
        interpreter and imports OCCT to read a version string.
        """
        if self._described is not None:
            return self._described
        self._described = self._ask_version()
        return self._described

    def _ask_version(self) -> str:
        """Start an interpreter and read the version out of it."""
        try:
            completed = subprocess.run(
                [
                    str(self._python),
                    "-c",
                    "import build123d, OCP; print(build123d.__version__)",
                ],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return "build123d (unavailable)"
        if completed.returncode != 0:
            return "build123d (unavailable)"
        return f"build123d {completed.stdout.strip()} over OCCT"

    # -------------------------------------------------------------------- run

    def check_script(self, script: str) -> Result[str]:
        """Reject a script that reaches for something a CAD script never needs.

        A tripwire rather than a sandbox. The subprocess boundary is the real
        containment; this makes a suspicious script visible instead of silent,
        and gives the generate-and-correct loop a clear message to work with.
        """
        if not script.strip():
            return failure("Empty script", "the model produced nothing to run")

        lowered = script.lower()
        found = [pattern for pattern in _FORBIDDEN if pattern in lowered]
        if found:
            return failure(
                "The script does something a CAD script should not",
                f"it references {', '.join(sorted(found))}",
            )
        return success(script)

    def run(self, script: str, timeout_seconds: float = 60.0) -> Result[ScriptResult]:
        """Execute a script in a subprocess and measure the solid it produced."""
        checked = self.check_script(script)
        if not checked.ok:
            return checked  # type: ignore[return-value]

        job_dir = Path(tempfile.mkdtemp(prefix="modelpop-cad-", dir=self._workspace))
        try:
            return self._run_in(job_dir, script, timeout_seconds)
        finally:
            shutil.rmtree(job_dir, ignore_errors=True)

    def _run_in(self, job_dir: Path, script: str, timeout: float) -> Result[ScriptResult]:
        """Run one job in a prepared directory."""
        job_file = job_dir / "job.json"
        job_file.write_text(
            json.dumps({"script": script, "output_dir": str(job_dir)}), encoding="utf-8"
        )

        try:
            completed = subprocess.run(
                [str(self._python), "-m", _WORKER_MODULE, str(job_file)],
                cwd=job_dir,  # confine anything the script writes
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return failure(
                "The script did not finish",
                f"it ran for more than {timeout:.0f} seconds. It may contain a loop "
                "that never ends, or ask for geometry too complex to build.",
            )
        except OSError as exc:
            return failure("Could not run the script", f"{type(exc).__name__}: {exc}")

        outcome_file = job_file.with_suffix(".result.json")
        if not outcome_file.is_file():
            return failure(
                "The script produced no result",
                (completed.stderr or "the worker exited without reporting anything")[:500],
            )

        outcome = json.loads(outcome_file.read_text(encoding="utf-8"))
        if not outcome.get("ok"):
            return failure(
                "The script failed",
                str(outcome.get("error", "no reason given")),
            )

        return self._collect(outcome, completed.stdout)

    def _collect(self, outcome: dict[str, Any], stdout: str) -> Result[ScriptResult]:
        """Load the worker's output before its directory is swept away."""
        stl_path = Path(outcome["stl"])
        if not stl_path.is_file():
            return failure("The script produced no geometry", "no mesh was written")

        mesh = self._read_stl(stl_path)
        if mesh is None:
            return failure("The script produced unreadable geometry", str(stl_path.name))

        raw = outcome["measurements"]
        measurements = SolidMeasurements(
            volume_mm3=float(raw["volume_mm3"]),
            width=Length.mm(float(raw["width"])),
            depth=Length.mm(float(raw["depth"])),
            height=Length.mm(float(raw["height"])),
            face_count=int(raw.get("face_count", 0)),
            edge_count=int(raw.get("edge_count", 0)),
            vertex_count=int(raw.get("vertex_count", 0)),
            solid_count=int(raw.get("solid_count", 1)),
            is_valid=bool(raw.get("is_valid", True)),
        )

        step_path = self._keep(outcome.get("step"))
        return success(
            ScriptResult(
                mesh=mesh,
                measurements=measurements,
                step_path=step_path,
                stdout=stdout[:4000],
                duration_seconds=float(outcome.get("duration_seconds", 0.0)),
            )
        )

    @staticmethod
    def _read_stl(path: Path) -> Mesh | None:
        """Read the worker's STL, merging its vertices.

        STL stores three independent vertices per triangle and shares nothing.
        Loaded without merging, a perfectly sound solid has no shared edges at
        all and every watertight check fails - which made every generated part
        look broken until this was tracked down.
        """
        import trimesh

        try:
            body = trimesh.load(path, force="mesh", process=False)
        except Exception:
            return None
        # force="mesh" should always give a Trimesh, but the signature permits a
        # Scene or a point cloud, either of which would fail obscurely below.
        if not isinstance(body, trimesh.Trimesh) or len(body.faces) == 0:
            return None

        with contextlib.suppress(Exception):
            # merging is an improvement, not a requirement
            body.merge_vertices()

        return Mesh(
            np.asarray(body.vertices, dtype=np.float64),
            np.asarray(body.faces, dtype=np.int32),
        )

    @staticmethod
    def _keep(step: str | None) -> Path | None:
        """Copy the STEP file somewhere it will survive the job directory."""
        if not step:
            return None
        source = Path(step)
        if not source.is_file():
            return None
        destination = Path(tempfile.gettempdir()) / f"modelpop-{source.stem}-{id(source)}.step"
        try:
            shutil.copy2(source, destination)
        except OSError:
            return None
        return destination
