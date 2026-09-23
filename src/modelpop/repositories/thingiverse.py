"""Thingiverse, the other sanctioned source.

Documented OpenAPI, a live spec, and a supported "Desktop" application type.
Three things about it decide how this adapter behaves, all verified in
``docs/research/spike-repositories.md``:

**Nothing is cached.** The API Licence Agreement, revised 19 February 2026,
licenses only limited intermediate copies and separately prohibits storing
content. So a download goes into the open editing session and the user saves it
themselves. ADR-0008 records why, because it will otherwise look like a missing
feature.

**Cloudflare, not a rate limit.** A burst from a default client user-agent comes
back as HTTP 429 with an HTML interstitial. That is a browser check wearing a
rate limit's status code, and the HTTP layer tells them apart.

**Access is all or nothing.** The API has no scopes, so a user granting access
grants full read and write on their Thingiverse account just to search. The
settings panel says that in those words; it is not our place to bury it.

The stated rate limit is 300 requests per five minutes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from modelpop.domain.discovery import Candidate
from modelpop.domain.licensing import Licence, Permission
from modelpop.domain.result import Result, failure, success
from modelpop.repositories.http import HttpClient, RateLimit

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from modelpop.application.repository_ports import Download
    from modelpop.domain.discovery import SearchQuery

__all__ = ["ACCESS_WARNING", "ThingiverseRepository"]

NAME = "Thingiverse"
BASE_URL = "https://api.thingiverse.com"

ACCESS_WARNING = (
    "Thingiverse has no permission scopes: connecting ModelPop grants it full "
    "read and write access to your Thingiverse account, not just search. "
    "Models are downloaded into the open project and never cached, because "
    "Thingiverse's API terms do not permit storing them."
)

# Licence strings this API returns, and what they mean. Kept as a table rather
# than parsed, because "Creative Commons - Attribution - No Derivatives" is a
# label from a fixed set, not a grammar.
_LICENCES: dict[str, tuple[str, Permission, Permission, bool]] = {
    "creative commons - public domain dedication": ("CC0", Permission.YES, Permission.YES, False),
    "public domain": ("Public domain", Permission.YES, Permission.YES, False),
    "creative commons - attribution": ("CC BY", Permission.YES, Permission.YES, True),
    "creative commons - attribution - share alike": (
        "CC BY-SA",
        Permission.YES,
        Permission.YES,
        True,
    ),
    "creative commons - attribution - no derivatives": (
        "CC BY-ND",
        Permission.NO,
        Permission.YES,
        True,
    ),
    "creative commons - attribution - non-commercial": (
        "CC BY-NC",
        Permission.YES,
        Permission.NO,
        True,
    ),
    "creative commons - attribution - non-commercial - share alike": (
        "CC BY-NC-SA",
        Permission.YES,
        Permission.NO,
        True,
    ),
    "creative commons - attribution - non-commercial - no derivatives": (
        "CC BY-NC-ND",
        Permission.NO,
        Permission.NO,
        True,
    ),
    "gnu - gpl": ("GPL", Permission.YES, Permission.YES, True),
    "gnu - lgpl": ("LGPL", Permission.YES, Permission.YES, True),
    "bsd license": ("BSD", Permission.YES, Permission.YES, True),
    "nokia": ("Nokia", Permission.UNKNOWN, Permission.UNKNOWN, True),
    "all rights reserved": ("All rights reserved", Permission.NO, Permission.NO, False),
}

_PRINTABLE = (".stl", ".3mf", ".obj", ".ply", ".step", ".stp")


class ThingiverseRepository:
    """Satisfies the ``ModelRepository`` port against Thingiverse."""

    def __init__(self, token: str = "", client: HttpClient | None = None) -> None:
        """Wire the adapter.

        Args:
            token: an OAuth bearer token. The API has no other auth: there is
                no anonymous read and basic auth is not offered.
            client: an HTTP client to use instead of building one.
        """
        self._token = token.strip()
        self._http = client or HttpClient(BASE_URL, RateLimit.thingiverse())

    @property
    def _auth(self) -> dict[str, str]:
        """The bearer header, sent on every request.

        Per request rather than baked into the client, so the adapter keeps its
        credentials when it is handed a client rather than building one.
        """
        return {"Authorization": f"Bearer {self._token}"} if self._token else {}

    @property
    def name(self) -> str:
        """What to call this source on a gallery card."""
        return NAME

    def is_configured(self) -> bool:
        """Whether a token has been supplied."""
        return bool(self._token)

    def can_search(self) -> bool:
        """This source has a real search API."""
        return True

    # -------------------------------------------------------------- search

    def search(self, query: SearchQuery, limit: int = 20) -> Result[list[Candidate]]:
        """Find candidates matching a query."""
        if not self.is_configured():
            return failure(f"{NAME} is not connected", "Connect it in Settings.")

        term = query.text.strip()
        if not term:
            return success([])

        outcome = self._http.get_json(
            f"/search/{term}/",
            {"type": "things", "per_page": max(1, min(limit, 30)), "page": 1, "sort": "relevant"},
            self._auth,
        )
        if not outcome.ok:
            return outcome  # type: ignore[return-value]

        body = outcome.unwrap()
        hits = body.get("hits") if isinstance(body, dict) else body
        if not isinstance(hits, list):
            return success([])

        return success([c for c in (_read_thing(hit) for hit in hits) if c is not None])

    def resolve(self, url: str) -> Result[Candidate]:
        """Turn a pasted thing URL into a candidate."""
        thing_id = _id_in(url)
        if thing_id is None:
            return failure("Not a Thingiverse link")
        if not self.is_configured():
            return failure(f"{NAME} is not connected", "Connect it in Settings.")

        outcome = self._http.get_json(f"/things/{thing_id}", headers=self._auth)
        if not outcome.ok:
            return outcome  # type: ignore[return-value]

        candidate = _read_thing(outcome.unwrap())
        if candidate is None:
            return failure("That model could not be read")
        return success(candidate)

    # --------------------------------------------------------------- fetch

    def fetch(
        self,
        candidate: Candidate,
        into: Path,
        on_progress: Callable[[float], None] | None = None,
    ) -> Result[Download]:
        """Download a candidate's model file into the open project.

        Deliberately into the working directory the caller names, and nowhere
        else. Thingiverse's terms do not permit keeping a copy, so there is no
        library to put it in.
        """
        from modelpop.application.repository_ports import Download

        if not self.is_configured():
            return failure(f"{NAME} is not connected", "Connect it in Settings.")

        listed = self._http.get_json(f"/things/{candidate.source_id}/files", headers=self._auth)
        if not listed.ok:
            return listed  # type: ignore[return-value]

        files = listed.unwrap()
        if not isinstance(files, list) or not files:
            return failure("That model has no downloadable files")

        chosen = _best_file(files)
        if chosen is None:
            return failure(
                "That model has no printable file",
                "It may be documentation or images only. Open it in a browser to check.",
            )

        url, filename = chosen
        written = self._http.download(url, into / filename, on_progress, self._auth)
        if not written.ok:
            return written  # type: ignore[return-value]

        return success(Download(written.unwrap(), candidate))


# ---------------------------------------------------------------- translation


def _read_thing(raw: Any) -> Candidate | None:
    """Turn one API thing into a ``Candidate``."""
    if not isinstance(raw, dict):
        return None
    identity = raw.get("id")
    name = raw.get("name")
    if identity is None or not isinstance(name, str):
        return None

    creator = raw.get("creator")
    who = creator.get("name") or creator.get("first_name") if isinstance(creator, dict) else ""

    return Candidate(
        source=NAME,
        source_id=str(identity),
        title=name,
        url=str(raw.get("public_url") or raw.get("url") or ""),
        thumbnail_url=str(raw.get("thumbnail") or ""),
        creator=str(who or ""),
        licence=_read_licence(raw.get("license"), raw.get("allows_derivatives")),
        downloads=_whole_number(raw.get("download_count")),
        likes=_whole_number(raw.get("like_count")),
        description=str(raw.get("description") or "")[:600],
        file_count=_whole_number(raw.get("file_count")),
        tags=tuple(_tag_names(raw.get("tags")))[:12],
    )


def _read_licence(name: Any, allows_derivatives: Any) -> Licence:
    """Read the licence string, and trust the boolean over the table.

    The API carries both a name and an explicit ``allows_derivatives`` flag.
    Where they disagree the flag wins: it is the field the site itself uses to
    decide whether to offer a remix button.
    """
    if not isinstance(name, str) or not name.strip():
        return Licence()

    short, derivatives, commercial, attribution = _LICENCES.get(
        name.strip().lower(), (name.strip(), Permission.UNKNOWN, Permission.UNKNOWN, True)
    )
    if isinstance(allows_derivatives, bool):
        derivatives = Permission.YES if allows_derivatives else Permission.NO

    return Licence(
        raw=name.strip(),
        short=short,
        derivatives=derivatives,
        commercial=commercial,
        attribution_required=attribution,
    )


def _tag_names(raw: Any) -> list[str]:
    """Tag names, whether the API sent strings or objects."""
    if not isinstance(raw, list):
        return []
    names: list[str] = []
    for tag in raw:
        if isinstance(tag, str):
            names.append(tag)
        elif isinstance(tag, dict) and isinstance(tag.get("name"), str):
            names.append(tag["name"])
    return names


def _best_file(files: list[Any]) -> tuple[str, str] | None:
    """The printable file worth downloading, and what to call it.

    ``direct_url`` is documented as null for anything not in the printable
    list, which makes it a useful filter as well as a URL.
    """
    candidates: list[tuple[int, str, str]] = []
    for entry in files:
        if not isinstance(entry, dict):
            continue
        filename = entry.get("name")
        if not isinstance(filename, str):
            continue
        url = entry.get("direct_url") or entry.get("download_url") or entry.get("public_url")
        if not isinstance(url, str) or not url:
            continue
        if not filename.lower().endswith(_PRINTABLE):
            continue
        # a smaller printable file first: it is usually the part, not the set
        candidates.append((_whole_number(entry.get("size")), filename, url))

    if not candidates:
        return None
    candidates.sort()
    _, filename, url = candidates[0]
    return url, filename


def _whole_number(value: Any) -> int:
    """A count, or zero when the source sent something else."""
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(value, 0)
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return 0


def _id_in(url: str) -> str | None:
    """The thing id in a Thingiverse page URL, if it is one."""
    if "thingiverse.com" not in url:
        return None
    for part in url.rstrip("/").replace(":", "/").split("/"):
        if part.isdigit():
            return part
    return None
