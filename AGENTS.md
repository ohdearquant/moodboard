# Agent guide

Instructions for coding agents (and new human contributors) working in this repository.
[`CONTRIBUTING.md`](CONTRIBUTING.md) carries the full build and test detail; this file is the
short contract an agent must not break. If the two ever disagree, one of them is stale, and
the fix is a pull request that makes them agree, not a silent local interpretation.

## What this repository is

Two artifacts, joined by a file. A Python engine under `moodboard/` builds a reference board
from images, ranks candidates against it, and writes a closed JSON report. A TypeScript/React
viewer under `viewer/` renders that report as one self-contained offline HTML file. The report
is the only boundary between them ([ADR-0001](docs/adr/0001-engine-and-viewer-split.md)); the
viewer never recomputes a score and never fetches anything at runtime.

## Rules that bind every change

1. **A claim lands in the README only after the artifact that reproduces it lands in the
   repository.** This is the project's standing rule. Do not write "supports", "validated",
   or a number into any document without the test, fixture, or recorded run that produces it.

2. **Recorded measurements are immutable** ([ADR-0009](docs/adr/0009-measurement-and-evaluation-contract.md)).
   A published value is bound to the revision that produced it. If the code changes, produce a
   new measurement under the new revision; never edit an existing one to match.

3. **Keep the numbers meaning what they mean.** Conformal p-values are board-relative
   exchangeability statements, not aesthetic quality and not an approval probability.
   Retrieval cosine is a retrieval similarity in `[-1, 1]`, never a substitute for the
   conformal score. Preference-replay outputs are model snapshots on frozen probes, not human
   preference. Do not collapse these into one number, in code or in prose.

4. **Abstention is a first-class output, not an error path**
   ([ADR-0004](docs/adr/0004-abstention.md)). When the engine cannot score, it says so in the
   report. Do not convert an abstention into a default value to make a test pass.

5. **Fail-closed stays fail-closed.** The Khive/Lattice path rejects malformed, partial,
   drifting, non-finite, wrongly dimensioned, or non-unit results. Report readers refuse
   unknown schema versions before interpreting content. Resource ceilings (report file size,
   ingest byte budgets, pixel bounds) reject rather than truncate. A change that turns any
   refusal into a warning or a fallback needs its own decision record, not a code review nit.

6. **Frozen policy is frozen.** `build` writes the complete scoring policy into the board
   artifact; `rank` consumes the stored policy. Editing `eval/thresholds.json` must never
   move an existing board's scores. Do not add a path that lets it.

## Build and test

Engine (offline, no network, no model weights):

```bash
uv sync --locked
uv run pytest -q -rA
uv run ruff check .
```

Viewer:

```bash
npm --prefix viewer ci
npm --prefix viewer run test:ci
```

CI runs the lint job and the full pytest suite on Python 3.11 and 3.13, rebuilds the viewer
from locked inputs, and fails if the committed `viewer/dist-static` is not byte-identical to
the fresh build. So: **if your change affects viewer output, run `npm --prefix viewer run
build` and commit the resulting `viewer/dist-static` changes in the same pull request.**

Real-integration tests (`tests/test_khive_real.py`) are opt-in via the environment gates
documented in `CONTRIBUTING.md`. The ordinary suite uses a fake executable; never make an
ordinary test depend on a real `kkernel`, a model checkpoint, or the network.

## Generated files: regenerate, never hand-edit

| Path | Regenerate with |
|---|---|
| `viewer/src/generated/report-validators.mjs` (+ `.d.mts`) | `npm --prefix viewer run validators:write` |
| `viewer/src/generated/*-bridge.json` | the matching `*:write` script in `viewer/package.json` |
| `viewer/dist-static/` | `npm --prefix viewer run build` |
| `moodboard/viewer_dist/` | staged by the viewer build; never edited directly |

Each generated artifact has a `--check` twin that CI or the build chain runs; a hand edit will
be detected and rejected, so save yourself the round trip.

## Contracts and where they are decided

- [`INTERFACES.md`](INTERFACES.md) — the shared signatures between `moodboard/` modules.
  Changing a signature means changing this document first, in the same pull request.
- `moodboard/schema/report_v1_0.schema.json` and `report_v1_1.schema.json` — the report
  contract ([ADR-0002](docs/adr/0002-report-contract.md),
  [ADR-0008](docs/adr/0008-report-contract-for-viewer.md)). Dispatch is by exact
  `schema_version` string. A field change is a version change.
- `moodboard/preference.py` — the preference feature artifact version. Changing any feature
  formula requires a new version even if field names stay the same.
- `viewer/artifact-manifest.schema.json` — the viewer package manifest shape.

## Decision records

`docs/adr/` holds one file per decision. Records are immutable once accepted; a later change
gets its own record and marks the earlier one superseded. A record that makes a measurable
claim names the dataset that measures it, as a row in [`DATASETS.md`](DATASETS.md), with
source, licence, and the exact reproduction command. If your change contradicts an accepted
record, the change is a new record plus the code, not just the code.

## Style

The style authority is [`docs/STYLE.md`](docs/STYLE.md). The points agents get wrong most:

- New comments and docstrings are terse: one-line docstrings for most functions, comments only
  where the why is not visible in the code. Rationale longer than two sentences goes to a
  decision record, `INTERFACES.md`, or the commit message, with a citation left in the source.
- Much of the existing code predates that rule and carries paragraph-length load-bearing
  comments. Do not delete them on style grounds; migrate their substance to the right home
  when you touch the code they describe.
- Python is linted by ruff with the configuration in `pyproject.toml` (line length 100).
  There is no separate formatter; match the surrounding code.
- Tests are deterministic. No sleeps, no wall-clock dependence, no network, no randomness
  without a fixed seed.
- Commit subjects are lowercase and type-prefixed (`docs:`, `feat:`, `fix:`, `ci:`), matching
  the existing history.

## Pull requests

Work on a branch, open a pull request, and let CI finish. `main` blocks force-push and branch
deletion for every actor. Keep pull-request text technical and about the product change;
the diff and the tests are the argument.
