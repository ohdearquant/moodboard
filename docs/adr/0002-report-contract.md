# ADR-0002: The report is the product, and its JSON schema is the contract

- **Status:** Proposed
- **Date:** 2026-08-07
- **Measurable claim:** yes. The report states an interval around every score and calls two
  assets tied when their intervals overlap. That is a claim about coverage and it is
  falsifiable. Dataset row: `interval-coverage` in [`DATASETS.md`](../../DATASETS.md).

## Context

A coherence score on its own is not usable and is easy to misuse. Three specific ways it
misleads, each of which is a property of the reference set rather than of the asset being
scored:

A board of ten nearly identical references makes almost everything score low, and a board of
ten deliberately varied references makes almost everything score high. Without the board's
own spread on the page, the same number means different things on different boards.

Real moodboards carry an accent image on purpose. One reference sitting far from the others
widens the fitted distribution, which quietly raises the score of everything, including
assets that resemble nothing on the board.

With a reference set of ten to fifty against a representation of several hundred dimensions,
the sampling variation in the fitted distribution is not small. Two scores a few points apart
can easily be the same score measured twice.

None of these are cured by improving the model. They are cured by what the report carries.

## Decision

The engine emits a versioned JSON document. It is the interface, and the human-facing HTML in
ADR-0001 is a rendering of it.

### Schema, version 1

```jsonc
{
  "schema_version": "1.0",
  "board": {
    "id": "sha256 of the sorted reference content hashes",
    "name": "spring-campaign",
    "n_references": 24,
    "built_at": "RFC 3339 timestamp",
    "representation": {
      "style": {"model": "…", "revision": "…", "dim": 768},
      "axes": ["palette", "tone", "composition"]
    }
  },
  "board_stats": {
    "tightness": {
      "loo_mean": 0.0, "loo_sd": 0.0,
      "loo_quantiles": {"p10": 0.0, "p50": 0.0, "p90": 0.0}
    },
    "leverage": [
      {"reference_id": "…", "delta_tightness": 0.0, "rank": 1}
    ],
    "flags": ["degenerate_board"]
  },
  "assets": [
    {
      "asset_id": "…", "source": "path or URI",
      "score": 0.0,
      "interval": {"low": 0.0, "high": 0.0, "level": 0.9, "method": "loo-bootstrap"},
      "rank": 1,
      "axes": {"style": 0.0, "palette": 0.0, "tone": 0.0, "composition": 0.0},
      "exemplars": [{"reference_id": "…", "similarity": 0.0}],
      "flags": []
    }
  ],
  "comparisons": {
    "ties": [["asset_a", "asset_b"]],
    "note": "assets whose intervals overlap at the stated level are not distinguishable"
  },
  "provenance": {
    "engine": {"name": "moodboard", "version": "…"},
    "model": {"repo": "…", "revision": "…", "sha256": "…"},
    "command": "the argv that produced this report",
    "seed": 0,
    "created_at": "RFC 3339 timestamp"
  }
}
```

### Rules the schema exists to enforce

**Every score carries an interval, with no option to omit it.** A bare score field with an
optional interval beside it would be omitted by every consumer in a hurry, and the omission
would be invisible. `interval` is required, `level` and `method` are required inside it.

**Ties are computed by the engine and listed explicitly.** The viewer does not decide what
counts as a tie by comparing numbers itself, because then the engine and the viewer would
carry two definitions of the same thing and they would drift. `comparisons.ties` is the
definition.

**The score is calibrated against the board's own leave-one-out distribution.** This is what
makes a score comparable across boards of different tightness, and it is stated in the
report rather than assumed by the reader.

**Per-axis values are reported separately and are never silently blended away.** A candidate
can match the palette and miss the texture completely, and that is the useful thing to know.
The combined `score` is a documented function of the axes, and the axes stay on the page.

**Provenance pins the model.** A report is reproducible only if the exact weights are named,
so `provenance.model` carries the repository, the revision, and a content hash.

**`flags` is how the report says something is wrong with the question.** A board with almost
no spread, an asset far outside every reference, or fewer references than the estimator needs
are all conditions where a number would still be produced and would still be meaningless.
They get flags, and the viewer is required to surface them.

### Compatibility policy

`schema_version` is `major.minor`. A minor version may add fields. A major version may
remove or change the meaning of a field. A consumer must ignore fields it does not know and
must refuse a major version it does not support rather than reading it partially.

## Acceptance criterion, and the dataset behind it

The tie rule is only honest if the interval means what it says. Acceptance requires a
committed measurement of the interval's empirical coverage.

Protocol. Take boards of size n in {10, 20, 50} sampled from a labelled group, hold out
assets from the same group as the on-look population, resample the board B times, and record
how often the interval computed from one board contains the score computed from an
independent board drawn from the same group. Report coverage against the stated level, per
board size. Acceptance is that the observed coverage does not fall below the stated level by
more than a stated tolerance, and if it does, the reported level is corrected to the observed
one rather than the claim being kept.

Dataset: `interval-coverage` in `DATASETS.md`. It reuses the sets prepared for ADR-0003 and
adds no new sources. The command that reproduces it is named in that row.

## Alternatives considered

**A single number with an optional detail mode.** Rejected. The three failure modes above
are not edge cases, they are the normal condition of a small hand-picked reference set, so
the detail is the product.

**A tabular output, CSV or similar.** Rejected as the primary format. The report is nested by
nature: assets carry exemplars, boards carry per-reference leverage. A CSV exporter for the
per-asset rows is worth having later as a convenience, derived from the JSON rather than
produced beside it.

**Letting the viewer recompute ties from the numbers.** Rejected, as described above.

## Consequences

The schema has to be settled before either half is built, which is why this record comes
before the engine exists.

Adding a field to the report is a schema change with a version bump, so the report grows
deliberately.

A machine-readable schema file, JSON Schema, is committed alongside the engine, and the
engine validates its own output against it before writing. A report that fails its own
schema is a failure, not a warning.
