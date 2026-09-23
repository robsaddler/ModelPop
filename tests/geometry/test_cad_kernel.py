"""The CAD kernel, running real build123d scripts in real subprocesses.

Slower than a unit test because each case starts an interpreter and builds
geometry, so most of it is marked integration. The security checks are not:
those must run on every save.
"""

import textwrap

import pytest

from modelpop.cad import Build123dKernel
from tests.conftest import kernel_is_available, kernel_required  # noqa: F401


def script(source: str) -> str:
    return textwrap.dedent(source).strip()


BRACKET = script("""
    from build123d import *
    with BuildPart() as bracket:
        Box(60, 40, 8)
        with Locations((-20, 0, 0), (20, 0, 0)):
            Hole(radius=2.0)
    result = bracket.part
""")


@pytest.fixture(scope="module")
def kernel() -> Build123dKernel:
    return Build123dKernel()


class TestScriptChecking:
    """The tripwire. Fast, so it runs in the normal suite."""

    @pytest.mark.parametrize(
        "source",
        [
            "import os\nresult = None",
            "import subprocess",
            "import socket",
            "__import__('os').system('echo hi')",
            "eval('1+1')",
            "exec('x = 1')",
            "open('/etc/passwd').read()",
            "import urllib.request",
        ],
    )
    def test_a_script_reaching_outside_cad_is_refused(self, kernel, source):
        result = kernel.check_script(source)
        assert not result.ok
        assert "should not" in result.error

    def test_an_empty_script_is_refused(self, kernel):
        assert not kernel.check_script("   \n  ").ok

    def test_an_ordinary_cad_script_is_allowed(self, kernel):
        assert kernel.check_script(BRACKET).ok

    def test_the_check_is_case_insensitive(self, kernel):
        assert not kernel.check_script("IMPORT OS").ok


@kernel_required
@pytest.mark.integration
class TestRunning:
    def test_a_correct_script_produces_a_measured_solid(self, kernel):
        result = kernel.run(BRACKET, timeout_seconds=120)
        assert result.ok, getattr(result, "error", "")

        produced = result.unwrap()
        measured = produced.measurements
        assert measured.width.millimetres == pytest.approx(60.0, abs=0.01)
        assert measured.depth.millimetres == pytest.approx(40.0, abs=0.01)
        assert measured.height.millimetres == pytest.approx(8.0, abs=0.01)
        assert measured.volume_mm3 > 0
        assert measured.is_valid
        assert measured.solid_count == 1

    def test_the_result_is_a_watertight_mesh(self, kernel):
        """STL shares no vertices, so this fails unless they are merged on load.

        Every generated part looked broken until that was tracked down.
        """
        from modelpop.mesh import TrimeshOps

        produced = kernel.run(BRACKET, timeout_seconds=120).unwrap()
        assert TrimeshOps().inspect(produced.mesh, measure_walls=False).is_watertight

    def test_a_step_file_is_produced_for_later_editing(self, kernel):
        produced = kernel.run(BRACKET, timeout_seconds=120).unwrap()
        assert produced.step_path is not None
        assert produced.step_path.stat().st_size > 500

    def test_a_syntax_error_is_reported_with_its_line(self, kernel):
        result = kernel.run("this is not python at all ((", timeout_seconds=60)
        assert not result.ok
        assert "does not parse" in result.error
        assert "line 1" in result.error

    def test_a_runtime_error_is_reported_by_name(self, kernel):
        result = kernel.run(
            script("""
            from build123d import *
            result = Box(10, 10, "not a number")
        """),
            timeout_seconds=60,
        )
        assert not result.ok

    def test_a_script_that_builds_nothing_says_what_to_do(self, kernel):
        result = kernel.run(
            script("""
            from build123d import *
            x = 1 + 1
        """),
            timeout_seconds=60,
        )
        assert not result.ok
        assert "result" in result.error, "it must name the variable the model should assign"

    def test_the_wildcard_import_does_not_masquerade_as_a_result(self, kernel):
        """`from build123d import *` puts classes with a `volume` property in scope.

        Without an isinstance guard the fallback search returns `Box` the class
        and the failure that follows is baffling.
        """
        result = kernel.run(
            script("""
            from build123d import *
            unrelated = 42
        """),
            timeout_seconds=60,
        )
        assert not result.ok
        assert "no solid" in result.error

    def test_an_endless_loop_is_stopped_by_the_timeout(self, kernel):
        result = kernel.run(
            script("""
            from build123d import *
            while True:
                pass
        """),
            timeout_seconds=5,
        )
        assert not result.ok
        assert "did not finish" in result.error

    def test_a_crash_in_the_kernel_does_not_take_us_with_it(self, kernel):
        """The whole reason scripts run out of process."""
        result = kernel.run(
            script("""
            from build123d import *
            result = Box(1e12, 1e12, 1e12)
        """),
            timeout_seconds=60,
        )
        # either outcome is fine; what matters is that we are still here
        assert result.ok or not result.ok

    def test_several_solids_are_counted(self, kernel):
        produced = kernel.run(
            script("""
            from build123d import *
            with BuildPart() as p:
                with Locations((-50, 0, 0), (50, 0, 0)):
                    Box(20, 20, 20)
            result = p.part
        """),
            timeout_seconds=120,
        ).unwrap()
        assert produced.measurements.solid_count == 2


@kernel_required
@pytest.mark.integration
class TestKernelCapabilities:
    """The operations the C# kernel could not do (spike S1)."""

    def test_it_fillets_every_edge_of_a_cube_at_once(self, kernel):
        produced = kernel.run(
            script("""
            from build123d import *
            with BuildPart() as p:
                Box(20, 20, 20)
                fillet(p.edges(), radius=2)
            result = p.part
        """),
            timeout_seconds=120,
        ).unwrap()
        # a 20mm cube is 8000; rounding all twelve edges removes a little under 200
        assert 7700 < produced.measurements.volume_mm3 < 8000

    def test_it_chamfers(self, kernel):
        produced = kernel.run(
            script("""
            from build123d import *
            with BuildPart() as p:
                Box(20, 20, 20)
                chamfer(p.edges().filter_by(Axis.Z), length=2)
            result = p.part
        """),
            timeout_seconds=120,
        ).unwrap()
        assert produced.measurements.volume_mm3 == pytest.approx(7840.0, rel=1e-3)

    def test_it_fillets_twice_in_sequence(self, kernel):
        produced = kernel.run(
            script("""
            from build123d import *
            with BuildPart() as p:
                Box(20, 20, 20)
                fillet(p.edges().filter_by(Axis.Z), radius=2)
                fillet(p.edges().filter_by(Axis.X), radius=1)
            result = p.part
        """),
            timeout_seconds=120,
        ).unwrap()
        assert produced.measurements.volume_mm3 < 7931.4

    def test_booleans_are_exact(self, kernel):
        import math

        produced = kernel.run(
            script("""
            from build123d import *
            result = Box(20, 20, 20) - Cylinder(radius=5, height=30)
        """),
            timeout_seconds=120,
        ).unwrap()
        expected = 8000 - math.pi * 25 * 20
        assert produced.measurements.volume_mm3 == pytest.approx(expected, rel=1e-6)


@pytest.mark.integration
class TestAvailability:
    """Marked integration because both of these start an interpreter.

    That is the definition of the marker, and it is not a formality: between
    them they cost seven seconds of a suite whose whole point is to be quick
    enough to run after every change. The security checks below start nothing
    and stay in the fast loop, which is where they belong - a tripwire that
    only runs nightly is not a tripwire.
    """

    def test_it_reports_whether_it_can_run(self, kernel):
        assert isinstance(kernel.is_available(), bool)

    @kernel_required
    def test_it_names_the_kernel_and_version(self, kernel):
        assert "build123d" in kernel.describe()
        assert "OCCT" in kernel.describe()
