"""Create the environment that turns pictures into meshes.

Run from the project root::

    uv run python scripts/setup_generation.py

Deliberately a script rather than something the app does on its own. It
downloads gigabytes, it takes minutes, and it can fail in ways only a person can
resolve - a driver too old, a CUDA build that does not match, no disk left.
Doing that silently behind a button would be worse than asking.

It installs **PyTorch only**. Model weights come on first use from the backend's
own cache, because which backend you want is a choice and they are several
gigabytes each. See ``docs/10-mesh-generation.md``.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

# The machine-learning stack does not have wheels for 3.14, which is what
# ModelPop itself runs on. That mismatch is the whole reason this is a separate
# environment rather than an extra.
PYTHON_VERSION = "3.12"

# Matched to a current NVIDIA driver. A driver older than this needs the cu126
# index instead; the script says so rather than failing with a wheel error.
CUDA_INDEX = "https://download.pytorch.org/whl/cu128"
MIN_DRIVER = 525


def app_data_dir() -> Path:
    """Where ModelPop keeps per-user data. Mirrors the application's own rule."""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / "ModelPop"
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / "modelpop"
    return Path.home() / ".local" / "share" / "modelpop"


def say(message: str) -> None:
    """Tell the person running this what is happening."""
    print(message, flush=True)


def find_uv() -> str | None:
    """Where uv is, if it is anywhere.

    It is not always on PATH even when it is installed, which is a specific
    thing this machine does, so the usual per-user location is checked too.
    """
    found = shutil.which("uv")
    if found:
        return found

    candidates = [
        Path(os.environ.get("APPDATA", ""))
        / "Python"
        / f"Python{PYTHON_VERSION.replace('.', '')}"
        / "Scripts"
        / "uv.exe",
        Path.home() / ".local" / "bin" / "uv",
        Path.home() / ".cargo" / "bin" / "uv.exe",
    ]
    for base in (Path(os.environ.get("APPDATA", "")) / "Python",):
        if base.exists():
            candidates.extend(base.glob("Python*/Scripts/uv.exe"))

    for candidate in candidates:
        if candidate and candidate.exists():
            return str(candidate)
    return None


def driver_version() -> int | None:
    """The installed NVIDIA driver's major version, if there is one."""
    nvidia_smi = shutil.which("nvidia-smi")
    if not nvidia_smi:
        return None
    try:
        completed = subprocess.run(
            [nvidia_smi, "--query-gpu=driver_version", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None

    first = completed.stdout.strip().splitlines()
    if not first:
        return None
    try:
        return int(first[0].split(".")[0])
    except ValueError:
        return None


def run(command: list[str], *, what: str) -> bool:
    """Run one step, reporting what it was doing when it failed."""
    say(f"\n== {what}")
    say("   " + " ".join(command))
    try:
        completed = subprocess.run(command, check=False)
    except OSError as error:
        say(f"   could not start: {error}")
        return False
    if completed.returncode != 0:
        say(f"   failed with exit code {completed.returncode}")
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    """Create the generation environment."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--into",
        type=Path,
        default=None,
        help="where to create it (default: beside the application's own data)",
    )
    parser.add_argument(
        "--index",
        default=CUDA_INDEX,
        help="the PyTorch wheel index; use the cu126 one for an older driver",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="report what is installed and change nothing",
    )
    arguments = parser.parse_args(argv)

    target = arguments.into or (app_data_dir() / "generation")
    python = target / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

    if arguments.check:
        return _report(target, python)

    uv = find_uv()
    if uv is None:
        say(
            "uv was not found. Install it from https://docs.astral.sh/uv/ and run "
            "this again - it is what creates the environment."
        )
        return 2

    driver = driver_version()
    if driver is None:
        say(
            "No NVIDIA driver was found. Generation needs a CUDA graphics card; "
            "on the processor alone a single model takes hours.\n"
            "Carrying on anyway - PyTorch will install, it just will not be usable."
        )
    elif driver < MIN_DRIVER:
        say(
            f"The NVIDIA driver is version {driver}, which is older than the CUDA 12.8 "
            f"wheels expect.\nEither update the driver, or run this again with "
            f"--index https://download.pytorch.org/whl/cu126"
        )
        return 3
    else:
        say(f"NVIDIA driver {driver} found.")

    say(f"\nCreating the generation environment in:\n  {target}")
    target.parent.mkdir(parents=True, exist_ok=True)

    if not run(
        [uv, "venv", "--python", PYTHON_VERSION, str(target)],
        what=f"creating a Python {PYTHON_VERSION} environment",
    ):
        return 4

    if not run(
        [
            uv,
            "pip",
            "install",
            "--python",
            str(python),
            "--index-url",
            arguments.index,
            "torch",
            "torchvision",
        ],
        what="installing PyTorch (this is the several-gigabyte part)",
    ):
        return 5

    if not run(
        [uv, "pip", "install", "--python", str(python), "trimesh", "pillow", "numpy"],
        what="installing the mesh and image libraries",
    ):
        return 6

    say("\n== Checking what we ended up with")
    _report(target, python)

    say(
        "\nPyTorch is installed. A generator is not, yet - that is the next step "
        "and it is a choice:\n"
        "\n"
        "  TRELLIS (MIT, the default):\n"
        "    https://github.com/microsoft/TRELLIS - follow its own install notes,\n"
        f"    using {python}\n"
        "\n"
        "  TripoSG (MIT, lighter):\n"
        "    https://github.com/VAST-AI-Research/TripoSG\n"
        "\n"
        "Optional but worth it - removes a photo's background before generating,\n"
        "which is the commonest reason a result comes out wrong:\n"
        f"    uv pip install --python {python} rembg\n"
        "\n"
        "Then open ModelPop and look at File > Settings. It will say what it found."
    )
    return 0


def _report(target: Path, python: Path) -> int:
    """Ask the environment what it has, using the app's own probe."""
    if not python.exists():
        say(f"No generation environment at {target}.")
        return 1

    worker = Path(__file__).resolve().parents[1] / "src/modelpop/generation/mesh_worker.py"
    try:
        completed = subprocess.run(
            [str(python), str(worker), "--probe", "--request", "{}"],
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        say(f"Could not probe it: {error}")
        return 1

    for line in completed.stdout.splitlines():
        if line.strip().startswith("{"):
            say("   " + line.strip())
    if completed.stderr.strip():
        say("   " + completed.stderr.strip()[-300:])

    say(f'\nPoint ModelPop at it with:\n  $env:MODELPOP_GENERATION_PYTHON = "{python}"')
    say("(not needed if it is in the default location, which it is by default)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
