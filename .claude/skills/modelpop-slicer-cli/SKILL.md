---
name: modelpop-slicer-cli
description: Drive the Bambu Studio CLI to slice a model for a Bambu Lab P2S, and read its result.json telemetry. Use when slicing, generating G-code, enabling supports, auto-orienting or arranging, writing or debugging the Slicer adapter, or interpreting a slicer failure in ModelPop.
---

# Slicing via the Bambu Studio CLI

Verified against **Bambu Studio 02.08.02.61** on Windows 11, and now covered by
`tests/integration/test_slicing.py`. ModelPop does not implement slicing or support generation
(ADR-0006). The adapter is `modelpop.printing.bambu_slicer`.

## The invocation that works

```python
subprocess.run(
    [
        r"C:\Program Files\Bambu Studio\bambu-studio.exe",
        "--load-settings",
        f"{machine_profile};{process_profile}",  # ONE argument, semicolon-joined
        "--load-filaments",
        str(filament_profile),
        "--enable-support",
        "--support-type",
        "tree(auto)",
        "--orient",
        "1",
        "--arrange",
        "1",
        "--slice",
        "0",  # 0 = all plates, N = plate N
        "--export-3mf",
        "sliced.3mf",
        "--outputdir",
        str(output_dir),
        str(model_path),
    ],
    cwd=workdir,
    capture_output=True,
    timeout=900,
    check=False,
)
```

Profiles live under `C:\Program Files\Bambu Studio\resources\profiles\BBL\{machine,process,filament}`.

## Four traps that will cost you an afternoon

1. **No stdout, no stderr, no usable exit code.** It is a GUI-subsystem executable and returns 0
   almost immediately regardless of outcome. `result.json` is the *only* status.
2. **`result.json` is written to `--outputdir`**, alongside the G-code — not to the working
   directory. It falls back to the working directory only when no output directory is given.
   An earlier note here said the opposite; that was wrong, because the original spike used one
   folder for both and could not tell them apart. Read `--outputdir` first, fall back second.
3. **Pass arguments as a list, never a joined string.** Profile paths contain spaces and
   `--load-settings` takes the machine and process profiles joined by a semicolon as a *single*
   argument. Getting this wrong produces a misleading `return_code: -3`, "input files not found".
4. **Clean up the working directory.** A run leaves scratch data behind. Use a fresh
   `tempfile.mkdtemp()` per job and delete it in a `finally`.

## Reading result.json

```json
{ "return_code": 0, "error_string": "Success.",
  "layer_height": 0.2, "wall_loops": 2, "sparse_infill_density": 20.0,
  "sliced_plates": [{
     "total_predication": 648.3,
     "generate_support_material_time": 2,
     "filament_change_times": 0,
     "filaments": [{ "main_used_g": 0.0, "total_used_g": 0.0 }],
     "objects": [{ "name": "part.stl", "triangle_count": 24,
                   "bbox": { "width": 35.0, "depth": 20.0, "height": 20.0 } }],
     "feature_type_times": { "Outer wall": 113.2, "Bridge": 22.7, "Sparse infill": 276.4 },
     "warning_message": "" }] }
```

| Code | Meaning |
|---|---|
| `0` | success |
| `-3` | "input files not found" — almost always argument quoting, not a missing file |

Map every field into the readiness report:

- `total_predication` → predicted print time.
- `generate_support_material_time > 0` → supports actually ran.
- `objects[].bbox` → placement after auto-arrange; compare against the 256 mm envelope.
- `feature_type_times.Bridge` high on a supposedly simple model → bad orientation. Surface it.
- `warning_message` → **show verbatim.** Never swallow it.
- `total_used_g - main_used_g` → filament purged on tool changes, the "poop". This is the number
  behind the AMS-versus-multi-plate comparison in `docs/09-virtual-print.md`.

## P2S facts (read from the installed profiles)

- Build volume **256 × 256 × 256 mm**. Nozzle variants 0.2 / 0.4 / 0.6 / 0.8.
- Default process for the 0.4 nozzle: `0.20mm Standard @BBL P2S`.
- Support defaults in `process/fdm_process_common.json`: `enable_support: "0"`,
  `support_type: "tree(auto)"`, `support_style: "default"`, `support_threshold_angle: "30"`,
  `support_on_build_plate_only: "0"`, `support_top_z_distance: "0.2"`.
- Profiles use `inherits`, so a profile we author must inherit correctly or be fully resolved.

## Known open issue

Filament binding does not resolve: `filament_id` comes back empty and `main_used_g` / plate `weight`
are `0`, while print time is correct. **Do not report grams to the user until this is fixed.** The
AMS-versus-multi-plate waste comparison is blocked on it.

## Rules for the adapter

- Detect the slicer at start-up; fail with a clear, actionable message if absent. Never degrade silently.
- Return `Result[SliceReport]`; a non-zero `return_code` is an expected failure, not an exception.
- Keep `parse_result_json` separate from the subprocess call, so it can be tested against recorded
  payloads with no slicer installed. That is what keeps the fast suite fast.
- Keep every Bambu-specific detail behind the adapter so OrcaSlicer and PrusaSlicer can be added later.
