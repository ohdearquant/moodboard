# Roadmap

Where this project is going, in order. Direction is stated here; claims are not. Every item
below lands through its own decision record and its own artifacts, and an item on this list is
an intention, not a promise of a date. When an item ships, the entry is replaced by a pointer to
what shipped.

The destination is the product category the decision records call an **executable aesthetic
judge**: reference images make a visual target operational — context, board-relative evidence,
explicit constraints, and bounded preference learning — with every number traceable to how it
was measured.

## Now: close the contract layer (ADR-0012 through ADR-0016)

The five records added to `docs/adr/` in August 2026 (each still `Status: Proposed`, open for
challenge under the ADR index's rules) define the typed judgment vocabulary,
the Studio/viewer trust boundary, the frozen intent packet and provider boundary, the human
comparison lifecycle, and locality verification. Each carries numbered acceptance conditions;
the current work is the implementation ladder that satisfies them:

1. canonical identity primitives (domain-separated RFC 8785 digests, golden vectors);
2. the closed judgment contract and its six-kind discriminated union;
3. the frozen intent packet and verification policy;
4. provider evidence artifacts: attempt state machine, durable journal, canonical timestamps,
   normalized requests, response evidence, media admission;
5. locality: evidence contracts, the exact outside-mask verifier, the deterministic compositor;
6. the provider adapter itself, capability-driven, one model in v1.

Report schemas v1.0 and v1.1 stay frozen throughout; none of this widens them.

## Next

- **Representation acceptance (ADR-0003).** The learned visual representation remains an
  experimental opt-in until the named baselines, style/content counterfactual tests, and
  calibration gates in that record pass with committed artifacts. This is the load-bearing
  open question of the project and it is answered by measurement, not by adoption.
- **Blind preference serving (ADR-0015).** The dedicated serve path with both exposure fields
  false, the enrolled-principal scope, and the candidate bridge — the pieces that make explicit
  human comparisons admissible as training evidence.
- **Studio (ADR-0013).** The connected work surface over a loopback backend, kept strictly
  apart from the immutable viewer, plus the one-way `moodboard.export.v1` envelope and its
  viewer-side decoder.

## Later, each behind its own future record

- a multi-model arena and accepted-outputs-per-dollar reporting;
- painted (non-rectangular) masks and any perceptual locality diagnostic, with a complete
  registered operator profile;
- list ranking or tournament aggregation over pairwise preference;
- a hosted deployment, which is a separate authentication, tenancy, and retention decision.

## Not planned

Aesthetic quality scoring ("is this a good photograph") stays out of scope; coherence with a
reference set is a different measurement and mixing them would damage both. A design-application
SDK dependency stays out of the core build. A combined taste score stays nonexistent unless a
record names its training evidence, error costs, and evaluation protocol.
