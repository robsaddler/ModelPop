"""The subprocess that actually executes a generated CAD script.

Runs in its own interpreter, deliberately. Generated code is untrusted: it may
loop forever, allocate everything, crash the CAD kernel, or simply be wrong. A
separate process means none of that can take the application down with it, and
a timeout can end it.

Invoked as ``python -m modelpop.cad._worker <job.json>``. Communicates through
files rather than pipes because the payload includes meshes.

Not imported by the application. Nothing here should be called in-process.
"""

from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path
from typing import Any

__all__ = ["main"]

# Names the script is allowed to see. Anything not listed here it must import
# for itself, which keeps the surface visible in the script rather than hidden
# in the harness.
_PREAMBLE = """
from build123d import *
"""


def _measure(solid: Any) -> dict[str, Any]:
    """Measure the solid the script produced."""
    bbox = solid.bounding_box()
    measurements: dict[str, Any] = {
        "volume_mm3": float(solid.volume),
        "width": float(bbox.size.X),
        "depth": float(bbox.size.Y),
        "height": float(bbox.size.Z),
    }
    # Topology counts are best-effort: some shapes refuse to enumerate, and a
    # missing count is far better than a failed run.
    for key, attribute in (
        ("face_count", "faces"),
        ("edge_count", "edges"),
        ("vertex_count", "vertices"),
    ):
        try:
            measurements[key] = len(getattr(solid, attribute)())
        except Exception:
            measurements[key] = 0
    try:
        measurements["solid_count"] = len(solid.solids())
    except Exception:
        measurements["solid_count"] = 1
    try:
        measurements["is_valid"] = bool(solid.is_valid())
    except Exception:
        measurements["is_valid"] = True
    return measurements


def _looks_like_a_solid(value: Any) -> bool:
    """Whether a value is a built shape rather than a class or a number.

    The ``from build123d import *`` preamble puts dozens of classes into the
    namespace, and several of them carry a ``volume`` property on the class
    itself. Without the ``isinstance(value, type)`` guard the fallback search
    happily returns ``Box`` the class, and the failure that follows is baffling.
    """
    if isinstance(value, type):
        return False
    return hasattr(value, "volume") and hasattr(value, "bounding_box")


def _find_result(namespace: dict[str, Any]) -> Any:
    """Find the solid the script meant to produce.

    Looks for a variable named ``result`` or ``part`` first, because that is
    what we tell the model to use. Falls back to the last shape-like object
    defined, so a script that simply builds something still works.
    """
    for name in ("result", "part", "model", "solid"):
        candidate = namespace.get(name)
        if _looks_like_a_solid(candidate):
            return candidate

    for key, value in reversed(list(namespace.items())):
        if key.startswith("_"):
            continue
        if _looks_like_a_solid(value):
            return value
    return None


def _find_bodies(namespace: dict[str, Any]) -> dict[str, Any]:
    """The scene's objects, if the script built a scene rather than one part.

    A scene script collects each object into ``results`` keyed by body id. One
    run of this worker therefore produces every object on the plate, which is
    the point: an OCCT rebuild is a couple of seconds and a maker with five
    objects must not wait five times that for one nudge.
    """
    found = namespace.get("results")
    if not isinstance(found, dict):
        return {}
    return {str(key): value for key, value in found.items() if _looks_like_a_solid(value)}


def _report_bodies(bodies: dict[str, Any], output_dir: Path, started: float) -> dict[str, Any]:
    """Measure and export every object the scene produced."""
    from build123d import export_stl

    reported: list[dict[str, Any]] = []
    for index, (body, solid) in enumerate(bodies.items()):
        try:
            measurements = _measure(solid)
        except Exception as exc:
            return {"ok": False, "error": f"{body} could not be measured: {exc}"}
        if measurements["volume_mm3"] <= 0:
            return {"ok": False, "error": f"{body} came out with no volume"}

        # Named by position rather than by body id: an id is user-visible text
        # and has no business being a file name.
        stl_path = output_dir / f"body-{index}.stl"
        try:
            export_stl(solid, str(stl_path), tolerance=0.01, angular_tolerance=0.1)
        except Exception as exc:
            return {"ok": False, "error": f"{body} could not be tessellated: {exc}"}

        reported.append({"body": body, "stl": str(stl_path), "measurements": measurements})

    return {
        "ok": True,
        "bodies": reported,
        "duration_seconds": time.perf_counter() - started,
    }


def run_job(job: dict[str, Any]) -> dict[str, Any]:
    """Execute one script and write its outputs."""
    started = time.perf_counter()
    output_dir = Path(job["output_dir"])
    script = job["script"]

    namespace: dict[str, Any] = {"__name__": "__modelpop_script__"}
    try:
        exec(compile(_PREAMBLE, "<preamble>", "exec"), namespace)
    except Exception as exc:
        return {"ok": False, "error": f"the CAD kernel failed to load: {exc}"}

    try:
        exec(compile(script, "<generated>", "exec"), namespace)
    except SyntaxError as exc:
        return {
            "ok": False,
            "error": f"the script does not parse: line {exc.lineno}: {exc.msg}",
            "traceback": traceback.format_exc(limit=3),
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(limit=5),
        }

    bodies = _find_bodies(namespace)
    if bodies:
        return _report_bodies(bodies, output_dir, started)

    solid = _find_result(namespace)
    if solid is None:
        return {
            "ok": False,
            "error": (
                "the script ran but produced no solid. Assign the finished part to a "
                "variable named `result`."
            ),
        }

    try:
        measurements = _measure(solid)
    except Exception as exc:
        return {"ok": False, "error": f"the result could not be measured: {exc}"}

    if measurements["volume_mm3"] <= 0:
        return {"ok": False, "error": "the script produced a solid with no volume"}

    from build123d import export_step, export_stl

    stl_path = output_dir / "result.stl"
    step_path: Path | None = output_dir / "result.step"
    try:
        export_stl(solid, str(stl_path), tolerance=0.01, angular_tolerance=0.1)
    except Exception as exc:
        return {"ok": False, "error": f"the solid could not be tessellated: {exc}"}

    try:
        export_step(solid, str(step_path))
    except Exception:
        step_path = None

    return {
        "ok": True,
        "measurements": measurements,
        "stl": str(stl_path),
        "step": str(step_path) if step_path else None,
        "duration_seconds": time.perf_counter() - started,
    }


def main() -> int:
    """Read a job file, run it, write the outcome beside it."""
    if len(sys.argv) != 2:
        print("usage: python -m modelpop.cad._worker <job.json>", file=sys.stderr)
        return 2

    job_path = Path(sys.argv[1])
    job = json.loads(job_path.read_text(encoding="utf-8"))
    try:
        outcome = run_job(job)
    except BaseException as exc:
        outcome = {"ok": False, "error": f"the worker crashed: {type(exc).__name__}: {exc}"}

    job_path.with_suffix(".result.json").write_text(json.dumps(outcome), encoding="utf-8")
    return 0 if outcome.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
