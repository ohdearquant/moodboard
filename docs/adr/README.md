# Architecture decision records

One file per decision. A record states the context, the decision, the alternatives that were
considered and rejected, and the consequences that follow. Records are immutable once
accepted: a later decision that changes an earlier one gets its own record and marks the
earlier one superseded. New records start from [`TEMPLATE.md`](TEMPLATE.md).

## Statuses

| status | meaning |
|---|---|
| `Proposed` | written, open for challenge, nothing is built against it yet |
| `Accepted` | agreed, and every acceptance criterion below is satisfied |
| `Superseded by NNNN` | replaced, kept in place for the history |

## The rule that gates acceptance

**A record that makes a measurable claim names the dataset that measures it, in the record
itself.** The named dataset carries its source, its size, its licence, the split or protocol
used, and the exact command that reproduces the measurement. A record whose acceptance
criterion has no dataset behind it stays `Proposed`.

This is stricter than it sounds and it is deliberate. Most of the risk in this project is a
representation that appears to work because it was only ever looked at, so the decision to
use it is not separable from the measurement that tests it. Writing the dataset row at
decision time, rather than promising it at implementation time, is what keeps the two
together.

Records that make no measurable claim say so explicitly, so that a missing dataset row reads
as a decision rather than as an omission.

Dataset rows live in [`DATASETS.md`](../../DATASETS.md) at the repository root, one row per
validation claim.

## Index

| id | title | status |
|---|---|---|
| [0001](0001-engine-and-viewer-split.md) | Two artifacts, a Python engine and a TypeScript viewer, joined by a file | Proposed |
| [0002](0002-report-contract.md) | The report is the product, and its JSON schema is the contract | Proposed |
| [0003](0003-style-representation.md) | Style representation, and the content invariance test that decides it | Proposed |
| [0004](0004-abstention.md) | The tool refuses to score when it cannot, and refusing is a first-class output | Proposed |
| [0005](0005-reference-set.md) | The reference set is an input with properties, not a folder of images | Proposed |
| [0006](0006-standalone.md) | Standalone tool, no design-application SDK dependency | Proposed |
| [0007](0007-viewer-architecture.md) | The viewer has one report model, explicit outcome components, and one offline build | Proposed |
| [0008](0008-report-contract-for-viewer.md) | Report version 1.1 carries what the viewer presents | Proposed |
| [0009](0009-measurement-and-evaluation-contract.md) | Every published measurement is an immutable, revision-bound result | Proposed |
| [0010](0010-frontend-verification.md) | Frontend verification uses engine-generated fixtures and layered tests | Proposed |
| [0011](0011-khive-native-visual-assets.md) | Khive owns visual assets and Lattice descriptor inference | Proposed |
| [0012](0012-aesthetic-judgment-vocabulary.md) | Typed Aesthetic Judgment Vocabulary and Decision Order | Proposed |
| [0013](0013-studio-viewer-trust-boundary.md) | Studio and Immutable Viewer Trust Boundary | Proposed |
| [0014](0014-intent-packet-generation-run-provider.md) | Frozen Intent Packet, Generation Run, and Provider Boundary | Proposed |
| [0015](0015-human-comparison-preference-snapshots.md) | Human Comparison and Preference Snapshot Lifecycle | Proposed |
| [0016](0016-region-mask-locality-verification.md) | Region, Mask, and Locality Verification | Proposed |
