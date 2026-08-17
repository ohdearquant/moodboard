# Evaluation thresholds, and why they are written down first

`thresholds.json` holds every number an acceptance criterion depends on, and each one is
fixed before the measurement it governs is run for the first time.

The reason is narrow and worth stating plainly. Two of the decision records originally said
that coverage must not fall short "by more than a stated tolerance" and that the style
representation must beat the baseline "by a clear margin". Neither number was written
anywhere, which leaves the judgment to be made after the result is known. At that point the
honest outcome and the convenient one are indistinguishable from outside, and they are
frequently indistinguishable from inside as well.

Changing a threshold is allowed. It is not allowed to be quiet. A change needs a commit that
says what was learned and why the previous value was wrong, and the history of this file
shows whether a bar ever moved to meet a measurement.

## The numbers, and how each was chosen

**Interval coverage, `min_observed_coverage` 0.85 against a stated level of 0.90.** With
1,000 resamples, the standard error of an estimated coverage near 0.9 is about 0.0095 if the
resamples were independent. Five percentage points is more than five of those standard
errors, so a shortfall that large is a real miscalibration rather than sampling noise.

**A shortfall smaller than five points is not thereby noise, and this paragraph used to say
it was.** At the same standard error a three-point shortfall is about 3.2 standard errors,
which is a detectable miscalibration that this gate deliberately tolerates. The gate is a
tolerance, not a detection threshold, and the difference matters because the old wording
justified the number with a claim about noise that the arithmetic does not support. Two
consequences are accepted deliberately: an observed 0.87 passes and the report then says 0.87
rather than 0.90, and the correction rule is what carries the honesty rather than the gate.

Two further caveats, stated because a coverage figure invites more confidence than it earns.
The resamples are drawn from the same group and share references, so they are not independent
Bernoulli trials and the true standard error is larger than 0.0095; the reported interval
around the coverage estimate is a cluster bootstrap over source images, not the binomial
figure quoted above, which is used here only to size the gap between pass and fail. And
coverage is pooled per board size *and* per group, gated on the worst group, because a pooled
figure can sit comfortably above 0.85 while a minority group runs at 0.70 and every report
still claims 0.90.

**Interval sharpness, `max_median_interval_width` 0.25 and `max_all_tied_rate` 0.50.**
Coverage on its own cannot pass this criterion. An interval of [0, 1] covers every score
perfectly and makes every asset tie with every other, so an instrument measuring only
coverage certifies that the tool declines to distinguish anything. These two bounds are what
make the coverage number mean something, and the direction of error they guard is the one the
tie rule creates: over-wide intervals look conservative and read as caution.

**Content invariance, `min_auc_absolute` 0.80 and `min_margin_over_clip` 0.10.** Two
conditions, because either one alone can be satisfied by something useless. A large margin
over a baseline that is itself near chance would mean both are unusable, so there is an
absolute floor. A high absolute score with no margin would mean the property came from the
data rather than from the representation, so there is a margin.

The figure acceptance reads is the **cell-balanced** AUC. PACS cells run from 80 items to
816, so an all-pairs AUC is weighted by cell size and is mostly a statement about the large
cells; both are reported and the all-pairs value is secondary. The uncertainty is a bootstrap
over **images**, not over pairs. Millions of pairs built from thousands of images are heavily
dependent, so a pair-level standard error is far too small — the "tens of thousands of
eligible pairs, standard error well under 0.01" reasoning this paragraph used to carry
counted dependent pairs as independent evidence. At the image level the margin of 0.10 is
still comfortably clear of the noise, which is why the threshold does not move; the
justification for it does.

**Off-style rejection, `max_inversions` 0, and it no longer gates.** This is the weakest of
the tests and it is the one a reader will run by hand with two obviously different folders.
At that scale a single inversion is worth looking at rather than tolerating, so the threshold
is zero and any inversion is reported with both images named. It is marked informational
because on the only runnable source its groups differ by medium: a green result says a
photograph is not a sketch, which is not the property being certified. It becomes a gate on a
source whose groups differ by treatment within a medium.

## The partial-pass rule

`content_invariance.both_datasets_must_pass` is `false`, and that is deliberate rather than
lenient. Passing on illustration and failing on brand photography is a real and likely
outcome, since the two differ by how far apart the styles are. It does not make the tool
worthless, and it does make the claim narrower. The rule is that the record is not accepted
as written, the claim is narrowed to the domain that passed, and the narrowing goes in the
README where a reader will see it rather than in a footnote.

## OpenRouter real-provider evaluation is a two-stage confirmation

`openrouter_real_e2e.py` is an opt-in evaluation harness, not a general provider CLI. Preparation
fetches and freezes exact discovery and public source bytes, compiles the authoritative rectangle
mask and visible overlay, binds the board/retrieval authority, computes the exact provider wire
identity, and writes an owner-only confirmation challenge. Preparation has no credential or
transport parameter and cannot dispatch.

Execution requires a separate, closed confirmation context that names the exact challenge and
compact summary, one enrolled principal, one Studio session, the same creative session, and one
fresh explicit approval. A document hash alone is not Studio authority: the production API has no
default confirmer and fails `confirmation_authority_unavailable` until a trusted Studio boundary
that atomically verifies and consumes the authorization is supplied. The retired Boolean
`authorize_one_paid_call` entry point always fails
`two_phase_confirmation_required`; the command line only reports the missing trusted-authority
integration and cannot prepare or dispatch a live challenge.

After all frozen bytes, identities, timestamps, directory inode, reconstructed wire, and a fresh
byte-identical discovery response agree, a caller-supplied Studio boundary must atomically and
durably consume the confirmation before Keychain access. This repository does not implement that
ledger: the default rejects execution, while the injected offline seam proves one-process CAS and
local `O_EXCL` concurrency behavior only. Credential-bearing work is contained in a non-raising
inner scope, core dumps are disabled before Keychain access, and private response and output bytes
remain in the owner-only journal/run directory.

The fixed `$0.05` value is a **quote-admission limit**, not a provider-enforced spending cap. It is
checked against the exact live discovery pricing before source access. Reported cost is post-hoc
telemetry: missing, differently reported, or unexpectedly high telemetry cannot undo a charge and
therefore does not strand an otherwise valid provider response before terminal media admission.
A receipt distinguishes the absence of a legible cost value (`not_reported`, covering absent or
non-object telemetry) from a present cost value the adapter could not certify
(`reported_uncertifiable`); the raw response bytes always retain the original. This covers telemetry the adapter could parse: a response whose JSON number lexemes
exceed the adapter's structural budgets is rejected as a malformed document by the bounded parse,
which is a document-integrity bound, not a telemetry judgment.
Reports distinguish provider lifecycle state, media admission, raw structural/locality evidence,
localized-edit gate status, workflow acceptance (`not_recorded`), semantic/aesthetic judgment
(`not_run`), and compositor execution (`not_run`).

No paid call is currently authorized. The available local Pixel-RAG evidence uses a retired
projection and fails the current public reader when supplied explicitly. A separately governed
evidence republication, a trusted authority-to-creative-session integration, a durable Studio
confirmation consumer, and a credential-free idempotent post-response finalizer are prerequisites
to a live run. Until those exist, the two-stage functions are an injected offline contract harness.

The executable acceptance map for this slice is:

| Condition | Evidence test |
| --- | --- |
| Prepare has no credential, transport, or Boolean authorization surface | `test_prepare_api_has_no_credential_or_transport_and_returns_frozen_challenge` |
| Exact discovery/source/authority/mask/overlay/summary bytes are bound | `test_prepare_freezes_exact_content_bound_snapshot_summary_and_overlay` and the artifact-drift matrix |
| Self-minted confirmation is insufficient without Studio authority | `test_production_default_rejects_self_minted_context_before_discovery_or_key` |
| Context, expiry, inode, fresh discovery, and rebuilt wire gate Keychain | confirmation-context, expiry, directory-swap, discovery-drift, and wire-drift tests |
| One injected in-process consumption winner can reach one fake POST | replay, ambiguity, and concurrent-executor tests |
| Quote arithmetic is exact and `$0.05` is pre-dispatch only | ambient-Decimal and over-quote tests |
| Missing post-paid cost telemetry does not strand valid media evidence | `test_missing_reported_cost_remains_terminal_success_after_paid_response` |
| Non-conforming cost telemetry degrades to explicit unavailability, never rejection | `test_nonconforming_cost_telemetry_degrades_to_unavailable_without_stranding` |
| A cost lexeme past the structural budget rejects the document (integrity bound, not telemetry) | `test_cost_number_is_bounded_before_decimal_expansion` |
| Credentials cannot survive public exceptions or local artifacts | real-E2E transport exception-graph tests |
| An unanticipated post-response failure still scans the private artifacts | `test_post_response_failure_still_scans_the_private_artifacts` |
| Real board/retrieval identities are derived, never label hashes | `test_openrouter_real_e2e_authority.py` |
