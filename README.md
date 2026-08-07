# moodboard

Point it at a moodboard, meaning ten to fifty reference images that define a look, and it
scores how well any other image fits that look. The score comes with a decomposition of why
and with the nearest reference images as the explanation.

**Status: design stage.** The architecture decisions are written down in `docs/adr/` and the
datasets that will be used to test them are specified in `DATASETS.md`. The engine is not
built yet. Nothing in this README claims a measured result, because no measurement has been
committed yet. That rule holds for the whole project: a claim lands here only after the
artifact that reproduces it lands in the repository.

## The problem

A team generates two hundred candidate images and has to pick the ten that look like they
belong to the brand. Today a person does that by eye, one image at a time, and the reason a
particular candidate was rejected is difficult to write down. The volume is growing faster
than the number of people who can do the looking.

The interesting part is not "is this image good", which is a different and older question. It
is "does this image belong with those images", which is a question about a relationship
between one asset and a set.

**This is not claimed to be new.** A survey of the commercial landscape found no vendor
*publicly documenting* a calibrated numeric score for a candidate against a multi-image
reference set, but that is a statement about published documentation and not about what
exists. At least two products market workflows of this shape without publishing their
method, and enterprise tools that are not publicly documented cannot be ruled out at all.
So the survey supports "not publicly specified" and does not support "does not exist", and
those are different claims. What this repository offers is a method written down in enough
detail to be checked and refuted, which is a claim about transparency rather than priority.

## What it will do

```
moodboard build   <reference_dir> -o brand.mb    # embed references, fit the distribution, calibrate
moodboard score   <asset ...> -b brand.mb        # calibrated score, per-axis decomposition, exemplars
moodboard rank    <candidate_dir> -b brand.mb    # order many candidates, which is the main use
moodboard report  ... --html out.html            # a single self-contained file
```

The output is a JSON report before it is anything else. The schema is specified in
[ADR-0002](docs/adr/0002-report-contract.md) and is the boundary the viewer is built against.

## Design in one page

A score against a moodboard has to answer three things that a single number cannot answer on
its own.

**How tight is the board itself.** Ten near-identical references and ten deliberately varied
ones do not mean the same thing by "fits", so the report carries the board's own spread,
measured by leaving each reference out and scoring it against the rest.

**Which reference is doing the work.** Real moodboards carry an accent image that is there on
purpose and sits far from the rest. The report lists per-reference leverage so that image is
visible rather than quietly widening what counts as on-look.

**How much of the difference is real.** With ten to fifty references against a
high-dimensional representation, the difference between 73 and 68 can be noise. Every score
carries an interval, and any two assets whose intervals overlap are reported as tied. The
tool is built for ranking and it says so.

The representation is the load-bearing choice and it has its own decision record with an
acceptance test attached: see [ADR-0003](docs/adr/0003-style-representation.md).

## Scope for the first version

**A panel inside the design application, backed by an engine that runs outside it.** The work
being scored is already open in Photoshop, Illustrator or InDesign when the question gets
asked, so that is where the answer belongs. A designer with two hundred candidates is not
going to export them, run a command line tool and open a separate file, and every step
between the question and the answer is a step where this loses to doing it by eye.

The command line tool is how the engine is driven and tested, not a smaller version of the
product. It stays, because every acceptance measurement in the records below runs headless
over thousands of images and none of that is expressible through a panel.
[ADR-0006](docs/adr/0006-adobe-panel-is-the-primary-surface.md) has the surface decision and
the constraint that makes it an architectural one rather than a packaging choice: the engine
is Python with large model weights, a panel is a sandboxed JavaScript host, and unreleased
work under embargo should not leave the machine to get scored.

Aesthetic quality scoring, meaning "is this a good photograph", is a different axis and is
deliberately out of scope. Coherence with a reference set is not quality, and mixing them
would make both numbers harder to interpret.

## Repository layout

```
docs/adr/         architecture decision records, one file per decision
docs/design/      the longer design write-up
DATASETS.md       one row per validation claim, with source, license and prepare command
```

## Licence

MIT. See `LICENSE`.

The code licence says nothing about the datasets. Those are third-party sources
under their own terms, recorded per row in `DATASETS.md`, and at least one of them
is restrictive enough that this repository cannot republish even its manifest.
