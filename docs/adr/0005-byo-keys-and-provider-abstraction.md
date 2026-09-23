# ADR-0005 — Bring-your-own keys behind a segregated provider abstraction

- Status: Accepted
- Date: 2026-09-23

## Context

The user supplies their own AI credentials and wants to configure them in the app. Different stages
need different capabilities: CAD code generation wants a strong reasoning model, render critique wants
vision, routing and naming want something cheap, and an offline mode should work with local models.

A single fat `IAiProvider` interface would force every adapter to pretend to support everything.

## Decision

Segregate the interfaces: `IChatProvider`, `IVisionProvider`, `IToolCallingProvider`. Adapters
implement only what they genuinely support; Ollama implements the first two and declares it does not
support the third, rather than throwing at runtime.

Providers: Anthropic (official C# SDK) as default, an OpenAI-compatible adapter (covers OpenAI,
OpenRouter, LM Studio), and Ollama for local/offline.

Model choice is **per role**, not global: `Routing`, `CadCodegen`, `Critique`, `Naming`. Each role maps
to a provider + model in settings, with sane defaults.

Keys are stored via `ISecretStore` using Windows DPAPI per-user protection. Never in `appsettings.json`,
never in logs, never in a crash dump. The settings panel writes through the store and validates a key
with a cheap round-trip before saving.

## Consequences

**Good.** The app works fully offline with Ollama, at reduced quality, which is a real feature given
16 GB of VRAM and 128 GB of RAM. Costs are visible and attributable per role. Swapping a model is a
settings change, not a code change.

**Cost.** Three interfaces and a capability-negotiation step instead of one interface. Worth it: it is
the difference between an honest abstraction and one that lies.

**Obligation.** Every provider adapter must be covered by record/replay fixture tests including
malformed and adversarial responses.
