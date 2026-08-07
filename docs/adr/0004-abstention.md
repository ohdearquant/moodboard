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
things and nothing in the arithmetic reports that.

In all three the current design emits a confident-looking number. That is the worst available
behaviour, because the failure is invisible at the point of use and the report is designed to
be believed.

## Decision

**Abstention is an output, with the same standing as a score.** The report schema in ADR-0002
gains a state where the score field is absent rather than null, carrying a machine-readable
reason and the measurement that triggered it. Absent rather than null because a null in a
numeric field is read as zero by something eventually, and zero is the most confident possible
wrong answer here.

**Three rules, each with a stated trigger.**

1. **Resolution.** If the requested threshold is below 1/(n+1) for the board in hand, refuse
   the threshold and report the finest value the board supports. Do not round it up silently,
   which converts a request the tool cannot honour into an answer the reader will trust.

2. **Multi-modality.** Test the reference set for whether it is one cluster or several before
   fitting anything. When it is several, the default is to fit one local model per sub-look
   and score against the nearest, reporting which. Abstain only when the sub-looks are too
   small individually to satisfy rule 1, since that is the case where splitting produces
   several boards that are each too small to say anything.

3. **Out of distribution.** Score the asset's own conformity to the board's *medium* before
   scoring its style, and abstain when the asset is further from every reference than the
   references are from each other by a margin. This reuses the same nonconformity machinery,
   so it adds a rule rather than a mechanism.

**Refusal explains itself in the same words a designer would use.** "This board has 10
references, so the finest distinction it can express is about 9%, and you asked for 5%" is a
sentence a person can act on, by adding references. "ABSTAIN: resolution" is not.

## Acceptance criteria

This record stays `Proposed` until both measurements exist.

**1. The rules fire when they should.** Construct the triggering conditions from data already
in the repository: boards resampled down to sizes below the requested threshold's resolution,
boards deliberately built from two disjoint style groups, and assets drawn from a medium
absent from the board. Every constructed case must abstain, and the reason must be the one
constructed.

**2. The rules stay quiet when they should — and this is the harder half.** A refusal rule
that fires on everything is trivially safe and useless, and it would pass the first criterion
completely. So the same measurement runs on well-formed boards with in-distribution assets,
where the abstention rate must be low. Pre-registered in `eval/thresholds.json` under
`abstention`: at most 5% false abstention on single-look boards of 20 or more with assets from
the board's own group.

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

Rule 2 requires clustering the reference set, which is work ADR-0003 did not call for. It is
also the natural place to detect near-duplicate references, which reduce the effective sample
size and therefore feed back into rule 1. That interaction is the subject of ADR-0005.
