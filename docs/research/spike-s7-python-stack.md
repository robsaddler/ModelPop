# Spike S7 — Python-first stack — PASSES decisively

Run on this machine, 2026-09-23, on **Python 3.14.7** (the system install), in a plain venv.
This spike decides ADR-0007. It re-runs, in Python, the exact tests that C# failed.

## Headline: 12/12 on the CAD tests, including everything C# could not do

`build123d 0.13.0` + `cadquery-ocp 8.0.1.0.0` (OCCT 8.0.1), installed **as a binary wheel** —
no C++ compiler, no SDK, no build toolchain.

```
PASS  Box 20^3                        :: volume=8000.00
PASS  box - cylinder volume           :: 6429.20 vs 6429.20
PASS  fillet 4 vertical edges AT ONCE :: volume=7931.33, 4 ms
PASS  fillet ALL 12 edges at once     :: volume=7804.70, faces=26, 7 ms
PASS  sequential fillet (2 passes)    :: 7931.33 -> 7899.07, faces=26
PASS  chamfer 4 edges                 :: volume=7840.00
PASS  fillet on boolean result        :: 6429.20 -> 6377.70
PASS  export STEP                     :: 63,311 bytes
PASS  export STL                      :: 473,084 bytes
PASS  export 3MF natively             :: 128,681 bytes
PASS  STEP round-trip                 :: volume=7804.70
==== 12 passed, 0 failed ====
```

### Side by side with spike S1 (C# / CADability)

| Operation | C# / CADability 1.1.13 | Python / OCCT 8.0.1 |
|---|---|---|
| Boolean subtract | 6429.20 — exact | 6429.20 — exact |
| Fillet 1 edge | works | works |
| **Fillet 4 edges at once** | **null** | **7931.33 in 4 ms** |
| **Fillet all 12 edges** | **null** | **7804.70 in 7 ms** |
| **Fillet twice in sequence** | **NullReferenceException** | works |
| **Chamfer** | **null in every case** | 7840.00 — exact |
| 3MF export | hand-rolled zip required | **native** |
| API documentation | **none shipped** | full docs + type hints |

Every volume is analytically correct. Filleting four edges of a 20 mm cube at r=2 removes
(4 − π)·r²·h per edge = 68.67 mm³ total, giving 7931.33. Chamfering the same edges at 2 mm removes
4 × ½ × 2 × 2 × 20 = 160 mm³, giving 7840.00. Both match to the last digit.

## Viewport: PyVista 0.49.0 / VTK 9.7.0

| Check | Result |
|---|---|
| Build 983,040-triangle mesh | 187 ms |
| Windowed orbit at 983,040 triangles | **28–36 FPS** |
| Ray pick, cached `vtkCellLocator` | **0.0045 ms/pick**, exact hit at z=1.0000, returns cell id |
| Locator build (once, reused) | 111 ms |
| Clipping plane / section view | 507,904 cells, works |
| Screenshot shows real geometry | 67,317 non-background pixels |

**Caveat, stated plainly: VTK rendered on the Intel integrated GPU, not the RTX 4090.** Setting a
per-application `HKCU\...\DirectX\UserGpuPreferences` entry did not move it within this session (it
likely needs a sign-out). So 28–36 FPS is an **integrated-graphics** figure; the C# number of 55 FPS
was measured on the 4090 and the two are **not comparable**.

This is an open item, not a blocker:
- VTK is the standard scientific visualisation toolkit and routinely handles far larger datasets.
- Our own pipeline decimates to ~300k triangles (stage P8) before display anyway.
- Picking at 4.5 microseconds is far better than the C# path needed.

**Resolve it in Phase 1**: confirm the discrete GPU is used, via Windows Graphics Settings for the
project's `python.exe`, and re-measure.

## Gotchas worth keeping

1. **Windows path length breaks pip.** Installing into a venv under the deep scratchpad path failed
   with `OSError: [Errno 2] No such file or directory` on a long `jedi/third_party/typeshed/...` path.
   Keep the project and its venv at a short path, or enable long-path support.
2. `vtkOBBTree.IntersectWithLine(p1, p2, points, None)` **segfaults**. Use `vtkCellLocator` with the
   full argument list (`tol, t, x, pcoords, subId, cellId`) instead — it is also far faster.
3. `pyvista.Plotter.render()` in `off_screen` mode does not block, so naive frame timing reports
   absurd numbers (166,021 FPS in my first attempt). Measure in a real window with `update()`.
4. Python **3.14 works for the CAD stack** — `cadquery-ocp` ships cp314 wheels. The AI generation
   stack will still need its own 3.12 venv for PyTorch.

## Conclusion

The Python stack does everything the C# stack did, plus the things it could not do at all, with fewer
moving parts and no sidecar. **Adopt it — see ADR-0007.**

Probe sources: `C:\mp-spike\` (`fillet_test.py`, `viewport_window.py`, `pick_test.py`).
