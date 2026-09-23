"""The shared HTTP client for repository adapters.

Three things belong here rather than at each call site, because all three were
found by measurement rather than read in a document:

**A realistic user-agent.** ``api.thingiverse.com`` sits behind a Cloudflare
managed challenge. With a default client user-agent a short burst returns HTTP
429 with an HTML interstitial; with a browser user-agent the same requests
return clean JSON. See ``docs/research/spike-repositories.md``.

**HTML where JSON was expected means a challenge, not a parse error.** Reporting
"could not read the response" for that would send whoever debugs it in exactly
the wrong direction.

**Rate limits the sites state.** Thingiverse publishes 300 requests per five
minutes. MyMiniFactory publishes nothing, so it gets a conservative default:
being a good guest costs nothing here and getting an application key revoked
costs the feature.

Nothing here writes to disk. ADR-0008 forbids caching repository content, and
one download path with no cache is easier to keep honest than two.
"""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx

from modelpop.domain.result import Result, failure, success

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

__all__ = ["HttpClient", "RateLimit"]

# Sending a plain library user-agent gets challenged. This is not an attempt to
# hide what we are - the app identifies itself first - it is the part Cloudflare
# actually looks at.
USER_AGENT = (
    "ModelPop/0.1 (+https://github.com/robsaddler/ModelPop) "
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)

_TIMEOUT = 20.0
_DOWNLOAD_TIMEOUT = 300.0
_CHUNK = 64 * 1024

# A response that is neither JSON nor an error we recognise. Cloudflare's
# interstitial says this; so does an origin serving a maintenance page.
_CHALLENGE_MARKERS = ("just a moment", "cf-browser-verification", "enable javascript")


@dataclass
class RateLimit:
    """A token bucket over a sliding window, shared across threads.

    Sources are searched in parallel, so two threads can hit the same host at
    once. Without the lock the window would be a suggestion.
    """

    requests: int = 60
    per_seconds: float = 60.0
    _times: deque[float] = field(default_factory=deque, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @classmethod
    def thingiverse(cls) -> RateLimit:
        """300 per 5 minutes, as the documentation states.

        Deliberately a little under, because the window here starts when we
        send and theirs starts when they receive.
        """
        return cls(requests=280, per_seconds=300.0)

    @classmethod
    def unstated(cls) -> RateLimit:
        """For a source that publishes no limit.

        Conservative on purpose. There is no cost to being a good guest and a
        real cost to having a key revoked.
        """
        return cls(requests=60, per_seconds=60.0)

    def wait(self, sleep: Callable[[float], None] = time.sleep) -> float:
        """Block until another request is allowed. Returns how long it waited."""
        with self._lock:
            now = time.monotonic()
            self._forget_before(now - self.per_seconds)
            if len(self._times) < self.requests:
                self._times.append(now)
                return 0.0
            delay = self._times[0] + self.per_seconds - now

        sleep(max(delay, 0.0))

        with self._lock:
            now = time.monotonic()
            self._forget_before(now - self.per_seconds)
            self._times.append(now)
        return max(delay, 0.0)

    def _forget_before(self, cutoff: float) -> None:
        while self._times and self._times[0] <= cutoff:
            self._times.popleft()


class HttpClient:
    """A small JSON-and-files client for repository adapters.

    Every method returns a ``Result``. A source being down, rate-limited or
    behind a challenge are all expected outcomes of asking a third party for
    something, not exceptional ones, and one dead source must never take the
    gallery with it.
    """

    def __init__(
        self,
        base_url: str = "",
        limit: RateLimit | None = None,
        client: httpx.Client | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        """Wire the client.

        Args:
            base_url: prefix for relative paths.
            limit: how fast this host may be asked. Conservative by default.
            client: an ``httpx.Client`` to use instead of building one, which
                is how tests supply a transport with no network behind it.
            headers: sent with every request, on top of the user-agent.
        """
        self._base = base_url.rstrip("/")
        self._limit = limit or RateLimit.unstated()
        self._extra = dict(headers or {})
        self._client = client or httpx.Client(
            timeout=_TIMEOUT, follow_redirects=True, headers={"User-Agent": USER_AGENT}
        )
        self._owns_client = client is None

    def close(self) -> None:
        """Release the connection pool, if this client made one."""
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> HttpClient:
        """Use as a context manager."""
        return self

    def __exit__(self, *_: object) -> None:
        """Close on the way out."""
        self.close()

    # ----------------------------------------------------------------- json

    def get_json(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Result[dict[str, Any] | list[Any]]:
        """Fetch and decode a JSON document."""
        return self._json("GET", path, params=params, extra_headers=headers)

    def post_json(
        self,
        path: str,
        body: dict[str, Any],
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Result[dict[str, Any] | list[Any]]:
        """Send and decode JSON."""
        return self._json("POST", path, params=params, json=body, extra_headers=headers)

    def _json(
        self,
        method: str,
        path: str,
        extra_headers: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> Result[dict[str, Any] | list[Any]]:
        self._limit.wait()
        try:
            response = self._client.request(
                method, self._url(path), headers=self._headers(extra_headers), **kwargs
            )
        except httpx.HTTPError as error:
            return failure("The source could not be reached", str(error))

        problem = self._problem_with(response)
        if problem is not None:
            return problem

        try:
            return success(response.json())
        except (json.JSONDecodeError, ValueError):
            return failure(
                "The source sent something that was not JSON",
                f"{response.status_code} with {len(response.content)} bytes.",
            )

    def _problem_with(self, response: httpx.Response) -> Result[Any] | None:
        """Turn an unhappy response into a ``Failure``, or return ``None``."""
        if _looks_like_a_challenge(response):
            return failure(
                "The source is asking for a browser check",
                "It served a bot-protection page instead of data. Try again shortly.",
            )
        if response.status_code == httpx.codes.TOO_MANY_REQUESTS:
            retry = response.headers.get("retry-after", "")
            return failure(
                "The source is rate limiting us",
                f"Try again in {retry} seconds." if retry else "Try again shortly.",
            )
        if response.status_code in (httpx.codes.UNAUTHORIZED, httpx.codes.FORBIDDEN):
            return failure(
                "The source refused the credentials",
                "Check the API key in Settings.",
            )
        if response.status_code >= httpx.codes.BAD_REQUEST:
            return failure(
                "The source returned an error",
                f"HTTP {response.status_code}.",
            )
        return None

    # ------------------------------------------------------------ downloads

    def download(
        self,
        url: str,
        into: Path,
        on_progress: Callable[[float], None] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Result[Path]:
        """Stream a file to disk.

        Written to a neighbouring temporary file and moved into place, so an
        interrupted download never leaves something that looks like a model
        and is half a model.
        """
        self._limit.wait()
        scratch = into.with_suffix(into.suffix + ".part")
        try:
            into.parent.mkdir(parents=True, exist_ok=True)
            with self._client.stream(
                "GET",
                url,
                timeout=_DOWNLOAD_TIMEOUT,
                headers=self._headers(headers),
            ) as response:
                problem = self._problem_with(response)
                if problem is not None:
                    return problem

                expected = float(response.headers.get("content-length") or 0)
                written = 0
                with scratch.open("wb") as handle:
                    for chunk in response.iter_bytes(_CHUNK):
                        handle.write(chunk)
                        written += len(chunk)
                        if on_progress is not None and expected > 0:
                            on_progress(min(written / expected, 1.0))

            if written == 0:
                scratch.unlink(missing_ok=True)
                return failure("The download was empty", "The source sent no data.")

            scratch.replace(into)
        except httpx.HTTPError as error:
            scratch.unlink(missing_ok=True)
            return failure("The download failed", str(error))
        except OSError as error:
            scratch.unlink(missing_ok=True)
            return failure("The file could not be written", str(error))

        if on_progress is not None:
            on_progress(1.0)
        return success(into)

    def _headers(self, extra: dict[str, str] | None) -> dict[str, str] | None:
        """Per-client headers plus any this call adds.

        Per-call rather than only at construction, because an adapter given a
        ready-made client - which is how tests drive one - would otherwise lose
        its credentials silently. A test found exactly that.
        """
        merged = {**self._extra, **(extra or {})}
        return merged or None

    def _url(self, path: str) -> str:
        if path.startswith(("http://", "https://")):
            return path
        return f"{self._base}/{path.lstrip('/')}"


def _looks_like_a_challenge(response: httpx.Response) -> bool:
    """Whether a response is a bot check rather than data.

    Cloudflare returns its interstitial with a 429 and an HTML body, so the
    status code alone sends you looking for a rate limit that is not there.
    """
    content_type = response.headers.get("content-type", "").lower()
    if "html" not in content_type:
        return False
    try:
        body = response.text[:2000].lower()
    except (UnicodeDecodeError, httpx.ResponseNotRead):
        return False
    return any(marker in body for marker in _CHALLENGE_MARKERS)
