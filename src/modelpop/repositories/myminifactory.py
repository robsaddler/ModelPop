"""MyMiniFactory, the friendliest of the sanctioned sources.

Documented OpenAPI v2, a free self-service key, and a manufacturers page that
explicitly invites slicer integrations - which is exactly what this is. See
``docs/research/spike-repositories.md`` for what was verified and when.

Two things about this API shape the adapter:

**Licences are a boolean vector, not a string.** A result carries entries like
``{"type": "remix", "value": false}`` for the types ``mention``, ``remix``,
``commercial-use`` and ``exclusivity``. That is *better* than a Creative Commons
string for ranking: No Derivatives is exactly ``remix: false``, with nothing to
parse and nothing to get wrong.

**A key gets you search; downloading needs OAuth.** ``download_url`` and
``archive_download_url`` are annotated in the spec as available only to an
OAuth-connected user. So with a key alone the gallery browses and deep-links
out, and in-app download needs the authorisation-code flow. That split is the
API's, not a choice, and the adapter reports it in those words rather than
failing with a bare 401.

There is **no download count** in this API, only views and likes. Every field on
``Candidate`` is optional for reasons like this one.
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

__all__ = ["MyMiniFactoryRepository"]

NAME = "MyMiniFactory"
BASE_URL = "https://www.myminifactory.com/api/v2"

# The site's own object page, for the "open in a browser" link and for
# recognising a pasted URL.
_PAGE_PREFIX = "https://www.myminifactory.com/object/"


class MyMiniFactoryRepository:
    """Satisfies the ``ModelRepository`` port against MyMiniFactory."""

    def __init__(self, api_key: str = "", client: HttpClient | None = None) -> None:
        """Wire the adapter.

        Args:
            api_key: from ``myminifactory.com/settings/developer``. Free, and
                behind a free account.
            client: an HTTP client to use instead of building one, which is how
                tests drive this with no network.
        """
        self._key = api_key.strip()
        self._http = client or HttpClient(BASE_URL, RateLimit.unstated())

    @property
    def name(self) -> str:
        """What to call this source on a gallery card."""
        return NAME

    def is_configured(self) -> bool:
        """Whether a key has been supplied."""
        return bool(self._key)

    def can_search(self) -> bool:
        """This source has a real search API."""
        return True

    # -------------------------------------------------------------- search

    def search(self, query: SearchQuery, limit: int = 20) -> Result[list[Candidate]]:
        """Find candidates matching a query."""
        if not self.is_configured():
            return failure(f"{NAME} has no API key", "Add one in Settings.")

        outcome = self._http.get_json(
            "/search",
            {
                "q": query.text,
                "per_page": max(1, min(limit, 50)),
                "page": 1,
                "key": self._key,
            },
        )
        if not outcome.ok:
            return outcome  # type: ignore[return-value]

        body = outcome.unwrap()
        if not isinstance(body, dict):
            return failure(f"{NAME} sent an unexpected response")

        items = body.get("items")
        if not isinstance(items, list):
            return success([])

        return success([c for c in (_read_object(item) for item in items) if c is not None])

    def resolve(self, url: str) -> Result[Candidate]:
        """Turn a pasted object URL into a candidate."""
        object_id = _id_in(url)
        if object_id is None:
            return failure("Not a MyMiniFactory link")
        if not self.is_configured():
            return failure(f"{NAME} has no API key", "Add one in Settings.")

        outcome = self._http.get_json(f"/objects/{object_id}", {"key": self._key})
        if not outcome.ok:
            return outcome  # type: ignore[return-value]

        body = outcome.unwrap()
        candidate = _read_object(body) if isinstance(body, dict) else None
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
        """Download a candidate's model file.

        Needs an OAuth-connected user. With an API key alone the API withholds
        every download URL, which is a permissions answer and is reported as
        one rather than as a mysterious failure.
        """
        from modelpop.application.repository_ports import Download

        if not self.is_configured():
            return failure(f"{NAME} has no API key", "Add one in Settings.")

        listed = self._http.get_json(f"/objects/{candidate.source_id}/files", {"key": self._key})
        if not listed.ok:
            return listed  # type: ignore[return-value]

        body = listed.unwrap()
        files = body.get("items") if isinstance(body, dict) else None
        if not isinstance(files, list) or not files:
            return failure(
                "That model has no downloadable files",
                f"{NAME} listed none for it.",
            )

        chosen = _best_file(files)
        if chosen is None:
            return failure(
                f"{NAME} will not hand over the file with an API key alone",
                "Downloading needs a connected MyMiniFactory account. You can open the "
                "model in a browser and download it there, then drop the file on the window.",
            )

        url, filename = chosen
        target = into / filename
        written = self._http.download(url, target, on_progress)
        if not written.ok:
            return written  # type: ignore[return-value]

        return success(Download(written.unwrap(), candidate))


# ---------------------------------------------------------------- translation


def _read_object(raw: Any) -> Candidate | None:
    """Turn one API object into a ``Candidate``.

    Every field is treated as absent-until-proven-present. A source changing a
    field name should cost one empty subtitle, not a crashed gallery.
    """
    if not isinstance(raw, dict):
        return None
    identity = raw.get("id")
    name = raw.get("name")
    if identity is None or not isinstance(name, str):
        return None

    designer = raw.get("designer")
    creator = designer.get("name") or designer.get("username") if isinstance(designer, dict) else ""

    return Candidate(
        source=NAME,
        source_id=str(identity),
        title=name,
        url=str(raw.get("url") or f"{_PAGE_PREFIX}{identity}"),
        thumbnail_url=_thumbnail(raw.get("images")),
        creator=str(creator or ""),
        licence=_read_licence(raw.get("licenses")),
        downloads=0,  # this API does not report downloads at all
        likes=_whole_number(raw.get("likes")),
        description=str(raw.get("description") or "")[:600],
        file_count=len(raw.get("files") or []),
        tags=tuple(str(t) for t in (raw.get("tags") or []) if isinstance(t, str))[:12],
    )


def _read_licence(raw: Any) -> Licence:
    """Read the boolean licence vector.

    Absent is not the same as false. A source that did not say gets
    ``UNKNOWN``, and the badge says so rather than inventing a permission.
    """
    if not isinstance(raw, list) or not raw:
        return Licence()

    flags: dict[str, bool] = {}
    for entry in raw:
        if isinstance(entry, dict):
            kind, value = entry.get("type"), entry.get("value")
            if isinstance(kind, str) and isinstance(value, bool):
                flags[kind] = value

    if not flags:
        return Licence()

    derivatives = _permission(flags.get("remix"))
    commercial = _permission(flags.get("commercial-use"))
    attribution = flags.get("mention", False)

    parts = []
    if attribution:
        parts.append("credit the designer")
    if derivatives is Permission.NO:
        parts.append("no derivatives")
    if commercial is Permission.NO:
        parts.append("non-commercial")
    if flags.get("exclusivity"):
        parts.append("MyMiniFactory exclusive")

    short = "MMF: " + (", ".join(parts) if parts else "open")
    return Licence(
        raw=short,
        short=short,
        derivatives=derivatives,
        commercial=commercial,
        attribution_required=attribution,
    )


def _permission(value: bool | None) -> Permission:
    """A tri-state from an optional boolean."""
    if value is None:
        return Permission.UNKNOWN
    return Permission.YES if value else Permission.NO


def _thumbnail(images: Any) -> str:
    """The primary image's thumbnail, or the first one there is."""
    if not isinstance(images, list):
        return ""
    ordered = [i for i in images if isinstance(i, dict)]
    primary = next((i for i in ordered if i.get("is_primary")), None)
    for image in (primary, *ordered):
        if image is None:
            continue
        for size in ("thumbnail", "standard", "original"):
            block = image.get(size)
            if isinstance(block, dict):
                url = block.get("url")
                if isinstance(url, str) and url:
                    return url
    return ""


def _best_file(files: list[Any]) -> tuple[str, str] | None:
    """The printable file worth downloading, and what to call it.

    Prefers a mesh over anything else, because a gallery pick is meant to open
    in the viewport and a PDF assembly guide will not.
    """
    printable = (".stl", ".3mf", ".obj", ".ply", ".step", ".stp")
    candidates: list[tuple[int, str, str]] = []

    for entry in files:
        if not isinstance(entry, dict):
            continue
        url = entry.get("download_url")
        filename = entry.get("filename")
        if not isinstance(url, str) or not url or not isinstance(filename, str):
            continue
        rank = 0 if filename.lower().endswith(printable) else 1
        candidates.append((rank, filename, url))

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
    """The object id in a MyMiniFactory page URL, if it is one."""
    if "myminifactory.com" not in url:
        return None
    tail = url.rstrip("/").rsplit("/", 1)[-1]
    # pages are /object/<slug>-<id>
    digits = tail.rsplit("-", 1)[-1]
    return digits if digits.isdigit() else None
