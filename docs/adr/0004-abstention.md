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

1. **Resolution. Two floors, and the binding one is whichever refuses more.** Refuse the
   threshold, and report the finest value the board supports, when either of these fails:

   - **Achievability:** α ≥ 1/(n_local+1). `n_local` is the number of references in the
     candidate's own category under rule 2, and equals n on a single-look board. This one is
     a count of files, because the achievable p-values of a conformal score are the multiples
     of one over the number of ranked calibration scores actually in the bag.
   - **Admissibility:** α ≥ 1/(n_eff_local+1), where `n_eff_local` is the ADR-0005 Kish
     effective size computed over the candidate's own category. Since n_eff ≤ n_local, this
     arm is the stricter of the two whenever the board carries near-duplicates, and it is the
     only one that fires on them.

   Both comparisons are strict: α exactly equal to a floor is honoured, because that value is
   achievable. Do not round α up silently, which converts a request the tool cannot honour
   into an answer the reader will trust. The report names both numbers and which one bound,
   since they differ exactly when the board has a problem worth showing.

   **The second arm is the one that was missing, and its absence was under-protection rather
   than an omission of detail.** Rule 1 previously read only the file count, so a board of
   twenty files built from six distinct sources advertised twenty-file resolution and honoured
   an α its diversity could not support — precisely the defect ADR-0005 exists to prevent,
   left unenforced in the single predicate able to enforce it. ADR-0005's Consequences already
   asserted that this rule reads n_eff; that assertion was false about this record until now,
   and by this record's own standard, that a rule described only in prose is a rule each
   implementer writes differently, the floor did not exist. n_eff enters here as a floor and
   nowhere else: it is never the conformal denominator, which ADR-0005 states at length
   because the opposite rule was withdrawn.

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

   **Amendment: encoder revision 2 did not change this cut, it changed what the cut is
   applied to.** 0.35 was pinned a priori and is unchanged, which is what makes the change
   easy to miss: nothing in this record moved. Revision 1 unit-normalised the concatenated
   descriptor once, so the block with the largest raw norm dominated the vector; revision 2
   unit-normalises palette, tone and composition separately before concatenating, so each
   carries a third. Measured by `eval/encoder_revision_figures.py`, over the 7,140 pairs among
   the 120 synthetic images the two families in `tests/test_cli.py` draw at seed 20260808, the
   fraction of pairs falling above this 0.35 cut goes from **21.3%** under revision 1 to
   **48.9%** under revision 2, and the median pair distance from **0.2581** to **0.3414**.
   Roughly half the pairs now land on the far side of a cut that about a fifth did. The 0.05
   duplicate cut is barely touched by comparison, **93.6%** to **91.1%**, because
   near-coincident pairs stay near-coincident under any reweighting.

   **Those six figures were replaced on 2026-08-08 and the previous ones are withdrawn rather
   than corrected, because they could not be reproduced.** This paragraph previously read 22.1%
   to 49.8%, a median of 0.2546 to 0.3488, and 93.8% to 91.4%. Those came from an ad-hoc
   measurement that was never committed, on a population whose seed and size were not recorded,
   so there is no way to run it again and no way to tell whether the difference is the seed, the
   count, or an error. The replacement is not a re-measurement of the same thing; it is the
   first measurement of this kind that a reader can repeat, and every figure above now names the
   command that produces it. The direction is worth stating plainly because it runs against the
   author: the withdrawn numbers were slightly LARGER on the headline shift, so this record
   previously overstated its own effect by about a point. The qualitative claim is unchanged and
   is what the conclusion rests on.

   The magnitude is population-dependent and should not be quoted as a constant. On the
   pre-revision generator, which varied hue alone, the same comparison gave a much larger
   shift; that generator no longer exists in the tree, having been replaced precisely because
   it did not exercise revision 2. What is stable across both populations, and is the
   substantive finding, is that composition contributed **0.0%** of revision-1 energy — the
   axis was in the contract, in the report and in the vector, and was arithmetically incapable
   of affecting any clustering decision. A reader deciding whether a revision-1 calibration
   transfers should treat it as not transferring and re-derive, rather than reasoning from the
   cut's unchanged value.

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
- *Resolution, effective-size arm.* A board of **20 files built from 6 distinct sources**
  (four sources contributing 3 near-duplicate copies each, two contributing 4), at α = 0.05.
  The arithmetic is what makes this case discriminating rather than merely additional:
  1/(20+1) = 0.0476 < α, so the **achievability arm honours the request**, while
  n_eff = 20²/(4·3² + 2·4²) = 400/68 = 5.88 gives 1/(5.88+1) = 0.145 > α, so the
  **admissibility arm refuses**. A conforming implementation abstains here **only if the
  n_eff floor exists**; one that reads the file count alone returns a score and passes every
  other must-fire case. The report must name `effective` as the binding floor, since a
  refusal that says only "resolution" cannot distinguish which arm fired.

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
on multi-look boards whose sub-looks each satisfy the requested α, **and on a mildly
duplicated board whose n_eff still admits the requested α**: 50 files from 40 distinct
sources (30 singletons, 10 pairs), α = 0.05, where n_eff = 50²/(30·1² + 10·2²) = 2500/70 =
35.7 and 1/(35.7+1) = 0.027 < α. That row is what stops the new effective-size arm being
satisfied by an implementation that simply refuses whenever any duplicate is present, which
would pass the must-fire case above and be useless. A floor needs both directions for the
same reason every other rule here does. Restricting this arm to
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
