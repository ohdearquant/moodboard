# ADR-0012: Typed Aesthetic Judgment Vocabulary and Decision Order

- **Status:** Proposed
- **Date:** 2026-08-16
- **Extends:** ADR-0002's report semantics, ADR-0003's separate axes and no-combined-score
  rule, ADR-0004's first-class abstention, and ADR-0011's separation of visual retrieval
  from board scoring.
- **Measurable claim:** none. This record fixes vocabulary, precedence, and presentation
  semantics. It defines no quality target or statistical acceptance threshold. Product metrics
  named below are event definitions, not claims that Moodboard improves them.

## Context

Moodboard now exposes several mechanisms that answer different questions. A reference route can
exclude an asset from an intent. Image retrieval can order the remaining assets by cosine in one
frozen representation. The engine can issue a board-relative conformal p-value or abstain. An edit
verifier can pass or fail one declared constraint. A separately trained pairwise head can estimate
which of two occurrences a person may prefer.

Those mechanisms are useful together and unsafe when presented as one score. Cosine is not a
probability of quality. A conformal p-value is not a user preference. An abstention is not a low
score. A failed locality gate cannot be rescued by a high board score. A gallery click is not a
pairwise judgment.

The product needs one coherent judging experience without assigning one fictional meaning to all
of its evidence. This record defines the shared vocabulary and the order in which typed results may
affect a creative decision.

The product category is an **executable aesthetic judge**: references make a target operational by
supplying context, board-relative evidence, explicit constraints, and bounded preference learning.
Lineage and receipts support that judge; collaboration and approval workflow are not its v1 face.

## Decision

### The product has six judgment types

The following vocabulary is closed for v1:

| Type | Question | Required result | May do | Must not do |
|---|---|---|---|---|
| `intent_eligibility` | Is this asset permitted by the declared intent route? | `eligible`, `excluded`, or `not_computed`, plus the route revision and reason | Filter the retrieval candidate set | Claim visual or aesthetic quality |
| `source_similarity` | Which eligible assets are nearest to the source in the frozen descriptor? | Ordered cosine rows with descriptor identity and original source rank | Order the eligible intersection while preserving exact source scores and order | Become a board score, verifier, or learned intent reranker |
| `board_compatibility` | Is this occurrence compatible with this frozen board under its registered method? | A conformal p-value, interval, competition rank, and board identity, or an explicit abstention | Rank scored occurrences and expose board-relative evidence | Become approval probability, universal quality, or a hard edit gate |
| `constraint_verification` | Did this occurrence satisfy one declared, versioned constraint? | `pass`, `fail`, or `not_run`, with verifier identity, inputs, parameters, and receipt | Reject an occurrence when a required verifier fails | Degrade a failure into a low aesthetic score or treat `not_run` as pass |
| `human_comparison` | Which of two displayed occurrences did the person explicitly choose? | `left`, `right`, `tie`, or `abstain`, with serve, display-order, occurrence, person, and board identities | Create explicit pairwise evidence | Infer a choice from passive interaction or impersonate a model prediction |
| `preference_prediction` | Under one immutable person-and-board snapshot, which member of one declared pair is predicted? | Pairwise probability or an explicit unavailable state, with snapshot and occurrence identities | Compare the declared pair | Claim universal taste, overwrite another judgment type, or silently rerank a list in v1 |

The serialized `kind` values above are stable contract vocabulary. Display labels may be localized,
but a consumer may not rename a kind into a stronger claim. In particular, `source_similarity`
may be displayed as image proximity, not style quality; `board_compatibility` may be displayed as
board-relative fit evidence, not percent on-brand.

Existing report fields named `score` and `rank` keep their wire names and exact meanings. Product
presentation qualifies them as **Board compatibility** and **Board rank**. Retrieval presentation
uses **Source visual rank** and **Routed reference order**. A main workflow must not render bare
`Score`, `Rank #1`, `engine-provided order`, or `taste score` without the owning mechanism.

These objects are Studio and evidence-artifact contracts. They do not add fields to report v1.0 or
v1.1. Those report versions retain their existing closed shapes; ADR-0013 defines the versioned
export envelope that may carry a report beside these richer artifacts.

Each result is a closed object with this common envelope:

```jsonc
{
  "schema_version": "moodboard.judgment.v1",
  "evidence_id": "<kind-specific identity>",
  "kind": "constraint_verification",
  "subject": {
    "kind": "selectable_output_occurrence",
    "output_occurrence_id": "<domain-separated digest>"
  },
  "result": {"state": "pass"},
  "authority": {
    "schema_version": "moodboard.verifier.outside-mask-rgb-exact.v1",
    "input_digest": "<sha256>"
  },
  "evidence_ref": "<immutable artifact or ContentRef>"
}
```

The `subject` and `result` members form this closed discriminated union:

| `kind` | `subject.kind` and identity | Closed `result.state` | Additional result |
|---|---|---|---|
| `intent_eligibility` | `asset_occurrence`: asset id, ContentRef, and route-query occurrence id | `eligible`, `excluded`, `not_computed` | typed route reason |
| `source_similarity` | `retrieval_result`: query occurrence id and ordered-result artifact id | `computed`, `empty`, `not_computed`, `refused` | ordered rows with source ranks and exact cosine values when computed |
| `board_compatibility` | `selectable_output_occurrence`: output occurrence id | `scored`, `abstained`, `not_computed` | conformal result when scored; typed reason when abstained |
| `constraint_verification` | `selectable_output_occurrence`: output occurrence id; or `provider_output_payload`: attempt, output index, receipt, ContentRef, and byte SHA-256 for invalid-payload structural failure or its blocked `not_run` only | `pass`, `fail`, `not_run` | verifier-owned measurements and reason |
| `human_comparison` | `comparison_pair`: serve id plus left and right output occurrence ids | `recorded` | `choice`: `left`, `right`, `tie`, or `abstain` |
| `preference_prediction` | `comparison_pair`: pair id plus left and right output occurrence ids | `predicted`, `unavailable` | pairwise probability/indifference result or typed refusal |

`selectable_output_occurrence` is the shared identity for an image the user may inspect, accept,
or compare. It always has ADR-0014 `admission: eligible`. V1 producer kinds are `generator_raw` and
`deterministic_compositor`; each occurrence retains its producer-specific lineage and constraint
results. The term does not collapse a raw provider output and a source-backed composite into one
occurrence.

`provider_output_payload` is the narrower identity for retained provider bytes that may fail before
ADR-0014 can publish a selectable output occurrence. Only raster-structure verification and the
exact-locality `not_run` result blocked by that structural evidence may use it. A measured
exact-locality `pass|fail`, board compatibility, acceptance, and human comparison still require a
selectable output occurrence. Structural `pass` and the repairable `dimension_mismatch` failure
also require that occurrence; other structural failures and their blocked `not_run` bind the same
provider-payload subject. This preserves failure evidence without making invalid bytes selectable.
Before publishing either receipt, the locality runtime hydrates the named immutable provider
receipt and requires its attempt id plus `outputs[output_index]` ContentRef and SHA-256 to equal the
subject. Envelope validation alone does not turn a caller-supplied tuple into provider evidence.

For computed kinds, `evidence_id` is
`sha256("moodboard.judgment.v1\0" || RFC8785(document-without-evidence_id))`. The domain tag is
intentionally identical to the envelope's `schema_version`; changing either string is a schema and
identity revision, never a compatible cleanup. A human comparison uses the immutable
judgment-event UUID issued by its serve authority. An implementation must not
choose between those identity rules heuristically. Kind-specific contracts extend the envelope
without changing another kind's fields. A consumer rejects an unknown schema major, kind,
subject kind, result state, choice, or authority revision rather than treating it as the nearest
familiar result.

### One authority owns each meaning

The producer that computes a result owns its meaning:

- the intent router owns eligibility and exclusion reasons;
- the Khive/Lattice retrieval response owns cosine values and source ranks;
- the Moodboard engine owns conformal scores, intervals, ranks, ties, and abstentions;
- the named verifier owns pass, fail, and not-run states;
- the explicit human comparison event owns the observed choice; and
- the immutable preference snapshot owns a pairwise prediction.

Studio may join and present these objects. It may not recompute them, copy one value into another
kind, or infer a stronger state from display position. The immutable viewer remains subject to the
same no-recomputation rule already established by ADR-0001 and ADR-0007.

### Decision precedence is explicit

V1 resolves a selectable output occurrence in this order:

```text
intent eligibility
    -> source-similarity ordering of eligible references
    -> generator-raw or deterministic-compositor output occurrence
    -> required constraint verification
    -> board compatibility score or abstention
    -> optional explicit human comparison and scoped pairwise prediction
```

This is semantic precedence, not a requirement that every computation run synchronously.

1. An excluded reference cannot be restored by cosine, board compatibility, or preference.
2. An empty intent route remains empty. There is no silent ungated fallback.
3. A required verifier failure makes that occurrence ineligible for acceptance. Its bytes and
   failed receipt remain visible and immutable.
4. `not_run` is not pass. A required verifier in `not_run` state blocks acceptance until it runs or
   the user creates a new intent packet whose policy does not require it.
5. Board abstention is a refusal to issue board compatibility, not rejection of the occurrence.
   The UI shows the verifier state and abstention separately. An abstained occurrence may still be
   explicitly accepted, but it cannot enter the v1 preference-training surface, whose Khive
   feature contract requires a scored candidate row.
6. A pairwise preference result cannot override a failed required verifier. V1 does not turn
   pairwise probabilities into an automatic gallery reranker.

No default combined `taste_score`, weighted sum, hidden tie-break, or cross-kind normalization
exists. A later combined decision policy requires a new ADR that names its training evidence,
error costs, evaluation protocol, and visible explanation.

V1 also does not compile feeling text into palette roles or turn palette/tone/composition
diagnostics into editable aesthetic intent. That product surface remains deferred and cannot be
smuggled into a combined score.

### Acceptance is a workflow event, not training data

The primary P0/P1 metric is **time to accepted result**. `accepted` is therefore a separate,
explicit workflow event naming one selectable output occurrence and one creative session. It is
not a judgment kind and does not alter any score or gate.

A `moodboard.creative-session.v1` starts from one explicit user action that commits an intent for
generation. One session may freeze multiple packets and contain multiple runs. Its metric clock
starts at the earliest append-only `submitted` provider-attempt event across those runs. A
`moodboard.output-accepted.v1` event is accepted only when its occurrence has every verifier marked
required by its own frozen packet in `pass` state. Its id is
`sha256(UTF8("moodboard.output-accepted.v1\0") ||
RFC8785({creative_session_id,selectable_output_occurrence_id}))`. Repeating that key returns the
existing event; a different payload for the same key is a conflict. Events are append-only. The
earliest valid acceptance fixes the session's north-star measurement; later acceptances remain
recorded but never rewrite it.

An acceptance event records at least:

- the creative-session, packet, run, and accepted-output-occurrence identities;
- the first submitted generation time and explicit acceptance time;
- the number of submitted attempts and provider-reported cost, currency, and provenance when
  available, or an explicit `unavailable` cost state;
- the active board and required verifier-policy identities.

Both elapsed time and total cost are instrumented from the first release. P0 and P1 report elapsed
time as the north-star metric. A later multi-model arena may report accepted outputs per dollar.
Neither metric is preference evidence. Accepting an occurrence, opening it, clicking a thumbnail,
or favoriting it must never synthesize a left/right judgment.

### The compact path is default-trust with expandable inspection

The main creative loop presents a compact summary of the exact context and required gates. A user
can expand that summary to inspect reference identities, ranks, route reasons, provider details,
hashes, and receipts.

The product may remember a user's trust in an unchanged context policy and allow a single primary
action to generate. It must require renewed confirmation when the ordered reference set changes,
the reference-use mode changes, a reference crosses a privacy/provider boundary, or a required
verifier policy changes. Default trust is never permission to hide what was sent; the run receipt
always records the exact context.

This keeps explicit confirmation available without turning every generation into a multi-step
approval flow. Collaboration, review queues, and approval chains are outside this decision.

### Canonical wording follows the evidence boundary

Product copy, reports, and examples use these meanings:

- The board uses a frozen visual representation; a separate preference head can learn from
  explicit comparisons.
- The generation or compositor pipeline edits; the board scores and gates the result.
- Cosine measures proximity in a frozen image representation.
- A conformal p-value is board-relative compatibility evidence, not approval probability.
- The compositor enforces preservation separately from the generator.
- When a run used references only to construct prompt wording, the receipt says so; it does not say
  the references were attached as direct image inputs.
- Simulated and human preference evidence are named and kept separate.

These are contract semantics, not optional marketing style. A UI string that contradicts them is a
contract defect even if the underlying artifact is correct.

## Alternatives considered

**One universal taste score.** Rejected. The inputs have incompatible meanings, scales, and failure
states. A weighted sum could let a pleasant-looking result hide a failed locality constraint and
would attach statistical authority to unvalidated weights.

**Let each screen choose its own vocabulary.** Rejected. The same cosine would become "style fit"
in retrieval and "similarity" in audit, while abstention and failure would collapse into empty or
low states. The product needs one semantic contract across Studio and exports.

**Treat explicit acceptance and passive UI interactions as preference labels.** Rejected. Those
events answer different questions and carry different selection bias. Keeping them separate is
necessary for a clean, auditable preference corpus.

**Require a confirmation dialog before every generation.** Rejected. It preserves disclosure at
the cost of making the normal path feel like an approval workflow. The default-trust,
expandable-inspect rule preserves exact receipts and prompts only when a material boundary changes.

## Consequences

The product can feel like one judge while its evidence remains typed. Explanations become more
useful because a reader can tell whether an item was routed, visually near, board-compatible,
constraint-compliant, or preferred.

The UI needs more than one visual grammar. A hard failure must look different from a low or
abstained board result, and a pairwise prediction must carry its scope. That additional design work
is the cost of not misleading the user.

Downstream contracts inherit the precedence above. ADR-0013 separates the connected Studio from
the immutable viewer; ADR-0014 binds the context and provider run; ADR-0015 owns explicit human
comparison; ADR-0016 owns locality verification.

## Acceptance conditions

This record remains Proposed until:

1. the six kind/subject/result combinations and selectable-output occurrence are represented by
   closed versioned schemas or types, and computed evidence-id tests pin the exact schema-version
   domain tag;
2. deterministic tests prove that a failed required gate cannot be overridden by board score or
   preference and that an empty route has no fallback;
3. presentation tests pin the canonical meanings and distinguish fail, abstain, and not-run;
4. creative-session and acceptance tests pin the first-submission boundary, required-gate
   admission, deterministic replay identity/conflict behavior, earliest-valid-acceptance metric,
   and separation from preference; and
5. no producer or consumer exposes a default combined taste score.
