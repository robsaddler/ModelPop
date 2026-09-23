# ADR-0003 — Avalonia shell with HelixToolkit v3; WinUI as the sanctioned second target

> **SUPERSEDED by ADR-0007 (Python-first).** Kept for the reasoning and for spike S3's measured Avalonia/HelixToolkit findings. The viewport is now PyVista/VTK.

- Status: **Accepted and validated by spike S3** (supersedes the provisional WPF + Helix v2 draft)
- Date: 2026-09-23

> **Spike S3 result (`docs/research/spike-s3-avalonia-helix.md`): PASS, with Avalonia pinned to 11.3.22.**
> Measured on this machine: 1,015,808 triangles at **54.9 FPS** orbiting, with **pixel-accurate picking**
> and a real D3D11 device. **Avalonia 12.1.3 fails** at template application with
> `MissingMethodException: 'Avalonia.Data.IBinding Avalonia.Data.TemplateBinding.ProvideValue()'`,
> because Helix 3.1.2's pre-compiled XAML targets Avalonia 11. The version gap predicted below is real;
> the mitigation (pin 11.x) works and costs little, since Avalonia 12 shipped 2026-09-22 and its own
> ecosystem has not caught up.

## Context

We need a Windows desktop shell and a CAD-grade 3D viewport: orbit/pan/zoom, picking of faces, edges
and vertices, section and clipping planes, transform gizmos, a build-plate and 256 mm print-volume
overlay, a 2D HUD, and smooth handling of meshes in the millions of triangles. Free and open source
only, so devDept Eyeshot and every other commercial component is excluded.

HelixToolkit is the only free .NET library that ships a CAD-style viewport as a **finished component**
— hit-testing, manipulators, cutting planes, camera controls, exporters — rather than a rendering API
you build one on top of. So the viewport choice forces the shell choice, not the other way round.

Verified on nuget.org, 2026-09-23:

| Package | Latest | Published | Licence | Notes |
|---|---|---|---|---|
| `Avalonia` | **12.1.3** | **2026-09-22** | MIT | native **net10.0** target |
| `Avalonia.Headless` / `.Headless.XUnit` | 12.1.3 | 2026-09-22 | MIT | in-process UI testing |
| `HelixToolkit.SharpDX` (v3 core) | **3.1.2** | 2025-11-25 | MIT | net48, net6.0, net8.0, netstandard2.0 |
| `HelixToolkit.Avalonia.SharpDX` (v3) | **3.1.2** | 2025-11-25 | MIT | **depends on Avalonia 11.2.0** |
| `HelixToolkit.WinUI.SharpDX` (v3) | 3.1.2 | 2025-11-25 | MIT | same core |
| `Dock.Avalonia` | 12.1.0.6 | 2026-08-27 | MIT | docking layout |
| `FluentAvaloniaUI` | 3.1.0 | current | MIT | Fluent controls/theming |
| `Semi.Avalonia` | 12.1.0.1 | current | MIT | alternative theme |
| `CommunityToolkit.Mvvm` | 8.4.2 | current | MIT | works with Avalonia |
| `HelixToolkit.*.Core.Wpf` (v2) | 2.27.3 | 2025-08-27 | MIT | WPF only, **maintenance-only** per its README |
| `SharpDX.Direct3D11` | 4.2.0 | **abandoned 2019** | MIT | dependency of **both** Helix lines |

The v2 line is mature and complete but explicitly maintenance-only, and has no successor for WPF.
The v3 line is where active development happens, and it supports Avalonia and WinUI but **not WPF**.

## Decision

- **Shell: Avalonia 12** on .NET 10, with `CommunityToolkit.Mvvm`, `Dock.Avalonia` for layout and
  FluentAvalonia for theming.
- **Viewport: `HelixToolkit.Avalonia.SharpDX` 3.1.2** (the v3 line) behind our own `IViewport` port.
- **WinUI is the sanctioned second target, not a parallel build.** Because v3 ships
  `HelixToolkit.WinUI.SharpDX` against the same platform-agnostic core, a WinUI shell remains a
  supported option. We build **one** shell — Avalonia — and keep all view-models, services and the
  viewport abstraction framework-agnostic so a WinUI head is additive work rather than a rewrite.
  Maintaining two shells simultaneously is not worth a solo developer's time and is explicitly not
  the plan.

Rationale: we take the line with a future. v3 is actively developed; v2 is a dead end for WPF. Avalonia
12 natively targets net10.0, released yesterday, and brings `Avalonia.Headless` — which lets UI
view-model and interaction tests run **in-process** rather than through an external automation driver.
Given how central the testing standard is to this project (`04-engineering-standards.md`), that is a
real and recurring benefit, not a nice-to-have. Cross-platform capability is a side effect we are not
designing for, but it is no longer foreclosed.

## Consequences

**Good.**
- Actively developed rendering line, so bug fixes and new platforms keep arriving.
- Native net10.0, modern XAML, and a healthy free ecosystem for docking and theming.
- `Avalonia.Headless.XUnit` makes UI testing part of the fast suite instead of a separate, flaky tier.
- CADability's core is `netstandard2.0` and separable from its WinForms UI, so ADR-0002 is unaffected.
- A WinUI head later is additive, because the same Helix core backs it.

**Cost and risk.**
- **The version gap is the real risk. `HelixToolkit.Avalonia.SharpDX` 3.1.2 declares a dependency on
  Avalonia 11.2.0, while current Avalonia is 12.1.3 — a major version apart.** NuGet will happily
  resolve upward, but Avalonia 11 → 12 is a breaking major, so the integration may not compile or may
  misbehave at runtime. **This must be proven before any UI work begins.**
- Helix's Avalonia integration is younger and less battle-tested than the WPF v2 line it replaces.
  Expect rougher edges in picking, manipulators and clipping planes, and budget time to contribute
  fixes upstream or carry patches.
- Both Helix lines still depend on **SharpDX 4.2.0, abandoned since 2019**. `Vortice.Windows` 3.8.3 is
  the maintained successor but Helix has not moved. Shared risk with the rejected option, not a
  regression.
- Avalonia's ecosystem is smaller than WPF's and there is less Stack Overflow history to lean on.
- No drop-in free gizmo library exists for .NET (`Hexa.NET.ImGuizmo` is prerelease only; ImGui-based
  gizmos are immediate-mode overlays needing their own render pass), so Helix's built-in manipulators
  carry more weight here than they appear to.

**Resolved by spike S3.** Avalonia 12 fails; Avalonia **11.3.22** passes every gate. We pin 11.3.22 and
treat the move to Avalonia 12 as a dependency-upgrade task, unblocked once Helix rebuilds against it.

Two standing obligations that came out of the spike:

1. **Merge `avares://HelixToolkit.Avalonia.SharpDX/Styles/Generic.axaml` into `Application.Resources`.**
   Without the control template the viewport renders **nothing, silently** — no exception, null device,
   zero frames, correct layout bounds. This cost the most time in the spike and would cost more later.
2. **Add a smoke test asserting `OnRendered` fires at least once**, and always subscribe to
   `RenderExceptionOccurred`, so a missing template or a broken render path can never regress silently.

If Helix's Avalonia integration later fails us, the fallback remains a hand-written viewport on
Silk.NET 2.23.0 behind the same `IViewport` port.
