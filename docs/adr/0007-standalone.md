# ADR-0007: Standalone tool, no design-application SDK dependency

- **Status:** Proposed
- **Date:** 2026-08-07
- **Measurable claim:** no, and it says so explicitly per the convention in
  [`README.md`](README.md). This is a scope decision.
- **Supersedes:** [ADR-0006](0006-adobe-panel-is-the-primary-surface.md), same day.

## Context

ADR-0006 made a panel inside a design application the primary surface, on the argument that
the work being scored is already open there when the question is asked. The argument about
insertion points is not wrong, but the decision built the project's identity on someone
else's platform, and the costs of that were all listed in ADR-0006's own text without being
weighed as costs:

- The design could be **invalidated by facts about a vendor's SDK** — its own acceptance
  criterion said so. A project whose viability waits on a platform survey is not standalone
  in any sense.
- Building and testing a panel requires the host application, which means a **paid
  subscription as a development dependency** and as a contributor prerequisite. That is a
  real barrier for an open-source project: anyone should be able to clone, fetch the
  datasets, and reproduce every measurement with nothing but this repository.
- The validation work — the entire substance of ADRs 0003 through 0005 — never needed a
  panel. It runs headless. The panel added surface area without adding a single measurable
  claim.

## Decision

**The product is the standalone tool: the CLI, the library, and the self-contained HTML
report.** Exactly the shape ADR-0001 defined. It depends on nothing a contributor cannot get
for free, and every claim in this repository is reproducible from a clone.

**No design-application SDK is a dependency of anything in this repository.** No panel, no
plugin, no extension, for any host. The engine-and-report split from ADR-0001 already means
any external tool can consume the report file, which is the correct integration story: the
report is the interface, and whoever wants an integration builds it against the report
schema on their own side of the boundary. Nothing here needs to know they exist.

**The insertion-point observation survives at the only layer that needs it.** What made a
panel attractive was proximity to the work. The standalone tool gets most of that with none
of the dependency: `moodboard rank <dir>` pointed at a folder of candidates, and a report
that opens in a browser. Watching a directory or a hot-reloading report is ordinary CLI
territory and needs no one's SDK.

## Alternatives considered

**Keep ADR-0006.** Rejected for the reasons above. The decisive one is the development
dependency: a validation-first open project cannot have a paid third-party application in
its build-and-test loop.

**Keep the panel as a stretch goal in-repo.** Rejected. A half-alive integration directory
is a maintenance surface and an implied promise. If an integration is ever built, the report
contract is where it plugs in, and it can live in its own repository with its own
dependencies.

## Consequences

ADR-0006 is marked superseded and stays in place per the ADR convention; its platform survey
acceptance criterion dies with it, and the research that would have satisfied it is not
needed.

The README scope section returns to the standalone description. ADR-0002's report contract
regains its role as the sole integration boundary, which it was designed for, and the
abstention rendering concern from ADR-0004 reduces to the CLI and the HTML report, both of
which have room to say a full sentence.
