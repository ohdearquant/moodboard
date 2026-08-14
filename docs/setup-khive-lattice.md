# Setting up the Khive + Lattice backend

Moodboard's default engine is local: the `classical` encoder needs nothing beyond `uv sync`. This
guide is for the opt-in `khive-lattice` encoder, which stores exact visual assets in a durable
BlobStore and obtains a frozen Lattice visual descriptor through Khive. It assumes no prior
familiarity with Khive.

## When you actually need this

Only three things touch Khive:

- `moodboard build --encoder khive-lattice`
- `moodboard rank --encoder khive-lattice` (and, because it requires that same flag,
  `--preference-features-output`, which opts a rank run into publishing a preference-learning
  scope and writing a feature artifact)
- `moodboard retrieve`, which always talks to Khive: it has no `--encoder` flag because it only
  returns Khive's own exact-cosine neighbours for an already-ingested asset

`moodboard build` and `moodboard rank` default to `--encoder classical` and stay fully offline
unless you pass `--encoder khive-lattice` explicitly. `moodboard report` never touches Khive.

## 1. Obtain and build a kkernel with the Moodboard pack

The backend is a separate executable, `kkernel`, built from the
[`ohdearquant/khive`](https://github.com/ohdearquant/khive) repository. Follow that repository's
own build instructions to produce a `kkernel` binary. For Moodboard, the build must include
`khive-pack-moodboard`, the pack that implements the `moodboard.model`, `moodboard.ingest`,
`moodboard.search`, `moodboard.serve`, `moodboard.judge`, `moodboard.train_preference`, and
`moodboard.preference` tools, plus the generic `create` tool Moodboard uses to publish a
preference-learning scope.

Moodboard's client never puts operation payloads on the command line. Every call to `kkernel`
writes a JSON-lines file of ordered operations and asks `kkernel exec` to read it and write its
results back through a second file:

```bash
kkernel exec \
  --ops-file ops.jsonl \
  --save-file results.jsonl \
  --namespace local \
  --actor lambda:moodboard \
  --expect-actor lambda:moodboard \
  --presentation verbose \
  --output-format json \
  --serial \
  --strict
```

`--serial` makes `kkernel` execute the submitted operations physically in the order they were
written, so a batch of independent Moodboard calls cannot be reordered or contend on shared pack
state. `--strict` makes a failed operation turn into a nonzero exit status instead of a silently
partial result. Moodboard's client re-verifies both of these on every call: it checks that the
save-file's manifest reports one row per submitted operation, that the manifest's checksum matches
the bytes actually read back, and that each result row names the same tool it was submitted with,
in order (`moodboard/khive.py:1494-1691`). A `kkernel` that is not a real build, or that does not
honour this contract, is rejected before its output is trusted.

## 2. Configure `khive.toml`

Point Moodboard's `--khive-config` flag at a `khive.toml` that enables the pack and a durable
blob store:

```toml
[runtime]
packs = ["khive-pack-moodboard"]  # loads the Moodboard tool surface into this kkernel process

[storage.blob]
backend = "local"                 # or another BlobStore backend your kkernel build supports
path = "/absolute/path/to/blob-store"  # durable location for the exact visual bytes Moodboard ingests
```

If you would rather not maintain a config file, `kkernel` also accepts the pack list through the
`KHIVE_PACKS` environment variable instead of `[runtime] packs`. When `--khive-config` is omitted,
Moodboard passes nothing to `kkernel`, and Khive's own `KHIVE_CONFIG` environment variable and
discovery fallback apply instead (`moodboard/cli.py:1767-1775`).

## 3. Point the pack at a checkpoint

Before `moodboard.model` can return a descriptor, `khive-pack-moodboard` needs a local Qwen3.5
visual checkpoint. It is resolved from three environment variables read by the pack itself:

- `KHIVE_MOODBOARD_MODEL_DIR`: the local directory holding the checkpoint files.
- `KHIVE_MOODBOARD_MODEL_REVISION`: the pinned revision string the pack must report back in the
  descriptor's `model_revision` field.
- `KHIVE_MOODBOARD_CHECKPOINT_SHA256` (optional): a SHA-256 attestation of the checkpoint files.
  When set, the pack verifies the checkpoint on disk against this value before serving any
  descriptor, so a checkpoint that has been swapped or corrupted fails before it can affect a
  board.

Moodboard's own client does not read any of these three variables. It only calls the
`moodboard.model` tool once, through `KhiveClient.model()`, and treats whatever descriptor comes
back as the pin for the rest of that client's lifetime. That descriptor is parsed and checked
field by field (schema version, model name, checkpoint SHA-256 shape, preprocessing revision,
prompt hash, pooling, dimensions, and normalization) before its `fingerprint` is recomputed from
the canonical identity and compared against the value the pack sent
(`moodboard/encoders.py:335-434`). If a later call in the same client session returns a
descriptor whose canonical form differs at all, `KhiveClient.model()` raises rather than silently
switching vector spaces (`moodboard/khive.py:900-912`).

## 4. Actor and namespace flags

Every Khive-backed command accepts the same four flags:

| Flag | Default | What it does |
| --- | --- | --- |
| `--khive-executable` | `kkernel` | the executable Moodboard invokes for this operation |
| `--khive-actor` | `lambda:moodboard` | the actor Moodboard submits and also pins with `kkernel exec --expect-actor`, so a misconfigured `kkernel` cannot silently attribute the call to someone else |
| `--khive-namespace` | `local` | the storage namespace for visual assets and retrieval; two runs with different namespaces cannot see each other's assets |
| `--khive-config` | none | an explicit `khive.toml`; when omitted, Khive's own `KHIVE_CONFIG`/discovery fallback applies |

(`moodboard/cli.py:209-211`, `moodboard/cli.py:1751-1775`)

## 5. Request bounds

These limits are enforced in Python before anything reaches `kkernel`, and read directly from the
source rather than restated from memory:

- At most 64 image occurrences in one logical `build`, `rank`, or embedding call
  (`KHIVE_REQUEST_MAX_IMAGES`, `moodboard/encoders.py:240`).
- At most 32 MiB of decoded image bytes across that same call
  (`KHIVE_REQUEST_MAX_BYTES`, `moodboard/encoders.py:242`).
- Each source image is capped at 8192 pixels on its longer side before Moodboard converts and
  mattes it for Khive (`KHIVE_SOURCE_MAX_SIDE`, `moodboard/cli.py:189`).
- The retained RGB matte for one `build`/`rank` invocation is capped at 256 MiB across all loaded
  images (`KHIVE_RETAINED_VISUAL_MAX_BYTES`, `moodboard/cli.py:191`).
- `moodboard retrieve --top-k` accepts 1 through 100 non-self neighbours and defaults to 20
  (`_MAX_SEARCH_TOP_K`/`_DEFAULT_SEARCH_TOP_K`, `moodboard/khive.py:61-62`).
- A search result name is at most 512 UTF-8 bytes, and a `content_ref` is always 64 lowercase hex
  characters (`moodboard/khive.py:1465-1476`).
- A preference-serve `policy_revision` string is at most 128 UTF-8 bytes
  (`moodboard/khive.py:1070-1078`).
- A judgment's `response_ms`, when supplied, is an integer from 0 through 3,600,000
  (`moodboard/khive.py:1207-1210`).
- The Lattice descriptor's own dimensionality is bounded to 1 through 8192
  (`moodboard/encoders.py:392-400`).

## 6. Fail-closed behaviors you will actually hit

This path is deliberately strict, and most first-run failures fall into one of three shapes:

**Descriptor fingerprint mismatch.** `moodboard.model`'s reported `fingerprint` must equal the
SHA-256 of the descriptor's own canonical identity. If a pack build computes it differently, or
the checkpoint changes underneath a running `kkernel` without a matching descriptor update, every
call that touches the descriptor raises a `KhiveProtocolError` naming the mismatch
(`moodboard/encoders.py:414-418`, `moodboard/khive.py:904-912`). The fix is almost always a stale
or mismatched `KHIVE_MOODBOARD_CHECKPOINT_SHA256`/checkpoint pair, not a Moodboard bug.

**Adapter or board revision mismatch.** `moodboard rank` refuses to score a candidate with an
encoder whose `(name, revision)` does not match the one the board was built with. The revision
Moodboard records is the descriptor's fingerprint plus the pinned adapter contract string
`moodboard-khive-adapter-v3` (`KHIVE_ADAPTER_REVISION`, `moodboard/encoders.py:236`,
`moodboard/encoders.py:594`). Rebuilding a board after switching checkpoints, or scoring an old
board with a newer `kkernel` build, raises with a message naming both revisions
(`moodboard/cli.py:1250-1255`). Rebuild the board against the current encoder rather than trying
to reconcile the two.

**Malformed embeddings.** Moodboard validates every `moodboard.ingest` result before it enters a
board: the embedding must have exactly the descriptor's declared dimension, every coordinate must
be a plain finite number, and the vector's L2 norm must be within `1e-5` of 1.0. Any violation
raises a `KhiveProtocolError` naming the offending index rather than admitting an unnormalized or
truncated vector into scoring (`moodboard/encoders.py:896-929`).

Under all three, Moodboard raises before writing any board or report artifact, so a failed run
never leaves a partially-scored one behind.

## 7. Prove a setup actually works

`tests/test_khive_real.py` is the opt-in integration smoke path; the rest of the test suite never
requires Khive or a checkpoint. It is skipped entirely unless you set:

- `MOODBOARD_REAL_KKERNEL`: an absolute path to a freshly built `kkernel` binary. Setting this
  alone runs two tests that only exercise the ordered `--ops-file`/`--save-file` transport
  (a checkpoint-free `whoami` round trip, and confirmation that an unknown verb fails closed)
  and needs no Moodboard pack or checkpoint.
- `MOODBOARD_REAL_KHIVE_CONFIG` (optional): an absolute path to the `khive.toml` the smoke client
  should pass through to `kkernel`.
- `MOODBOARD_REAL_KHIVE_MODEL=1`: opts into the two tests that require a fully configured pack,
  BlobStore, and checkpoint. One confirms `moodboard.model`'s descriptor matches Python's parsed
  contract; the other ingests two assets into a named namespace and confirms retrieval both finds
  a peer inside that namespace and returns nothing for the same query asset from an isolated
  namespace.

```bash
MOODBOARD_REAL_KKERNEL=/absolute/path/to/kkernel \
MOODBOARD_REAL_KHIVE_CONFIG=/absolute/path/to/khive.toml \
MOODBOARD_REAL_KHIVE_MODEL=1 \
uv run pytest tests/test_khive_real.py -v
```

A clean run of this file, with `MOODBOARD_REAL_KHIVE_MODEL=1` set, is what demonstrates a working
Khive + Lattice setup end to end: real transport, a real pinned checkpoint, and real namespace
isolation, rather than the unit-test double the rest of the suite runs against.
