# ADR-0015: Human Comparison and Preference Snapshot Lifecycle

- **Status:** Proposed
- **Date:** 2026-08-16
- **Depends on:** ADR-0012's typed judgment vocabulary and precedence, and ADR-0014's output
  occurrence/admission contract. A localized-edit candidate additionally depends on ADR-0016's
  required locality gate.
- **Extends:** ADR-0011's frozen asset and descriptor identities and the existing Moodboard
  preference wire contract documented in `INTERFACES.md`. It consumes the serve, judge,
  train-preference, and pairwise-inference semantics defined by Khive ADR-149; it does not change
  conformal scoring.
- **Measurable claim:** none. This record defines evidence admission, scope, lifecycle, and product
  behavior. It makes no claim that a learned snapshot matches a person, improves quality, or
  generalizes beyond its evidence.

## Context

Preference data is valuable only when the system can say exactly what was compared, what the person
saw, and what choice they explicitly made. A click in a gallery can mean inspection, navigation,
accident, or preference. A thumbs-up on one image has no explicit counterfactual. Reconstructing a
pair after the fact changes the question and contaminates the training corpus.

Khive already provides a bounded pairwise substrate: randomized serving, occurrence-bound
judgments, distinct left/right/tie/abstain outcomes, support-gated training, immutable model
snapshots, and inference on one pair. Its serve result creates display occurrences named by
`result_occurrence_id`; those are not generation occurrences. Moodboard therefore needs an exact
bridge between the output a person can select and the candidate row Khive can serve.

The current Moodboard adapter also fixes `source_rank_shown: true`. That behavior was suitable for
the recorded replay, but it cannot satisfy the blind human-comparison flow selected here. This ADR
defines the required product and adapter contract; it does not implement that contract.

The first scope is one person and one frozen board. Project, page, team, and organization scopes are
deferred rather than encoded as an unused general matrix.

## Decision

### Selectable outputs bridge exactly to Khive display occurrences

A `selectable_output_occurrence` is an immutable ADR-0014 output occurrence that Studio may place
into a comparison. Its v1 producer may be `generator_raw` or the ADR-0016
`deterministic_compositor`; the producer and lineage remain distinct. It is not a Khive
`result_occurrence_id`.

Before selection, Moodboard creates an immutable `preference_candidate_bridge` containing:

- one `selectable_output_occurrence_id`;
- its required ADR-0014 `admission: eligible` state;
- the canonical output identity and a closed Khive candidate row with exactly `state`, `asset_id`,
  `content_ref`, `source_rank`, and `features`: state is `"scored"`, rank is positive, identities
  are canonical, and the registered preference-feature order contains exactly ten finite values in
  `[0, 1]`;
- the frozen preference-feature artifact identity, feature schema, feature-producer revision,
  descriptor identity, and `source_report_sha256` from which that row came; and
- evidence that every required hard gate passed and board compatibility produced a score rather
  than abstaining.

The asset and content identities in the candidate row must identify the bytes of that exact
selectable output. A stale row, an output that failed a required gate, a board-compatibility
abstention, an unfrozen feature artifact, or any non-`scored` row is ineligible.

A comparison requires two bridges with distinct `selectable_output_occurrence_id`, distinct
`asset_id`, and distinct `content_ref`. Consequently, byte-identical generated results cannot form
a v1 training pair even if they came from different generation runs. They may still be inspected or
accepted separately; Moodboard must not fabricate distinct preference identities for them.

Moodboard submits only the two exact candidate rows to `moodboard.serve`. Khive randomizes their
presentation and returns, for each displayed side, a new `result_occurrence_id` plus the candidate's
`asset_id` and `content_ref`. Moodboard resolves each returned side back to exactly one input bridge
by that distinct identity pair and freezes a side binding containing both identifiers:

```text
display side
  -> Khive result_occurrence_id
  -> asset_id + content_ref
  -> Moodboard selectable_output_occurrence_id
```

The `result_occurrence_id` is the authority used by `moodboard.judge`; the
`selectable_output_occurrence_id` is the authority used to join generation, verification,
acceptance, and cost provenance. They are never substituted for one another. A response that cannot
be mapped one-to-one is a protocol failure and is not shown as a comparison.

`source_report_sha256` is event provenance for the frozen candidate rows and serve. It is not part
of the preference-model compatibility key. Different events may name different source reports as
long as their candidates satisfy the exact substrate scope and feature contract.

### The outcome set is closed

The stored outcomes are:

- `left`: the displayed left occurrence is preferred;
- `right`: the displayed right occurrence is preferred;
- `tie`: the person explicitly judges the two indistinguishable for the task; and
- `abstain`: the person skips, cannot decide, finds the pair invalid, or declines to provide a
  preference.

Studio may label `abstain` as **Skip**, but the stored value remains `abstain`. Optional reason text
is annotation and never changes the outcome.

Only one deliberate comparison-submit action creates a judgment. A choice is rejected if the serve
is missing, already judged, has mismatched display occurrences, or does not match its recorded
left/right order. Moodboard does not invent a client-side serve expiry policy. Any additional
substrate refusal is preserved and shown without reinterpretation.

### Preference evidence is pure and blind by construction

The following actions never create or modify a pairwise judgment:

- gallery click, hover, dwell, open, zoom, drag, or reorder;
- thumbs-up, favorite, save, download, export, or share;
- explicit acceptance under ADR-0012;
- provider retry, model selection, or reference selection;
- board compatibility score, rank, tie, or abstention;
- constraint pass, fail, or not-run; and
- an inferred model probability.

There is no configuration flag that turns these proxies into preference evidence. A later research
dataset may retain passive interaction telemetry under its own schema and consent policy, but it is
not admissible to this training corpus and cannot be converted in place.

Every v1 human serve records both exposure fields as false:

```text
preference_probability_shown = false
source_rank_shown = false
```

Before judgment, Studio also hides board score, provider/model identity, cost, active-model
prediction, and other information that could reveal a system preference. The presentation revision
and both requested exposure values are frozen with Moodboard's serve envelope.

The current adapter hard-codes `source_rank_shown: true` and gives callers no exposure control. It
is therefore non-conforming for this human flow. Dependent implementation must add a dedicated
blind serve path that always sends both values as false and validates the request it actually
submits. The human UI must not expose a switch that can silently weaken this rule. Historical or
experimental rank-exposed events remain inspectable but are not admitted by this v1 product flow.

Khive training does not currently promise to exclude source-rank-exposed events. The blind human
flow must therefore use a newly admitted technical scope whose namespace/actor/board tuple contains
only serves created through this path. Rank-exposed history stays in its prior substrate scope, and
no alternate writer may append exposed serves to the blind scope. This isolation is an admission
rule, not a claim that Khive filters by Moodboard presentation metadata.

### Scope is one enrolled local Studio principal x one immutable board

The product scope key is:

```text
stable enrolled Studio principal x immutable board id
```

Local setup creates one random principal UUID, requires a non-empty user-visible label, and persists
the exact one-to-one Khive actor string that will be used for serve, judge, train, and inference.
Changing or rotating that principal creates a new preference scope; evidence is never migrated or
merged implicitly. Each comparison envelope and snapshot reference freezes the principal UUID and
actor string. The adapter passes and compares the complete actor string; it may not split, prefix,
reconstruct, or fall back to another spelling.

The ADR-0013 loopback session token proves that a browser may act on the local backend; it does not
independently verify a legal person. `human_explicit` therefore means explicit single-user evidence
attributable to the enrolled local principal. A hosted or multi-user identity authority requires a
separate authentication and tenancy decision.

An anonymous browser session, policy-simulated actor, service actor, or team identity cannot be
treated as this enrolled principal. Changing the immutable board id creates a new product scope
even if the board name is unchanged. Project, page, and organization are optional run annotations
in v1 and never enter preference scope.

The Khive model-compatibility key is the exact scope returned with the immutable snapshot:
namespace, actor kind and actor id, board entity id, immutable board id, descriptor model key and
fingerprint, and feature schema id. Moodboard's active-pointer check uses those fields exactly.
`source_report_sha256`, source rank, candidate-pool identity, and feature-producer revision remain
per-event provenance; they are not added to that model key.

A future scope expansion requires a new ADR with migration and isolation rules.

### Human, simulated, and imported evidence never share a substrate scope

Moodboard's comparison envelope has an `evidence_class`. V1 recognizes `human_explicit` and
`policy_simulated`; another class requires a schema revision. Khive does not filter or train by this
Moodboard field, so the field alone is not an isolation mechanism.

Admission instead makes the underlying writer scopes disjoint. `human_explicit` and
`policy_simulated` use different admitted `(namespace, exact actor)` identities and may reference
the same immutable board entity/id so their results can be compared honestly within one aesthetic
scope. Studio rejects any serve, judgment, training request, snapshot, or activation whose declared
class disagrees with the admitted namespace/actor mapping or whose board differs from the frozen
comparison scope. A human scope accepts only the enrolled local-principal mapping; a simulation
scope accepts only its declared non-human actor. Board identity defines what is being judged; it is
not used as a substitute for evidence-class isolation.

`policy_simulated` events remain demo or evaluation evidence. They cannot be copied, relabeled,
weighted, or warm-started into a `human_explicit` scope. Human observed counts exclude them. A
viewer renders the evidence class beside a snapshot, while the immutable Khive scope remains the
technical proof that the two classes could not have trained together.

Imported human data requires occurrence, presentation, consent, and scope evidence equivalent to a
native serve, including the exact bridge and disjoint admitted scope. A pair of labels without that
evidence is not imported as preference data.

### Cold start reports observation, not predicted support

Before a model is published, Studio displays **No learned preference snapshot yet**. It may report
the number of locally observed, structurally admissible explicit judgments as a descriptive count.
That count is not called training support, eligibility, or an exact remaining requirement.

Khive's support calculation and train refusal are authoritative. The existing interface has no
support-status read verb, so Moodboard does not duplicate the decision or promise a continuously
accurate countdown. Only an explicit train request establishes whether support is sufficient. If
Khive refuses it, Studio preserves the refusal and shows whatever authoritative support detail the
response actually supplies; it does not manufacture missing counts or fall back to a weaker local
threshold.

Candidate eligibility, required hard gates, and board compatibility continue to function without a
preference snapshot. V1 gallery order remains the engine's board ranking. Pairwise inference is not
required to generate or score, and board rank is never described as learned preference.

### Retraining publishes; Studio activation points

V1 training is explicit and manual. Studio sends the exact admitted person-and-board substrate scope
to `moodboard.train_preference`; Khive either refuses or returns an immutable published snapshot
from its authoritative eligible evidence. Moodboard preserves the returned `created` value rather
than implying every successful request created a new model. Returned training, calibration, and
test objects remain opaque substrate evidence; Moodboard does not claim an event boundary Khive did
not return.

The lifecycle is:

```text
no snapshot --explicit train--> refused | published
published --explicit activate--> active
active --activate another compatible snapshot--> superseded-as-active
```

`superseded-as-active` is a pointer state, not deletion or mutation. A person can roll back by
activating an earlier compatible snapshot. Snapshot bytes, returned scope, training evidence,
calibration, test evidence, model id, and content identities remain immutable.

The active-snapshot pointer is Moodboard Studio product state, not a Khive model mutation. At most
one snapshot is active for one exact compatibility key. Activation requires an exact match of the
local principal mapping, evidence class admission, and returned Khive compatibility key, and records
who activated it and when. Publishing a snapshot never activates it silently.

### Inference remains pairwise in v1

The snapshot estimates one pairwise probability conditional on a decisive comparison, plus its
calibrated indifference result. It does not output a universal quality score.

V1 may use the active snapshot to:

- explain a previously frozen probe comparison;
- select an informative next pair under a separately documented pair-selection policy; or
- show a prediction for the exact pair currently under inspection after the person has judged it.

Inference candidates obey the same exact scored-row, distinct-identity, hard-gate, board-score, and
bridge requirements as served candidates. V1 does not use the snapshot to rerank the gallery. It
never restores an intent-excluded reference, overrides a required verifier failure, converts board
abstention into a score, or changes a conformal rank. List ranking, tournament aggregation, cycle
handling, and model-assisted acceptance require a later ADR and their own evaluation.

### Snapshot evaluation is descriptive until a protocol says otherwise

Studio's snapshot-evaluation record binds the substrate-provided training, calibration, and test
evidence to predictions on any registered frozen probes. It does not mutate the Khive snapshot.
Studio may compare two immutable snapshots on the same probe set when all compatibility identities
match.

A probability movement is described directionally. It is not called improvement, adaptation
success, or learned taste unless a preregistered human evaluation supports that claim. Moving toward
0.5 is moving toward indifference, not adopting the opposite policy.

Simulation demos always disclose `policy_simulated`, immutable retraining, frozen probes, and the
absence of human evidence. Human snapshots disclose their person-and-board scope and never imply
team or universal preference.

### Acceptance and product metrics stay separate

An explicit accepted-result event may be joined to comparison and cost data for product analytics,
but it is not a judgment label. Time to accepted result is the P0/P1 north-star metric. A later
arena may report accepted outputs per dollar. Both are instrumented from the first generation run.

Analytics joins operate on immutable selectable-output and session identities and cannot write back
into the preference corpus. Reporting one metric does not erase the other.

## Alternatives considered

**Use thumbs-up as positive preference evidence.** Rejected. It provides no controlled comparison,
has unknown exposure, and conflates acceptance with relative preference.

**Treat a generation occurrence as Khive's display occurrence.** Rejected. Khive creates
`result_occurrence_id` only when it randomizes a serve. Collapsing the two hides display order and
breaks the exact bridge back to generation provenance.

**Train continuously after every event.** Rejected. It makes the active behavior a moving target,
weakens replay, and hides support and calibration boundaries. Explicit immutable publication makes
change inspectable and reversible.

**Scope v1 to person x project x page x board.** Rejected. Most dimensions would be empty and the
fallback rules would decide behavior more than evidence. Person x immutable board is the smallest
useful, explainable scope.

**Mix simulated events into human cold start.** Rejected. A Moodboard-only class label cannot make
that safe because Khive trains by its own exact scope. Disjoint admitted namespace/exact-actor
writers keep the evidence physically out of the human training scope even when both classes judge
the same immutable board.

**Automatically rerank every gallery.** Rejected. The existing substrate is pairwise and can be
cyclic. List aggregation, exploration, and error costs are a separate product decision.

**Implement another learner or support calculator in Moodboard.** Rejected. Khive owns randomized
serve, occurrence-bound judgment, support-gated training, immutable snapshots, and pairwise
inference. Moodboard defines product admission, exact bridging, and activation around that contract
rather than forking it.

## Consequences

Preference data remains smaller but far more trustworthy. Every training label has a visible
counterfactual, known display order, exact output bridge, and explicit human action. Byte-identical
outputs and unscored or gate-failed outputs cannot cheaply inflate the corpus.

Cold start is honest but less predictive: without a substrate support-status verb, Studio can show
observed admissible judgments and authoritative train refusals, not an exact countdown. Snapshot
changes remain reviewable without turning the product into an approval system.

The existing Moodboard adapter does not yet satisfy this ADR because it forces
`source_rank_shown: true`. A dependent Moodboard implementation must add the blind serve path,
principal-to-actor mapping, bridge artifacts, class admission, and app-owned active pointer after
this spec is approved. This ADR authorizes none of that implementation by itself.

No new Khive verb is required: the substrate already represents both exposure booleans, exact
candidate identities, randomized display occurrences, scoped training, and immutable snapshots. A
future continuously readable support-status or shared server-side activation primitive would
require its own Khive ADR. Lattice is unchanged because the visual representation remains frozen.

## Acceptance conditions

This record remains Proposed until:

1. a frozen bridge maps every eligible `selectable_output_occurrence_id` to one exact scored
   ten-feature Khive row and its asset, content, report, descriptor, producer, and gate provenance;
2. serve tests prove returned `result_occurrence_id` values map one-to-one through distinct
   `asset_id` and `content_ref` values and are never treated as generation occurrences;
3. candidate admission rejects byte-identical pairs, non-scored rows, board abstentions, unfrozen
   feature artifacts, and any occurrence with a failed or not-run required hard gate;
4. the human serve path submits `preference_probability_shown: false` and
   `source_rank_shown: false`, and no pre-judgment UI exposes hidden ranking or model information;
5. every passive interaction and accepted-result event produces zero preference judgments;
6. a stable enrolled local principal maps one-to-one to the exact Khive actor, rotation creates a
   new scope, and disjoint admitted namespace/actor writers prevent human and simulated evidence
   from entering the same snapshot even when they share one board;
7. cold start shows only observed admissible counts until an explicit train request returns an
   authoritative publication or refusal, with no local support fallback or zeroed model;
8. successful training returns an immutable snapshot plus its `created` state, and activation or
   rollback changes only the compatible app-owned active pointer;
9. `source_report_sha256` remains per-event provenance and is not required as an active-snapshot
   compatibility field;
10. v1 APIs expose pairwise inference without list reranking or hard-gate override; and
11. simulation and snapshot-comparison copy retain the non-claims and visible evidence-class labels
    in this record.
