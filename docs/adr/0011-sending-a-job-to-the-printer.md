# ADR-0011 — A job reaches the printer over LAN mode, and never by accident

**Status:** Accepted
**Date:** 2026-09-24
**Completes:** Phase 2 of `docs/00-plan.md` — "printer gateway over LAN mode, dry-run by default" —
and the scope line "Sending jobs to the printer".

## Context

Everything ModelPop did up to this point ended in a file. Open a model, repair it, scale it, slice
it: the result is bytes on disk, and every one of those steps can be undone by deleting something.
The last stage is not like the others. It starts a machine in another room, on a spool of filament
that costs money, and nothing in this application can stop it once it is going.

There are two ways to reach a Bambu printer. The **cloud** route goes through a Bambu account, a
token refresh cycle and someone else's server sitting in the middle of a print job. The **LAN**
route talks to the machine directly: FTPS on 990 to put a file on it, MQTT over TLS on 8883 to tell
it to start and to ask what it is doing.

## Decision

### 1. LAN mode only

The cloud route is not implemented and is not planned. It contradicts the point of the application:
this is a tool that runs on your machine, with your keys, against your printer. Routing a print job
through an account and a third-party server to reach a device on the same desk is the arrangement
ModelPop exists to avoid.

### 2. Uploading needs nothing; starting needs an optional extra

FTPS is in the standard library, so putting a file on the printer costs no dependency at all. MQTT
is not, so `paho-mqtt` is an **optional extra** (`uv sync --extra printer`) rather than a
requirement. Without it the file still lands on the printer and the user starts it from the
printer's own screen, and the application says exactly that rather than reporting a failure — their
model is on the printer either way, and calling it a failure would send them looking for it.

**Licence:** `paho-mqtt` is EPL-2.0 / EDL-1.0, both permissive. Consistent with the open-source-only
constraint in `CLAUDE.md`.

### 3. Nothing is sent unless the user has said so, twice

Two switches, both off, and they are deliberately not the same switch:

- **"Really send jobs to this printer"**, in Settings, off at every start-up. Until it is ticked the
  application describes what it would send and sends nothing.
- **Start now**, asked at the moment of sending, with "send the file only" as the default button.
  Uploading is reversible — delete the file. Starting is not.

The switch that decides whether anything leaves this machine lives in the **use case**, not in the
gateway. The first implementation put it in the adapter, wiring a real gateway in and letting the
window choose which dialog to show. That was wrong in a way worth recording: the dry-run tick then
governed only the dialog, and leaving the setting alone still uploaded. Refused in
`Workspace.send_to_printer` instead, there is no arrangement of gateways in which an unasked-for job
reaches a printer, and the test that says so is the one that matters most in this area.

### 4. The access code is treated as a credential

`PrinterConnection` overrides `__repr__` to hide it. That is not decoration: the object travels
inside jobs, results and exception messages, any of which may be logged, and the access code is what
lets anyone on the network drive the printer. The address and serial are stored beside it in the OS
credential store — they are not secrets, but the credential store is the one place this application
persists anything, and splitting an address from the code that opens it across two mechanisms helps
nobody.

### 5. TLS is encrypted but not verified, and that is stated rather than buried

The printer presents a self-signed certificate for an IP address, which no certificate authority
will ever vouch for, so verification is off. This protects the access code from passive listeners on
the local network; it does **not** prove the machine at that address is the printer. It is the same
trade Bambu Studio makes, on a network the user controls, and the reasoning is in the adapter's
docstring where somebody changing it will read it.

## Consequences

- ModelPop now covers the whole path: idea or photo → model → prepared → sliced → printing.
- The FTPS and MQTT paths cannot be unit tested without a printer. Everything decidable without a
  socket — the job's validity, the filename, the printer's own report — is a pure function and is
  tested. The rest is an integration test that skips unless a printer is configured.
- Print monitoring (Phase 8) is largely already here: `status()` reads the printer's report, and a
  panel that polls it is a small piece of interface on top.
- The unverified certificate is a known, accepted limitation, not an oversight.
