"""The script that runs inside the generation environment.

**This file never runs in ModelPop's own interpreter.** It is handed to a
separate Python that has PyTorch, CUDA and a generative model installed, and it
talks back over stdout. Nothing here may import anything from ``modelpop``,
because that package is not installed in that environment and never will be.

The protocol is deliberately dull: a JSON request on the command line, JSON
lines on stdout, and a mesh written to a file the caller named.

    {"phase": "loading", "fraction": 0.1, "message": "loading the model"}
    {"phase": "done", "output": "C:/.../out.glb", "seconds": 41.2, "seed": 7}
    {"phase": "failed", "error": "...", "detail": "..."}

Progress lines matter more than they look. Generation takes tens of seconds and
a window with no sign of life reads as a crash, so the worker reports each phase
as it starts rather than only at the end.

Which model actually runs is chosen at the bottom, by what is importable. The
adapter does not care, and a new backend is a function here plus a name.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from typing import Any

# The ceiling ModelPop asks for. These models produce millions of triangles
# happily, and a printer cannot use them - the slicer only takes longer to throw
# the detail away.
DEFAULT_TRIANGLE_BUDGET = 200_000

_DETAIL_STEPS = {"draft": 12, "standard": 25, "fine": 50}


def report(phase: str, **fields: Any) -> None:
    """Send one line back to the caller.

    Flushed every time: the caller is reading line by line to drive a progress
    bar, and a buffered pipe would deliver the whole run at the end.
    """
    print(json.dumps({"phase": phase, **fields}), flush=True)


def progress(fraction: float, message: str) -> None:
    """Report how far along the run is."""
    report("progress", fraction=round(max(0.0, min(fraction, 1.0)), 3), message=message)


def main(argv: list[str] | None = None) -> int:
    """Run one generation and report the result."""
    parser = argparse.ArgumentParser(description="ModelPop mesh generation worker")
    parser.add_argument("--request", required=True, help="the request, as JSON")
    parser.add_argument("--probe", action="store_true", help="report what is installed and exit")
    arguments = parser.parse_args(argv)

    if arguments.probe:
        report("probe", **probe())
        return 0

    try:
        request = json.loads(arguments.request)
    except ValueError as error:
        report("failed", error="The request could not be read", detail=str(error))
        return 2

    started = time.perf_counter()
    try:
        output, seed, notes = generate(request)
    except MemoryError:
        report(
            "failed",
            error="The graphics card ran out of memory",
            detail="Try a lower detail setting, or close anything else using the card.",
        )
        return 3
    except Exception as error:
        report("failed", error="Generation failed", detail=f"{type(error).__name__}: {error}")
        return 4

    report(
        "done",
        output=str(output),
        seconds=round(time.perf_counter() - started, 2),
        seed=seed,
        notes=notes,
    )
    return 0


def probe() -> dict[str, Any]:
    """What this environment can actually do.

    Called with ``--probe`` before anything else, so the application can say
    "no CUDA" or "weights not downloaded" instead of failing at the click.
    """
    found: dict[str, Any] = {"python": sys.version.split()[0], "backends": []}

    try:
        import torch
    except ImportError:
        # Not installed, which is the ordinary state of a machine where nobody
        # has set this up. Deliberately NOT reported as an error: "PyTorch is
        # missing" and "PyTorch is broken" need different answers from the user,
        # and collapsing them sends people to fix the wrong thing.
        return found
    except Exception as error:
        found["torch_error"] = f"{type(error).__name__}: {error}"
        return found

    try:
        found["torch"] = torch.__version__
        found["cuda"] = bool(torch.cuda.is_available())
        if found["cuda"]:
            found["device"] = torch.cuda.get_device_name(0)
            found["vram_gb"] = round(torch.cuda.get_device_properties(0).total_memory / 1024**3, 1)
    except Exception as error:
        # Installed but unusable - a driver mismatch, a broken CUDA build. This
        # one IS an error, and a different one from being absent.
        found["torch_error"] = f"{type(error).__name__}: {error}"

    for name, check in (("trellis", _has_trellis), ("triposg", _has_triposg)):
        try:
            if check():
                found["backends"].append(name)
        except Exception:
            pass

    return found


def _has_trellis() -> bool:
    """Whether TRELLIS is importable here."""
    import importlib.util

    return importlib.util.find_spec("trellis") is not None


def _has_triposg() -> bool:
    """Whether TripoSG is importable here."""
    import importlib.util

    return importlib.util.find_spec("triposg") is not None


def generate(request: dict[str, Any]) -> tuple[str, int, list[str]]:
    """Run one generation, whichever backend is present."""
    output = request["output"]
    seed = int(request.get("seed") or random.randrange(1, 2**31 - 1))
    steps = _DETAIL_STEPS.get(str(request.get("detail", "standard")), 25)
    budget = int(request.get("target_triangles") or DEFAULT_TRIANGLE_BUDGET)

    progress(0.05, "starting up")

    if _has_trellis():
        notes = _run_trellis(request, output, seed, steps, budget)
    elif _has_triposg():
        notes = _run_triposg(request, output, seed, steps, budget)
    else:
        raise RuntimeError(
            "No generator is installed in this environment. "
            "See docs/10-mesh-generation.md for how to set one up."
        )

    return output, seed, notes


def _run_trellis(
    request: dict[str, Any], output: str, seed: int, steps: int, budget: int
) -> list[str]:
    """Generate with TRELLIS.

    Written against its documented pipeline interface. Kept in one function so
    that when the interface moves - and it will, these projects move fast - the
    change is here and nothing else knows.
    """
    import torch
    from trellis.pipelines import TrellisImageTo3DPipeline

    progress(0.15, "loading the model")
    pipeline = TrellisImageTo3DPipeline.from_pretrained(
        request.get("weights") or "microsoft/TRELLIS-image-large"
    )
    pipeline.cuda()

    torch.manual_seed(seed)
    progress(0.35, "imagining the shape")

    image = _load_image(request)
    result = pipeline.run(
        image,
        seed=seed,
        sparse_structure_sampler_params={"steps": steps},
        slat_sampler_params={"steps": steps},
    )

    progress(0.8, "building the surface")
    return _write_mesh(result, output, budget)


def _run_triposg(
    request: dict[str, Any], output: str, seed: int, steps: int, budget: int
) -> list[str]:
    """Generate with TripoSG, the fallback backend."""
    import torch
    from triposg.pipelines import TripoSGPipeline

    progress(0.15, "loading the model")
    pipeline = TripoSGPipeline.from_pretrained(request.get("weights") or "VAST-AI/TripoSG")
    pipeline.to("cuda")

    torch.manual_seed(seed)
    progress(0.35, "imagining the shape")

    result = pipeline(image=_load_image(request), num_inference_steps=steps, generator=None)

    progress(0.8, "building the surface")
    return _write_mesh(result, output, budget)


def _load_image(request: dict[str, Any]) -> Any:
    """The input image, with its background removed if asked.

    A photo's background otherwise becomes part of the model - the single most
    common way an image-to-3D result comes out wrong.
    """
    from PIL import Image

    path = request.get("image")
    if not path:
        raise RuntimeError(
            "This backend needs an image. Describe the shape to a language model "
            "first, or supply a photo."
        )

    image = Image.open(path).convert("RGBA")
    if request.get("remove_background", True):
        image = _cut_out(image)
    return image


def _cut_out(image: Any) -> Any:
    """Remove the background, if a remover is installed.

    Not a hard requirement: a picture already on a plain background works
    without it, and refusing to run because an optional package is missing
    would be worse than a slightly worse result.
    """
    try:
        import rembg
    except ImportError:
        progress(0.25, "no background remover installed; using the image as it is")
        return image

    progress(0.25, "cutting the subject out")
    return rembg.remove(image)


def _write_mesh(result: Any, output: str, budget: int) -> list[str]:
    """Get a mesh out of whatever the pipeline returned, and save it.

    Pipelines differ in what they hand back - a dict of representations, an
    object with a ``.mesh``, or a mesh directly - so this looks for something
    that can be written rather than insisting on one shape.
    """
    mesh = _find_mesh(result)
    if mesh is None:
        raise RuntimeError("The generator produced no mesh.")

    notes: list[str] = []
    before = len(mesh.faces)
    if before > budget:
        mesh = mesh.simplify_quadric_decimation(face_count=budget)
        notes.append(f"Simplified from {before:,} to {len(mesh.faces):,} triangles for printing.")

    mesh.export(output)
    return notes


def _find_mesh(result: Any) -> Any:
    """The first thing in a pipeline result that looks like a mesh."""
    import trimesh

    if isinstance(result, trimesh.Trimesh):
        return result

    candidates: list[Any] = []
    if isinstance(result, dict):
        candidates.extend(result.values())
    for attribute in ("mesh", "meshes", "glb", "trimesh"):
        candidates.append(getattr(result, attribute, None))

    for candidate in candidates:
        if isinstance(candidate, trimesh.Trimesh):
            return candidate
        if isinstance(candidate, (list, tuple)) and candidate:
            found = _find_mesh(candidate[0])
            if found is not None:
                return found
        if candidate is not None and hasattr(candidate, "to_trimesh"):
            return candidate.to_trimesh()
    return None


if __name__ == "__main__":
    sys.exit(main())
