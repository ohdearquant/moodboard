# Governed, intent-scoped Pixel RAG

`moodboard.pixel_rag` freezes a control-plane artifact. It does not generate an image and it
does not call a model. Its input is the reviewed public-domain acquisition manifest plus a
closed record of Khive retrievals. The compiler proves that every candidate ContentRef belongs
to the manifest, that the Lattice descriptor is the current `lattice-embed` 0.9.0 contract,
and that the scores remain in the exact order returned by the measured search.

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
5. optionally add explicit human relevance gains (`0..3`); and
6. optionally add verifier measurements, including the named method revision, threshold, and
   comparison operator.

The input must validate against
`moodboard/schema/pixel_rag_measurements_v1.schema.json`. Use
`evidence_status: measured_run` only for real Khive outputs. Tests use
`evidence_status: contract_fixture`; fixture numbers are never promoted automatically.

If relevance judgments are absent, P@3 and nDCG@5 are emitted as `not_computed` with a reason.
If edit-verification measurements are absent, verification is `not_run`. Merely supplying a
generated image never invents either kind of metric.

Compile and freeze without overwriting an earlier artifact:

```bash
python -m moodboard.pixel_rag \
  --manifest .cache/adobe-demo-public-domain-v1/run-20260812-final-current/manifest.json \
  --measurements .cache/adobe-demo-runs/measured-pixel-rag-input.json \
  --local-output .cache/adobe-demo-generated-v1/apple-to-lemon.png \
  --restyle-output .cache/adobe-demo-generated-v1/apple-classical-restyle.png \
  --output .cache/adobe-demo-runs/pixel-rag-artifact.json
```

Add `--local-record-id` plus `--local-content-ref` (and the corresponding restyle flags) only
after Khive has registered those exact output bytes. The compiler hashes the file again and
rejects a mismatched expected ContentRef.

The output validates against `moodboard/schema/pixel_rag_artifact_v1.schema.json`. Its
`artifact_id` hashes the canonical document excluding only that field; the document also binds
the SHA-256 identities of the artifact, measurements, and source-manifest schemas.

## Viewer projection

The engine artifact is the evidence source of truth. A viewer projection must map fields as
follows and must not supply defaults for missing evidence:

| Engine artifact | Viewer concept |
| --- | --- |
| `evidence_status` | measured-run / contract-fixture badge |
| `source` | shared source identity and media inventory lookup by SHA-256 or ContentRef |
| `intents[].route` | granularity, human region overlay, namespace, and active corpus |
| `intents[].retrieval.ranked_evidence` | evidence cards, exact scores, licence, source page, Khive record and ContentRef |
| `intents[].plan.stages` | control-pipeline strip |
| `intents[].retrieval.metrics` | retrieval metrics; preserve `not_computed` visibly |
| `intents[].verification` | edit checks; preserve `not_run` visibly |
| `intents[].output` | external result media, provider provenance, BlobStore registration state, and rollback identity |
| `cross_intent_metrics` | route-separation metric |
| `descriptor`, `contracts`, `provenance` | model/checkpoint/Lattice, schema, Khive revision, and run identity drawer |

Preference Model A/B is a separate governed artifact. The viewer may join it by board, model,
feature-schema, and candidate-pool identity, but the Pixel RAG compiler never fabricates that
join. Likewise, source and generated image bytes belong in a hash-verified viewer media
inventory; the viewer must not retain its earlier compile-time report-fixture media IDs.

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
semantic, and identity validation. The checked-in `fallback` sentinel contains no artifact and
keeps the clearly labelled presentation fixture active until a measured bridge is generated.
Pixel RAG evidence status never upgrades the independently governed preference panel.
