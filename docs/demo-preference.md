# Governed preference replay for the Adobe demo

The preference replay demonstrates a narrow, auditable claim: Khive can collect
randomized pairwise events over Moodboard's governed 10-dimensional feature artifact,
refuse training below its support floor, publish immutable FANN model snapshots, and
measure how a second disclosed **simulated feature policy** changes predictions on a
frozen conflict set.

It is deliberately separate from coherence and conformal evidence. It is also separate
from Pixel RAG retrieval quality (hubness, MMR, precision, and nDCG). Those are different
evidence domains and do not become preference evidence merely because the same assets are
used.

## Required input and isolation

Pass the closed `moodboard.preference-feature-artifact.v2` emitted by `moodboard rank` with
`--preference-features-output`. The reader rederives its candidate-pool and complete-scope
digests before the first Khive operation. The demo pool needs enough distinct SHA-split pairs
for the allocations below; capacity is preflighted before any event is written.

Generate the governed artifact and its Khive board entity in the namespace that will run
the replay. Use a fresh `policy-simulated` actor/board scope for every replay; a new namespace
for the whole rank-and-replay run is the cleanest isolation. The actor must contain
`policy-simulated`, for example `lambda:adobe-demo-policy-simulated`. This prevents these
synthetic events from being attributed to a human. Existing events are not deleted or
ignored. Contamination is caught either by the exact initial zero-train support refusal or by
the trained model's exact split-count checks.

The callable entry point is `moodboard.preference_demo.replay_preference_demo`. Both the
initial client and the client returned by `restart_client_factory` must point at the same
Khive state, actor, and namespace; the factory must create a new client instance so the
restart check is meaningful.

```python
from pathlib import Path

from moodboard.khive import KhiveClient
from moodboard.preference_demo import replay_preference_demo

executable = Path("/absolute/path/to/kkernel")
config = Path("/absolute/path/to/fresh-khive.toml")
actor = "lambda:adobe-demo-policy-simulated"
namespace = "adobe-demo-preference-replay-20260812"


def client() -> KhiveClient:
    return KhiveClient(
        executable=executable,
        config=config,
        actor=actor,
        namespace=namespace,
    )


replay_preference_demo(
    client=client(),
    artifact_path=Path("/absolute/path/to/preference-features.json"),
    restart_client_factory=client,
    output_path=Path("/absolute/path/to/preference-replay.json"),
)
```

Do not rerun this snippet against the same actor/board scope. Create a new isolated state or
run rank and replay in a new namespace instead; Khive judgments are append-only evidence.
The output path must differ from the input artifact path, including after path resolution,
and must not already exist. Publication is atomic and no-clobber, including against a file
that races into place after preflight.

## What one replay does

Pair identity is the unordered pair of candidate content refs. Split assignment exactly
matches Khive's `moodboard-pair-split-v1`: SHA-256 over board ID, descriptor fingerprint,
feature schema ID, and ascending content refs, then buckets 0–13 train, 14–16 calibration,
and 17–19 test.

1. Validate the closed feature artifact and preflight all pair capacity.
2. Call `train_preference` with zero judgments and retain the exact below-support refusal.
3. Append distinct Policy A pairs: 64 train decisive, 16 calibration decisive, 16 test
   decisive, and 16 different calibration ties. Khive randomizes display side; if a real
   split happens to lack either a displayed-left or displayed-right winning label, the
   replay adds distinct reserved pairs until both exist. `phase_counts` records the realized
   counts rather than pretending the repair did not happen.
4. Train immutable model A and infer on eight unjudged, frozen pairs where Policy A and
   Policy B prefer opposite candidates.
5. Append 96 **new** train pairs labeled by Policy B. They are deterministically selected
   from the largest Policy B feature margins, which gives the real optimizer more signal
   than a balanced 64-versus-64 cancellation while keeping runtime bounded. Calibration,
   held-out test labels, ties, and probes remain unchanged.
6. Train immutable model B and infer on the same frozen probes. Re-run model A after B and
   require byte-for-value prediction equality. Create a fresh client, reload B by immutable
   model ID, and require exact prediction equality again.

Every event records the submitted pair, displayed left and right identities, serve and
judgment IDs, randomized occurrence IDs, `swap_applied`, displayed-side choice, semantic
winner, split, and policy ID. Returned occurrence identities must bind exactly to the
submitted candidates and reported swap; a mismatched Khive response fails before judgment.
No unordered pair is reused across decisive labels, ties, probes, or the second phase.

The output is canonical UTF-8 JSON with no wall-clock field. Its `replay_fingerprint` binds
the complete realized trace, including Khive's occurrence provenance and immutable model
identities. Pair selection is deterministic for one governed artifact. A genuinely fresh
real Khive run may have new event UUIDs and therefore a different trace fingerprint; that
is honest event identity, not selection drift.

## Reading the A-to-B result

`delta.mean_probability_for_policy_b_preferred_before` and `after` are measured on the
same eight policy-conflict probes. `adaptation_direction_observed` is true only when the
measured mean increases; the producer records the result and never substitutes an expected
direction. Khive's published training provenance retains train/calibration/test support,
optimizer identity, calibration, held-out metrics, network hashes, and verified FANN
inference.

The demo must always be described with all of these non-claims:

- Labels are `policy_simulated`; they are not human preference observations.
- This is immutable snapshot retraining, not online learning.
- Preference probability is not a coherence score or conformal p-value.
- The replay is not causal personalization and is not a user study.
- It makes no generalization claim beyond this artifact and frozen probe set.
- The A-to-B delta is only a frozen policy-conflict probe measurement.

The support refusal is itself a governance result: below the declared support, the system
refuses to manufacture a model. A polished demo should show that refusal before showing the
trained snapshots.
