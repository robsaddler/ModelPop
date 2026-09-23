"""Finding an existing model to start from.

Driven entirely through fake sources. Nothing here touches a network, which is
the point of the port: a repository that is down, slow, rate-limited or simply
not set up is a case worth testing, and all four are impossible to arrange
reliably against a live site.
"""

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from modelpop.application.discovery_service import Discovery, SearchOutcome
from modelpop.application.repository_ports import (
    AcceptanceStore,
    Download,
    ModelRepository,
)
from modelpop.domain.discovery import Candidate, SearchQuery, merge, rank
from modelpop.domain.licensing import (
    CLAUSE_VERSION,
    THE_CLAUSE,
    Acceptance,
    Licence,
    Permission,
)
from modelpop.domain.result import Result, failure, success

CC_BY = Licence(
    "Creative Commons - Attribution", "CC BY", Permission.YES, attribution_required=True
)
ND = Licence("Creative Commons - Attribution - NoDerivatives", "CC BY-ND", Permission.NO)
UNSTATED = Licence()


def model(
    title: str,
    *,
    source: str = "Thingiverse",
    ident: str = "",
    licence: Licence = UNSTATED,
    downloads: int = 0,
    likes: int = 0,
    description: str = "",
    tags: tuple[str, ...] = (),
    profile: bool = False,
    creator: str = "someone",
) -> Candidate:
    return Candidate(
        source=source,
        source_id=ident or title.lower().replace(" ", "-"),
        title=title,
        url=f"https://example.invalid/{title}",
        creator=creator,
        licence=licence,
        downloads=downloads,
        likes=likes,
        description=description,
        tags=tags,
        has_print_profile=profile,
    )


# --------------------------------------------------------------------- fakes


@dataclass
class FakeRepository:
    """A source with controllable behaviour."""

    name: str = "Fake"
    configured: bool = True
    searchable: bool = True
    results: list[Candidate] = field(default_factory=list)
    error: str = ""
    raises: str = ""
    queries: list[SearchQuery] = field(default_factory=list)
    resolves: str = ""

    def is_configured(self) -> bool:
        return self.configured

    def can_search(self) -> bool:
        return self.searchable

    def search(self, query: SearchQuery, limit: int = 20) -> Result[list[Candidate]]:
        self.queries.append(query)
        if self.raises:
            raise RuntimeError(self.raises)
        if self.error:
            return failure(self.error)
        return success(self.results[:limit])

    def fetch(self, candidate: Candidate, into: Path, on_progress=None) -> Result[Download]:
        if self.error:
            return failure(self.error)
        return success(Download(into / "model.stl", candidate))

    def resolve(self, url: str) -> Result[Candidate]:
        if self.resolves and self.resolves in url:
            return success(model("Pasted", source=self.name))
        return failure("not one of ours")


@dataclass
class FakeAcceptance:
    """Remembers a tick, in memory."""

    stored: Acceptance = field(default_factory=Acceptance)
    save_error: str = ""

    def load(self) -> Acceptance:
        return self.stored

    def save(self, acceptance: Acceptance) -> Result[None]:
        if self.save_error:
            return failure(self.save_error)
        self.stored = acceptance
        return success(None)


def accepted() -> FakeAcceptance:
    return FakeAcceptance(stored=Acceptance.now())


def service(*repositories: FakeRepository, store: FakeAcceptance | None = None) -> Discovery:
    return Discovery(list(repositories), store or accepted())


# --------------------------------------------------------------------- tests


class TestTheFakesMatchThePorts:
    """If these drift, every test below stops proving anything."""

    def test_the_fake_repository_satisfies_the_port(self):
        assert isinstance(FakeRepository(), ModelRepository)

    def test_the_fake_store_satisfies_the_port(self):
        assert isinstance(FakeAcceptance(), AcceptanceStore)


class TestReadingTheRequest:
    def test_a_size_is_understood_and_removed_from_the_search_words(self):
        query = SearchQuery.parse("a dragon about 6 inches tall")
        assert query.height is not None
        assert query.height.millimetres == pytest.approx(152.4)
        assert "6" not in query.text
        assert "inches" not in query.text
        assert "dragon" in query.text

    def test_a_size_written_without_a_space_is_understood(self):
        assert SearchQuery.parse("a 200mm vase").height.millimetres == pytest.approx(200)

    def test_a_colour_is_a_filter_not_a_subject(self):
        """Searching for 'black' returns filament spools, not dragons."""
        query = SearchQuery.parse("a black dragon")
        assert query.colours == ("black",)
        assert query.text == "dragon"

    def test_several_colours_are_kept(self):
        assert SearchQuery.parse("black with grey lettering").colours == ("black", "grey")

    def test_a_request_full_of_modifications_is_marked_as_a_remix(self):
        assert SearchQuery.parse("a dragon with a hollow core").wants_editing

    def test_a_plain_request_is_not(self):
        assert not SearchQuery.parse("a phone stand").wants_editing

    def test_repeated_words_appear_once(self):
        assert SearchQuery.parse("an MSI dragon with MSI on it").text.count("msi") == 1

    def test_the_original_wording_is_kept_for_the_generator(self):
        """A search that finds nothing should hand the prompt on intact."""
        asked = "an MSI dragon about six inches tall"
        assert SearchQuery.parse(asked).original == asked

    def test_a_request_of_only_noise_is_empty(self):
        assert SearchQuery.parse("I want something please").is_empty

    def test_the_users_example_request_is_read_correctly(self):
        """The request Rob actually gave, end to end."""
        query = SearchQuery.parse(
            "an MSI dragon model, about 6 inches tall in black with a hollow core "
            "and MSI in grey across his front"
        )
        assert "msi" in query.text
        assert "dragon" in query.text
        assert query.height.millimetres == pytest.approx(152.4)
        assert query.colours == ("black", "grey")
        assert query.wants_editing


class TestRanking:
    def test_a_title_match_outranks_a_description_match(self):
        query = SearchQuery.parse("dragon")
        results = rank(
            [
                model("Vase", description="looks a bit like a dragon"),
                model("Dragon", ident="d"),
            ],
            query,
        )
        assert results[0].candidate.title == "Dragon"

    def test_a_remixable_model_outranks_a_no_derivatives_one_when_editing(self):
        query = SearchQuery.parse("a dragon with a hollow core")
        results = rank(
            [
                model("Dragon", ident="nd", licence=ND),
                model("Dragon", ident="by", licence=CC_BY),
            ],
            query,
        )
        assert results[0].candidate.licence.short == "CC BY"

    def test_licence_does_not_reorder_a_request_that_is_not_a_remix(self):
        """We inform, we do not police. A plain search is not a remix."""
        query = SearchQuery.parse("a dragon")
        results = rank(
            [
                model("Dragon", ident="nd", licence=ND, downloads=500),
                model("Dragon", ident="by", licence=CC_BY),
            ],
            query,
        )
        assert results[0].candidate.source_id == "nd"

    def test_an_unstated_licence_is_not_treated_as_a_refusal(self):
        """Burying everything a source failed to label would empty the gallery."""
        assert UNSTATED.is_remixable

    def test_print_settings_are_worth_something(self):
        query = SearchQuery.parse("dragon")
        results = rank(
            [
                model("Dragon", ident="plain"),
                model("Dragon", ident="profiled", profile=True),
            ],
            query,
        )
        assert results[0].candidate.source_id == "profiled"

    def test_popularity_cannot_swamp_relevance(self):
        """One viral model must not bury forty better matches."""
        query = SearchQuery.parse("articulated dragon")
        results = rank(
            [
                model("Benchy", ident="benchy", downloads=5_000_000, likes=200_000),
                model("Articulated Dragon", ident="dragon", downloads=10),
            ],
            query,
        )
        assert results[0].candidate.title == "Articulated Dragon"

    def test_popularity_still_breaks_a_tie(self):
        query = SearchQuery.parse("dragon")
        results = rank(
            [
                model("Dragon", ident="quiet", downloads=2),
                model("Dragon", ident="loved", downloads=90_000),
            ],
            query,
        )
        assert results[0].candidate.source_id == "loved"

    def test_the_same_model_from_one_source_appears_once(self):
        query = SearchQuery.parse("dragon")
        duplicate = model("Dragon", ident="same")
        assert len(rank([duplicate, duplicate, duplicate], query)) == 1

    def test_the_same_title_from_two_sources_is_not_a_duplicate(self):
        """Different sites hosting a similar model are genuinely two choices."""
        query = SearchQuery.parse("dragon")
        results = rank(
            [
                model("Dragon", source="Thingiverse", ident="1"),
                model("Dragon", source="Printables", ident="1"),
            ],
            query,
        )
        assert len(results) == 2

    def test_every_result_carries_a_reason(self):
        query = SearchQuery.parse("dragon")
        results = rank([model("Dragon", downloads=50_000, profile=True)], query)
        assert results[0].reasons

    def test_the_gallery_is_capped(self):
        query = SearchQuery.parse("dragon")
        many = [model("Dragon", ident=str(n)) for n in range(200)]
        assert len(rank(many, query, limit=12)) == 12

    def test_merging_keeps_the_better_of_two_scores(self):
        query = SearchQuery.parse("dragon")
        left = rank([model("Dragon", ident="d")], query)
        right = rank([model("Dragon", ident="d", downloads=90_000)], query)
        merged = merge(left, right)
        assert len(merged) == 1
        assert merged[0].score == max(left[0].score, right[0].score)

    def test_an_empty_query_ranks_nothing_above_anything(self):
        results = rank([model("Dragon"), model("Vase", ident="v")], SearchQuery())
        assert len(results) == 2


class TestLicensing:
    def test_the_clause_names_the_thing_that_actually_bites(self):
        """ND and trademark are the two real traps; the clause must say both."""
        assert "No Derivatives" in THE_CLAUSE
        assert "trademark" in THE_CLAUSE

    def test_the_clause_does_not_claim_to_verify_anything(self):
        assert "does not verify" in THE_CLAUSE

    def test_searching_before_accepting_is_refused_with_a_reason(self):
        discovery = Discovery([FakeRepository(results=[model("Dragon")])], FakeAcceptance())
        result = discovery.search("dragon")
        assert not result.ok
        assert "accepted" in result.error

    def test_accepting_once_is_enough(self):
        store = FakeAcceptance()
        discovery = Discovery([FakeRepository(results=[model("Dragon")])], store)
        assert discovery.needs_acceptance

        assert discovery.accept().ok
        assert not discovery.needs_acceptance
        assert discovery.search("dragon").ok

    def test_an_acceptance_of_an_older_clause_asks_again(self):
        """If the wording changes, the old tick no longer covers it."""
        store = FakeAcceptance(stored=Acceptance(version=CLAUSE_VERSION - 1, accepted_at=None))
        assert Discovery([], store).needs_acceptance

    def test_accepting_is_recorded_with_a_timestamp(self):
        store = FakeAcceptance()
        Discovery([], store).accept()
        assert store.stored.accepted_at is not None
        assert store.stored.version == CLAUSE_VERSION

    def test_a_store_that_cannot_save_says_so(self):
        store = FakeAcceptance(save_error="the disk is read-only")
        assert not Discovery([], store).accept().ok

    def test_nothing_is_filtered_out_once_accepted(self):
        """The clause gates the feature, not the results. No policing."""
        discovery = service(FakeRepository(results=[model("Dragon", licence=ND)]))
        outcome = discovery.search("dragon").unwrap()
        assert len(outcome.results) == 1

    def test_a_licence_describes_itself_for_the_detail_panel(self):
        assert "not permitted" in ND.describe()
        assert "did not state" in UNSTATED.describe()


class TestSearchingSeveralSources:
    def test_results_from_every_source_are_combined(self):
        discovery = service(
            FakeRepository(name="A", results=[model("Dragon", source="A", ident="1")]),
            FakeRepository(name="B", results=[model("Dragon", source="B", ident="1")]),
        )
        outcome = discovery.search("dragon").unwrap()
        assert len(outcome.results) == 2
        assert outcome.searched == ("A", "B")

    def test_one_dead_source_does_not_sink_the_others(self):
        """The whole reason for fanning out rather than chaining."""
        discovery = service(
            FakeRepository(name="Up", results=[model("Dragon", source="Up")]),
            FakeRepository(name="Down", error="503 from the origin"),
        )
        outcome = discovery.search("dragon").unwrap()

        assert outcome.found_anything
        assert outcome.searched == ("Up",)
        assert outcome.failed == (("Down", "503 from the origin"),)

    def test_a_source_that_throws_is_caught_rather_than_killing_the_pool(self):
        """An HTTP client can raise something undocumented. It must not spread."""
        discovery = service(
            FakeRepository(name="Up", results=[model("Dragon", source="Up")]),
            FakeRepository(name="Rude", raises="connection reset by peer"),
        )
        outcome = discovery.search("dragon").unwrap()

        assert outcome.found_anything
        assert any(name == "Rude" for name, _ in outcome.failed)

    def test_an_unconfigured_source_is_skipped_not_failed(self):
        """Missing a key is not the same as being broken, and reads differently."""
        discovery = service(
            FakeRepository(name="Set up", results=[model("Dragon", source="Set up")]),
            FakeRepository(name="No key", configured=False),
        )
        outcome = discovery.search("dragon").unwrap()

        assert outcome.skipped == ("No key",)
        assert outcome.failed == ()

    def test_a_link_only_source_is_not_queried(self):
        """Some sites have no sanctioned API. That is an integration, not a fault."""
        link_only = FakeRepository(name="MakerWorld", searchable=False)
        discovery = service(link_only, FakeRepository(name="A", results=[model("D", source="A")]))

        discovery.search("dragon")
        assert link_only.queries == []

    def test_every_source_failing_is_distinguishable_from_finding_nothing(self):
        discovery = service(
            FakeRepository(name="A", error="down"),
            FakeRepository(name="B", error="down"),
        )
        outcome = discovery.search("dragon").unwrap()

        assert outcome.nothing_worked
        assert "No source could be reached" in outcome.summary()

    def test_finding_nothing_points_at_generating_instead(self):
        outcome = service(FakeRepository(name="A")).search("dragon").unwrap()
        assert not outcome.nothing_worked
        assert "generating" in outcome.summary()

    def test_a_search_with_no_words_is_refused_before_any_network_call(self):
        source = FakeRepository()
        result = service(source).search("I want something please")
        assert not result.ok
        assert source.queries == []

    def test_with_no_sources_configured_the_search_is_empty_not_an_error(self):
        outcome = service(FakeRepository(configured=False)).search("dragon").unwrap()
        assert outcome.results == ()
        assert outcome.skipped == ("Fake",)

    def test_the_parsed_query_is_handed_to_every_source(self):
        first, second = FakeRepository(name="A"), FakeRepository(name="B")
        service(first, second).search("a dragon 6 inches tall")

        for source in (first, second):
            assert source.queries[0].height is not None

    def test_the_summary_mentions_a_partial_failure(self):
        discovery = service(
            FakeRepository(name="Up", results=[model("Dragon", source="Up")]),
            FakeRepository(name="Down", error="503"),
        )
        assert "could not be reached" in discovery.search("dragon").unwrap().summary()


class TestPastedLinks:
    def test_a_link_is_offered_to_every_source_until_one_claims_it(self):
        discovery = service(
            FakeRepository(name="A", resolves="aaa.invalid"),
            FakeRepository(name="B", resolves="bbb.invalid"),
        )
        result = discovery.resolve("https://bbb.invalid/model/7")
        assert result.ok
        assert result.unwrap().source == "B"

    def test_an_unrecognised_link_says_which_sources_were_tried(self):
        result = service(FakeRepository(name="A", resolves="aaa")).resolve("https://elsewhere/x")
        assert not result.ok
        assert "A:" in result.detail

    def test_an_empty_paste_is_refused(self):
        assert not service(FakeRepository()).resolve("   ").ok

    def test_a_link_cannot_be_resolved_before_accepting_the_clause(self):
        discovery = Discovery([FakeRepository(resolves="x")], FakeAcceptance())
        assert not discovery.resolve("https://x/1").ok


class TestFetching:
    def test_a_download_is_routed_to_the_source_it_came_from(self, tmp_path):
        wanted = model("Dragon", source="B")
        discovery = service(FakeRepository(name="A"), FakeRepository(name="B"))

        result = discovery.fetch(wanted, tmp_path)
        assert result.ok
        assert result.unwrap().candidate.source == "B"

    def test_a_download_carries_its_attribution(self, tmp_path):
        wanted = model("Dragon", source="B", creator="Ada", licence=CC_BY)
        result = service(FakeRepository(name="B")).fetch(wanted, tmp_path)

        credit = result.unwrap().attribution
        assert "Dragon" in credit
        assert "Ada" in credit
        assert "CC BY" in credit

    def test_a_missing_source_is_reported_rather_than_crashing(self, tmp_path):
        """A gallery can outlive the source it was filled from."""
        orphan = model("Dragon", source="Gone")
        result = service(FakeRepository(name="Still here")).fetch(orphan, tmp_path)
        assert not result.ok
        assert "Gone" in result.error


class TestReportingBack:
    def test_an_empty_outcome_describes_itself(self):
        assert not SearchOutcome().found_anything

    def test_sources_are_listed_whether_configured_or_not(self):
        discovery = service(
            FakeRepository(name="A"),
            FakeRepository(name="B", configured=False),
        )
        assert discovery.sources == ("A", "B")
        assert discovery.searchable_sources == ("A",)
