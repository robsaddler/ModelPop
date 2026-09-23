---
description: Create a numbered ADR from a title
---

Create a new Architecture Decision Record in `docs/adr/`.

1. Find the highest existing ADR number in `docs/adr/` and use the next one, zero-padded to 4 digits.
2. Name the file `NNNN-kebab-case-title.md`.
3. Use this structure exactly: a heading `# ADR-NNNN — <title>`, then `- Status:` and `- Date:` lines,
   then `## Context`, `## Decision`, `## Consequences`.
4. Context states the forces and the options actually considered, with licences where relevant.
   Decision is imperative and specific. Consequences state the costs honestly, not just the benefits,
   and name the escape hatch if the decision proves wrong.
5. Keep it under a page. An ADR nobody reads is worthless.

Title: $ARGUMENTS
