# Moodboard

Build a visual reference board, rank candidate images against it, and keep every number
traceable to how it was measured.

A complete worked run is live at
[ohdearquant.github.io/moodboard](https://ohdearquant.github.io/moodboard/), with the full
[technical report](https://ohdearquant.github.io/moodboard/report.html) from the same run.

Both pages are self-contained HTML files with their data, media, measurements, and provenance
embedded. They make no runtime API calls and can be inspected without access to a local build
environment.

**Status: demonstrable proof of concept.** The core engine and the measurement pipelines shown on
the live pages are implemented and covered by deterministic tests.

## What it demonstrates

Moodboard turns a folder of reference images into a versioned board, ranks candidate images
against that board, and produces an auditable report rather than an unexplained score.

| Measurement | What the PoC demonstrates | Deliberate boundary |
|---|---|---|
| Board compatibility | Board-relative conformal p-values, fit tiers, ties, nearest references, and leave-one-reference-out sensitivity | Not an aesthetic-quality score or approval probability |
| Intent routing | An explicit collection gate over a preserved visual-similarity order | Routing control, not learned retrieval quality |
| Edit locality | Source, generated output, and protected-region verification in one recorded loop | A compositor pass does not prove generator quality |
| Preference replay | Separate immutable model snapshots evaluated on frozen pair probes | `policy_simulated` policies, not human preference; never merged into the 24-image ranking |

The core distinction is simple: *is this a good image?* and *does this image belong with these
images?* are different questions. Moodboard addresses the second one and keeps the measurements
behind each decision separate.

## Run the core workflow

The default engine is local and requires no external service.
Replace `references/` and `candidates/` below with directories containing your own images.

```bash
uv sync --frozen

uv run moodboard build references/ -o board.mb
uv run moodboard rank candidates/ \
  --board board.mb \
  --references references/ \
  --output report.json
uv run moodboard report report.json
```

To produce the self-contained viewer from a source checkout:

```bash
npm --prefix viewer ci
npm --prefix viewer run build
uv run moodboard report report.json --html report.html
```

`build` freezes the scoring policy and representation identity into a content-addressed board
artifact. `rank` consumes that exact policy and writes a closed report; it does not silently
inherit later config changes. `report` validates the document before rendering it and never
recomputes a score.

## How the score works

For each candidate, Moodboard computes k-nearest cosine nonconformity within the board context and
converts it into a board-relative full-conformal p-value. Higher values mean the candidate is
harder to tell apart from the board's own references.

The report preserves the context needed to read that number:

- the reference board and its immutable identity;
- effective support and near-duplicate diagnostics;
- the exact scoring policy and representation revision;
- nearest reference images and classical palette, tone, and composition diagnostics;
- leave-one-reference-out sensitivity ranges and explicit tie relations;
- source, schema, command, and model provenance.

P-values are discrete on small boards, so exact ties are expected. They are not probabilities that
an image is “on brand,” and they do not replace human review.

## Optional Khive + Lattice backend

The offline encoder is the default. The opt-in Khive path stores exact visual assets in BlobStore
and obtains frozen Lattice descriptors through the Moodboard pack. The commands below assume a
preconfigured `kkernel` with that pack enabled, a durable BlobStore, and a pinned visual checkpoint;
the core workflow above needs none of them. See
[`docs/setup-khive-lattice.md`](docs/setup-khive-lattice.md) for a cold-builder guide to obtaining
and configuring that backend.

```bash
uv run moodboard build references/ -o board.mb \
  --encoder khive-lattice \
  --khive-executable kkernel \
  --khive-config /absolute/path/to/khive.toml \
  --khive-actor lambda:moodboard \
  --khive-namespace local

uv run moodboard rank candidates/ \
  --board board.mb \
  --references references/ \
  --output report.json \
  --encoder khive-lattice \
  --khive-executable kkernel \
  --khive-config /absolute/path/to/khive.toml \
  --khive-actor lambda:moodboard \
  --khive-namespace local
```

This path is fail-closed: descriptor identity, dimensions, finiteness, normalization, adapter
revision, actor, and namespace are checked before a result is accepted. Retrieval cosine remains
a retrieval similarity; it is never substituted for the board-relative conformal score.

## Reproducibility

The repository keeps product claims close to the artifacts that can challenge them:

- [`INTERFACES.md`](INTERFACES.md) — executable contracts and cross-field invariants
- [`docs/showcase-corpus.md`](docs/showcase-corpus.md) — public-domain integration corpus
- [`docs/pixel-rag.md`](docs/pixel-rag.md) — intent-scoped retrieval
- [`docs/firefly-measured-loop.md`](docs/firefly-measured-loop.md) — measured generation and locality loop
- [`docs/demo-preference.md`](docs/demo-preference.md) — immutable preference replay
- [`DATASETS.md`](DATASETS.md) — datasets, licences, and reproduction commands
- [`docs/adr/README.md`](docs/adr/README.md) — architecture decision history

The decision records remain available for implementation review, but the live pages are the best
place to understand the PoC as a complete system.

## Scope

The PoC does **not** claim general aesthetic validity, production readiness, human-preference
agreement, or generalization beyond the frozen runs shown here.

## Repository layout

```text
moodboard/        Python engine, adapters, artifacts, and report contracts
viewer/           TypeScript/React offline report viewer
tests/            deterministic unit, property, CLI, packaging, and protocol tests
docs/             workflows, report contracts, and architecture decisions
DATASETS.md       one recorded row per validation claim
```

## Licence

The code is MIT licensed; see [`LICENSE`](LICENSE). Dataset and image licences are recorded
separately in [`DATASETS.md`](DATASETS.md) and the corpus manifests.
