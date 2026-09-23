"""Is this model actually printable?

No industry-standard metric exists for this - every "print-ready" claim in the
2026 market is vendor-sourced and unverified (see ``docs/research/findings.md``).
So ModelPop defines its own, and makes it honest and explainable rather than a
single mysterious score.

The report is a list of findings, each linked to the stage that can fix it. It
rolls up to a traffic light, but the detail is the point: a warning the user
cannot act on is noise.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Protocol, runtime_checkable

from modelpop.domain.mesh import Mesh
from modelpop.domain.printer import PrinterProfile
from modelpop.domain.units import Length

__all__ = [
    "Finding",
    "MeshFacts",
    "PrintRule",
    "ReadinessReport",
    "Severity",
    "standard_rules",
]


class Severity(IntEnum):
    """How much a finding matters. Ordered, so ``max()`` gives the overall verdict."""

    INFO = 0
    WARNING = 1
    BLOCKER = 2

    @property
    def traffic_light(self) -> str:
        """Green, amber or red."""
        return {Severity.INFO: "green", Severity.WARNING: "amber", Severity.BLOCKER: "red"}[self]


@dataclass(frozen=True, slots=True)
class Finding:
    """One thing worth telling the user about.

    Attributes:
        rule: which check produced this.
        severity: how much it matters.
        message: what is wrong, in the user's language.
        remedy: what to do about it. Empty only when nothing can be done.
        fix_stage: the print-prep stage that would fix it, for the one-click fix.
    """

    rule: str
    severity: Severity
    message: str
    remedy: str = ""
    fix_stage: str = ""

    def __str__(self) -> str:
        prefix = {Severity.INFO: "info", Severity.WARNING: "warn", Severity.BLOCKER: "STOP"}[
            self.severity
        ]
        return f"[{prefix}] {self.message}" + (f" -> {self.remedy}" if self.remedy else "")


@dataclass(frozen=True, slots=True)
class MeshFacts:
    """What the geometry layer measured, handed to the rules.

    A plain record rather than a live mesh, so the rules stay pure and cheap to
    test. Anything a rule needs must be measured once, here, not recomputed.
    """

    mesh: Mesh
    is_watertight: bool
    is_winding_consistent: bool
    shell_count: int = 1
    hole_count: int = 0
    self_intersection_count: int = 0
    degenerate_face_count: int = 0
    duplicate_vertex_count: int = 0
    thinnest_wall: Length | None = None
    overhang_area_fraction: float = 0.0
    bed_contact_area_mm2: float = 0.0


@runtime_checkable
class PrintRule(Protocol):
    """One printability check.

    Rules are a Specification: independent, individually testable, and reported
    rather than thrown. Adding a rule must never require touching the report.
    """

    @property
    def name(self) -> str:
        """A stable identifier for this check, used in findings and in the UI.

        Declared read-only so that frozen dataclasses satisfy the protocol. A
        settable attribute here would require every rule to be mutable, which is
        exactly what we do not want.
        """
        ...

    def check(self, facts: MeshFacts, printer: PrinterProfile) -> Finding | None:
        """Return a finding, or ``None`` when all is well."""
        ...


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    """Everything known about whether a model will print."""

    findings: tuple[Finding, ...] = ()
    triangle_count: int = 0
    volume_mm3: float = 0.0

    @property
    def verdict(self) -> Severity:
        """The worst finding, or INFO when there are none."""
        return max((f.severity for f in self.findings), default=Severity.INFO)

    @property
    def is_printable(self) -> bool:
        """Whether anything blocks printing outright."""
        return self.verdict < Severity.BLOCKER

    @property
    def blockers(self) -> tuple[Finding, ...]:
        """The findings that must be fixed first."""
        return tuple(f for f in self.findings if f.severity is Severity.BLOCKER)

    @property
    def warnings(self) -> tuple[Finding, ...]:
        """The findings worth a look."""
        return tuple(f for f in self.findings if f.severity is Severity.WARNING)

    def summary(self) -> str:
        """A one-line verdict for the status bar."""
        if not self.findings:
            return "Ready to print."
        if self.blockers:
            return (
                f"Cannot print: {len(self.blockers)} blocker(s), {len(self.warnings)} warning(s)."
            )
        return f"Printable, with {len(self.warnings)} warning(s)."

    def __str__(self) -> str:
        lines = [self.summary()]
        lines.extend(f"  {finding}" for finding in self.findings)
        return "\n".join(lines)


# --------------------------------------------------------------------- rules


@dataclass(frozen=True, slots=True)
class NotEmpty:
    """A mesh with no triangles cannot print."""

    name: str = "not-empty"

    def check(self, facts: MeshFacts, printer: PrinterProfile) -> Finding | None:
        """Flag an empty mesh."""
        if facts.mesh.is_empty:
            return Finding(
                self.name,
                Severity.BLOCKER,
                "The model has no geometry.",
                "Load or generate a model first.",
            )
        return None


@dataclass(frozen=True, slots=True)
class Watertight:
    """The hard gate. Everything downstream assumes a closed solid."""

    name: str = "watertight"

    def check(self, facts: MeshFacts, printer: PrinterProfile) -> Finding | None:
        """Flag an open or non-manifold mesh."""
        if facts.mesh.is_empty or facts.is_watertight:
            return None
        detail = f" ({facts.hole_count} hole(s))" if facts.hole_count else ""
        return Finding(
            self.name,
            Severity.BLOCKER,
            f"The model is not watertight{detail}, so the slicer cannot tell inside from outside.",
            "Run automatic repair.",
            fix_stage="repair",
        )


@dataclass(frozen=True, slots=True)
class ConsistentWinding:
    """Flipped normals slice as inside-out solids."""

    name: str = "winding"

    def check(self, facts: MeshFacts, printer: PrinterProfile) -> Finding | None:
        """Flag inconsistent face winding."""
        if facts.mesh.is_empty or facts.is_winding_consistent:
            return None
        return Finding(
            self.name,
            Severity.BLOCKER,
            "Some faces point inwards, so the model may slice inside-out.",
            "Run automatic repair to fix the normals.",
            fix_stage="repair",
        )


@dataclass(frozen=True, slots=True)
class FitsBuildVolume:
    """Bigger than the printer is a blocker; the user must scale or split."""

    name: str = "fits-build-volume"

    def check(self, facts: MeshFacts, printer: PrinterProfile) -> Finding | None:
        """Flag a model too large for the bed."""
        if facts.mesh.is_empty:
            return None
        bounds = facts.mesh.bounds
        if bounds.fits_within(*printer.envelope):
            return None
        width, depth, height = printer.envelope
        return Finding(
            self.name,
            Severity.BLOCKER,
            (
                f"The model is {bounds.width.format()} x {bounds.depth.format()} x "
                f"{bounds.height.format()}, larger than the "
                f"{width.format(places=0)} x {depth.format(places=0)} x "
                f"{height.format(places=0)} build volume."
            ),
            "Scale it down, or split it into parts.",
            fix_stage="scale",
        )


@dataclass(frozen=True, slots=True)
class WallThickness:
    """Detail thinner than two extrusion lines will not survive printing.

    This is the mechanism behind the commonest complaint about AI-generated
    models: the render has crisp detail, the print is a smooth blob.
    """

    name: str = "wall-thickness"

    def check(self, facts: MeshFacts, printer: PrinterProfile) -> Finding | None:
        """Flag walls below two line widths."""
        if facts.thinnest_wall is None or facts.mesh.is_empty:
            return None
        minimum = printer.nozzle.minimum_wall
        if facts.thinnest_wall >= minimum:
            return None
        return Finding(
            self.name,
            Severity.WARNING,
            (
                f"The thinnest wall is {facts.thinnest_wall.format()}, below the "
                f"{minimum.format()} needed for a {printer.nozzle.diameter.format()} nozzle. "
                "Fine detail will be lost or fragile."
            ),
            "Thicken the thin regions, scale the model up, or fit a finer nozzle.",
            fix_stage="thickness",
        )


@dataclass(frozen=True, slots=True)
class SingleShell:
    """Several disconnected shells usually means stray fragments."""

    name: str = "single-shell"

    def check(self, facts: MeshFacts, printer: PrinterProfile) -> Finding | None:
        """Flag a mesh made of several disconnected pieces."""
        if facts.shell_count <= 1:
            return None
        return Finding(
            self.name,
            Severity.WARNING,
            f"The model is {facts.shell_count} separate pieces.",
            "Keep the largest piece, or arrange them as separate objects.",
            fix_stage="normalise",
        )


@dataclass(frozen=True, slots=True)
class NoSelfIntersections:
    """Self-intersections confuse the slicer even when the mesh looks closed."""

    name: str = "self-intersections"

    def check(self, facts: MeshFacts, printer: PrinterProfile) -> Finding | None:
        """Flag self-intersecting geometry."""
        if facts.self_intersection_count == 0:
            return None
        return Finding(
            self.name,
            Severity.WARNING,
            f"{facts.self_intersection_count} self-intersecting face(s) found.",
            "Run automatic repair.",
            fix_stage="repair",
        )


@dataclass(frozen=True, slots=True)
class DegenerateFaces:
    """Zero-area triangles are harmless to look at and poison to process."""

    name: str = "degenerate-faces"

    def check(self, facts: MeshFacts, printer: PrinterProfile) -> Finding | None:
        """Flag slivers and duplicate vertices."""
        if facts.degenerate_face_count == 0 and facts.duplicate_vertex_count == 0:
            return None
        parts = []
        if facts.degenerate_face_count:
            parts.append(f"{facts.degenerate_face_count} zero-area face(s)")
        if facts.duplicate_vertex_count:
            parts.append(f"{facts.duplicate_vertex_count} duplicate vertex/vertices")
        return Finding(
            self.name,
            Severity.INFO,
            f"The mesh contains {' and '.join(parts)}.",
            "Cleaning these up makes later steps more reliable.",
            fix_stage="normalise",
        )


@dataclass(frozen=True, slots=True)
class TriangleBudget:
    """Enormous meshes make the viewport and the slicer crawl."""

    name: str = "triangle-budget"
    limit: int = 1_000_000

    def check(self, facts: MeshFacts, printer: PrinterProfile) -> Finding | None:
        """Flag an excessive triangle count."""
        count = facts.mesh.triangle_count
        if count <= self.limit:
            return None
        return Finding(
            self.name,
            Severity.INFO,
            f"{count:,} triangles is more than a print needs.",
            "Decimate to around 300,000; the printed result is identical.",
            fix_stage="decimate",
        )


@dataclass(frozen=True, slots=True)
class TipOverRisk:
    """A tall model on a small footprint falls over mid-print."""

    name: str = "tip-over"
    ratio_limit: float = 4.0

    def check(self, facts: MeshFacts, printer: PrinterProfile) -> Finding | None:
        """Flag a poor height-to-footprint ratio."""
        if facts.mesh.is_empty:
            return None
        bounds = facts.mesh.bounds
        footprint = min(bounds.width.millimetres, bounds.depth.millimetres)
        if footprint <= 0:
            return None
        ratio = bounds.height.millimetres / footprint
        if ratio <= self.ratio_limit:
            return None
        return Finding(
            self.name,
            Severity.WARNING,
            f"The model is {ratio:.1f} times taller than its narrowest footprint.",
            "Add a brim, or re-orient it to sit on a wider face.",
            fix_stage="orient",
        )


def standard_rules() -> tuple[PrintRule, ...]:
    """The default rule set, ordered from most to least serious."""
    rules: tuple[PrintRule, ...] = (
        NotEmpty(),
        Watertight(),
        ConsistentWinding(),
        FitsBuildVolume(),
        SingleShell(),
        NoSelfIntersections(),
        WallThickness(),
        TipOverRisk(),
        DegenerateFaces(),
        TriangleBudget(),
    )
    return rules


def assess(
    facts: MeshFacts,
    printer: PrinterProfile,
    rules: tuple[PrintRule, ...] | None = None,
) -> ReadinessReport:
    """Run every rule and collect the findings."""
    active = rules if rules is not None else standard_rules()
    findings = tuple(
        finding for rule in active if (finding := rule.check(facts, printer)) is not None
    )
    ordered = tuple(sorted(findings, key=lambda f: f.severity, reverse=True))
    return ReadinessReport(
        findings=ordered,
        triangle_count=facts.mesh.triangle_count,
        volume_mm3=facts.mesh.volume,
    )
