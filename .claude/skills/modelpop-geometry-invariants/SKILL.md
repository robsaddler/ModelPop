---
name: modelpop-geometry-invariants
description: How to test geometry code in ModelPop - the property-based invariants every mesh operation must satisfy, tolerance and rounding conventions, golden-mesh snapshot rules, and the degenerate inputs that break kernels. Use when writing or reviewing mesh operations, boolean/repair/decimate code, IMeshOps adapters, or any geometry test.
---

# Testing geometry in ModelPop

Geometry fails **silently**. A subtly non-manifold mesh slices fine until it doesn't, and a boolean
regression surfaces as a failed print three days later. Example-based tests barely probe this.
Property-based tests plus hard gates are the only real defence.

## The invariants

Every one of these is a property test over generated meshes, not a single example.

### Boolean operations
- `union(a, b)` of two watertight solids is watertight. **No exceptions.**
- `volume(union(a,b)) <= volume(a) + volume(b)` and `>= max(volume(a), volume(b))`
- `union(a, a) ≡ a` — idempotent
- `union(a, b) ≡ union(b, a)` — commutative
- `difference(a, a)` is empty
- `intersection(a, b) ⊆ a` and `⊆ b`
- disjoint inputs: `volume(union) == volume(a) + volume(b)` within tolerance

### Repair
- the output of a successful repair is **manifold and watertight**, always
- repair either succeeds and produces a valid mesh, or reports failure — it must never return
  "succeeded" while leaving the mesh broken. This is the single most important assertion in the suite.
- repairing an already-valid mesh is a near-identity (volume within tolerance)

### Transforms
- scale by `s` then by `1/s` returns the original within floating-point tolerance
- translation does not change volume, surface area, or triangle count
- rotation does not change volume or surface area
- transforms compose: `apply(A, apply(B, m)) ≡ apply(A*B, m)`

### Decimation
- triangle count decreases monotonically with a decreasing target
- volume changes by less than the silhouette tolerance
- decimating to 90% then 80% ≈ decimating to 80% directly, within tolerance
- a watertight input stays watertight

### Commands (the document, not the mesh)
- every command has an inverse: `apply(cmd)` then `undo(cmd)` restores the document content hash
- replaying the full history from empty reproduces the same document hash
- `redo(undo(x)) ≡ x`

## Tolerances

Pick one convention and hold it everywhere:

| Quantity | Tolerance |
|---|---|
| vertex merge | 1e-4 mm |
| geometric comparison | 1e-6 relative |
| volume comparison after a lossy op | 0.5% |
| snapshot serialisation | 6 decimal places, invariant culture |

Never compare floats with `==`. Never use a tolerance loose enough to hide a real regression — if a
test needs 5% slack to pass, the operation is wrong, not the test.

## Degenerate inputs that break kernels

Include every one of these as a named case in the suite. They are what real downloads and real
generator output actually contain:

- zero-area and sliver triangles
- duplicate and near-duplicate vertices
- inverted normals, and mixed winding within one mesh
- open boundaries (a hole), and a mesh that is two disconnected shells
- self-intersecting geometry
- a non-manifold edge shared by three or more faces
- coincident faces (two solids sharing an exact face — the classic boolean killer)
- NaN and infinite coordinates
- enormous and microscopic scale (a 0.001 mm feature; a 10,000 mm model)
- an empty mesh, and a single triangle

Property-based shrinking will hand you the minimal mesh that breaks a kernel. That result is worth more
than any number of hand-written cases — when it finds one, add it as a named regression case.

## Golden-mesh snapshots

- Serialise geometry through a **custom converter with fixed rounding** (invariant culture, 6 dp).
  Raw floats make snapshots machine-dependent and the suite becomes noise.
- Keep the binary STL/3MF as the verified artefact and compare it with a **tolerance comparer**, not
  byte-exactly.
- Render comparisons use a threshold and are marked unique per OS and GPU. A byte-exact assertion on a
  GPU render is a test that fails forever for the wrong reason.
- **Never accept a snapshot you have not looked at.** An unread accepted snapshot is worse than no test.

## Contract tests for IMeshOps

Every `IMeshOps` implementation runs the same suite (`MeshOpsContractTests<T>`). This is the
Liskov check: swapping ManifoldSharp for PicoGK must not silently change behaviour. When adding an
adapter, the suite runs unchanged — if it needs relaxing, the abstraction is wrong.

ManifoldSharp is pre-1.0. Pin the exact version; the contract suite is what makes that safe.

## Performance

Benchmark the operations that decide whether the app feels fast: boolean on a 500 k-triangle mesh,
decimation, SDF thickness sampling, STL/3MF read and write, GPU upload. Record results in
`docs/benchmarks/`. Measure before optimising, always.
