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

## `judgment.py`: the typed evidence envelope

`moodboard.judgment.v1` is a Studio/evidence-artifact contract. It does not extend either
closed report schema. Its structural authority is the committed Draft 2020-12 schema at
`moodboard/schema/judgment_v1.schema.json`; `judgment.py` validates that raw JSON without
coercion before returning one of six distinct frozen, slotted dataclass branches.

Every document has exactly
`{schema_version,evidence_id,kind,subject,result,authority,evidence_ref}`. `evidence_ref` is a
tagged union: `{kind:"artifact",artifact_id:<SHA-256>}` or
`{kind:"content_ref",content_ref:<BLAKE3-256 ContentRef>}`. A consumer must not infer which
digest algorithm a bare 64-character string meant.

| `kind` | subject | closed result states | registered authority |
|---|---|---|---|
| `intent_eligibility` | one asset id, ContentRef, and route-query occurrence | `eligible`, `excluded`, `not_computed` | `moodboard.intent-route.collection-gate.v1` |
| `source_similarity` | query occurrence and ordered-result artifact | `computed`, `empty`, `not_computed`, `refused` | `moodboard.source-similarity.v1` |
| `board_compatibility` | selectable-output occurrence id | `scored`, `abstained`, `not_computed` | `moodboard.board-compatibility.v1` |
| `constraint_verification` | selectable-output occurrence id | `pass`, `fail`, `not_run` | `moodboard.verifier.raster-structure.v1` or `moodboard.verifier.outside-mask-rgb-exact.v1` |
| `human_comparison` | serve plus exact left/right Moodboard and Khive occurrences | `recorded` with `left`, `right`, `tie`, or `abstain` | `moodboard.preference-judgment.v1` |
| `preference_prediction` | declared pair plus exact left/right output occurrences | `predicted`, `unavailable` | `moodboard.preference.v1` or `moodboard.preference-availability.v1` |

Machine evidence uses
`sha256(UTF8("moodboard.judgment.v1\0") || RFC8785(document-without-evidence_id))`.
Human comparison evidence uses the canonical judgment UUID returned by its serve authority.
Both paths reject non-I-JSON values, unknown schema/kind/subject/result/authority tokens, and
unknown fields at every object depth.

The first registered intent authority is the already measured explicit collection gate. It binds
the immutable corpus manifest, collection field/value, equality operator, policy id, and
`no_ungated_fallback`; its interpretation remains
`structural_routing_control_not_learned_retrieval_quality`. It does not claim the unmerged
text/graph intent-router experiment as part of this contract.

Computed source-similarity rows use the ADR-0014 wire names
`routed_rank`, `source_search_rank`, and `source_similarity`. Routed ranks are contiguous array
order; source ranks are strictly increasing; cosine is non-increasing and preserved at full
precision. The authority fixes `source_image_cosine`, the exact descriptor/model identity,
`preference_applied:false`, and `reranker:null`. It is not a style or board-fit score.

Board results deliberately retain report wire names `score`, `interval`, and `rank`. Their
authority binds the exact report SHA-256, schema version `1.1`, board id, and source report asset
id. Abstention retains the existing `resolution|multi_modality|far_outlier` reason and measured
shape; it never carries a score or rank.

The exact-locality authority binds source/output canonical raster ids and the mask id. A measured
result reports protected pixels, changed pixels, and maximum absolute channel error. Pass means
both difference measurements are zero; fail means both are positive. If structural verification
prevented a comparable output raster, locality is a distinct `not_run` result that binds the
blocking structural evidence id instead of inventing an output-raster id. The structural
verifier is a separate registered authority: it binds the source raster, original provider-output
bytes, and decoder revision. `container_decoded` means bounded inspection succeeded; it does not
claim that a canonical raster exists. `canonical_raster_compiled` and `output_raster_sha256` are
present together only for a passing source-sized RGB output or an otherwise eligible RGB output
whose sole structural mismatch is dimensions. Non-opaque, multi-frame, unsupported-color,
unsafe-decoder, over-limit, and undecodable failures never invent a canonical output-raster id.
An observed `RGB` channel mode does not prove that an embedded ICC/profile contract is supported;
`unsupported_color_contract` may therefore report `output_mode:"RGB"` while canonical compilation
remains false.
Typed structural failures remain distinct from the locality result. A cross-receipt validator
requires the structural failure and blocked locality result to name the same output occurrence,
source raster, and structural evidence id.

Human comparison is blind in v1: the authority must bind both
`preference_probability_shown:false` and `source_rank_shown:false`, the enrolled principal, and
the exact Khive namespace/actor/board/descriptor/feature scope. The choice-to-reason vocabulary
matches the current Khive contract. Prediction preserves the Khive meaning “conditional on a
decisive judgment” and keeps conformal evidence explicitly `not_computed_by_this_verb`. An
unavailable result uses the separate availability authority because no preference-model identity
exists in the `no_active_snapshot` case.

`to_json_dict` revalidates the frozen value before emitting it. Direct construction of a
dataclass therefore cannot bypass the schema or serialize a class/kind mismatch. The full
`selectable_output_occurrence` producer/admission/lineage union is intentionally not defined by
this module; ADR-0014's contract supplies it before ADR-0012 acceptance condition 1 is complete.

## `intent_packet.py`: the frozen generation input

`moodboard.intent-packet.v1` is the immutable, confirmed input to one future generation run. It
is not a mutable Studio draft, a provider request, or a provider receipt, and it does not extend
report v1.0 or v1.1. Its structural authority is the committed Draft 2020-12 schema at
`moodboard/schema/intent_packet_v1.schema.json`, with the localized-operation and verification
policy schemas resolved from the same installed package. Every nested object is closed.

Packet identity is
`sha256(UTF8("moodboard.intent-packet.v1\0") || RFC8785(packet-without-intent_packet_id))`.
The registered localized operation separately identifies its payload under
`moodboard.operation.localized-edit.v1`; the verifier policy identifies its full document minus
`policy_id` under `moodboard.verification-policy.v1`. These identities use the shared
`contracts.py` primitive and are verified independently, so changing a payload while preserving
an old operation digest fails even when a caller recomputes the outer packet id.

The packet binds:

- the creative session, exact board id, descriptor representation id, and fit-policy id;
- the source asset UUID, BLAKE3 ContentRef, source-byte SHA-256, media type, and dimensions;
- the exact instruction, retrieval authority, ordered routed references, original Khive ranks,
  full-precision source-image cosine values, and each reference's provider use;
- requested provider/model, adapter and capability-snapshot identities, a closed options profile,
  source/mask delivery, provider-route and destination policy, model-disclosure policy, and the
  provider's declared idempotency/reconciliation capabilities; and
- the acceptance verifier policy plus the exact references, operation inputs, and dispatch facts
  shown at confirmation.

The first retrieval route is an explicit collection gate with
`empty_result_policy:"no_ungated_fallback"`. Dispatch-ready packets require at least one routed
reference. Array order is authoritative: `routed_rank` is contiguous one-based array order,
`source_search_rank` is strictly increasing, source cosine is non-increasing, and reference,
asset, and ContentRef identities are unique. This is stable filtering, not a learned reranker.

Reference transmission is an explicit enum: `attached_image`, `prompt_context_only`, or
`not_sent`. Attached images bind their byte SHA-256. Prompt-only references bind the compiler
revision and exact text items; they do not imply that image bytes crossed the provider boundary.
The localized operation uses a role-discriminated input union: source images use
`native_input|attached_control`, while masks use
`native_mask|attached_overlay|prompt_only|not_sent`. The registered OpenRouter packet profile uses
`locality_mask:not_sent`; no OpenRouter mask capability or provider run is claimed. The compositor
enforces preservation separately from the generator.

`board_representation_id(board)` hashes exactly `{model_repo,model_revision,model_dim}` under
`moodboard.board-representation.v1`. `board_fit_policy_id(board)` hashes the complete persisted
score-moving fit projection under `moodboard-fit-policy.v1`; it excludes only the provenance label
that names the source of the already-bound far-outlier multiplier. Both are narrower identities
inside the existing whole-board id, not replacements for it.

The first closed options profiles are `moodboard.openrouter-images-options.v1` and the empty
provider-neutral `moodboard.generation-options.none.v1`. The OpenRouter profile is a request
vocabulary, not proof that one endpoint supports every enumerated value; a later immutable
capability artifact and preflight must narrow it before dispatch. The packet has no credential,
header, or cookie fields and records only a non-secret credential-profile UUID. Closure does not
classify arbitrary instruction or prompt text as secret; the Studio/backend boundary must keep and
scan actual credential values out of packet, confirmation, error, and log artifacts.

The confirmation mirror includes the exact idempotency and reconciliation projections, because a
change from at-most-once dispatch to retransmit-safe behavior is itself a renewed-confirmation
event. It proves what the current packet says was confirmed. `default_trust` eligibility
additionally depends on an enrolled, unchanged prior Studio policy and cannot be proven by this
packet alone; the Studio boundary owns that check.

This packet-only layer completes ADR-0014's frozen packet identity and drift detection. It does
not define generation runs, attempts, append-only attempt events, capability artifacts,
normalized provider requests, provider receipts, output occurrences, dispatch/retry behavior, or
actual provider I/O. Those remain separate contracts and PRs. In particular, it does not complete
ADR-0012's `selectable_output_occurrence` union or claim that a provider-backed run occurred.

## `provider_artifacts.py`: closed provider artifacts and P0 relational validation

ADR-0014's provider handoff uses seven independent, closed Draft 2020-12 wire artifacts:

- `moodboard.generation-run.v1` records one user-invoked run UUID;
- `moodboard.generation-attempt.v1` records one Moodboard-initiated provider-call UUID;
- `moodboard.generation-attempt-event.v1` records one event intended for a later append-only
  attempt stream;
- `moodboard.provider-capability-snapshot.v1` records one declared capability projection and its
  evidence reference;
- `moodboard.normalized-provider-request.v1` records the secret-redacted request projection
  proposed for dispatch;
- `moodboard.provider-receipt.v1` records response evidence without inventing absent provider
  claims; and
- `moodboard.output-occurrence.v1` records one output slot carrying a passing producer-supplied
  media-validation record.

The schemas live in `moodboard/schema/` and use an offline registry that includes their
intent-packet dependencies. Nested objects are closed and bounded. Python values returned by
`from_json_dict` are frozen and slotted, and `to_json_dict` revalidates direct dataclass
construction. This is in-process value immutability; this module does not provide append-only or
immutable durable storage.

Run and attempt ids are caller-supplied canonical UUIDs because deliberately repeating a packet
creates new occurrences. Event, capability, normalized-request, and receipt ids are
domain-separated RFC 8785 SHA-256 identities over their complete documents minus the identity
field. The request key binds run, attempt, packet, adapter, and normalized-request identities.
Output identity uses the ADR-fixed `{attempt_id,output_index}` projection, with a zero-based index.
Within one supplied bundle, the relational validator rejects duplicate or conflicting output
keys; persistent uniqueness remains a storage/runtime responsibility.

The supported producer surface is deliberately small. `seal_provider_artifact` copies a draft,
derives the registered identity for an event, capability, normalized request, receipt, or output,
then validates and freezes it. It rejects prefilled identities and never signs run/attempt UUIDs.
`build_normalized_request_ref` derives the exact SHA-256, BLAKE3 ContentRef, and byte count of the
canonical request artifact. `compute_provider_request_key` is the only public implementation of
the request-key projection. Network dispatch, timestamps, UUID allocation, retry policy, and
storage remain outside these helpers.

`validate_artifact_bundle` validates one bounded, complete P0 response/output bundle: exactly one
run, one ordinal-one attempt, one capability snapshot, one normalized request, one receipt, the
required event records, and every recorded output. It is not a general validator for preflight
failures, cancellation, terminal `outcome_unknown`, reconciliation, retries, fallbacks, or
multi-attempt run history. A complete bundle may contain one `outcome_unknown` only when a later
`response_received` resolves it before the terminal event.

Within that narrow bundle it checks:

- packet/run/attempt equality for requested provider/model, adapter, destination, route policy,
  options, operation-input delivery, and ordered reference use;
- that the declared capability projection authorizes the recorded count, options, input modes,
  roles, output media bounds, and shared source-plus-reference image budget;
- the SHA-256, BLAKE3 ContentRef, and byte count of the canonical RFC 8785 serialization of the
  normalized-request artifact;
- the structural consistency of the closed, secret-redacted OpenRouter body projection, including
  endpoint, method, declared `ContentPartImage` field paths, reference authority/order, route pin,
  and disabled fallback; transport mode and transport-value SHA-256 remain recorded claims for the
  adapter's wire-byte-equivalence check;
- equality among recorded receipt/output digests, byte counts, media facts, admission, and packet
  lineage; and
- exactly the prepared, submitted, optional single resolved-outcome-unknown, response-received,
  and terminal event trace plus the receipt/output ids required by this complete P0 bundle.

These checks do not observe the actual network request or response bytes. The provider adapter
must separately prove that the dispatched wire request corresponds to the normalized projection.
The OpenRouter P0 arm accepts `locality_mask.delivery_mode:not_sent` only; a native mask or overlay
requires a separately registered transport contract. Local preservation is therefore enforced by
the later deterministic compositor and exact verifier, not attributed to OpenRouter.

The OpenRouter capability arm records a discovery endpoint, declared capability fields, and a
`{ContentRef, SHA-256, byte_count}` reference to a private discovery-response artifact. This
validator neither loads nor rehashes those discovery bytes and does not prove that the declared
fields were extracted from them. A listed route is capability evidence, not proof that it served a
response.

When an API omits actual-model, upstream-route, or media-type claims, the receipt can preserve
`actual_model:undisclosed`, `upstream_route:unknown`, and `media_type_claim:null`; it does not copy
requested values into actual fields. An attested model or upstream tag that conflicts with the
confirmed route makes otherwise valid output bytes ineligible. A receipt that claims attestation
unsupported by the capability invalidates the bundle. Raw response bytes are either explicitly
retained by immutable reference or explicitly marked `not_retained` with a closed reason. Actual
response parsing and provenance extraction belong to the provider adapter.

This change implements the provider-artifact schema and identity portion of ADR-0014 acceptance
condition 1 and tests relational prerequisites for conditions 6 and 7. It does not by itself prove
dispatched request-byte equality, inspect or decode provider output bytes, enforce
validation-before-success in a durable transition system, or establish terminal immutability.
Those portions remain for the adapter, media verifier, durable terminal-state, and storage changes.

## `attempt_state.py`: pure provider-attempt transition reduction

`reduce_attempt_events(attempt, events)` validates an immutable generation-attempt descriptor and
reduces its supplied append-order `moodboard.generation-attempt-event.v1` history. It does not sort
events: sequences must already be exactly contiguous from one in the order supplied. Empty history
is valid before the initial `prepared` event, and the longest legal v1 history is bounded to five
events.

The reducer implements the closed ADR-0014 graph:

```text
prepared -> failed | cancelled | submitted
submitted -> response_received | failed | cancelled | outcome_unknown
outcome_unknown -> response_received | failed | cancelled
response_received -> succeeded | failed
```

`succeeded`, `failed`, and `cancelled` are terminal. An `outcome_unknown` reconciliation that
remains unknown does not append a self-transition. Cancellation directly after `prepared` requires
`local_pre_dispatch`; cancellation after `submitted` or `outcome_unknown` requires
`provider_confirmed_no_output`. The closed `failure_stage` and `cancellation_stage` values remain
evidence rather than an unwritten predecessor-state policy. A non-null provider handle may first
appear after a null handle, but it cannot change within one attempt.

The frozen result exposes current state, terminal status, last event/time, retained provider
handle, and `(attempt_id, head_event_id, next_sequence)`. That tuple is only a precondition token
for a later durable compare-and-append operation. This module neither creates a dispatch claim nor
makes a write atomic. It also does not prove that a topological `succeeded` event references an
eligible receipt/output set; provider relational validation and the later durable terminal gate own
that check.

This change supplies the pure transition and terminal-immutability portion of ADR-0014 acceptance
condition 3. Durable append-only storage, reconciliation I/O, dispatch idempotency, retry/fallback
policy, and crash/race tests remain separate concerns.

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
    axes: tuple[str, ...]  # today: the three names in AXES, in that order


@dataclass(frozen=True, slots=True)
class IntervalMethod:
    method: Literal["loo-jackknife-plus"]
    replicates: None  # always None; the method has no inner replicates
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
    id: str  # board_hash(...) from board.py; computed once, echoed here
    name: str
    n_references: int
    n_eff: float  # real, never rounded before use
    requested_alpha: float
    supported_alpha: float  # 1 / (n_eff_local + 1) for the category the request lands in
    built_at: str  # RFC 3339
    representation: Representation
    fit: BoardFit
    categories: tuple[Category, ...]
```

`Board.id` is the field the contract calls "the `board_id` field". It is not computed here.
It is computed once, by `board_hash` in `board.py`, and both the report and the `brand.mb`
artifact echo that one value.

`report_v1_0`'s `BoardFit` is a frozen compatibility projection, not the complete persisted
runtime policy. Report v1.1 adds a distinct `BoardFitV1_1` whose closed shape also requires
configured `k_cap`, `min_category_size`, `interval_level`, `far_outlier_iqr_multiplier`, and
`far_outlier_iqr_multiplier_source`. Rank copies those values from verified `brand.mb`; it never
reloads mutable defaults. The source string is provenance and does not enter `board_id`, while
every numeric field does. The v1.0 type and schema remain unchanged and valid forever.

### Board statistics

```python
@dataclass(frozen=True, slots=True)
class Tightness:
    loo_mean: float
    loo_sd: float
    loo_quantiles: Mapping[str, float]  # {"p10": ..., "p50": ..., "p90": ...}


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
    axes: Mapping[str, float]  # keys checked against AXES invariant, below
    exemplars: tuple[Exemplar, ...]
    flags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AbstainedAsset:
    state: Literal["abstained"]
    asset_id: str
    source: str
    reason: Literal["resolution", "multi_modality", "far_outlier"]
    explanation: str  # a full sentence, see abstain.py below
    measurement: Mapping[str, Any]  # shape depends on reason; see abstain.py
    category_id: str
    axes: Mapping[str, float | None]  # style is None; classical axes are still computed
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
    created_at: str  # RFC 3339
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

### Report v1.1 additive types

Report v1.0 is not widened. Version 1.1 has distinct typed objects, and reuses only the shared
v1.0 primitives whose wire paths, types, and meanings are unchanged:

```python
@dataclass(frozen=True, slots=True)
class CandidateImage:
    content_sha256: str
    mime: str
    width: int
    height: int
    thumbnail: Thumbnail


@dataclass(frozen=True, slots=True)
class AxisDefinition:
    axis_id: str
    label: str
    value_kind: Literal["conformal_p_value", "normalized_distance"]
    direction: Literal["higher_is_better_fit", "lower_is_closer"]
    aggregation: Literal["full_conformal_category", "mean_over_exemplars"]
    availability: Literal["scored_only", "all_assets"]
    uncertainty: Literal["asset_interval", "none"]
    method: AxisMethod


@dataclass(frozen=True, slots=True)
class BoardFitV1_1:
    metric: Literal["cosine"]
    k: int
    k_cap: int
    cluster_cut: float
    dup_cut: float
    min_category_size: int
    interval_level: float
    far_outlier_iqr_multiplier: float
    far_outlier_iqr_multiplier_source: str
    interval: IntervalMethod


@dataclass(frozen=True, slots=True)
class RepresentationV1_1:
    style: StyleModelInfo
    axes: tuple[str, ...]
    axis_definitions: tuple[AxisDefinition, ...]
```

`BoardV1_1` has the same board-level fields as `Board`, with `RepresentationV1_1` and
`BoardFitV1_1` at the two widened paths. `ScoredAssetV1_1` and `AbstainedAssetV1_1` keep their
respective v1.0 branch fields and each adds the required `image: CandidateImage`. The branches
remain separate dataclasses; abstained assets still cannot represent a score.

```python
@dataclass(frozen=True, slots=True)
class EngineSourceProvenance:
    source_repository: str
    source_revision: str
    source_dirty: bool


@dataclass(frozen=True, slots=True)
class ProvenanceV1_1:
    engine: EngineProvenanceV1_1  # source fields serialize flat in engine, all present or absent
    model: ModelProvenance
    command: str  # exactly shlex.join(argv)
    argv: tuple[str, ...]
    seed: int
    created_at: str
    schema: SchemaProvenance


@dataclass(frozen=True, slots=True)
class ReportV1_1:
    schema_version: Literal["1.1"]
    board: BoardV1_1
    board_stats: BoardStats
    references: tuple[ReferenceEntry, ...]
    assets: tuple[AssetV1_1, ...]
    comparisons: Comparisons
    provenance: ProvenanceV1_1
```

`axis_definitions_for(axes)` is the sole constructor for the fixed ADR-0008 definitions. It
returns `style` first and then the registered classical definitions in declared order. A v1.1
report requires exactly `min(3, len(references))` distinct, resolving exemplars per asset, ordered
by descending similarity and then reference-catalogue position. Candidate and reference thumbnails
are strict base64 JPEG, PNG, or WebP whose decoded type and dimensions must match their metadata.

### The axis-vocabulary invariant, exactly

```python
def validate_axis_vocabulary(report: Report | ReportV1_1) -> None:
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

SCHEMA_PATH_V1_0: Path = Path(__file__).parent / "schema" / "report_v1_0.schema.json"
SCHEMA_PATH_V1_1: Path = Path(__file__).parent / "schema" / "report_v1_1.schema.json"
SCHEMA_PATH: Path = SCHEMA_PATH_V1_0  # frozen compatibility alias


def to_json_dict(report: Report | ReportV1_1) -> dict[str, Any]:
    """Serialize either exact minor without projecting, stripping, or relabeling fields."""


def from_json_dict(data: dict[str, Any]) -> Report | ReportV1_1:
    """Inspect the complete version first, validate against its exact schema, and construct
    that version's typed object. Raise UnsupportedSchemaVersionError for every unnamed version
    before interpreting payload content."""


def validate_report(report: Report | ReportV1_1, schema_path: Path | None = None) -> None:
    """Select the schema from the typed version, validate it, then enforce shared and
    version-specific cross-field assertions. A failure is an error, not a warning."""


def write_report(
    report: Report | ReportV1_1,
    path: Path,
    schema_path: Path | None = None,
    *,
    candidate_inputs: Sequence[CandidateImageInput] | None = None,
) -> None:
    """Validate before writing. Version 1.1 additionally requires independent original-input
    identity facts for every candidate; these must equal image hash, MIME, width, and height."""
```

Both JSON Schemas are committed. The v1.0 path remains exact and closed forever. Version dispatch
names only `1.0` and `1.1`; unknown minor versions do not receive a best-effort projection. Both
schemas encode the discriminated union as a `oneOf` keyed by `state`. Cross-object equalities,
reference resolution, exemplar ordering, decoded thumbnail truth, command/argv equality, and the
v1.1 schema-byte hash run as explicit second-stage validation because JSON Schema cannot express
them. `report_schema_sha256(SCHEMA_PATH_V1_1)` is pinned in report provenance and in a golden test.

Every path-based report consumer first calls
`read_report_bytes(path: Path) -> bytes` under `REPORT_MAX_BYTES = 128 * 1024 * 1024`. The helper
uses file size as a preflight, refuses an oversized report before opening it, then reads no more
than the ceiling plus one byte so a file that grows after preflight still cannot allocate without
bound. The CLI validator and offline HTML inliner share this exact helper. This is a transport and
availability bound only; it does not change report values or scoring semantics.

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

### The offline implementation and the opt-in Khive implementation

```python
class ClassicalEncoder:
    name: str = "classical-v1"
    revision: str = "2"
    dim: int  # = the fixed sum of the three feature-vector lengths below

    def embed(self, images: Sequence[np.ndarray]) -> np.ndarray:
        """For each image: concatenate axes.palette_feature_vector(image),
        axes.tone_feature_vector(image) and axes.composition_feature_vector(image), in that
        order; L2-normalise each nonzero block; then L2-normalise the concatenation. Returns
        (len(images), self.dim) float32.
        Implements the Encoder Protocol above; conformal.py, board.py and report.py see it
        through that Protocol and never import ClassicalEncoder by name."""
```

ADR-0011 adds a second implementation without changing `Encoder`:

```python
class KhiveLatticeEncoder:
    name: str  # "khive:" plus the descriptor's model_name
    revision: str  # "<descriptor fingerprint>+moodboard-khive-adapter-v3"
    dim: int  # descriptor dimensions
    descriptor: VisualDescriptor
    last_assets: tuple[KhiveAsset, ...]

    def embed(self, images: Sequence[np.ndarray]) -> np.ndarray:
        """Encode each array as a deterministic RGB8/RGBA8 PNG rendition, submit ordered
        moodboard.ingest operations, and return only a complete validated float32 matrix."""
```

Construction first calls `moodboard.model()` and validates the closed
`moodboard.visual-descriptor.v1` object. Its SHA-256 `fingerprint` is recomputed over compact,
recursively key-sorted JSON excluding `fingerprint` and `model_key`; `model_key` must then equal
`moodboard_<fingerprint>_<dimensions>`. The v1 descriptor pins `lattice-embed` 0.9.0, Qwen3.5 visual
token mean pooling, sRGB/Lanczos preprocessing padded to the model's 32-pixel spatial-merge
alignment at a maximum side of 448, the prompt identity, checkpoint SHA-256, dimension and L2
normalisation. Root and nested key sets are closed. Every ingest repeats the same descriptor,
and any drift, malformed vector, non-finite value, dimension mismatch, or norm outside `1e-5`
of one fails the complete call without returning a partial matrix.

`embed_source_assets(...)` is the CLI-only stronger seam. It rereads each path, verifies its
SHA-256 against the value already used for `board_hash`, and submits the exact file bytes and
MIME. `embed(...)` cannot recover source bytes from an array, so its returned `KhiveAsset`
metadata labels the BlobStore object `canonical-png-rendition`; the path-aware call labels it
`source-bytes`. RGBA stays RGBA so the descriptor-pinned Khive matte owns compositing. The v1
path-aware source contract admits PNG, JPEG, and WebP and rejects another MIME before dispatch.
The adapter revision and a byte-exact RGBA PNG golden freeze array conversion as part of the
encoder identity; a conversion or persistence-scope change must bump that revision. Revision 2
retains revision 1's byte-exact PNG rendition and adds operation-level storage namespace binding.
Revision 3 retains both and partitions globally admitted, globally byte-deduplicated unique ingests
into ordered groups of at most eight per `kkernel` process. That process boundary is
identity-bearing because it changes the durable failure scope even though returned vector math is
unchanged.
The encoder is internal and
byte-frozen: filter-0 scanlines, a fixed zlib wrapper, manually framed DEFLATE stored blocks,
fixed PNG chunks/CRC, and Adler-32. It does not delegate canonical byte identity to a ranged
Pillow or compressor implementation.

`moodboard/khive.py` is an application adapter, not a general SDK. Every invocation uses
`kkernel exec --ops-file ... --save-file ... --strict --serial` with explicit `--actor`, identical
`--expect-actor`, and explicit `--namespace`. The CLI namespace remains execution attribution;
the same exact configured value is also injected into the closed `args` object of every supported
Moodboard operation and the narrow bare `create` board publication because those fields select
durable storage, retrieval, and learning scope. An optional config path is passed as `--config`;
when absent, Khive's normal environment/discovery fallback remains active. It verifies the saved JSONL manifest, byte
checksum, row count, per-row tool/order, success flag, and strict JSON before releasing any
result. Image base64 never appears in argv.

Its retrieval surface is equally narrow and typed:

```python
@dataclass(frozen=True)
class KhiveSearchRequest:
    asset_id: str
    top_k: int | None = None


@dataclass(frozen=True)
class KhiveSearchHit:
    asset_id: str
    score: float
    rank: int
    name: str
    content_ref: str


@dataclass(frozen=True)
class KhiveSearchResult:
    query_asset_id: str
    descriptor: VisualDescriptor
    experimental: Literal[True]
    hits: tuple[KhiveSearchHit, ...]


class KhiveClient:
    def search(self, asset_id: str, top_k: int | None = None) -> KhiveSearchResult: ...
```

`top_k` is absent for the pack default or a plain integer in `[1,100]`. Query and hit asset ids
are bare canonical UUIDs. Search discovers and validates the model descriptor before the first
query; the response descriptor must match it exactly. The result and each hit are closed objects.
Asset-id lookup remains global under Khive's identity contract while vector candidates are
selected from the operation namespace. A globally known query asset in another namespace is
therefore a successful search with zero hits, not a missing-query protocol failure.
Hits are self-excluded, unique by asset id, ranked contiguously from one, and ordered by
non-increasing finite cosine similarity in `[-1,1]`. Names are required non-empty UTF-8 strings
within the pack limit, and content references are raw 64-character lowercase BLAKE3 hex. The
`moodboard retrieve` CLI prints those ranked locators and calls the value `cosine`; it does not
label retrieval as style fit, coherence, or a calibrated score. It renders each name as a JSON
string so control characters cannot forge terminal rows.

ADR-149 preference export remains a separate opt-in on `rank`. The canonical producer consumes the
candidate embedding, complete reference matrix, candidate-local member indices/effective support,
conformal score and interval, and three classical distances while that geometry is still in memory.
It never reconstructs missing values from the serialized report. Only scored candidates are
eligible, and fewer than two fail rather than fabricating a pairwise pool. After `write_report`
validates and atomically publishes the report, its exact bytes are SHA-256 hashed; only then does the
client issue its one narrow bare `create` operation for a live `artifact/moodboard`. Khive's
registered verb name is canonical here; the adapter has no compatibility fallback to the
unregistered `kg.create` spelling.
The entity's closed
properties bind board id, model key, descriptor fingerprint, report digest, feature schema, and
producer identity. A strict response parser requires the exact Entity wire shape, matching namespace
and properties, and null deletion/merge/content lifecycle fields.

The resulting `moodboard.preference-feature-artifact.v2` stores that board entity UUID and a
domain-separated scope digest over the board, descriptor, report, producer, schema, and independent
candidate-pool digest. Candidate rows bind canonical Khive asset UUIDs and BlobStore content refs.
The artifact writer is atomic. `--preference-features-output` is rejected with the classical encoder
or when it aliases the report or `brand.mb`, before an encoder is constructed or Khive is invoked.
This handoff does not itself call `serve`, collect judgments, or train a preference model.

The recorded replay adds three narrow typed batch items and corresponding client methods; this is
still not a generic Khive SDK:

```python
@dataclass(frozen=True)
class KhiveServeRequest:
    candidates: Sequence[Mapping[str, Any]]
    candidate_pool_sha256: str
    policy_revision: str = "moodboard-demo-pairs-v1"
    pair_propensity: float | None = None


@dataclass(frozen=True)
class KhiveJudgmentRequest:
    serve_id: str
    left_result_occurrence_id: str
    right_result_occurrence_id: str
    choice: Literal["left", "right", "tie", "abstain"]
    reason_code: str | None = None
    response_ms: int | None = None


@dataclass(frozen=True)
class KhivePreferenceRequest:
    left: Mapping[str, Any]
    right: Mapping[str, Any]
```

`batch_serve`, `batch_judge`, and `batch_preference` reject empty input, validate the complete
ordered input before starting `kkernel`, submit only their one fixed Moodboard verb, and return one
typed result per input row after the existing manifest checksum, tool-order, row-count, and success
checks. Every returned preference scope must bind the resolved Khive actor exactly: an explicitly
configured request actor is represented as `actor_kind="actor"` and `actor_id` equal to the complete
requested actor string (for example `lambda:showcase-policy-simulated`). The adapter neither
splits the actor string on `:` nor accepts a prefixed or reconstructed compatibility spelling.
The adapter always requests `kkernel exec --serial`: ops-file parsing may chunk the input,
but physical pack execution is sequential and preserves occurrence-dependent ordering without
reader contention. The outer `--presentation verbose` flag selects lossless response rendering;
inside each `moodboard.serve` argument bag, the distinct `exposure` object records the experiment's
`preference_probability_shown`, `source_rank_shown`, and optional served-model provenance. Neither
`presentation` nor `presentation_per_op` is a verb argument: Khive reserves both names for the
request envelope and rejects them during typed parsing.

Serial execution is ordered, not atomic or fail-fast. Khive validates the complete typed ops-file
snapshot before its first dispatch, so a structural error such as an envelope-reserved argument
makes zero handler writes. After that preflight, an individual handler failure may still leave a
successful prefix durable, later rows may execute, and `--strict` reports the failed batch with a
nonzero process status after row execution. A failed judgment batch can therefore leave judgments
whose exact retry returns `created=false`; the replay recovers only in fresh
isolated state rather than pretending to roll back or accepting reused rows. Their singleton
counterparts delegate to a one-item batch. Serve and judgment remain separate batches because
displayed-side labels depend on Khive's returned randomized occurrence identities.

For each ingest, it also recomputes the Khive BlobStore v1 BLAKE3-256 `content_ref` over the
submitted bytes and requires the same row to return that value. This detects swapped successful
rows even though every operation has the identical `moodboard.ingest` tool name.
Byte-identical inputs are deduplicated across the complete logical call, submitted once, and fanned
back to every original position. First-occurrence name/caption wins; later occurrence metadata
carries `created=False`.
One logical call admits at most 64 total asset occurrences and 32 MiB of decoded bytes across
those occurrences before deduplication. Source reads are bounded to remaining budget plus one;
array geometry is checked against exact canonical-PNG size before encoding. All input admission,
byte production, ContentRef computation, and global deduplication finish before the first ingest
process. Unique operations are then submitted in stable consecutive groups of at most eight, so one
serial ops batch cannot consume the entire bounded Khive request-read deadline. The process groups
do not reset either logical-call budget.

Every returned group is fully validated before the next process starts. A failed process or invalid
result stops later groups, leaves `last_assets` empty, and returns no matrix. This is not a
transaction: operations commit independently, so a validated earlier group or a successful prefix
of the failing group may remain durable in Khive. Same-state retry converges on the existing
namespace-plus-ContentRef asset UUID with `created=False`, while inference and indexing run again;
the adapter performs no compensating deletion. Khive-mode CLI loading also applies the same
occurrence/source-byte gate before decode, caps either side at 8192, and retains at most 256 MiB of
matte-composited RGB arrays. Classical loading is unchanged.

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

## `khive.py`: the Moodboard pack wire contract

`moodboard/khive.py` is an application adapter, not a general Khive SDK: it knows how to submit
one small, fixed set of Moodboard operations through `kkernel exec` and how to prove that the
saved result file has exactly one successful, ordered row per operation. Everything else about
Khive, every other verb, every other pack, is out of scope for this client and out of scope for
this section. What follows records the exact request and response shapes this adapter emits and
consumes, so that a second client aimed at the same pack can be checked against them field by
field.

### The verbs this adapter emits

Every operation travels as one JSON object per line in an ops file, `{"tool": "<name>", "args":
{...}}`, with `args` written key-sorted and compact. The adapter emits exactly these tool names,
and no others:

| tool | emitted by |
| --- | --- |
| `create` | `KhiveClient.publish_board` (publishes the one `artifact/moodboard` entity) |
| `moodboard.model` | `KhiveClient.model` |
| `moodboard.ingest` | `KhiveClient.ingest` (arguments built by `KhiveLatticeEncoder`) |
| `moodboard.search` | `KhiveClient.search` |
| `moodboard.serve` | `KhiveClient.serve` / `KhiveClient.batch_serve` |
| `moodboard.judge` | `KhiveClient.judge` / `KhiveClient.batch_judge` |
| `moodboard.train_preference` | `KhiveClient.train_preference` |
| `moodboard.preference` | `KhiveClient.preference` / `KhiveClient.batch_preference` |

For all eight of these tools, the adapter also writes its own configured `namespace` into
`args["namespace"]` before submission (rejecting the call outright if a caller already put a
different namespace value there). The `--namespace` command-line flag is separate: it is Khive's
execution-attribution namespace, and the adapter always sets it to the same configured value,
but the two are bound independently by the pack. `moodboard.model` and `moodboard.search` are
documented above in the `encoders.py` section together with the descriptor and search-hit shapes
they carry; this section does not repeat those fields, only the six verbs ADR-149's preference
loop adds plus the transport and error rules shared by every verb.

### The `--ops-file` / `--save-file` transport

Every call, whether it submits one operation or a batch, runs:

```
kkernel exec [--config CONFIG] --ops-file OPS --save-file SAVE \
  --namespace NAMESPACE --actor ACTOR --expect-actor ACTOR \
  --presentation verbose --output-format json --serial --strict
```

`--config` is only present when the client was constructed with one; otherwise Khive's normal
environment/discovery fallback applies. `--serial` makes physical execution order match the
submitted order. `--strict` makes a failed row change the process exit status. No image or other
payload ever appears in `argv`; both the operations and the results travel through private
temporary files.

On success, `kkernel exec` must print exactly one non-blank JSON line to stdout: the save
manifest. The adapter requires at least these keys (extra manifest keys are read and ignored):

```json
{
  "path": "/absolute/path/that/resolves/to/the/requested/--save-file",
  "rows": 3,
  "checksum": "<sha256 hex of the exact bytes written to --save-file>",
  "summary": {"total": 3, "succeeded": 3, "failed": 0, "aborted": 0}
}
```

`path` must be absolute and must resolve (`Path.resolve(strict=True)`) to the same file as the
requested `--save-file`; a symlink or a same-directory alias both fail this check. `rows` must
equal the number of operations submitted. `checksum` must equal the SHA-256 of the bytes actually
read back from that file. `summary.succeeded` and `summary.total` must both equal the submitted
row count, and `summary.failed` and `summary.aborted` must both be `0`; if either is nonzero the
adapter raises with that count reported, even if the process exit code was `0`.

The save file itself is JSONL, one row per submitted operation in the same order:

```json
{"tool": "moodboard.serve", "ok": true, "result": {...}, "usage": {}}
```

The number of lines must equal the number of submitted operations, with no blank lines. For each
row, `tool` must equal the tool name of the operation at that position (this is how the adapter
detects a reordered or substituted row even though a batch can contain the same tool name more
than once), `ok` must be `true`, an `error` key must not be present when `ok` is `true`, an
`aborted` key must not be present unless it is exactly `false`, and a `result` key must be
present. The value under `result` is the per-verb response shape documented below.

### Error semantics: what fails closed

Every check above is enforced before any result is handed back to the caller, and any single
violation, anywhere in the batch, discards the entire batch: the adapter either returns one
validated result per submitted operation, or it raises and returns nothing. Concretely:

* A nonzero `kkernel exec` exit status raises immediately with the exit code and any stderr text
  attached; the manifest and save file are not read at all.
* Stdout that is not exactly one non-blank JSON line, or that does not parse as a JSON object,
  raises before the save file is opened.
* Every manifest and row check above (`path`, `rows`, `checksum`, `summary`, per-row `tool`/`ok`/
  `error`/`aborted`/`result`) raises `moodboard.khive.KhiveProtocolError` on the first violation
  found.
* A row that reports `"ok": false` raises with whatever the row's `error` field contains (or
  `"no error detail"` if that field is absent); this is the one place a per-operation failure
  message from Khive reaches the caller.

None of this makes the underlying Khive execution itself atomic. `--strict` and `--serial`
together guarantee the *manifest* Python sees is consistent and ordered, not that a rejected
batch left no durable effect: Khive validates the complete ops file up front, so a structural
error (for example, an operation carrying the envelope-reserved `presentation` argument) causes
zero handler writes, but once execution starts, an individual handler failure can still leave an
earlier successful prefix durable in Khive while the adapter still raises and reports nothing to
the caller. A retried `moodboard.judge` call after such a partial failure can come back with
`created: false` because the judgment already exists; the adapter does not attempt to detect or
undo a partial prior batch.

### `moodboard.serve`

Request `args` (after namespace binding), for one candidate pair:

```json
{
  "board_entity_id": "<uuid>",
  "board_id": "<64 lowercase hex>",
  "descriptor": {"model_key": "moodboard_<fingerprint>_<dims>", "descriptor_fingerprint": "<64 lowercase hex>"},
  "feature_schema_id": "f691fc73bf9a50d72157e21601fa579caa707bf2c448df546c63e915b4e42175",
  "source_report_sha256": "<64 lowercase hex>",
  "candidates": [
    {"state": "scored", "asset_id": "<uuid>", "content_ref": "<64 lowercase hex>", "source_rank": 1, "features": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]},
    {"state": "scored", "asset_id": "<uuid>", "content_ref": "<64 lowercase hex>", "source_rank": 2, "features": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]}
  ],
  "selection": {"policy_revision": "moodboard-demo-pairs-v1", "candidate_pool_sha256": "<64 lowercase hex>"},
  "exposure": {"preference_probability_shown": false, "source_rank_shown": true},
  "namespace": "<client namespace>"
}
```

`candidates` always has exactly two entries with distinct `asset_id` and distinct `content_ref`;
each `features` array has exactly ten finite numbers in `[0, 1]`, in the fixed order named below
under "the preference feature artifact". `selection.policy_revision` is a trimmed, non-empty
string of at most 128 UTF-8 bytes (the adapter's default is `moodboard-demo-pairs-v1`).
`selection` gains an optional `pair_propensity` key, a finite number in `(0, 1]`, when the caller
supplies one. `exposure` is always exactly these two booleans; the adapter gives the caller no
way to change them.

Response:

```json
{
  "schema_version": "moodboard.preference-serve.v1",
  "serve_id": "<uuid>",
  "scope": {
    "namespace": "<echoes request>", "actor_kind": "actor", "actor_id": "<client actor>",
    "board_entity_id": "<echoes request>", "board_id": "<echoes request>",
    "model_key": "<echoes request>", "descriptor_fingerprint": "<echoes request>",
    "feature_schema_id": "<echoes request>"
  },
  "feature_schema": {
    "schema_version": "moodboard.preference-features.v1",
    "feature_schema_id": "f691fc73bf9a50d72157e21601fa579caa707bf2c448df546c63e915b4e42175",
    "dtype": "float32",
    "bounds": [0.0, 1.0],
    "pair_transform": "left_minus_right",
    "features": ["visual_local_max_similarity_01", "visual_local_top3_mean_similarity_01", "visual_local_mean_similarity_01", "style_conformal_p", "style_interval_width", "local_support_fraction", "local_effective_support_fraction", "palette_compatibility", "tone_compatibility", "composition_compatibility"]
  },
  "left": {"result_occurrence_id": "<uuid>", "asset_id": "<uuid>", "content_ref": "<64 lowercase hex>", "source_rank": 1},
  "right": {"result_occurrence_id": "<uuid>", "asset_id": "<uuid>", "content_ref": "<64 lowercase hex>", "source_rank": 2},
  "randomization": {"revision": "moodboard-side-v1", "sha256": "<64 lowercase hex>", "swap_applied": false},
  "experimental": true
}
```

`scope`, `feature_schema`, `randomization`, `left`, and `right` are each closed objects: exactly
the keys shown, nothing more and nothing fewer. `scope.actor_id` must equal the exact actor
string the client was configured with; the adapter does not split it on `:` or otherwise
reinterpret it. `left` and `right` must have distinct `asset_id` and distinct `content_ref`.
`swap_applied` records whether Khive presented the two candidates in flipped left/right order;
the adapter does not undo that flip, it only reports which side is which through
`result_occurrence_id`.

### `moodboard.judge`

Request `args`:

```json
{
  "serve_id": "<uuid, from a prior moodboard.serve response>",
  "left_result_occurrence_id": "<uuid, from the same moodboard.serve response>",
  "right_result_occurrence_id": "<uuid, from the same moodboard.serve response>",
  "choice": "left",
  "reason_code": "style",
  "response_ms": 1200,
  "namespace": "<client namespace>"
}
```

`reason_code` and `response_ms` are omitted entirely when the caller does not supply them, rather
than sent as `null`. `choice` is one of `"left"`, `"right"`, `"tie"`, `"abstain"`, and constrains
which `reason_code` values are accepted: `"left"` and `"right"` accept `null` or one of `"style"`,
`"palette"`, `"tone"`, `"composition"`, `"other"`; `"tie"` accepts `null` or one of
`"equally_good"`, `"equally_bad"`, `"other"`; `"abstain"` requires one of `"insufficient_context"`,
`"both_unacceptable"`, `"render_failure"`, `"other"` (it does not accept `null`).
`response_ms`, when present, is an integer from `0` to `3600000`. A batch submitted through
`batch_judge` rejects a repeated `serve_id` before contacting Khive at all.

Response:

```json
{
  "schema_version": "moodboard.preference-judgment.v1",
  "judgment_id": "<uuid>",
  "serve_id": "<echoes request>",
  "choice": "left",
  "reason_code": "style",
  "created": true,
  "experimental": true
}
```

`serve_id`, `choice`, and `reason_code` must echo the request exactly. `created` is `false` on an
exact retry of an already-recorded judgment; the write is otherwise append-only and idempotent
per `serve_id`.

### `moodboard.train_preference`

Request `args`:

```json
{
  "board_entity_id": "<uuid>",
  "board_id": "<64 lowercase hex>",
  "descriptor": {"model_key": "moodboard_<fingerprint>_<dims>", "descriptor_fingerprint": "<64 lowercase hex>"},
  "feature_schema_id": "f691fc73bf9a50d72157e21601fa579caa707bf2c448df546c63e915b4e42175",
  "namespace": "<client namespace>"
}
```

Response:

```json
{
  "schema_version": "moodboard.preference-model.v1",
  "preference_model_id": "<uuid>",
  "content_ref": "<64 lowercase hex>",
  "model_fingerprint": "<64 lowercase hex>",
  "network_content_ref": "<64 lowercase hex>",
  "network_sha256": "<64 lowercase hex>",
  "created": true,
  "scope": {"namespace": "...", "actor_kind": "actor", "actor_id": "...", "board_entity_id": "...", "board_id": "...", "model_key": "...", "descriptor_fingerprint": "...", "feature_schema_id": "..."},
  "training": {"...": "opaque, Khive-defined, only required to be a JSON object"},
  "calibration": {"...": "opaque, Khive-defined, only required to be a JSON object"},
  "test_metrics": {"...": "opaque, Khive-defined, only required to be a JSON object"},
  "fann_inference_verified": true,
  "experimental": true
}
```

The adapter requires `fann_inference_verified: true` explicitly: a model that Khive has not
verified against its own FANN inference path is treated as a protocol failure, not returned to
the caller. `training`, `calibration`, and `test_metrics` are read as opaque objects; the adapter
does not close or interpret their keys, and callers should not assume any particular field is
present in every Khive version. `content_ref`, `model_fingerprint`, `network_content_ref`, and
`network_sha256` are each independent 64-character lowercase hex digests; the adapter does not
assert any relationship between them beyond their format.

### `moodboard.preference`

Request `args`:

```json
{
  "preference_model_id": "<uuid, from a prior moodboard.train_preference response>",
  "board_entity_id": "<uuid>",
  "board_id": "<64 lowercase hex>",
  "descriptor": {"model_key": "moodboard_<fingerprint>_<dims>", "descriptor_fingerprint": "<64 lowercase hex>"},
  "feature_schema_id": "f691fc73bf9a50d72157e21601fa579caa707bf2c448df546c63e915b4e42175",
  "source_report_sha256": "<64 lowercase hex>",
  "left": {"state": "scored", "asset_id": "<uuid>", "content_ref": "<64 lowercase hex>", "source_rank": 1, "features": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]},
  "right": {"state": "scored", "asset_id": "<uuid>", "content_ref": "<64 lowercase hex>", "source_rank": 2, "features": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]},
  "namespace": "<client namespace>"
}
```

`left` and `right` are validated exactly like a `moodboard.serve` candidate (`state: "scored"`,
distinct asset/content identity, ten finite `[0, 1]` features) and this verb runs an inference
call, not a training call: it does not record a served presentation or accept a judgment.

Response:

```json
{
  "schema_version": "moodboard.preference.v1",
  "prediction_kind": "learned_pairwise_preference",
  "conditional_on": "decisive_judgment",
  "probability_left_given_decisive": 0.75,
  "probability_right_given_decisive": 0.25,
  "raw_fann_logit": 0.8,
  "calibrated_temperature": 1.25,
  "indifference": {"state": "outside_calibrated_band"},
  "conformal_evidence": {"state": "not_computed_by_this_verb"},
  "preference_model_id": "<echoes request>",
  "model_content_ref": "<64 lowercase hex>",
  "model_fingerprint": "<64 lowercase hex>",
  "source_report_sha256": "<echoes request>",
  "scope": {"namespace": "...", "actor_kind": "actor", "actor_id": "...", "board_entity_id": "...", "board_id": "...", "model_key": "...", "descriptor_fingerprint": "...", "feature_schema_id": "..."},
  "left": {"asset_id": "<echoes request left>", "content_ref": "<echoes request left>"},
  "right": {"asset_id": "<echoes request right>", "content_ref": "<echoes request right>"},
  "experimental": true
}
```

`probability_left_given_decisive` and `probability_right_given_decisive` are each in `[0, 1]` and
must sum to exactly `1.0` within `1e-12`. `raw_fann_logit` is a finite number in
`[-1e30, 1e30]`; `calibrated_temperature` is a finite number in `(0, 1e30]`. `indifference.state`
must be `"inside_calibrated_band"` or `"outside_calibrated_band"`, and `conformal_evidence.state`
must be exactly `"not_computed_by_this_verb"`: this verb deliberately keeps a learned pairwise
probability separate from the conformal p-value and coherence statistics the rest of Moodboard
computes, and does not let a Khive response relabel one as the other. Both `indifference` and
`conformal_evidence` may carry additional Khive-defined fields beyond `state`; the adapter reads
only `state` and passes the rest through unvalidated. `left` and `right` in the response are the
narrow `{asset_id, content_ref}` pair, not the full candidate the request sent, and must echo the
identities that were requested.

### The preference feature artifact hand-off

Before any of the six verbs above run, `moodboard rank --preference-features-output PATH`
(`khive-lattice` encoder only) writes a `moodboard.preference-feature-artifact.v2` JSON file:
schema id `"moodboard.preference-feature-artifact.v2"` in its own `schema_version` field. This is
a file on disk, not a wire message, but it is the one artifact that lets a later process replay
the pairwise-serving verbs above without recomputing candidate geometry, so its shape is part of
the same contract:

```json
{
  "schema_version": "moodboard.preference-feature-artifact.v2",
  "board_entity_id": "<uuid, from the create-board response>",
  "board_id": "<64 lowercase hex>",
  "model_key": "moodboard_<fingerprint>_<dims>",
  "descriptor_fingerprint": "<64 lowercase hex>",
  "source_report_sha256": "<sha256 of the exact bytes write_report already published>",
  "feature_schema_id": "f691fc73bf9a50d72157e21601fa579caa707bf2c448df546c63e915b4e42175",
  "producer_revision": "moodboard.preference-producer.v1",
  "producer_id": "3fd22977f9f3686429cdb6569580b70573396efe0562095f43ed44e0a0ff3f22",
  "candidate_pool_sha256": "<64 lowercase hex, digest over all candidates>",
  "scope_sha256": "<64 lowercase hex, digest over the fields above plus candidate_pool_sha256>",
  "candidates": [
    {"label": "<non-empty string, at most 512 UTF-8 bytes>", "asset_id": "<uuid>", "content_ref": "<64 lowercase hex>", "source_rank": 1, "features": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]}
  ]
}
```

`feature_schema_id` is the SHA-256 of the canonical JSON describing the ten named features, their
`float32` dtype, their `[0, 1]` bounds, and the `left_minus_right` pair transform; changing any of
those, even without renaming a feature, requires a new schema id. `producer_id` is a second,
independent SHA-256 over the frozen prose mapping from each feature name to the geometric
quantity it measures (for example, `visual_local_max_similarity_01` is defined as
`max(local_transformed_similarities)`, and the cosine-to-`[0,1]` transform itself is
`clip((cosine+1)/2, 0, 1)`); this catches a producer that emits the right names and bounds but a
different formula. `candidate_pool_sha256` is a digest over every candidate's asset id, content
ref, source rank, and exact `float32` feature bytes, sorted by asset id, so the artifact cannot be
reordered or edited after the fact without invalidating it. `scope_sha256` is a second,
domain-separated digest over the board, descriptor, report, producer, and schema identity plus
`candidate_pool_sha256`, so board identity and candidate-pool identity stay independently
checkable rather than conflated into one hash. On read, every one of these digests is
recomputed and compared, not merely stored: a hand-edited artifact fails to load rather than
loading with silently stale identity. `candidates[].features` are written and read in the
project's own compact-JSON convention (`sort_keys=True`, `allow_nan=False`), not through Khive at
all; the artifact only supplies the values that later feed `moodboard.serve` and
`moodboard.preference` candidates once a board has been published and, for `moodboard.preference`,
once `moodboard.train_preference` has returned a `preference_model_id`.

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
    shape (n,). Build computes the effective board k = min(configured k_cap, n - 1). Every
    downstream caller passes that stored value, clamping only when a smaller local/fold bag has
    fewer available neighbours; this function does not choose k. Ties are broken by
    ascending row index, so the result is deterministic for a fixed embeddings array."""
```

### The symmetric full-conformal p-value

```python
def conformal_p_value(
    reference_embeddings: np.ndarray,
    candidate_embedding: np.ndarray,
    k: int,
) -> float:
    """ADR-0003's construction, exactly. Let n = reference_embeddings.shape[0]. Form the
    augmented bag of n + 1 rows (the n references, then the candidate). Compute
    nonconformity_scores over that bag with k = min(k, n), giving alpha_1 .. alpha_n for the
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
    candidate_category_members: tuple[int, ...]  # indices into reference_embeddings
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
`len(partition.candidate_category_members)`, the references sharing the category, **without the
candidate**. This is the same `n_local` `abstain.py`'s resolution check reads.

**Corrected 2026-08-08, and the correction is recorded rather than made silently.** This
paragraph and the `check_resolution` docstring below both pinned `len(...) + 1`, "plus the
candidate itself", and the implementation deliberately declined to adopt it. Three independent
statements agree on the reference count alone: the committed schema's `pValue` description,
`(1 + count) / (n_local + 1)`; `conformal_p_value`, which divides by the reference count plus
one; and ADR-0004's worked arithmetic, `1/(8+1) = 0.111` for an eight-member sub-look. Under
the withdrawn form a report would state `n_local = 11` beside a score of `10/11`, so the count
printed in the report would disagree with the denominator of the score printed next to it.

The document was wrong and the code was right, which is the direction that matters here: this
file is the contract other modules are written against, so a module written from it rather than
from the code would have inherited the off-by-one. Nothing was broken at runtime, because both
modules that read this had already refused the pinned form and said so in their own source.

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
def check_resolution(
    partition: CategoryPartition, requested_alpha: float
) -> AbstentionVerdict | None:
    """Covers ADR-0004 rules 1 and 2. n_local = len(partition.candidate_category_members),
    the reference count WITHOUT the candidate (corrected 2026-08-08; this line pinned `+ 1`,
    see the note beside Category.n_local above).
    Abstain (return a verdict) when requested_alpha < 1 / (n_local + 1); the comparison is
    strict, so requested_alpha exactly equal to 1 / (n_local + 1) is honoured and this returns
    None. When it abstains: reason is "resolution" if partition.candidate_category_members
    covers every other reference on the board (n_local equals the board's full reference
    count), otherwise "multi_modality". measurement carries {"n_local": ..., "supported_alpha":
    1 / (n_local + 1), "requested_alpha": requested_alpha, "category_id":
    partition.category_id}. explanation is a full sentence built from those numbers, in the
    register of 'This board has 10 references, so the finest distinction it can express is
    about 9%, and you asked for 5%.'"""


def check_multi_modality(
    partition: CategoryPartition, requested_alpha: float
) -> AbstentionVerdict | None:
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
    *,
    far_outlier_iqr_multiplier: float | None = None,
    far_outlier_iqr_multiplier_source: str | None = None,
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
    reference_embeddings: np.ndarray,
    model_repo: str,
    model_revision: str,
    metric: str,
    k: int,
    cluster_cut: float,
    dup_cut: float,
    *,
    k_cap: int,
    min_category_size: int,
    interval_level: float,
    far_outlier_iqr_multiplier: float,
) -> str:
    """ADR-0005's board hash, computed in exactly one place. sha256 hex digest over the
    canonical JSON serialisation (sorted keys, no insignificant whitespace) of

        {"v": 2, "refs": sorted(reference_content_hashes),
         "reference_embeddings": {
             "sha256": canonical_source_to_row_digest,
             "shape": [n, dim], "dtype": "float32-le"},
         "model": {"repo": model_repo, "revision": model_revision},
         "fit": {"schema_version": "moodboard-fit-policy.v1",
                 "metric": metric, "k": k, "k_cap": k_cap,
                 "cluster_cut": cluster_cut, "dup_cut": dup_cut,
                 "min_category_size": min_category_size,
                 "interval_level": interval_level,
                 "far_outlier_iqr_multiplier": far_outlier_iqr_multiplier}}

    The embedding digest frames shape/dtype and sorts [content_sha256,
    sha256(little-endian-float32-row)] pairs. Stable when references and their rows reorder
    together. report.py's
    Board.id and the brand.mb artifact's own id both call this function; neither recomputes
    it independently. Any new fitting parameter that can move a score is added inside "fit"
    and the literal "v" is bumped, both in the same change. The present v2/format-3 shape is
    the single unpublished migration from v1/format 1 and includes all fields above."""
```

`board.py` also builds the `brand.mb` artifact (the fitted board plus the reference embeddings
needed to score future candidates without re-embedding). Its on-disk format is that module's
own decision; the one constraint pinned here is that the artifact's own board id and
`Report.board.id` are both `board_hash(...)` called with the same arguments, never two values
that happen to agree.

Verified artifacts use `brand.mb` format version 3. The reader validates the canonical
little-endian float32 matrix, unit norms, source-to-row digest, shape/model dimension, and board
id. Versions 1 and 2 fail by default; an explicit migration-only legacy read returns
`integrity_verified=False` and cannot be re-written as verified.

A complete ordered tuple of
`ReferenceAssetLocation(asset_id, content_ref, byte_identity)` values is written as
`reference_asset_locations`, exactly one per reference. `byte_identity` is closed to
`source-bytes|canonical-png-rendition`. Locations are excluded from `board_hash`: the hash binds
source SHA-256, exact embedding rows, descriptor plus adapter identity, and fit identity, while
an entity id can be republished without changing a computed score. A separate catalogue digest
binds sorted `(source_sha256, content_ref, byte_identity)` tuples and excludes only `asset_id`.
Any future hydration must verify fetched bytes against both BLAKE3 `content_ref` and source
SHA-256; this module currently performs no hydration.

## `cli.py`

`moodboard build`, `moodboard rank` and `moodboard report --html` are the three entry points
named in the contract and in `README.md`. They are thin: `build` calls `encoders.py` to embed
a reference directory, `conformal.py` to partition and fit, and `board.py` to write
`brand.mb`; `rank` loads a `brand.mb`, calls `conformal.py` and `abstain.py` per candidate, and
calls `report.write_report`; `report --html` validates the selected report minor and invokes
`viewer.inline_report`, which verifies the packaged viewer manifest before an atomic write. It
does not recompute or project report values. No new type is introduced at this layer; every
engine object `cli.py` touches is defined above.

`build` resolves the complete fit policy once and persists it in `brand.mb`. `rank` scores from
that verified policy, including effective/configured k, category-size rule, interval level and
far-outlier multiplier; threshold registry discovery cannot move an existing board's results.
Supplying `rank --thresholds PATH` is an explicit compatibility assertion and refuses when the
file disagrees with the board rather than overriding it.

Both `build` and `rank` take `--encoder classical|khive-lattice`; `classical` remains the
default. The Khive executable, optional config path, actor and namespace are explicit options shared by both
commands. Supplying Khive configuration alone does not opt in, and a selected encoder whose
name/revision does not match the board fails before candidate scoring.
