import dataclasses

import pytest
from hypothesis import given
from hypothesis import strategies as st

from modelpop.domain import CommandBus, Document, DocumentHistory, GenericCommand, Origin

command_names = st.sampled_from(["fillet", "chamfer", "extrude", "scale", "hollow"])


@st.composite
def commands(draw):
    return GenericCommand(
        draw(command_names),
        {"radius": draw(st.floats(min_value=0.1, max_value=10, allow_nan=False))},
    )


def fillet(radius: float = 2.0) -> GenericCommand:
    return GenericCommand("fillet", {"radius": radius}, label=f"Fillet {radius} mm")


class TestDocument:
    def test_starts_empty(self):
        assert len(Document()) == 0
        assert Document().active_features == ()

    def test_appending_a_feature_does_not_mutate_the_original(self):
        original = Document()
        updated = original.with_feature(fillet().to_feature())
        assert len(original) == 0
        assert len(updated) == 1

    def test_content_hash_ignores_when_the_edit_was_made(self):
        # two documents built the same way by different routes must match,
        # otherwise caching and snapshot tests are useless
        a = Document().with_feature(fillet(2.0).to_feature())
        b = Document().with_feature(fillet(2.0).to_feature())
        assert a.content_hash == b.content_hash

    def test_content_hash_notices_a_different_parameter(self):
        a = Document().with_feature(fillet(2.0).to_feature())
        b = Document().with_feature(fillet(3.0).to_feature())
        assert a.content_hash != b.content_hash

    def test_content_hash_notices_a_different_order(self):
        one, two = fillet(1.0).to_feature(), fillet(2.0).to_feature()
        assert (
            Document().with_feature(one).with_feature(two).content_hash
            != Document().with_feature(two).with_feature(one).content_hash
        )

    def test_suppressed_features_stay_in_history_but_leave_the_active_set(self):
        feature = fillet().to_feature()
        document = Document().with_feature(feature)
        suppressed = Document(features=(dataclasses.replace(feature, suppressed=True),))
        assert len(document.active_features) == 1
        assert len(suppressed.features) == 1
        assert len(suppressed.active_features) == 0

    def test_suppressing_a_feature_changes_the_content_hash(self):
        # a suppressed feature must rebuild to different geometry
        feature = fillet().to_feature()
        assert (
            Document(features=(feature,)).content_hash
            != Document(features=(dataclasses.replace(feature, suppressed=True),)).content_hash
        )


class TestHistory:
    def test_a_new_history_has_nothing_to_undo(self):
        history = DocumentHistory()
        assert not history.can_undo
        assert not history.can_redo
        assert history.undo_label is None

    def test_undo_then_redo_returns_to_the_same_document(self):
        history = DocumentHistory()
        edited = Document().with_feature(fillet().to_feature())
        history.push(edited, "Fillet")

        assert history.can_undo
        assert history.undo().content_hash == Document().content_hash
        assert history.redo().content_hash == edited.content_hash

    def test_undoing_past_the_start_is_a_no_op_not_an_error(self):
        history = DocumentHistory()
        for _ in range(5):
            history.undo()
        assert len(history.current) == 0

    def test_a_new_edit_discards_the_redo_branch(self):
        history = DocumentHistory()
        history.push(Document().with_feature(fillet(1.0).to_feature()), "a")
        history.undo()
        history.push(Document().with_feature(fillet(2.0).to_feature()), "b")
        assert not history.can_redo

    def test_labels_describe_what_undo_would_reverse(self):
        history = DocumentHistory()
        history.push(Document(), "Fillet 2 mm")
        assert history.undo_label == "Fillet 2 mm"
        history.undo()
        assert history.redo_label == "Fillet 2 mm"

    def test_old_states_are_discarded_once_the_limit_is_reached(self):
        history = DocumentHistory(limit=3)
        for i in range(10):
            history.push(Document(name=str(i)), str(i))
        assert len(history) == 3
        assert history.current.name == "9"

    def test_a_zero_limit_is_rejected(self):
        with pytest.raises(ValueError, match="at least 1"):
            DocumentHistory(limit=0)


class TestCommandBus:
    def test_executing_a_command_records_it(self):
        bus = CommandBus()
        bus.execute(fillet())
        assert len(bus.document) == 1
        assert bus.document.features[0].name == "fillet"

    def test_the_origin_of_every_edit_is_recorded(self):
        bus = CommandBus()
        bus.execute(fillet(), origin=Origin.ASSISTANT)
        assert bus.document.features[0].origin is Origin.ASSISTANT

    def test_an_assistant_edit_is_undoable_exactly_like_a_manual_one(self):
        # this is the whole point of ADR-0001
        bus = CommandBus()
        before = bus.document.content_hash
        bus.execute(fillet(), origin=Origin.ASSISTANT)
        assert bus.document.content_hash != before
        bus.undo()
        assert bus.document.content_hash == before

    def test_a_batch_of_assistant_commands_is_one_undo_step(self):
        # "make the walls 3 mm and round the top edges" must not cost five undos
        bus = CommandBus()
        before = bus.document.content_hash
        bus.execute_all(
            [fillet(1.0), fillet(2.0), GenericCommand("hollow", {"thickness": 3.0})],
            origin=Origin.ASSISTANT,
        )
        assert len(bus.document) == 3
        bus.undo()
        assert bus.document.content_hash == before

    def test_an_empty_batch_changes_nothing(self):
        bus = CommandBus()
        before = bus.document.content_hash
        bus.execute_all([])
        assert bus.document.content_hash == before
        assert not bus.history.can_undo

    def test_listeners_are_told_about_every_change_including_undo(self):
        seen = []
        bus = CommandBus()
        bus.subscribe(lambda doc: seen.append(len(doc)))
        bus.execute(fillet())
        bus.execute(fillet(3.0))
        bus.undo()
        bus.redo()
        assert seen == [1, 2, 1, 2]

    def test_a_batch_label_names_the_first_command_and_the_count(self):
        bus = CommandBus()
        bus.execute_all([fillet(1.0), fillet(2.0)])
        assert bus.history.undo_label is not None
        assert "2 changes" in bus.history.undo_label


class TestInvariants:
    """The properties ADR-0001 promises, asserted over generated input."""

    @given(commands())
    def test_undo_after_apply_always_restores_the_document_hash(self, command):
        bus = CommandBus()
        before = bus.document.content_hash
        bus.execute(command)
        bus.undo()
        assert bus.document.content_hash == before

    @given(st.lists(commands(), min_size=1, max_size=12))
    def test_undoing_every_edit_returns_to_the_empty_document(self, command_list):
        bus = CommandBus()
        empty = bus.document.content_hash
        for command in command_list:
            bus.execute(command)
        for _ in command_list:
            bus.undo()
        assert bus.document.content_hash == empty

    @given(st.lists(commands(), min_size=1, max_size=12))
    def test_redo_after_undo_is_the_identity(self, command_list):
        bus = CommandBus()
        for command in command_list:
            bus.execute(command)
        final = bus.document.content_hash
        bus.undo()
        bus.redo()
        assert bus.document.content_hash == final

    @given(st.lists(commands(), min_size=0, max_size=12))
    def test_replaying_a_history_reproduces_the_same_document(self, command_list):
        """Parametric rebuild is a replay - so a replay must be faithful."""
        original = CommandBus()
        for command in command_list:
            original.execute(command)

        replayed = CommandBus()
        for feature in original.document.features:
            replayed.execute(GenericCommand.from_feature(feature), origin=Origin.REPLAY)

        assert replayed.document.content_hash == original.document.content_hash

    @given(commands())
    def test_applying_the_same_command_twice_gives_the_same_result_both_times(self, command):
        document = Document()
        assert command.apply(document).content_hash == command.apply(document).content_hash


class TestWhatHasBeenUndone:
    """Undone steps stay knowable, so a view can show them greyed.

    Asked for directly: an undone step used to vanish from the tree, and the
    only evidence that redo would bring anything back was whether a button
    happened to be enabled.
    """

    def history(self) -> DocumentHistory:
        history = DocumentHistory()
        for label in ("a box", "a fillet", "a hollow"):
            history.push(history.current, label)
        return history

    def test_nothing_is_undone_to_begin_with(self):
        assert self.history().undone_labels == ()

    def test_one_undo_leaves_one_step_waiting(self):
        history = self.history()
        history.undo()
        assert history.undone_labels == ("a hollow",)

    def test_they_come_back_oldest_first_which_is_redo_order(self):
        """The order matters: it is the order redo will put them back in."""
        history = self.history()
        history.undo()
        history.undo()
        assert history.undone_labels == ("a fillet", "a hollow")

    def test_redoing_takes_one_off_the_list_again(self):
        history = self.history()
        history.undo()
        history.undo()
        history.redo()
        assert history.undone_labels == ("a hollow",)

    def test_a_new_step_discards_the_branch_that_was_waiting(self):
        """Doing something else after an undo is what throws redo away."""
        history = self.history()
        history.undo()
        assert history.undone_labels == ("a hollow",)

        history.push(history.current, "a chamfer instead")
        assert history.undone_labels == ()
        assert not history.can_redo

    def test_the_list_agrees_with_can_redo(self):
        history = self.history()
        assert bool(history.undone_labels) == history.can_redo
        history.undo()
        assert bool(history.undone_labels) == history.can_redo

    def test_the_first_of_them_is_what_redo_would_do(self):
        history = self.history()
        history.undo()
        history.undo()
        assert history.undone_labels[0] == history.redo_label
