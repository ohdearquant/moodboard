# ADR-0006: The Adobe panel is the primary surface, and the engine stays out of process

- **Status:** Superseded by [0007](0007-standalone.md)
- **Date:** 2026-08-07
- **Measurable claim:** partly. The surface choice is a product decision and carries no
  dataset. The latency budget it implies is measurable and is stated below; it is measured
  against the engine, not against a dataset, so it has no row in `DATASETS.md`.
- **Amends:** [ADR-0001](0001-engine-and-viewer-split.md), which named two artifacts and
  should have named three.

## Context

ADR-0001 split the system into an engine that computes and a viewer that presents, joined by
a JSON report. That split is right and this record does not disturb it. What ADR-0001 got
wrong is the set of consumers: it assumed a command line tool and a standalone HTML view, and
the README went further and put panels for design applications down as "a later question".

That is backwards. The work being scored is *already open in Photoshop, Illustrator or
InDesign* when the question is asked. A designer with two hundred candidates does not export
them, run a command line tool, and open an HTML file. Every step between the question and the
answer is a step where the tool loses. A separate application is not a smaller version of a
panel, it is a different product with a worse insertion point.

So the panel is the primary surface. The command line tool is the engine's interface and stays
exactly as ADR-0001 defines it, because a panel that cannot be scripted is not testable and
because the validation work in ADR-0003 through ADR-0005 runs headless.

## The constraint that makes this an architecture decision

An Adobe panel runs JavaScript inside a sandboxed host. The engine is Python, and it loads
model weights measured in hundreds of megabytes. **The engine cannot run inside the panel**,
so ADR-0001's file boundary has to survive a process boundary as well, and the panel needs a
way to reach an engine that is somewhere else.

Three shapes, with genuinely different costs:

**A. Local companion process.** The engine runs on the user's machine and the panel talks to
it over localhost. Assets never leave the machine, which matters because the assets in
question are routinely unreleased work under embargo. Cost: the user installs two things, and
the panel has to handle the engine being absent, stale, or busy.

**B. Bundled runtime.** Ship the engine inside the plugin package. Best install experience by
a distance. Cost: package size, per-platform builds, and whatever the host's plugin
distribution actually permits, which is the part this record cannot answer from here.

**C. Hosted service.** The panel calls a server. Cheapest to build and the only one that puts
unreleased client work on someone else's machine. For this use case that is close to
disqualifying on its own, and it also breaks the offline claim ADR-0001 makes for the HTML
report.

## Decision

**The panel is the primary surface and the report contract is how it talks to the engine.**
The panel is a third consumer of the ADR-0002 report, alongside the CLI and the HTML viewer,
and it is held to the same rule as the viewer: it renders numbers, it never computes them. A
score shown in the panel and a score in a report file are the same number by construction, or
the panel is wrong.

**The engine stays out of process, and shape A is the default.** Assets under embargo do not
leave the machine. Shape B is the preferred end state if the host's packaging permits it,
which is a question of fact rather than of preference. Shape C is rejected for the primary
path and is not kept as a fallback, because a fallback that quietly uploads unreleased work is
worse than an error message.

**The panel's own layer is thin on purpose:** collect the selection, hand the engine a list of
paths, receive a report, render it. Every piece of judgment stays in the engine where it is
testable headlessly.

**What this record does NOT decide, because deciding it from here would be a guess.** Which
Adobe extensibility platform to build against, what that platform permits by way of local
network access, process spawning and package size, and therefore whether shape B is available
at all. Those are facts about someone else's SDK, they change between versions, and the
honest thing to write down is that they need reading rather than a confident sentence that
happens to be current. Settling them is the acceptance criterion below.

## Acceptance criterion

This record stays `Proposed` until a written survey of the target platform's extensibility
model exists in the repository, citing the platform's own current documentation, and answers:

1. Which platform is current and supported for the target applications, and which is legacy.
2. Whether a panel may reach a process on localhost, and under what permission declaration.
3. Whether a panel may spawn or bundle a native runtime, and what the package limits are.
4. What the panel can read about the current document: can it hand the engine file paths, or
   must it export pixels, and what does that cost for two hundred candidates.
5. The latency floor that implies. The budget is **3 seconds per frame** at the panel, and it
   is a budget rather than a measurement until the export path in question 4 is known, since
   an export step could dominate everything the engine does.

Question 4 is the one that can invalidate the design rather than adjust it. If the panel
cannot hand over paths and must serialise pixels for every candidate, the cost is paid per
candidate on every run, and the ranking use case, which is the main one, is where it lands
hardest.

## Alternatives considered

**Ship the CLI and the HTML report first, add a panel later.** This is what the README said
and it is what this record reverses. The objection is not that it is slow, it is that it
optimises the wrong thing: the CLI and the report are how the engine is *tested*, not how the
work is done, and treating a test harness as the product would have let the surface question
go unanswered until the engine was finished and its assumptions were expensive to revisit.
The export-path question above is exactly such an assumption.

**Build the panel first and skip the CLI.** Rejected. Every acceptance criterion in ADR-0003,
0004 and 0005 runs headless over thousands of images, and none of that is expressible through
a panel. Removing the CLI would remove the project's ability to validate itself.

**A desktop application of our own.** Rejected. It has the panel's build cost and the command
line tool's insertion point, which is the worst of both.

## Consequences

ADR-0001's title and framing are now narrower than the system. It is `Proposed`, so this is an
amendment rather than a supersession, but the engine-and-viewer pair should be read as
engine-and-consumers, with the panel first among them.

ADR-0002's report contract is now load-bearing for a consumer that was not imagined when it
was written. It should be re-read with the panel in mind before it is accepted, specifically
for whether a panel rendering a subset of a report can do so without recomputing anything.

The abstention states in ADR-0004 need a panel rendering. A refusal that a CLI prints as a
paragraph has to survive in a space a few hundred pixels wide, and if it does not survive
there it will be dropped by whoever builds the panel, which converts a safety property into a
formatting casualty.
