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
1,000 resamples, the standard error of an estimated coverage near 0.9 is about 0.0095. Five
percentage points is therefore more than five standard errors, so a shortfall that large is a
real miscalibration rather than sampling noise, and a shortfall smaller than that is not
distinguishable from noise and should not fail the criterion. On a genuine shortfall the
reported level moves to the observed value rather than the measurement being adjusted.

**Content invariance, `min_auc_absolute` 0.80 and `min_margin_over_clip` 0.10.** Two
conditions, because either one alone can be satisfied by something useless. A large margin
over a baseline that is itself near chance would mean both are unusable, so there is an
absolute floor. A high absolute score with no margin would mean the property came from the
data rather than from the representation, so there is a margin. Both PACS and the Unsplash
collections yield tens of thousands of eligible pairs, so the standard error on an AUC is
well under 0.01 and a margin of 0.10 is an order of magnitude clear of it.

**Off-style rejection, `max_inversions` 0.** This is the weakest of the three tests and it is
the one a reader will run by hand with two obviously different folders. At that scale a
single inversion is worth looking at rather than tolerating, so the threshold is zero and any
inversion is reported with both images named.

## The partial-pass rule

`content_invariance.both_datasets_must_pass` is `false`, and that is deliberate rather than
lenient. Passing on illustration and failing on brand photography is a real and likely
outcome, since the two differ by how far apart the styles are. It does not make the tool
worthless, and it does make the claim narrower. The rule is that the record is not accepted
as written, the claim is narrowed to the domain that passed, and the narrowing goes in the
README where a reader will see it rather than in a footnote.
