# Research digest — AI to printable 3D, September 2026

Compiled 2026-09-23 from parallel research passes. Facts verified by direct fetch are unmarked;
anything uncertain is flagged. Local facts measured on this machine are in `spike-bambu-cli.md`.

---

## 1. The printability reality

Consistent across 3dprinting.com's 2026 guide, the Bambu community forum, and mesh-cleanup guides:
AI generators optimise for how a model **renders**, not how it **slices**. Recurring failures:

- non-manifold / not watertight; thousands of duplicate vertices; interior inverted faces
- arbitrary scale, no flat base, thin or zero-thickness shells
- fused limbs, invented or flat backs on single-image generations
- **the dominant complaint:** detail lives in the *texture*, not the geometry, so the print is smooth

Notable market signal: **Bambu retired PrintMon Maker and AI Scanner on 20 Sept 2026**, saying its
first-generation AI tools "no longer meet the community's increased expectations". The incumbent
withdrew rather than improve. *(Reported via search snippet; primary source was paywalled.)*

The only quantified print-pass number found anywhere is vendor-sourced: Meshy claims a 97% Bambu
Studio slicer pass rate on 75 figurines **after** its paid auto-repair, which discards textures and UVs.
**No independent printability benchmark exists.** Existing comparisons rank geometric fidelity, not
printability. That is an open niche and a good reason for our readiness score.

## 2. Two paradigms, cleanly separated

**Mechanical parts → LLM writes parametric CAD code, in a measure-and-correct loop.**

- Text2CAD-Bench (600 CadQuery tasks, May 2026): frontier models handle simple parts; 68–93% invalidity
  at the hardest tier. Claude degrades most gracefully on complex parts.
- BenchCAD: a 2 B fine-tuned model beat frontier models on CAD; notably, **adding images to edit
  instructions barely helped** — so prefer numeric, textual feedback for corrections.
- Tool-augmented loops are what actually move the needle: `build123d-mcp` reports CADGenBench
  0.360 → 0.457 and validity 88% → 100% *for the same model*.
- CADCodeVerify / EvoCAD: verify with a generated yes/no question catalogue over **4 orthogonal
  renders**. A local project (`textcad`) independently found an **orthographic contact sheet** beats
  isometric renders for vision-model judging.
- MUSE describes a "failure cascade": executable → valid B-rep → engineering-ready. Passing the first
  two says little about the third.

**Organic shapes → flow/diffusion mesh generation.** No competitive alternative.

Key reads: arxiv.org/abs/2605.18430 (Text2CAD-Bench), arxiv.org/html/2605.10865v1 (BenchCAD),
github.com/dbhavery/textcad, github.com/pzfreo/build123d-mcp, arxiv.org/html/2410.05340v2
(CADCodeVerify), github.com/huggingface/cadgenbench.

## 3. Local generators on a 16 GB RTX 4090 Laptop

| Model | Licence | VRAM | Watertight? |
|---|---|---|---|
| **TRELLIS.2-4B** (Microsoft) | **MIT** | 1024³ in ~16 GB with low-VRAM mode; 512³ in ~8 GB | **No** — O-Voxel models open surfaces by design. Repair is mandatory. |
| **Pixal3D** (TencentARC) | **MIT** | low-VRAM mode | inherits TRELLIS.2; adds multi-view with camera params |
| **TripoSG** (VAST) | **MIT** | ≥ 8 GB | yes — SDF/rectified flow, closed |
| **PartCrafter** | **MIT** | ≥ 8 GB | closed parts; good for multi-colour splitting |
| Hunyuan3D-2.x / 2.1 | Tencent community | 6–10 GB shape | yes (SDF + marching cubes) |
| Step1X-3D | Apache 2.0 | 27–29 GB full; geometry-only may fit | watertight TSDF |
| Direct3D-S2 | MIT | 10 GB @512 | closed |
| SF3D / SPAR3D | Stability community (free < $1 M) | < 8 GB | fast preview tier |
| PartPacker | **NVIDIA non-commercial** | ~10 GB | excluded on licence |

> **Licence trap that decides our default.** The Tencent Hunyuan community licence **excludes the EU,
> UK and South Korea**. You are UK-based, so the otherwise-obvious best-supported family is out.
> This single fact makes **TRELLIS.2 (MIT)** the primary and **TripoSG (MIT)** the fallback.
> See ADR-0004.

Every strong open model in 2026 is **image-conditioned**; text-to-3D is really text → image → 3D.
Windows support generally comes via ComfyUI wrapper nodes shipping prebuilt CUDA wheels, or WSL2.

## 4. Photos → metric mesh

- Classical photogrammetry (COLMAP + OpenMVS) remains the accuracy and reliability baseline. Feed-forward
  transformers win on very sparse input but "cannot fully replace traditional SfM and MVS".
- **Scale is never recovered by reconstruction.** It comes from a marker, a known object, or the user.
  An ArUco/ChArUco board gives sub-1% error and doubles as camera calibration.
- Licence watch: MASt3R, DUSt3R, Pi3 and VGGT-1B have **non-commercial** weights. MapAnything
  (Apache-2.0 weights variant), Depth Anything 3 base/metric (Apache), MoGe (MIT) and SAM 3 (commercial
  OK) are the usable ones.
- OpenCvSharp (Apache-2.0) has the ArUco/ChArUco API we need. **Emgu CV is GPL/commercial — avoid.**

## 5. .NET mesh libraries

The landscape changed twice this year, both in our favour:

- **ManifoldSharp 0.1.0 (15 Sept 2026)** — a pure-C# port of Manifold, **Apache-2.0, net10.0**,
  AOT-friendly, single dependency (Clipper2). Exact + robust boolean engines, hull, Minkowski,
  SDF→mesh, orientation repair, self-intersection detection. **One week old and pre-1.0** — pin the
  version and back it with a contract test suite.
- **geometry3Sharp** — Boost licence, pure managed. NuGet is from 2019 but the author resumed work
  (`dotnet8` branch, commits Jan 2026). Gives what Manifold lacks: `MeshAutoRepair`, hole fillers,
  isotropic remesh, quadric decimation, SDF grid + marching cubes for offset/hollow/voxel remesh.
- **PicoGK** (Apache-2.0, Aug 2026) — OpenVDB-backed voxel ops for offset/shell/hollow at scale.
- **MeshLib** is the most complete single library and is **not open source** — its GitHub licence is
  explicitly non-commercial. Excluded by your constraints.
- Avoid: CGAL/CGALDotNet (GPL), pymeshfix (AGPL), PyMeshLab (GPL), mcut (LGPL/commercial),
  `MeshDecimatorCore` (unlicensed re-upload), Triangle.NET (licence ambiguity).
- Supporting: **AssimpNetter** (MIT, Jun 2026) for import/export, **Clipper2** (Boost, 2.0.0) for 2D
  offsetting and CDT, **MeshDecimator** (MIT, archived) to vendor if needed.

## 6. .NET test and quality stack

- **Verify** 33.1.1 for snapshot testing. Note it adopted an **Open Source Maintenance Fee** from
  1 Sept 2026; a solo hobbyist qualifies for the `SmallRevenue` exemption, declared in
  `Directory.Build.props`, otherwise the build fails with SC021. Free and legitimate for you.
- Image diffs: **Verify.ImageMagick** (MIT, threshold comparers). Avoid Verify.ImageSharp (AGPL-3.0)
  and Verify.Phash (stale since 2023).
- **ArchUnitNET** 0.13.4 (Apache-2.0, Aug 2026) for layering rules. NetArchTest.Rules is dead (2021).
- **BenchmarkDotNet** 0.15.8. **Stryker.NET** 5.0.0 (Apache-2.0, requires the .NET 10 runtime — we have
  it). **coverlet** 10.0.1 + **ReportGenerator** 5.5.11 (Apache-2.0).
- Snapshot tests of geometry need **fixed rounding** in a custom converter and **tolerance comparers**
  for binary artefacts; render comparisons must be per-OS/GPU unique with a threshold.

## 7. Slicing and the printer

Proven locally — see `spike-bambu-cli.md`. Summary: do **not** implement support generation. No
embeddable open library exists; slicer tree-support code is not packaged for reuse. Generate support
**enforcer/blocker volumes** in the 3MF and let Bambu Studio produce the supports.

## 8. The competitive gap

Nobody currently fills: automatic scale and dimension assignment, orientation and base generation,
wall-thickness enforcement, texture→geometry detail transfer, or an unattended loop that judges
**printability** rather than looks. That list is ModelPop's product.
