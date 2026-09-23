# Spike — which model repositories can ModelPop actually search?

**Run 23 September 2026.** Every claim below was verified by fetching the URL on the day. Where
something could not be verified it says so. This exists because `docs/07-discovery-and-remix.md`
listed five sources as a *research starting point*, and two of the five turned out to be unusable
for reasons that are not visible from the outside.

## Verdicts

| Source | Verdict | Why |
|---|---|---|
| **MyMiniFactory** | **BUILD** | Sanctioned, documented, live OpenAPI v2. Free key, self-service. They explicitly invite slicer integrations. |
| **Thingiverse** | **BUILD, with a caching constraint** | Sanctioned OAuth2 REST API with a live OpenAPI spec. But the API Licence Agreement forbids storing content. See below. |
| **Printables** | **LINK IMPORT** | No official API, no docs, no app registration. The site's own GraphQL endpoint works unauthenticated but is unsanctioned and demonstrably drifts. |
| **MakerWorld** | **FILE DROP ONLY** | No sanctioned API, and the Terms of Use explicitly forbid automated access. |
| **Thangs** | **DROPPED** | Every endpoint returns 401, no key issuance exists, and geometric search — the only reason it was on the list — appears to have been removed from the product. |

---

## MyMiniFactory — build this one first

* Spec: `https://www.myminifactory.com/api-doc/api-v2.yaml` (Swagger 2.0, live, 27 KB).
  `https://www.myminifactory.com/api-doc/` without `index.html` is a 404; use the full path.
* Base URL `https://www.myminifactory.com/api/v2`. Key as `?key=`, self-service at
  `/settings/developer` behind a free account.
* Search is `GET /search?q=&page=&per_page=&sort=visits|date|popularity`, returning
  `{total_count, items:[Object]}`. Results carry thumbnails, designer, views, likes and licences.
* **No download count.** Only `views` and `likes`, which is why ModelPop's ranking treats
  popularity as one damped axis rather than the axis.
* **Licences are a boolean vector, not a string**: `[{type: "remix", value: false}, …]` with types
  `mention`, `remix`, `commercial-use`, `exclusivity`. That is *better* than a CC string for this
  app — No Derivatives is exactly `remix: false`, and it is a server-side filter, so it can be a
  ranking input without any string parsing.
* **The split that forces a design decision:** `download_url` and `archive_download_url` are
  annotated *"Available ONLY with Oauth connected User. Not with API key."* So a key gives search
  and metadata; downloading in-app needs the full OAuth authorisation-code flow with the
  `download` scope. That split is the API's, not ours.
* Terms searched for `scrape`, `spider`, `crawler`, `automated means`, `harvest`: **no general
  prohibition on automated access.** The only automation clause is about contest vote fraud.
  Their manufacturers page solicits slicer integrations directly.
* `robots.txt` disallows `/api/`. Read as anti-indexing hygiene rather than access policy — a
  keyed API client is not a crawler — but the signal is ambiguous and worth recording.
* Alive: PHP 8.4.13 on the API host, active third-party clients pushed within the last month, and
  an OpenAPI-generated Go client whose bundled spec matches today's live one structurally.

**Unknowns to settle on first contact:** whether the key is issued instantly or approval-gated;
the real rate limit (none is published and no rate-limit headers come back); and whether the OAuth
token URL is `/v1/oauth/tokens` (what a working client uses) or `/v1/oauth/` (what the spec says).
Try `/v1/oauth/tokens` first.

## Thingiverse — build it, but do not cache

* Spec: `https://www.thingiverse.com/swagger/docs/openapi.yaml` (OpenAPI 3.0.0, live).
  The old `/developers/rest-api-reference` page is a 404; anything linking to it is stale.
* OAuth2 only, browser flow only, no basic auth. Register at `/apps/create` (free account).
  **Desktop is a supported app type.** Apps are private until moderated, with a **10-user cap**
  while unapproved; the FAQ claims 1–3 business days, which could not be confirmed as still true.
* **No scopes.** Access is all-or-nothing: a user has to grant full read/write on their account
  just to search. That is worth saying out loud in the settings panel.
* Search: `GET /search/{term}/?type=things` with `sort` in
  `relevant|text|popular|makes|newest`, `page`/`per_page`, and a `license` filter.
* Licence is a string *and* a dedicated `allows_derivatives` boolean, so ND is unambiguous.
  Sixteen licence values, including three CERN OHL variants added in April 2026.
  **Discrepancy:** the spec documents the share-alike filter id as `ccsa`; the live site sends
  `cc-sa`. Test both.
* Rate limit is **stated**: 300 requests per 5 minutes.
* **Operational trap, measured not documented:** `api.thingiverse.com` sits behind a Cloudflare
  managed challenge. With a default client user-agent, four requests in a burst returned HTTP 429
  with an HTML interstitial; with a browser user-agent the same requests returned clean JSON.
  So the adapter must send a realistic user-agent and treat an HTML body where JSON was expected
  as a challenge rather than a parse error.
* **The licence agreement is the important part.** Revised 19 February 2026. §2(a)(i)(C) licenses
  only *"limited intermediate copies of Content only as necessary"*; §4(b) requires deleting
  intermediate copies when no longer required and in any case after thirty days; §4(e)(v)
  prohibits *"copy or store the Content, other than for the intermediate purposes allowed"*;
  §4(e)(ix) forbids commingling API content with scraped data; §4(e)(x) forbids robots and
  scraping outright.

  **A persistent local library of downloaded Thingiverse files is not compatible with that.**
  Downloading into the open editing session and letting the *user* save where they choose is. This
  is a real constraint on the design, not a footnote, and it is why it has its own ADR.

## Printables — link import

* Verified absent: `api.printables.com/docs` (404), `developers.printables.com` (no DNS),
  `help.prusa3d.com/article/printables-api` (404). No developer link in the footer or nav.
* The site's own backend is `POST https://api.printables.com/graphql/` and answers **anonymously**.
  Introspection is disabled, so operations have to be mined from the site's own JavaScript bundles.
* It works today: an anonymous `searchPrints3` for "benchy" returned 3,789 results, and an
  anonymous `getDownloadLink` mutation returned a working file URL.
* **Licence data is better than Thingiverse's**: an explicit `disallowRemixing` boolean on the
  licence object and a separate `excludeCommercialUsage` per model. Twenty licences, and search
  can filter by licence id.
* Licence is **not** in search results — it is detail-only — though search can still filter on it.
* The Printables terms of service (effective 2022) were searched for `API`, `automat`, `robot`,
  `spider`, `scrap`, `crawl`, `bulk`, `cach`, `redistrib`, `data min`. **Every one returned
  nothing.** The terms are silent on APIs and automated access. So this is unsanctioned rather than
  prohibited.
* **It drifts.** `searchPrints2` has already been superseded by `searchPrints3` in the live
  bundles, and a real third-party client shipped a fix on 18 September 2026 for *"Printables
  GraphQL queries and mutation for API schema changes"*, with a test for provider 403s.

So: usable, unsanctioned, and will break. Worth having behind a clearly labelled switch with every
operation string pinned in one module, not worth pretending is an API.

## MakerWorld — file drop only

The instinct in `docs/07` was right, and the reason is stronger than "no API exists".

* No developer programme anywhere on any Bambu property. `developer.bambulab.com` does not
  resolve. A forum request for a public API opened January 2024 is still open with no staff answer,
  last bumped July 2026.
* The site's own backend *is* partially anonymous — search and design detail both answer without
  auth — but **the Bambu print profile requires an account**:
  `/api/v1/design-service/instance/{id}/f3mf` returns `403 "Please log in to download models."`
* **Terms of Use §9** (effective 18 June 2024), verbatim in the relevant part:

  > You may not use any "deep-link", "page-scrape", "robot", "spider" or other automatic device,
  > program, algorithm or methodology, **or by any means of artificial intelligence service**, or
  > any similar or equivalent manual process, to access, acquire, copy, reproduce, exploit or
  > monitor any portion of the site or any content…

  That clause is drafted about as directly at what a tool like this would do as a 2024 document
  could manage. There is also a "no competitive product" clause.

**Decision: ModelPop makes no network request to MakerWorld at all.** The user downloads the 3MF
themselves, as they already do, and drops it on the window. ModelPop reads the title, the print
profile and the plate settings out of the file, which is a file the user already has. That path is
also *better* than a link fetch would be: a downloaded MakerWorld 3MF carries the Bambu print
profile, which is the thing worth having.

## Thangs — dropped

* `developers.thangs.com`, `docs.thangs.com` and `api.thangs.com` all resolve to a wildcard CNAME
  with no valid certificate. They are not services.
* The real host is `production-api.thangs.com` and every endpoint returns 401. Auth is a user
  session JWT; there is no mechanism to issue a key to a third-party application.
* All 201 page routes were enumerated from the site's build manifest. There is no developer, API
  or key-management route.
* **Geometric search, the only reason it was on the list, appears to be gone.** The 2023 homepage
  advertised it in its own meta description; today's sells a marketplace. There is no file input
  anywhere in the current bundle, no "upload a model" or "geometric" string outside one dead
  no-results message, and a live model page carries `"matchingPhyndexerId": null` — the geometric
  index field survives with no value in it. The iOS listing updated 21 September 2026 does not
  mention it.
* The engine lives on at Physna, the parent company, whose API is genuinely good — part-to-part,
  part-in-part and photo-to-model matching. But every endpoint needs an Okta tenant, there is no
  self-serve signup, pricing is not published, and **matches are scoped to models you uploaded
  yourself, not to a public catalogue.** That is not the capability that made Thangs interesting.

Dropped from the roadmap. A user who already has a Thangs download on disk needs no Thangs-specific
code at all: it is just an STL.

---

## What this changes in the plan

1. `docs/07` listed Thangs as *"uniquely useful"* for geometric search. That is no longer true and
   the row should be struck.
2. Two of five sources are build-able, which is enough for a useful gallery. Both are sanctioned.
3. The Thingiverse caching restriction shapes the whole download path, so it gets an ADR.
4. Neither buildable source returns everything in search: Thingiverse's payload is unverified for
   licence completeness, and MyMiniFactory has no download count at all. The `Candidate` type
   already treats every field as optional, which turns out to be the right call.
