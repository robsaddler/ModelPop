"""Every action says what it is doing while it does it.

Asked for after the obvious failure mode: an operation takes a while, the
window says nothing, and the user clicks again. The second click is either
refused - which looks like the first one failed - or applied twice.

Both halves of the application do work: the feature tree rebuilds through
OCCT, and the mesh workspace repairs, simplifies and places. Either can be
busy, and until now only one of them said so.
"""

import pytest

from modelpop.application.modelling import ModellingSession
from modelpop.application.workspace import Workspace
from modelpop.domain.units import Length
from modelpop.mesh import TrimeshIO, TrimeshOps
from modelpop.presentation.modelling_view_model import ModellingViewModel
from modelpop.presentation.workspace_view_model import WorkspaceViewModel

from .test_modelling import FakeCompiler, cube


def workspace() -> WorkspaceViewModel:
    view = WorkspaceViewModel(Workspace(TrimeshIO(), TrimeshOps()))
    view.adopt(cube(40))
    return view


class TestTheMeshWorkspace:
    def test_it_is_silent_when_nothing_is_happening(self):
        assert workspace().doing == ""

    @pytest.mark.parametrize(
        ("act", "expected"),
        [
            (lambda v: v.prepare_for_bed(), "Placing"),
            (lambda v: v.repair(), "Repairing"),
            (lambda v: v.simplify(100), "Simplifying"),
            (lambda v: v.scale_to_fit(Length.mm(60)), "Scaling"),
        ],
        ids=["place on bed", "repair", "simplify", "resize"],
    )
    def test_each_action_says_what_it_is_doing(self, act, expected):
        """Caught while it runs, by listening to the busy signal."""
        view = workspace()
        said: list[str] = []
        view.on_busy_changed(lambda busy: said.append(view.doing if busy else ""))

        act(view)

        assert said and said[0].startswith(expected), f"it said {said[0]!r}"

    def test_it_goes_quiet_again_afterwards(self):
        view = workspace()
        view.prepare_for_bed()
        assert view.doing == ""

    def test_it_goes_quiet_even_when_the_action_failed(self):
        view = WorkspaceViewModel(Workspace(TrimeshIO(), TrimeshOps()))
        view.repair()  # nothing is open
        assert view.doing == ""

    def test_a_long_action_warns_that_it_is_long(self):
        """Repairing a detailed model is well over a minute."""
        view = workspace()
        said: list[str] = []
        view.on_busy_changed(lambda busy: said.append(view.doing if busy else ""))
        view.repair()

        assert "minute" in said[0]


class TestTheFeatureTree:
    def test_it_says_what_it_is_building(self):
        model = ModellingViewModel(ModellingSession(FakeCompiler()))
        said: list[str] = []
        model.on_busy(lambda busy: said.append(model.doing if busy else ""))

        model.add_box(10, 10, 10)

        assert said and "box" in said[0].lower()

    def test_it_goes_quiet_afterwards(self):
        model = ModellingViewModel(ModellingSession(FakeCompiler()))
        model.add_box(10, 10, 10)
        assert model.doing == ""


class TestAndNeitherAcceptsASecondGo:
    """Which is the other half of the complaint: a double apply."""

    def test_the_workspace_refuses_while_it_is_working(self):
        held: list = []
        view = WorkspaceViewModel(Workspace(TrimeshIO(), TrimeshOps()), runner=held.append)
        view.adopt(cube(40))

        warned: list = []
        view.on_notification(warned.append)
        view.prepare_for_bed()
        view.prepare_for_bed()

        assert warned, "the second went through silently"
        assert "Already working" in warned[-1].message

    def test_the_feature_tree_refuses_while_it_is_working(self):
        held: list = []
        model = ModellingViewModel(ModellingSession(FakeCompiler()), runner=held.append)
        outcomes: list = []
        model.on_outcome(outcomes.append)

        model.add_box(10, 10, 10)
        model.add_sphere(5)

        assert any(o.refused and "rebuilding" in o.message.lower() for o in outcomes)


class TestItIsSaidBeforeTheWorkStarts:
    """Not eventually. *Before*, on the thread that took the click.

    "Did a Simplify and I did a Repair and both lock the UI and don't update
    the status bar with the action being done, not the timer."

    The message was being set correctly and never drawn. Announcing busy and
    then handing the work straight to a runner leaves the repaint queued behind
    a worker that is Python and numpy, and a worker holding the interpreter
    lock stops the interface getting a turn just as dead as running inline
    would. Measured on the dragon: the window went 0.65 s without a heartbeat
    from the moment a simplify was clicked.

    So the order has to hold - busy, and what it is doing, are both true before
    the task is dispatched - and the window paints the status bar and the clock
    on the spot. Without the first of those the second has nothing to draw.
    """

    def test_what_it_is_doing_is_known_before_the_task_runs(self):
        seen = []
        view = WorkspaceViewModel(Workspace(TrimeshIO(), TrimeshOps()))
        view.adopt(cube(40))
        view._runner = lambda work: (seen.append((view.doing, view.is_busy)), work())

        view.simplify(1000)

        assert seen == [("Simplifying to about 1,000 triangles", True)], (
            "the task was dispatched before the window had anything to say"
        )

    def test_the_same_holds_for_repair(self):
        seen = []
        view = WorkspaceViewModel(Workspace(TrimeshIO(), TrimeshOps()))
        view.adopt(cube(40))
        view._runner = lambda work: (seen.append((view.doing, view.is_busy)), work())

        view.repair()

        assert seen[0][1] is True
        assert seen[0][0].startswith("Repairing the model")

    def test_the_busy_announcement_arrives_before_the_task_too(self):
        """It is the announcement the window listens to, not the property."""
        order = []
        view = WorkspaceViewModel(Workspace(TrimeshIO(), TrimeshOps()))
        view.adopt(cube(40))
        view.on_busy_changed(lambda busy: order.append(f"busy={busy}"))
        view._runner = lambda work: (order.append("dispatched"), work())

        view.simplify(1000)

        assert order[:2] == ["busy=True", "dispatched"], f"the order was {order}"
