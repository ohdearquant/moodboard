# ADR-0006: Standalone tool, no design-application SDK dependency

- **Status:** Proposed
- **Date:** 2026-08-07
- **Measurable claim:** no, and it says so explicitly per the convention in
  [`README.md`](README.md). This is a scope decision.

## Context

The tempting move for a tool like this is to live inside a design application as a panel or
plugin, on the argument that the work being scored is already open there when the question is
asked. The insertion-point observation is fair. The costs of acting on it are not:

- It builds the project's identity on **someone else's platform**. Whether the design even
  works becomes a question about a vendor's SDK — what a sandboxed panel may reach, whether
  it can hand file paths to an external process or must serialise pixels per candidate — and
  those facts change between versions and are answerable only by tracking that vendor's
  documentation on an ongoing basis.
- It puts a **paid application into the build-and-test loop** of an open project whose whole
  claim is that anyone can clone it, fetch the datasets, and reproduce every measurement with
  nothing but this repository.
- It adds surface without adding a single measurable claim. The validation work in ADRs 0003
  through 0005 — the entire substance of the project — runs headless and never needed a host
  application.

## Decision

**The product is the standalone tool: the CLI, the library, and the self-contained HTML
report.** Exactly the shape ADR-0001 defined. It depends on nothing a contributor cannot get
for free, and every claim in this repository is reproducible from a clone.

**No design-application SDK is a dependency of anything in this repository.** No panel, no
plugin, no extension, for any host. The engine-and-report split from ADR-0001 already means
any external tool can consume the report file, which is the correct integration story: the
report is the interface, and whoever wants an integration builds it against the report schema
on their own side of the boundary. Nothing here needs to know they exist.

**The insertion-point observation survives at the only layer that needs it.** Proximity to
the work does not require living inside another application: `moodboard rank <dir>` pointed
at a folder of candidates, and a report that opens in a browser. Watching a directory or
hot-reloading a report is ordinary CLI territory and needs no one's SDK.

## Alternatives considered

**A panel or plugin inside a design application as the primary surface.** Rejected for the
reasons in the context. The decisive one is the development dependency: a validation-first
open project cannot have a paid third-party application in its build-and-test loop.

**Keeping a panel as a stretch goal in-repo.** Rejected. A half-alive integration directory
is a maintenance surface and an implied promise. If an integration is ever built, the report
contract is where it plugs in, and it can live in its own repository with its own
dependencies.

## Consequences

ADR-0002's report contract is the sole integration boundary, which is what it was designed
for. The abstention rendering concern from ADR-0004 reduces to the CLI and the HTML report,
both of which have room to say a full sentence.
