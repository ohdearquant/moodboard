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
