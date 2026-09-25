"""Pictures for the gallery.

A list of titles is not a way to choose a model. You recognise the thing you
want by looking at it, and every source already returns a thumbnail URL with
its search results - so the only missing piece was fetching them.

**Held in memory, never written to disk.** A picture shown in a window and
dropped when that window closes is what a browser does. Writing it to disk
would be caching content, which Thingiverse's API terms do not permit, and
ADR-0008 records why this application keeps nothing.

Every failure is an ordinary outcome. A thumbnail that will not load costs a
grey square, never a broken gallery - a source can be slow, a CDN can be down,
and a URL can be nonsense, none of which is a reason to stop showing results.
"""

from __future__ import annotations

from threading import Lock
from typing import TYPE_CHECKING

from modelpop.repositories.http import HttpClient, RateLimit

if TYPE_CHECKING:
    from modelpop.domain.discovery import Candidate

__all__ = ["Thumbnails"]

# Enough for a gallery of results several pages deep, and small enough that a
# long session cannot grow without bound. Thumbnails are tens of kilobytes.
REMEMBER_AT_MOST = 300


class Thumbnails:
    """Fetches and remembers gallery pictures, for as long as the app runs."""

    def __init__(self, client: HttpClient | None = None) -> None:
        """Wire the fetcher.

        Args:
            client: an HTTP client to use instead of building one, which is
                how tests drive this with no network. Built with no base URL:
                thumbnails live on CDN hosts, not on the API.
        """
        self._http = client or HttpClient("", RateLimit.unstated())
        self._seen: dict[str, bytes | None] = {}
        self._lock = Lock()

    def of(self, candidate: Candidate) -> bytes | None:
        """The picture for one result, or ``None`` if there is not one.

        Blocking, and meant to be called from a worker. Asked twice for the
        same URL it answers from memory the second time - including when the
        answer was "that one does not load", so a dead URL is tried once.
        """
        url = candidate.thumbnail_url.strip()
        if not url:
            return None

        with self._lock:
            if url in self._seen:
                return self._seen[url]

        fetched = self._http.get_bytes(url)
        picture = fetched.unwrap() if fetched.ok else None

        with self._lock:
            if len(self._seen) >= REMEMBER_AT_MOST:
                self._seen.clear()
            self._seen[url] = picture
        return picture

    def already_have(self, candidate: Candidate) -> bytes | None:
        """Whatever is in memory for this result, without fetching anything.

        So a redraw can paint what it has instead of asking again.
        """
        with self._lock:
            return self._seen.get(candidate.thumbnail_url.strip())

    def close(self) -> None:
        """Release the connection pool."""
        self._http.close()
