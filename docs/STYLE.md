# Style guide

How code and documents in this repository are written. `CONTRIBUTING.md` says how to build and
test; this file says what the result should look like. Where an older file disagrees with a rule
here, the rule wins for new work, and the old file is brought over when the code it belongs to is
next touched, not in a mass rewrite.

## Comments and docstrings are terse

This is the rule most of the existing code predates, so it is stated first.

- A docstring is one line for most functions: what it returns or does, not how. A module
  docstring is at most one short paragraph.
- A comment exists only where the *why* is not visible in the code. A comment that restates the
  code is deleted on sight.
- Rationale longer than two sentences does not live in the source. It lives in a decision record,
  in `INTERFACES.md`, or in the commit message, and the code cites it: `# See ADR-0014.` The
  source file says what is true; the record says why it was decided.
- Do not delete an existing long comment just because this rule exists. When you touch the code
  it describes, move its substance to the right home and leave the citation behind.

## Python

- Linted by ruff with the configuration in `pyproject.toml`: line length 100, rules
  `E, F, I, UP, B, SIM`, target Python 3.11. There is no separate formatter; ruff and the
  surrounding code decide formatting questions.
- Public functions that form a contract (anything named in `INTERFACES.md`) carry full type
  annotations. Internal helpers may rely on inference.
- Errors fail closed. A validation failure raises with a message naming the field and the rule;
  it never degrades into a warning, a default, or a truncation. If a path genuinely may degrade,
  the degraded state is an explicit value the caller can see, not a silent substitution.
- No stubs, no `TODO` placeholders standing in for logic, no commented-out code.
- Constants that are policy (thresholds, limits, versions) are named and sourced from one place;
  a number that appears twice is a bug waiting to disagree with itself.

## Source arrangement

- `moodboard/` is a flat package: one concern per module, module names that say what they own
  (`conformal.py`, `abstain.py`, `report.py`). New concerns get a new module, not a `utils.py`.
- Signatures shared between modules are decided in `INTERFACES.md` before they are implemented.
  Changing a shared signature changes that document in the same pull request.
- JSON Schemas live in `moodboard/schema/`, one file per schema version, never edited in place
  after release: a field change is a new version.
- `tests/` mirrors the package: `test_<module>.py` for unit tests, `test_<module>_properties.py`
  for property-based tests. Tests are deterministic and offline; real-integration tests are
  opt-in via environment gates and never required by the ordinary suite.
- `viewer/` keeps presentation components free of network, mutation, and provider code. Anything
  under `viewer/src/generated/` or `viewer/dist-static/` is written only by its generator.

## TypeScript (viewer)

- The compiler settings in `viewer/tsconfig.json` are the style authority; `tsc --noEmit` must
  pass. Prefer types derived from the report schemas over hand-written duplicates.
- Components render validated data; parsing, validation, and interpretation happen at the
  boundary, once.

## Documents

- Headings are sentence case. Lines wrap near 100 characters. Plain, complete sentences.
- Every document type with a repeated shape has a template, and new instances start from it:
  decision records use [`docs/adr/TEMPLATE.md`](adr/TEMPLATE.md), releases use
  [`docs/RELEASE.md`](RELEASE.md), pull requests and issues use the forms under `.github/`.
- A document that makes a claim names the artifact that reproduces it. A document that makes no
  measurable claim says so where a reader would expect one.

## Commits, pull requests, issues

- Commit subjects: lowercase, type-prefixed, imperative — `feat:`, `fix:`, `docs:`, `ci:`,
  `test:`, `refactor:`. The body says why, and names any contract or record the change touches.
- One concern per pull request. The pull-request form in `.github/PULL_REQUEST_TEMPLATE.md` is
  the hygiene checklist; a PR stays a draft until its checks are green.
- Issues follow the forms under `.github/ISSUE_TEMPLATE/`: a bug report carries the exact
  command and the observed output; a feature request states the problem before the proposal.
