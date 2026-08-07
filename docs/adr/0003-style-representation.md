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
compared by earth mover's distance, clustered in the **chroma plane (a, b) only**. Tone, as a
distance between luminance and local contrast distributions. Composition, coarsely, as saliency
placement and negative space ratio. These are cheap, they are interpretable to a designer
without any explanation, and they cover the part of "look" that a person can name. They are
never folded into the style axis silently. ADR-0002 requires them on the page.

**Palette excludes lightness deliberately, and that is a statement about meaning rather than a
tuning choice.** Palette is the axis of hue and chroma identity: what colours these are. Tone is
the axis of lightness: how light or dark they sit. So the L channel belongs to tone and is kept
out of the palette clustering, and the consequence a designer must be told is that **two images
with identical hues at very different lightness have a palette distance of zero.** That is
intended behaviour, not a limitation. The pair is not invisible to the tool: it moves to the
axis named for lightness, where tone registers it maximally. Relocating a signal to the axis
whose name claims it is what a decomposition is for.

An earlier version of this record clustered palette over the full CIELAB vector with L included,
and that was the defect rather than a defensible alternative. It made the axes correlated by
construction: a luminance shift moved the palette centroids even with chroma held exactly fixed,
so palette absorbed response belonging to tone, and criterion 4 below became unsatisfiable by any
conforming implementation. Measured across twelve independent subject sets, the tone margin ran
0.70 to 1.90 against a required 2.0 and was never cleared. The amendment is recorded here rather
than made in the clustering call because it changes what the report means to its reader.

**There is no combined score in the first version.** There is no labelled data for what
weighting matches human judgment, so learning one would be fitting to nothing, and a
hand-picked weighting would be a number wearing the statistical machinery's authority without
its guarantee. The report's `score` field is the style axis's conformal p-value alone, defined
below. The classical axes are reported beside it and are never folded in. A blended
convenience index, if a viewer ever wants one, is a labelled viewer concern and never occupies
`score`. An earlier draft of this record promised a documented combining function; that
promise is withdrawn here rather than left dangling, because two implementers reading it
would have built two different scores.

**Distance is local, to the nearest references, not to a centroid or a fitted Gaussian.**
Brands carry sub-looks, so a reference set is frequently multi-modal and its mean can sit in
a gap where no reference lives. The nonconformity measure is therefore pinned as: embeddings
are L2-normalised, distance is cosine distance (one minus cosine similarity), and the
nonconformity of an observation is its mean distance to its k nearest neighbours among the
other observations in the bag, with k = min(5, n − 1). A local measure needs no covariance
estimate and degrades gracefully on multi-modal boards, because each point is judged against
its own neighbourhood. The shrunk-covariance Mahalanobis machinery an earlier draft called
for is demoted to board diagnostics (the tightness and leverage statistics in ADR-0002),
where a misestimated geometry misleads a summary rather than the score; if it is ever
promoted back into the score path, that is a new record with its own validation, since at
n ≤ 50 against 768 dimensions almost all of its geometry would come from the shrinkage
target rather than from the data. Scores are calibrated against the board itself as defined
below, which is what makes them comparable across boards.

## What the number means, and the board size that bounds it

This section was added after the decision above, and it closes a hole in it. The decision
said scores are "calibrated by leave-one-out against the board itself" and never said what
the resulting number *is*. A report that prints an uninterpreted number is the failure
ADR-0002 exists to prevent, and it was about to print one.

The score is a **conformal p-value**, and the construction is the symmetric full-conformal
one, stated exactly because an asymmetric paraphrase of it carries no guarantee. Form the
augmented bag of n + 1 observations: the n references plus the candidate. For every
observation in that bag, including the candidate, compute the same nonconformity measure
against the other n observations of the bag: mean cosine distance to its k nearest
neighbours among them, k = min(5, n − 1). Call these α₁ … αₙ for the references and
α_cand for the candidate. The score is

    p = (1 + #{i : αᵢ ≥ α_cand}) / (n + 1)

with ties counted in the numerator, which is the conservative direction. Every observation
in the bag is treated by the same rule, so under exchangeability of the references with the
candidate this is a finite-sample inlier test with a guarantee that does not depend on the
embedding being well behaved. Two things an implementer must not substitute: the fraction is
over n + 1, not over n, and each reference's αᵢ is computed with the candidate present in
its neighbour pool — scoring references only against each other breaks the permutation
symmetry the guarantee rests on. Exchangeability itself is an assumption about how the board
and candidate were assembled, not a property the tool can verify; the guarantee is stated
conditional on it, and ADR-0005 records the ways real curation strains it.

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

This record stays `Proposed` until **four gating** measurements exist in the repository, each
reproducible by the exact command in its dataset row. A fifth, off-style rejection, is
informational and does not gate — see criterion 3 for why the demotion, not the inclusion,
is the honest call.

**Two of the four cannot run today and this record therefore cannot be accepted today.**
Content invariance on brand photography has no dataset, and weight reproduction has no
licensed WikiArt route. Neither is a failing measurement; both are absent ones, and the
distinction matters because `content_invariance.on_partial_pass` governs an observed brand
*failure* and says nothing about an unavailable measurement. `DATASETS.md` now carries
`weight-reproduction` as an explicit blocked row rather than a sentence, and both rows must
reach a runnable state before this record can be accepted rather than partially accepted.
The sentence "neither needs a dataset this repository cannot obtain" was in this preamble and
was false about the WikiArt row when written.

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
coarse test and be useless here. So the same test runs on photographs carrying two independent
groupings, with the photographer — creator identity — standing for style and the
photograph's own subject standing for content. An earlier version of this paragraph said
"human-curated collections, with the collection standing for style"; that assumption was
measured against a real release and refuted (the largest collections are subject buckets),
and the adopted amendment in `DATASETS.md` under `content-invariance-brand` governs. Creator
identity assumes no curation at all: same person, same equipment, same grade, varying
subject.

Passing the coarse test and failing this one is a real possible outcome and it would change
the product rather than end it, since the honest response is a narrower claim about what kind
of look is measurable. That response is written down in advance rather than improvised at the
time: `content_invariance.on_partial_pass` in `eval/thresholds.json` says the record is not
accepted as written, the claim narrows to the domain that passed, and the narrowing goes in
the README where a reader sees it.

**3. Off-style rejection — informational, and not a gate.** Build a board from one group,
score assets from that group and assets drawn from a deliberately different group, and
require that every on-look asset ranks above every off-look one, over 100 resampled board
pairs rather than one board per group so that board-selection variance is measured. This is
the weakest of the tests and it is kept because it is the one a reader will try first, by
hand, with two obviously different folders.

It does not gate acceptance, and the reason is the caveat it always carried: on the only
runnable source its groups differ by *medium*, so a green result says a photograph is not a
sketch and says nothing about discriminating treatments within commercial photography. A
measurement that cannot distinguish the property it is named for should not be able to
certify it. It becomes a gate on a source whose groups differ by treatment within a medium,
which is the same source the brand row needs.

**4. The axes measure what their labels claim.** The classical axes are reported separately
under the names palette, tone and composition, and until now nothing checked that those
names are true. An axis that responds to every change is not a decomposition. It is one
number printed three times under different headings, and it would make the report look
richer while telling a designer less than a single number honestly labelled.

Take an image, apply one intervention at a time, and record how much each axis moves.
Recolouring must move palette most. A luminance shift must move tone most. Cropping must
move composition most. Acceptance, pre-registered in `eval/thresholds.json` under
`axis_intervention`: per-axis movements are normalised before comparison (each axis's
movement is divided by that axis's median absolute movement across all interventions, so the
ratio compares like with like rather than raw units), and the intended axis moves at least
twice as much as the largest unintended one. An axis that fails loses its name in the report
and appears as an unlabelled component, or comes out.

**The palette/tone pair is tested in BOTH directions, and one direction alone does not
establish the claim.** Separability between two axes is a symmetric assertion, so it takes two
measurements:

- *Lightness isolation.* Shift luminance with chroma held exactly fixed. **Tone** clears the 2.0
  margin over palette.
- *Chroma isolation, the mirrored arm.* Shift hue and chroma with lightness held exactly fixed.
  **Palette** clears the 2.0 margin over tone.

Both are required. This is recorded because the natural repair, when one direction fails, is to
fix that direction and re-run it alone, which measures one arm of a two-arm claim while reading
as though the claim were restored. The mirrored arm is also the one that would catch the
opposite error: a palette axis stripped so far toward chroma that it stops responding to a
genuine recolour would pass the lightness arm perfectly and fail here. There is no grain row: texture is not
one of the v1 axes, so a grain intervention has no registered axis to move, and a test case
whose expected winner is absent from the vocabulary can only fail by schema rather than
inform. If a texture axis is ever added — the Gram-matrix statistic in the alternatives is
the standing candidate — it arrives with its own intervention row before it gets a name.

This runs entirely on images the repository already has, needs no human judgment, and would
have caught a mislabelled decomposition that every other test in this record passes.

**5. The pinned weights are the paper's weights.** The CSD repository's own documentation
warns that its published checkpoint does not reproduce the numbers in the paper. ADR-0002
requires pinning an exact revision, which guarantees the same answer on every run. It does
not guarantee the right one, and a pinned wrong answer is more durable than an unpinned one
because it is reproducible.

So reproduce the published benchmark before quoting it: WikiArt artist retrieval, mAP@1,
against the reported 64.56 from arXiv:2404.01292. Acceptance is a deviation of no more than
2 absolute points **in either direction**. A one-sided shortfall gate accepts an arbitrarily
higher result, and a score meaningfully above the published number is evidence of a protocol
or checkpoint mismatch exactly as a lower one is. On failure the weights are not the paper's,
and either weights that do reproduce it are found or that published number is struck from
every claim here. Citing a paper's benchmark while running weights that miss it is the quiet
version of making the number up.

**Landing within tolerance does not prove the checkpoint is the paper's, and this criterion
used to imply that it did.** Several checkpoints and protocols can reproduce one number to
two points, so agreement is necessary and not sufficient. The criterion compares the
checkpoint's own sha256 against an authoritative published hash where one exists. Where none
exists, the claim is renamed rather than stretched: the repository says "benchmark reproduced
under this pinned revision" and never "the paper's weights".

This criterion has no dataset today. `DATASETS.md` carries `weight-reproduction` as a blocked
row needing a licensed WikiArt route with an exact split, preprocessing and command.

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

The four gating acceptance measurements, plus the informational fifth, are the project's
validation section, and they are the part that makes the tool believable, so they belong in
continuous integration and in the README once they exist. This sentence said "three" after
the record had grown to five criteria, which is the same stale-count defect the review that
produced this text was hunting elsewhere; a count in prose is a state claim and decays like
any other.

The datasets carry licences that do not permit redistributing images, so the repository ships
manifests, checksums and fetch scripts, and never the image files. See `DATASETS.md`.
