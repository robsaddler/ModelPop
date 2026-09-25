"""Pictures for the gallery, fetched without a network.

A list of titles is not a way to choose a model - you recognise the thing you
want by looking at it. Every source already returned a thumbnail URL with its
results; nothing fetched them.

What matters here is that a picture that will not load costs a grey square and
never a broken gallery. A source can be slow, a CDN can be down, and a URL can
be nonsense, none of which is a reason to stop showing results.
"""

import httpx
import pytest

from modelpop.domain.discovery import Candidate
from modelpop.repositories.http import HttpClient, RateLimit
from modelpop.repositories.thumbnails import REMEMBER_AT_MOST, Thumbnails

A_PICTURE = b"\x89PNG\r\n\x1a\n" + b"x" * 200


def a_candidate(url: str = "https://cdn.example.com/a.png", ident: str = "1") -> Candidate:
    return Candidate(
        source="Thingiverse",
        source_id=ident,
        title="A dragon",
        url="https://www.thingiverse.com/thing:1",
        thumbnail_url=url,
    )


def thumbnails(handler) -> tuple[Thumbnails, list[str]]:
    """A fetcher wired to a fake transport, and the URLs it was asked for."""
    asked: list[str] = []

    def record(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        return handler(request)

    client = httpx.Client(transport=httpx.MockTransport(record))
    # A limit that never waits. The real one is 60 requests a minute, which is
    # right for a stranger's CDN and absurd against a transport in this
    # process - the eviction test asks for three hundred pictures and spent
    # five minutes sleeping between them.
    unhurried = RateLimit(requests=1_000_000, per_seconds=1.0)
    return Thumbnails(HttpClient("", unhurried, client)), asked


def always(status: int, body: bytes = A_PICTURE, content_type: str = "image/png"):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=body, headers={"content-type": content_type})

    return handler


class TestFetchingOne:
    def test_a_picture_comes_back(self):
        source, _asked = thumbnails(always(200))
        assert source.of(a_candidate()) == A_PICTURE

    def test_a_result_with_no_picture_asks_for_nothing(self):
        source, asked = thumbnails(always(200))

        assert source.of(a_candidate(url="")) is None
        assert asked == [], "it went looking for a thumbnail that was never offered"

    def test_a_dead_url_gives_nothing_rather_than_raising(self):
        """One broken image must not take the gallery with it."""
        source, _asked = thumbnails(always(404))
        assert source.of(a_candidate()) is None

    def test_a_source_that_refuses_gives_nothing(self):
        source, _asked = thumbnails(always(403))
        assert source.of(a_candidate()) is None

    def test_a_network_error_gives_nothing(self):
        def explode(_request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no route to host")

        source, _asked = thumbnails(explode)
        assert source.of(a_candidate()) is None

    def test_an_empty_body_is_not_a_picture(self):
        source, _asked = thumbnails(always(200, body=b""))
        assert source.of(a_candidate()) is None

    def test_something_enormous_is_refused(self):
        """A URL from a third party is not a promise about size."""
        source, _asked = thumbnails(always(200, body=b"x" * (5 * 1024 * 1024)))
        assert source.of(a_candidate()) is None


class TestRememberingThem:
    def test_the_same_picture_is_fetched_once(self):
        source, asked = thumbnails(always(200))
        source.of(a_candidate())
        source.of(a_candidate())

        assert len(asked) == 1, f"it fetched the same picture {len(asked)} times"

    def test_a_url_that_failed_is_not_retried(self):
        """Otherwise every redraw hammers a CDN that has already said no."""
        source, asked = thumbnails(always(404))
        source.of(a_candidate())
        source.of(a_candidate())

        assert len(asked) == 1

    def test_what_is_already_held_can_be_asked_for_without_fetching(self):
        source, asked = thumbnails(always(200))
        assert source.already_have(a_candidate()) is None
        assert asked == []

        source.of(a_candidate())
        assert source.already_have(a_candidate()) == A_PICTURE
        assert len(asked) == 1

    def test_it_does_not_grow_without_bound(self):
        """A long session must not fill memory with old search results."""
        source, _asked = thumbnails(always(200))
        for index in range(REMEMBER_AT_MOST + 5):
            source.of(a_candidate(url=f"https://cdn.example.com/{index}.png", ident=str(index)))

        assert len(source._seen) <= REMEMBER_AT_MOST

    def test_different_results_sharing_one_picture_share_the_fetch(self):
        source, asked = thumbnails(always(200))
        source.of(a_candidate(ident="1"))
        source.of(a_candidate(ident="2"))

        assert len(asked) == 1


class TestNothingIsWritten:
    def test_the_picture_is_only_ever_in_memory(self, tmp_path, monkeypatch):
        """Thingiverse's API terms do not permit storing content (ADR-0008).

        A picture shown in a window and dropped when it closes is what a
        browser does; a file on disk is a cache.
        """
        monkeypatch.chdir(tmp_path)
        source, _asked = thumbnails(always(200))
        source.of(a_candidate())

        assert list(tmp_path.iterdir()) == []


class TestTheStateCarriesProgress:
    """A download can take a minute over somebody else's CDN."""

    def test_a_fresh_state_is_not_downloading(self):
        from modelpop.presentation.gallery_view_model import GalleryState

        assert not GalleryState().is_fetching

    def test_a_started_download_is(self):
        from modelpop.presentation.gallery_view_model import GalleryState

        assert GalleryState(fetching=0.0).is_fetching

    @pytest.mark.parametrize("far", [0.0, 0.5, 1.0])
    def test_every_point_along_the_way_counts_as_downloading(self, far):
        from modelpop.presentation.gallery_view_model import GalleryState

        assert GalleryState(fetching=far).is_fetching
