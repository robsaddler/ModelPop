"""Slicing through the Bambu Studio command line.

Satisfies the ``Slicer`` port. ModelPop does not implement slicing or support
generation (ADR-0006); we prepare a clean, oriented, scaled model and hand it to
a slicer that already does this well.

Everything here was verified against Bambu Studio 02.08.02.61 on the target
machine. The write-up, including the traps, is in
``docs/research/spike-bambu-cli.md``. The three that matter:

1. The executable is GUI-subsystem. It writes **nothing** to stdout or stderr
   and never sets a usable exit code. ``result.json`` is the only status.
2. ``result.json`` lands in ``--outputdir``, alongside the G-code, and falls
   back to the working directory only when no output directory is given. An
   earlier spike concluded it always went to the working directory; that was
   wrong, and only looked right because the two were the same folder.
3. Profile paths contain spaces and ``--load-settings`` takes a semicolon-joined
   pair as a single argument. Arguments go to ``subprocess`` as a **list**;
   building a command string by hand produced a misleading "input files not
   found" error that cost an afternoon.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from modelpop.application.ports import SliceJob, SliceObject, SliceReport
from modelpop.domain.printer import SupportType
from modelpop.domain.result import Result, failure, success
from modelpop.domain.units import Length

__all__ = ["BambuSlicer", "parse_result_json", "read_slice_info"]

_DEFAULT_EXE = Path(r"C:\Program Files\Bambu Studio\bambu-studio.exe")
_PROFILE_ROOT = Path(r"C:\Program Files\Bambu Studio\resources\profiles\BBL")

# Slicing a large model is slow, but it is not hours. A run that exceeds this has
# hung, and hanging silently is the worst outcome for an unattended pipeline.
_TIMEOUT_SECONDS = 900

_RETURN_CODE_MEANINGS = {
    0: "Success.",
    -3: (
        "The slicer could not find its input. This is almost always an argument-quoting "
        "problem rather than a missing file."
    ),
}


@dataclass(frozen=True, slots=True)
class BambuPaths:
    """Where Bambu Studio and its profiles live."""

    executable: Path = _DEFAULT_EXE
    profiles: Path = _PROFILE_ROOT

    def machine_profile(self, nozzle: float) -> Path:
        """The machine profile for a nozzle size."""
        return self.profiles / "machine" / f"Bambu Lab P2S {nozzle} nozzle.json"

    def process_profile(self, name: str) -> Path:
        """A process profile by name, such as ``0.20mm Standard @BBL P2S``."""
        return self.profiles / "process" / f"{name}.json"

    def filament_profile(self, name: str) -> Path:
        """A filament profile by name."""
        return self.profiles / "filament" / f"{name} @BBL P2S.json"


def parse_result_json(payload: dict[str, Any]) -> SliceReport:
    """Turn the slicer's ``result.json`` into a report.

    Kept separate from the subprocess call so it can be tested against recorded
    payloads without a slicer installed - which is what keeps the fast suite fast.
    """
    code = int(payload.get("return_code", -1))
    message = str(payload.get("error_string", "")) or _RETURN_CODE_MEANINGS.get(
        code, f"Slicer returned {code}."
    )
    if code != 0:
        return SliceReport(
            succeeded=False,
            message=_RETURN_CODE_MEANINGS.get(code, message),
        )

    plates = payload.get("sliced_plates") or []
    plate = plates[0] if plates else {}

    objects = tuple(
        SliceObject(
            name=str(obj.get("name", "object")),
            triangle_count=int(obj.get("triangle_count", 0)),
            width=Length.mm(float(obj.get("bbox", {}).get("width", 0.0))),
            depth=Length.mm(float(obj.get("bbox", {}).get("depth", 0.0))),
            height=Length.mm(float(obj.get("bbox", {}).get("height", 0.0))),
        )
        for obj in plate.get("objects", [])
    )

    filaments = plate.get("filaments", []) or []
    used = sum(float(f.get("main_used_g", 0.0) or 0.0) for f in filaments)
    total = sum(float(f.get("total_used_g", 0.0) or 0.0) for f in filaments)

    warnings = tuple(
        str(plate[key]) for key in ("warning_message",) if str(plate.get(key, "")).strip()
    )

    return SliceReport(
        succeeded=True,
        message=message,
        predicted_seconds=float(plate.get("total_predication", 0.0)),
        layer_height=Length.mm(float(payload.get("layer_height", 0.0))),
        wall_loops=int(payload.get("wall_loops", 0)),
        infill_density=float(payload.get("sparse_infill_density", 0.0)),
        # Deliberately not derived from generate_support_material_time: that
        # field is always non-zero because it times the support *stage*, which
        # runs even when no supports are produced. The authoritative answer
        # lives in the sliced project file; see read_slice_info.
        supports_generated=False,
        filament_change_count=int(plate.get("filament_change_times", 0)),
        grams_used=used,
        # total minus main is what the tool changes purged: the "poop".
        # Both come back as zero until the filament-binding issue is fixed.
        grams_purged=max(0.0, total - used),
        objects=objects,
        feature_seconds={
            str(k): float(v) for k, v in (plate.get("feature_type_times") or {}).items()
        },
        warnings=warnings,
    )


def read_slice_info(project: Path) -> dict[str, str]:
    """Read ``Metadata/slice_info.config`` out of a sliced 3MF.

    This is the slicer's own record of what it actually did, and it is the only
    reliable source for some facts. In particular ``support_used`` is correct
    here, whereas ``result.json``'s support timing is not.

    Returns an empty mapping if anything at all goes wrong: this is extra
    information, never a reason to fail a slice that otherwise succeeded.
    """
    try:
        with zipfile.ZipFile(project) as archive:
            text = archive.read("Metadata/slice_info.config").decode("utf-8", "replace")
    except (OSError, KeyError, zipfile.BadZipFile):
        return {}
    return dict(re.findall(r'key="([^"]+)"\s+value="([^"]*)"', text))


class BambuSlicer:
    """Drives the Bambu Studio CLI."""

    def __init__(self, paths: BambuPaths | None = None) -> None:
        """Create a slicer driver.

        Args:
            paths: where the executable and profiles live. The defaults match a
                standard Windows installation.
        """
        self._paths = paths or BambuPaths()

    # ------------------------------------------------------------ availability

    def is_available(self) -> bool:
        """Whether the slicer is installed and usable right now."""
        return self._paths.executable.is_file()

    def describe(self) -> str:
        """Which slicer this is, for diagnostics and bug reports."""
        if not self.is_available():
            return f"Bambu Studio (not found at {self._paths.executable})"
        return f"Bambu Studio at {self._paths.executable}"

    # ------------------------------------------------------------------ slice

    def slice(self, job: SliceJob) -> Result[SliceReport]:
        """Slice a model and report what happened."""
        if not self.is_available():
            return failure(
                "Bambu Studio is not installed",
                f"expected it at {self._paths.executable}. Install it, or choose "
                "another slicer in settings.",
            )
        if not job.model_path.is_file():
            return failure("Nothing to slice", f"{job.model_path} does not exist")

        machine = self._paths.machine_profile(job.printer.nozzle.value)
        process = self._paths.process_profile(job.printer.default_process_name())
        for label, profile in (("machine", machine), ("process", process)):
            if not profile.is_file():
                return failure(
                    f"Missing {label} profile",
                    f"{profile.name} was not found. The installed Bambu Studio may be "
                    "a different version from the one this was built against.",
                )

        job.output_dir.mkdir(parents=True, exist_ok=True)

        # A run leaves scratch data behind, so give it a throwaway working
        # directory of its own and delete it afterwards.
        workdir = Path(tempfile.mkdtemp(prefix="modelpop-slice-"))
        try:
            return self._run(job, self._flattened(machine, workdir), process, workdir)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    def _flattened(self, machine: Path, workdir: Path) -> Path:
        """The machine profile with its parents folded in, written out whole.

        **The CLI does not follow ``inherits``.** Bambu's own profiles are a
        chain - the leaf for a P2S with a 0.4 nozzle holds barely a dozen
        settings, and the bed lives on a parent shared across the range - so
        handing the CLI the leaf hands it a profile with no ``printable_area``
        in it at all, and it falls back to a default plate far smaller than the
        printer.

        The symptom is nothing like the cause. Slicing refuses "one of the
        plate is empty or has no object fully inside it" for a model sitting
        dead centre on a 256 mm bed, with no hint that the bed it is being
        measured against is not the one on screen. Measured: the ceiling is
        exactly 143 mm on either axis - 144 fails - whatever the other axis and
        the height are doing. Flattened, the same 238 x 194 mm model slices.

        Written beside the run rather than back into Bambu's own directory,
        which is under Program Files and not ours to edit.
        """
        flattened = _with_parents_folded_in(machine)
        if flattened is None:
            return machine
        written = workdir / "machine.json"
        written.write_text(json.dumps(flattened), encoding="utf-8")
        return written

    def _run(
        self, job: SliceJob, machine: Path, process: Path, workdir: Path
    ) -> Result[SliceReport]:
        """Invoke the CLI and read back its result file."""
        command = self._build_command(job, machine, process)

        try:
            subprocess.run(
                command,
                cwd=workdir,
                capture_output=True,
                timeout=_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return failure(
                "The slicer timed out",
                f"it ran for more than {_TIMEOUT_SECONDS // 60} minutes without finishing",
            )
        except OSError as exc:
            return failure("Could not start the slicer", f"{type(exc).__name__}: {exc}")

        # Trap 1: no stdout, no usable exit code. result.json is the only status.
        # It is written to --outputdir; the working directory is only a fallback.
        result_file = job.output_dir / "result.json"
        if not result_file.is_file():
            result_file = workdir / "result.json"
        if not result_file.is_file():
            return failure(
                "The slicer produced no result",
                "it exited without writing result.json, which usually means it could "
                "not start at all",
            )

        try:
            payload = json.loads(result_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return failure("Could not read the slicer result", f"{type(exc).__name__}: {exc}")

        report = parse_result_json(payload)
        if not report.succeeded:
            return failure("Slicing failed", report.message)

        project = self._find_output(job.output_dir, ".3mf")
        supports, grams = self._facts_from_project(project)
        return success(
            replace(
                report,
                gcode_path=self._find_output(job.output_dir, ".gcode"),
                project_path=project,
                supports_generated=supports,
                grams_used=grams if grams > 0 else report.grams_used,
            )
        )

    @staticmethod
    def _facts_from_project(project: Path | None) -> tuple[bool, float]:
        """Pull the facts ``result.json`` gets wrong out of the sliced project.

        Returns whether supports were actually used, and the reported weight in
        grams - zero when the slicer left it blank, which it does while the
        filament-binding issue persists.
        """
        if project is None:
            return (False, 0.0)
        info = read_slice_info(project)
        if not info:
            return (False, 0.0)

        supports = info.get("support_used", "").lower() == "true"
        try:
            grams = float(info.get("weight", "") or 0.0)
        except ValueError:
            grams = 0.0
        return (supports, grams)

    def _build_command(self, job: SliceJob, machine: Path, process: Path) -> list[str]:
        """Assemble the argument list.

        Trap 3: this must stay a list. ``--load-settings`` takes the machine and
        process profiles joined by a semicolon as one argument, and every profile
        path contains spaces.
        """
        command = [
            str(self._paths.executable),
            "--load-settings",
            f"{machine};{process}",
        ]

        filament = self._paths.filament_profile(job.printer.default_filament.name)
        if filament.is_file():
            command += ["--load-filaments", str(filament)]

        if job.supports is not SupportType.NONE:
            command += ["--enable-support", "--support-type", job.supports.value]

        command += [
            "--orient",
            "1" if job.auto_orient else "0",
            "--arrange",
            "1" if job.auto_arrange else "0",
            "--slice",
            str(job.plate),
            "--export-3mf",
            "sliced.3mf",
            "--outputdir",
            str(job.output_dir),
            str(job.model_path),
        ]
        return command

    @staticmethod
    def _find_output(directory: Path, suffix: str) -> Path | None:
        """The most recently written file with a given suffix."""
        candidates = sorted(
            (p for p in directory.glob(f"*{suffix}") if p.is_file()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        return candidates[0] if candidates else None


def find_bambu_studio() -> Path | None:
    """Locate Bambu Studio, checking the default install and then PATH."""
    if _DEFAULT_EXE.is_file():
        return _DEFAULT_EXE
    found = shutil.which("bambu-studio")
    if found:
        return Path(found)
    program_files = os.environ.get("PROGRAMFILES")
    if program_files:
        candidate = Path(program_files) / "Bambu Studio" / "bambu-studio.exe"
        if candidate.is_file():
            return candidate
    return None


def _with_parents_folded_in(profile: Path) -> dict[str, Any] | None:
    """One Bambu profile with everything it inherits merged into it.

    Nearest wins, and ``inherits`` itself is dropped so nothing downstream goes
    looking for a parent that is no longer needed. Depth-limited and
    cycle-guarded, because a profile that inherits from itself would otherwise
    take the application down rather than a print job.

    ``None`` when the chain cannot be read at all, which leaves the caller
    passing the original file - no worse off than before this existed.
    """
    try:
        folded = _fold(profile.stem, {f.stem: f for f in profile.parent.glob("*.json")})
    except (OSError, ValueError, RecursionError):
        return None
    if not folded:
        return None
    folded.pop("inherits", None)
    return folded


def _fold(stem: str, files: dict[str, Path], seen: tuple[str, ...] = ()) -> dict[str, Any]:
    """A profile and its ancestors, oldest first so the leaf wins."""
    if stem in seen or stem not in files:
        return {}
    settings: dict[str, Any] = json.loads(files[stem].read_text(encoding="utf-8"))
    parent = _fold(str(settings.get("inherits", "")), files, (*seen, stem))
    return {**parent, **settings}
