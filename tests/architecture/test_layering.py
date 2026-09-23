"""The layering contracts, run as an ordinary test so they gate the build.

`import-linter` is configured in `.importlinter`. Running it from pytest means a
layering violation fails `pytest` as well as CI, which is where people notice.
"""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_import_contracts_hold():
    result = subprocess.run(
        [sys.executable, "-m", "importlinter.cli", "lint"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        "Architecture contracts broken. This is the mechanism that keeps the "
        "kernel swappable; fix the import rather than the contract.\n\n"
        f"{result.stdout}\n{result.stderr}"
    )


def test_the_domain_really_imports_no_kernel():
    """A direct check, so the contract cannot be quietly weakened unnoticed."""
    domain = REPO_ROOT / "src" / "modelpop" / "domain"
    banned = ("build123d", "trimesh", "pyvista", "PySide6", "vtk", "anthropic")
    offenders = [
        f"{path.name}: {word}"
        for path in domain.glob("*.py")
        for word in banned
        if f"import {word}" in path.read_text(encoding="utf-8")
    ]
    assert not offenders, f"the domain must stay pure, found: {offenders}"
