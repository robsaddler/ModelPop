# ADR-0006 — We do not implement slicing or support generation

- Status: Accepted
- Date: 2026-09-23

## Context

The app must produce printable G-code with supports. Tree and organic support generation is thousands
of lines of heavily tuned code inside existing slicers, and none of it is packaged as an embeddable
library. Re-implementing it would consume the entire project budget and still be worse.

Bambu Studio 2.8 is installed and its CLI was verified working on this machine
(`docs/research/spike-bambu-cli.md`): a test STL sliced to G-code with tree supports, returning
structured telemetry.

## Decision

ModelPop prepares a **clean, repaired, scaled, oriented Bambu project 3MF** including any support
**enforcer and blocker** volumes as extra parts, then invokes the Bambu Studio CLI to slice it.

Slicing sits behind `ISlicer` so OrcaSlicer and PrusaSlicer can be added later without touching the
pipeline.

We **do** write the Bambu project 3MF ourselves, because generic 3MF libraries drop the Bambu-specific
sidecar files (`model_settings.config`, `project_settings.config`) that carry per-object settings. It
is a zip with known contents; the spike documents the layout.

## Consequences

**Good.** Support quality equals Bambu Studio's, for free. We get slicer telemetry — predicted time,
per-feature seconds, warnings — to feed the readiness score. Effort goes into the parts that are
actually differentiating.

**Cost.** A hard dependency on an installed slicer, detected at startup with a clear message if absent.
The CLI is GUI-subsystem: it writes no stdout and sets no usable exit code, so all status comes from
`result.json` in the working directory. Argument quoting must use `ProcessStartInfo.ArgumentList`.

**Open question.** Filament binding did not fully resolve in the spike (weight came back as 0), so
material estimates need a fix in Phase 2.
