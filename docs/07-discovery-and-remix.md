# Pipeline D — discover and remix

> "I want an MSI dragon, about 6 inches tall, in black with a hollow core and MSI in grey across the
> front." Find me some good starting points before generating anything from scratch.

This is the right instinct and it should be the **first** thing the app tries for most requests.
Starting from a good human-made model and modifying it beats generating from scratch on quality almost
every time, and it is far cheaper in GPU time and credits.

## Flow

```
prompt ──► IntentRouter ──► extract: subject, style, size, colours, features
                                        │
                                        ▼
                       IModelRepository.SearchAsync (parallel, all sources)
                                        │
                        rank: relevance × licence × printability × popularity
                                        │
                                        ▼
                            GALLERY VIEW  ── thumbnails, licence badge,
                                              size, triangle count, remixability
                                        │
                     user picks one ──► import ──► print-prep ──► CAD/prompt editing
                     nothing fits   ──► fall through to Pipeline A or B (generate)
```

The gallery is not a dead end: every card has **"use as a base"** and **"generate something like this"**.
A rejected search still improves the generation prompt, because the user has just told us what they
did not mean.

## Licensing — inform, don't police

Modifying someone's model is creating a **derivative work**, and a fair share of models on these sites
carry **ND (No Derivatives)** terms. The app's job is to make that visible, not to enforce it.

**Design decision: a one-time acceptance clause, then get out of the way.**

- On first use of the discovery feature, show a short, clearly worded clause and require a tick.
  Store the acceptance with a timestamp and the clause version; re-prompt only if the wording changes.
- After that, **nothing is blocked.** Every gallery card shows a licence badge and the required
  attribution, because that is genuinely useful information, but the user decides what to do with it.
- Attribution and source URL are carried in the document and written into the exported 3MF metadata,
  so provenance survives a remix without the user having to remember.

Draft clause, to be refined before it ships:

> Models found through ModelPop are published by third parties under their own licences. Some licences
> permit modification and sharing; others, including any marked **ND (No Derivatives)**, do not.
> Brand, character and franchise designs may also be protected by trademark or copyright regardless of
> the file's licence. ModelPop shows you the licence it was given, but it does not verify it and it
> cannot give legal advice. **You are responsible for ensuring your use of any model — printing,
> modifying, sharing or selling — complies with its licence and with applicable law.**
>
> ☐ I understand and accept responsibility for my use of third-party models.

## Sources

Ranked by how usable their access is. **All API details below need verifying in a Phase 7 spike** —
treat this table as a research starting point, not established fact.

| Source | Access | Notes |
|---|---|---|
| **Thingiverse** | Documented public REST API with app tokens | The most clearly sanctioned. Older catalogue, mixed quality. |
| **Printables** | API exists and is used by community tools; terms need checking | Good quality, strong licence metadata. |
| **MakerWorld** | No documented public API found. Bambu-native, best P2S-ready content, rich print profiles. | **Verify before building.** If there is no sanctioned API, do not scrape — integrate by letting the user paste a link or drop a downloaded 3MF, which also preserves Bambu print profiles. |
| **Thangs** | Has an API; notable for **geometric** search (find models by shape, not just text) | Geometric search is uniquely useful for "find me something shaped like this". |
| **MyMiniFactory** | Has an API | Curated, strongly print-focused. |

**Rule: honour robots.txt and each site's terms. No scraping.** If a source has no sanctioned API, the
integration is a link/file import, not a crawler. This is both the legal answer and the one that will
not break silently.

## Ranking

Relevance alone produces a bad gallery. Rank on four axes and show why each result scored:

1. **Relevance** — text match, plus optional geometric similarity where the source supports it.
2. **Remixability** — for a request that clearly implies editing, a permissively licensed model ranks
   above an ND one. A ranking nudge, not a block.
3. **Printability signal** — has print profiles, has real print photos, reported success rate,
   triangle count sane, single watertight object.
4. **Popularity** — downloads and likes, dampened so a viral low-quality model does not dominate.

## Requirements this unlocks

The example request maps onto features the rest of the app already needs, which is a good sign:

| From the prompt | Feature |
|---|---|
| "about 6 inches tall" | scale to a stated dimension — already stage P3 |
| "hollow core" | hollowing with drain holes — already stage P7 |
| "in black … MSI in grey across the front" | multi-colour part splitting for the AMS — already stage P9 |
| "MSI across his front" | text-on-surface: emboss/deboss a string onto a picked face. **New CAD command**, needed in Phase 5 anyway |
| "find me a base" | this pipeline |

`ITextOnSurface` (project text onto a face, emboss or deboss by depth) is worth adding explicitly to
the Phase 5 command list. It is one of the most-wanted edits for printed models and it is a natural fit
for prompt-driven editing.

## Where this lands

New **Phase 2.5**, straight after the print pipeline works and before generation. It is the cheapest
path to a genuinely useful app: at that point ModelPop can find, prepare and print an existing model
end to end, with no GPU and no AI credits spent.
