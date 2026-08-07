# ADR-0003: Style representation, and the content invariance test that decides it

- **Status:** Proposed
- **Date:** 2026-08-07
- **Measurable claim:** yes, and it is the central claim of the project. The representation
  must respond to how an image looks and not to what it depicts. Dataset rows:
  `content-invariance-coarse`, `content-invariance-brand` and `off-style-rejection` in
  [`DATASETS.md`](../../DATASETS.md).

## Context

The whole tool rests on one property. When it says a candidate fits the board, that has to
be because the candidate looks like the board, not because it contains the same objects.

A general purpose image embedding does not have that property. Image and text contrastive
models are trained so that an image embedding predicts a caption, and captions are mostly
about subject matter. Two photographs of a bicycle in completely different treatments land
near each other, and two photographs sharing a treatment but showing different subjects land
apart. A scorer built on that representation will rank a candidate highly for showing the
right product, which is the failure the tool exists to prevent, and it will do so while
producing entirely reasonable looking numbers.

So the representation is not an implementation detail to be chosen during implementation. It
is the decision, and it is only decidable by measurement.

## Decision

**Style axis.** Use CSD, the contrastive style descriptor from "Measuring Style Similarity in
Diffusion Models" (Somepalli, Gupta, Gupta, Palta, Goldblum, Geiping, Shrivastava, Goldstein,
2024, arXiv:2404.01292). Reference implementation at `github.com/learn2phoenix/CSD` under the
MIT licence, ViT-L weights published at `tomg-group-umd/CSD-ViT-L`. It is trained specifically
to place images by style rather than by content, which is exactly the property being bought,
and it ships weights and an evaluation protocol, so the claim is checkable rather than
inherited.

**Baselines, kept in the repository and runnable.** CLIP ViT-L/14 via `open_clip`, and
DINOv2 ViT-L/14. They are not fallbacks. They are the contrast that gives the acceptance
measurement its meaning: a number for CSD alone says nothing without a number for the
representation everyone would otherwise have reached for.

**Classical axes, computed alongside and reported separately.** Palette, as dominant colours
in CIELAB compared by earth mover's distance. Tone, as a distance between luminance and local
contrast distributions. Composition, coarsely, as saliency placement and negative space
ratio. These are cheap, they are interpretable to a designer without any explanation, and
they cover the part of "look" that a person can name. They are never folded into the style
axis silently. ADR-0002 requires them on the page.

**Combination is a stated function, not a learned one, in the first version.** There is no
labelled data for what weighting matches human judgment, so learning one would be fitting to
nothing. The combination is documented, its weights are configurable, and the axes remain
visible so a reader can disagree with the weighting and still use the report.

**Distance is to the reference distribution, not to a centroid.** Brands carry sub-looks, so
a reference set is frequently multi-modal and its mean can sit in a gap where no reference
lives. With n between 10 and 50 against several hundred dimensions the sample covariance is
singular, so it is regularised by shrinkage, with the estimator and its parameter recorded in
the report. Scores are calibrated by leave-one-out against the board itself, which is what
makes them comparable across boards.

## What the number means, and the board size that bounds it

This section was added after the decision above, and it closes a hole in it. The decision
said scores are "calibrated by leave-one-out against the board itself" and never said what
the resulting number *is*. A report that prints an uninterpreted number is the failure
ADR-0002 exists to prevent, and it was about to print one.

The score is a **conformal p-value**. Take the mean distance to the k nearest references as
the nonconformity measure, compute it for the candidate and for every reference under
leave-one-out, and report the fraction of references at least as unusual as the candidate.
Under exchangeability of the references with the candidate, that is a finite-sample inlier
test with a guarantee that does not depend on the embedding being well behaved.

**It is not a probability that an asset is on-brand.** It answers "would this look out of
place among these references", which is a narrower question than "is this right for the
brand", and the two come apart whenever the board is an incomplete statement of the brand,
which is nearly always. The report renders it as an inlier test and the vocabulary in
`eval/thresholds.json` under `score_semantics` forbids the percentage-of-fit rendering.

**The board size bounds the resolution, and at the bottom of the supported range it bounds
it hard.** With n references the achievable p-values are the multiples of 1/(n+1):

| board size | finest expressible p | a 0.05 rejection rule |
|---|---|---|
| 10 | 1/11 = 0.0909 | impossible, zero power |
| 20 | 1/21 = 0.0476 | just expressible |
| 50 | 1/51 = 0.0196 | fine |

So a ten-image board cannot reject anything at a nominal 5%, no matter how far off the
candidate is. This is a property of the sample size and not of the method, and no choice of
encoder changes it. The report computes 1/(n+1) for the board in front of it and refuses a
threshold below that rather than rounding up to something it cannot support.

This does not disturb the interval coverage pre-registered under ADR-0002. That asks for a
0.90 level, and 1 - 1/11 = 0.909 at the smallest board, so the level is reachable at every
supported size. Checked rather than assumed, because a correction that quietly invalidates
an already-registered number is worse than the gap it closes.

## Acceptance criteria

This record stays `Proposed` until all **five** measurements exist in the repository, each
reproducible by the command named in its dataset row. The first three test the
representation. The last two, added later, test the report's labels and the weights the
whole thing runs on, and are cheap: neither needs human labels and neither needs a dataset
this repository cannot obtain.

**1. Content invariance, coarse.** On a set with a full crossing of style and subject, embed
every image and compare two families of pairs: same style with different subject, and
different style with same subject. Report the area under the ROC curve for ranking the first
family above the second, for CSD and for each baseline.

Acceptance is pre-registered in [`eval/thresholds.json`](../../eval/thresholds.json) and is
fixed before the measurement runs: CSD reaches an AUC of at least 0.80, and beats CLIP by at
least 0.10 AUC. Both conditions, because either alone can be met by something useless. A wide
margin over a baseline that is itself near chance would mean neither is usable, and a high
absolute score with no margin would mean the property came from the data rather than from the
representation. Both datasets yield tens of thousands of eligible pairs, so the standard error
on an AUC is well under 0.01 and a margin of 0.10 clears it by an order of magnitude. The
reasoning is in `eval/README.md`.

If CLIP does just as well, this record is wrong and should be rejected rather than adjusted.
That outcome is worth stating in advance, because the cheap and comfortable move at that
point is to keep the conclusion and soften the test.

**2. Content invariance, brand photography.** The first measurement establishes the property
on illustration and painting, which is the domain the published evaluations use. The intended
use is commercial photography, where styles differ by lighting, grade, grain and framing
rather than by medium, and those differences are far smaller. A representation can pass the
coarse test and be useless here. So the same test runs on human-curated photographic
collections, with the collection standing for style and the photograph's own subject
standing for content.

Passing the coarse test and failing this one is a real possible outcome and it would change
the product rather than end it, since the honest response is a narrower claim about what kind
of look is measurable. That response is written down in advance rather than improvised at the
time: `content_invariance.on_partial_pass` in `eval/thresholds.json` says the record is not
accepted as written, the claim narrows to the domain that passed, and the narrowing goes in
the README where a reader sees it.

**3. Off-style rejection.** Build a board from one group, score assets from that group and
assets drawn from a deliberately different group, and require that every on-look asset ranks
above every off-look one. This is the weakest of the three tests and it is included because
it is the one a reader will try first, by hand, with two obviously different folders.

**4. The axes measure what their labels claim.** The classical axes are reported separately
under the names palette, tone and composition, and until now nothing checked that those
names are true. An axis that responds to every change is not a decomposition. It is one
number printed three times under different headings, and it would make the report look
richer while telling a designer less than a single number honestly labelled.

Take an image, apply one intervention at a time, and record how much each axis moves.
Recolouring must move palette most. A luminance shift must move tone most. Cropping must
move composition most. Added grain must move texture most. Acceptance, pre-registered in
`eval/thresholds.json` under `axis_intervention`: the intended axis moves at least twice as
much as the largest unintended one. An axis that fails loses its name in the report and
appears as an unlabelled component, or comes out.

This runs entirely on images the repository already has, needs no human judgment, and would
have caught a mislabelled decomposition that every other test in this record passes.

**5. The pinned weights are the paper's weights.** The CSD repository's own documentation
warns that its published checkpoint does not reproduce the numbers in the paper. ADR-0002
requires pinning an exact revision, which guarantees the same answer on every run. It does
not guarantee the right one, and a pinned wrong answer is more durable than an unpinned one
because it is reproducible.

So reproduce the published benchmark before quoting it: WikiArt artist retrieval, mAP@1,
against the reported 64.56 from arXiv:2404.01292. Acceptance is a shortfall of no more than
2 absolute points. On failure the weights are not the paper's, and either weights that do
reproduce it are found or that published number is struck from every claim here. Citing a
paper's benchmark while running weights that miss it is the quiet version of making the
number up.

## Alternatives considered

**Bare CLIP image embeddings.** Rejected on the grounds above, and retained as the baseline
that the acceptance measurement has to beat. Cheapest and most available, and entangled with
content in exactly the way that matters here.

**DINOv2.** Self-supervised and not caption-driven, so its content entanglement should differ
from CLIP's rather than being a copy of it. Kept as the second baseline for that reason.
Promoting it to the style axis is a live option if the measurement favours it.

**Training a style encoder on brand assets.** Rejected. There is no labelled corpus of what
counts as on-brand across brands, collecting one is a larger project than this tool, and a
system that needs training data per brand cannot be handed to someone with a folder of
twenty images, which is the whole use case.

**Classical axes only, with no learned representation.** Rejected as the style axis and kept
as the decomposition. Palette and tone are genuinely most of what a person says out loud when
describing a look, and they are blind to texture, grain, lens character and lighting quality,
which is most of what makes two photographs feel like they belong together.

**Gram matrix statistics from a convolutional network, the classical neural style transfer
representation.** Rejected as the primary axis. It is a texture statistic, it is sensitive to
resolution and crop, and the newer style descriptors exist because it was not sufficient.
Worth keeping in mind as a cheap additional texture axis.

## Consequences

The engine depends on published model weights, so the report has to pin the exact revision.
ADR-0002 already requires this.

The three acceptance measurements are the project's validation section, and they are the part
that makes the tool believable, so they belong in continuous integration and in the README
once they exist.

The datasets carry licences that do not permit redistributing images, so the repository ships
manifests, checksums and fetch scripts, and never the image files. See `DATASETS.md`.
