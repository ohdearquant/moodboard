# Interfaces

This document pins the exact shared contract that every module in `moodboard/` builds
against. It exists because the modules in `moodboard/` were written against each other before
any of them existed: `conformal.py` returns intervals that
`report.py` embeds verbatim, `axes.py` and `encoders.py` both compute palette, tone and
composition features and must not diverge on what those features are, and `abstain.py`
consumes exactly the category partition `conformal.py` produces. A signature decided twice,
once by each side, is a signature that drifts. This document is the one place it is decided.

Every function and type below is a contract: the name, the argument order, the return shape
and the stated behaviour are load-bearing. An implementation may choose its internals freely.
It may not choose a different signature, a different return shape, or a different meaning for
an argument, without changing this document first.

Every threshold-shaped constant named in prose below (0.35, 0.05, k = min(5, n-1), the 1.5 in
the far-outlier rule) is read from `eval/thresholds.json` at runtime by the modules that use
it. Nothing here hard-codes one; each is repeated in prose only so the signature that carries
it is self-explanatory.

## Module map

```
                         ┌─────────────┐
                         │  report.py  │  the schema, the discriminated union,
                         │             │  the self-validator, the JSON Schema file
                         └──────┬──────┘
                                │ types: Interval, Exemplar, ReferenceEntry,
                                │ ScoredAsset, AbstainedAsset, Board, Category
                 ┌──────────────┼──────────────┬───────────────┐
                 │              │              │               │
          ┌──────┴─────┐ ┌──────┴──────┐┌──────┴──────┐ ┌──────┴──────┐
          │  axes.py   │ │ encoders.py ││ conformal.py│ │  abstain.py │
          │ palette/   │ │  Encoder    ││ nonconform- │ │  three      │
          │ tone/comp  │ │  Protocol,  ││ ity, p-value│ │  rules on   │
          │ distances +│ │  Classical- ││ partition,  │ │  Category-  │
          │ feature    │ │  Encoder    ││ n_eff, loo- │ │  Partition  │
          │ vectors    │ │  (uses      ││ jackknife-  │ │  +alphas    │
          │            │ │  axes.py's  ││ plus        │ │             │
          │            │ │  vectors)   ││             │ │             │
          └────────────┘ └──────┬──────┘└──────┬──────┘ └──────┬──────┘
                                 │              │               │
                                 └──────┬───────┴───────┬───────┘
                                        │               │
                                 ┌──────┴──────┐  ┌──────┴──────┐
                                 │  board.py   │  │   cli.py    │
                                 │ board_hash, │  │  build /    │
                                 │ brand.mb    │  │  rank /     │
                                 │             │  │  report     │
                                 └─────────────┘  └─────────────┘
```

`report.py` has no dependency on the other modules. It defines the shapes everything else
fills in, so the arrow runs from the computing modules to the schema, never back.
`encoders.py` depends on `axes.py` for the feature-vector functions the classical encoder
concatenates, and on nothing else. `abstain.py` depends on `conformal.py`'s
`CategoryPartition` and on the nonconformity values `conformal.py` already computed; it never
recomputes them. `board.py` and `cli.py` are the two modules that see the whole set.

## Why `report.py`'s types are dataclasses, not pydantic

Nothing upstream pins this choice, so it is decided here, once, rather than separately in
each module that touches a report type: **plain, frozen, slotted `dataclasses`.** The pinned dependency list is `numpy`, `scipy`, `scikit-image` and
`Pillow` for the computation, plus `pytest` and `ruff` for development. Pydantic would add a
runtime dependency to carry a job the standard library already does for a fixed, fully-typed
schema with no dynamic validation logic beyond one cross-field invariant. That invariant
(the axis-vocabulary check below) does not fit inside a single model anyway, since it compares
an asset's axis keys against a *sibling* object's field, so a hand-written validator function
is needed regardless of which library builds the types. Given that, the smaller dependency
wins. The one place this project does take on a validation dependency is `jsonschema`, for the
JSON Schema file the contract requires; that is a document-shape check against an external,
committed artifact, which is a different job from typing the in-memory objects and is not
better served by hand-rolling a validator for the JSON Schema spec.

`report.py`'s own docstring should say this in one sentence and point here for the reasoning.

## Shared primitive types

These are defined once, in `report.py`, and imported by every module that needs them.
`conformal.py` produces `Interval` values; it does not define its own.

```python
from dataclasses import dataclass
from typing import Literal, Mapping, Any

@dataclass(frozen=True, slots=True)
class Interval:
    low: float
    high: float
    level: float
    method: Literal["loo-jackknife-plus"]

@dataclass(frozen=True, slots=True)
class Exemplar:
    reference_id: str
    similarity: float
```

## `report.py`

### The axis vocabulary

The set of classical axes is data, not a fixed set of struct fields, because ADR-0003 states
that an axis which fails its intervention test "loses its name in the report and appears as an
unlabelled component, or comes out": the vocabulary can shrink at runtime. Today it is exactly
three names.

```python
AXES: frozenset[str] = frozenset({"palette", "tone", "composition"})
```

`board.representation.axes` carries the *current* vocabulary for a given report, which is
`AXES` unless an axis has been dropped, and every asset's `axes` mapping is checked against
that list, not against the constant directly. The constant is what today's callers pass when
building a board.

### Reference catalogue

```python
@dataclass(frozen=True, slots=True)
class Thumbnail:
    mime: str
    width: int
    height: int
    data_base64: str

@dataclass(frozen=True, slots=True)
class ReferenceEntry:
    reference_id: str
    content_sha256: str
    mime: str
    width: int
    height: int
    thumbnail: Thumbnail
```

### Board

```python
@dataclass(frozen=True, slots=True)
class StyleModelInfo:
    model: str
    revision: str
    dim: int

@dataclass(frozen=True, slots=True)
class Representation:
    style: StyleModelInfo
    axes: tuple[str, ...]          # today: the three names in AXES, in that order

@dataclass(frozen=True, slots=True)
class IntervalMethod:
    method: Literal["loo-jackknife-plus"]
    replicates: None                # always None; the method has no inner replicates
    seed: int

@dataclass(frozen=True, slots=True)
class BoardFit:
    metric: Literal["cosine"]
    k: int
    cluster_cut: float
    dup_cut: float
    interval: IntervalMethod

@dataclass(frozen=True, slots=True)
class Category:
    category_id: str
    n_local: int
    member_ids: tuple[str, ...]

@dataclass(frozen=True, slots=True)
class Board:
    id: str                         # board_hash(...) from board.py; computed once, echoed here
    name: str
    n_references: int
    n_eff: float                    # real, never rounded before use
    requested_alpha: float
    supported_alpha: float          # 1 / (n_eff_local + 1) for the category the request lands in
    built_at: str                   # RFC 3339
    representation: Representation
    fit: BoardFit
    categories: tuple[Category, ...]
```

`Board.id` is the field the contract calls "the `board_id` field". It is not computed here.
It is computed once, by `board_hash` in `board.py`, and both the report and the `brand.mb`
artifact echo that one value.

### Board statistics

```python
@dataclass(frozen=True, slots=True)
class Tightness:
    loo_mean: float
    loo_sd: float
    loo_quantiles: Mapping[str, float]   # {"p10": ..., "p50": ..., "p90": ...}

@dataclass(frozen=True, slots=True)
class Leverage:
    reference_id: str
    delta_tightness: float
    rank: int

@dataclass(frozen=True, slots=True)
class BoardStats:
    tightness: Tightness
    leverage: tuple[Leverage, ...]
    flags: tuple[str, ...]
```

### Assets: the scored/abstained discriminated union

Two distinct dataclasses, not one dataclass with optional fields. `state` is the discriminator
a consumer switches on before reading anything else, and each state's field set is exactly
what ADR-0002 requires for that state. `ScoredAsset` has no `reason` field to leave `None`;
`AbstainedAsset` has no `score` field to leave `None`. There is no representable state in which
`score` is present and unusable, which is the property that makes the union worth having.

```python
@dataclass(frozen=True, slots=True)
class ScoredAsset:
    state: Literal["scored"]
    asset_id: str
    source: str
    category_id: str
    n_local: int
    score: float
    interval: Interval
    rank: int
    axes: Mapping[str, float]              # keys checked against AXES invariant, below
    exemplars: tuple[Exemplar, ...]
    flags: tuple[str, ...]

@dataclass(frozen=True, slots=True)
class AbstainedAsset:
    state: Literal["abstained"]
    asset_id: str
    source: str
    reason: Literal["resolution", "multi_modality", "far_outlier"]
    explanation: str                        # a full sentence, see abstain.py below
    measurement: Mapping[str, Any]          # shape depends on reason; see abstain.py
    category_id: str
    axes: Mapping[str, float | None]        # style is None; classical axes are still computed
    exemplars: tuple[Exemplar, ...]
    flags: tuple[str, ...]

Asset = ScoredAsset | AbstainedAsset
```

Serialisation drops the field set of whichever branch was not taken. `score` is absent from
the JSON object for an abstained asset because `AbstainedAsset` has no `score` attribute, not
because a serialiser was told to omit a null. `axes` stays a mapping rather than becoming three
named fields, in both branches, precisely so a future axis removal changes the *value* of
`board.representation.axes` and every `axes` mapping, and changes no type.

### Comparisons and provenance

```python
@dataclass(frozen=True, slots=True)
class Comparisons:
    ties: tuple[tuple[str, str], ...]
    note: str

@dataclass(frozen=True, slots=True)
class ModelProvenance:
    repo: str
    revision: str
    sha256: str

@dataclass(frozen=True, slots=True)
class EngineProvenance:
    name: str
    version: str

@dataclass(frozen=True, slots=True)
class Provenance:
    engine: EngineProvenance
    model: ModelProvenance
    command: str
    seed: int
    created_at: str                          # RFC 3339
```

### The report itself

```python
@dataclass(frozen=True, slots=True)
class Report:
    schema_version: Literal["1.0"]
    board: Board
    board_stats: BoardStats
    references: tuple[ReferenceEntry, ...]
    assets: tuple[Asset, ...]
    comparisons: Comparisons
    provenance: Provenance
```

### The axis-vocabulary invariant, exactly

```python
def validate_axis_vocabulary(report: Report) -> None:
    """Raise ValueError, listing every offending asset_id, unless

        set(asset.axes.keys()) == {"style"} | set(report.board.representation.axes)

    holds for every asset in report.assets, in both states. A null value in `axes["style"]`
    on an abstained asset satisfies the invariant; a missing "style" key does not. Today
    report.board.representation.axes is exactly {"palette", "tone", "composition"}, so today
    this reduces to the exact set equality {"style", "palette", "tone", "composition"} for
    every asset, but the check is written against board.representation.axes rather than
    against the AXES constant, because that field, not the constant, is what a report with a
    dropped axis actually carries.
    """
```

### Self-validation and the JSON Schema file

```python
from pathlib import Path

SCHEMA_PATH: Path = Path(__file__).parent / "schema" / "report_v1_0.schema.json"

def to_json_dict(report: Report) -> dict[str, Any]:
    """Serialise report to a plain JSON-compatible dict matching schema_version "1.0" exactly:
    field names and nesting as in ADR-0002, tuples as JSON arrays, and no key present for a
    field the dataclass in question does not have (score/interval/rank absent on an
    abstained asset; reason/explanation/measurement absent on a scored one)."""

def from_json_dict(data: dict[str, Any]) -> Report:
    """Parse an already schema-valid dict into a typed Report, dispatching each entry of
    data["assets"] to ScoredAsset or AbstainedAsset by its "state" key."""

def validate_report(report: Report, schema_path: Path = SCHEMA_PATH) -> None:
    """to_json_dict(report), validate the result against the JSON Schema at schema_path with
    jsonschema.validate, then validate_axis_vocabulary(report). Raise on any failure. This is
    the function the engine calls on every report before writing it; a failure here is an
    error the caller must not catch and continue past, per the contract's 'a report that
    fails its own schema is an error, not a warning.'"""

def write_report(report: Report, path: Path, schema_path: Path = SCHEMA_PATH) -> None:
    """validate_report(report, schema_path), then write to_json_dict(report) as indented JSON
    to path. There is no path through this module that writes a report which has not just
    passed validate_report in the same call."""
```

The JSON Schema file lives at `moodboard/schema/report_v1_0.schema.json`, is committed, and
encodes the discriminated union as a `oneOf` on two branches keyed by `state`, each branch
listing its own `required` properties exactly as the two dataclasses above list their fields.
The axis-vocabulary invariant is not expressible in JSON Schema, since it is an equality
between two different parts of the same document; that is why `validate_report` runs it as a
second, explicit step rather than folding it into the schema file.

## `encoders.py`

```python
from typing import Protocol, runtime_checkable
from collections.abc import Sequence
import numpy as np

@runtime_checkable
class Encoder(Protocol):
    name: str
    revision: str
    dim: int

    def embed(self, images: Sequence[np.ndarray]) -> np.ndarray:
        """Return an (len(images), self.dim) float32 array, one L2-normalised row per input
        image, in input order. Deterministic for a fixed (name, revision) pair and identical
        input arrays. Must not mutate any array in images."""
```

`name`, `revision` and `dim` are plain attributes, not properties with hidden computation,
because `Provenance.model` and `Representation.style` read them directly when a report is
built. A real CSD, CLIP or DINOv2 encoder implements this Protocol with `dim` fixed by its
published architecture and `revision` naming the pinned weight revision; none of that changes
anything below `Encoder` itself.

### The one concrete implementation this pass builds

```python
class ClassicalEncoder:
    name: str = "classical-v1"
    revision: str = "1"
    dim: int   # = the fixed sum of the three feature-vector lengths below

    def embed(self, images: Sequence[np.ndarray]) -> np.ndarray:
        """For each image: concatenate axes.palette_feature_vector(image),
        axes.tone_feature_vector(image) and axes.composition_feature_vector(image), in that
        order, then L2-normalise the concatenation. Returns (len(images), self.dim) float32.
        Implements the Encoder Protocol above; conformal.py, board.py and report.py see it
        through that Protocol and never import ClassicalEncoder by name."""
```

**A gap left implicit in the specification, closed here.** The classical encoder is specified
as "built from the palette/tone/composition features below, concatenated and L2-normalised",
and each axis as returning "a scalar distance in [0, 1]". A scalar distance
between two images and a fixed-length embedding of one image are not the same object, and nothing
in that module list names a place a fixed-length per-image feature vector lives.
Earth mover's distance, which palette and composition both effectively need, does not in
general reduce to a fixed-length vector under an L2 or cosine comparison, so the vector `axes.py`
hands to the encoder is not the same computation as the distance `axes.py` hands to a caller
comparing two images directly. The resolution: `axes.py` exports both, under different names
(the `_distance` functions below, and the `_feature_vector` functions in the next section),
and both read the same source image data. They are related by measuring the same three
perceptual properties, palette, tone and composition; they are not required to be numerically
consistent with each other beyond that, and no code should assume
`palette_distance(a, b) == 1 - cosine_similarity(palette_feature_vector(a), palette_feature_vector(b))`.

## `axes.py`

### The distance functions, as the contract names them

```python
def palette_distance(image_a: np.ndarray, image_b: np.ndarray) -> float:
    """Dominant colours in CIELAB compared by earth mover's distance, returned in [0, 1] by
    normalising against the maximum transport cost the comparison admits, so values are
    comparable across image pairs. Deterministic: any internal clustering uses a fixed seed."""

def tone_distance(image_a: np.ndarray, image_b: np.ndarray) -> float:
    """A distance between luminance and local-contrast distributions, in [0, 1]."""

def composition_distance(image_a: np.ndarray, image_b: np.ndarray) -> float:
    """Saliency placement and negative-space ratio, compared coarsely, in [0, 1]."""
```

Each takes two HWC images (as `np.ndarray`, uint8 or float in [0, 1]) and returns one scalar.
These are the functions `eval/thresholds.json`'s `axis_intervention` protocol drives: an
intervention is applied to one image, both the intervened and original images are compared
against a fixed third point (or against each other, per the eventual test's own construction),
and the per-axis movements are compared.

### The feature-vector functions, for the encoder

```python
def palette_feature_vector(image: np.ndarray) -> np.ndarray:
    """A fixed-length descriptor for embedding purposes: a quantised CIELAB colour-histogram
    with a fixed bin count, chosen so every image maps to the same-length vector regardless of
    how many distinct dominant colours it has. This is deliberately not the variable-length
    cluster-and-mass representation palette_distance may use internally; a fixed length is
    what makes concatenation and a single L2 normalisation in ClassicalEncoder well-defined.
    Unnormalised; ClassicalEncoder normalises the full concatenation once, not each part."""

def tone_feature_vector(image: np.ndarray) -> np.ndarray:
    """A fixed-length luminance/local-contrast histogram descriptor."""

def composition_feature_vector(image: np.ndarray) -> np.ndarray:
    """A fixed-length descriptor of saliency placement and negative-space ratio, for example a
    coarse fixed-size spatial grid of saliency mass."""
```

`ClassicalEncoder.dim` is fixed once these three lengths are fixed; it is not a free parameter
chosen at construction time.

## `conformal.py`

### Nonconformity

```python
def nonconformity_scores(embeddings: np.ndarray, k: int) -> np.ndarray:
    """embeddings is (n, dim), L2-normalised rows. For every row i, the mean cosine distance
    (1 - cosine similarity) to its k nearest neighbours among the OTHER n - 1 rows. Returns
    shape (n,). The caller computes k = min(5, n - 1) for whichever bag it is scoring and
    passes it in; this function does not choose k. Ties in neighbour selection are broken by
    ascending row index, so the result is deterministic for a fixed embeddings array."""
```

### The symmetric full-conformal p-value

```python
def conformal_p_value(reference_embeddings: np.ndarray, candidate_embedding: np.ndarray) -> float:
    """ADR-0003's construction, exactly. Let n = reference_embeddings.shape[0]. Form the
    augmented bag of n + 1 rows (the n references, then the candidate). Compute
    nonconformity_scores over that bag with k = min(5, n), giving alpha_1 .. alpha_n for the
    references and alpha_cand for the candidate. The references' own alphas are computed
    WITH the candidate present in their neighbour pool, never against each other alone.
    Return

        p = (1 + count(alpha_i >= alpha_cand for i in 1..n)) / (n + 1)

    with ties counted in the numerator. The candidate is never one of the n references passed
    in; this function forms the augmented bag itself and does not accept it pre-formed, so
    there is exactly one place n + 1 is computed."""
```

### Category partition (ADR-0004 rule 2)

```python
@dataclass(frozen=True, slots=True)
class CategoryPartition:
    category_id: str
    candidate_category_members: tuple[int, ...]   # indices into reference_embeddings
    all_categories: Mapping[str, tuple[int, ...]]  # every category_id -> its reference indices

def partition_categories(
    reference_embeddings: np.ndarray,
    reference_content_hashes: Sequence[str],
    candidate_embedding: np.ndarray,
    candidate_content_hash: str,
    cut: float,
    min_category_size: int = 5,
) -> CategoryPartition:
    """Average-linkage agglomerative clustering on the L2-normalised augmented bag (the
    references plus the candidate) under cosine distance, cut at `cut` (0.35 per
    eval/thresholds.json today, read by the caller, not by this function). Ties in merge order
    are broken by the pair whose members have the lexicographically smaller content hash,
    with the candidate's own hash a full participant in that comparison. Categories smaller
    than min_category_size are merged into their nearest surviving category. The candidate is
    clustered as a full member of the bag, never assigned post hoc to a cluster built from the
    references alone, and that distinction is the permutation-symmetry requirement ADR-0004
    states explicitly. all_categories keys every surviving category by a stable id and maps it
    to the reference indices (not including the candidate) that share it; report.py's
    board.categories is built directly from this mapping."""
```

`Category.n_local` in `report.py` for the category the candidate landed in is
`len(partition.candidate_category_members) + 1`, the references sharing the category plus the
candidate itself. This is the same `n_local` `abstain.py`'s resolution check reads.

### Near-duplicate grouping and Kish n_eff (ADR-0005)

```python
def duplicate_groups(reference_embeddings: np.ndarray, cut: float) -> tuple[tuple[int, ...], ...]:
    """Single-linkage agglomerative clustering on the L2-normalised reference embeddings under
    cosine distance, cut at `cut` (0.05 per eval/thresholds.json today, read by the caller).
    Returns groups as tuples of reference indices, covering every reference exactly once. This
    is a SEPARATE clustering from partition_categories and must never be substituted for it:
    sub-looks are far apart (average-linkage, cut 0.35) and duplicates are nearly coincident
    (single-linkage, cut 0.05). One cut cannot serve both purposes."""

def kish_n_eff(group_sizes: Sequence[int]) -> float:
    """Kish's effective sample size: (sum(group_sizes)) ** 2 / sum(s ** 2 for s in
    group_sizes), returned as a real, unrounded float. Equals len(group_sizes) when every
    group has size 1, equals the group count when all groups are the same size."""
```

`board.n_eff` is `kish_n_eff` over `duplicate_groups(reference_embeddings, dup_cut)` on the
whole reference set. The category-local `n_eff_local` an abstained asset's `measurement` field
carries (see `abstain.py`) is the same function over the duplicate groups restricted to the
candidate's own category.

### The loo-jackknife-plus interval

```python
def loo_jackknife_plus_interval(
    category_embeddings: np.ndarray,
    candidate_embedding: np.ndarray,
    k: int,
    level: float,
) -> Interval:
    """The interval around the candidate's score. For each reference in category_embeddings,
    remove it, and on the remaining category recompute the FULL pipeline: re-cluster
    (partition_categories) and re-group duplicates (duplicate_groups) on the reduced bag, then
    recompute conformal_p_value for the candidate against the reduced category. Clustering and
    duplicate grouping are refit inside every fold; holding them fixed leaks the full board
    into the fold and narrows the interval by an amount nobody measures, which is the mistake
    ADR-0002 names explicitly. Take the empirical `level` interval of the resulting per-fold
    score distribution using the type-7 quantile convention, ties broken upward. Returns an
    Interval with method="loo-jackknife-plus" and replicates left to the caller building the
    surrounding IntervalMethod record (this function has no seed dependence and produces an
    exactly reproducible result for a fixed category_embeddings and candidate_embedding)."""

def paired_score_difference_interval(
    category_embeddings: np.ndarray,
    candidate_a_embedding: np.ndarray,
    candidate_b_embedding: np.ndarray,
    k: int,
    level: float,
) -> Interval:
    """The interval around the DIFFERENCE between two candidates' scores against the same
    category, computed by the same leave-one-out folds so the two scores share their
    randomness. This shared-fold construction is what a naive comparison of two intervals
    from independent calls to loo_jackknife_plus_interval does not give, and ADR-0002 requires
    it: marginal-interval overlap is not transitive and cannot define tie groups. Two assets
    are tied exactly when this interval contains zero; comparisons.ties in report.py is built
    by calling this once per pair under consideration, never by inspecting two marginal
    intervals."""
```

## `abstain.py`

```python
@dataclass(frozen=True, slots=True)
class AbstentionVerdict:
    reason: Literal["resolution", "multi_modality", "far_outlier"]
    measurement: Mapping[str, Any]
    explanation: str
```

**Rules 1 and 2 are one check with two names, not two checks.** ADR-0004 defines rule 2's
trigger as "the candidate's own category cannot satisfy rule 1 at the requested alpha", and
rule 1 is itself stated in terms of `n_local`, "the number of references in the candidate's
own category under rule 2, and equals n on a single-look board." Read together, both rules are
the identical arithmetic test applied to the candidate's category size; what differs is only
which reason string is honest to report, and that depends on whether the category is the whole
board. Implementing them as two independently-invoked functions risks two formulas that drift
apart on a boundary case; they are pinned here as one function that chooses the reason.

```python
def check_resolution(partition: CategoryPartition, requested_alpha: float) -> AbstentionVerdict | None:
    """Covers ADR-0004 rules 1 and 2. n_local = len(partition.candidate_category_members) + 1.
    Abstain (return a verdict) when requested_alpha < 1 / (n_local + 1); the comparison is
    strict, so requested_alpha exactly equal to 1 / (n_local + 1) is honoured and this returns
    None. When it abstains: reason is "resolution" if partition.candidate_category_members
    covers every other reference on the board (n_local equals the board's full reference
    count), otherwise "multi_modality". measurement carries {"n_local": ..., "supported_alpha":
    1 / (n_local + 1), "requested_alpha": requested_alpha, "category_id":
    partition.category_id}. explanation is a full sentence built from those numbers, in the
    register of 'This board has 10 references, so the finest distinction it can express is
    about 9%, and you asked for 5%.'"""

def check_multi_modality(partition: CategoryPartition, requested_alpha: float) -> AbstentionVerdict | None:
    """Rule 2, given its own name because ADR-0004 names it separately and a reader looking
    for "the multi-modality check" should find one. Delegates entirely to
    check_resolution(partition, requested_alpha) and returns exactly what that call returns;
    it is not a second implementation of the test, for the reason given above. On a
    single-look board this legitimately returns a "resolution"-reasoned verdict or None,
    never a "multi_modality" one, since there is only one category to check."""
    return check_resolution(partition, requested_alpha)

def check_far_outlier(
    candidate_alpha: float,
    board_reference_alphas: Sequence[float],
) -> AbstentionVerdict | None:
    """ADR-0004 rule 3. board_reference_alphas are the references' own nonconformity values
    from the SAME augmented-bag computation conformal_p_value already ran for this candidate,
    not a separate pass. Abstain when

        candidate_alpha > max(board_reference_alphas) + 1.5 * IQR(board_reference_alphas)

    the Tukey far-outlier rule, 1.5 fixed and read from eval/thresholds.json by the caller if
    it is ever made configurable (it is not today). measurement carries {"candidate_alpha":
    ..., "reference_max": ..., "reference_iqr": ..., "threshold": ...}. explanation states
    that the candidate is nothing like these references, in plain language, never in terms of
    a medium or file-type classification. ADR-0004 withdraws that framing explicitly."""

def evaluate_abstention(
    partition: CategoryPartition,
    requested_alpha: float,
    candidate_alpha: float,
    board_reference_alphas: Sequence[float],
) -> AbstentionVerdict | None:
    """Runs check_resolution first. If it abstains, returns that verdict; an asset that
    cannot be scored at the requested resolution is reported as exactly one reason, not
    additionally checked for being a far outlier. Only if check_resolution returns None does
    this run check_far_outlier and return its result (which may also be None, meaning score
    the asset). This ordering is not stated in ADR-0004 and is decided here: a report needs
    exactly one abstention reason per asset, and resolution is the cheaper, more specific
    check, so it is asked first."""
```

## `board.py`

```python
def board_hash(
    reference_content_hashes: Sequence[str],
    model_repo: str,
    model_revision: str,
    metric: str,
    k: int,
    cluster_cut: float,
    dup_cut: float,
) -> str:
    """ADR-0005's board hash, computed in exactly one place. sha256 hex digest over the
    canonical JSON serialisation (sorted keys, no insignificant whitespace) of

        {"v": 1, "refs": sorted(reference_content_hashes),
         "model": {"repo": model_repo, "revision": model_revision},
         "fit": {"metric": metric, "k": k, "cluster_cut": cluster_cut, "dup_cut": dup_cut}}

    Stable under reordering the references, since refs is sorted before hashing. report.py's
    Board.id and the brand.mb artifact's own id both call this function; neither recomputes
    it independently. Any new fitting parameter that can move a score is added inside "fit"
    and the literal "v" is bumped, both in the same change."""
```

`board.py` also builds the `brand.mb` artifact (the fitted board plus the reference embeddings
needed to score future candidates without re-embedding). Its on-disk format is that module's
own decision; the one constraint pinned here is that the artifact's own board id and
`Report.board.id` are both `board_hash(...)` called with the same arguments, never two values
that happen to agree.

## `cli.py`

`moodboard build`, `moodboard rank` and `moodboard report --html` are the three entry points
named in the contract and in `README.md`. They are thin: `build` calls `encoders.py` to embed
a reference directory, `conformal.py` to partition and fit, and `board.py` to write
`brand.mb`; `rank` loads a `brand.mb`, calls `conformal.py` and `abstain.py` per candidate, and
calls `report.write_report`; `report --html` is out of scope for this pass and raises
`NotImplementedError` with a message naming the separate viewer artifact, per the contract.
No new type is introduced at this layer; every object `cli.py` touches is defined above.
