# ADR-0011: Khive owns visual assets and Lattice descriptor inference

- **Status:** Proposed
- **Date:** 2026-08-08
- **Extends:** ADR-0003's representation boundary, ADR-0005's immutable board identity,
  and ADR-0006's standalone command-line surface.
- **Measurable claim:** none. This record assigns ownership and failure semantics. A learned
  descriptor still has to pass ADR-0003's registered content-invariance measurements before
  its score may be described as aesthetic or style coherence.

## Context

The engine deliberately separates an `Encoder` from its exact conformal and diagnostic math.
That makes a remotely managed visual representation possible without moving the statistical
engine, its calibration rules, or the report contract into an inference service.

Khive already supplies the two durable identities a visual asset needs: an artifact entity in
the knowledge graph and an opaque `content_ref` in its content-addressed BlobStore. Its
Moodboard pack can invoke Lattice for visual inference and index the resulting vector in a
revision-bound vector space. Reimplementing those stores in this repository would create a
second asset catalogue, a second lifecycle, and references that cannot participate in Khive
retrieval.

The process boundary is consequential. Image payloads are much larger than a safe command-line
argument, Khive operations may partially fail unless strict execution is requested, and an
unattributed actor would make later interaction learning impossible to audit. A client that
accepts a short or malformed batch could silently pair one reference with another reference's
embedding, invalidating every downstream result while leaving all numerical checks plausible.

## Decision

### Khive and Lattice own storage, inference, and retrieval

The optional `KhiveLatticeEncoder` uses the first-party Khive Moodboard pack:

- `moodboard.model()` returns the active immutable descriptor identity;
- `moodboard.ingest(image_base64, name?, media_type?, caption?)` publishes the exact decoded
  request bytes to Khive BlobStore, creates or reuses their visual-asset entity, runs Lattice
  inference, and returns the descriptor plus a unit-normalized embedding; and
- `moodboard.search(asset_id, top_k?)` is Khive's visual-retrieval surface. Search does not
  replace, blend with, or calibrate the score defined by ADR-0003.

The client obtains `moodboard.model()` before ingest. Every ingest result must carry exactly
the same descriptor identity. Descriptor drift within a batch is fatal. The client also
rejects a missing result, a non-finite value, a dimension mismatch, or a vector that is not
unit length within a documented floating-point tolerance. It never repairs or renormalizes a
server result, because doing so would hide a broken wire or model contract.

The plain `Encoder.embed` boundary receives decoded arrays, not source files. Programmatic array
callers are therefore converted without mutation to RGB8 or RGBA8 using the same accepted value
ranges as the classical axes, then encoded as deterministic PNG bytes. RGBA remains transparent
so the descriptor-pinned server matte, rather than the adapter, owns alpha compositing. The exact
PNG encoder remains byte-frozen in `moodboard-khive-adapter-v3`: every scanline uses filter 0 and
the zlib stream uses a fixed wrapper plus manually framed DEFLATE stored blocks, with fixed PNG
chunk framing/CRC and Adler-32. It therefore has no Pillow/zlib compressor heuristic in its byte
identity. A byte-for-byte RGBA and BLAKE3 golden plus a fresh-process test pin the result;
changing any array-to-byte conversion requires an adapter revision bump. Adapter v2 produces the
same canonical PNG bytes as v1 but binds the selected storage namespace inside each pack operation;
that persistence-scope correction requires a revision bump even though vector math did not change.
Adapter v3 preserves those bytes and namespace rules but bounds each serial Khive ingest process to
eight unique assets. That changes durable failure boundaries and therefore also requires a revision
bump even though it does not change the descriptor or returned vector math.
The encoder revision entering `board_hash` is
`<descriptor fingerprint>+<adapter revision>`, so a client-side conversion cannot move embeddings
under an unchanged board model identity. Their BlobStore `content_ref` identifies that **canonical
adapter rendition**, not the bytes of a JPEG, PNG, or other file the array may once have come from.
The PNG media type and a stable position/name are supplied to `moodboard.ingest`.

The CLI has a stronger, narrow path-aware seam. It rereads each `LoadedImage.path` immediately
before ingest, verifies that the SHA-256 still equals the source digest already entering
`board_hash`, and then submits those exact bytes with their original MIME type and name. A file
changed between load and ingest fails before any operation is submitted. Thus CLI reference
locations identify the same source bytes whose SHA-256 defines the board, while generic encoder
locations are explicitly labelled canonical renditions. Lattice's own preprocessing remains
part of the returned descriptor identity in either case; a server-side preprocessing change
therefore changes the model identity and invalidates comparison with an older board.
The v1 Qwen3.5 contract is explicitly
`moodboard-qwen35-srgb-pad32-max448-v1`: its 16-pixel patch and 2-patch spatial merge require
32-pixel alignment. Padding to 28 is invalid for this model geometry and must fail descriptor
validation rather than reappear as an interchangeable implementation detail.
The v1 pack admits PNG, JPEG, and WebP source bytes. Khive mode rejects another source MIME
before submitting its batch; the offline classical path retains its broader decoder.

Khive CLI diagnostics and report thumbnails use a separate defined decoded view composited
onto the descriptor's `[128,128,128]` matte with the pack's exact integer rounding. The source
bytes still go unchanged to ingest. This prevents transparent hidden RGB from being embedded as
gray-matted pixels while palette/tone/composition explanations and thumbnails show different
pixels. The default classical loader keeps its prior RGB conversion semantics and revision.

### The adapter is application-specific and fail-closed

This repository adds a small application adapter, not a general Khive SDK. It invokes
`kkernel exec` non-interactively with all of the following pinned on every call:

- an explicit actor and the identical `--expect-actor` value;
- an explicit CLI namespace for execution attribution;
- that same exact namespace in every `moodboard.model`, `moodboard.ingest`, and
  `moodboard.search` argument object, where the pack selects durable storage and retrieval;
- `--serial`, so one process executes its bounded ingest group in physical input order;
- `--strict`; and
- temporary `--ops-file` and `--save-file` paths.

The ops file is JSONL, so image bytes never enter argv. The result file is parsed one line per
submitted operation and in submission order. The adapter verifies the command's manifest,
row count, checksum, tool name, per-row success flag, and result shape before returning any
value. Non-zero exit, partial output, malformed JSON, a failed row, or a manifest disagreement
fails the whole local encoder call without returning a partial matrix or partial `last_assets`.
Already committed Khive operations remain durable; the process protocol is fail-closed, not a
cross-process transaction. Temporary files are private to each invocation and are removed on exit.

The operation-level namespace is deliberate rather than redundant. `kkernel --namespace`
controls execution attribution and gate policy; it does not implicitly select the namespace used
by pack storage APIs. Omitting `args.namespace` therefore persisted and searched the pack's local
default while the command line appeared to name another namespace. Adapter v2 injects one
authoritative configured value into both layers and rejects a low-level conflicting override.

Ops JSONL is streamed one operation at a time instead of first concatenating the whole base64
batch in another Python string. Before byte deduplication, one logical encoder call is capped at
64 total asset occurrences and 32 MiB of decoded source/rendition bytes. Admission happens while
inputs are produced: source files are read to at most the remaining budget plus one byte, and
canonical PNG size is computed from array geometry before encoding. This is intentionally below
the pack's per-object ceiling because current in-process `kkernel` parsing retains and clones batch
JSON.
The adapter fails before submission when the aggregate budget is exceeded. It also computes every
ContentRef and performs complete-call byte deduplication before the first process starts, then
submits the ordered unique operations in consecutive groups of at most eight. `kkernel --serial`
scopes one bounded request-read deadline to the complete ops batch; limiting unique ingests per
process prevents a valid larger logical call from exhausting that shared deadline. The fixed bound
does not reset or weaken the complete-call occurrence or byte budgets.

Each successful group is validated completely before the next process starts. A descriptor drift,
row/content mismatch, malformed response, or process failure stops later groups, clears local
`last_assets`, and exposes no matrix. Khive operations commit independently, so a successful group
or a successful prefix inside the failing group can remain as visual assets, blobs, and vector rows.
Retrying the same namespace and bytes reuses the namespace-plus-ContentRef visual-asset identity and
reports `created=false`, but inference and indexing run again. A caller that requires a clean
state after failure must use a fresh isolated state path; the adapter does not attempt
unsafe compensating deletion. The extra model cold loads are the accepted cost of retaining Khive's
bounded cancellation policy instead of raising or disabling its deadline.

The Khive CLI loader enforces the same occurrence/source-byte bounds before Pillow decode,
rejects either source side above the pack's 8192-pixel ceiling, and caps cumulative retained
matte-composited RGB visual arrays at 256 MiB. This bounds compressed images whose decoded
pixels are much larger than their files. The classical loader remains unchanged.

Every ingest row is the same verb, so a tool-name check alone cannot detect successful rows
returned in the wrong order. The adapter computes Khive BlobStore v1's BLAKE3-256 `content_ref`
over each submitted byte payload and requires the corresponding result to match. This binds
the asset id and embedding to their input row. A future BlobStore content-identity algorithm
is therefore a versioned cross-repository contract change, not an opaque substitution beneath
this v1 adapter.

Before dispatch, the adapter deduplicates byte-identical inputs by that same ContentRef and
submits only the first occurrence across the complete logical call. Without this step, duplicate
rows can waste a second full inference or cross an eight-operation process boundary with different
identity. The validated asset and vector fan back to every original position in order. The first
occurrence's name/caption wins, and later occurrence metadata reports `created=false` because it did
not cause another publication.

`ClassicalEncoder` remains the default for both `build` and `rank`. Khive/Lattice is selected
explicitly and its executable, optional config path, actor, and namespace are explicit command options. An ordinary
test run needs neither a model checkpoint nor a running Khive service.

The application exposes retrieval as `KhiveClient.search(asset_id, top_k?)` and
`moodboard retrieve ASSET_ID`. Before the first query the client discovers the descriptor with
`moodboard.model()`, and the search response must repeat that exact canonical identity. Request
ids are bare canonical UUIDs and `top_k` is absent for the pack default or a plain integer from
1 through 100. The response and each hit have exact closed key sets. The client rejects a query-id
mismatch, self hit, duplicate or noncanonical hit id, malformed content reference, non-finite or
out-of-range cosine, non-contiguous rank, score-order inversion, invalid name, too many hits, or
descriptor drift. The CLI prints rank, raw cosine, asset id, content reference, and name; the name
is a required non-empty bounded UTF-8 string and is rendered with JSON escaping so control
characters cannot forge terminal rows. The CLI never labels that retrieval value as coherence,
aesthetic quality, style fit, or a conformal score.

Khive's entity identity contract resolves a canonical asset UUID globally; namespace is
attribution and candidate scope, not entity isolation. `moodboard.search` therefore resolves the
query asset globally and restricts vector candidates to `args.namespace`. A known query asset
searched from a foreign namespace returns a valid result with an empty `hits` array rather than a
missing-query error. Authorization remains a separate gate-policy concern.

### Asset locations extend `brand.mb` without changing board identity

`BrandBoard` gains an optional, ordered location entry for every reference: Khive `asset_id`,
BlobStore `content_ref`, and a closed byte provenance label of `source-bytes` or
`canonical-png-rendition`. The label prevents a public array caller's canonical PNG locator
from being mistaken for the source-file SHA-256 identity beside it. Verified boards are
format version 3 whether or not they have Khive locations, because version 3 also binds the
reference embedding matrix under ADR-0005's corrected board hash. When a location catalogue is
present, it contains exactly one closed, validated object per reference in the same order.
Formats 1 and 2 are legacy-unverified and require the explicit migration read described in
ADR-0005.

Locations are deliberately excluded from score-bearing `board_hash`. ADR-0005 already binds the
reference file bytes, their exact embedding rows, model identity, and every fit parameter
capable of moving a score. Format 3 separately authenticates the immutable location catalogue
with SHA-256 over sorted `(source_sha256, content_ref, byte_identity)` tuples. `asset_id` is
excluded from that catalogue digest because an entity may be republished without changing its
bytes; `content_ref` and byte provenance are not mutable locators and cannot change unnoticed.
A future hydration path must accept bytes only after both BLAKE3 equals `content_ref` and
SHA-256 equals the bound source hash. This Python artifact does not hydrate, so it never treats
an unverified locator as source media.

`rank` verifies that an explicitly selected encoder matches the board's model identity. A
Khive-built board must therefore be ranked with Khive selected; the classical default fails
clearly rather than silently mixing representations. Candidate locations are exposed by the
encoder for callers that want to record interactions, but they do not alter the existing
report schema in this decision.

## Alternatives considered

**Store base64 or local paths in `brand.mb`.** Rejected. Base64 duplicates BlobStore and makes
the board artifact scale with source media; a local path is neither portable nor a content
identity.

**Call Lattice directly from Python.** Rejected for this integration. It bypasses the defined
model/preprocessing identity and leaves Khive retrieval with no canonical asset or vector.

**Raise or disable Khive's request-read deadline for a large serial ingest.** Rejected. The
deadline is a bounded cancellation guarantee shared by the complete ops batch, not an inference
throughput knob. Eight-operation process groups retain that guard and make the durable boundary
explicit; removing it would trade a deterministic recovery point for an unbounded local request.

**Replace conformal scoring with nearest-neighbour retrieval.** Rejected. Retrieval similarity
has no conformal meaning, interval, effective-sample-size correction, or abstention semantics.
It is useful for discovery and candidate generation, not a calibrated score.

**Train a LoRA adapter as part of ingestion.** Deferred. Updating representation geometry
requires a new model identity, re-embedding, re-indexing, and fresh calibration. Pairwise
interaction learning can begin with a separately identified head over frozen features without
changing the score in this record.

**Keep the location-only version 2 approach and write old format for classical boards.**
Rejected after discovering that `embeddings.npy` was not bound to the old board id. Embedding
integrity is score-bearing for every encoder, so format 3 applies to classical and Khive boards;
only the separately authenticated location catalogue remains optional.

## Consequences

Khive unavailability is now an explicit failure mode only for callers that opt in. Ingest is a
durable external side effect even if a later board fit fails; content addressing and pack-side
idempotence make a retry converge on the same asset instead of duplicating it. A CLI
`content_ref` names the verified source bytes. A programmatic array call names its documented
canonical PNG rendition; the API does not pretend it can reconstruct source bytes it never saw.
Visual retrieval is now reachable without raw `kkernel` calls, but it remains a discovery/candidate
generation surface. It does not change the board hash, scoring math, report schema, or abstention.

The server's descriptor object becomes part of the cross-repository contract. Any field that
can change an embedding must participate in that immutable identity, including checkpoint,
pooling, prompt, preprocessing revision, and dimension. Tests use a fake `kkernel` executable
to exercise the complete file protocol and corrupt-output cases; real-model quality and
retrieval measurements belong in the registered evaluation pipeline before this record can
support a quality claim.

## Cross-reference

The Khive side of this decision, the pack invoked by `kkernel exec` and referred to above as
the Moodboard pack, is implemented in a separate repository: github.com/ohdearquant/khive. Its
asset-ownership half (BlobStore storage, the Moodboard pack's `model`, `ingest`, and `search`
verbs, and the Lattice descriptor identity this record binds against) lives there as the
`khive-pack-moodboard` pack, not in this repository.
