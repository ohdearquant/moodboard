# ADR-0004: The tool refuses to score when it cannot, and refusing is a first-class output

- **Status:** Proposed
- **Date:** 2026-08-07
- **Measurable claim:** yes. The refusal rules must fire on inputs that warrant them and stay
  quiet on inputs that do not, and both directions are measured. Dataset rows:
  `abstention-triggers` in [`DATASETS.md`](../../DATASETS.md), built by resampling the
  existing `content-invariance-coarse` data rather than acquiring anything new.

## Context

Every command in this tool takes a board and an asset and returns a number. The design so far
has no path where it returns nothing, and that is a defect rather than a simplification.

Three situations make the number meaningless, and all three are ordinary rather than exotic.

**The board is too small to express the answer.** ADR-0003 established that the score is a
conformal p-value whose achievable values are the multiples of 1/(n+1). At the bottom of the
supported range, ten references, the finest expressible value is 0.0909. A threshold below
that cannot fire no matter what the candidate looks like. A tool that accepts a 0.05 rule on
a ten-image board and then never rejects anything is not conservative, it is broken in the
direction that looks like agreement.

**The board is not one look.** A brand routinely carries sub-looks, a product line and a
campaign line, or a photographic register and an illustrative one. Fitting one distribution
across both puts the centre in the gap between them, where no reference lives and no real
asset belongs. The score is then lowest for assets that sit squarely in either genuine look,
which inverts the answer rather than degrading it.

**The asset is not the kind of thing the board is made of.** A board of photographs scoring a
vector logo, a UI screenshot or a video frame produces a number by construction, because the
encoder returns a vector for any image. The number is a comparison between incomparable
things and nothing in the arithmetic reports that. What the tool can actually observe about
this case is distance, not medium, and rule 3 below claims only what it observes.

In all three the current design emits a confident-looking number. That is the worst available
behaviour, because the failure is invisible at the point of use and the report is designed to
be believed.

## Decision

**Abstention is an output, with the same standing as a score.** The report schema in ADR-0002
gains a state where the score field is absent rather than null, carrying a machine-readable
reason and the measurement that triggered it. Absent rather than null because a null in a
numeric field is read as zero by something eventually, and zero is the most confident possible
wrong answer here.

**Three rules, each with a stated trigger. Every trigger below is an executable predicate, and
that is deliberate: a refusal rule described in prose is a rule each implementer writes
differently, which is the same defect as no rule at all.**

1. **Resolution.** If the requested threshold α is below 1/(n_local+1) for the board in hand,
   refuse the threshold and report the finest value the board supports. `n_local` is the
   number of references in the candidate's own category under rule 2, and equals n on a
   single-look board. Do not round α up silently, which converts a request the tool cannot
   honour into an answer the reader will trust. The comparison is strict: α exactly equal to
   1/(n_local+1) is honoured, because that value is achievable.

2. **Multi-modality.** The reference set is partitioned before anything is fitted, by a
   procedure that is a symmetric function of the augmented bag — the n references plus the
   candidate — so that the partition is a Mondrian taxonomy and the category-conditional
   p-value keeps its finite-sample guarantee. Pinned: average-linkage agglomerative
   clustering on the L2-normalised style embeddings under cosine distance, cut at a fixed
   distance of 0.35, with ties in the merge order broken by the pair whose members have the
   lexicographically smaller content hashes. Categories holding fewer than 5 members are
   merged into their nearest surviving category, so the partition never manufactures a
   category too small to calibrate. The candidate is scored inside its own category, against
   that category's members only, by the same construction ADR-0003 specifies with n_local in
   place of n. The report names the category.

   The permutation symmetry is the load-bearing part and it is why the clustering runs on the
   augmented bag rather than on the references alone. Clustering the references, then
   assigning the candidate to the nearest resulting cluster, treats the candidate differently
   from every calibration point and forfeits the guarantee. Both procedures produce the same
   answer nearly always and differ exactly on the boundary cases the guarantee exists for.

   Abstain only when the candidate's own category cannot satisfy rule 1 at the requested α.
   **Detecting several sub-looks is not by itself a reason to abstain** — it is a reason to
   score locally and say so, which is a reported outcome and not a refusal.

3. **Far-outlier.** Abstain when the candidate is a gross outlier against the whole board, on
   the same nonconformity scale already computed: abstain when α_cand exceeds
   max(α₁ … αₙ) + 1.5 × IQR(α₁ … αₙ), the conventional Tukey far-outlier rule, applied to
   the board's own leave-one-out nonconformity values. This reuses the same machinery, so it
   adds a rule rather than a mechanism, and it has no free parameter beyond the 1.5 fixed
   here.

   This rule used to be stated in terms of the asset's *medium* — abstaining when a board of
   photographs was handed a vector logo. That framing is withdrawn. It named a medium
   taxonomy, a medium classifier and a margin, none of which exist in this project and each
   of which would have been a second representation to validate. What the machinery can
   actually see is distance, and a vector logo against a board of photographs is a far
   outlier by distance. The rule now claims what it measures: this asset is nothing like
   these references. It will not fire on a merely unusual in-medium asset, which is correct,
   because that case is what a low p-value is for.

**Refusal explains itself in the same words a designer would use.** "This board has 10
references, so the finest distinction it can express is about 9%, and you asked for 5%" is a
sentence a person can act on, by adding references. "ABSTAIN: resolution" is not.

## Acceptance criteria

This record stays `Proposed` until all three arms below are measured. There are three rather
than two because rule 2 has two required outcomes — refuse a sub-look too small to answer,
score one that is large enough — and an instrument measuring only the refusal cannot tell a
working rule from one stuck on abstain.

**Every case below names its requested α.** The same board is serviceable at one α and must
refuse at another, so a constructed case without an α has no defined outcome. This was left
implicit and is now fixed in `eval/thresholds.json`.

**1. The rules fire when they should.** Construct the triggering conditions from data already
in the repository. Every constructed case must abstain, and the reason must be the one
constructed:

- *Resolution.* Boards resampled to n = 10 with α = 0.05, where 1/(n+1) = 0.0909 > α.
- *Multi-modality.* Boards built from two disjoint PACS domains, **each sub-look resampled to
  8 members**, with α = 0.05: 1/(8+1) = 0.111 > α, so neither category can express the
  request and rule 2 refuses. The sub-look size is pinned here because the rule scores
  locally whenever it can, so a two-group board with large sub-looks is a case the tool is
  supposed to *score*, and requiring it to abstain would demand behaviour the decision rule
  forbids. That contradiction was live in an earlier version of this record and would have
  made the must-fire arm unsatisfiable by any conforming implementation.
- *Far-outlier.* Assets drawn from a PACS domain absent from the board, which are far
  outliers by distance on any board built from a single other domain.

**1b. The rules report multi-modality without refusing it.** Boards built from two disjoint
PACS domains with **each sub-look at 25 members** and α = 0.05, where 1/(25+1) = 0.0385 < α.
Required outcome: a score, not an abstention, carrying the category the candidate was scored
in. This is the other half of rule 2 and it is a distinct arm because a stuck-on-abstain
implementation passes criterion 1 and fails only here.

**2. The rules stay quiet when they should — and this is the harder half.** A refusal rule
that fires on everything is trivially safe and useless, and it would pass the first criterion
completely. So the same measurement runs on well-formed boards with in-distribution assets,
where the abstention rate must be low. Pre-registered in `eval/thresholds.json` under
`abstention`: at most 5% false abstention, measured across **every supported board size at an
α that size can express** — n = 10 at α = 0.10, n = 20 at α = 0.05, n = 50 at α = 0.02 — and
on multi-look boards whose sub-looks each satisfy the requested α. Restricting this arm to
single-look boards of 20 or more, as an earlier version did, leaves the small-board and
multi-look populations unmeasured, which are exactly the populations rule 1 and rule 2 are
most likely to refuse without cause. False-abstention rates are reported per reason, since a
pooled rate can hide one rule firing constantly while the others stay silent.

Both directions or neither. Reporting only the first would be an instrument that cannot
distinguish a working rule from a stuck one.

## Alternatives considered

**Always return a number, and put the caveats in the report.** Rejected. The caveat and the
number travel to different places: the number gets pasted into a deck and the caveat stays in
the JSON. A report format cannot fix a problem created by the report's own existence, and this
project's whole claim is that the number means something specific.

**Return a number with a confidence interval that widens instead of abstaining.** Genuinely
attractive, and rejected for the small-board case specifically, because the interval on a
ten-image board spans nearly the whole range and a reader takes the point estimate anyway. It
is kept for the multi-modal case, where the uncertainty is real and bounded rather than total.

**Require a minimum board size and refuse to build below it.** Rejected as the primary
mechanism because it moves the refusal to build time, where the user has no asset in front of
them and no way to judge whether the restriction matters. The resolution rule says the same
thing at the moment it is actionable.

**Let the user override abstention with a flag.** Deferred rather than rejected. It is a
reasonable escape hatch, and it is also how a safety property becomes decorative. If it is
added it needs its own record and the override has to appear in the report output, so that a
report produced under override is distinguishable from one that was not.

## Consequences

ADR-0002's schema is not yet accepted, so this is an amendment to a proposal rather than a
break of a contract. The schema change is small and the viewer must render the refusal as
prominently as it renders a score, which is a viewer requirement and not a formatting
preference.

Rule 2 requires partitioning the reference set, which is work ADR-0003 did not call for.
Near-duplicate detection also feeds rule 1, through the n_eff floor, and ADR-0005 is where
that lives. It is a **second** grouping with its own cut, not this one reused: sub-looks are
far apart and duplicates are nearly coincident, so one distance threshold cannot serve both.
An earlier version of this paragraph called rule 2's clustering "the natural place" to detect
duplicates, and ADR-0005 inherited that and specified an estimator on top of it, which would
have shipped a duplicate detector that could not see duplicates.
