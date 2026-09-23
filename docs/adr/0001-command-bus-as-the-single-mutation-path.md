# ADR-0001 — Every model mutation goes through a command bus

- Status: Accepted
- Date: 2026-09-23

## Context

ModelPop lets three different actors change a model: the user through the UI, an LLM responding to a
prompt, and replay/test code. If each has its own path into the geometry, then undo, parametric
rebuild, testing and safety all have to be solved three times, and the AI path will be the one that is
solved worst.

Letting an LLM emit executable code that runs in-process against the geometry is the obvious shortcut
and is unacceptable: it is unbounded, unvalidatable, and untestable.

## Decision

A `Document` is an ordered list of features produced by `ICommand` instances applied through a single
`CommandBus`. All three actors emit commands. The LLM emits a **schema-validated JSON array of typed
commands**, never code. Commands are validated and range-checked before they reach the bus.

Rebuild is a replay of the command history. Undo/redo is history navigation.

## Consequences

**Good.** Undo/redo works identically for AI and manual edits. Parametric rebuild falls out for free.
Tests are "apply commands, assert invariants" with no UI and no mocks. The LLM is confined to an
expressible vocabulary, which is a security boundary as well as a quality one. AI behaviour can be
tested offline from recorded prompt→command fixtures.

**Cost.** Every new modelling operation needs a command type, a validator and a serialisation format;
this is more work per feature than calling a kernel method directly. Accepted deliberately.

**Constraint.** Nothing may mutate geometry outside the bus. This is enforced by an architecture test,
not by convention.
