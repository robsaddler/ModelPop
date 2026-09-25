"""Exercise every capability, and report anything a library is missing for.

Importing the modules the source names is not enough, and this script exists
because that lesson was paid for. ``trimesh.voxel.marching_cubes`` needs
``scikit-image``; nothing in ModelPop imports ``skimage``, nothing declared it,
every import check passed, and repairing a model failed in front of a user with
``No module named 'skimage'``.

So this *runs* things. Each check does the smallest real piece of work that
touches its dependency and reports what happened. A missing optional feature is
reported as absent rather than as a failure - photogrammetry and mesh
generation are deliberately not installed here.

    .venv/Scripts/python.exe tools/check_dependencies.py
"""

from __future__ import annotations

import traceback
from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import numpy as np

# Small enough to be quick, closed enough to be repairable.
CUBE_VERTICES = np.array(
    [
        [-5.0, -5.0, 0.0],
        [5.0, -5.0, 0.0],
        [5.0, 5.0, 0.0],
        [-5.0, 5.0, 0.0],
        [-5.0, -5.0, 10.0],
        [5.0, -5.0, 10.0],
        [5.0, 5.0, 10.0],
        [-5.0, 5.0, 10.0],
    ]
)
CUBE_FACES = np.array(
    [
        [0, 3, 2], [0, 2, 1], [4, 5, 6], [4, 6, 7],
        [0, 1, 5], [0, 5, 4], [1, 2, 6], [1, 6, 5],
        [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7],
    ],
    dtype=np.int32,
)  # fmt: skip


def a_cube() -> Any:
    """A small closed box, the subject of every check below."""
    from modelpop.domain.mesh import Mesh

    return Mesh(CUBE_VERTICES, CUBE_FACES)


def check_reading_and_writing_every_format() -> str:
    """Each file type the open dialog offers, written and read back."""
    from modelpop.mesh import TrimeshIO

    io = TrimeshIO()
    mesh = a_cube()
    with TemporaryDirectory() as where:
        for suffix in (".stl", ".obj", ".ply", ".3mf", ".glb", ".off"):
            path = Path(where) / f"check{suffix}"
            written = io.save(mesh, path)
            if not written.ok:
                return f"cannot write {suffix}: {written.detail}"
            read = io.load(path)
            if not read.ok:
                return f"cannot read {suffix}: {read.detail}"
    return "every format reads and writes"


def check_repair() -> str:
    """Including the voxel last resort, which is what needed scikit-image."""
    from modelpop.mesh import TrimeshOps

    ops = TrimeshOps()
    # An open box: two faces removed, so hole filling has work to do and the
    # voxel path is reachable.
    from modelpop.domain.mesh import Mesh

    holed = Mesh(CUBE_VERTICES, CUBE_FACES[2:])
    fixed = ops.repair(holed)
    if not fixed.ok:
        return f"repair failed: {fixed.detail}"

    # And force the voxel rebuild directly, so a missing marching-cubes
    # backend cannot hide behind an easy repair.
    rebuilt = ops._voxel_remesh(a_cube())
    if not rebuilt.ok:
        return f"the voxel rebuild failed: {rebuilt.detail}"
    return "repair works, voxel rebuild included"


def check_simplify() -> str:
    """Quadric decimation, which has its own backend."""
    import trimesh

    from modelpop.domain.mesh import Mesh
    from modelpop.mesh import TrimeshOps

    sphere = trimesh.creation.icosphere(subdivisions=3)
    mesh = Mesh(np.asarray(sphere.vertices), np.asarray(sphere.faces, np.int32))
    reduced = TrimeshOps().decimate(mesh, 200)
    if not reduced.ok:
        return f"decimation failed: {reduced.detail}"
    return f"decimation works ({mesh.triangle_count} -> {reduced.unwrap().triangle_count})"


def check_thickening() -> str:
    """Growing a surface outwards, which is how thin walls are fixed."""
    from modelpop.domain.units import Length
    from modelpop.mesh import TrimeshOps

    ops = TrimeshOps()
    grown = ops.thicken(a_cube(), Length.mm(0.25))
    if not grown.ok:
        return f"thickening failed: {grown.detail}"
    got = grown.unwrap().bounds.height.millimetres
    if abs(got - 10.5) > 0.05:
        return f"thickening is wrong: a 10 mm cube grown 0.25 mm each side came out {got:.2f}"
    return "thickening works"


def check_booleans() -> str:
    """Manifold booleans, which cutting and merging parts depend on."""
    from modelpop.domain.mesh import Mesh
    from modelpop.mesh import TrimeshOps

    other = Mesh(CUBE_VERTICES + np.array([3.0, 0.0, 0.0]), CUBE_FACES)
    joined = TrimeshOps().union(a_cube(), other)
    if not joined.ok:
        return f"boolean union failed: {joined.detail}"
    return "booleans work"


def check_ray_casting() -> str:
    """Which engine trimesh casts rays with, which is a 63x difference.

    Not whether ray casting *works* - it always does. Without Embree, trimesh
    falls back to its own numpy intersector, which tests every ray against
    every triangle: measured at 81 seconds for the 2,000 rays the wall-
    thickness check needs on a 1.1 million triangle model, against 1.3 with
    it. Opening that model ran the check twice and took over two minutes,
    and nothing anywhere said why.
    """
    import trimesh.ray

    if not trimesh.ray.has_embree:
        return (
            "the slow ray engine is in use: embreex is not installed, so the "
            "wall-thickness check will take a minute on a large model"
        )
    return "ray casting is accelerated (Embree)"


def check_readiness() -> str:
    """Overhang and wall checks, which reach for ray casting and spatial trees."""
    from modelpop.domain.printer import PrinterProfile
    from modelpop.domain.readiness import assess
    from modelpop.mesh import TrimeshOps

    facts = TrimeshOps().inspect(a_cube())
    report = assess(facts, PrinterProfile.p2s())
    return f"readiness works ({len(report.findings)} findings on a cube)"


def check_the_cad_kernel() -> str:
    """build123d over OCCT, in its own interpreter."""
    from modelpop.cad import Build123dCompiler, Build123dKernel

    kernel = Build123dKernel()
    if not kernel.is_available():
        return "ABSENT: build123d could not be loaded"
    from modelpop.domain.cad_commands import CreateBox
    from modelpop.domain.commands import Document

    built = Build123dCompiler(kernel).build(CreateBox(10, 10, 10).apply(Document()))
    if not built.ok:
        return f"the kernel is there but a box failed: {built.detail}"
    return f"the CAD kernel works ({kernel.describe()})"


def check_the_viewport() -> str:
    """VTK actually rasterising something."""
    import pyvista as pv

    from modelpop.rendering import ViewportScene

    plotter = pv.Plotter(off_screen=True, window_size=(64, 64))
    try:
        ViewportScene(plotter)
        plotter.render()
    finally:
        plotter.close()
    return "the viewport draws"


def check_the_interface() -> str:
    """Qt, and the widget the viewport lives in."""
    import PySide6  # noqa: F401
    import pyvistaqt  # noqa: F401

    return "Qt and the Qt viewport are installed"


def check_the_assistant() -> str:
    """The "say what you want" path. Optional, but it is a button in the UI."""
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return "ABSENT: anthropic is not installed, so describing a part will fail"
    return "the assistant's client is installed"


def check_the_printer() -> str:
    """The MQTT client a print needs to be started remotely."""
    try:
        import paho.mqtt.client  # noqa: F401
    except ImportError:
        return "ABSENT: paho-mqtt is not installed, so a print cannot be started remotely"
    return "the printer client is installed"


def check_the_slicer() -> str:
    """Bambu Studio, which is driven as an external process."""
    from modelpop.printing.bambu_slicer import find_bambu_studio

    found = find_bambu_studio()
    if found is None:
        return "ABSENT: Bambu Studio was not found, so slicing will not run"
    return f"the slicer is there ({found.name})"


def check_photogrammetry() -> str:
    """COLMAP and OpenMVS, both external."""
    from modelpop.vision.photogrammetry import find_colmap, find_openmvs

    colmap, openmvs = find_colmap(), find_openmvs()
    if colmap is None or openmvs is None:
        return "ABSENT: COLMAP or OpenMVS was not found, so photographs cannot be measured"
    return "COLMAP and OpenMVS are both there"


def check_detail_rescue() -> str:
    """Turning a model's colour into relief."""
    from modelpop.mesh.detail_bake import TrimeshDetailBake

    TrimeshDetailBake()
    return "detail rescue is available"


CHECKS: tuple[tuple[str, Callable[[], str]], ...] = (
    ("interface", check_the_interface),
    ("viewport", check_the_viewport),
    ("file formats", check_reading_and_writing_every_format),
    ("repair", check_repair),
    ("simplify", check_simplify),
    ("booleans", check_booleans),
    ("thickening", check_thickening),
    ("ray casting", check_ray_casting),
    ("readiness", check_readiness),
    ("detail rescue", check_detail_rescue),
    ("CAD kernel", check_the_cad_kernel),
    ("assistant", check_the_assistant),
    ("printer", check_the_printer),
    ("slicer", check_the_slicer),
    ("photogrammetry", check_photogrammetry),
)


def main() -> int:
    """Run every check and report. Non-zero if anything is actually broken."""
    broken: list[str] = []
    absent: list[str] = []

    for name, check in CHECKS:
        try:
            said = check()
        except Exception as exc:
            said = f"BROKEN: {type(exc).__name__}: {exc}"
            if isinstance(exc, ModuleNotFoundError):
                said += "  <- a missing library"
            broken.append(f"{name}: {said}\n{traceback.format_exc(limit=3)}")
        else:
            if said.startswith("ABSENT"):
                absent.append(f"{name}: {said}")
            elif (
                said.startswith(("cannot", "the kernel is there but", "the slow"))
                or "failed" in said
            ):
                broken.append(f"{name}: {said}")
        print(f"  {name:18s} {said.splitlines()[0]}")

    print()
    if absent:
        print("Optional and not installed - these features will say so rather than fail:")
        for line in absent:
            print(f"  {line}")
        print()
    if broken:
        print("BROKEN - something that should work does not:")
        for line in broken:
            print(f"  {line}")
        return 1

    print("Everything installed works.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
