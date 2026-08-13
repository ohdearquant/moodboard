# Governed, intent-scoped Pixel RAG

`moodboard.pixel_rag` freezes a control-plane artifact. It does not generate an image and it
does not call a model. Its input is the reviewed public-domain acquisition manifest plus a
closed record of Khive retrievals. The compiler proves that every candidate ContentRef belongs
to the manifest, that the Lattice descriptor is the current `lattice-embed` 0.9.0 contract,
and that the scores remain in the exact order returned by the measured search. A measured run
also binds the exact preregistration, intent-freeze, and verification-summary bytes. Those
hash bindings prove which evidence files were used; a dedicated projector is still responsible
for proving that every projected number equals the corresponding frozen evidence field.

## Why one source produces two retrievals

Both plans start from `fruit_apple_garden`, but intent changes what is a useful query:

| Intent | Query | Hard metadata gate | What the visual score ranks |
| --- | --- | --- | --- |
| `local_replace` | a Khive asset derived from an operator-confirmed normalized tree rectangle | `collection == fruit-lemon` | view, silhouette, light, and composition compatibility among the governed lemon references |
| `global_restyle` | the complete immutable source asset | `collection == style-claude-lorrain` | global composition compatibility among the governed Claude Lorrain references |

The gate is explicit intent routing, not a claim that the visual model inferred “lemon” or
“Claude Lorrain” from pixels. Khive owns retrieval and immutable record identity; Lattice owns
the visual embedding and exact cosine score; the governed manifest owns collection, licence,
and source-page metadata.

Each plan keeps these stages separate and in order:

1. retrieval;
2. region selection;
3. deterministic conditioning;
4. external generation;
5. recorded verification;
6. immutable output registration; and
7. rollback by selecting the source ContentRef.

The optional demo outputs were produced by **OpenAI ImageGen through Codex** and are recorded
as `precomputed_external` executions. Moodboard never calls them Firefly outputs and never
claims they are BlobStore-managed until a matching Khive record UUID and BLAKE3 ContentRef are
provided.

## Producing a measured input

Use a persistent Khive state and two explicit namespaces. Ingest the 15 manifest assets into
each namespace (or otherwise make the complete governed corpus visible there), then:

1. record `moodboard.model`'s full descriptor;
2. confirm and save the normalized tree rectangle, create its crop/rendition, ingest it, and
   run `moodboard.search(top_k=100)` in the replacement namespace;
3. run `moodboard.search(top_k=100)` for the source asset in the restyle namespace;
4. copy every returned row, without filtering or reordering, into the corresponding `hits`;
5. optionally add explicit human relevance gains (`0..3`); the frozen demo projector separately
   asserts complete judgment coverage for its claimed evaluation;
6. optionally add verifier measurements, including a threshold and comparison operator, and
   identify the measurement either by a named method revision or by an exact field in the bound
   verification summary; and
7. bind the preregistration, intent freeze, and verification summary as an ordered three-file
   `evidence_bindings` set with their exact SHA-256 digests.

The input must validate against
`moodboard/schema/pixel_rag_measurements_v1.schema.json`. Use
`evidence_status: measured_run` only for real Khive outputs and only with all three frozen
evidence bindings. Tests use
`evidence_status: contract_fixture`; fixture numbers are never promoted automatically.

If relevance judgments are absent, P@3, nDCG@5, MRR, and recall@5 are emitted as
`not_computed` with a reason. If they are present, the artifact preserves both views:

- `raw_diagnostics` records the complete ungated Qwen score order and its four metrics;
- `metrics` recomputes the same four metrics after the declared intent collection gate; and
- `metrics_interpretation` states that the routed values test deterministic routing integrity,
  not learned retrieval quality.

Both views are measured, but neither turns a cosine into a probability. If edit-verification
measurements are absent, verification is `not_run`. Merely supplying a generated image never
invents either kind of metric.

The frozen demo evidence records that a human confirmed the crop, but it does not record an
actor or timestamp. That case uses `evidence_bound_human_confirmation` with the exact
preregistration hash and JSON pointer. It must not synthesize an operator identity or time.
Likewise, a verifier value already frozen without a method revision uses `evidence_field`; it
must not synthesize a revision label.

Project the frozen measured demo into a fresh immutable cache directory:

```bash
python eval/adobe_demo_pixel_rag_projection.py \
  --output-dir .cache/adobe-demo-pixel-rag-measured-v2
```

The generic `python -m moodboard.pixel_rag` CLI supports measurements without historical output
rows. Add `--local-record-id` plus `--local-content-ref` (and the corresponding restyle flags)
only after Khive has registered those exact selected-output bytes. The compiler hashes the file
again and rejects a mismatched expected ContentRef. Use the tracked projector/API when retaining
historical outputs, because the generic CLI deliberately has no historical-output flags.

Failed historical attempts can be retained beside the selected output with
`negative_output_evidence` plus the compiler's `historical_external_outputs` API. Every retained
attempt must resolve to actual bytes, carry its own generator and immutable registration
identity, and fail at least one recorded verifier. For the local demo this is what preserves the
v1 outside-mask SSIM failure beside the selected v3 pass instead of hiding the iteration.

The selected v3 locality result uses the recorded `source_backed_region_overlay` compositor and
passes the preregistered outside-mask SSIM threshold. This means a source-backed deterministic
composite passed that verifier. It does **not** mean every RGB channel outside the rectangle is
byte-identical to the source, so `restore_source_pixels_outside_confirmed_region` is rejected as
an overclaim.

The output validates against `moodboard/schema/pixel_rag_artifact_v1.schema.json`. Its
`artifact_id` hashes the canonical document excluding only that field; the document also binds
the SHA-256 identities of the artifact, measurements, and source-manifest schemas.

## Frozen evidence projection

For a one-off measured demo, keep the deterministic projector in the tracked `eval/` directory
and write only its large measurements/artifact outputs to an ignored cache directory.
The projector should:

1. verify the exact SHA-256 of every frozen input before parsing it;
2. assert equality for the descriptor, raw hit UUID/ContentRef/score order, relevance gains,
   output registrations, verifier values, and optional diagnostics;
3. write a new measurements document without overwriting an earlier run;
4. compile the artifact from those measurements and real output files; and
5. assert that the artifact's raw and routed metric rows equal the frozen evaluation rows.

Record the projector revision and its own SHA-256 under `provenance.projection`, and bind the
source-backed compositor provenance document by SHA-256 in `deterministic_postprocess`. This is the
attestation boundary that closes the gap between “the genuine files were hash-bound” and “these
artifact fields were deterministically derived from those files.”

`experimental_visual_embedding_diagnostics` may retain the measured Qwen cosine comparisons for
the local output crop, restyle content retention, and Claude-Lorrain-versus-Van-Gogh reference
centroids. The compiler checks the reported margins arithmetically. The contract explicitly
marks these values descriptive, nonprobabilistic, and not a validated style or CSD score; a tiny
style margin is evidence of weak discrimination, not evidence to round up into a success claim.

## Viewer projection

The engine artifact is the evidence source of truth. A viewer projection must map fields as
follows and must not supply defaults for missing evidence:

| Engine artifact | Viewer concept |
| --- | --- |
| `evidence_status` | measured-run / contract-fixture badge |
| `source` | shared source identity and media inventory lookup by SHA-256 or ContentRef |
| `intents[].route` | granularity, exact normalized region rectangle, region-crop query identity, namespace, and active corpus |
| `intents[].retrieval.ranked_evidence` | evidence cards, exact scores, licence, source page, Khive record and ContentRef |
| `intents[].plan.stages` | control-pipeline strip |
| `intents[].retrieval.raw_diagnostics` | ungated Qwen order and four descriptive metrics; label as geometry, never probability |
| `intents[].retrieval.metrics` | four intent-routed structural-control metrics; preserve `not_computed` visibly |
| `intents[].verification` | edit checks; preserve `not_run` visibly |
| `intents[].negative_output_evidence` | rejected real outputs and their failed verifier evidence |
| `intents[].output` | selected external result identity, provider/compositor provenance, BlobStore registration state, and rollback identity |
| `experimental_visual_embedding_diagnostics` | explicitly nonprobabilistic Qwen geometry diagnostics and their limitations |
| `cross_intent_metrics` | route-separation metric |
| `descriptor`, `contracts`, `provenance` | model/checkpoint/Lattice, schema, Khive revision, and run identity drawer |

Preference Model A/B is a separate governed artifact. The viewer may join it by board, model,
feature-schema, and candidate-pool identity, but the Pixel RAG compiler never fabricates that
join. Likewise, source and generated image bytes belong in a hash-verified viewer media
inventory; the viewer must not retain its earlier compile-time report-fixture media IDs.
The current bridge intentionally projects output identity and evidence rather than output pixels;
actual before/after media requires a separate report-owned, hash-verified safe-media channel.

Freeze a compiled artifact into the offline bundle from the repository root:

```bash
npm --prefix viewer run pixel-rag:write -- \
  --input /absolute/path/to/pixel-rag-artifact.json \
  --write src/generated/pixel-rag-bridge.json
npm --prefix viewer run build
```

The input must be the canonical JSON emitted by `write_pixel_rag_artifact`. The bridge embeds
that same closed artifact and pins its byte length, raw SHA-256, canonical SHA-256, artifact id,
schema version, and bridge-generator revision. Every viewer build repeats the engine's schema,
semantic, and identity validation. The `fallback` sentinel contains no artifact and keeps the
clearly labelled presentation fixture active until a measured bridge is generated. A checked-in
measured bridge instead remains bound to its immutable input artifact identities and is
revalidated at build time. Pixel RAG evidence status never upgrades the independently governed
preference panel.
