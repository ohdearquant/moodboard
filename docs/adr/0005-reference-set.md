# ADR-0005: The reference set is an input with properties, not a folder of images

- **Status:** Proposed
- **Date:** 2026-08-07
- **Measurable claim:** yes. Near-duplicate references inflate the board size without adding
  information, and the size is what every guarantee in ADR-0003 and every threshold in
  ADR-0004 is computed from. Dataset row: `effective-board-size` in
  [`DATASETS.md`](../../DATASETS.md), built by resampling existing data.

## Context

ADR-0003 makes the board size load-bearing. The score's resolution is 1/(n+1), the covariance
shrinkage is chosen for n against several hundred dimensions, and ADR-0004 refuses thresholds
by comparing them against that same n. Every one of those uses n as if it were a count of
independent observations.

It is a count of files in a directory.

A designer assembling twenty references does not sample them independently from the brand's
distribution. They export four crops of the same shoot, three colourways of one layout, and a
handful of frames from one campaign. The directory holds twenty images and perhaps six
distinct pieces of information. The arithmetic downstream does not know the difference, and
every quantity it produces is stated with more confidence than the input supports.

The direction of the error is the dangerous one. Duplicates make the reference set look
*tighter* than the brand really is, because the repeated look dominates the fitted
distribution. So the tool becomes more confident and more restrictive at the same time, and it
rejects legitimate assets while reporting a finer resolution than it has. Nothing in the
output would look wrong.

A second property matters and has no home yet. A score is a statement about a specific board.
When the board changes, every previously issued score describes a board that no longer exists,
and there is currently nothing in the system that would notice.

## Decision

**The board is a versioned artifact with a content hash, and every score names it.** The
`brand.mb` file carries the hash and ADR-0002's report echoes the same value under
`board.id`. One definition, used in both places: sha256 over a canonical JSON serialisation,
with sorted keys and no insignificant whitespace, of the object

    {"v": 1, "refs": [sorted reference content hashes],
     "model": {"repo": …, "revision": …},
     "fit": {"metric": "cosine", "k": …, "cluster_cut": …, "dup_cut": …}}

Two scores are comparable when their board hashes match and are not comparable otherwise.
Including the fitting parameters is the load-bearing part: an earlier version had ADR-0002
hashing the reference content alone while this record hashed content plus parameters, which
means changing k or a clustering cut would have preserved the identifier under one record and
changed it under the other, and scores fitted under incompatible parameters would have been
put side by side under a matching id. Any parameter that can change a score belongs inside
this hash, and adding one bumps `v`.

**The tool reports effective sample size, not just file count.** At build time, group the
references by near-duplication and report both n, the number of files, and n_eff, an estimate
that discounts near-duplicates.

The estimator is pinned, because "an estimate that discounts near-duplicates" is a sentence
several incompatible formulas satisfy. Near-duplicate grouping is a **separate** procedure
from the sub-look clustering in ADR-0004 and uses the opposite distance regime: sub-looks are
far apart, duplicates are almost coincident. Group references by single-linkage agglomerative
clustering on the L2-normalised style embeddings under cosine distance, cut at 0.05, giving
groups g₁ … g_m of sizes s₁ … s_m summing to n. Then

    n_eff = (Σ sᵢ)² / Σ sᵢ²

which is Kish's effective sample size applied to equal weights within a group. It equals n
when every group is a singleton, equals m when all groups are the same size, and lies between
otherwise. It is reported as a real number and never rounded before use. An earlier version
of this record said the estimate came from "the same clustering ADR-0004 already needs";
that was wrong in a way that would have shipped, because one cut cannot separate sub-looks
and collapse duplicates at once.

**n_eff is a reported diagnostic and an admissibility floor. It is not the conformal
denominator.** An earlier version of this record said every downstream rule reading n reads
n_eff instead, and that rule is withdrawn here rather than softened. The achievable p-values
of a conformal score are determined by the number of ranked calibration scores actually in
the bag, which is a count of files and cannot be a fractional estimate; 1/(n_eff+1) is
frequently not an achievable value at all, and substituting it does not restore
exchangeability after clustered selection — it only relabels the same score. So:

- **The score's denominator stays n_local**, the count of references in the candidate's
  category, exactly as ADR-0003 and ADR-0004 define it. The report carries it.
- **n_eff gates what the tool will agree to answer.** A requested α is honoured only when it
  is at least 1/(n_eff_local+1). This is a conservative policy floor, labelled as one, and it
  is the mechanism by which a board of twenty near-identical files stops advertising
  twenty-file resolution.
- **The report carries n, n_eff and the requested α side by side**, so a reader can see the
  gap rather than inferring it from a single corrected number.

Restoring the guarantee itself under dependent references needs a weighted or cluster
conformal construction with its own theorem, which is the deferred alternative at the end of
this record and not something a substitution can stand in for.

**A board is not rejected for containing duplicates.** They are usually there for a good
reason and removing them is the user's decision, not the tool's. The tool reports the gap,
names the groups it found, and computes its guarantees from the honest number. Telling someone
their twenty-image board behaves like a six-image board is useful; deleting fourteen of their
files is not.

**Effective size is estimated from a second clustering, not the one ADR-0004 already needs.**
An earlier version of this record said the opposite, and the retraction is above; the sentence
is restated here in corrected form rather than deleted, because it survived its own retraction
once and a reader landing on this paragraph alone would otherwise rebuild the withdrawn
version. The duplicate cut is single-linkage at 0.05 and the sub-look cut is average-linkage
at 0.35, and no single pass can serve both: sub-looks are far apart, duplicates are nearly
coincident, so a threshold tuned to find one is blind to the other. This therefore adds a
mechanism and not merely a number, and the cost is named rather than absorbed. The estimator
and its parameters are recorded in the report, because an unstated correction to a sample size
is a worse problem than an uncorrected one.

Both cuts are pinned a priori and neither has moved, but encoder revision 2 changed the
distances they are applied to, which is recorded as an amendment under ADR-0004 rule 2. The
two cuts are affected very differently: the 0.35 sub-look cut sees roughly half its pairs
change side, while the 0.05 duplicate cut barely moves, because near-coincident pairs stay
near-coincident under any reweighting of the blocks. That asymmetry is a consequence of the
same argument this paragraph makes for why one pass cannot serve both.

## Acceptance criteria

This record stays `Proposed` until the following is measured.

**Duplicates change the answer, and n_eff tracks the change.** Take a group with enough
members to build disjoint boards. Build a board of k genuinely distinct references. Build a
second board of the same file count where some references are near-duplicates of others,
generated by crops, mild recolours and recompressions of members already present. Score a
fixed held-out set against both.

Two numbers must move, and pre-registration lives in `eval/thresholds.json` under
`effective_board_size`:

1. The duplicated board must be measurably more restrictive on held-out on-style assets than
   the distinct board of the same file count. **The effect is fixed now, before any pilot:**
   a rejection-rate difference of at least 0.10 in absolute terms at α = 0.05, one-sided, on
   a held-out on-style population of 500 assets across 20 board pairs. At that size the
   standard error on a rejection-rate difference near 0.2 is under 0.03, so 0.10 is more than
   three standard errors and the pass/fail boundary is not a coin flip. An earlier version
   deferred this number until "the pilot fixes the sampling error", which is a threshold
   chosen after seeing the result wearing a pre-registration label — the exact move the
   preamble of `eval/thresholds.json` exists to forbid. If the pilot shows the sampling error
   was badly misjudged, that is a commit to this file stating what was learned, which is the
   visible act the policy intends.

   If the effect is below 0.10, the premise of this record is wrong and it should be rejected
   rather than kept for tidiness.
2. n_eff on the duplicated board must land closer to the count of distinct sources than to the
   file count, within the tolerance in `eval/thresholds.json`. The tolerance is a fraction of
   the interval between those two counts, so it has a unit and a denominator:
   `|n_eff − distinct| / |file_count − distinct| ≤ 0.25`.

The first is the falsifier and it comes first deliberately. It is entirely possible that the
effect is negligible at these board sizes, and this record should be capable of dying on that
measurement instead of surviving because the correction it proposes was implemented.

**A control that keeps the first number honest, and it does not run on n_eff.** The confound
is that a board carrying less information is more restrictive for reasons this hypothesis does
not predict, since a board of six distinct references is also more restrictive than a board of
twenty. The control holds the **ground-truth distinct-source count** constant: the duplicated
board of n files built from d distinct sources is compared against a genuinely distinct board
of d files, and criterion 1's effect must survive that comparison as well as the equal-file
one. An earlier version said to compare at equal n_eff, which is circular — n_eff is the
estimator under test, so matching on it validates the estimator by construction and measures
nothing. The distinct-source count is known because the duplicates are generated, and it is
withheld from the estimator, which is what makes criterion 2 a test rather than a restatement.

## Alternatives considered

**Deduplicate the reference set automatically at build time.** Rejected. It silently discards
user input on the basis of a threshold the user never saw, and the cases it would get wrong
are exactly the legitimate ones: a brand whose look genuinely is a tight variation on one
setup would be stripped down to a board too small to score anything.

**Require the user to declare independence.** Rejected as unanswerable. Nobody assembling a
moodboard knows how many effectively independent looks it contains, and asking produces a
number that is worse than an estimate because it carries the authority of having been
declared.

**Ignore it and document the assumption.** This is the status quo, and it is the option this
record exists to reject. The assumption is invisible at the point of use and its failure
biases toward false confidence, which is the combination that does not survive a user finding
it before we do.

**Weight references instead of counting them.** Attractive and deferred. A continuous weighting
would flow naturally into the conformal machinery, but the guarantee in ADR-0003 is stated for
exchangeable observations and a weighted version needs its own justification rather than an
adaptation of the same sentence. Worth its own record if the measurement above shows the
effect is large.

## Consequences

The build step gains a second clustering, run at its own cut and distinct from the sub-look
clustering ADR-0004 requires, plus a hash, which is trivial. The report gains two fields.
Counting the clustering as free because ADR-0004 already clusters is the conflation this
record retracts above, so the real cost is stated: two passes over the references, not one.

The admissibility floor in ADR-0004's rule 1 reads n_eff; the score's denominator in
ADR-0003 does not. Both records carry that split in their own text rather than inheriting it
from here, because a rule that lives in one record and governs another propagates by memory,
which is how the two hash definitions drifted apart. Those records are `Proposed`, so this is
an amendment rather than a supersession.
