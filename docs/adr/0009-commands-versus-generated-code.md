# ADR-0009 — Where a language model may write code, and where it may not

**Status:** Accepted
**Date:** 2026-09-24
**Refines:** ADR-0001 (the command bus)
**Does not supersede:** ADR-0001 remains in force for every change to an existing model.

## Context

ADR-0001 says, in as many words:

> An LLM emits validated, typed commands, never executable code that touches the process.

Pipeline A, which is built and working, does the opposite: a model writes build123d source and
ModelPop runs it in a sandboxed subprocess. `CLAUDE.md` repeats ADR-0001 as a non-negotiable
invariant. So the codebase has been quietly contradicting its own stated rule since Pipeline A
landed, and nobody wrote down why.

That needed resolving rather than leaving, because the next person to read ADR-0001 would either
"fix" Pipeline A or conclude the ADRs are decoration.

There is also a second, worse problem that this exposed. Until now `CommandBus` existed, was
tested, and **was wired to nothing**. Every change to a model went straight through `Workspace`
into a new `WorkspaceState`. The invariant was not being violated so much as not being implemented.

## Decision

### 1. Two paths, with a line between them that is about *what is being changed*

**Creating a part from nothing** may use generated code. The model writes a build123d script, it
runs in the sandboxed kernel, and the gates measure what came out. This is Pipeline A and it stays.

**Changing a model that already exists** goes through typed commands on the bus. No exceptions,
whoever is asking - toolbar, prompt or replay.

The line is not arbitrary. Generating a novel part is an open-ended problem where the space of
useful outputs is much larger than any command vocabulary could cover, and the published results
are unambiguous that a measure-and-correct loop over generated code clears far more of a CAD
benchmark than a constrained vocabulary does. Editing is the opposite: the space of useful edits is
small, enumerable, and the user needs every one of them to be undoable.

### 2. Editing is now implemented, not just specified

`modelpop.domain.cad_commands` is the vocabulary: create a box, cylinder or sphere; fillet; chamfer;
hollow; move; rotate; scale to a stated size; text on a surface. `ModellingSession` owns the bus.
`Build123dCompiler` replays the tree into geometry.

The tree **is** the model. Geometry is derived, so changing an early feature rebuilds everything
after it - scaling to six inches still gives six inches after an earlier feature changed the shape,
which is the difference between a parametric model and a recording of one.

### 3. A change that will not build is refused, not applied

The obvious implementation appends the feature, rebuilds, and reports the error. That leaves the
user holding a broken model *and* a broken history, and they then have to work out that undoing is
what fixes it.

Instead: apply, rebuild, and roll back if the kernel refuses. **The model is therefore always in a
state that builds.** That is the invariant that makes undo worth trusting.

### 4. Edges are selected by name, never by index

`EdgeSelector.VERTICAL`, not "edge 7". Edge numbering is not stable across a rebuild, so an indexed
selection silently rounds a *different* edge once an earlier feature changes - the classic
parametric CAD failure, and one that produces a plausible wrong answer rather than an error.

### 5. Every command clamps its own parameters

Not defensive habit. These numbers arrive from a language model, and an unclamped one reaches OCCT
as a process crash rather than a message. NaN is the case a naive range check misses, because it
compares unequal to itself.

### 6. Known-impossible combinations are caught before OCCT sees them

OCCT reports several genuine constraints as `Standard_ConstructionError` with no further detail,
which tells the user nothing. The compiler checks for the ones that have been *measured* and
answers with the number to change instead.

One is in place: **hollowing to a wall thickness exactly equal to an existing fillet radius fails**,
because the inner fillet collapses to zero radius. A 2.0 mm wall on a 2.0 mm fillet fails; 1.9 mm
and 2.5 mm both work. The user is told to try 1.8 or 2.5 rather than shown OCCT's wording.

Entries go in that list only when measured. The first guess at this one blamed fillet-and-chamfer
interaction and was wrong; a test now pins the correct behaviour so the wrong belief cannot return.

## Consequences

**Good.** An AI edit is undoable because it is an ordinary command - no special case, no separate
history. The feature tree is inspectable and the compiled script is readable, so "what is this
model actually" is answerable. Every edit is testable with no kernel, because a fake compiler
satisfies the port. ADR-0001's invariant is now true of the code rather than only of the document.

**Bad.** Two editing paths now exist: typed commands for a parametric model, and script rewriting
for a part that came out of Pipeline A. They will need to converge, and the natural direction is for
Pipeline A's output to be *imported* as a feature tree rather than kept as a script. That is not
built and is not free.

Every rebuild costs a subprocess, roughly a second or two on a tree of a handful of features. That
is acceptable for a click and would not be for a drag; a gizmo will need a preview path that does
not round-trip through the kernel.

**Watch.** The vocabulary is small. It covers what Rob asked for by name - "a hollow core", "MSI in
grey across his front" - and it does not yet cover sketches, revolves, patterns or holes. Each
addition is a command, a compiler fragment, and a test, which is the right shape for growth.

## Alternatives considered

**Make Pipeline A emit commands too.** Rejected on evidence. The command vocabulary cannot express
an arbitrary novel part, and constraining generation to it would make ModelPop worse at the thing it
exists to do.

**Call build123d directly instead of generating a script.** Rejected. The script route gets
sandboxing, timeouts and crash isolation from the existing kernel for free, and it makes the model
readable. The cost is a subprocess per rebuild, which is the right trade at this scale.

**Let a failed command stay in the tree with a warning.** Rejected. A model that does not build is
not a model, and a history containing a step that breaks it makes undo something the user has to
reason about rather than trust.
