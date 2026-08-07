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
    "id": "board hash, defined once in ADR-0005 and computed there",
    "name": "spring-campaign",
    "n_references": 24,
    "n_eff": 17.4,                       // ADR-0005; real, never rounded before use
    "requested_alpha": 0.05,
    "supported_alpha": 0.0556,           // 1/(n_eff_local+1), the finest honourable request
    "built_at": "RFC 3339 timestamp",
    "representation": {
      "style": {"model": "…", "revision": "…", "dim": 768},
      "axes": ["palette", "tone", "composition"]
    },
    "fit": {                             // every parameter that can move a score
      "metric": "cosine", "k": 5,
      "cluster_cut": 0.35, "dup_cut": 0.05,
      "interval": {"method": "loo-jackknife-plus", "replicates": null, "seed": 0}
    },
    "categories": [                      // ADR-0004 rule 2; one entry on a single-look board
      {"category_id": "c0", "n_local": 24, "member_ids": ["…"]}
    ]
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
  "references": [                        // the offline catalogue ADR-0001's viewer needs
    {
      "reference_id": "…", "content_sha256": "…",
      "mime": "image/jpeg", "width": 1600, "height": 1067,
      "thumbnail": {"mime": "image/webp", "width": 256, "height": 171,
                    "data_base64": "…"}
    }
  ],
  "assets": [
    {
      // discriminated union on "state"; a consumer switches on it before reading anything else
      "state": "scored",
      "asset_id": "…", "source": "path or URI",
      "category_id": "c0", "n_local": 24,
      "score": 0.0,
      "interval": {"low": 0.0, "high": 0.0, "level": 0.9,
                   "method": "loo-jackknife-plus"},
      "rank": 1,
      "axes": {"style": 0.0, "palette": 0.0, "tone": 0.0, "composition": 0.0},
      "exemplars": [{"reference_id": "…", "similarity": 0.0}],
      "flags": []
    },
    {
      "state": "abstained",              // no "score" key at all, not a null
      "asset_id": "…", "source": "path or URI",
      "reason": "resolution",            // resolution | multi_modality | far_outlier
      "explanation": "This board has 10 references, so the finest distinction it can express is about 9%, and you asked for 5%.",
      "measurement": {"n_local": 10, "n_eff_local": 8.3,
                      "supported_alpha": 0.1075, "requested_alpha": 0.05},
      "category_id": "c0",
      "axes": {"style": null, "palette": 0.0, "tone": 0.0, "composition": 0.0},
      "exemplars": [{"reference_id": "…", "similarity": 0.0}],
      "flags": ["abstained"]
    }
  ],
  "comparisons": {
    "ties": [["asset_a", "asset_b"]],
    "note": "assets whose score-difference interval spans zero are not distinguishable"
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

**An asset is scored or it abstained, and the two are different shapes.** `state` is the
discriminator. In the `scored` state `score` and `interval` are both required, with `level`
and `method` required inside the interval. In the `abstained` state the `score` key is absent
— absent rather than null, because a null in a numeric field is read as zero by something
eventually, and zero is the most confident possible wrong answer here — and `reason`,
`explanation` and `measurement` are required instead. A consumer that switches on `state`
cannot silently treat a refusal as a low score, which is the failure this shape exists to
make impossible.

**The interval method is named and specified, not labelled.** `method` is
`loo-jackknife-plus`: the interval around an asset's score is formed from the leave-one-out
score deviations computed by refitting the nonconformity rule with each reference in turn
removed from the candidate's category, taking the empirical `level` interval of the resulting
score distribution by the type-7 quantile convention, with ties broken upward. The resampling
unit is the reference, not the pixel and not the duplicate group. The clustering and the
duplicate grouping are refit inside every leave-one-out fold, because holding them fixed
leaks the full board into every fold and narrows the interval by an amount nobody measures.
There are no inner replicates and no seed dependence, which is why `replicates` is null and
the interval is exactly reproducible. An earlier version named a method, `loo-bootstrap`, and
specified nothing about it; a name is not an algorithm, and two implementers reading it would
have produced intervals of different widths, which moves both the coverage gate and every tie.

**Ties are computed by the engine from a paired interval, and listed explicitly.** Two assets
are tied when the interval around their score *difference*, computed by the same jackknife
over the same folds so the two scores share their randomness, contains zero. Overlap of two
marginal intervals is not that test — it is conservative in a way nobody has quantified, and
it is not transitive, so it cannot define groups. The viewer does not recompute any of this,
because then the engine and the viewer would carry two definitions of one thing and they
would drift. `comparisons.ties` is the definition and it is a list of pairs, never a
partition.

**Rank is fully specified, because a rank policy left implicit is a rank policy that differs
per implementer.** Larger score ranks first, since a larger conformal p-value means a better
fit. Equal scores — which are common, the score being a discrete multiple of 1/(n_local+1) —
take competition ranking, so two assets tied at rank 3 are followed by rank 5, and the
tie-break for stable ordering within an equal-score group is the ascending `asset_id`.
Abstained assets carry no rank and are excluded from the ranking entirely rather than sorted
to the end, since ranking a refusal against a score is exactly the comparison the refusal
exists to refuse. Interval-overlap ties do not affect rank; they are reported beside it.

**The score is calibrated against the board's own leave-one-out distribution.** This is what
makes a score comparable across boards of different tightness, and it is stated in the
report rather than assumed by the reader.

**Per-axis values are reported separately and are never blended.** A candidate can match the
palette and miss the grain completely, and that is the useful thing to know. `score` is the
style axis's conformal p-value alone, per ADR-0003; it is not a combination of the axes, and
the classical axes sit beside it as separately readable numbers. An earlier version of this
line described `score` as "a documented function of the axes", which promised a combining
function that was never stated anywhere; ADR-0003 withdraws the promise rather than inventing
the function.

**The report is self-contained, including the images the viewer must show.** ADR-0001
promises a reader can see the reference images a score is closest to, in one HTML file with
no network access at view time, and the report is the only thing crossing that boundary. So
`references[]` carries a hash-addressed catalogue with MIME type, pixel dimensions and an
inline thumbnail, and `assets[].exemplars[].reference_id` resolves into it. Without the
catalogue the viewer's central interaction is unimplementable from a conforming report, which
is a promise the file format cannot keep rather than a rendering detail.

**Provenance pins the model.** A report is reproducible only if the exact weights are named,
so `provenance.model` carries the repository, the revision, and a content hash.

**`flags` is how the report says something is wrong with the question.** A board with almost
no spread, an asset far outside every reference, or fewer references than the estimator needs
are all conditions where a number would still be produced and would still be meaningless.
They get flags, and the viewer is required to surface them.

**The axis vocabulary is defined once and asserted.** `board.representation.axes` lists the
classical axes, and every entry of `assets[].axes` carries the style axis plus those. The
invariant is exact:

```
set(assets[i].axes.keys()) == {"style"} | set(board.representation.axes)   for every i
```

This holds in both asset states. An abstained asset carries the same axis keys with
`axes.style` set to null, because the classical axes are still computed and still worth
reading when the style score is refused — a designer told "this asset is nothing like your
references" is helped by seeing that its palette matched. The invariant is on the key set, so
a null value satisfies it and a missing key does not, which is the intended asymmetry: the
vocabulary is fixed even where a number is unavailable.

The engine asserts it during the self-validation below, and a report that violates it is a
failure rather than a warning. Without the assertion the schema holds two enumerations of one
vocabulary in two places, and they drift in exactly the way the tie rule above predicts:
silently, and in the direction where each side looks correct on its own.

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
board size.

Acceptance is pre-registered in [`eval/thresholds.json`](../../eval/thresholds.json) and is
fixed before the measurement runs: stated level 0.90, 1,000 resamples, board sizes 10, 20 and
50, observed coverage at or above 0.85 for every board size **and for every dataset group
within it**. Pooling across groups only by board size lets a pooled 0.87 sit on top of a
minority group at 0.70 while every report still claims 0.90, so the gate is a worst-group
one and the per-group table is committed beside the pooled figure.

On a shortfall the report's stated level is corrected to the observed coverage rounded **down
to the nearest 0.01**, and that single rule lives here and in `thresholds.json` with the same
words. The two files previously disagreed — one said corrected to the observed coverage, the
other to the nearest 0.05, which turns an observed 0.86 into 0.86 or 0.85 depending on which
file the implementer read.

**Coverage alone cannot pass this criterion, because an interval of [0,1] covers everything
and ties every asset.** Two companion bounds are registered with it: a maximum median
interval width of 0.25 at every board size, and a maximum all-tied rate of 0.50 on the
held-out population. An instrument that certifies coverage without sharpness certifies that
the tool declines to distinguish anything, which is the direction of error this whole record
is built to catch.

The reasoning for 0.85, which is roughly five standard errors below the stated level at this
number of resamples, is in `eval/README.md`. A threshold chosen after seeing the result is a
description of the result, so this one is written down first and any later change to it is a
commit that has to say what was learned.

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
