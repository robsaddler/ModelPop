"""The gallery, driven with no display and no network.

The whole journey - tick the clause, search, read a card, pick one, download
it - runs here. The view-model imports no UI framework, which is the only
reason that is possible, and an import-linter contract keeps it that way.
"""

from modelpop.application.discovery_service import Discovery
from modelpop.presentation.gallery_view_model import Card, GalleryViewModel, Phase

from .test_discovery import (
    CC_BY,
    ND,
    FakeAcceptance,
    FakeRepository,
    accepted,
    model,
)


def gallery(*repositories: FakeRepository, store: FakeAcceptance | None = None) -> GalleryViewModel:
    return GalleryViewModel(Discovery(list(repositories), store or accepted()))


class TestTheClause:
    def test_the_gallery_opens_on_the_clause_when_nothing_is_accepted(self):
        view = gallery(FakeRepository(), store=FakeAcceptance())
        assert view.state.phase is Phase.NEEDS_ACCEPTANCE
        assert view.clause_needs_showing

    def test_ticking_it_opens_the_gallery(self):
        view = gallery(FakeRepository(), store=FakeAcceptance())
        view.accept_terms()

        assert view.state.phase is Phase.READY
        assert not view.clause_needs_showing

    def test_an_already_accepted_clause_is_not_shown_again(self):
        assert gallery(FakeRepository()).state.phase is Phase.READY

    def test_a_store_that_cannot_write_still_lets_the_user_through(self):
        """They read it and ticked it. Our failure to write that down is ours."""
        view = gallery(FakeRepository(), store=FakeAcceptance(save_error="read-only disk"))
        view.accept_terms()

        assert view.state.phase is Phase.READY
        assert "read-only disk" in view.state.message


class TestSearching:
    def test_results_become_cards(self):
        view = gallery(FakeRepository(results=[model("Dragon"), model("Dragon II", ident="2")]))
        view.search("dragon")

        assert view.state.phase is Phase.RESULTS
        assert len(view.state.cards) == 2

    def test_the_first_card_is_selected_so_the_panel_is_never_blank(self):
        view = gallery(FakeRepository(results=[model("Dragon")]))
        view.search("dragon")
        assert view.state.selected == 0
        assert view.state.can_use_selection

    def test_the_request_is_kept_so_generation_can_be_offered_the_same_words(self):
        view = gallery(FakeRepository())
        view.search("an MSI dragon six inches tall")
        assert view.state.request == "an MSI dragon six inches tall"

    def test_an_empty_search_box_does_nothing(self):
        source = FakeRepository()
        view = gallery(source)
        view.search("   ")
        assert source.queries == []

    def test_searching_before_accepting_says_so_rather_than_appearing_broken(self):
        view = gallery(FakeRepository(results=[model("Dragon")]), store=FakeAcceptance())
        view.search("dragon")

        assert view.state.phase is Phase.NEEDS_ACCEPTANCE
        assert "accepted" in view.state.message

    def test_listeners_are_told(self):
        seen = []
        view = gallery(FakeRepository(results=[model("Dragon")]))
        view.on_change(seen.append)
        view.search("dragon")

        assert len(seen) >= 2  # searching, then results
        assert seen[-1].phase is Phase.RESULTS


class TestTheEmptyStates:
    """Four different kinds of nothing, and they need four different sentences."""

    def test_nothing_matched_offers_generation(self):
        view = gallery(FakeRepository(results=[]))
        view.search("a dragon")

        assert view.state.phase is Phase.NOTHING_MATCHED
        assert view.state.offers_generation

    def test_every_source_down_does_not_offer_generation(self):
        """The user has learned nothing. Pushing them to spend credits is wrong."""
        view = gallery(FakeRepository(error="503"), FakeRepository(name="B", error="timeout"))
        view.search("a dragon")

        assert view.state.phase is Phase.ALL_SOURCES_DOWN
        assert not view.state.offers_generation

    def test_no_sources_configured_is_its_own_state(self):
        view = gallery(FakeRepository(configured=False))
        view.search("a dragon")

        assert view.state.phase is Phase.NO_SOURCES
        assert view.state.unconfigured == ("Fake",)

    def test_a_gallery_with_no_searchable_sources_says_so_before_any_search(self):
        assert gallery(FakeRepository(searchable=False)).state.phase is Phase.NO_SOURCES

    def test_a_partial_failure_still_shows_results_and_names_the_problem(self):
        view = gallery(
            FakeRepository(name="Up", results=[model("Dragon", source="Up")]),
            FakeRepository(name="Down", error="503 from the origin"),
        )
        view.search("dragon")

        assert view.state.phase is Phase.RESULTS
        assert view.state.has_results
        assert any("Down could not be reached" in p for p in view.state.problems)


class TestCards:
    def card(self, **kwargs) -> Card:
        return Card(model("Dragon", **kwargs), ("matches what you asked for",))

    def test_a_card_names_the_creator_and_the_source(self):
        assert self.card(creator="Ada", source="Printables").subtitle == "Ada on Printables"

    def test_an_unknown_creator_is_said_plainly(self):
        assert "unknown creator" in self.card(creator="").subtitle

    def test_the_licence_badge_is_the_short_name(self):
        assert self.card(licence=CC_BY).licence_badge == "CC BY"

    def test_an_unstated_licence_says_unknown_rather_than_guessing(self):
        assert self.card().licence_badge == "Unknown"

    def test_a_no_derivatives_model_is_marked_but_not_withheld(self):
        """Inform, do not police."""
        assert self.card(licence=ND).warns_about_derivatives

    def test_a_permissive_model_is_not_marked(self):
        assert not self.card(licence=CC_BY).warns_about_derivatives

    def test_an_unstated_licence_is_not_marked_as_forbidden(self):
        assert not self.card().warns_about_derivatives

    def test_downloads_are_readable_at_a_glance(self):
        assert self.card(downloads=1_400_000).popularity == "1.4M downloads"
        assert self.card(downloads=12_500).popularity == "12k downloads"
        assert self.card(downloads=7).popularity == "7 downloads"

    def test_a_model_nobody_has_downloaded_says_nothing_rather_than_zero(self):
        assert self.card(downloads=0).popularity == ""

    def test_the_reasons_are_shown(self):
        assert "matches" in self.card().why


class TestPickingOne:
    def view(self) -> GalleryViewModel:
        view = gallery(FakeRepository(results=[model("Dragon"), model("Dragon II", ident="2")]))
        view.search("dragon")
        return view

    def test_selecting_changes_the_chosen_card(self):
        view = self.view()
        view.select(1)
        assert view.state.chosen.title == "Dragon II"

    def test_an_index_past_the_end_is_ignored_rather_than_fatal(self):
        view = self.view()
        view.select(99)
        assert view.state.selected == 0

    def test_a_negative_index_clears_the_selection(self):
        view = self.view()
        view.select(-1)
        assert view.state.chosen is None
        assert not view.state.can_use_selection

    def test_clearing_empties_the_gallery(self):
        view = self.view()
        view.clear()
        assert not view.state.has_results
        assert view.state.phase is Phase.READY


class TestDownloading:
    def test_the_chosen_model_is_fetched_and_handed_on(self, tmp_path):
        arrived = []
        view = gallery(
            FakeRepository(results=[model("Dragon", source="Fake", creator="Ada", licence=CC_BY)])
        )
        view.search("dragon")
        view.download_selected(tmp_path, arrived.append)

        assert len(arrived) == 1
        assert "Ada" in arrived[0].attribution

    def test_downloading_with_nothing_selected_does_nothing(self, tmp_path):
        arrived = []
        view = gallery(FakeRepository(results=[]))
        view.search("dragon")
        view.download_selected(tmp_path, arrived.append)
        assert arrived == []

    def test_a_failed_download_is_reported_and_nothing_is_handed_on(self, tmp_path):
        arrived = []
        source = FakeRepository(results=[model("Dragon", source="Fake")])
        view = gallery(source)
        view.search("dragon")

        source.error = "404 from the origin"
        view.download_selected(tmp_path, arrived.append)

        assert arrived == []
        assert "404" in view.state.message


class TestPastedLinks:
    def test_a_recognised_link_becomes_a_single_selected_card(self):
        view = gallery(FakeRepository(name="MakerWorld", searchable=False, resolves="mw.invalid"))
        view.paste_link("https://mw.invalid/models/42")

        assert view.state.phase is Phase.RESULTS
        assert len(view.state.cards) == 1
        assert view.state.selected == 0

    def test_an_unrecognised_link_says_so(self):
        view = gallery(FakeRepository(resolves="aaa.invalid"))
        view.paste_link("https://somewhere-else.invalid/x")

        assert view.state.phase is Phase.NOTHING_MATCHED
        assert "not recognised" in view.state.message

    def test_an_empty_paste_does_nothing(self):
        view = gallery(FakeRepository())
        before = view.state
        view.paste_link("  ")
        assert view.state is before

    def test_a_link_only_source_is_reachable_this_way_even_though_search_is_not(self):
        """The whole integration for a site with no sanctioned API."""
        link_only = FakeRepository(name="MakerWorld", searchable=False, resolves="mw.invalid")
        view = gallery(link_only)

        view.search("dragon")
        assert link_only.queries == []

        view.paste_link("https://mw.invalid/models/42")
        assert view.state.has_results
