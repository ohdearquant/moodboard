# ADR-0001: Two artifacts, a Python engine and a TypeScript viewer, joined by a file

- **Status:** Proposed
- **Date:** 2026-08-07
- **Measurable claim:** none. This record decides a boundary, not a behaviour, so no dataset
  row is owed. See `docs/adr/README.md` for why that sentence is written down instead of
  left out.

## Context

The work splits cleanly into two halves that want different tools.

Computing a score means loading vision models, fitting a covariance estimator on a small
sample, and running colour-space conversions. Every mature library for that is in Python:
`torch`, `open_clip`, `scikit-learn`, `numpy`, `Pillow`, `scikit-image`. Writing that half
anywhere else means either reimplementing estimators or shipping a model runtime that is
younger than the models.

Presenting a score is a different job with a different audience. The reader is a designer or
an art director looking at their own work, and the thing that makes a quantified judgment
acceptable to that reader is not the number. It is being able to see the three reference
images the score is closest to, hover a candidate and watch where it sits in the spread, and
notice immediately that two candidates are tied. That is interface work, and the tools for
it are in the browser.

Building both halves in one language means one of the halves is built in the wrong one.

## Decision

Two artifacts with one boundary between them.

**`moodboard`**, a Python package with a command line interface and an importable library.
It owns everything that computes: embedding references, fitting the reference distribution,
calibration, scoring, per-axis decomposition, and writing the report. It renders nothing.

**`moodboard-view`**, a TypeScript application built as static assets. It owns everything
that presents. It reads a report and displays it. It computes nothing, and in particular it
never recomputes a score, a ranking, or an interval. If a number is on screen it came out of
the report file.

The boundary between them is the JSON report defined in ADR-0002. It is a file. The viewer
can be handed a report from any source and the engine can be run with no viewer present.

`moodboard report --html out.html` produces one self-contained HTML file by inlining the
report JSON into the built viewer bundle. No server, no network access at view time, and
the file can be attached to a message and opened by someone who has never installed
anything.

**The report therefore carries the reference images, not just their identifiers.** The
interaction this record names as the reason for a browser viewer — seeing the three reference
images a score is closest to — is unimplementable from a report that carries only IDs, on a
boundary that forbids network access at view time. ADR-0002's schema grows a
`references[]` catalogue with a content hash, MIME type, pixel dimensions and an inline
thumbnail for exactly this reason. A viewer cannot show what the report does not carry, so
what the viewer must show is what the report must carry, and that direction of the
implication was missing.

## Alternatives considered

**One Python artifact, with server-rendered HTML from a template engine.** Rejected. It is
faster to a first result and it caps the ceiling immediately. The parts of the report that
make the number trustworthy are the interactive parts, and a static template can show a
table of numbers but not a spread you can brush or an exemplar you can hover. Since the
presentation quality is a primary goal rather than a nicety here, taking the cheaper path
first would mean rewriting the half that matters most.

**One TypeScript artifact, running the models in the browser through ONNX Runtime Web or
`transformers.js`.** Rejected for the first version, and worth revisiting later. It would
make a zero-install web demo possible, which is genuinely attractive. It also requires
exporting each candidate representation to ONNX, reimplementing the shrinkage covariance
estimator and the calibration, and accepting whatever the browser runtime supports on the
day. That is a second project, and it is a second project that gets easier, not harder, if
the boundary is already a file: swapping the engine for a WebAssembly one leaves the viewer
untouched.

**A service with an HTTP API between the halves.** Rejected. It adds deployment, versioning
and availability problems in exchange for nothing the file boundary does not already give,
and it makes the offline single-file report impossible.

## Consequences

The report schema becomes the real public interface of the project, more than the Python API
is, so it needs versioning and a compatibility policy. That is ADR-0002 and it is why that
record exists early rather than after the engine is written.

Two toolchains means two continuous integration jobs and two release paths. Accepted.

Either half can be replaced without touching the other, which is the point. A different
engine, a different viewer, or a third party writing their own consumer of the report are
all the same shape of change.

The viewer cannot show anything the report does not carry. That constraint is a feature: it
forces every quantity the interface wants to display to be an explicit, named, versioned
field rather than a calculation that quietly diverges from the engine's.
