"""The two sanctioned repository adapters, with no network behind them.

Every response here is a fake transport returning bodies shaped like the ones
the real APIs return, per the field lists in
``docs/research/spike-repositories.md``. That is deliberate: the interesting
cases are a rate limit, a browser challenge, a field the source renamed and a
model with no printable file, and none of those can be arranged against a live
site on demand.
"""

import json
from pathlib import Path

import httpx
import pytest

from modelpop.application.repository_ports import ModelRepository
from modelpop.domain.discovery import SearchQuery
from modelpop.domain.licensing import Permission
from modelpop.repositories.http import HttpClient, RateLimit
from modelpop.repositories.myminifactory import MyMiniFactoryRepository
from modelpop.repositories.thingiverse import ACCESS_WARNING, ThingiverseRepository


def responder(handler):
    """An httpx client whose every request is answered by a function."""
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def always(body, status: int = 200, content_type: str = "application/json"):
    """A client that answers every request the same way."""
    payload = body.encode() if isinstance(body, str) else json.dumps(body).encode()

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=payload, headers={"content-type": content_type})

    return responder(handler)


def routed(routes: dict[str, object]):
    """A client that answers by path suffix, recording what it was asked."""
    asked: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request)
        for suffix, body in routes.items():
            if suffix in request.url.path:
                return httpx.Response(
                    200,
                    content=json.dumps(body).encode(),
                    headers={"content-type": "application/json"},
                )
        return httpx.Response(404, content=b"{}", headers={"content-type": "application/json"})

    return responder(handler), asked


# --------------------------------------------------------------- the HTTP layer


class TestTheHttpLayer:
    def client(self, transport) -> HttpClient:
        return HttpClient("https://example.invalid", RateLimit(requests=1000), transport)

    def test_json_comes_back_decoded(self):
        result = self.client(always({"hello": "world"})).get_json("/thing")
        assert result.unwrap() == {"hello": "world"}

    def test_a_browser_challenge_is_not_reported_as_a_rate_limit(self):
        """Cloudflare returns its interstitial with a 429 and an HTML body.

        Reading the status alone sends whoever debugs it looking for a rate
        limit that is not there.
        """
        transport = always("<html><body>Just a moment...</body></html>", 429, "text/html")
        result = self.client(transport).get_json("/thing")

        assert not result.ok
        assert "browser check" in result.error

    def test_a_challenge_is_not_reported_as_a_parse_error(self):
        transport = always("<html>Please enable JavaScript to continue</html>", 200, "text/html")
        result = self.client(transport).get_json("/thing")

        assert not result.ok
        assert "not JSON" not in result.error

    def test_a_real_rate_limit_says_when_to_come_back(self):
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(429, json={}, headers={"retry-after": "90"})

        result = self.client(responder(handler)).get_json("/thing")
        assert not result.ok
        assert "90" in result.detail

    def test_bad_credentials_point_at_settings(self):
        result = self.client(always({}, 401)).get_json("/thing")
        assert not result.ok
        assert "Settings" in result.detail

    def test_a_server_error_is_a_failure_not_an_exception(self):
        result = self.client(always({}, 503)).get_json("/thing")
        assert not result.ok
        assert "503" in result.detail

    def test_an_unreachable_host_is_a_failure_not_an_exception(self):
        def handler(_: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no route to host")

        result = self.client(responder(handler)).get_json("/thing")
        assert not result.ok
        assert "could not be reached" in result.error

    def test_malformed_json_says_so(self):
        transport = always("{ this is not json", 200, "application/json")
        result = self.client(transport).get_json("/thing")
        assert not result.ok
        assert "not JSON" in result.error

    def test_the_user_agent_is_realistic_enough_to_pass_a_bot_check(self):
        """Measured, not assumed: a library user-agent gets challenged."""
        from modelpop.repositories.http import USER_AGENT

        assert "ModelPop" in USER_AGENT, "identify ourselves first"
        assert "Mozilla/5.0" in USER_AGENT, "the part Cloudflare looks at"


class TestDownloading:
    def client(self, transport) -> HttpClient:
        return HttpClient("https://example.invalid", RateLimit(requests=1000), transport)

    def bytes_client(self, payload: bytes, status: int = 200):
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                status,
                content=payload,
                headers={
                    "content-type": "application/octet-stream",
                    "content-length": str(len(payload)),
                },
            )

        return responder(handler)

    def test_a_file_arrives_on_disk(self, tmp_path):
        client = self.client(self.bytes_client(b"solid x\nendsolid x\n"))
        result = client.download("https://example.invalid/x.stl", tmp_path / "x.stl")

        assert result.ok
        assert (tmp_path / "x.stl").read_bytes().startswith(b"solid")

    def test_progress_is_reported_and_ends_at_one(self, tmp_path):
        seen: list[float] = []
        client = self.client(self.bytes_client(b"x" * 200_000))
        client.download("https://example.invalid/x.stl", tmp_path / "x.stl", seen.append)

        assert seen
        assert seen[-1] == pytest.approx(1.0)
        assert seen == sorted(seen)

    def test_an_empty_download_is_refused_rather_than_written(self, tmp_path):
        client = self.client(self.bytes_client(b""))
        result = client.download("https://example.invalid/x.stl", tmp_path / "x.stl")

        assert not result.ok
        assert not (tmp_path / "x.stl").exists()

    def test_an_interrupted_download_leaves_nothing_that_looks_like_a_model(self, tmp_path):
        def handler(_: httpx.Request) -> httpx.Response:
            raise httpx.ReadError("connection dropped")

        result = self.client(responder(handler)).download(
            "https://example.invalid/x.stl", tmp_path / "x.stl"
        )
        assert not result.ok
        assert list(tmp_path.iterdir()) == []

    def test_a_refused_download_is_reported(self, tmp_path):
        client = self.client(self.bytes_client(b"nope", status=403))
        assert not client.download("https://example.invalid/x.stl", tmp_path / "x.stl").ok


class TestTheRateLimiter:
    def test_requests_under_the_limit_do_not_wait(self):
        limit = RateLimit(requests=3, per_seconds=60)
        slept: list[float] = []
        for _ in range(3):
            limit.wait(slept.append)
        assert slept == []

    def test_the_limit_is_enforced(self):
        limit = RateLimit(requests=2, per_seconds=60)
        slept: list[float] = []
        for _ in range(3):
            limit.wait(slept.append)

        assert len(slept) == 1
        assert slept[0] > 0

    def test_thingiverses_stated_limit_is_honoured_with_headroom(self):
        """300 per 5 minutes is documented. Ours starts when we send, theirs
        when they receive, so sit under it."""
        limit = RateLimit.thingiverse()
        assert limit.requests < 300
        assert limit.per_seconds == 300.0

    def test_an_unstated_limit_is_conservative(self):
        assert RateLimit.unstated().requests <= 60


# ------------------------------------------------------------- MyMiniFactory

MMF_OBJECT = {
    "id": 12345,
    "name": "Articulated Dragon",
    "url": "https://www.myminifactory.com/object/3d-print-dragon-12345",
    "description": "A print-in-place dragon.",
    "views": 40_000,
    "likes": 900,
    "designer": {"name": "Ada Lovelace", "username": "ada"},
    "images": [
        {"is_primary": True, "thumbnail": {"url": "https://img.invalid/thumb.jpg"}},
        {"is_primary": False, "thumbnail": {"url": "https://img.invalid/other.jpg"}},
    ],
    "licenses": [
        {"type": "mention", "value": True},
        {"type": "remix", "value": False},
        {"type": "commercial-use", "value": False},
    ],
    "tags": ["dragon", "articulated"],
    "files": [{"id": 1, "filename": "dragon.stl"}],
}


class TestMyMiniFactory:
    def repo(self, transport, key: str = "a-key") -> MyMiniFactoryRepository:
        return MyMiniFactoryRepository(
            key,
            HttpClient("https://www.myminifactory.com/api/v2", RateLimit(requests=999), transport),
        )

    def test_it_satisfies_the_port(self):
        assert isinstance(MyMiniFactoryRepository(), ModelRepository)

    def test_without_a_key_it_reports_itself_unconfigured(self):
        assert not MyMiniFactoryRepository().is_configured()

    def test_a_search_becomes_candidates(self):
        repo = self.repo(always({"total_count": 1, "items": [MMF_OBJECT]}))
        found = repo.search(SearchQuery.parse("dragon")).unwrap()

        assert len(found) == 1
        assert found[0].title == "Articulated Dragon"
        assert found[0].creator == "Ada Lovelace"
        assert found[0].source == "MyMiniFactory"

    def test_the_primary_image_is_the_thumbnail(self):
        repo = self.repo(always({"items": [MMF_OBJECT]}))
        assert (
            repo.search(SearchQuery.parse("dragon")).unwrap()[0].thumbnail_url.endswith("thumb.jpg")
        )

    def test_the_boolean_licence_vector_is_read(self):
        """remix: false is exactly No Derivatives, with nothing to parse."""
        repo = self.repo(always({"items": [MMF_OBJECT]}))
        licence = repo.search(SearchQuery.parse("dragon")).unwrap()[0].licence

        assert licence.derivatives is Permission.NO
        assert licence.commercial is Permission.NO
        assert licence.attribution_required
        assert not licence.is_remixable

    def test_an_absent_licence_is_unknown_not_forbidden(self):
        """A source that did not say is different from one that said no."""
        repo = self.repo(always({"items": [{**MMF_OBJECT, "licenses": []}]}))
        licence = repo.search(SearchQuery.parse("dragon")).unwrap()[0].licence

        assert licence.derivatives is Permission.UNKNOWN
        assert licence.is_remixable

    def test_downloads_are_zero_because_this_api_does_not_report_them(self):
        """Not a bug. The field does not exist, and inventing one would rank on air."""
        repo = self.repo(always({"items": [MMF_OBJECT]}))
        assert repo.search(SearchQuery.parse("dragon")).unwrap()[0].downloads == 0

    def test_a_renamed_field_costs_one_empty_line_not_the_gallery(self):
        broken = {**MMF_OBJECT, "designer": {"handle": "ada"}, "images": "not a list"}
        repo = self.repo(always({"items": [broken]}))
        found = repo.search(SearchQuery.parse("dragon")).unwrap()

        assert len(found) == 1
        assert found[0].creator == ""
        assert found[0].thumbnail_url == ""

    def test_an_item_with_no_name_is_dropped_rather_than_shown_blank(self):
        repo = self.repo(always({"items": [{"id": 1}, MMF_OBJECT]}))
        assert len(repo.search(SearchQuery.parse("dragon")).unwrap()) == 1

    def test_a_response_with_no_items_is_empty_not_an_error(self):
        repo = self.repo(always({"total_count": 0}))
        assert repo.search(SearchQuery.parse("dragon")).unwrap() == []

    def test_searching_without_a_key_says_what_to_do(self):
        repo = self.repo(always({"items": []}), key="")
        result = repo.search(SearchQuery.parse("dragon"))

        assert not result.ok
        assert "Settings" in result.detail

    def test_the_key_is_sent_as_a_query_parameter(self):
        transport, asked = routed({"/search": {"items": []}})
        self.repo(transport).search(SearchQuery.parse("dragon"))
        assert "key=a-key" in str(asked[0].url)

    def test_a_pasted_object_link_is_recognised(self):
        repo = self.repo(always(MMF_OBJECT))
        result = repo.resolve("https://www.myminifactory.com/object/3d-print-dragon-12345")

        assert result.ok
        assert result.unwrap().source_id == "12345"

    def test_someone_elses_link_is_declined(self):
        assert not self.repo(always(MMF_OBJECT)).resolve("https://example.invalid/x").ok

    def test_withheld_download_urls_are_explained_as_permissions(self):
        """With an API key alone the API omits every download URL. That is an
        answer about permissions, not a mysterious failure."""
        transport, _ = routed({"/files": {"items": [{"id": 1, "filename": "dragon.stl"}]}})
        repo = self.repo(transport)

        result = repo.fetch(_candidate_from(MMF_OBJECT, "MyMiniFactory"), Path())
        assert not result.ok
        assert "connected MyMiniFactory account" in result.detail


# --------------------------------------------------------------- Thingiverse

THING = {
    "id": 763622,
    "name": "Benchy",
    "public_url": "https://www.thingiverse.com/thing:763622",
    "thumbnail": "https://cdn.invalid/benchy.jpg",
    "creator": {"name": "Creative Tools"},
    "license": "Creative Commons - Attribution - No Derivatives",
    "allows_derivatives": False,
    "download_count": 573_724,
    "like_count": 22_529,
    "description": "A calibration boat.",
    "file_count": 1,
    "tags": [{"name": "calibration"}, {"name": "boat"}],
}


class TestThingiverse:
    def repo(self, transport, token: str = "a-token") -> ThingiverseRepository:
        return ThingiverseRepository(
            token, HttpClient("https://api.thingiverse.com", RateLimit(requests=999), transport)
        )

    def test_it_satisfies_the_port(self):
        assert isinstance(ThingiverseRepository(), ModelRepository)

    def test_a_search_becomes_candidates(self):
        repo = self.repo(always({"total": 1, "hits": [THING]}))
        found = repo.search(SearchQuery.parse("benchy")).unwrap()

        assert len(found) == 1
        assert found[0].title == "Benchy"
        assert found[0].downloads == 573_724

    def test_the_derivatives_flag_beats_the_licence_name(self):
        """The site itself uses the boolean to decide whether to offer remix."""
        odd = {**THING, "license": "Creative Commons - Attribution", "allows_derivatives": False}
        repo = self.repo(always({"hits": [odd]}))
        licence = repo.search(SearchQuery.parse("benchy")).unwrap()[0].licence

        assert licence.derivatives is Permission.NO
        assert not licence.is_remixable

    def test_a_known_licence_gets_a_short_badge(self):
        repo = self.repo(always({"hits": [THING]}))
        assert repo.search(SearchQuery.parse("benchy")).unwrap()[0].licence.short == "CC BY-ND"

    def test_an_unknown_licence_string_is_shown_as_given_rather_than_guessed(self):
        odd = {**THING, "license": "Some Bespoke Terms", "allows_derivatives": True}
        repo = self.repo(always({"hits": [odd]}))
        licence = repo.search(SearchQuery.parse("benchy")).unwrap()[0].licence

        assert licence.short == "Some Bespoke Terms"
        assert licence.commercial is Permission.UNKNOWN

    def test_tags_are_read_whether_strings_or_objects(self):
        repo = self.repo(always({"hits": [THING, {**THING, "id": 2, "tags": ["boat"]}]}))
        found = repo.search(SearchQuery.parse("benchy")).unwrap()

        assert "calibration" in found[0].tags
        assert "boat" in found[1].tags

    def test_a_hit_list_returned_bare_is_accepted(self):
        """The spec says hits sit under a key; be forgiving if they do not."""
        repo = self.repo(always([THING]))
        assert len(repo.search(SearchQuery.parse("benchy")).unwrap()) == 1

    def test_searching_without_a_token_says_what_to_do(self):
        result = self.repo(always({}), token="").search(SearchQuery.parse("benchy"))
        assert not result.ok
        assert "Settings" in result.detail

    def test_the_token_is_sent_as_a_bearer_header(self):
        transport, asked = routed({"/search": {"hits": []}})
        self.repo(transport).search(SearchQuery.parse("benchy"))
        assert asked[0].headers["authorization"] == "Bearer a-token"

    def test_a_pasted_thing_link_is_recognised(self):
        repo = self.repo(always(THING))
        result = repo.resolve("https://www.thingiverse.com/thing:763622")

        assert result.ok
        assert result.unwrap().source_id == "763622"

    def test_a_printable_file_is_downloaded_into_the_project(self, tmp_path):
        def handler(request: httpx.Request) -> httpx.Response:
            if "/files" in request.url.path:
                return httpx.Response(
                    200,
                    json=[
                        {"name": "notes.pdf", "direct_url": "https://cdn.invalid/notes.pdf"},
                        {
                            "name": "benchy.stl",
                            "size": 100,
                            "direct_url": "https://cdn.invalid/benchy.stl",
                        },
                    ],
                )
            return httpx.Response(200, content=b"solid benchy\nendsolid benchy\n")

        repo = self.repo(responder(handler))
        result = repo.fetch(_candidate_from(THING, "Thingiverse"), tmp_path)

        assert result.ok
        assert result.unwrap().path.name == "benchy.stl"
        assert result.unwrap().path.read_bytes().startswith(b"solid")

    def test_a_model_of_only_documents_says_so(self, tmp_path):
        transport, _ = routed(
            {"/files": [{"name": "assembly.pdf", "direct_url": "https://cdn.invalid/a.pdf"}]}
        )
        result = self.repo(transport).fetch(_candidate_from(THING, "Thingiverse"), tmp_path)

        assert not result.ok
        assert "no printable file" in result.error

    def test_a_download_carries_its_attribution(self, tmp_path):
        def handler(request: httpx.Request) -> httpx.Response:
            if "/files" in request.url.path:
                return httpx.Response(
                    200,
                    json=[{"name": "b.stl", "size": 1, "direct_url": "https://cdn.invalid/b.stl"}],
                )
            return httpx.Response(200, content=b"solid b\n")

        result = self.repo(responder(handler)).fetch(
            _candidate_from(THING, "Thingiverse"), tmp_path
        )
        credit = result.unwrap().attribution

        assert "Benchy" in credit
        assert "Creative Tools" in credit
        assert "thingiverse.com" in credit

    def test_the_access_warning_says_the_two_things_that_matter(self):
        """No scopes, and nothing cached. Both are surprising and both are true."""
        assert "scopes" in ACCESS_WARNING
        assert "never cached" in ACCESS_WARNING


def _candidate_from(raw: dict, source: str):
    from modelpop.domain.discovery import Candidate

    return Candidate(
        source=source,
        source_id=str(raw["id"]),
        title=raw["name"],
        url=raw.get("public_url") or raw.get("url") or "",
        creator=(raw.get("creator") or raw.get("designer") or {}).get("name", ""),
    )
