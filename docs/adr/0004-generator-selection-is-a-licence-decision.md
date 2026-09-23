# ADR-0004 — Primary local generator is TRELLIS.2; Hunyuan3D is excluded

- Status: Accepted
- Date: 2026-09-23

## Context

The best-supported open image-to-3D family in 2026 is Tencent's Hunyuan3D: mature, modest VRAM, ships
its own REST server, large Windows community, and produces closed SDF-derived meshes that need little
repair. It would be the obvious first integration.

Its licence is the Tencent Hunyuan Community License, which **excludes the EU, UK and South Korea**.
The developer and primary user of ModelPop is UK-based.

The project constraint is that everything must be open source and free.

## Decision

- **Primary:** TRELLIS.2-4B (MIT), with Pixal3D (MIT) for multi-view conditioning.
- **Fallback / lightweight tier:** TripoSG (MIT).
- **Part-aware generation:** PartCrafter (MIT) for multi-colour and multi-piece splitting.
- **Excluded:** the entire Hunyuan3D family (territorial licence restriction), PartPacker (NVIDIA
  non-commercial), and anything else whose weights are non-commercial.

Licence is checked **before** capability for every model and library we adopt, and recorded here.

## Consequences

**Cost.** TRELLIS.2 models open surfaces by design, so its output is **not reliably watertight**. The
repair gate in the print-prep pipeline becomes mandatory rather than defensive. It is also officially
Linux-only, so Windows runs via ComfyUI wrapper wheels or a WSL2 container.

**Good.** MIT throughout means no territorial or commercial restriction ever has to be revisited, and
the app could be shared publicly without a licensing problem.

**Mitigation.** `IModelGenerator` keeps generator choice a runtime decision. If Tencent relicenses, or
a better MIT model appears, it is a new adapter and a config change.
