# Spike S3 — Avalonia + HelixToolkit v3 viewport — PASSES, but pin Avalonia 11.x

Run on this machine (RTX 4090 Laptop 16 GB, i9-14900HX, Windows 11), 2026-09-23.
Gate for ADR-0003. **Verdict: the architecture holds. Pin Avalonia 11.3.22, not 12.x.**

## Results

| Configuration | Result |
|---|---|
| Avalonia **12.1.3** + Helix 3.1.2 | **FAIL** — `MissingMethodException` when the control template is applied |
| Avalonia **11.2.0** + Helix 3.1.2 | PASS — 1,015,808 triangles, 55.6 FPS, accurate picking |
| Avalonia **11.3.22** (latest 11.x) + Helix 3.1.2 | **PASS** — 54.9 FPS, identical accuracy. **This is what we pin.** |

Measured on the passing configuration:

```
MESH   : 549,120 verts, 1,015,808 triangles, built 57 ms
DEVICE : Device1                                   (D3D11 device created)
ORBIT  : 275 GPU frames / 5.0s = 54.9 FPS  @ 1,015,808 triangles
PICK   : 1 hit(s) -> MeshGeometryModel3D at <15.400001, 15.400001, 1.0000038>, dist 58.99
RESULT : PASS (rendered, no exceptions)
```

Picking is exact: the target sphere sits at (15.4, 15.4, 0) with radius 1.0, and the reported hit is
its front surface at z = 1.0000038. Sub-millimetre accuracy on a million-triangle scene.

~55 FPS is a vsync-capped 60, not a ceiling — frames are counted from Helix's own `OnRendered`, and the
orbit loop invalidates on a 1 ms dispatcher timer at render priority.

## The Avalonia 12 failure, precisely

```
STYLES : Generic.axaml merged OK
MESH   : 549,120 verts, 1,015,808 triangles, built 63 ms
FATAL MissingMethodException: Method not found:
       'Avalonia.Data.IBinding Avalonia.Data.TemplateBinding.ProvideValue()'.
```

Helix 3.1.2 is compiled against **Avalonia 11.2.0.0** assemblies. `TemplateBinding.ProvideValue()`
changed signature in Avalonia 12, so Helix's **pre-compiled** XAML template fails the moment it is
applied. Nothing we can do from our side: it needs a Helix rebuild against Avalonia 12.

Note the failure mode. The package *restores*, *compiles* and *type-loads* fine against Avalonia 12 —
`Avalonia.Base 12.1.3.0` binds by roll-forward without complaint. It only dies at template application.
**A restore-and-build check would have passed and told us nothing.**

Context: Avalonia 12.1.3 was released 2026-09-22, the day before this spike, and the ecosystem has not
caught up — `Avalonia.Diagnostics` has no 12.x at all (latest 11.3.22). Pinning the 11.x line gives a
coherent dependency graph, not just a working one.

## The trap that cost the most time

**A `Viewport3DX` with no control template renders nothing, silently.** No exception, no warning.
`RenderHost.Device` is null, `OnRendered` never fires, zero frames — but the control lays out with
correct `Bounds` and the app exits cleanly reporting success.

The template lives in the package and **must be merged into `Application.Resources` by hand**:

```xml
<Application.Resources>
  <ResourceDictionary>
    <ResourceDictionary.MergedDictionaries>
      <ResourceInclude Source="avares://HelixToolkit.Avalonia.SharpDX/Styles/Generic.axaml" />
    </ResourceDictionary.MergedDictionaries>
  </ResourceDictionary>
</Application.Resources>
```

or in C#:

```csharp
Resources.MergedDictionaries.Add(new ResourceInclude(new Uri("avares://YourApp/"))
{
    Source = new Uri("avares://HelixToolkit.Avalonia.SharpDX/Styles/Generic.axaml")
});
```

I initially mis-diagnosed this as an Avalonia 11/12 incompatibility. The tell was that **Avalonia 11
failed identically** — if both versions fail the same way, the harness is wrong, not the library.
Worth remembering as a debugging heuristic.

## Package graph

Referencing Helix 3.1.2 drags in Avalonia satellites pinned at 11.2.0 while the core resolves to
whatever you asked for, producing a mixed graph. Pin the whole family explicitly:

```
Avalonia, Avalonia.Desktop, Avalonia.Themes.Fluent, Avalonia.Themes.Simple,
Avalonia.Controls.ColorPicker, Avalonia.Controls.DataGrid, Avalonia.Diagnostics  -> 11.3.22
Avalonia.Labs.CommandManager -> 11.3.x
HelixToolkit.Avalonia.SharpDX -> 3.1.2
```

`Avalonia.BuildServices` versions independently and can be ignored.

## API surface confirmed present

`Viewport3DX`, `PerspectiveCamera` / `OrthographicCamera`, `CameraController`,
`MeshGeometryModel3D`, `BatchedMeshGeometryModel3D`, `InstancingMeshGeometryModel3D`,
**`CrossSectionMeshGeometryModel3D`** (section/clipping planes), `OutLineMeshGeometryModel3D`,
`DirectionalLight3D`, `PhongMaterials`, `DefaultEffectsManager`, `MeshBuilder`, `FindHits` with
`HitTestResult.PointHit`/`Distance`/`ModelHit`, and the `OnRendered` / `RenderExceptionOccurred`
events. Template parts include a view cube, coordinate view and frame-statistics view.

That covers everything the CAD viewport needs in Phase 1.

## Consequences for the plan

- **ADR-0003 stands.** Avalonia shell + HelixToolkit v3, with `IViewport` keeping it swappable.
- **Pin Avalonia 11.3.22.** Revisit when Helix ships a build against Avalonia 12; track it as a
  dependency-upgrade task rather than a blocker.
- Always hook `RenderExceptionOccurred` — it is how render failures surface at all.
- Add a smoke test that asserts `OnRendered` fires at least once, so a missing template can never
  regress silently.

## Reproduce

Probe sources are in the session scratchpad under `s3/` (`PkgProbe` for binding and reflection,
`RenderProbe` / `RenderProbe11` for the windowed benchmark). They are throwaway; the numbers above and
the `Generic.axaml` requirement are the findings worth keeping.
