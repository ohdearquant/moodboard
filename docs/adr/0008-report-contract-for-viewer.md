# ADR-0008: Report version 1.1 carries what the viewer presents

- **Status:** Proposed
- **Date:** 2026-08-08
- **Amends:** [ADR-0002](0002-report-contract.md), and incorporates the complete immutable fit
  identity later decided by [ADR-0005](0005-reference-set.md) into the viewer-facing contract.
  This record does not supersede either record. ADR-0002 is `Proposed`, and this repository has
  twice chosen amendment over supersession for exactly that
  reason and said so in as many words: `docs/adr/0004-abstention.md:243` ("ADR-0002's schema is not
  yet accepted, so this is an amendment to a proposal rather than a break of a contract") and
  `docs/adr/0005-reference-set.md:199-200`. The immutability rule that makes supersession the right
  instrument applies to records that are *accepted* (`docs/adr/README.md:5-7`), and none is. ADR-0002
  therefore stays live and authoritative for everything this record does not change.
- **Measurable claim:** yes, inherited. Dataset row: `interval-coverage` in
  [`DATASETS.md`](../../DATASETS.md), measured by the pre-registered coverage, width and all-tied
  criterion at `docs/adr/0002-report-contract.md:215-252` and `eval/thresholds.json:16-47`. The
  media, metadata and compatibility decisions this record introduces are a deterministic file
  contract and owe no dataset row of their own. The claim is named here anyway because version 1.1
  is where the interval requirement lives for a v1.1 report, and `docs/adr/README.md:18-21` asks the
  record making a claim to name its dataset in the record itself. Under amendment ADR-0002 also
  still names it, so this is deliberately redundant rather than load-bearing: the row is named in
  whichever record a reader arrives at first.

## Context

ADR-0001 makes the JSON report the only boundary between the Python engine and the static
TypeScript viewer. The viewer does not recompute scores, ranks, intervals, or ties, and the HTML
artifact must work without a server or view-time network access
(`docs/adr/0001-engine-and-viewer-split.md:28-56`). The report therefore has to carry every image
representation and every interpretation that the viewer presents.

ADR-0002 anticipated part of that need. Version 1.0 has an offline reference catalogue whose
entries include a content hash, MIME type, dimensions, and an inline thumbnail. An exemplar points
to an entry by `reference_id` (`moodboard/schema/report_v1_0.schema.json:#/$defs/referenceEntry`,
`#/$defs/thumbnail`, and `#/$defs/exemplar`). That part is implemented and remains sufficient.

The implemented asset shape is not equally complete. Both asset branches carry a `source` string,
but neither carries the candidate image's hash, dimensions, or thumbnail
(`moodboard/schema/report_v1_0.schema.json:#/$defs/scoredAsset/properties/source` and
`#/$defs/abstainedAsset/properties/source`; `moodboard/report.py:221-267,490-525`). A local path or
remote URI in `source` is not a portable image inside a self-contained HTML file. The exemplar
array also has no count, uniqueness, ordering, or reference-resolution constraint
(`moodboard/schema/report_v1_0.schema.json:88-95,330-332,370-372`). A conforming v1.0 report can
therefore leave the viewer unable to show the candidate beside the three references that ADR-0001
names as its central interaction. Before report v1.1, the CLI defaulted to three exemplars but
accepted another count, which was behavior rather than a report guarantee. The v1.1 writer now
refuses any other setting and enforces the cardinality in the typed contract.

The axis values have a second gap. Version 1.0 places a conformal p-value and classical distances
in one scalar-or-null mapping. Only the overall style score has an interval, and the mapping does
not carry a value kind, direction, display label, availability reason, or method revision
(`moodboard/schema/report_v1_0.schema.json:#/$defs/axesScored` and `#/$defs/axesAbstained`). The
classical functions are distances in `[0, 1]` (`INTERFACES.md:409-430`), while a larger conformal
p-value ranks as a better fit (`docs/adr/0002-report-contract.md:152-159`). A viewer that treats the
four scalars alike reverses the meaning of at least one kind of value.

Version 1.0 provenance carries engine name and version, model repository, revision and hash, one
command string, one seed, and a creation time. It does not carry the engine source revision, whether
that source was modified, a structured argument vector, the schema identity, or method provenance
for each axis (`moodboard/schema/report_v1_0.schema.json:#/$defs/provenance`;
`moodboard/report.py:293-313,549-562`). Reference content hashes identify the board inputs, but
candidate content hashes are absent.

Version 1.0 also predates ADR-0005's complete persisted fit identity. Its `board.fit` projects
`metric`, effective `k`, the two distance cuts, and interval method metadata, but omits configured
`k_cap`, `min_category_size`, `interval_level`, and `far_outlier_iqr_multiplier`. Those values can
move scores, intervals, category admission, or abstention and are now immutable fields of verified
`brand.mb` format 3 and its board hash. The report must disclose that complete numeric policy rather
than reload mutable registry defaults or leave a viewer unable to explain it. The multiplier's
source string is provenance: it is recorded beside the numeric value but, as ADR-0005 specifies,
does not enter the score-bearing board hash.

This record amends ADR-0002 rather than superseding it, and amends rather than merely extends it,
because the implemented compatibility behavior contradicts ADR-0002's policy. Amendment is the
instrument this repository uses against a `Proposed` record, following
`docs/adr/0004-abstention.md:243` and `docs/adr/0005-reference-set.md:199-200`; what follows replaces
one specific rule of ADR-0002 and leaves the rest of that record live and authoritative.

ADR-0002 says a consumer must ignore fields added by an unknown minor version (`docs/adr/0002-report-contract.md:209-213`). The implemented schema fixes the
version to `1.0`, closes the root and most fixed-shape nested objects, and deliberately rejects both
`1.1` and an unknown root field (`moodboard/schema/report_v1_0.schema.json:7-20,74-125,300-405`;
`tests/test_report.py:468-489`). Before this amendment, the parser expected an already schema-valid
mapping and the report command constructed the typed object before validating its serialized form.
That implementation still refused v1.1 before rendering, but it did not provide the required
version-first dispatch. The v1.1 reader now selects the exact named schema from the root version
token before interpreting any other report content.

ADR-0002 was written before the engine existed (`docs/adr/0002-report-contract.md:267-277`). The
original author could know that reference thumbnails and versioning were necessary, but could not
inspect the later closed validator or a concrete viewer's candidate-media, axis-detail, and source
revision needs. Those later facts are the reason for this amendment. The implemented strict validator
and the now-explicit viewer requirements resolve the earlier compatibility ambiguity in favor of the
policy below. The scope remains the one file boundary already chosen by ADR-0001.

## Decision

Report version 1.1 is the complete viewer-facing contract. It retains every v1.0 field with the
same path, type, and meaning, then adds candidate display media, explicit axis details, stronger
exemplar assertions, the complete immutable fit policy, and structured provenance. The engine
remains the only producer of statistical meaning.

This record carries forward the unaffected decisions from ADR-0002. The report remains a closed,
versioned JSON document. `state` remains the discriminator between scored and abstained assets.
Scored assets still require `score`, `interval`, and `rank`; abstained assets still omit those keys
and require `reason`, `explanation`, and `measurement`. The leave-one-out interval, paired tie list,
competition ranking, separate axes, reference catalogue, flags, and exact axis-vocabulary invariant
retain their v1.0 meanings (`docs/adr/0002-report-contract.md:119-207`). A v1.1 writer validates
against the v1.1 JSON Schema and the cross-field assertions in this record before writing anything.

ADR-0002's interval-coverage and sharpness acceptance criterion remains in force unchanged. It
still requires the pre-registered coverage, width, and all-tied measurements at
`docs/adr/0002-report-contract.md:215-252` and `eval/thresholds.json:16-47`. This record's inherited
measurable claim applies to that interval. The new media, fit-disclosure, metadata, and compatibility
decisions are deterministic contract assertions and add no separate dataset claim. They do not
remove the predecessor's empirical gate for the interval this record carries forward.

### Current v1.0 and viewer-required v1.1 fields

| Viewer need | Implemented v1.0 field | v1.0 result | Required v1.1 field or assertion | v1.0 report in a v1.1 viewer |
|---|---|---|---|---|
| The viewer shows each reference offline. | `#/references[]/{reference_id,content_sha256,mime,width,height,thumbnail}` | The field is sufficient. The thumbnail is inline, although v1.0 accepts a generic MIME token. | The same fields remain required. `thumbnail.mime` is restricted to `image/jpeg`, `image/png`, or `image/webp`, and strict decoding must agree with the declared MIME and dimensions. | The viewer shows a safely decoded reference thumbnail. An unsupported MIME or undecodable legacy payload produces an inert placeholder and a v1.0 compatibility notice. It never causes a network request or report-level rejection. |
| The viewer shows the candidate offline. | `#/assets[]/source` | The string is provenance only. It supplies no portable bytes, hash, or dimensions. | `#/assets[]/image` is required in both asset states and has the exact image shape defined below. `source` remains unchanged and is never fetched by the viewer. | The viewer shows a candidate placeholder reading "Candidate image was not included in report version 1.0." |
| The viewer shows three closest references together. | `#/assets[]/exemplars[]/{reference_id,similarity}` | The values exist, but their count, uniqueness, order, and reference resolution are not enforced. The engine currently selects them board-wide (`moodboard/cli.py:497-519,589-595`). | The existing array contains exactly `min(3, references.length)` distinct entries, ordered by descending similarity and then by earlier position in `references`, preserving the implemented stable tie break. Every id resolves into `#/references`, whose `reference_id` values are unique. | The viewer uses at most the first three resolvable entries in transmitted order. Missing, dangling, or extra legacy entries produce placeholders or a v1.0 compatibility notice rather than invented references. A DUPLICATE id is fatal in every version and refuses the report; it is never silently deduplicated into a placeholder. |
| The viewer knows the current axis vocabulary. | `#/board/representation/axes` and `#/assets[]/axes` | The vocabulary exists, and Python asserts the exact key set (`moodboard/report.py:723-751`). | Both fields remain unchanged. `#/board/representation/axis_definitions` has exactly `style` followed by the ids in `axes`, in the same order. | The existing key set remains authoritative. |
| The viewer discloses every immutable scoring-policy value. | `#/board/fit/{metric,k,cluster_cut,dup_cut,interval}` | This is only the report-v1.0 compatibility projection; it omits four numeric policy values later bound by ADR-0005. | `#/board/fit` is the exact closed object below, adding `k_cap`, `min_category_size`, `interval_level`, `far_outlier_iqr_multiplier`, and `far_outlier_iqr_multiplier_source`. | The viewer labels the v1.0 object as a legacy fit projection and does not invent the omitted values. |
| The viewer distinguishes a p-value from a distance. | `#/assets[]/axes/*` | Values are scalar or null without a machine-readable kind or direction. | Every axis definition requires `value_kind` and `direction`. Style is `conformal_p_value` with `higher_is_better_fit`; each current classical axis is `normalized_distance` with `lower_is_closer`. | The viewer may show the documented v1.0 values, but labels the section "Legacy axis values" and states that axis metadata was not recorded. |
| The viewer renders availability without turning missing data into zero. | `#/assets[]/axes/style` is null on abstention. | Style has an explicit null convention. The implemented engine still computes every classical axis for an abstained asset (`moodboard/cli.py:1004-1016`). | Each definition requires `availability`. Style is `scored_only`; current classical axes are `all_assets`. A v1.1 classical value is numeric in both asset states. | The overall abstention renders from the existing union. Missing metadata remains visibly unavailable. |
| The viewer presents uncertainty without implying that every scalar has an interval. | `#/assets[]/interval` exists only for a scored style result. | Style uncertainty is available at the asset level. Classical-axis uncertainty is not estimated. | Each definition requires `uncertainty`. Style is `asset_interval`, which points to the existing asset interval. Current classical axes are `none`; the viewer states that no interval is reported. | The overall style interval renders normally. Classical values are labelled "Interval not reported in version 1.0." |
| The viewer explains how each axis value was produced. | Style model identity appears under `#/board/representation/style` and `#/provenance/model`; classical values are means over the selected exemplars in code (`moodboard/cli.py:522-546`). | There is no public aggregation token, method name, or method revision in the report. | Every axis definition requires `aggregation`, `method.name`, and positive integer `method.revision`. The v1.1 values are fixed below. | The viewer reports the model fields it has and says "Axis method revision was not recorded." |
| The viewer identifies the engine source. | `#/provenance/engine/{name,version}` | Package identity exists, but exact source and modified state do not. | `#/provenance/engine/{source_repository,source_revision,source_dirty}` is required WHEN THE BUILD CAN SUPPLY IT, and the object is absent as a whole when it cannot. The repository is an absolute URI, the revision is forty or sixty-four lowercase hexadecimal characters, and the dirty state is Boolean. The three fields are required together or absent together; a partial object is invalid. | The viewer says "Engine source revision was not recorded in report version 1.0." |
| The viewer identifies the style model. | `#/provenance/model/{repo,revision,sha256}` | The repository, revision, and weight identity are already present. | The three fields remain required with their v1.0 types and meanings. | The viewer shows the v1.0 model identity exactly as recorded. |
| The viewer identifies the report time and recorded random seed. | `#/provenance/{seed,created_at}` | The integer seed and RFC 3339 creation time are already present. | Both fields remain required and unchanged. | The viewer shows the recorded values without inventing additional seeds. |
| The viewer can preserve invocation token boundaries. | `#/provenance/command` | A human-readable string exists, but token boundaries are ambiguous. | `#/provenance/argv` is a non-empty array of strings and is authoritative. `command` remains required and equals the POSIX `shlex.join(argv)` representation. | The viewer treats the recorded command as unstructured and potentially sensitive provenance. It does not display it until the reader explicitly reveals it. |
| The viewer identifies the schema it interpreted. | `#/schema_version` | The value is exactly `1.0`, but there is no schema hash in provenance. | `#/schema_version` is `1.1`; `#/provenance/schema/{id,sha256}` identifies the validating schema. | The bundled v1.0 schema identity is shown, and no report-supplied schema hash is invented. |
| The viewer identifies every image input. | Reference entries carry `content_sha256`; candidate assets do not. | Board images are content-addressed, while candidate identity depends on an opaque id and source string. | Each candidate `image.content_sha256` is required. The existing reference hash remains unchanged. | Candidate content identity is shown as unavailable. |

Dataset identity is not added to an ordinary v1.1 product report. A product report may be generated
from private user files rather than a named evaluation dataset. Reference and candidate content
hashes identify the actual image inputs, while a published evaluation result must identify its
dataset in the separate measurement artifact. Adding a misleading optional dataset label here would
not make a private input reproducible.

### The v1.1 complete fit-policy shape

ADR-0005's later board-integrity amendment makes every score-moving numeric fit value immutable.
Version 1.1 therefore replaces the v1.0 fit projection with this required, closed object:

```jsonc
"fit": {
  "metric": "cosine",
  "k": 5,
  "k_cap": 5,
  "cluster_cut": 0.35,
  "dup_cut": 0.05,
  "min_category_size": 5,
  "interval_level": 0.9,
  "far_outlier_iqr_multiplier": 1.5,
  "far_outlier_iqr_multiplier_source":
    "eval/thresholds.json#/abstention/far_outlier_iqr_multiplier",
  "interval": {
    "method": "loo-jackknife-plus",
    "replicates": null,
    "seed": 0
  }
}
```

`k` is the effective value and equals `min(k_cap, references.length - 1)`. `k_cap` and
`min_category_size` are positive integers. Both cuts are finite cosine distances in `[0, 2]`.
`interval_level` is finite and strictly between zero and one. The far-outlier multiplier is finite
and non-negative. Its source is a non-empty provenance string naming where the frozen numeric value
was selected; changing only that string does not change `board.id`, while changing the numeric value
does. Every scored asset's `interval.level` equals `board.fit.interval_level`. Rank copies these
values from a verified `brand.mb`; it does not consult the current threshold registry to fill or
override them. Requested alpha, exemplar count, and tie-pair mode remain invocation/report-request
identity rather than board-fit fields.

### The v1.1 image shape

Both scored and abstained assets add the following required object:

```jsonc
"image": {
  "content_sha256": "64 lowercase hexadecimal characters",
  "mime": "image/jpeg",
  "width": 1600,
  "height": 1067,
  "thumbnail": {
    "mime": "image/webp",
    "width": 512,
    "height": 341,
    "data_base64": "..."
  }
}
```

`image` is closed and requires exactly the five fields shown. `content_sha256` hashes the original
candidate bytes consumed by the writer. It does not hash the thumbnail. `mime` uses the existing
MIME string pattern. Width and height are positive integers describing that original candidate. The
nested thumbnail uses the existing v1.0 thumbnail field names. Its MIME is one of `image/jpeg`,
`image/png`, or `image/webp`. Its width and height are positive integers, and `data_base64` strictly
decodes to that image type and those pixel dimensions. The same thumbnail MIME and decode assertions
apply to v1.1 reference entries. SVG and other active or generic payloads are not valid thumbnails. The
report carries separately encoded display bytes and information about the original. It does not require
the original bytes as a second embedded payload, and it does not claim that the thumbnail is smaller
without a separately registered size rule.

No pixel-size, byte-size, or compression-quality threshold is set here. Such a number would be a
visual and payload quality claim, and no declared transport or visual-quality requirement currently
justifies one. A later proposal must justify and pre-register those bounds before measuring
representative reports. The positive dimension, safe MIME, decodability, and three-exemplar rules
are functional contract assertions and impose no empirical quality thresholds.

The exemplar assertion requires three entries whenever the board has at least three references.
`min(3, references.length)` covers every board carrying fewer than three references, in either asset
state, and inventing a third reference would be worse than showing every reference the board actually
contains. It is not confined to abstention. A verified board's floor is two references, and any
requested alpha at or above 1/(n_eff+1) is honoured, so a two-reference board returns a **scored**
asset. Measured 2026-08-08 through the public CLI with the
classical encoder, on a board of two structurally distinct references and a candidate that is a copy
of the first: `references 2, n_eff 2.0000 | requested a 0.5 | supported a 0.3333 | scored 1 |
abstained 0`, and the written report carries `state: scored`, two exemplars, `score: 1.0`, `rank: 1`.
So the viewer labels a reduced set rather than presenting it as a complete three-way comparison, and
it must do so for a scored two-cell strip and not only for an abstention.

Exemplars remain board-wide, matching the implemented v1.0 selection: `_exemplars`
(`moodboard/cli.py:497-519`) ranks against `board.reference_ids`, and is called at `:589-594` with the
full reference arrays. This record accepts a consequence its predecessor left unstated. The score is
category-local, computed against that category's members only
(`docs/adr/0004-abstention.md:88-90`), while the exemplars are the whole board's nearest references,
so on a multi-category board an asset's exemplars can come from a category the score was never
computed against. The engine states the split itself at `moodboard/cli.py:572-574`: "Rule 3's
nonconformity values come from the board-wide augmented bag ... even though the score itself is
category-local." A viewer must therefore not caption the exemplars as the basis for the score
without that qualification. Selecting exemplars category-locally instead is a change to
`moodboard/cli.py:589-594` and belongs in a record that says so.

### The v1.1 axis-definition shape

`board.representation` adds one required `axis_definitions` array. Metadata belongs at board scope
because its meaning is identical for every asset in the report. Repeating labels, directions, and
methods on every asset would increase report size and create another per-asset consistency problem.

```jsonc
"axis_definitions": [
  {
    "axis_id": "style",
    "label": "Style fit",
    "value_kind": "conformal_p_value",
    "direction": "higher_is_better_fit",
    "aggregation": "full_conformal_category",
    "availability": "scored_only",
    "uncertainty": "asset_interval",
    "method": {"name": "full-conformal-p-value", "revision": 1}
  },
  {
    "axis_id": "palette",
    "label": "Palette distance",
    "value_kind": "normalized_distance",
    "direction": "lower_is_closer",
    "aggregation": "mean_over_exemplars",
    "availability": "all_assets",
    "uncertainty": "none",
    "method": {"name": "palette-distance", "revision": 1}
  }
]
```

Every definition is a closed object. `axis_id`, `label`, and `method.name` are non-empty strings,
and `method.revision` is a positive integer. `value_kind` is `conformal_p_value` or
`normalized_distance`; `direction` is `higher_is_better_fit` or `lower_is_closer`; `aggregation` is
`full_conformal_category` or `mean_over_exemplars`; `availability` is `scored_only` or `all_assets`;
and `uncertainty` is `asset_interval` or `none`.

The array begins with `style` and then contains exactly the ids from
`board.representation.axes` in their declared order. The v1.1 metadata for the current vocabulary is
fixed as follows:

| Axis id | Label | Value kind and direction | Aggregation | Availability and uncertainty | Method |
|---|---|---|---|---|---|
| `style` | `Style fit` | `conformal_p_value`, `higher_is_better_fit` | `full_conformal_category` | `scored_only`, `asset_interval` | `full-conformal-p-value`, revision `1` |
| `palette` | `Palette distance` | `normalized_distance`, `lower_is_closer` | `mean_over_exemplars` | `all_assets`, `none` | `palette-distance`, revision `1` |
| `tone` | `Tone distance` | `normalized_distance`, `lower_is_closer` | `mean_over_exemplars` | `all_assets`, `none` | `tone-distance`, revision `1` |
| `composition` | `Composition distance` | `normalized_distance`, `lower_is_closer` | `mean_over_exemplars` | `all_assets`, `none` | `composition-distance`, revision `1` |

For a scored asset, `assets[].axes.style` equals `assets[].score`, and the existing
`assets[].interval` is the interval named by the style definition. For an abstained asset,
`assets[].axes.style` remains null and the asset-level `reason` and `explanation` explain why. Every
current classical axis is numeric in both states and remains the mean distance to that asset's
exemplars, as implemented at `moodboard/cli.py:522-546,985-1016`. The viewer displays the existing
asset value using the matching board definition and does not infer a classical interval.

An axis that is removed after failing its intervention test is absent from
`board.representation.axes`, every asset's `axes`, and `axis_definitions` together. A later axis can
use the current enum values only if they describe it truthfully. Version 1.1 fixes every method name,
revision, and aggregation token above and does not alter ADR-0005's canonical board-hash object. A
change to the style method or aggregation can move a score and therefore cannot ship by incrementing
metadata alone. It first requires an amendment to ADR-0005 that defines a new hash-object version,
the exact new canonical fields and ordering, and their value source
(`docs/adr/0005-reference-set.md:35-52`). A classical-method revision changes a diagnostic value but
not the style score; it requires a later report version, while the existing board hash remains the
style-score comparability key.

### The v1.1 provenance additions

The existing provenance fields retain their v1.0 meanings. Version 1.1 adds these required fields:

```jsonc
"provenance": {
  "engine": {
    "name": "moodboard",
    "version": "0.1.0",
    "source_repository": "https://github.com/ohdearquant/moodboard.git",
    "source_revision": "40 or 64 lowercase hexadecimal characters",
    "source_dirty": false
  },
  "model": {"repo": "...", "revision": "...", "sha256": "..."},
  "command": "human-readable legacy command",
  "argv": ["moodboard", "rank", "..."],
  "seed": 0,
  "created_at": "RFC 3339 timestamp",
  "schema": {
    "id": "https://github.com/ohdearquant/moodboard/schema/report_v1_1.schema.json",
    "sha256": "64 lowercase hexadecimal characters"
  }
}
```

`argv` is a non-empty array of strings, records token boundaries, and is the authoritative
invocation. `command` remains required because its v1.0 path and meaning are preserved, and it must
equal the POSIX `shlex.join(argv)` representation. This follows the current command construction at
`moodboard/cli.py:1174-1179` and prevents two invocation claims in one report. Both fields are
potentially sensitive because arguments can contain local paths or credentials. The viewer escapes
them as text, keeps them collapsed by default, and requires an explicit reveal action.

`source_repository` is an absolute URI; `source_revision` is forty or sixty-four lowercase
hexadecimal characters identifying the engine source used to build the executable. A true
`source_dirty` value is valid but requires the viewer to state that the report cannot be reproduced
from the named revision alone.

**The producer is named here, and the field is conditional because not every installation can
resolve it.** The engine implements runtime resolution from a source checkout. An installed wheel
has no `.git` to interrogate at report time, so the object cannot be unconditionally required
without inventing provenance. Two valid producer arms remain, exactly one of which a given build
takes:

- **Build-time stamp.** The packaging writes the revision and dirty state into the distribution at
  build time. This requires a `pyproject.toml` change and a record that decides it; that record owns
  the change, not this one. Under this arm the object is present in every report the build produces.
- **Runtime resolution.** The implemented engine resolves `HEAD` and tracked or untracked dirty
  state from a source checkout. Under this arm the object is present for a source install and absent
  for a wheel or a failed Git query, which is why absence is valid rather than a defect.

A consumer therefore treats an absent engine-source object as "not recorded" and says so, using the
same unavailable notice it uses for a v1.0 report. It never treats absence as a validation failure. These fields record source identity and modified state; they do not
attest dependency versions, private input availability, or the content of uncommitted changes.
`schema` is closed. Its `id` must equal
`https://github.com/ohdearquant/moodboard/schema/report_v1_1.schema.json`, which is the v1.1 JSON
Schema `$id`, and its lowercase hexadecimal `sha256` hashes the exact schema bytes used by the
writer. The model fields remain unchanged because v1.0 already records their repository, revision,
and weight identity.

### Compatibility policy

`schema_version` remains `major.minor`, but compatibility is directional. A minor version preserves
the paths, types, and meanings of fields from earlier minors in the same major. It may add fields
required of new writers and may add cross-field assertions. A newer minor consumer supports the
older minors it names explicitly. An older exact-schema consumer is not required to accept an
unknown newer minor. A major version may remove, retype, or reinterpret a field. Every consumer
dispatches on the complete version before interpreting any other field and refuses every version it
does not explicitly support.

The required matrix is exact:

| Consumer | Report | Required behavior |
|---|---|---|
| v1.0 | v1.0 | It validates with the v1.0 schema and consumes the report normally. |
| v1.0 | v1.1 | It refuses the document and renders nothing. It does not strip fields, rewrite the version, or render a partial score. |
| v1.1 | v1.0 | It validates with the v1.0 schema and enters declared legacy mode. It does not relabel or serialize the report as v1.1. |
| v1.1 | v1.1 | It validates with the v1.1 schema and every cross-field assertion, then consumes the full contract. |
| v1.1 | v1.2 or any other unknown minor | It returns `unsupported_schema_version` before interpreting report content. |
| Any version-aware v1.1 consumer | v2.x, a malformed version, or a missing version | It returns `unsupported_schema_version` before interpreting report content. An existing v1.0 consumer may surface its schema or parse error, but it still renders nothing. |

`from_json_dict` reads only the root object and complete version token before selecting the exact
v1.0 or v1.1 schema. It validates against that schema before constructing the corresponding typed
object. It never projects one minor into the other. Unknown, malformed, and missing versions raise
`unsupported_schema_version` before malformed score or asset content can influence the error.

Legacy mode has one defined presentation. The v1.1 viewer shows the v1.0 overall score, interval,
rank, abstention, ties, flags, safely decodable reference thumbnails, and recorded provenance without
changing their meaning. It never fetches `assets[].source`. A schema-valid v1.0 thumbnail whose MIME
is outside the v1.1 allowlist, whose bytes do not decode, or whose declared dimensions disagree with
the bytes is never inserted into the document. Its tile becomes an inert "Reference preview is not
safely renderable from report version 1.0" placeholder, and the rest of the report remains available.

For v1.0 exemplars, the viewer considers at most the first three transmitted entries and displays
only ids that resolve into `references`. It uses an empty tile for every missing or dangling slot
and shows the incomplete-exemplar notice whenever the transmitted set is not exactly three unique
resolvable ids. It does not reorder legacy entries or recompute similarity.

**A duplicate exemplar id within one asset is fatal in EVERY version, v1.0 included, and this record
no longer deduplicates it.** An earlier draft had the viewer remove a duplicate after its first
occurrence for legacy reports. That is struck. Silent removal changes what the user is shown without
telling them, which defeats the simultaneous comparison the viewer exists for
(`docs/adr/0001-engine-and-viewer-split.md:19-24`), and it contradicted ADR-0007, whose fatal list
already names duplicate exemplar identifiers within one asset with no version qualifier
(`docs/adr/0007-viewer-architecture.md:533-537`).

The cost is stated rather than hidden. A schema-valid v1.0 report carrying a duplicate exemplar id
will now REFUSE instead of rendering. That is correct rather than regrettable: ADR-0007 had already
declared such a report malformed, so the dedup was quietly rendering a document this batch's own
contract calls invalid. And where the batch means to scope behaviour by version it says so outright,
as at `docs/adr/0007-viewer-architecture.md:552-554`, where one image's rejection is a non-fatal
diagnostic for legacy v1.0 and fatal for a strict-triptych report. The absence of any such qualifier
on the duplicate rule is therefore a decision, not an oversight. A null classical value permitted by the abstained v1.0 shape renders as
"Axis value unavailable in report version 1.0"; it is never coerced to zero.

Legacy mode also shows the candidate placeholder, the legacy-axis notice, and explicit unavailable
text for candidate hash, engine source revision, structured argv, schema hash, and per-axis method
revision. Missing v1.1 data is not inferred from filenames, package versions, browser state, or
network access, and the reader never emits a rewritten v1.1 document.

### Assertions and breaking-input regression cases

The v1.1 writer and reader share the following assertions. Each regression first proves that its
unmodified generated baseline passes, then applies the named single-purpose mutation.

| Assertion | Breaking input that must catch a regression |
|---|---|
| A v1.1 reader dispatches before content interpretation, and no reader renders an unsupported version. | Give a v1.1 reader an unknown-version report whose first asset also contains a malformed score. The result must be `unsupported_schema_version`; a score error or a partial render fails. Give the current v1.0 reader a valid v1.1 report and require refusal with no render. Add an unknown root key to an otherwise valid v1.1 report and require schema rejection. |
| Both asset states require the candidate image object. | Remove `image` once from a scored asset and once from an abstained asset. Each mutated v1.1 document must fail. |
| The complete immutable fit policy is disclosed without a mutable-registry escape hatch. | Remove each fit field in turn, add an unknown fit field, change `k` so it differs from `min(k_cap, references.length - 1)`, or change a scored asset's interval level without changing `board.fit.interval_level`. Each case must fail. Generate a report from a verified board after changing the current registry and require the emitted fit and score-bearing content to remain unchanged. |
| Candidate identity describes the original input rather than the preview. | Against the verifier's known candidate bytes, substitute the thumbnail hash for `image.content_sha256`, change the original MIME, or change either original dimension. Schema shape may still pass, but the writer/input assertion must fail in every case. |
| Every thumbnail is inert, decodable image data whose declared MIME and dimensions are true. | Corrupt the base64, declare WebP bytes as PNG, change one declared dimension, and supply SVG bytes. Each case must fail validation before rendering. |
| The exemplar set is the deterministic board-wide set the viewer promises. | Supply two exemplars on a board with at least three references, a duplicate exemplar id, duplicate ids in `references`, a dangling id, a fourth exemplar, a reversed similarity order, and an equal-similarity pair in reverse catalogue order. Each v1.1 case must fail. The duplicate-exemplar-id case must ALSO fail when supplied as a schema-valid v1.0 report, which is the arm that proves the rule is not version-scoped and that the struck legacy dedup has not survived anywhere. |
| The scored and abstained branches remain disjoint. | Add `score` to an abstained asset and add `reason` to a scored asset. Both must fail, preserving the v1.0 regression (`moodboard/schema/report_v1_0.schema.json:#/$defs/asset`). |
| `axis_definitions`, `axes`, and the report vocabulary have one exact ordered id set. | Delete a definition, add an unlisted definition, reorder two definitions, and rename a legacy axis without changing the vocabulary. Each case must identify the inconsistent ids. |
| Axis definitions are closed and internally coherent. | Mark palette as `higher_is_better_fit`, label it a p-value, use `full_conformal_category`, claim `asset_interval`, or omit its method revision. Each case must fail. |
| The repeated style value cannot drift. | Change only `assets[].axes.style` on a scored asset. It must fail equality with `assets[].score`. Change style to numeric on an abstained asset or null on a scored asset, and require rejection. |
| Classical values remain honest about availability and uncertainty. | Set a classical value to null in either asset state, mark a classical definition `scored_only`, or claim an interval in v1.1. Each case must fail. |
| Provenance records source, one invocation, and schema identity without contradictory copies. | Omit `source_repository` while retaining `source_revision` (a PARTIAL engine-source object, which must fail), use a relative repository value or non-hex revision, omit `argv`, change only `command` so it differs from `shlex.join(argv)`, change the schema hash from the bytes used to validate, or omit an axis method revision. Each case must fail. A report omitting the engine-source object ENTIRELY must PASS, and a consumer given it must render the not-recorded notice — that arm is the one that distinguishes a conditional field from a required one, so it is not optional to test. |
| Modified source is valid but never presented as reproducible from the revision alone. | Set `source_dirty` to true in an otherwise valid report and suppress the dirty-source warning. Contract validation must pass and the consumer assertion must fail. |
| A v1.1 consumer's v1.0 path is explicit legacy mode. | Feed the frozen v1.0 scored and abstained fixtures. The reader must show every required unavailable notice, must not create `image` or `axis_definitions` fields, and must not emit a v1.1 report. |
| Schema-valid v1.0 boundary values degrade visibly rather than becoming invented data. | Feed v1.0 fixtures with a generic thumbnail MIME, regex-valid but undecodable thumbnail bytes, null classical axes on an abstained asset, zero, two, four, duplicate, and dangling exemplars. The report remains in legacy mode; affected tiles or values become the exact placeholders above, no unsafe bytes render, and no score or similarity is recomputed. |
| `source` is never an image transport. | Set every v1.1 `source` to an unreachable HTTPS URI while keeping valid inline thumbnails. Rendering must succeed with zero network requests and must use the inline candidate and reference previews. |

### Pre-registered conformance gate

The criterion is fixed before implementation: every generated valid baseline must pass its named
schema and consumer path, and every breaking case above must fail in the stated way. The permitted
counts are zero unexpected accepts, zero unexpected rejects, zero cross-field mismatches, and zero
view-time network requests. One mismatch fails the whole gate. Contract errors cannot be averaged
away because one false acceptance is enough to present a refusal as a number or a distance in the
wrong direction. This is a deterministic conformance gate rather than an empirical product-quality
claim. Any relaxation requires a visible contract decision made before rerunning the changed case.

## Verification

This verification writes under `contract-verification/`, not `results/`. `results/<measurement-id>/
<run-id>/` is [ADR-0009](0009-measurement-and-evaluation-contract.md)'s reserved results namespace
for entries registered in `eval/measurements.json`, bound to a run identifier, dataset digests and a
scientific `PASS`/`FAIL`/`INFORMATIONAL` verdict. This record's gate is a deterministic document-shape
and dispatch conformance check with no dataset row and no scientific verdict, so it does not claim a
`results/` subtree or a measurement identifier.

**Command.** The reproducible command is:

```sh
uv run python eval/verify_report_contract.py \
  --schema-v1-0 moodboard/schema/report_v1_0.schema.json \
  --schema-v1-1 moodboard/schema/report_v1_1.schema.json \
  --seed 20260808 \
  --out contract-verification/report-contract-v1.1 \
  --junit contract-verification/report-contract-v1.1/junit.xml
```

**Data provenance.** `build_contract_inputs(seed)` in `eval/verify_report_contract.py` is the sole
input generator. With literal seed `20260808`, NumPy `PCG64`, and the dimensions fixed in that
function, it writes ten 32 by 32 RGB PNG references under
`contract-verification/report-contract-v1.1/inputs/` and a candidate that is a byte-for-byte copy of the first
reference under a distinct asset id. The same images drive the scored alpha `0.10` path and the
resolution-abstained alpha `0.05` path. The verifier produces one scored and one abstained report
through each version's real typed engine and serializer. It does not hand-write a positive JSON
fixture.

`manifest.json` records the full source revision that generated each baseline. The frozen v1.0
baseline is generated by revision `4c0798240104ca818e8f3b872cfb8022f4a0382a`; that revision belongs
in the manifest because a v1.0 report has no engine source-revision field. The v1.1 baseline itself
also records its engine source revision, schema hash, input hashes, argv, and seed. Every negative
fixture is one named mechanical mutation of a freshly generated positive baseline, and the legacy
boundary fixtures are derived from the v1.0 baseline by the mutations named above. The two alpha
values match the registered ten-reference cases at `eval/thresholds.json:194-207,261-271`. No
external dataset is consumed because the claims are document-shape and dispatch properties, not
model-quality measurements.

**Artifacts.** The command writes the generated PNG inputs under
`contract-verification/report-contract-v1.1/inputs/`, the four valid scored and abstained baseline documents under
`contract-verification/report-contract-v1.1/valid/`, every invalid v1.1 mutation under
`contract-verification/report-contract-v1.1/breaking/`, and the schema-valid v1.0 boundary fixtures under
`contract-verification/report-contract-v1.1/legacy/`. A `manifest.json` contains source revisions, schema hashes,
fixture hashes and expected outcomes; `summary.json` contains the compatibility matrix and case
results; and `junit.xml` contains the same pass-or-fail cases for continuous integration.

**Inspected pass-or-fail output.** A reader inspects `summary.json` and the final standard-output
block. Pass requires the six compatibility-matrix rows to equal the behavior above and the block to
read `unexpected_accepts: 0`, `unexpected_rejects: 0`, `cross_field_mismatches: 0`,
`view_time_network_requests: 0`, and `report_contract_v1.1: PASS`. The command exits nonzero and
prints `report_contract_v1.1: FAIL` when any count differs. A general test-suite success without
these artifacts and named counts does not verify this record.

## Alternatives considered

**Extend ADR-0002 and leave its compatibility paragraph in force.** Rejected. The paragraph
requires unknown-minor tolerance while the implemented schema and tests require exact-version
rejection. Calling this an extension would leave two authoritative answers for a v1.0 consumer.

**Call the additive report version 2.0.** Rejected. Every v1.0 data field keeps its path, type, and
meaning, and a v1.1 consumer deliberately retains a v1.0 decoder. A major number would imply a data
semantic break that this record avoids. The amendment to ADR-0002 is still required because the
compatibility direction changes. The retained report data keeps its meaning.

**Load the candidate from `assets[].source` at view time.** Rejected. Local paths do not survive
attachment to another machine, remote URIs violate the offline boundary, and automatic fetching
turns report viewing into an unrequested network and privacy action.

**Inline every original image.** Rejected. Full-resolution originals can make a report too large to
attach and disclose more user content than the comparison needs. A separately encoded thumbnail
preserves the interaction while content hashes and original dimensions preserve identity. Version
1.1 does not claim a payload reduction until a separate size rule is justified and pre-registered.

**Ship an HTML file beside an image directory.** Rejected. A sidecar bundle can preserve full image
quality, but it breaks ADR-0001's one-file attachment and creates missing-file failure modes. It
becomes reasonable only if that earlier distribution boundary is superseded.

**Replace `axes` scalars with rich objects.** Rejected. Changing the type at an existing path is a
major-version change and breaks the v1.0 decoder retained by the v1.1 reader. The additive
board-level `axis_definitions` array costs one cross-field assertion and keeps the existing
machine-readable values stable without repeating metadata on every asset.

**Hard-code axis labels, directions, methods, and missing-value behavior in the viewer.** Rejected.
The runtime axis vocabulary can shrink, and the viewer would then carry a second definition that can
drift from the engine. The report is the contract precisely so presentation does not invent those
facts.

## Consequences

A conforming v1.1 viewer can show the candidate and its three closest reference previews from one
file. It can distinguish fit p-values from distances, present the one interval the engine actually
estimates, disclose where uncertainty is not estimated, and inspect the complete immutable numeric
fit policy. It can also show the recorded engine source revision and dirty state, model and schema
identities, structured invocation, and image identities behind a report.

The engine and viewer remain coupled through one explicit versioned contract. The additional
cross-field assertions keep the shared definitions and per-asset values coherent. A v1.1 consumer
can still open historical v1.0 reports without fabricating missing information.

All path-based engine and viewer readers enforce one shared 128 MiB report-byte ceiling before
JSON, base64, thumbnail, or schema work. A size preflight avoids opening an already oversized file,
and a bounded `limit + 1` read catches growth after preflight. The ceiling is deliberately above
the defined reference/candidate preview envelope; it is an availability/transport limit and does
not alter any measurement, score, or compatibility meaning in this record.

Reports become larger because every candidate has an inline preview. Shared HTML files now contain
candidate pixels as well as reference pixels, which is a privacy and retention consequence even
though full-resolution originals are not separately required. The existing `source`, `command`, and
new `argv` fields may also reveal local paths or credentials. The report contains image content and
sensitive provenance. It is unsuitable as an anonymous score table.

Writers and readers must maintain two schemas and an explicit v1.0 adapter. Validation becomes more
complex because JSON Schema alone cannot assert image magic bytes, cross-reference resolution,
exemplar order, key-set equality, schema hashes, or duplicated-value equality. Those checks require
a version-specific validator and the regression matrix above.

Existing v1.0 consumers reject v1.1 reports and must be upgraded. Existing v1.0 reports remain
usable, but their candidate preview, source revision, structured invocation, schema hash, and axis
method details are permanently unavailable. A thumbnail supports comparison. Full-resolution
inspection and archival recovery remain unavailable.

## Invalidation conditions

This decision is wrong if ADR-0001's offline, self-contained, single-file boundary is superseded. A
networked viewer or a managed sidecar bundle would change the image transport tradeoff.

It is also wrong if observed viewer use shows that neither a candidate preview nor simultaneous
comparison with three references is required, or if a product privacy rule forbids embedding
candidate pixels in a shareable report. Either finding removes the primary reason for the v1.1
media addition.

Representative payload measurements may show that inline previews make ordinary boards
unsharable. That finding would justify a new transport decision, but the payload and visual-quality
thresholds must be registered before that measurement runs rather than selected from its result.

The axis-detail contract requires amendment if a supported axis cannot be described truthfully as a
conformal p-value with higher fit or a normalized distance for which lower means closer. A
statistically justified classical-axis interval would also require a named method, a pre-registered
verification criterion, and a new report decision before `uncertainty: none` can claim an interval.

Finally, a demonstration that safe partial rendering of an unknown report version is more reliable
than exact version dispatch would invalidate the compatibility policy. Such a demonstration must
include the same malformed-score, abstention, axis-direction, and unknown-field cases in this
record. A reader that merely appears to render a newer report does not thereby show that it
interpreted the statistics correctly.
