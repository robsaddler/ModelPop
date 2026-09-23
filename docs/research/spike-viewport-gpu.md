# Spike — which GPU the viewport gets, and whether it matters — CLOSED, no action needed

Run on this machine, 2026-09-24, in a **real on-screen window**. This closes the open Phase 1 task
in `docs/00-plan.md`: *"get VTK onto the RTX 4090 and re-measure (spike S7 ran on the integrated
GPU)"*.

It closes it with an answer nobody expected: **the discrete GPU cannot be forced from inside the
application, and it does not need to be.**

## What the viewport actually renders on

```
OpenGL vendor string:    Intel
OpenGL renderer string:  Intel(R) RaptorLake-S Mobile Graphics Controller
OpenGL version string:   4.5.0 - Build 31.0.101.5081
```

Unchanged from spike S7. The RTX 4090 is present, the driver is current, and OpenGL still lands on
the integrated graphics.

## Forcing the discrete GPU: tried, and it does not work from here

**Windows per-application GPU preference** — `HKCU\Software\Microsoft\DirectX\UserGpuPreferences`,
with `GpuPreference=2;` against the interpreter's full path. This is the exact mechanism Windows'
own *Settings → Display → Graphics* page writes. Set for both `python.exe` and `pythonw.exe` in the
project venv, then re-measured in a fresh process:

```
OpenGL renderer string:  Intel(R) RaptorLake-S Mobile Graphics Controller
```

No change. The setting was removed again; the registry is exactly as it was found.

**Why it does not work.** The Windows preference governs the Direct3D adapter. On an Optimus laptop
the OpenGL context provider is chosen by the NVIDIA driver's own profile system, which honours
either the `NvOptimusEnablement` symbol **exported by the executable** — `python.exe` does not
export it and cannot be made to — or a per-application profile in the NVIDIA Control Panel.

So the only route is the user, by hand, in the NVIDIA Control Panel: *Manage 3D settings → Program
Settings → add the interpreter → High-performance NVIDIA processor*. Which is worth knowing, and is
**not worth doing**, because of the next section.

## Whether it matters: no

Frame rates measured with `update()` in a real 1280x800 window, sixty frames of continuous orbit.
Not `render()` off-screen — that does not block, and once reported 166,021 FPS.

| Triangles | FPS (Intel iGPU) |
|---|---|
| 393,216 | **70 – 96** |
| 1,572,864 | **50 – 53** |

Spike S7 reported 28–36 FPS at 983k triangles and called it a floor. It was: the same hardware now
sustains **50 FPS at 1.57 million triangles**, which is five times the triangle budget the app
actually displays.

The readiness rules decimate for display at `DEFAULT_TRIANGLE_BUDGET` — around 300k — and at that
size the integrated GPU is delivering 70 to 96 FPS. There is no interactivity problem to solve.

## Conclusion

1. **Do not spend more time forcing the discrete GPU.** It cannot be done from inside the
   application, and the measurement says there is nothing to gain.
2. **Do tell the user which GPU is in use.** A person seeing a slow viewport should not have to
   guess; `ViewportScene.describe_renderer()` reports it, and the settings panel shows it.
3. The RTX 4090 is not idle in this application. It is what `trellis.cpp` runs on, which is the
   workload that actually needs 16 GB of it — see ADR-0010.
4. Re-measure only if the display budget rises above a million triangles, or if somebody reports a
   viewport that genuinely stutters.
