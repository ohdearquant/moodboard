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
cannot be committed either. So a row is only `ready` when its licence permits publishing the
manifest specifically — checked at the terms shipped with the data, not inferred from the
source's public description of its licence. Where it does not, the fetch script and the
manifest's checksum are committed, the manifest is git-ignored, and the row is `blocked`.

## Rows

| claim | dataset | source | size | licence | redistributable | status |
|---|---|---|---|---|---|---|
| `content-invariance-coarse` | PACS | mirror `flwrlabs/pacs`, rev `394113073`; original release is gone, see below | 9,991 images, 4 style groups, 7 content groups | **unresolved**; mirror declares `unknown` | no, manifest only | **`ready`** |
| `content-invariance-brand` | Unsplash Lite | `unsplash.com/data/lite/latest` | 25,000 photos | grant is **internal use only**; publishing any portion is barred | **no, not even the manifest** | **`blocked`** |
| `off-style-rejection` | derived | no new source | held-out cells of the above | inherits | n/a | `ready` on PACS, `blocked` on brand |
| `interval-coverage` | derived | no new source | resampled boards of n in {10, 20, 50} | inherits | n/a | `ready` on PACS, `blocked` on brand |
| `human-style-grouping` | none yet | — | — | — | — | `blocked`, needs a source |
| `abstention-triggers` | derived | no new source | resampled boards below threshold resolution, two-group boards, cross-medium assets | inherits | n/a | `ready` on PACS |
| `effective-board-size` | derived | no new source | paired boards of equal file count, one padded with generated near-duplicates | inherits | n/a | `ready` on PACS |

Reproduce with `uv run --with pyarrow datasets/pacs/fetch.py`. Each fetch script verifies an
exact checksum and fails loudly rather than proceeding with a partial or substituted file.

**One row is ready, three are blocked on a licence, and that is the finding.** The
brand-photography half of the central claim has no usable source today. What follows records
how each row got to its status, because in both cases the first plan was wrong in a way that
would have survived into implementation.

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
    --out results/invariance-pacs.json
```

The result is a table of AUC by representation, committed under `results/`, with the exact
model revisions recorded in the output.

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
4. **Enough groups of enough size**: boards run to 50, and members must be held out, so a
   usable group needs roughly 15 items minimum and the corpus needs tens of such groups.
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

### `off-style-rejection`

Build a board from one group, meaning one PACS domain while that is the only ready source.
Score held-out members of that group and members of a deliberately different group. Require
that every on-look asset ranks above every off-look one, and report any inversion with both
images named so it can be looked at.

Note what a PACS-only version of this test can and cannot say. Separating photographs from
sketches is a low bar and passing it is close to uninformative; the test earns its place only
on a source where the groups differ by treatment rather than by medium. Until the brand row
has a dataset, a green result here is not evidence the tool works.

### `interval-coverage`

Sample boards of size n in {10, 20, 50} from a group. For each board, compute the interval
around a held-out asset's score. Independently resample a second board from the same group
and recompute the score. Record how often the first board's interval contains the second
board's score, and report that empirical coverage against the stated level, by board size.

If the observed coverage is below the stated level, the stated level is corrected to the
observed one. The claim moves, not the measurement.

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

**WikiArt** — still unresolved and still not a dependency of any acceptance criterion. Its
artist labels remain an appealing source of human style grouping. Resolving the terms is a
task in its own right and the answer belongs in this file beside the source that settles it.

**A rule earned from the Unsplash case, applying to every future source.** Read the terms
shipped *with the data* and quote the operative clause into the row, rather than summarising
the source's public marketing about licensing. The two disagreed here, the summary was the
permissive one, and the manifest was one command from being pushed to a public repository.
Terms get read before a dataset row is written, not before the measurement is run.
