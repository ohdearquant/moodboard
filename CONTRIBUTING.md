# Contributing

This project has two parts that build and test independently: a Python engine under
`moodboard/` and a TypeScript/React viewer under `viewer/`. Both are covered below, along with
what the continuous integration workflow actually runs and where the versioned contracts
between the two parts live.

## Python engine: the offline pytest lane

```bash
uv sync --locked
uv run pytest -q -rA
```

This is the same command the continuous integration workflow runs. It needs no network access
and no downloaded model weights: the default `ClassicalEncoder` computes its representation
from the axes defined in `moodboard/axes.py`, so the whole suite is exercisable on a bare
checkout.

Two things are deliberately excluded from this lane:

- **Real Khive integration.** Every test in `tests/test_khive_real.py` is skipped unless the
  `MOODBOARD_REAL_KKERNEL` environment variable is set, and two of them are skipped again
  unless `MOODBOARD_REAL_KHIVE_MODEL=1` is also set. See "Real-integration environment gates"
  below.
- **One cache-dependent Firefly test.** `test_firefly_projector_fails_closed_when_exact_source_bytes_are_unavailable`
  in `tests/test_firefly_viewer.py` is skipped unless the local measured-run caches under
  `.cache/showcase-firefly-v1` and `.cache/showcase-firefly-khive-v1/evidence` are present.
  A fresh checkout does not have these directories, so this test does not run there.

Lint with `uv run ruff check .` (this is a separate job in continuous integration; a lint
failure does not hide a test failure or vice versa).

## Viewer lane

```bash
npm --prefix viewer ci
npm --prefix viewer run test        # vitest run
npm --prefix viewer run typecheck   # tsc --noEmit
npm --prefix viewer run build
```

`npm run build` runs a chain of checks, in order: `validators:check`, `firefly:check`,
`preference-replay:check`, `typecheck`, the Vite build, and finally `package` (which runs
`scripts/package-artifacts.mjs`). Each step in that chain does real, specific checking:

- **`scripts/generate-validators.mjs --check`** recomputes the AJV-generated validator module
  from the two committed report schemas (`moodboard/schema/report_v1_0.schema.json` and
  `report_v1_1.schema.json`) and compares it byte-for-byte against the checked-in
  `viewer/src/generated/report-validators.mjs`. If the schemas and the generated file have
  drifted apart, it fails and tells you to run `validators:write`.
- **`scripts/package-artifacts.mjs`** (run as `npm run package`) reads the built
  `dist-static/index.html` and its script and stylesheet bundles, and rejects the build if any
  of them contain a local absolute path, a dynamic `import(`, a `sourceMappingURL` reference, a
  runtime `fetch(` call, `eval`/`new Function`, or a CSS `url()`. It then calls
  `preference-package-gate.mjs` (below), assembles a self-contained standalone HTML template
  with the script and stylesheet inlined as `data:` URIs behind a pinned Content Security
  Policy, checks that template has exactly one payload token and exactly one CSP tag and no
  other non-`data:` asset reference, copies the report schemas and the manifest/toolchain/
  contract files into `dist-static`, and writes a SHA-256 manifest (`artifact-manifest.json`)
  validated against `artifact-manifest.schema.json`. The result is staged into
  `moodboard/viewer_dist/`, which is what the Python `report --html` command packages.
- **`scripts/preference-package-gate.mjs`** shells out to
  `uv run --frozen --project .. --python 3.14.3 python -m moodboard.preference_replay_viewer
  --check <bridge> --require-projected` to confirm the committed preference-replay bridge is a
  valid, projected build input, then reads that bridge JSON directly and checks that its
  `state` is `"projected"` and that its `input` and `evidence` fields are present. It extracts
  three SHA-256 identities from the bridge (the replay fingerprint, the replay file hash, and
  the feature sidecar hash) and fails the build if the compiled application bundle does not
  contain all three, which is how it confirms the shipped bundle is bound to that exact
  preference replay rather than to a stale one.

Continuous integration's own viewer step only runs `npm ci` and `npm run build` and then diffs
the result against the committed `viewer/dist-static` (see below); it does not run the vitest
suite. Run `npm --prefix viewer run test` yourself before pushing, or use
`npm --prefix viewer run test:ci`, which chains `fixture:check`, `validators:check`,
`typecheck`, `test`, and `build` together.

## What continuous integration actually runs

The workflow is `.github/workflows/ci.yml`. It triggers on pull requests and manual dispatch,
not on pushes to `main` (`main` does not currently carry the Python project, so a push trigger
would fail at `uv sync` for reasons unrelated to the change being pushed). It has two jobs:

- **`lint`**: `uv sync --locked`, then `uv run ruff check .`.
- **`test`** (matrix: Python 3.11 and 3.13): `uv sync --locked --python <version>`, then
  `npm --prefix viewer ci` and `npm --prefix viewer run build`, then
  `git diff --exit-code -- viewer/dist-static` to prove the committed static viewer output is
  byte-identical to a fresh build from the locked frontend inputs, then `uv run pytest -q -rA`.

If you change anything under `viewer/` that affects the built output, rebuild it locally with
`npm --prefix viewer run build` and commit the resulting `viewer/dist-static` changes, or the
`git diff --exit-code` step above will fail.

## Real-integration environment gates

Three environment variables opt individual tests into exercising a real `kkernel` process
instead of the fake executable the ordinary suite uses:

- **`MOODBOARD_REAL_KKERNEL`**: an absolute path to a built `kkernel` binary. Setting this
  alone enables `tests/test_khive_real.py::test_real_kkernel_preserves_a_checkpoint_free_whoami_result_through_save_file`
  and `test_real_kkernel_unknown_verb_fails_closed`, which exercise the file-transport
  protocol (`--ops-file`/`--save-file`) without needing a model checkpoint.
- **`MOODBOARD_REAL_KHIVE_CONFIG`**: an optional path to a Khive config file, read by the same
  tests when set.
- **`MOODBOARD_REAL_KHIVE_MODEL=1`**: enables two further tests that require a configured
  Moodboard pack, a durable BlobStore, and a pinned visual checkpoint:
  `test_real_moodboard_model_descriptor_matches_the_python_contract` and
  `test_real_named_namespace_persists_ingests_and_scopes_retrieval`. Set this only when that
  environment is actually available; the ordinary suite and the continuous integration
  workflow never set it.

You do not need any of these to build, rank, report, or run the offline pytest lane with the
default `ClassicalEncoder`. They only matter if you are changing the `KhiveLatticeEncoder`
adapter or the `KhiveClient` protocol itself and want to check it against a real process.
[`docs/demo-preference.md`](docs/demo-preference.md) describes the separate, manual preference
replay workflow that also depends on a real `kkernel`; it does not read these three variables
itself, but it is the other place in the repository where a real `kkernel` process is required.

## Versioned contracts

Three artifacts carry an explicit version and a compatibility rule, so a reader can tell
exactly which contract a given document or bundle was produced under:

- **Report schema.** `#/schema_version` is `"1.0"` or `"1.1"`. The schemas live at
  `moodboard/schema/report_v1_0.schema.json` and `moodboard/schema/report_v1_1.schema.json`.
  Dispatch is by the exact version string, not best-effort parsing. Report v1.1
  ([ADR-0008](docs/adr/0008-report-contract-for-viewer.md)) retains every field defined in
  v1.0 ([ADR-0002](docs/adr/0002-report-contract.md)) and adds required fields the viewer
  needs; a v1.0 report is still valid input to a v1.1 reader, which renders it with explicit
  legacy-compatibility notices instead of inventing values for fields v1.0 never recorded.
- **Preference feature artifact.** Version 2, named by the constant
  `moodboard.preference-feature-artifact.v2` in `moodboard/preference.py`. Changing any
  feature formula requires a new version even if the field names stay the same.
- **Viewer package manifest.** `format_version` is a fixed constant, `1`, declared in
  `viewer/artifact-manifest.schema.json` and written by `viewer/scripts/package-artifacts.mjs`.
  It identifies the manifest shape that lists the schema files, the standalone template, and
  the application bundle staged into `moodboard/viewer_dist/`.
