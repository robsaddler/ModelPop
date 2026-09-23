# Spike: Bambu Studio CLI slicing — VERIFIED WORKING (2026-09-23)

Run locally against the installed **Bambu Studio 02.08.02.61**, `C:\Program Files\Bambu Studio\bambu-studio.exe`.
This de-risks the single most important external dependency in the project.

## Result: success

A 24-triangle test STL (20 mm cube with a 15 mm overhanging arm at z=15) sliced end to end,
producing `plate_1.gcode` (250 KB), `sliced.3mf` (67 KB) and a machine-readable `result.json`.

## The command that works

```bash
"C:/Program Files/Bambu Studio/bambu-studio.exe" \
  --load-settings "<PROF>\machine\Bambu Lab P2S 0.4 nozzle.json;<PROF>\process\0.20mm Standard @BBL P2S.json" \
  --load-filaments "<PROF>\filament\Bambu PLA Basic @BBL P2S.json" \
  --enable-support --support-type "tree(auto)" \
  --orient 1 --arrange 1 --slice 0 \
  --export-3mf "sliced.3mf" --outputdir "<OUT>" "<IN>\test.stl"
```

where `<PROF>` = `C:\Program Files\Bambu Studio\resources\profiles\BBL`.

### Critical gotchas found the hard way

1. **The process is GUI-subsystem: it writes nothing to stdout/stderr.** All status comes back in
   `result.json`, written to the **current working directory** (not `--outputdir`). Always run it in a
   throwaway working directory and read `result.json` from there.
2. **It never sets a process exit code** that PowerShell can read (`$p.ExitCode` was empty every time).
   `result.json.return_code` is the only reliable status. `0` = success, `-3` = input files not found.
3. **PowerShell `Start-Process -ArgumentList` silently mangles arguments containing spaces**, which
   produced a misleading `return_code: -3`. The profile paths all contain spaces. Invoke the exe
   directly with proper quoting (the Bash tool worked first time). In C#, use
   `ProcessStartInfo.ArgumentList` (which quotes each element correctly) and never a joined string.
4. `--load-settings` takes a **semicolon-separated** machine;process pair in one argument.
5. Filament binding did not fully take: `filament_id` came back empty and `main_used_g` / plate `weight`
   were `0`. Print time was correct. **Open question for Phase 1** — probably needs the filament profile
   to match the machine variant, or a resolved profile rather than an inheriting one.

## What `result.json` gives us (free telemetry for the readiness score)

| Field | Value in the test run |
|---|---|
| `return_code` / `error_string` | `0` / `"Success."` |
| `total_predication` | 648 s predicted print time |
| `layer_height` | 0.2 |
| `wall_loops`, `sparse_infill_density` | 2, 20.0 |
| `generate_support_material_time` | 2 s (confirms supports actually ran) |
| `objects[].bbox` | width/depth/height + x/y/z placement after auto-arrange |
| `objects[].triangle_count` | 24 |
| `feature_type_times` | per-feature seconds: outer wall, bridge, sparse infill, … |
| `warning_message` | empty here; **surface this to the user verbatim** |

`feature_type_times.Bridge` and `Undefined` are useful printability signals: a lot of bridge time on a
model the user thinks is simple means bad orientation.

## Verified P2S facts (read from the installed profiles, not the web)

- Build volume **256 × 256 × 256 mm** (`printable_area` 0x0→256x256, `printable_height: 256`).
- Nozzle variants shipped: 0.2 / 0.4 / 0.6 / 0.8; default process for 0.4 is `0.20mm Standard @BBL P2S`.
- Support defaults in `process/fdm_process_common.json`:
  `enable_support: "0"`, `support_type: "tree(auto)"`, `support_style: "default"`,
  `support_threshold_angle: "30"`, `support_on_build_plate_only: "0"`, `support_top_z_distance: "0.2"`.
- Profiles use `inherits` (e.g. `fdm_bbl_3dp_001_common`, `fdm_process_single_0.20`), so any profile we
  write ourselves must either inherit correctly or be **fully resolved**.

## Verified structure of a Bambu-written 3MF

```
3D/3dmodel.model                     3D/Objects/object_1.model
3D/_rels/3dmodel.model.rels          Metadata/_rels/model_settings.config.rels
Metadata/model_settings.config       <- per-object settings, <object id=..><metadata key=.. value=../>
Metadata/project_settings.config     <- fully resolved flat settings
Metadata/slice_info.config           <- prediction, weight, support_used, per-plate
Metadata/plate_1.gcode  + .md5       Metadata/plate_1.json
Metadata/plate_1.png, plate_1_small.png, plate_no_light_1.png, top_1.png, pick_1.png
Metadata/cut_information.xml         Metadata/filament_sequence.json
[Content_Types].xml                  _rels/.rels
```

- Per-object overrides in `model_settings.config` are **per-extruder-variant comma-joined strings**
  (`value="50,50,50"`), not scalars. Our writer must honour that.
- `slice_info.config` confirmed `support_used="true"` and `prediction="648"`.
- Because these Bambu-specific sidecars are not part of the 3MF core spec, a generic 3MF writer will
  drop them. **Write the Bambu project 3MF ourselves** (it is just a zip) rather than via a generic lib.

## Consequence for the architecture

The "app prepares a clean, oriented, scaled project 3MF → Bambu Studio CLI slices it" pattern is
**proven on this machine**. We do not need to implement support generation. Keep slicing behind an
`ISlicer` port so OrcaSlicer/PrusaSlicer can be added later.
