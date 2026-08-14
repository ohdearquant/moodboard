# ADR-0009: Every published measurement is an immutable, revision-bound result

- **Status:** Proposed
- **Date:** 2026-08-08
- **Extends:** ADR-0006. This record makes ADR-0006's clone-reproducibility promise operational; it
  changes no scope decision of ADR-0006 itself. It also binds the existing acceptance criteria of
  ADR-0002 (amended by ADR-0008), ADR-0003, ADR-0004, and ADR-0005 to one results envelope without
  altering any threshold, protocol, or dataset row those records already define.
- **Related:** [ADR-0010](0010-frontend-verification.md) registers `frontend-verification` as a
  measurable claim under the same `docs/adr/README.md:16-30` dataset-and-threshold discipline this
  record generalizes; see "One command per measurement" below. [ADR-0008](0008-report-contract-for-viewer.md)'s
  conformance gate is deliberately outside this record's `results/` namespace; see "Registry coverage
  is exact" below.
- **Measurable claim:** none. This record defines the results contract around measurements
  and adds no statistical acceptance criterion. Every quality bar remains pre-registered in
  `eval/thresholds.json`.

## Context

This repository already has three parts of a reproducibility policy. A measurable decision
names a dataset with a source, size, licence, protocol and exact command
(`docs/adr/README.md:16-33`). A dataset is runnable only when its fetch script verifies a
pinned source, asserts the properties used by the protocol and reproduces the committed
manifest checksum (`DATASETS.md:11-57`). Every acceptance number is fixed before its
measurement first runs, and changing one requires a visible commit explaining what was
learned (`eval/thresholds.json:2-15`; `eval/README.md:1-15`).

The existing protocol sections name useful commands, but each command owns its own flags and
writes one flat JSON file. The commands all name seed `20260807`, and their output paths range
from `results/invariance-pacs.json` through `results/neff-pacs.json`
(`DATASETS.md:212-219,310-393`). The invariance protocol says that its result is committed
and records model revisions, but it does not define a common envelope for the other
measurements (`DATASETS.md:218-219`).

The report contract establishes a useful precedent. Its provenance object carries the engine
name and version, model repository, model revision, model SHA-256, command, seed and creation
time (`INTERFACES.md:256-296`;
`moodboard/schema/report_v1_0.schema.json:#/$defs/provenance`). That closed object does not
carry the producing Git revision, dataset-manifest digest, dependency-lock digest, threshold
registry digest or the full set of random streams
(`moodboard/schema/report_v1_0.schema.json:#/$defs/provenance/required`;
`moodboard/schema/report_v1_0.schema.json:#/$defs/provenance/properties`). It is sufficient
to explain an individual report and insufficient to reconstruct a project evaluation.

The dataset layer already performs strong integrity checks. Downloads are checked for magic
bytes, exact byte size and SHA-256 before use, and a changed source is fatal
(`datasets/_common.py:40-99`). Rebuilt manifests are compared with a committed
`checksums.sha256`; an absent or changed expectation is also fatal
(`datasets/_common.py:168-220`). PACS is pinned to one mirror revision and one Parquet
digest, then checked for the expected row count, domain counts, class vocabulary, populated
cells and absence of exact duplicates (`datasets/pacs/fetch.py:49-84,102-186`). Source
images and generated manifests remain local by policy
(`DATASETS.md:21-41`; `.gitignore:1-15`).

Those guarantees are separate today. No single repository object binds a result to all of
the command, protocol, code, environment, data, seeds and thresholds that produced it. A
number can therefore be copied into prose while its flat result file is replaced, regenerated
under newer code or evaluated against a changed threshold without a machine-checkable
failure (`DATASETS.md:212-219,310-393`;
`moodboard/schema/report_v1_0.schema.json:#/$defs/provenance`).

This record makes ADR-0006's clone-reproducibility promise operational. That promise is
already qualified to runnable dataset rows, and it distinguishes integrity from availability
and permission (`docs/adr/0006-standalone.md:32-40`). The qualification remains. The
brand-photography, human-style-grouping and weight-reproduction rows are blocked today
(`DATASETS.md:84-95,235-300,413-440`). A result contract cannot turn a missing or
unpublishable input into a result.

## Decision

Adopt one repository-native evaluation entry point and one immutable result bundle. A
published number is valid only when it points to a value in a committed result bundle that
records enough information to replay the run from a clean clone.

The result chain has these components:

```text
eval/measurements.json -----------+
eval/thresholds.json -------------+
DATASETS.md + datasets/<id>/ -----+--> moodboard-eval run
uv.lock + clean Git revision -----+          |
model revision + model digest ----+          v
                                  results/<measurement>/<run-id>/
                                                |
                                                v
                                  documentation result reference
```

### One command per measurement

The implementation will add `eval/measurements.json`, validated as a closed schema. It maps
each measurement identifier to its dataset identifier, authoritative runnable or blocked
status, protocol arguments, ordered seed set, named random streams, raw-result schema and
JSON Pointers into `eval/thresholds.json`. The status cells in `DATASETS.md` mirror this
machine-readable state and a repository test requires equality. Values already present in
`eval/thresholds.json` are referenced and never copied into the measurement registry.
The registry contains no pass or fail value. Thresholds and their rationales remain solely in
`eval/thresholds.json` and `eval/README.md`
(`eval/thresholds.json:2-371`; `eval/README.md:1-79`).

The public command has no protocol, seed or threshold override flags:

| Measurement identifier | Canonical command | Pinned input state |
|---|---|---|
| `content-invariance-coarse` | `uv run --frozen moodboard-eval run content-invariance-coarse` | PACS, runnable with the manifest withheld. |
| `content-invariance-brand` | `uv run --frozen moodboard-eval run content-invariance-brand` | Unsplash collections, blocked. |
| `off-style-rejection` | `uv run --frozen moodboard-eval run off-style-rejection` | PACS is runnable and informational; the brand arm is blocked. |
| `interval-coverage` | `uv run --frozen moodboard-eval run interval-coverage` | PACS, runnable with the manifest withheld. |
| `human-style-grouping` | `uv run --frozen moodboard-eval run human-style-grouping` | No source, blocked. |
| `axis-intervention` | `uv run --frozen moodboard-eval run axis-intervention` | **No status asserted.** `DATASETS.md` carries a protocol section for this measurement (`DATASETS.md:344`) but no row in the Rows table, so there is no status cell for the registry test below to match. Add the row, or drop this identifier from the registry; asserting a status the repository does not carry is the failure that test exists to catch. |
| `abstention-triggers` | `uv run --frozen moodboard-eval run abstention-triggers` | PACS, runnable with the manifest withheld. |
| `effective-board-size` | `uv run --frozen moodboard-eval run effective-board-size` | PACS, runnable with the manifest withheld. |
| `weight-reproduction` | `uv run --frozen moodboard-eval run weight-reproduction` | WikiArt artist retrieval, blocked. |
| `frontend-verification` | `uv run --frozen moodboard-eval run frontend-verification` | Repository-owned deterministic image and report recipe defined by ADR-0010; runnable, no external fetch. |

The blocked commands terminate before model loading, write no measurement result and name the
missing readiness condition from `DATASETS.md`. They exist so every declared project
measurement has one stable entry point, not so a blocked row appears partially runnable.

`frontend-verification` has no `datasets/<id>/fetch.py` bundle, because its source images and
report fixtures are generated in-repository by ADR-0010's own generator rather than downloaded from
a pinned external mirror. The dataset-acquisition and integrity steps below apply only to
mirror-sourced datasets; `frontend-verification`'s canonical command instead runs ADR-0010's
generator, the engine build-and-rank pipeline, and its layered test suite, and writes its
`results/frontend-verification/<run-id>/` bundle exactly as any other measurement does. Its
`summary.json` verdict is `PASS` or `FAIL`; ADR-0010's zero-differing-pixel visual gate and its unit
and property layers are the checks contributing to that verdict.

`score_semantics` is a deterministic report-contract rule rather than a dataset
measurement. Its values remain read directly from
`eval/thresholds.json:/score_semantics` and are covered by contract tests
(`eval/thresholds.json:89-113`). It does not gain a synthetic dataset row.

The runner, rather than the caller, supplies the parameters currently written as flags in
`DATASETS.md`. This preserves the registered board sizes, resample counts, bootstrap unit,
models, alpha values and seed while removing a second source of truth from copied shell
commands (`DATASETS.md:199-227,302-393`; `eval/thresholds.json:16-371`).
Implementation of this record must place every evaluation and acquisition dependency in
`uv.lock`. The current PACS command adds an unpinned `pyarrow` at invocation time, and the
current package exposes only the `moodboard` console script (`DATASETS.md:97-102`;
`pyproject.toml:20-28`). Those are implementation gaps to close before this record can be
accepted.

### Dataset acquisition and integrity

Each runnable dataset is a four-part repository bundle: its row in `DATASETS.md`, its
`fetch.py`, its `checksums.sha256` and its `LICENCE.md`. The measurement registry points
to that bundle by dataset identifier. The runner invokes the registered fetch command before
scoring, including when a cached download or manifest exists. The standard acquisition
command is `uv run --frozen datasets/<dataset-id>/fetch.py`; for current runnable
measurements the concrete command is `uv run --frozen datasets/pacs/fetch.py` after
`pyarrow` has been placed in the lock (`DATASETS.md:84-102`).

The fetch phase must establish all of the following before it returns the manifest:

1. The source URL or repository and immutable revision are recorded.
2. Every downloaded archive or shard matches its recorded SHA-256, byte size and magic bytes.
3. Extraction starts from the verified archive and cannot reuse an unchecked prior extraction.
4. Every manifest row carries the item identifier, labels required by the protocol and a
   content digest.
5. Row counts, group vocabularies, required cells and other protocol assumptions are fatal
   assertions.
6. The byte-for-byte manifest matches the tracked `checksums.sha256`.

The ordinary runner never invokes `--write-checksums`. That option is a maintainer action
whose resulting pin change must be reviewed and committed before a measurement runs
(`datasets/_common.py:173-180`; `datasets/pacs/fetch.py:93-100,178-186`). A download,
archive or manifest mismatch exits as `BLOCKED`. Updating a pin in response to that failure
is a new input decision, not a retry.

Generated manifests, source images, archives and extracted vendor files remain regenerated
local artifacts. Their omission from Git is deliberate and does not weaken the binding
because the result records the manifest digest and the repository records the acquisition
recipe and expected digest. If the recipe cannot legally and non-interactively reconstruct
the same manifest, the dataset row is blocked and no project number may cite it.

### Random seeds and deterministic streams

The existing master seed `20260807` is preserved for every current command that names it
(`DATASETS.md:212-215,310-313,331-335,352-355,376-393`). The migration to
`eval/measurements.json` records it as the ordered seed set `[20260807]`; it does not
select a new seed after observing a result.

Each seed is expanded with NumPy `SeedSequence` into named child streams whose integer
identifiers are fixed in the measurement registry. At minimum the names distinguish dataset
sampling, board sampling, intervention generation and bootstrap resampling. A stream is
`Generator(PCG64(SeedSequence([master_seed, stream_id, replicate_index])))`, so creating
or consuming another named stream cannot move it. Workers receive their child seed
explicitly. Python hash randomization, NumPy, model-library randomness and worker ordering
are initialized by the runner; code under evaluation may not create an unseeded global
generator. Input rows are sorted by manifest item identifier before any random draw.
Evaluation math uses one worker thread unless a measurement registers a deterministic
parallel reduction order before its first run.

Adding a seed, removing a seed, changing the generator algorithm or renumbering a child
stream is a protocol change. It is committed before the first run under that change and
therefore produces a new run identifier. Every seed is run and preserved. Selecting the best
seed, discarding a valid seed or adding replications after looking at the first result is
forbidden.

Canonical measurements will use deterministic CPU execution under the locked dependency set.
Evaluation workers use one deterministic execution mode and stable serialization. If a
future measurement requires a nondeterministic accelerator, it needs a prior protocol
amendment that registers its replication count, summary and comparator before results are
seen. This record supplies no numerical tolerance for such a future case.

### Exact code and environment binding

A canonical run starts only from a clean tracked and untracked source tree. Ignored dataset
downloads, generated manifests, model caches and the designated result output root do not
make the tree dirty. Any other modified or untracked file causes the runner to stop before
measurement.

The runner records the full, unabbreviated value of `git rev-parse HEAD` as
`code_revision`. It also records SHA-256 digests for `uv.lock`,
`eval/measurements.json`, `eval/thresholds.json`, every dataset source archive or shard,
the dataset manifest and every model weight file, along with the exact Python version,
operating system, architecture and evaluation device. `uv run --frozen` prevents the run
from silently changing the lock. Model repository names and revisions do not replace weight
digests. The environment fingerprint excludes host name, user name, absolute paths and
timestamps. It includes only computational inputs, including Python, operating system,
architecture, device, locale, time zone, thread mode and dependency-lock digest.

Publishing therefore uses two commits. First, commit all source, lock, protocol, threshold,
model-pin and dataset-pin changes. Second, run the measurement from that clean revision and
commit the result bundle without changing those inputs. The result commit may differ from
`code_revision`; the recorded revision is the exact commit whose code executed and the
later commit is only its result carrier.

The run identifier is the SHA-256 of canonical JSON containing the measurement identifier,
full code revision, dependency-lock digest, measurement-registry digest, threshold-registry
digest, dataset-source and manifest digests, ordered seed set, model digests and environment
fingerprint. A change to any bound input creates a different identifier and a different
directory. A runner refuses to overwrite an existing identifier.

### Raw results, summaries and publication references

Every valid run writes one immutable directory:

```text
results/<measurement-id>/<run-id>/
    run.json
    raw/
    summary.json
    checksums.sha256
```

`run.json` is the closed provenance envelope. It carries the schema version, exact
acquisition and measurement commands as argument arrays, all identities and digests above,
the ordered seeds, named random streams, environment fingerprint, and start and completion
times.

`raw/` carries the measurement-specific observations sufficient to recompute every value
in `summary.json` without rerunning a model. The measurement registry names a versioned
schema for those files. Expanded pair sets need not be stored when the stored per-item
values, labels and deterministic pairing rule reproduce them, but a summary statistic by
itself is never raw data.

`summary.json` carries derived metrics, the aggregation named for each metric and an array
of checks. Each check contains an observed-value JSON Pointer, the exact
`eval/thresholds.json` JSON Pointer it evaluates, the comparison operator and the outcome.
Its top-level measurement verdict is one of `PASS`, `FAIL` or `INFORMATIONAL`.
`off-style-rejection` remains `INFORMATIONAL` on PACS because the existing registry marks
it as a non-gate (`eval/thresholds.json:75-87`). The existing content-invariance
partial-pass rule and every other gate are evaluated exactly as registered
(`eval/thresholds.json:49-73`). `BLOCKED` is an input state and never a measurement
verdict.

`checksums.sha256` covers `run.json`, `summary.json` and every file under `raw/`.
It detects alteration of the committed record. Reproduction compares regenerated raw files and
the summary with the committed versions after excluding operational timestamps from the
comparison envelope.

A valid measurement run is committed whether its verdict is `PASS`, `FAIL` or
`INFORMATIONAL`. A quality failure is a recorded result and is not deleted or overwritten. A
download failure, schema failure or interrupted worker is not a measurement result; it
changes no result directory and leaves the dataset or run blocked until the operational
problem is resolved.

The following artifacts are regenerated and never serve as proof: dataset archives,
images, extracted files, manifests, model caches, virtual environments, temporary worktrees
and replay output under `results/_reproduced/<run-id>/`. A reader-facing plot or table may
be committed, but it records its generator command and source result pointers. It is a view
of the committed result bundle and never the only copy of a number.

Every number in repository prose that describes this project's measured behavior carries a
relative result reference of the form
`results/<measurement-id>/<run-id>/summary.json#<JSON-Pointer>`. The pointer resolves to
the exact value shown. A table or sentence with several values supplies one pointer per value
or one pointer to the containing object. Numbers quoted from an external paper cite that
source and, when used by a project check, also point to their registered value in
`eval/thresholds.json`.

Pre-registered criteria and protocol inputs point to their JSON Pointer in
`eval/thresholds.json`; asserted dataset counts point to the fetch assertion and dataset
pin that reproduce them. Those are inputs rather than observed project results. Before this
record can be accepted, existing observed numbers in ADR prose, evaluation commentary and
dataset commentary must either gain a result reference or be removed. This includes measured
axis-intervention commentary currently embedded beside the registered protocol
(`eval/thresholds.json:141-154`). Preserving the threshold file does not exempt an observed
number inside its commentary from a result binding.

The replay sequence is:

```text
reader
  -> committed run.json
  -> temporary clean worktree at code_revision
  -> locked environment and verified model weights
  -> fetch, verify and rebuild the named dataset manifest
  -> execute every registered seed and named stream
  -> regenerate raw/ and summary.json
  -> compare schemas, pointers and checksums
  -> print REPRODUCED or NONREPRODUCIBLE
```

## Contract assertions and breaking-input regression cases

**Registry coverage is exact.** A repository test asserts that every measurement row and
protocol in `DATASETS.md` has one entry in `eval/measurements.json`, every dataset-backed
measurement in `eval/thresholds.json` maps to at least one entry, every runnable entry backed
by an external mirror names one fetch bundle, every `DATASETS.md` status matches the registry
and every threshold pointer resolves. The deterministic `score_semantics` section is the one
explicit non-measurement exception; `frontend-verification` is the one explicit
runnable-without-a-fetch-bundle exception, because its source images and report fixtures are
generated in-repository rather than downloaded, per ADR-0010. Adding a ready dataset row
without a registry entry, drifting a status cell, deleting a protocol entry or pointing at an
unknown threshold path must fail the test. `eval/measurements.json` names no entry for
ADR-0008's report-contract conformance gate, which has no dataset row and writes outside
`results/` under `contract-verification/`; this test does not require one.

**Blocked inputs cannot publish.** A test invokes the brand, human-grouping and
weight-reproduction commands while their rows are blocked. Each command must exit before
model loading, name the blocking row and leave no directory under `results/`. A regression
that writes a summary from the exploratory Unsplash manifest or an unspecified WikiArt split
must fail.

**Dataset integrity is fail-closed.** Fixture tests corrupt one byte of a pinned archive,
change its size, replace its magic bytes, reorder a manifest, remove a required
`content_group`, empty one PACS style-by-content cell and change one manifest content hash.
Every case must stop before scoring. This pins both byte identity and the semantic assumptions
that the current PACS fetcher asserts (`datasets/pacs/fetch.py:107-175`).

**Seeds are complete and order-independent.** Running a small fixture twice at the same code
revision, manifest digest and seed set must produce byte-identical raw scientific output and
`summary.json`. Running workers in the reverse discovery order must preserve those bytes
because streams are named. Changing one seed must change the run identifier. Introducing an
unseeded generator, dropping a seed's raw rows or choosing only the best seed must fail
reproduction or schema validation.

**The code revision is the executed revision.** A test dirties a tracked scoring file, adds an
untracked source module and supplies a `run.json` whose revision differs from the worktree.
All three cases must stop before scoring. A second fixture commits a one-line scoring change
and asserts that the same measurement receives a different run identifier.

**Threshold history cannot be bypassed.** The CLI rejects threshold-value overrides. A test
changes `eval/thresholds.json` after a fixture result was produced and leaves the result
envelope unchanged; verification must fail on the registry digest before evaluating the
verdict. A test against `off_style_rejection.acceptance_gate: false` must emit
`INFORMATIONAL`, even when every observed value happens to satisfy its recorded number.

**Committed artifacts are closed and linked.** Tests delete a raw file, alter a summary
value, add an undeclared key to `run.json`, corrupt `checksums.sha256`, and cite a missing
or non-resolving JSON Pointer from documentation. Each case must fail verification. A
scientific `FAIL` with valid provenance and matching raw output must pass artifact
verification while retaining its `FAIL` measurement verdict.

## Verification

Acceptance of this record requires both contract verification and a replay through the
public command.

**Command.** Run
`uv run --frozen moodboard-eval verify --all-committed` to validate every committed bundle,
then run
`uv run --frozen moodboard-eval reproduce results/content-invariance-coarse/<run-id>/run.json`
for at least one runnable PACS result. The replay command creates and uses a temporary clean
worktree at the recorded revision. It does not require the reader to edit their current
checkout.

**Data provenance.** The replay reads the measurement, seed and threshold pointers from the
committed `run.json` and registries. For the named PACS result it fetches
`flwrlabs/pacs` at revision
`394113073258ead631f617d2e13bb377c0715c4b`, verifies the
`4fc041ee92eec6043fe6e2859e8bdd138e5f958bc621afd153879812cbe65ff5`
Parquet digest, rebuilds the manifest and verifies its committed checksum
(`datasets/pacs/fetch.py:49-58,102-186`;
`datasets/pacs/checksums.sha256:1`). It then verifies the model digests recorded by the
original run.

**Artifacts.** The original command writes
`results/content-invariance-coarse/<run-id>/{run.json,raw/,summary.json,checksums.sha256}`.
The replay writes the same scientific files beneath
`results/_reproduced/<run-id>/`. Dataset bytes, the rebuilt manifest, model caches, the
temporary environment and the temporary worktree remain regenerated local artifacts.

**Inspected output.** `verify --all-committed` prints one
`CONTRACT PASS <measurement-id> <run-id>` or
`CONTRACT FAIL <measurement-id> <run-id>: <reason>` line and exits nonzero if any bundle,
schema, digest or result reference fails. `reproduce` prints
`REPRODUCED <measurement-id> <run-id>` only when regenerated raw scientific output and
summary match; otherwise it prints `NONREPRODUCIBLE` with the first differing artifact and
exits nonzero. The reader separately inspects `summary.json#/verdict` and its `checks`
array for the scientific `PASS`, `FAIL` or `INFORMATIONAL` outcome. Artifact
reproducibility and scientific acceptance are distinct results and both are visible.

## Alternatives considered

**Keep one bespoke shell command and one flat result file per protocol.** This is direct and
matches the current documentation (`DATASETS.md:212-219,310-393`). It is rejected because
copied flags become a second protocol registry, flat filenames are overwriteable, and none
of the files has to bind itself to the code, dataset manifest or threshold version that
produced it.

**Commit only summaries and regenerate raw observations on demand.** This keeps the repository
small. It is rejected because a summary cannot show whether exclusions, balancing,
aggregation or seed handling changed. A reader could rerun the model, but could not audit the
published calculation against the data that was actually used.

**Commit the source datasets and generated manifests with every result.** This would improve
offline availability. It is rejected because the current sources have restrictive or
unresolved terms, and repository policy therefore withholds both images and manifests
(`DATASETS.md:21-41,65-82`). Content digests and deterministic acquisition preserve
identity without republishing the inputs.

**Use an external experiment tracker or object store as the system of record.** Such systems
provide searchable metadata and can handle larger artifacts. It is rejected for the current
scale because ADR-0006 promises clone-based reproduction and excludes required external
services from the product boundary (`docs/adr/0006-standalone.md:26-46`). An external
dashboard may mirror results, but it cannot be the only place a published number is recorded.

**Bind results to a release name or mutable tag.** This is shorter to read than a full commit
digest. It is rejected because a name can move and does not identify uncommitted protocol or
threshold changes. The full Git object identifier plus a clean-tree assertion is the
smallest repository-native code identity with the required property.

## Consequences

A reader can walk from any published number to a summary value, from the summary to its raw
observations and registered criterion, and from the run envelope to the exact code, data,
models, environment and seeds. Failed measurements remain visible, which removes the
incentive and ability to publish only favourable valid runs.

The result directory becomes an append-only record. Changing code, a dependency,
model, dataset pin, seed or threshold creates a new identifier and preserves the old
interpretation. A result can be discussed after the project moves on without asking which
version of a flat JSON file the sentence meant.

Dataset licences remain respected because source bytes and manifests stay local. This buys
integrity, not perpetual availability. If a pinned source disappears, a new reader cannot
reconstruct the input even though the surviving result still proves which input was used.

The implementation cost is substantial for a small repository. It adds an evaluation
registry, result schemas, a runner, a replay path, documentation-reference validation and
fixture coverage. Evaluation dependencies must move into the lock, and existing protocol
commands in `DATASETS.md` must be rewritten to the canonical entry point
(`DATASETS.md:212-219,310-393`). None of that is implemented by this record.

Deterministic CPU execution can be slower than accelerator execution. Committing raw
scientific output also grows the repository, and the two-commit publication sequence is less
convenient than running against a dirty working tree. Those costs are accepted because
convenience at this boundary would make the central public record replaceable.

## Invalidation conditions

This decision must be revisited if two clean replays on supported platforms cannot produce
identical raw scientific output and summaries under the locked CPU environment. The remedy
may be a digest-pinned execution image or a pre-registered numerical or statistical
reproduction comparator. A tolerance chosen after observing the disagreement is not a valid
repair.

It must also be revisited if a raw result is too large or too restricted to commit safely.
That case requires a durable, publicly retrievable content-addressed store, an integrity pin
in this repository and a superseding decision that explains the new availability and
retention risk. A link to an ordinary mutable object is insufficient.

An affected result and every claim that cites it must be withdrawn if a source becomes
unavailable without a lawful mirror, its terms resolve against the recorded use, its
manifest cannot be rebuilt to the committed digest, or a model weight digest can no longer
be obtained. Historical integrity does not grandfather a result whose input can no longer
meet the repository's runnable-data promise.

Finally, this record is wrong if a future measurement has irreducible stochastic or
hardware-dependent behavior that the deterministic stream contract cannot express. Before
that measurement runs, a later record must define its replication count, aggregation,
environment class and comparator in the same pre-registration style. The existing result
contract must not be relaxed after seeing the first disagreement.
