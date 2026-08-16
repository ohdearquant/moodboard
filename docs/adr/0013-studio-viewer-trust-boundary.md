# ADR-0013: Studio and Immutable Viewer Trust Boundary

- **Status:** Proposed
- **Date:** 2026-08-16
- **Depends on:** ADR-0012's typed evidence vocabulary and selectable-output occurrence.
- **Amends:** ADR-0001's exhaustive two-artifact product-surface clause and ADR-0006's statement
  that the product consists only of the CLI, library, and self-contained report. It preserves
  ADR-0001's engine-to-report-to-viewer file boundary and no-recomputation rule, plus ADR-0006's
  absence of design-application SDK dependencies and clone-reproducible measurements.
- **Extends:** ADR-0007's read-only viewer architecture and ADR-0010's frontend verification. It
  does not weaken either record's report-file or offline boundaries.
- **Measurable claim:** none. This record assigns trust, mutation, network, and secret ownership.
  It makes no usability, performance, or product-quality claim.

## Context

The self-contained report viewer is intentionally incapable of creation. It receives a validated
report, performs no statistical computation, makes no network request, and cannot change the
evidence it presents. Those properties make the file portable and trustworthy.

An interactive creative loop has different requirements. It must select source media and regions,
retrieve references, submit paid provider calls, monitor jobs, preserve failures, compare outputs,
record human choices, and export new evidence. Putting those powers into the offline viewer would
erase the boundary that makes the viewer reliable. Putting provider secrets into a browser bundle
would expose them.

Moodboard therefore needs a connected work surface without turning the immutable viewer into that
surface.

## Decision

### Moodboard has three surfaces

```text
Python engine and local services
    compute, retrieve, generate, verify, persist
            |                         |
            | typed local API         | immutable report/export
            v                         v
Connected Moodboard Studio      Offline report viewer
    creates and mutates              reads and presents
```

1. **Engine and local services** own computation, provider adapters, secrets, filesystem and
   BlobStore access, Khive calls, verification, and immutable artifact publication.
2. **Moodboard Studio** is a connected browser application for one creator. It owns interaction
   state and asks the local backend to perform privileged work through a versioned API.
3. **The report viewer** remains the static TypeScript consumer defined by ADR-0001 and ADR-0007.
   It consumes only embedded, validated artifacts and never becomes a thin client for Studio.

Studio and the viewer may share pure presentation components. They do not share a mutable store,
network adapter, provider client, or source loader. A build-time dependency test keeps viewer code
free of Studio network and mutation modules.

### Trust ownership is closed

| Concern | Studio browser | Local backend | Offline viewer |
|---|---|---|---|
| API/provider secret | never receives | reads from approved secret source in memory | impossible |
| Source and reference bytes | bounded preview or user-selected browser file | validates, hashes, stores, and hydrates | only validated embedded derivatives |
| Retrieval/scoring | requests and presents | invokes typed producer and validates response | presents frozen producer result |
| Generation | creates intent and confirms context | calls adapter, records attempts and output bytes | impossible |
| Verification | presents states and evidence | computes through named verifier | presents frozen receipt |
| Preference event | captures explicit choice | validates and persists occurrence-bound event | read-only evidence only |
| Artifact mutation | edits workspace state | publishes new immutable versions | impossible |
| Network | same-origin local API only | explicitly configured Khive/provider endpoints | none at view time |

The Studio browser never receives a reusable provider credential. It does not call OpenRouter or
another provider directly. It does not receive arbitrary local paths and cannot request arbitrary
filesystem reads. Media access uses validated object identities and bounded endpoints.

### Mutable work never rewrites immutable evidence

Studio workspace state may change while a user edits intent, region, references, or notes. A frozen
intent packet, generation attempt, output occurrence, verifier receipt, human comparison, preference
snapshot, or exported report is immutable.

Changing a field that participates in one of those identities creates a new artifact. Studio may
move an explicit pointer such as `active_board`, `active_preference_snapshot`, or `selected_output`,
but the prior target remains addressable. Undo and rollback move pointers; they do not rewrite
history.

The offline viewer never edits a report, applies a newer preference snapshot, refreshes a provider
receipt, or fills a missing result from a live service. To show new evidence, Studio exports a new
self-contained file.

### The local API is an explicit privilege boundary

Studio talks only to a loopback-bound backend by default. The backend uses origin checks, an
unpredictable session token, bounded request bodies, closed schemas, and explicit media endpoints.
It refuses wildcard cross-origin access. A hosted deployment is not prohibited, but it requires a
separate authentication, authorization, tenancy, retention, and threat-model decision before use.

Long-running provider calls are backend jobs. Studio submits an idempotent request, receives a job
identity, and observes append-only state transitions. Closing the browser does not convert an
unknown paid attempt into a failure or cause an automatic duplicate submission.

Logs, errors, and exported artifacts never contain secrets. Provider payloads are projected into a
closed receipt; arbitrary headers and provider response objects are not copied wholesale into the
report.

### The creative path is compact, with evidence one disclosure away

Studio's primary navigation is organized around creation rather than governance:

```text
Board -> Create -> Compare -> Taste
```

The default generation action shows a compact context summary: operation, region, reference count,
reference-use mode, model, and required gates. An expandable inspector exposes exact reference
order, route reasons, hashes, provider route, costs, and receipts.

A user may opt into default trust for an unchanged context policy. Studio requires explicit renewed
confirmation when the ordered references, reference-use mode, operation-input/source/mask delivery,
provider privacy boundary, requested provider or model, actual-model disclosure policy, adapter
revision, capability snapshot, normalized provider options, provider-route policy, or required
verifier policy changes. A compact primary action must not turn into silent consent, and an
evidence disclosure must not become a mandatory three-click approval chain.

Collaboration, approval queues, board pull requests, and asynchronous review are not part of v1.

### Exports use a versioned one-way envelope

Studio export does not widen report v1.0 or v1.1. A `moodboard.export.v1` document contains:

- one exact validated report payload with its existing schema version and SHA-256;
- an ordered inventory of typed immutable artifacts, each with schema version, SHA-256, media
  type, byte count, and an embedded payload or immutable package-local reference;
- the viewer-package identity and supported artifact schema set;
- the publication-profile identity and any public-derivative attestations; and
- `export_id = sha256(UTF8("moodboard.export.v1\0") ||
  RFC8785(document-without-export_id))`.

Each artifact payload uses exactly one storage form: canonical Base64 in an `embedded` object, or a
`package_path` object whose path is normalized UTF-8 POSIX relative syntax. Package paths reject an
empty segment, `.`, `..`, backslash, absolute/root prefix, symlink, and case-fold collision. The
referenced bytes must match the inventory byte count and SHA-256. Array order is part of the export
identity; storage forms are never guessed from a string.

The initial artifact inventory may include intent packets, generation runs, output occurrences,
constraint receipts, comparisons, preference snapshots, creative sessions, and acceptance events.
Unknown required artifact kinds or schema majors fail closed. Existing report-only files remain
valid inputs to the existing viewer; they are not silently interpreted as Studio exports. The
offline viewer may render `moodboard.export.v1` only after its exact decoder support is packaged
and tested.

An export is produced only by the backend after it:

1. closes every included artifact against its versioned schema;
2. verifies every referenced digest and cross-artifact binding;
3. projects private or local-only provenance according to an explicit publication profile;
4. builds the pinned viewer package;
5. verifies that the output makes no runtime network request and contains no secret; and
6. writes atomically without overwriting an existing file.

The exported viewer contains no capability that can resume Studio. A link back to Studio, if ever
added, is ordinary text and not a privileged session token.

### Existing report semantics remain authoritative

Studio may use richer live contracts than report v1.1. When it presents an existing report field,
the meaning still comes from ADR-0002, ADR-0003, ADR-0004, and ADR-0008. Studio cannot relabel a
conformal p-value as quality, recompute a tie, or convert abstention into zero.

The standalone viewer remains a supported product artifact. Studio is additive; users may continue
to run the CLI/library and produce reports without installing or starting Studio.

## Alternatives considered

**Turn the offline viewer into Studio.** Rejected. Adding jobs, credentials, mutable state, and live
fetches would invalidate the viewer's strongest guarantees and make an attached HTML file dependent
on a service.

**Call providers directly from the browser.** Rejected. It exposes reusable credentials, weakens
idempotency around paid requests, and makes exact provider receipts dependent on client behavior.

**Build Studio as a plugin inside a design application.** Rejected for v1. ADR-0006's no-SDK
decision remains. A later integration can call the same local API from another repository without
becoming a dependency of the core build.

**Make all evidence visible before the primary action.** Rejected. It turns trust infrastructure
into interaction tax. Compact summary plus expandable inspection preserves disclosure without
making governance the product face.

**Store only current workspace state.** Rejected. Generation failures, verifier outcomes, and
preference events would become impossible to replay or compare once a user changed the board.

## Consequences

Moodboard gains an interactive product without sacrificing the portable evidence artifact. The
separation also makes security review tractable: provider secrets and filesystem access have one
backend owner, while the viewer retains a zero-network posture.

There are now two frontend build targets and a local API to version. Shared visual components must
remain free of hidden data dependencies. End-to-end tests must cover both the connected Studio path
and the one-way export boundary.

ADR-0006 is narrowed: standalone CLI/library/report remain fully supported and no design SDK enters
the repository, but they are no longer the only product surface.

## Acceptance conditions

This record remains Proposed until:

1. the repository defines dependency rules that keep provider, network, secret, and mutation code
   out of the offline viewer build;
2. the local API has a closed versioned contract and an explicit loopback/security test boundary;
3. report v1.0/v1.1 remain byte-compatible and a `moodboard.export.v1` decoder rejects unknown or
   mismatched artifact identities while producing a self-contained, read-only, network-free file;
4. changing frozen Studio inputs creates new identities rather than overwriting prior artifacts;
5. Studio tests cover default-trust summary, expandable inspection, and every renewed-confirmation
   condition; and
6. the CLI/library/report workflow remains usable without Studio.
