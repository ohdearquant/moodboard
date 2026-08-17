# ADR-0014: Frozen Intent Packet, Generation Run, and Provider Boundary

- **Status:** Proposed
- **Date:** 2026-08-16
- **Depends on:** ADR-0012's typed evidence vocabulary and ADR-0013's connected Studio/local
  backend boundary.
- **Extends:** ADR-0005's immutable board identity and ADR-0011's content-addressed visual assets
  and retrieval identity.
- **Related:** ADR-0016 registers operation kind `localized_edit` under schema
  `moodboard.operation.localized-edit.v1` as the first payload carried by this record's generic
  packet and run envelope.
- **Measurable claim:** none. This record defines immutable handoff, provider-attempt, failure,
  fallback, and provenance contracts. It selects no model-quality, latency, or cost threshold.

## Context

A generation result is uninterpretable without the exact instruction, source, ordered references,
provider options, and request that produced it. A UI screenshot or prompt string cannot answer
whether a reference was attached as an image, used only to construct text, or merely shown to the
user. A configured model name cannot prove which model actually served a request. A retry without
an occurrence identity can turn one click into two paid outputs.

The existing report and Khive visual-asset contracts provide useful immutable identities, but
neither is a generation-job protocol. The report is a read-only presentation artifact. Khive's
Moodboard pack owns visual storage, descriptors, retrieval, and pairwise preference; it does not
own provider dispatch.

The product needs a provider-neutral boundary whose first release exposes one model without
hard-coding one provider's request shape into the rest of Moodboard.

## Decision

### The frozen intent packet is the complete generation input

Studio may keep a mutable draft. Generation starts only from a closed
`moodboard.intent-packet.v1` document. Packet identity uses RFC 8785 JSON Canonicalization Scheme
(JCS), including its Unicode and ECMAScript number serialization rules. Non-finite numbers and
values not representable by the schema are rejected.

The identity projection is the complete document with only `intent_packet_id` omitted:

```text
intent_packet_id = sha256(
  UTF8("moodboard.intent-packet.v1\0") || JCS(identity_projection)
)
```

Storage may add a trailing line feed outside that identity projection. Cross-language golden
vectors pin Unicode, object ordering, arrays, exponent forms, negative zero, and boundary numbers.
The packet is immutable after hashing.

The closed top-level shape is:

```jsonc
{
  "schema_version": "moodboard.intent-packet.v1",
  "intent_packet_id": "<domain-separated SHA-256>",
  "creative_session_id": "<uuid>",
  "operation": {
    "kind": "localized_edit",
    "schema_version": "moodboard.operation.localized-edit.v1",
    "payload_sha256": "<operation-owned domain-separated digest>",
    "payload": {}
  },
  "board": {
    "board_id": "<immutable board hash>",
    "representation_id": "<model and adapter identity>",
    "fit_policy_id": "<immutable fit-policy identity>"
  },
  "source": {
    "asset_id": "<uuid>",
    "content_ref": "<BLAKE3 BlobStore identity>",
    "content_sha256": "<source-byte identity>",
    "mime": "image/jpeg",
    "width": 1280,
    "height": 960
  },
  "instruction": "Replace the selected tree with a mature lemon tree.",
  "retrieval_route": {},
  "references": [],
  "generation_request": {},
  "verification_policy": {
    "schema_version": "moodboard.verification-policy.v1",
    "policy_id": "<domain-separated digest>",
    "required_verifiers": ["moodboard.verifier.outside-mask-rgb-exact.v1"]
  },
  "confirmation": {}
}
```

`operation.payload` is the only operation-specific extension slot. Its owning schema defines the
payload projection and domain-separated digest. An unknown kind, schema major, payload field, or
digest fails closed. ADR-0016 defines the first registered kind, `localized_edit`, under schema
`moodboard.operation.localized-edit.v1` and owns its region/mask payload. This envelope does not
depend on that payload schema; another operation needs its own ADR and registration.

`verification_policy.policy_id` is the domain-separated RFC 8785 digest of the closed policy
without that field. Every required entry is an exact verifier schema id; aliases or separate
method/revision spellings are invalid. This top-level policy is the sole authority for whether a
verifier gates acceptance. An operation payload may require that an exact verifier id be present,
but may not define a second required set. Cross-field validation rejects a packet when an
operation-required verifier is absent; operation-local diagnostics never become required by
implication.

`content_ref` and `content_sha256` have different algorithms and meanings and are never
substituted. A packet carrying both verifies both before dispatch.

### Retrieval and provider dispatch are separate frozen authorities

`retrieval_route` records the versioned intent-routing policy, immutable eligible corpus or
manifest, empty-result policy, and evidence artifact. It preserves exact source-image cosine and
source ranks while stable-filtering eligible references. It does not create a blended score, apply
preference, or silently use an ungated fallback. An empty route cannot produce a packet ready for
generation.

`generation_request` independently freezes:

- requested provider and exact requested model;
- adapter revision and immutable capability-snapshot id;
- output count and normalized closed provider options;
- an ordered `operation_inputs` projection that says how each source, mask, control image, or
  provider derivative is delivered and which capability authorizes it;
- `provider_route_policy_id`, its ordered permitted routes, and whether Moodboard fallback is
  permitted;
- destination privacy/retention class and non-secret credential-profile id; and
- the `actual_model_policy` (`exact_required` or `requested_only_permitted`) plus provider
  idempotency/reconciliation capabilities.

Retrieval policy and provider-route policy never share a field or identifier. Any change to this
generation request creates a new packet and requires confirmation.

Each `operation_inputs` entry is closed and records an operation-owned input role, original
artifact identity, delivery mode, provider field/role, and exact delivered artifact identity. A
delivered derivative additionally records its compiler revision, source identity, parameters,
bytes, dimensions, and digest. The generic modes are `native_input`, `attached_control`,
`prompt_only`, and `not_sent`; an operation schema may register a narrower enum.

For operation kind `localized_edit`, ADR-0016 requires a `source_image` entry and a `locality_mask`
entry. The source entry identifies exact original bytes or one disclosed provider derivative. The
mask mode is one of `native_mask`, `attached_overlay`, `prompt_only`, or `not_sent`;
native/overlay modes bind exact mask or overlay bytes and their compiler. Studio does not infer
source or mask delivery from the instruction. An unavailable provider capability fails before
dispatch.

### Reference occurrences are ordered and explicit about use

`references` is an ordered array. Each element is an occurrence, not merely an asset:

```jsonc
{
  "reference_occurrence_id": "<uuid>",
  "role": "visual_context",
  "asset_id": "<uuid>",
  "content_ref": "<BLAKE3>",
  "content_sha256": "<SHA-256 when source bytes are bound>",
  "source_search_rank": 4,
  "routed_rank": 1,
  "source_similarity": 0.8432995826005936,
  "route_reason": "<typed reason>",
  "provider_use": "attached_image"
}
```

Array order is authoritative. `routed_rank` must equal the one-based array index and is therefore
contiguous and unique; `source_search_rank` retains the possibly gapped pre-route rank. Confirmation,
the normalized request, provider attachment order, and Studio presentation all consume this exact
array order. A mismatch fails packet validation.

The v1 `provider_use` set is:

- `attached_image`: exact image bytes are sent through a provider image-reference field;
- `prompt_context_only`: a declared text projection informs the final prompt, but image bytes are
  not sent; and
- `not_sent`: the occurrence is retained as routing evidence but absent from the provider request.

Selection in Studio does not imply attachment. For `prompt_context_only`, the packet names the
versioned prompt compiler and exact reference-derived text items. The normalized request artifact
records the resulting prompt. For `attached_image`, it records the ordered byte identities and
provider roles. If the adapter cannot honor the declared mode exactly, preflight fails before
submission.

### Confirmation binds the exact dispatch boundary

The packet's `confirmation` records:

- `mode`: `explicit` or `default_trust`;
- the ordered reference projection shown to the user;
- reference-use and operation-input delivery projections, requested provider/model, destination
  privacy class, adapter revision, capability snapshot, options, provider-route policy,
  actual-model policy, and required verifier-policy identities;
- the identity of the compact summary whose expandable evidence was available; and
- confirmation time, Studio session identity, and enrolled local principal identity.

Default trust is valid only under ADR-0013 for an unchanged policy projection. Any field above
changing requires renewed confirmation. The packet stores no reusable browser credential.

### One generation run contains immutable attempts

A `moodboard.generation-run.v1` binds one packet and one creative session to one user-invoked run.
V1 exposes one requested model, not an arena. The run UUID is independent of packet content so a
person can intentionally run the same packet again.

Each Moodboard-initiated provider call is a separate immutable attempt. Its event states are:

```text
prepared -> failed | cancelled | submitted
submitted -> response_received | failed | cancelled | outcome_unknown
outcome_unknown -> response_received | failed | cancelled
response_received -> succeeded | failed
```

`succeeded`, `failed`, and `cancelled` are terminal. Cancellation after submit, including resolution
from `outcome_unknown`, is terminal only when the provider authoritatively confirms that no output
can complete; otherwise it remains `outcome_unknown`. Reconciliation may resolve an unknown outcome
through authoritative provider status. Events are append-only and retain the original uncertainty.

Every failure records one closed `failure_stage`: `preflight`, `dispatch`, `provider`,
`reconciliation`, `provenance`, or `output_validation`. `succeeded` means all required provider
outputs were decoded, validated, and published as eligible output occurrences. A provider HTTP
success alone is `response_received`, not success.

### Idempotency is capability-bound, not inferred from a key

Before network dispatch, the backend durably claims one local-dispatch slot for the attempt. It
derives the request key from run id, attempt id, packet id, adapter revision, and normalized-request
digest.

The capability snapshot declares:

- whether the provider accepts that key and its exact deduplication scope/retention;
- whether status reconciliation is supported and which handle identifies the call; and
- whether an ambiguous transport may be safely retransmitted.

When provider idempotency is not guaranteed, Moodboard dispatches the attempt at most once. A lost
response becomes `outcome_unknown`; it is not automatically retransmitted. When the provider does
guarantee the declared key semantics, the same attempt may retransmit the same request bytes and
key under that contract. A local key alone never claims exactly-once provider execution or billing.

A deliberate retry creates a new attempt id and `retry_of` link. A Moodboard-initiated fallback is
also a new attempt with `fallback_of`; it is never hidden inside the first attempt.

### The provider adapter is neutral and capability-driven

Every adapter implements the same conceptual operations:

```text
discover_capabilities() -> immutable capability snapshot
prepare(packet) -> normalized request artifact
submit(prepared_attempt) -> submitted(handle) | response_received(receipt)
                            | failed | cancelled | outcome_unknown
reconcile(handle) -> response_received(receipt) | failed | cancelled | outcome_unknown
decode_and_validate(receipt) -> output occurrences
```

A synchronous `response_received` return still appends `submitted` before
`response_received`; the adapter cannot skip the dispatch event in the state trace.

For a retained synchronous response, the canonical receipt, exact private raw-response bytes,
every provider output payload, and the derived `response_received` event form one recoverable
local commit boundary. A receipt whose retention policy is `not_retained` commits the same package
without raw-response bytes. Exact replay returns the existing commit; a differing receipt,
payload, or attempt/output-index binding is a protocol conflict. The event is derived inside the
transaction, so stored event drift is corruption rather than a caller-supplied conflict. A lost
commit acknowledgement is resolved from durable local state and is never projected as provider
failure. This boundary does not perform media admission, mint output occurrences, or append
`succeeded`.

The immutable capability snapshot records adapter revision, provider, requested model, input
modalities, reference limits and roles, output count, dimensions/aspect-ratio surface, seed and
option support, operation-input roles (including source/mask/control support), actual-model
disclosure, idempotency, and reconciliation. Controls derive from this snapshot. Undeclared
options or operation-input modes fail preflight.

Provider-specific fields live in a namespaced closed section. The portable run does not depend on
an arbitrary provider response. A raw response may be retained as a private hashed blob subject to
provider terms, but secrets and headers never enter Studio or export artifacts.

### The normalized request is an auditable artifact

`moodboard.normalized-provider-request.v1` is an immutable, secret-redacted artifact containing:

- exact final prompt text and prompt-compiler revision;
- requested provider/model, normalized options, output count, and provider-route policy;
- ordered operation inputs, including exact source/mask delivery modes, provider roles, original
  identities, and any derivative compiler plus delivered-byte identities;
- ordered reference-use entries with occurrence, byte, role, and derived-text identities;
- adapter/capability identities and privacy class; and
- the canonical non-secret provider body projection that was dispatched.

Its id is domain-separated RFC 8785 SHA-256. The attempt stores both its id and payload reference;
a digest without the artifact is insufficient. The adapter validates actual request bytes against
this artifact before dispatch. This makes “attached image,” “prompt context only,” and “not sent”
auditable facts rather than UI assertions.

### Requested, actual, and fallback routing are visible

Every attempt records requested provider/model/route policy and the actual-model provenance state.
Under `exact_required`, absent or conflicting actual-model provenance fails at stage `provenance`,
even if returned bytes are retained. Under `requested_only_permitted`, an API that accepts an exact
requested model but does not attest it in the response records `actual_model: undisclosed`. Studio
must display **Requested model: X; actual model not attested by provider** and cannot claim that no
substitution occurred. A response that does disclose a conflicting model still fails. This weaker
policy must be visible in confirmation; it is not silently selected by the adapter.

A provider may internally select an upstream endpoint during one external call. That disclosed
trace stays inside the attempt and never masquerades as a Moodboard fallback. An undisclosed
upstream route is rendered `unknown`; it is allowed only when the confirmed provider-route and
privacy policy explicitly permit undisclosed routing. Otherwise preflight or provenance fails.

Moodboard fallback may select only another confirmed route serving the same exact model and privacy
class, and only under the frozen provider-route policy. It creates a new attempt, displays the
fallback, and retains both attempts. A different model always requires a newly confirmed packet and
run. Silent substitution is prohibited.

### Output occurrences are closed and producer-specific

Validated provider output is published as `moodboard.output-occurrence.v1`. Its id is
`sha256(UTF8("moodboard.output-occurrence.v1\0") ||
RFC8785({attempt_id,output_index}))`. The `(attempt_id, output_index)` key is unique: exact replay
returns the existing occurrence and a different payload or receipt for that key is a protocol
conflict. The occurrence contains:

- `producer_kind: generator_raw`, source attempt, output index, and role;
- original byte SHA-256, BlobStore ContentRef, media type, byte count, decoded dimensions, and
  media-validation receipt;
- `admission: eligible` or `rejected` with closed rejection reasons; and
- lineage to packet, normalized request, provider receipt, and source/reference occurrences.

Only a decoded, bounded, identity-verified output with `admission: eligible` is a
`selectable_output_occurrence`. An invalid or active payload remains a private provider-payload
artifact, not an image occurrence. A provenance-mismatched but otherwise valid image may be
retained as `rejected`; it cannot be accepted, compared for v1 training, used as an input to a
selectable descendant, or presented as the requested model's result.

A display derivative receives its own identity and never replaces original bytes. Generation,
deterministic compositor, and future manually edited outputs are different producer occurrences.
ADR-0016 adds the closed compositor producer and locality lineage without altering the provider
attempt.

### Secrets remain outside every artifact

Provider credentials are read by the backend from an approved secret source and held only in
memory. They never appear in argv, logs, packet JSON, normalized request, run receipt, raw-response
blob, report, browser storage, or frontend bundle. Error projection removes headers and known
secret fields before persistence.

A non-secret credential-profile id may explain routing. It is not the key or a reversible key
fingerprint.

### Metrics are instrumented, not inferred

Every run records provider-reported cost when available, or explicit unavailability; locally
observed latency and its boundary; attempt count; verifier outcomes; and ADR-0012 acceptance events.
This supports time to accepted result from P0 and accepted outputs per dollar in a later arena.
Static price tables do not replace provider-reported cost in a run receipt.

The record makes no claim that a cheaper, faster, or reference-conditioned run is better. Those are
evaluation questions for frozen tasks and explicit human acceptance.

## Alternatives considered

**Store only the final prompt and image.** Rejected. It loses source, operation, reference order,
attachment mode, capability policy, actual route, failures, and retry identity.

**Let each provider define the product contract.** Rejected. A capability-driven adapter contains
provider changes without making Studio or evidence artifacts provider-specific.

**Allow automatic fallback to another model.** Rejected. It changes the generator being evaluated
and makes model selection false. Another model is a new confirmed packet and run.

**Retry every transient-looking error automatically.** Rejected. A lost response may hide a
completed paid request. Retransmission requires provider-declared idempotency; otherwise unknown
outcome requires reconciliation or explicit action through a new attempt.

**Put provider dispatch into Khive or Lattice now.** Rejected. Khive owns assets, retrieval, and
preference snapshots; Lattice owns descriptor inference. V1 provider jobs are a Moodboard concern.

## Consequences

Every generated occurrence can be traced to exact creative intent and exact provider dispatch. The
UI can state whether references were attached and whether fallback occurred without inference.

The contract is deliberately more verbose than a provider request. That buys honest failure states,
bounded local dispatch, cost/latency telemetry, and adapter portability; it does not promise
provider-side exactly-once billing.

V1 has one model. Multi-model arena semantics, blind model presentation, and cost-to-taste
aggregation are deferred. Only operation schemas registered by separate ADRs may use the envelope.

No Khive or Lattice change is required. A future durable generation-run verb requires a separate
owning-repository ADR.

## Acceptance conditions

This record remains Proposed until:

1. packet, operation-extension, run, attempt, normalized-request, capability, receipt, and output
   occurrence schemas are closed and versioned;
2. RFC 8785/domain-separation golden vectors reject dispatch, reference order, reference-use,
   route-policy, option, operation-payload, or verifier-policy drift;
3. state-machine tests cover every legal transition, terminal immutability, and unknown-outcome
   reconciliation to response, failure, or authoritative cancellation;
4. dispatch tests prove one local send without provider idempotency, safe same-attempt retransmit
   only with a declared guarantee, and no claim of exactly-once provider billing;
5. fallback tests distinguish provider-internal trace from new Moodboard attempts and reject model
   substitution or privacy-boundary escape;
6. normalized-request tests bind exact prompt, adapter/options, operation-input delivery, and
   actual ordered reference use, including source/mask derivatives;
7. output validation binds original bytes, SHA-256, ContentRef, MIME, dimensions, admission, and
   producer lineage before attempt success, and exact replay cannot mint a second occurrence for
   one attempt/output index;
8. secret scanning covers artifacts, logs, frontend bundles, exports, and error projections; and
9. no implementation adds a Khive or Lattice contract without a separate owning-repository ADR.
