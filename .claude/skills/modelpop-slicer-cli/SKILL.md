---
name: modelpop-slicer-cli
description: Drive the Bambu Studio CLI to slice a model for a Bambu Lab P2S, and read its result.json telemetry. Use when slicing, generating G-code, enabling supports, auto-orienting or arranging, writing or debugging the ISlicer adapter, or interpreting a slicer failure in ModelPop.
---

# Slicing via the Bambu Studio CLI

Verified against **Bambu Studio 02.08.02.61** on Windows 11, 2026-09-23. Everything here was measured,
not inferred. ModelPop does not implement slicing or support generation (ADR-0006).

## The invocation that works

```bash
"C:/Program Files/Bambu Studio/bambu-studio.exe" \
  --load-settings "<PROF>\machine\Bambu Lab P2S 0.4 nozzle.json;<PROF>\process\0.20mm Standard @BBL P2S.json" \
  --load-filaments "<PROF>\filament\Bambu PLA Basic @BBL P2S.json" \
  --enable-support --support-type "tree(auto)" \
  --orient 1 --arrange 1 --slice 0 \
  --export-3mf "sliced.3mf" --outputdir "<OUT>" "<IN>\model.3mf"
```

`<PROF>` = `C:\Program Files\Bambu Studio\resources\profiles\BBL`.
`--slice 0` slices all plates; `--slice N` slices plate N. `--orient`/`--arrange` take 0 or 1.

## Four traps that will cost you an afternoon

1. **No stdout, no stderr, no exit code.** It is a GUI-subsystem executable. `Process.ExitCode` comes
   back empty. The *only* status is `result.json`.
2. **`result.json` is written to the current working directory**, not `--outputdir`. Always run in a
   fresh throwaway directory and read it from there.
3. **Never build the argument string by hand.** Profile paths contain spaces and the semicolon-joined
   `--load-settings` pair is one argument. In C# use `ProcessStartInfo.ArgumentList` and add each
   element separately. (PowerShell's `Start-Process -ArgumentList` mangles these and produces a
   misleading `return_code: -3`.)
4. **`--load-settings` takes machine and process as one semicolon-separated argument**, not two flags.

## Reading result.json

```json
{ "return_code": 0, "error_string": "Success.",
  "layer_height": 0.2, "wall_loops": 2, "sparse_infill_density": 20.0,
  "total_predication": 648.3,
  "sliced_plates": [{
     "generate_support_material_time": 2,
     "objects": [{ "name": "test.stl", "triangle_count": 24,
                   "bbox": { "width": 35.0, "depth": 20.0, "height": 20.0, "x": 82.5, "y": 90.0, "z": 0.0 } }],
     "feature_type_times": { "Outer wall": 113.2, "Bridge": 22.7, "Sparse infill": 276.4, "...": 0 },
     "warning_message": "" }] }
```

| Code | Meaning |
|---|---|
| `0` | success |
| `-3` | "The input files to the slicer are not found" — almost always an argument-quoting bug, not a missing file |

Map every field into the readiness report:

- `total_predication` → predicted print time.
- `generate_support_material_time > 0` → supports actually ran (cross-check `slice_info.config`
  `support_used`).
- `objects[].bbox` → final placement after auto-arrange; compare against the 256 mm build volume.
- `feature_type_times.Bridge` high on a supposedly simple model → bad orientation. Surface it.
- `warning_message` → **show verbatim to the user.** Never swallow it.

## P2S facts (read from the installed profiles)

- Build volume **256 × 256 × 256 mm**. Nozzle variants 0.2 / 0.4 / 0.6 / 0.8.
- Default process for the 0.4 nozzle: `0.20mm Standard @BBL P2S`.
- Support defaults in `process/fdm_process_common.json`: `enable_support: "0"`,
  `support_type: "tree(auto)"`, `support_style: "default"`, `support_threshold_angle: "30"`,
  `support_on_build_plate_only: "0"`, `support_top_z_distance: "0.2"`.
- Profiles use `inherits` (`fdm_bbl_3dp_001_common`, `fdm_process_single_0.20`), so a profile we author
  must inherit correctly or be fully resolved.

## Known open issue

Filament binding did not fully resolve in the spike: `filament_id` came back empty and
`main_used_g` / plate `weight` were `0`, while print time was correct. Material estimates are therefore
unreliable until this is fixed. Do not report grams to the user until it is.

## Rules for the ISlicer adapter

- Detect the slicer at startup; fail with a clear, actionable message if absent. Never silently degrade.
- One throwaway working directory per job; delete it after reading `result.json` (runs leave hundreds
  of MB of temp data).
- Kill the process on cancellation; respect the `CancellationToken` end to end.
- Return `Result<SliceReport>`; a non-zero `return_code` is an expected failure, not an exception.
- Keep every Bambu-specific detail behind the adapter so OrcaSlicer and PrusaSlicer can be added later.
