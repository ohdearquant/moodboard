# Datasets

Every architecture decision record that makes a measurable claim names its dataset here, and
stays `Proposed` until that dataset row reaches `ready` and the measurement is committed. The
rule and the reasoning are in [`docs/adr/README.md`](docs/adr/README.md).

Acceptance thresholds for every measurement below are pre-registered in
[`eval/thresholds.json`](eval/thresholds.json), fixed before the corresponding measurement is
run for the first time, with the reasoning for each number in `eval/README.md`.

## What "ready" means

A row is `ready` when all of the following exist in the repository:

- a fetch script that downloads the source, verifies it against a pinned checksum, and
  **asserts the properties the protocol depends on** rather than printing them,
- `datasets/<name>/checksums.sha256`, the checksum the rebuilt manifest must produce,
- `datasets/<name>/LICENCE.md` recording the terms under which the source was obtained, quoting
  the operative clauses, and saying what may and may not be republished.

**The manifest itself is a local artifact and is not committed.** It is one line per item,
carrying the item id, the group labels the protocol needs, and a content hash, and it is
rebuilt by running the fetch script. Committing the checksum instead of the file is not a
weaker claim, it is a stronger one: the file only shows what was built once, whereas the
checksum is a statement that anyone rebuilding gets exactly the same thing, and it fails
loudly when they do not. It also keeps the repository from republishing per-item data derived
from a source whose terms are restrictive or unresolved, which is the situation for both
sources here.

**Image files are never committed.** Every source below either forbids redistribution or
leaves it unclear, so the repository carries the manifest, the checksums and the recipe. A
fresh clone reconstructs the exact set by running the fetch script, and the checksums are what
make "the exact set" a checkable statement rather than a hope.

**That rule is not always sufficient, and assuming it was is how this project nearly published
licensed data.** It protects against a restriction on the *images*. A source may instead
restrict the *data*, in which case the manifest is a portion of the restricted thing and
cannot be committed either. Whether the manifest may be published is checked at the terms
shipped with the data, never inferred from the source's public description of its licence.
Where publication is not permitted, the fetch script and the manifest's checksum are
committed and the manifest itself is git-ignored.

**Readiness is two questions, and collapsing them into one status was wrong.** This section
previously said a row is `ready` only when its licence permits publishing the manifest, which
made publication permission a precondition for *running* a measurement. Those are different
things and they fail for different reasons:

- **`runnable`** — can a reader with a fresh clone rebuild the exact set and reproduce the
  measurement? That needs the fetch script, the pinned source checksum, the committed
  manifest checksum, and the protocol assertions. It does not need permission to republish
  anything, because the reader fetches from the source themselves.
- **`manifest-publishable`** — may this repository commit the per-item manifest? That is
  purely a licence question.

A row is `ready` when it is `runnable`. A row that is runnable but not manifest-publishable
is `ready, manifest withheld` and its manifest is git-ignored — the measurement stands, the
data does not travel. A row that is not runnable is `blocked`, whatever its licence says.

The distinction is load-bearing in both directions. Reading it the old way, PACS is
`blocked` on an unresolved licence and every acceptance criterion in ADRs 0002 through 0005
loses its evidence, which is a stronger conclusion than the facts support: the licence is
unresolved for *redistribution*, and nothing about it stops a reader fetching the mirror and
rebuilding. Reading it too loosely in the other direction is how the Unsplash manifest came
within one command of being published. Unsplash is `blocked` under the new reading too, and
for the reason that actually applies: its own fetcher is exploratory rather than ready, quite
apart from the terms.

**An unresolved licence is not permission, and `ready, manifest withheld` does not launder
one into the other.** PACS carries an unresolved licence and this repository publishes no
portion of it.

## Rows

| claim | dataset | source | size | licence | manifest published | status |
|---|---|---|---|---|---|---|
| `content-invariance-coarse` | PACS | mirror `flwrlabs/pacs`, rev `394113073`; original release is gone, see below | 9,991 images, 4 style groups, 7 content groups | **unresolved**; mirror declares `unknown` | **no** | **`ready, manifest withheld`** |
| `content-invariance-brand` | Unsplash Lite | `unsplash.com/data/lite/latest` | 25,000 photos | grant is **internal use only**; publishing any portion is barred | **no** | **`blocked`** — exploratory fetcher, and the terms bar publication |
| `off-style-rejection` | derived | no new source | held-out cells of the above | inherits | no | `ready, manifest withheld` on PACS (**informational only**, see protocol); `blocked` on brand |
| `interval-coverage` | derived | no new source | resampled boards of n in {10, 20, 50} | inherits | no | `ready, manifest withheld` on PACS |
| `human-style-grouping` | none yet | — | — | — | — | `blocked`, needs a source |
| `abstention-triggers` | derived | no new source | resampled boards below threshold resolution, two-group boards at pinned sub-look sizes, cross-domain assets | inherits | no | `ready, manifest withheld` on PACS |
| `effective-board-size` | derived | no new source | paired boards of equal file count, one padded with generated near-duplicates | inherits | no | `ready, manifest withheld` on PACS |
| `weight-reproduction` | WikiArt artist retrieval | unresolved, see licence notes | — | unresolved | — | `blocked`, needs a source |

Every row's exact reproduce command is in its protocol section below, and a row without one
is not `ready` regardless of its licence — `docs/adr/README.md` requires the command, and
six measurements previously had none. Rebuild the source set first with
`uv run --with pyarrow datasets/pacs/fetch.py`, which verifies an exact source checksum,
asserts the protocol's own properties, and compares the rebuilt manifest against the
committed checksum rather than overwriting it.

**One source is runnable, the brand source is blocked, and that is the finding.** The
brand-photography half of the central claim has no usable source today, so ADR-0003 cannot
reach its own five-measurement gate and stays `Proposed`. What follows records how each row
got to its status, because in both cases the first plan was wrong in a way that would have
survived into implementation.

## What preparing these actually found

### PACS: three dead acquisition routes, and a licence weaker than assumed

All three routes normally cited for PACS were checked on 2026-08-07, each with a reachable
control in the same call so that this records dead routes and not an unreachable network. The
DomainBed helper's Google Drive object returns HTTP 404 as a 1,652 byte HTML error page. The
paper's project page returns 404. The authors' lab download site does not resolve.

A 404 page is a perfectly valid file: it downloads without error, it has a plausible size, and
only its content says it is wrong. This is why every fetch script here verifies a checksum and
a magic-byte prefix, and why the failure is fatal rather than a warning.

The route used instead is a Hugging Face mirror pinned to an exact revision and file checksum,
obtained without downloading first by reading the hub's file metadata. **The cost is licence
provenance:** terms travel with the original distribution, and when that distribution
disappears what remains is a copy whose uploader declares the licence `unknown`. The row now
says unresolved, where it previously said "research use, per the original release" — a claim
this project could not verify and had inherited from convention. Details in
`datasets/pacs/LICENCE.md`.

The prepared set was checked against the properties the protocol actually needs, not just
against its checksum:

- 9,991 items, matching the published count exactly.
- The domain split matches the published one exactly: art_painting 2,048, cartoon 2,344,
  photo 1,670, sketch 3,929. The fetch script asserts this rather than printing it.
- All 28 style × content cells are populated, so both pair families can be built everywhere.
- 11,592,384 same-style pairs and 5,019,117 cross-style pairs, so the standard error on an AUC
  is far below the 0.10 margin `eval/thresholds.json` requires.
- Zero exact-duplicate images, which matters because PACS is assembled from overlapping public
  sources and duplicates across domains would seed the cross-style family with trivial pairs.

**One caveat that belongs in the measurement, not in a footnote.** The cells are badly
unbalanced: sketch/horse holds 816 items and sketch/house holds 80. An AUC over all pairs is
therefore weighted by cell size and is mostly a statement about the large cells. The
measurement should report both the all-pairs figure and a cell-balanced one, and if they
disagree, the balanced figure is the honest one.

### Unsplash: the premise was wrong, and then the licence made it moot

Two independent findings. The second blocks the row; the first stands on its own and applies
to whatever source replaces it.

**The licence blocks it.** The Dataset Terms shipped inside the archive grant only *internal*
business use (2.A) and bar publishing "any portion of the Licensed Data in any manner" (3.A).
This project's standing rule — never commit images, commit a manifest instead — does not help
here, because the restriction is on the data rather than on the images, and a manifest of photo
ids, URLs, keywords and photographer names is a portion of the data. The manifest is built and
git-ignored, never published. Clause quotes and the routes forward are in
`datasets/unsplash-collections/LICENCE.md`.

**The premise was already refuted before the licence was read.** This file used to assert that
"a collection is curated by a person and its members are chosen to sit together, so collection
membership is a human grouping of a coherent look." Measured against the release:

- 558,146 distinct collections touch the 25,000-photo sample, with a median of 2 members each.
- The largest are subject buckets, not looks: 'Nature' (2,650), 'Wallpapers' (1,996),
  'Animals' (1,393), 'Halloween!' (2,770).

The filter this file proposed — exclude collections "dominated by a single subject" — was
implemented and **failed both control arms**, in opposite directions:

- Its obvious form, highest within-group keyword share, rejected *everything*: 0 collections
  passed at a 30% threshold. The cause is that Unsplash tags are dense, 36 per photo on
  average, and the corpus is nature-heavy, so 'nature' sits on 70.9% of all photos and
  'outdoors' on 66.2%. The filter was measuring corpus ubiquity, not subject concentration.
  **Zero passing read as a fact about the data when it was a defect in the instrument, and it
  read in the direction that would have killed this row.**
- Re-expressed as lift over the corpus rate, it then failed open. 'Halloween!' passed at 1.3x,
  because a 2,770-member hoard dilutes every keyword below the share gate; and the top scorers
  were collections named 'Misc', 'Ideas' and 'Objects', which scored 0.00 because no keyword
  reached the gate at all. Absence of a measurable subject was being read as subject diversity.

**Photographer identity is the better grouping** and does not depend on a curation assumption:
same person, same equipment, same grade, varying subject. Of 8,558 photographers, 222 have at
least 15 photos in the sample and 87 of those pass a subject-lift filter, covering 3,925
photos across groups of 15 to 442. That is enough to build boards and hold out members.

A calibration note worth carrying to the replacement source, because it nearly shipped wrong:
an early version screened candidate keywords by a 5% ubiquity cutoff *before* computing lift.
That was redundant — a keyword on 70.9% of the corpus cannot reach 3x without appearing on
213% of a group, so ubiquitous tags are structurally incapable of flagging anything — and it
was harmful, admitting 73 extra groups including a photographer whose work is 78% 'wallpaper'
against a 6.7% corpus rate, an 11.6x concentration waved through as diverse. Lift needs no
help. The cutoff is right only for choosing which keywords to *record* as a subject.

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
    --balance cells --bootstrap group --seed 20260807 \
    --out results/invariance-pacs.json
```

The result is a table of AUC by representation, committed under `results/`, with the exact
model revisions recorded in the output.

**The gate is the cell-balanced AUC, not the all-pairs one.** Both are reported and the
all-pairs figure is secondary. The reason is above: cells run from 80 to 816 items, so an
all-pairs AUC is weighted by cell size and is mostly a statement about the large cells, and
`eval/thresholds.json` registers the balanced figure as the one acceptance reads. Uncertainty
is a group-level bootstrap over images, not a pair-level one — the millions of pairs are
built from thousands of images and are heavily dependent, so treating pairs as independent
understates the standard error by roughly the square root of the pairs-per-image factor.

**What a green result here can and cannot certify.** PACS domains differ by *medium*, and
separating a photograph from a sketch is not the property the tool is sold on. A pass here is
evidence for coarse cross-medium style invariance and nothing more; the same-medium claim
rests entirely on the brand row, which has no source. This is stated in the ADR as well, so
that a reader meeting the number first does not have to come here to learn what it means.

### `content-invariance-brand` — BLOCKED, no source

The coarse test uses domains that differ by medium, and a photograph is never going to be
confused with a sketch. Commercial photography is the intended use and its styles differ by
lighting, grade, grain and framing, which is a far smaller signal. A representation can pass
the coarse test and be useless on this one, so this row exists to say which of the two the
tool's claim rests on. **It is the more important of the two measurements and it currently has
no dataset.** The section above records why Unsplash cannot serve it.

**Amendment, adopted: the style grouping is the PHOTOGRAPHER, not a curated collection.** The
original protocol grouped by collection on the assumption that a curated collection is a
coherent look. That assumption was measured and does not hold — the largest collections are
subject buckets, and a filter meant to exclude subject-dominated ones failed in both
directions, rejecting everything when read as within-group share and admitting hoards when
read as lift. The measurements are in the section above. Photographer identity assumes no
curation at all: same person, same equipment, same grade, varying subject. On the source that
prompted the change it yielded 87 usable groups from 8,558 photographers, covering 3,925
photos in groups of 15 to 442, which is a real population rather than a residue.

The protocol survives the source going away, so it is stated here for whatever replaces it. It
needs a corpus of photographs carrying two independent groupings: a **style** grouping, now
the photographer or equivalent creator identity, and a **content** label per photo. Build the
same two pair families as the coarse test — same style with different content, different style
with same content — and report the AUC for ranking the first family above the second, for CSD
and each baseline.

Requirements a candidate source has to meet, each of which came from something that went wrong
above:

1. **A publishable manifest.** Per-item metadata that may be committed, which means the source
   licence has to permit it, not merely permit using the images.
2. **A style grouping that does not rest on an untested assumption.** Whatever the grouping is,
   measure whether it is confounded with subject before relying on it. Photographer identity
   worked where curated collections did not.
3. **Subject labels dense enough to test that confounding**, and lift measured against the
   corpus rate rather than raw within-group share.
4. **Enough groups of enough size, and the arithmetic is not "roughly 15".** The protocols
   here draw boards of up to 50 *without replacement*, hold out assets that were not on the
   board, and — for the interval-coverage protocol — draw a second board disjoint from the
   first. So a group that has to serve the largest board plus a holdout needs **at least 51
   distinct members**, and a group that has to serve two disjoint boards of 50 needs **at
   least 101**. A 20-member group cannot supply even one 50-member board. The earlier figure
   of 15 came from the smallest board size and was silently applied to a protocol that needs
   the largest; a group between 15 and 50 can serve the n=10 and n=20 rows only, and the row
   it can serve is recorded per group rather than assumed. Sampling with replacement would
   let the test run at any size, and is banned here, because it changes the duplicate
   structure of the board — which is the exact quantity ADR-0005 measures.
5. **A creator field per item**, since the style grouping is now creator identity. A source
   with per-item licence metadata but no creator cannot serve this protocol.

**Candidate routes, in the order they get tried.** Openly-licensed aggregators that carry both
per-item licence metadata and a creator field are the first choice — Openverse and Wikimedia
Commons both do, and both should be screened on the creator field before anything else, since
a source failing requirement 5 is out regardless of how good its licensing is. Second choice
is a source whose bulk-dataset terms block publication but whose ordinary content licence does
not; that licence gets read at its own source with the same care that found clause 3.A above,
and never inferred from the restrictive one.

**Decision rule, fixed now so the choice does not turn into a survey.** The first route
yielding roughly 50 or more creator groups of 20 or more members, with per-item licence
clarity, wins. If both qualify, the openly-licensed aggregator wins on reproducibility, since
a reader can rebuild the set without holding any permission the repository cannot grant them.

Not pursued: seeking written permission for an otherwise-blocked source. An acceptance
criterion whose reproduce command depends on a permission the reader cannot obtain is not
reproducible, which contradicts the rule at the top of this file.

### `off-style-rejection` — INFORMATIONAL on PACS, not an acceptance gate

Build a board from one group, meaning one PACS domain while that is the only runnable source.
Score held-out members of that group and members of a deliberately different group. Require
that every on-look asset ranks above every off-look one, and report any inversion with both
images named so it can be looked at. Resample 100 board pairs rather than using one board per
group, so the result carries board-selection variance instead of one draw's luck.

```
uv run moodboard-eval off-style --dataset pacs --board-size 20 --boards 100 \
    --seed 20260807 --out results/off-style-pacs.json
```

Note what a PACS-only version of this test can and cannot say. Separating photographs from
sketches is a low bar and passing it is close to uninformative; the test earns its place only
on a source where the groups differ by treatment rather than by medium. Until the brand row
has a dataset, a green result here is not evidence the tool works — so this row is marked
informational in `eval/thresholds.json` and ADR-0003 does not gate on it. It was previously
one of three acceptance measurements while carrying this same caveat, which is a caveat that
argues for exactly the demotion it did not receive.

### `interval-coverage`

Sample boards of size n in {10, 20, 50} from a group. For each board, compute the interval
around a held-out asset's score. Independently resample a second board from the same group,
**disjoint from the first**, and recompute the score. Record how often the first board's
interval contains the second board's score, and report that empirical coverage against the
stated level, by board size **and by group**.

```
uv run moodboard-eval coverage --dataset pacs --board-sizes 10,20,50 \
    --resamples 1000 --level 0.90 --per-group --seed 20260807 \
    --out results/coverage-pacs.json
```

Coverage alone cannot pass this row, because an interval of [0,1] covers everything.
The same run reports median interval width and the all-tied rate, and
`eval/thresholds.json` bounds both.

If the observed coverage is below the stated level, the stated level is corrected to the
observed one, rounded down to the nearest 0.01. The claim moves, not the measurement.

### `axis-intervention`

Take 200 images sampled across all four PACS domains, seeded. Apply each intervention at
three magnitudes, record every axis's movement, normalise each axis by its own median
absolute movement across all interventions, and report the diagonal-to-largest-off-diagonal
ratio per intervention with a bootstrap interval over images. An intervention whose intended
axis does not move at all is a failure, not a ratio of 0/0.

```
uv run moodboard-eval axes --dataset pacs --images 200 --magnitudes 3 \
    --seed 20260807 --out results/axes-pacs.json
```

### `abstention-triggers`

All cases are resampled from PACS and every case names its α, because the same board is
serviceable at one α and must refuse at another.

- must-fire, resolution: n=10 boards, α=0.05.
- must-fire, multi-modality: two disjoint PACS domains, **each sub-look resampled to 8
  members**, α=0.05. The sub-look size is pinned here, and it is the whole point of the case:
  ADR-0004 rule 2 scores locally whenever a sub-look can express the request, so a two-group
  board with large sub-looks is one the tool is supposed to score. Constructing this case
  without pinning the sub-look size below the rule-1 minimum demands an abstention the
  decision rule forbids, and no conforming implementation can pass it.
- must-fire, far-outlier: assets from a PACS domain absent from the board.
- must-detect-and-score (not a refusal): two disjoint domains, **each sub-look at 25
  members**, α=0.05. Required outcome is a score carrying its category.
- must-stay-quiet: single-look boards at n=10/α=0.10, n=20/α=0.05, n=50/α=0.02, plus
  multi-look boards whose sub-looks each satisfy the requested α. False-abstention rates
  reported per reason.

```
uv run moodboard-eval abstention --dataset pacs --seed 20260807 \
    --out results/abstention-pacs.json
```

### `effective-board-size`

Build paired boards of equal file count, one from distinct sources and one padded with
near-duplicates generated by crop, mild recolour and recompression. Score a held-out on-style
population of 500 assets against both, across 20 board pairs. Report the rejection-rate
difference at α=0.05, and n_eff against the known distinct-source count. The control board
holds the **distinct-source count** constant, not n_eff — matching on the estimator under
test would validate it by construction.

```
uv run moodboard-eval neff --dataset pacs --board-pairs 20 --holdout 500 \
    --alpha 0.05 --seed 20260807 --out results/neff-pacs.json
```

## Licence notes

Per-source detail lives beside each fetch script, in `datasets/<name>/LICENCE.md`, because
that is where someone running the script will look. Summary:

**PACS** — unresolved, and recorded as unresolved. The mirror actually used declares the
licence `unknown`; the original release, which is where terms would have travelled, is gone.
The wider literature has operated on the assumption of non-commercial research use and this
project has not been able to verify that at source. PACS is additionally assembled from
several upstream collections with terms of their own, so a single licence line could not have
been accurate even had one been given. See `datasets/pacs/LICENCE.md`.

**Unsplash Lite** — blocks the row. This entry previously read "free for commercial and
non-commercial use" with the only restriction being on redistributing images, which is the
paraphrase that caused the problem: the actual grant is for *internal* use and clause 3.A bars
publishing any portion of the data in any manner. See
`datasets/unsplash-collections/LICENCE.md` for the quoted clauses.

**WikiArt** — unresolved, and it **is** a dependency of an acceptance criterion, which this
note previously denied. ADR-0003's fifth criterion reproduces the published WikiArt artist
retrieval benchmark before quoting the paper's number, so the record cannot reach its own
five-measurement gate while this row has no source, no split, no preprocessing and no
command. That is now a `blocked` row in the table above rather than a footnote, because a
dependency recorded only in prose is a dependency nothing checks.

Its artist labels remain an appealing source of human style grouping for the brand row as
well. Resolving the terms is a task in its own right and the answer belongs in this file
beside the source that settles it.

### `weight-reproduction` — BLOCKED, no source

Reproduce the published WikiArt artist-retrieval mAP@1 under the pinned CSD checkpoint and
compare against the paper's 64.56. Two things the current criterion gets wrong and that a
source, when found, has to carry:

**The tolerance is two-sided.** A one-sided shortfall gate accepts an arbitrarily *higher*
result, and a result meaningfully above the published number is evidence of a protocol or
checkpoint mismatch just as a lower one is. `eval/thresholds.json` registers ±2.0 absolute.

**Benchmark agreement is necessary, not sufficient, for checkpoint identity.** Several
checkpoints and protocols can land within two points of one number, so matching it does not
establish that these are the paper's weights. The criterion therefore compares the
checkpoint's own sha256 against an authoritative published hash where one exists, and where
none exists the claim is renamed: the repository says "benchmark reproduced under this
pinned revision" and never "the paper's weights", and ADR-0003's existing rule to strike the
published number applies.

**A rule earned from the Unsplash case, applying to every future source.** Read the terms
shipped *with the data* and quote the operative clause into the row, rather than summarising
the source's public marketing about licensing. The two disagreed here, the summary was the
permissive one, and the manifest was one command from being pushed to a public repository.
Terms get read before a dataset row is written, not before the measurement is run.
