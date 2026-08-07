# Datasets

Every architecture decision record that makes a measurable claim names its dataset here, and
stays `Proposed` until that dataset row reaches `ready` and the measurement is committed. The
rule and the reasoning are in [`docs/adr/README.md`](docs/adr/README.md).

## What "ready" means

A row is `ready` when all of the following exist in the repository:

- a fetch script that downloads the source and verifies it,
- `datasets/<name>/manifest.jsonl`, one line per item, carrying the item id, the group labels
  the protocol needs, and a content hash,
- `datasets/<name>/checksums.sha256`,
- `datasets/<name>/LICENCE.md` recording the terms under which the source was obtained and
  what may be redistributed.

**Image files are never committed.** Every source below either forbids redistribution or
leaves it unclear, so the repository carries the manifest, the checksums and the recipe. A
fresh clone reconstructs the exact set by running the fetch script, and the checksums are what
make "the exact set" a checkable statement rather than a hope.

## Rows

| claim | dataset | source | size | licence | redistributable | status |
|---|---|---|---|---|---|---|
| `content-invariance-coarse` | PACS | Li et al. 2017, "Deeper, Broader and Artier Domain Generalization" | 9,991 images, 4 domains, 7 classes | research use, per the original release | no, manifest only | `pending` |
| `content-invariance-brand` | Unsplash Lite, collections | `unsplash.com/data`, `github.com/unsplash/datasets` | 25,000 photos, plus `collections.tsv` grouping them | commercial and non-commercial use permitted, images explicitly not redistributable | no, manifest and URLs only | `pending` |
| `off-style-rejection` | derived from the two above | no new source | held-out cells | inherits | n/a | `pending` |
| `interval-coverage` | derived from the two above | no new source | resampled boards of n in {10, 20, 50} | inherits | n/a | `pending` |
| `human-style-grouping` | Unsplash collections, and WikiArt artist labels if the licence resolves | as above | as above | WikiArt terms unresolved at the time of writing, see below | no | `pending` |

Every row is `pending` because no fetch script has been written yet. That is the honest state
of the project on the day these records were opened, and it is the reason all three decision
records are `Proposed` rather than `Accepted`.

## Protocols

### `content-invariance-coarse`, on PACS

PACS crosses four domains, photo, art painting, cartoon and sketch, with seven object classes,
dog, elephant, giraffe, guitar, house, horse and person. That crossing is the reason to use
it: domain stands for style, class stands for content, and every combination is present, so
the two families of pairs the test needs can both be constructed without any hand labelling.

Take every pair of images. Call a pair *same-style* when the two share a domain and differ in
class, and *cross-style* when they share a class and differ in domain. Compute the
representation's similarity for both families and report the area under the ROC curve for
ranking same-style pairs above cross-style pairs. Repeat for CSD, CLIP ViT-L/14 and DINOv2
ViT-L/14 without changing anything else.

```
uv run moodboard-eval invariance --dataset pacs --models csd,clip,dinov2 \
    --out results/invariance-pacs.json
```

The result is a table of AUC by representation, committed under `results/`, with the exact
model revisions recorded in the output.

### `content-invariance-brand`, on Unsplash collections

The coarse test uses domains that differ by medium, and a photograph is never going to be
confused with a sketch. Commercial photography is the intended use and its styles differ by
lighting, grade, grain and framing, which is a far smaller signal. A representation can pass
the coarse test and be useless on this one, so this row exists to say which of the two the
tool's claim rests on.

`collections.tsv` in the Unsplash Lite dataset has one row per photo and collection pair,
carrying `photo_id`, `collection_id` and `collection_title`. A collection is curated by a
person and its members are chosen to sit together, so collection membership is a human
grouping of a coherent look, which is the ground truth this test needs and is otherwise
expensive to obtain.

Same construction as the coarse test, with collection standing for style and the photo's own
subject keywords, from `keywords.tsv`, standing for content. Collections are filtered to
those with enough members for a board, and to those whose members are not dominated by a
single subject, since a collection that is entirely one subject cannot separate the two
factors and would score well for the wrong reason. The filter and its thresholds are part of
the prepared manifest, not a runtime flag, so the population is fixed and inspectable.

```
uv run moodboard-eval invariance --dataset unsplash-collections --models csd,clip,dinov2 \
    --out results/invariance-unsplash.json
```

### `off-style-rejection`

Build a board from one group, meaning one PACS domain or one Unsplash collection. Score
held-out members of that group and members of a deliberately different group. Require that
every on-look asset ranks above every off-look one, and report any inversion with both images
named so it can be looked at.

### `interval-coverage`

Sample boards of size n in {10, 20, 50} from a group. For each board, compute the interval
around a held-out asset's score. Independently resample a second board from the same group
and recompute the score. Record how often the first board's interval contains the second
board's score, and report that empirical coverage against the stated level, by board size.

If the observed coverage is below the stated level, the stated level is corrected to the
observed one. The claim moves, not the measurement.

## Licence notes

**PACS** is distributed for research use. The repository carries the manifest and the fetch
script and no images. The usual acquisition route is the download script in the DomainBed
benchmark suite, `facebookresearch/DomainBed`, which places the set under a `kfold`
directory. Availability of that link is an open question at the time of writing, since the
project's own issue tracker carries a report of it failing, so the fetch script must verify
what it downloaded against the committed checksums and fail loudly rather than proceeding with
a partial set. If the route is dead, the alternative is the original authors' release, and
whichever route is used gets recorded in `datasets/pacs/LICENCE.md` with the date it worked.

**Unsplash Lite** is documented as free for commercial and non-commercial use, and the
repository is explicit that it "cannot be used to redistribute the images contained within".
So the manifest carries `photo_id` and the image URL and the fetch script downloads at
prepare time. The dataset itself is obtained from `unsplash.com/data/lite/latest`.

**WikiArt** is the evaluation set used by the published style descriptor work and its artist
labels are an appealing second source of human grouping. Its redistribution terms were not
resolved at the time of writing, so it is listed as `pending` on the licence question rather
than being assumed usable, and it is not a dependency of any acceptance criterion. Resolving
it is a task in its own right, and the answer belongs in this file with the source that
settles it.
