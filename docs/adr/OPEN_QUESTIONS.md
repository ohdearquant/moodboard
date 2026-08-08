# Open questions deliberately deferred

These are decisions this reconciliation pass identified but did not resolve, because resolving them
would mean designing new mechanism rather than fixing a contradiction in what ADR-0007 through ADR-0010
already say. Each entry names the evidence that would settle it. Deep mode was not requested for this
round, so each is left for a future pass rather than decided here.

## 1. Gamut-headroom subject-set decision

**Status:** unresolved, and it was unresolved before this reconciliation pass — none of ADR-0007,
ADR-0008, ADR-0009, or ADR-0010 mentions gamut or clipping-control subjects. It is recorded here per
the explicit instruction to do so if it remains open.

**What it is.** `common.md`'s inventory of open items states: "The registered axis protocol names 200
seeded PACS images, three magnitudes, and `luminance_shift`, but no gamut-headroom subject set or
separate clipping-control row" (`eval/thresholds.json:123-172`; `DATASETS.md:344-355`). ADR-0003's
`axis_intervention` protocol moves lightness with chroma held fixed (`luminance_shift`) and moves
chroma with lightness held fixed (`recolour`), both round-tripping through sRGB. ADR-0003 already notes
that `recolour` clips at the gamut boundary: "The residue is sRGB gamut clipping on the round trip, it
is two percent of the deliberate intervention, and it does not disqualify the arm"
(`docs/adr/0003-style-representation.md:239-243`). What is missing is a registered subject set (and
protocol) specifically chosen to have low gamut headroom — subjects near the sRGB boundary, where a
`luminance_shift` or `recolour` intervention is more likely to clip and where the palette/tone
separability claim would be hardest to sustain. Without it, the current 200-seeded-image population may
under-represent exactly the case that would falsify the axis-separation claim.

**Why it is not resolved here.** Defining a gamut-headroom subject set requires: (a) a concrete
selection rule (e.g., a maximum distance from the sRGB gamut boundary in a specified color space, or a
minimum/maximum chroma at fixed lightness), (b) a registered sample size and seed, (c) pre-registered
pass/fail bounds in `eval/thresholds.json` under a named key, and (d) a decision about whether it is a
new row in the existing `axis_intervention` protocol or a separate gating/informational measurement.
None of that is decidable from the four new records or the accepted ones — it requires new statistical
design, which is out of scope for a contradiction-reconciliation pass and was explicitly named as
out of scope for a non-deep-mode round.

**Evidence needed to decide it.** A worked selection rule for "low gamut headroom" against the PACS
population (or a documented reason PACS cannot supply such subjects, forcing a new dataset row),
a pilot measurement of clipping rate under the existing `luminance_shift`/`recolour` interventions
restricted to that population, and a proposed threshold with the same "fixed before measurement"
discipline every other row in `eval/thresholds.json` follows. This belongs in a future amendment to
ADR-0003 (the record that owns `axis_intervention`) rather than in any of ADR-0007–0010, none of which
touches the style-axis intervention protocol.

## 2. Two independent fixture generators for the viewer — RESOLVED 2026-08-08

**Status:** decided and written into ADR-0010. This entry is kept rather than deleted so the
reasoning stays findable, and because a question that silently disappears reads as an oversight.

**What it was.** ADR-0007 defined `tests/generate_viewer_fixtures.py`, writing fresh uncommitted
fixtures to `viewer/test-artifacts/fixtures/` on every run, while ADR-0010 separately defined
`scripts/generate_frontend_fixtures.py`, committing a corpus under `viewer/tests/fixtures/` with a
provenance manifest and a `--check`/`--write` drift gate. Two scripts, two directories, two
provenance schemes, near-identical scenario coverage, same engine, same seed `20260808`.

**The decision.** The second of the three options set out below was taken: ADR-0010's committed
corpus is the single source, and ADR-0007 defines no fixture regime at all. There is one generator,
`scripts/generate_frontend_fixtures.py`, and one corpus. Every regression case either uses a
committed scenario as written or derives from one by a single named in-memory mutation that is never
committed. A verification run's working copies, build trees, and screenshots live under the ignored
`viewer/test-artifacts/` tree and are outputs rather than fixtures. Written into ADR-0010's
"Verification" section under **One generator, one corpus**.

**What moved with it.** ADR-0007's 43-row regression matrix and its four-part verification contract
now live in ADR-0010, which owns the corpus and carries the `frontend-verification` dataset row.
ADR-0007 keeps the invariants it decides and points at ADR-0010 for how they are checked, with a
stated rule that an invariant and its row change in the same commit.

**Why it did not need the evidence this entry originally asked for.** The entry said the decision
needed a comparison of the two generators' scenario coverage once both were implemented, and that
until then there was no code to compare. That framing was wrong in a way worth recording: it treated
the question as empirical when it is structural. Whatever the coverage comparison had returned, two
generators at one seed agree only until someone edits one of them, and the first divergence appears
as a frontend test failure whose cause lives in a file the failing test does not name. Waiting for
implementation would have meant writing both scripts before learning that one had to be deleted.

## 3. Mapping `frontend-verification`'s artifacts onto ADR-0009's evidence envelope

**Status:** ADR-0010 registers `frontend-verification` as an ADR-0009 measurement and names its
canonical entry point, but the exact fit is incomplete.

**What it is.** ADR-0009's `run.json` envelope records "the exact Python version, operating system,
architecture and evaluation device" plus dependency-lock and dataset digests
(`docs/adr/0009-measurement-and-evaluation-contract.md:195-204`), but does not name a Node version, npm
lockfile digest, or pinned-browser revision field — all of which ADR-0007's
`viewer/verification-toolchain.json` treats as load-bearing for reproducibility (Node `24.19.0`, npm
`11.17.0`, Chromium/Firefox/WebKit at exact revisions). ADR-0009's `raw/` directory is defined as
carrying "the measurement-specific observations sufficient to recompute every value in `summary.json`"
(`0009:234-238`), but does not say whether Playwright screenshots, diff PNGs, and browser traces count
as `raw/` evidence or as the separately-named "regenerated and never serve as evidence" category
(`0009:261-265`) that dataset archives and model caches fall into. Screenshots are exactly the primary
evidence for ADR-0010's zero-differing-pixel gate, so treating them as non-evidence would be wrong, but
committing every baseline screenshot into an append-only `results/` tree has a real storage-growth cost
ADR-0009 does not weigh for this case.

**Why it is not resolved here.** Extending ADR-0009's environment-fingerprint schema and clarifying its
`raw/`-versus-regenerated boundary for a non-statistical, browser-rendering measurement is a scope
decision for ADR-0009 (or a further superseding record), not something inferable from the current text
of either record without inventing the answer.

**Evidence needed to decide it.** A concrete `run.json` shape for `frontend-verification` showing which
fields it adds beyond ADR-0009's generic envelope, and a storage-cost estimate for committing visual
baselines under `results/frontend-verification/<run-id>/raw/` versus keeping them in ADR-0010's
existing `viewer/test-results/` location with only a content hash recorded in the `results/` bundle.
