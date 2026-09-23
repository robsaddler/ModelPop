# ADR-0008 — Which model repositories ModelPop talks to, and how

**Status:** Accepted
**Date:** 2026-09-23
**Supersedes the source table in:** `docs/07-discovery-and-remix.md`
**Evidence:** `docs/research/spike-repositories.md` — every claim verified by fetching the URL on
the day.

## Context

`docs/07` named five sources as a starting point and said explicitly that the access details
*"need verifying in a Phase 7 spike"*. They have now been verified, and two of the five are not
usable in the way the plan assumed. One of the two that are usable comes with a licence term that
shapes the download path rather than merely constraining it.

Rob's constraints that bear on this: open source only, no paid services, and **inform rather than
police** on licensing. That last one is about the *model's* licence, which the user accepts
responsibility for. It says nothing about ModelPop's own obligations to the sites it queries, and
those are ours, not the user's.

## Decision

### 1. Two sanctioned adapters: MyMiniFactory and Thingiverse

Both have documented public APIs, live specs, free self-service credentials and no charge.
MyMiniFactory explicitly invites integrations from slicers, which is precisely what this is.

Each is configured independently and each degrades to "not configured" rather than an error, so
the app is useful with neither, one, or both.

### 2. No persistent cache of Thingiverse content

The Thingiverse API Licence Agreement, revised 19 February 2026, licenses only *"limited
intermediate copies of Content"*, requires them deleted when no longer needed and within thirty
days regardless, and separately prohibits *"copy or store the Content, other than for the
intermediate purposes allowed"*.

So ModelPop **downloads into the open editing session and nowhere else**. The user saves the
result where they like, which is their file and their decision. There is no library, no index, no
thumbnail cache on disk, and no background prefetch.

This is a real constraint on the design and it will look like a missing feature to whoever reads
the code next, which is exactly why it is written down here. A model library is a reasonable thing
to want and it is not available to us on these terms.

The same rule is applied to every source rather than only to Thingiverse. One download path is
easier to keep honest than two, and none of the other sources' terms are made worse by it.

### 3. MakerWorld: file drop, and no network request at all

MakerWorld has no sanctioned API, and its Terms of Use §9 forbid using *"any automatic device,
program, algorithm or methodology, or by any means of artificial intelligence service"* to access
or acquire any content. The site's backend does answer anonymously, so a link-paste integration
would be trivial to write. We are not writing it.

Instead: the user downloads the 3MF themselves, as they already do, and drops it on the window.
ModelPop reads the title, the print profile and the plate settings out of that file.

This is not a lesser path. A downloaded MakerWorld 3MF carries the **Bambu print profile** — plate
layout, layer height, filament assignments, printer compatibility — which is the most valuable
thing MakerWorld has and which a metadata scrape would not give us anyway.

### 4. Printables: link import, off by default, clearly labelled

Printables has no official API and no documentation. Its own GraphQL backend answers anonymously
and works well, and its terms of service are entirely silent on APIs and automated access — so
this is unsanctioned rather than prohibited.

It is also **demonstrably unstable**: the search operation the site uses today already replaced
the one community clients were written against, and a real third-party client shipped a schema-break
fix five days before this was written.

So it ships as a link-paste path, **disabled by default**, with a settings label that says it is
unofficial and may stop working. Every operation string lives in one module so a break is one
file to fix. It is not presented as an API, because it is not one.

Its licence data is the best of any source — an explicit `disallowRemixing` boolean and a separate
commercial-use flag — which is why it is worth having at all.

### 5. Thangs is dropped

Every endpoint returns 401 and there is no mechanism to issue credentials to a third-party
application. Geometric search, the only reason it was on the list, appears to have been removed
from the product: no file input anywhere in the current site bundle, no "geometric" string outside
a dead no-results message, and the geometric index field on a live model page is null.

The engine lives on at Physna, whose API is genuinely capable, but it needs an enterprise Okta
tenant with unpublished pricing and — decisively — matches against models *you* uploaded, not
against a public catalogue. That is not the capability that made it interesting.

A user who already has a Thangs download needs no Thangs-specific code. It is just an STL.

### 6. Thumbnails are fetched for display and not written to disk

Follows from (2). A gallery of forty tiles fetches forty images into memory while the gallery is
open and forgets them when it closes.

## Consequences

**Good.** Both network sources are sanctioned, so there is no legal question hanging over the
feature. The licence data from MyMiniFactory is structured better than CC strings and can be used
for ranking without parsing anything. The MakerWorld path gets the print profile, which is better
than what a scrape would have produced. The gallery is useful with no credentials at all, because
file drop needs none.

**Bad.** No offline library and no search history. Two of the five sources named in the plan are
gone. In-app downloading from MyMiniFactory needs the OAuth authorisation-code flow, because the
API key alone does not authorise downloads — that split is the API's, not a choice.

**Watch.** Thingiverse apps are capped at ten users until moderated, and access has no scopes, so
a user has to grant full read/write on their account just to search. The settings panel says so
in those words. Thingiverse also sits behind a Cloudflare challenge that returns HTML where JSON
was expected unless a realistic user-agent is sent; that is handled in the HTTP layer, not at each
call site.

## Alternatives considered

**Scrape the sites that have no API.** Rejected for MakerWorld on the terms, and rejected
generally: a scraper breaks silently, which is the worst failure mode for a feature the user
relies on to find a starting point.

**Ship Printables' GraphQL as a first-class search source.** Rejected. It works today and it will
break, and a search box that silently returns nothing is worse than one that was never offered.
Behind a labelled switch, a user who turns it on knows what they are getting.

**Wait for a MakerWorld API.** Rejected. The request has been open since January 2024 with no
answer, and the file-drop path is available now and carries better data.

**Build a local library anyway and rely on nobody noticing.** Rejected on the terms as written.
The constraint is inconvenient, not ambiguous.
