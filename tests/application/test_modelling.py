"""The parametric model, driven through a fake compiler.

No kernel, no subprocess, no geometry. What is being tested is the contract the
session keeps with the user: that the model always builds, that undo is
trustworthy, and that a refused change leaves nothing behind.
"""

from dataclasses import dataclass, field

import numpy as np

from modelpop.application.cad_ports import FeatureCompiler, ScriptResult, SolidMeasurements
from modelpop.application.modelling import ModellingSession
from modelpop.domain.cad_commands import (
    Chamfer,
    CreateBox,
    CreateCylinder,
    EdgeSelector,
    Face,
    Fillet,
    Hollow,
    TextOnSurface,
)
from modelpop.domain.commands import Document, Feature, Origin
from modelpop.domain.mesh import Mesh
from modelpop.domain.result import Result, failure, success
from modelpop.domain.units import Length


def cube(size: float = 10.0) -> Mesh:
    half = size / 2
    vertices = np.array(
        [
            [-half, -half, 0],
            [half, -half, 0],
            [half, half, 0],
            [-half, half, 0],
            [-half, -half, size],
            [half, -half, size],
            [half, half, size],
            [-half, half, size],
        ],
        dtype=np.float64,
    )
    faces = np.array(
        [
            [0, 3, 2],
            [0, 2, 1],
            [4, 5, 6],
            [4, 6, 7],
            [0, 1, 5],
            [0, 5, 4],
            [1, 2, 6],
            [1, 6, 5],
            [2, 3, 7],
            [2, 7, 6],
            [3, 0, 4],
            [3, 4, 7],
        ],
        dtype=np.int32,
    )
    return Mesh(vertices, faces)


@dataclass
class FakeCompiler:
    """A compiler that builds whatever it is given, unless told otherwise."""

    available: bool = True
    refuse_containing: str = ""
    """Refuse any document holding a feature with this name. Stands in for a
    kernel rejecting geometry it cannot make."""

    builds: list[Document] = field(default_factory=list)

    def is_available(self) -> bool:
        return self.available

    def script_for(self, document: Document) -> Result[str]:
        return success("\n".join(f.name for f in document.active_features))

    def build(self, document: Document, timeout_seconds: float = 60.0) -> Result[ScriptResult]:
        self.builds.append(document)
        if self.refuse_containing and any(
            f.name == self.refuse_containing for f in document.active_features
        ):
            return failure("The kernel refused it", "OCCT could not make that shape.")
        return success(
            ScriptResult(
                mesh=cube(),
                measurements=SolidMeasurements(
                    volume_mm3=1000.0,
                    width=Length.mm(10),
                    depth=Length.mm(10),
                    height=Length.mm(10),
                ),
            )
        )


def session(**kwargs) -> ModellingSession:
    return ModellingSession(FakeCompiler(**kwargs))


class TestTheFakeMatchesThePort:
    def test_the_fake_compiler_satisfies_the_port(self):
        """If this drifts, every test below stops proving anything."""
        assert isinstance(FakeCompiler(), FeatureCompiler)


class TestBuildingUpAModel:
    def test_a_new_session_has_nothing_and_says_so(self):
        state = session().state
        assert state.is_empty
        assert "Start with a box" in state.describe()

    def test_applying_a_command_builds_geometry(self):
        result = session().apply(CreateBox(10, 10, 10))
        assert result.ok
        assert result.unwrap().has_geometry

    def test_features_accumulate_in_order(self):
        model = session()
        model.apply(CreateBox(10, 10, 10))
        model.apply(Fillet(1))
        model.apply(Chamfer(1))

        labels = [line.label for line in model.state.features]
        assert len(labels) == 3
        assert labels[0].startswith("Box")
        assert "Round" in labels[1]
        assert "Chamfer" in labels[2]

    def test_the_whole_tree_is_replayed_on_every_change(self):
        """What makes the model parametric: change an early feature and
        everything after it follows."""
        compiler = FakeCompiler()
        model = ModellingSession(compiler)
        model.apply(CreateBox(10, 10, 10))
        model.apply(Fillet(1))

        assert len(compiler.builds[-1].features) == 2

    def test_who_asked_is_recorded_on_the_row(self):
        model = session()
        model.apply(CreateBox(10, 10, 10))
        model.apply(Fillet(1), Origin.ASSISTANT)

        rows = model.state.features
        assert not rows[0].by_the_assistant
        assert rows[1].by_the_assistant

    def test_the_status_line_reports_the_size(self):
        model = session()
        model.apply(CreateBox(10, 10, 10))
        assert "10.0 mm" in model.state.describe()

    def test_a_single_feature_is_described_in_the_singular(self):
        model = session()
        model.apply(CreateBox(10, 10, 10))
        assert "1 feature -" in model.state.describe()


class TestARefusedChangeLeavesNothingBehind:
    """The invariant that makes undo trustworthy: the model always builds."""

    def test_a_command_the_kernel_refuses_is_not_kept(self):
        model = session(refuse_containing="fillet")
        model.apply(CreateBox(10, 10, 10))

        result = model.apply(Fillet(99))
        assert not result.ok
        assert len(model.state.document) == 1

    def test_the_refusal_names_the_command_and_says_nothing_changed(self):
        model = session(refuse_containing="fillet")
        model.apply(CreateBox(10, 10, 10))
        result = model.apply(Fillet(99))

        assert "Round all edges" in result.error
        assert "unchanged" in result.detail

    def test_the_geometry_on_screen_survives_a_refusal(self):
        """The user is still looking at a correct picture of their model."""
        model = session(refuse_containing="fillet")
        model.apply(CreateBox(10, 10, 10))
        model.apply(Fillet(99))

        assert model.state.has_geometry

    def test_a_refusal_does_not_leave_a_phantom_undo_step(self):
        model = session(refuse_containing="fillet")
        model.apply(CreateBox(10, 10, 10))
        model.apply(Fillet(99))

        model.undo()
        assert model.state.is_empty, "undo went back further than the refused change"

    def test_a_later_valid_command_still_works_after_a_refusal(self):
        model = session(refuse_containing="fillet")
        model.apply(CreateBox(10, 10, 10))
        model.apply(Fillet(99))

        assert model.apply(Chamfer(1)).ok
        assert len(model.state.document) == 2


class TestUndoAndRedo:
    def built(self) -> ModellingSession:
        model = session()
        model.apply(CreateBox(10, 10, 10))
        model.apply(Fillet(1))
        return model

    def test_undo_removes_the_last_feature(self):
        model = self.built()
        model.undo()
        assert len(model.state.document) == 1

    def test_redo_puts_it_back(self):
        model = self.built()
        model.undo()
        model.redo()
        assert len(model.state.document) == 2

    def test_undo_on_an_empty_model_says_so_rather_than_failing_silently(self):
        result = session().undo()
        assert not result.ok
        assert "nothing to undo" in result.error

    def test_redo_with_nothing_ahead_says_so(self):
        assert not self.built().redo().ok

    def test_the_undo_label_names_what_will_be_undone(self):
        model = self.built()
        assert "Round" in model.state.undo_label

    def test_an_assistants_edit_is_undoable_like_any_other(self):
        """The whole point of routing an AI through the same bus."""
        model = session()
        model.apply(CreateBox(10, 10, 10))
        model.apply(TextOnSurface("MSI"), Origin.ASSISTANT)

        assert model.state.can_undo
        model.undo()
        assert len(model.state.document) == 1

    def test_undoing_to_nothing_leaves_no_geometry(self):
        model = session()
        model.apply(CreateBox(10, 10, 10))
        model.undo()

        assert model.state.is_empty
        assert not model.state.has_geometry


class TestOpeningAndClearing:
    def test_a_saved_tree_is_rebuilt_when_it_is_opened(self):
        """A document that will not build should say so on opening, not at the
        first edit."""
        compiler = FakeCompiler()
        model = ModellingSession(compiler)
        document = Document(
            features=(
                Feature("create-box", {"width": 10.0, "depth": 10.0, "height": 10.0}),
                Feature("fillet", {"radius": 1.0, "edges": "all"}),
            )
        )

        assert model.load(document).ok
        assert compiler.builds
        assert len(model.state.features) == 2

    def test_opening_a_file_is_not_an_undoable_step(self):
        """Offering undo there would look like a way to lose the file."""
        model = session()
        model.load(
            Document(features=(Feature("create-box", {"width": 1, "depth": 1, "height": 1}),))
        )
        assert not model.state.can_undo

    def test_a_feature_from_a_newer_build_is_shown_rather_than_dropped(self):
        model = session()
        model.load(Document(features=(Feature("warp-drive", {}),)))

        rows = model.state.features
        assert len(rows) == 1
        assert not rows[0].understood
        assert "not supported" in rows[0].label

    def test_clearing_discards_the_tree_and_the_history(self):
        model = session()
        model.apply(CreateBox(10, 10, 10))
        model.clear()

        assert model.state.is_empty
        assert not model.state.can_undo


class TestWithoutAKernel:
    def test_the_session_still_opens(self):
        """A broken OCCT install must not stop the app starting."""
        model = ModellingSession(None)
        assert not model.can_build
        assert model.state.is_empty

    def test_building_says_what_is_wrong(self):
        result = ModellingSession(None).apply(CreateBox(10, 10, 10))
        assert not result.ok
        assert "kernel is unavailable" in result.error

    def test_an_unavailable_kernel_reports_itself(self):
        assert not session(available=False).can_build

    def test_asking_for_the_script_says_what_is_wrong(self):
        assert not ModellingSession(None).script().ok


class TestReadingTheModel:
    def test_the_script_can_be_read_without_building(self):
        """A model you cannot inspect is one you cannot trust."""
        model = session()
        model.apply(CreateBox(10, 10, 10))
        model.apply(Hollow(1.5, Face.BOTTOM))

        source = model.script().unwrap()
        assert "create-box" in source
        assert "hollow" in source

    def test_two_models_built_the_same_way_are_the_same_model(self):
        one, two = session(), session()
        for model in (one, two):
            model.apply(CreateBox(10, 10, 10))
            model.apply(Fillet(2, EdgeSelector.TOP))

        assert one.state.document.content_hash == two.state.document.content_hash

    def test_a_different_model_is_a_different_hash(self):
        one, two = session(), session()
        one.apply(CreateBox(10, 10, 10))
        two.apply(CreateCylinder(5, 10))

        assert one.state.document.content_hash != two.state.document.content_hash
