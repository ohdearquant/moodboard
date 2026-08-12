# moodboard

Point it at a moodboard, meaning ten to fifty reference images that define a look, and it
scores how well any other image fits that look. The score comes with a decomposition of why
and with the nearest reference images as the explanation.

**Status: working engine, unaccepted representation claim.** The offline classical pipeline,
exact conformal scoring, board artifact, report validation, and CLI are implemented and tested.
The learned visual representation is an explicit experimental opt-in through Khive and Lattice.
No quality measurement has been committed yet, so this README does not claim that either
representation has passed the aesthetic/style acceptance criteria. That rule holds for the
whole project: a claim lands here only after the artifact that reproduces it lands in the
repository.

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

## Run it

```bash
uv run moodboard build references/ -o brand.mb
uv run moodboard rank candidates/ -b brand.mb -r references/ -o report.json
uv run moodboard report report.json
```

The output is a JSON report before it is anything else. Rank writes the closed report schema v1.1
specified by [ADR-0008](docs/adr/0008-report-contract-for-viewer.md), which amends the v1.0 contract
in [ADR-0002](docs/adr/0002-report-contract.md). Readers keep an exact v1.0 compatibility path;
unknown minor or major versions are refused before report content is interpreted.
`moodboard report report.json --html report.html` verifies the installed viewer manifest and report
contract, then atomically writes one self-contained offline HTML file. It never recomputes a score
or fetches a runtime asset. Source checkouts stage the optional viewer package with
`npm --prefix viewer run build`; ordinary engine-only source and wheel builds remain valid when that
generated directory is absent. Both validation and HTML inlining apply the same 128 MiB report-file
ceiling before JSON, base64, or image decoding; the limit is a resource bound, not a score rule.

`build` freezes the complete numeric scoring policy into the verified `brand.mb` and its hash.
`rank` consumes that stored policy; later edits to `eval/thresholds.json` cannot move an existing
board's scores. Passing `rank --thresholds PATH` is optional and asserts that the file still
matches the board—it never overrides it. Report v1.1 discloses that complete numeric policy,
including configured and effective neighbourhood size, category and distance cuts, interval level,
and far-outlier multiplier plus its recorded source. It also carries candidate thumbnails and
content metadata, governed axis definitions, structured invocation and schema provenance, and
exactly `min(3, reference_count)` distinct closest references per asset. The accepted CLI setting is
therefore `--exemplars 3`; another value is refused rather than written into a weaker report.

The default above is fully offline and uses `ClassicalEncoder`. To publish the exact reference
bytes to Khive BlobStore and obtain Lattice visual embeddings from the Khive Moodboard pack,
opt in explicitly on both build and rank:

```bash
uv run moodboard build references/ -o brand.mb \
  --encoder khive-lattice \
  --khive-executable kkernel \
  --khive-config /absolute/path/to/khive.toml \
  --khive-actor lambda:moodboard \
  --khive-namespace local

uv run moodboard rank candidates/ -b brand.mb -r references/ -o report.json \
  --encoder khive-lattice \
  --khive-executable kkernel \
  --khive-config /absolute/path/to/khive.toml \
  --khive-actor lambda:moodboard \
  --khive-namespace local

uv run moodboard retrieve 01234567-89ab-cdef-0123-456789abcdef \
  --top-k 20 \
  --khive-executable kkernel \
  --khive-config /absolute/path/to/khive.toml \
  --khive-actor lambda:moodboard \
  --khive-namespace local
```

Khive mode is experimental and fail-closed. The active descriptor fingerprint plus the pinned
Moodboard adapter revision must match the board's model revision; malformed, partial, drifting, non-finite, wrongly
dimensioned, or non-unit embedding results stop the run. See
[ADR-0011](docs/adr/0011-khive-native-visual-assets.md).
`retrieve` reports Khive's self-excluded exact-cosine neighbours, asset UUIDs, BlobStore
content references, and names. Its cosine is retrieval evidence in `[-1,1]`; it is not
the conformal moodboard score, an aesthetic/coherence measurement, or a replacement for `rank`.

Exact source-byte ingest in this v1 pack accepts PNG, JPEG, and WebP. The offline classical
encoder retains the CLI's broader image-format support.

### Bootstrap the Khive/Lattice backend

Stock Khive builds do not load the opt-in Moodboard pack. Build a `kkernel` from a Khive
checkout that includes `khive-pack-moodboard` and supports the ordered
`--ops-file ... --save-file ...` transport, then point `--khive-executable` at that binary.
Configure the pack and a durable local BlobStore explicitly:

```toml
# /absolute/path/to/khive.toml
[runtime]
packs = ["kg", "moodboard"]

[storage.blob]
backend = "fs"
root = "/absolute/path/to/khive-blobs"
```

The equivalent pack selection is `KHIVE_PACKS=kg,moodboard`; omitting `--khive-config` keeps
Khive's usual `KHIVE_CONFIG` and project-discovery fallback. Configure the Qwen3.5 checkpoint
before invoking Moodboard:

```bash
export KHIVE_MOODBOARD_MODEL_DIR=/absolute/path/to/qwen3.5-0.8b
export KHIVE_MOODBOARD_MODEL_REVISION=your-immutable-deployment-revision

# Optional deployment attestation. If present it must equal the pack's framed tree digest.
export KHIVE_MOODBOARD_CHECKPOINT_SHA256=64-lowercase-hex
```

The pack always computes the canonical checkpoint-tree SHA-256. When the optional expected
digest is omitted, call `moodboard.model()` once and pin its returned `checkpoint_sha256` for
subsequent deployment attestation. Actor and namespace remain explicit Moodboard CLI options;
they are not inferred from the storage root. Moodboard sends the configured namespace both as
`kkernel` execution attribution and inside every pack operation, where it selects durable asset,
vector, and retrieval state. Khive asset UUID lookup remains global; the namespace narrows vector
candidates, so searching a globally known asset from another namespace succeeds with no hits.

One Moodboard request is bounded to 64 total asset occurrences and 32 MiB of decoded bytes
across those occurrences, before content deduplication. Admission is rolling: a source file is
read only to the remaining budget plus one byte, and an array's exact canonical-PNG size is
checked before encoding it. The client streams its ops JSONL, but `kkernel` currently retains
batch JSON in process; the conservative aggregate limit prevents multi-gigabyte base64 amplification.
Larger corpora must be deliberately partitioned into audited calls rather than being silently
split into repeated cold model loads.

Khive-mode CLI loading applies the same source count/byte limits before Pillow decoding, rejects
either source side above 8192 pixels, and retains at most 256 MiB of matte-composited RGB arrays
for diagnostics and thumbnails. The offline classical path keeps its existing loading behavior.

Ordinary tests use a fake executable and never load a model. The opt-in real-process smoke is
gated by `MOODBOARD_REAL_KKERNEL`; set `MOODBOARD_REAL_KHIVE_CONFIG` when that binary needs the
explicit config above. The descriptor smoke additionally requires
`MOODBOARD_REAL_KHIVE_MODEL=1` plus the model environment and is intentionally outside the
offline test gate.

```bash
MOODBOARD_REAL_KKERNEL=/absolute/path/to/kkernel \
MOODBOARD_REAL_KHIVE_CONFIG=/absolute/path/to/khive.toml \
uv run pytest tests/test_khive_real.py -q

# Add MOODBOARD_REAL_KHIVE_MODEL=1 to include moodboard.model descriptor validation.
```

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

**A standalone default with an explicit Khive integration.** The classical CLI and library
depend on no external service. No design-application SDK is a dependency
([ADR-0006](docs/adr/0006-standalone.md)); the Khive path is a small process adapter selected
by the operator, not a required SDK or an import-time service dependency. The JSON report
remains the display boundary.

Aesthetic quality scoring, meaning "is this a good photograph", is a different axis and is
deliberately out of scope. Coherence with a reference set is not quality, and mixing them
would make both numbers harder to interpret.

## Repository layout

```
docs/adr/         architecture decision records, one file per decision
DATASETS.md       one row per validation claim, with source, license and prepare command
moodboard/        Python engine, Khive adapter, artifact and report contracts
tests/            deterministic offline unit, property, CLI and wire-protocol tests
```

## Branch policy

`main` is pushed to directly. The branch ruleset blocks deletion and force-push, and it
deliberately does not require a pull request. This repository has one author, so a
required-pull-request rule that the only pusher is permitted to bypass would be a false
statement about how code actually gets in, and a false control is worse than a missing
one because it gets cited. The rule was removed rather than left standing as decoration.

The deletion and force-push rules carry no bypass at all, including for administrators, so
they bind every actor including the one that does the pushing. That is deliberate. Those
two guard irreversible acts that normal work here never performs, so making them real costs
nothing and leaves the ruleset saying only true things.

This is written down so that the absence of a pull-request requirement reads as a
decision. If a second author starts committing here, the rule should come back.

## Licence

MIT. See `LICENSE`.

The code licence says nothing about the datasets. Those are third-party sources
under their own terms, recorded per row in `DATASETS.md`, and at least one of them
is restrictive enough that this repository cannot republish even its manifest.
