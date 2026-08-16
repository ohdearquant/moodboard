# ADR-0016: Region, Mask, and Locality Verification

- **Status:** Proposed
- **Date:** 2026-08-16
- **Depends on:** ADR-0012's typed hard-gate semantics and ADR-0014's generic intent packet,
  generation attempt, and output occurrence.
- **Extends:** ADR-0014 by registering `moodboard.operation.localized-edit.v1` and the
  `deterministic_compositor` producer. It does not change ADR-0004's score-abstention rules.
- **Measurable claim:** none. This record defines coordinate, mask, verifier, failure-retention,
  and compositor contracts. It sets no perceptual threshold and makes no generator, boundary, or
  aesthetic-quality claim.

## Context

Localized editing is useful only when “outside the selected region” has one exact meaning. A screen
rectangle without a source-raster identity is ambiguous after resize, orientation, crop, or aspect
ratio change. A verifier value without its mask and operator cannot be recomputed. A source-backed
composite that copies protected pixels answers a different question from a raw generator output
that happens to preserve them.

This record defines the first operation carried by ADR-0014 and keeps those producer claims
separate.

## Decision

### `localized_edit` is the first registered operation kind

The operation replaces or transforms content inside one user-declared region while treating every
pixel outside its canonical mask as protected. Text-to-image, whole-frame restyle, multiple
disjoint regions, video, layered documents, and vector graphics are outside v1.

Its closed packet payload is:

```jsonc
{
  "source_raster": {},
  "region": {},
  "mask": {},
  "raw_diagnostic_verifiers": [],
  "insert_compiler_policy": "raw_crop_nearest.v1",
  "compositor_policy": "source_backed_rect_replace.v1"
}
```

The operation digest is
`sha256(UTF8("moodboard.operation.localized-edit.v1\0") || RFC8785(payload))`. The instruction in
ADR-0014 describes the desired inside-mask change; it cannot loosen this payload. The packet's
top-level `verification_policy.required_verifiers` is the sole acceptance-gate authority. This
operation is valid only when that array contains
`moodboard.verifier.outside-mask-rgb-exact.v1`. `raw_diagnostic_verifiers` may add only registered
diagnostics and can never make them acceptance gates implicitly.

### Source and mask delivery are explicit provider facts

The operation registers two required ADR-0014 `operation_inputs`: `source_image` and
`locality_mask`. The source delivery records `native_input` with exact original bytes or one
`attached_control` derivative with its compiler, source identity, dimensions, and delivered-byte
digest. The mask delivery is exactly one of `native_mask`, `attached_overlay`, `prompt_only`, or
`not_sent`. Native/overlay modes bind exact delivered bytes and a versioned compiler; prompt-only
binds exact mask-derived text in the normalized prompt.

All four mask modes still require post-generation verification. A provider mask parameter or
overlay is request intent, not proof of locality. Studio shows source/mask delivery in its compact
confirmation, and the adapter refuses modes absent from its capability snapshot.

### Coordinates bind to an immutable canonical source raster

Original compressed bytes retain their SHA-256 and ContentRef. The operation separately binds one
canonical raster artifact:

```jsonc
{
  "schema_version": "moodboard.raster.srgb-u8.v1",
  "compiler_revision": "<pinned decoder/color implementation>",
  "width": 1280,
  "height": 960,
  "mode": "RGB",
  "byte_count": 3686400,
  "source_content_sha256": "<original compressed bytes>",
  "raster_sha256": "<domain-separated identity>"
}
```

V1 accepts one opaque still frame. It rejects animation/multiple frames, non-opaque alpha, CMYK,
unsupported ICC profiles, malformed orientation, dimensions outside configured decode bounds, and
any decoder warning classified as unsafe. EXIF orientation is applied once. RGB or grayscale input
with no profile is interpreted as sRGB; a supported embedded profile is converted with the pinned
relative-colorimetric transform. Grayscale is expanded to equal RGB channels. Metadata is absent
from the raster artifact.

The compiler revision fixes decoder build, ICC engine/profile identities, orientation behavior,
rounding, and supported formats. It publishes `width * height * 3` row-major RGB bytes. Raster
identity is:

```text
raster_sha256 = sha256(
  UTF8("moodboard.raster.srgb-u8.v1\0") ||
  RFC8785({compiler_revision,width,height,mode,byte_count,source_content_sha256}) ||
  0x00 || raster_bytes
)
```

Width, height, mode, revision, and source identity therefore participate in the digest. Consumers
validate the published raster rather than independently choosing a decoder. The compiler is
accepted only with cross-language metadata/digest vectors and source-format golden fixtures.

All pixel coordinates use top-left origin. `x` increases rightward and `y` downward. Bounds are
half-open: `[left, right) x [top, bottom)`.

### Compiled integer bounds are authoritative

Studio may preserve normalized display coordinates as non-authoritative UI provenance. Before
confirmation, the backend compiles the selection to integer `left`, `top`, `right`, and `bottom`
bounds and returns the exact mask overlay for inspection. Those integers, not floating-point
coordinates, are authoritative and enter the packet identity.

V1 requires:

```text
0 <= left < right <= source_width
0 <= top < bottom <= source_height
```

The operation records the selection-tool revision and optional normalized strings that produced
the bounds, but no verifier recomputes integer bounds from them. Editing any integer bound creates
a new packet and requires confirmation. This removes cross-language floating-point rounding from
the evidence contract.

### Every region becomes one canonical binary mask

The authoritative mask is a one-byte-per-pixel row-major raster with source-raster dimensions:

- `1` means editable;
- `0` means protected.

V1 fills the authoritative integer rectangle with `1` and every other pixel with `0`. The mask
artifact records width, height, byte count, editable/protected counts, compiler revision, source
raster identity, and mask identity. Any other byte value, dimension/count mismatch, empty editable
set, empty protected set, or digest mismatch is invalid.

```text
mask_sha256 = sha256(
  UTF8("moodboard.mask.u8.v1\0") ||
  RFC8785({compiler_revision,width,height,byte_count,editable_count,
           protected_count,source_raster_sha256}) ||
  0x00 || mask_bytes
)
```

The canonical mask, not a CSS rectangle or thumbnail overlay, is the verifier input. Arbitrary
painted masks require a later authoring contract but may target the same byte-level mask format.

### Raw provider output is retained before any repair

Provider bytes are validated and published under ADR-0014 as a `generator_raw` occurrence before
crop, resize, background removal, color conversion, or source-backed composition.

Structural verification requires a supported single opaque frame, canonical-raster compilation,
and dimensions equal to the source raster. A structural failure is a visible
`constraint_verification: fail`; locality is `not_run` because the rasters are not comparable. The
provider payload and failure receipt remain immutable evidence.

A safe decoded RGB output whose only structural mismatch is source dimensions is ineligible as the
final raw edit but may still feed `raw_crop_nearest.v1` only when ADR-0014 records
`producer_kind: generator_raw` and `admission: eligible`. Undecodable, active, over-limit,
provenance-rejected, wrong-model, or otherwise admission-rejected payloads cannot feed any
selectable descendant. Rejection is transitive through lineage. The insert compiler retains an
eligible raw occurrence's structural/locality failure rather than converting that verdict to pass.

When structure passes, every raw verifier registered in the packet runs against the original raw
occurrence. Each receipt binds source-raster, output-raster, mask, operator revision, complete
parameters, optional threshold, measurements, and verdict. Repair never changes that verdict.

### V1 defines one required locality verifier

`moodboard.verifier.outside-mask-rgb-exact.v1` compares canonical RGB bytes at every mask byte `0`.
It reports
`protected_pixel_count`, `changed_pixel_count`, and `max_abs_channel_error`. Pass requires the count
to match the mask and both changed/error values to be zero. The receipt identity binds both raster
digests, mask digest, verifier revision, and result.

No SSIM operator or default threshold is defined by this ADR. A perceptual raw diagnostic may be
registered only through a separate closed verifier profile that pins channel/data-range handling,
window, weights, protected-set boundary behavior, aggregation, numeric precision, threshold, and
independent golden vectors. Until then Studio may display previously frozen perceptual evidence as
historical artifact data, but cannot create a new v1 SSIM receipt under this record.

`pass`, `fail`, and `not_run` remain separate. `not_run` is never shown with a check mark or counted
as pass.

### Final protected pixels must be exact

A selectable occurrence accepted as a v1 localized edit must pass
`moodboard.verifier.outside-mask-rgb-exact.v1`. A raw generator occurrence may pass directly.
Otherwise the user may create a separate deterministic-compositor occurrence; board compatibility
or preference cannot waive the exact gate.

This proves only protected decoded pixels. It does not prove semantic correctness, boundary
quality, or aesthetic success inside the mask.

### A deterministic compiler produces the compositor insert

`raw_crop_nearest.v1` turns a retained provider output into the one insert accepted by the v1
compositor. It consumes the provider output's canonical RGB raster at its native dimensions, one
user-confirmed half-open integer crop inside that raster, and the authoritative target-region width
and height. It performs no semantic segmentation or automatic foreground selection.

For target coordinate `(tx, ty)`, crop size `(cw, ch)`, and target size `(tw, th)`, it selects:

```text
sx = crop_left + min(cw - 1, floor(((2 * tx + 1) * cw) / (2 * tw)))
sy = crop_top  + min(ch - 1, floor(((2 * ty + 1) * ch) / (2 * th)))
insert[tx, ty] = raw_output[sx, sy]
```

All arithmetic is nonnegative integer arithmetic with a sufficiently wide accumulator. The output
is an opaque RGB insert exactly `(tw, th)`. Its domain-separated identity binds compiler revision,
raw output/raster identities, crop bounds, target dimensions, and row-major output bytes. Changing
the crop produces a new insert occurrence and requires user confirmation. Golden vectors cover
upscale, downscale, one-pixel, odd-ratio, and edge cases.

That post-generation action is frozen first as `moodboard.insert-compile-confirmation.v1`. It binds
the enrolled principal and Studio session, raw output/raster identities, crop bounds, target-region
and compiler-policy identities, the exact preview projection shown, and confirmation time. Its id
is the domain-separated RFC 8785 digest of the document without the id. The insert occurrence must
name that confirmation and match every compiled field. Repeating the same confirmation key returns
the prior artifact; changing the crop or compiler creates a new confirmation and insert.

This compiler intentionally favors a reproducible P0 repair path over sophisticated cutout quality.
An alpha matting, segmentation, resampling, or feathering producer requires a later ADR with its own
lineage and golden vectors; it cannot appear as an implicit preprocessing step.

### The v1 compositor is narrow and byte-deterministic

`source_backed_rect_replace.v1` accepts exactly:

- the canonical source RGB raster;
- one canonical opaque RGB insert from `raw_crop_nearest.v1`;
- the authoritative edit bounds, whose width/height exactly equal the insert dimensions;
- the canonical binary mask; and
- the compositor and canonical PNG encoder revisions.

No additional resize, alpha, warp, feather, color adjustment, or layers are permitted. The edit
bounds must equal the rectangle represented by the v1 mask. For pixel `(x, y)` and channel `c`:

```text
output[x, y, c] = insert[x - left, y - top, c]  if mask[x, y] == 1
                  source[x, y, c]               if mask[x, y] == 0
```

There is one layer and no hidden parameter. The resulting RGB raster is identified under the raster
contract. It is encoded with a versioned lossless PNG profile that fixes RGB8 color type, no
interlace, filter byte zero per row, no ancillary chunks, and a pinned Deflate
implementation/settings identity. Golden fixtures pin both raster and PNG bytes.

The compositor occurrence uses a domain-separated digest of its closed identity projection rather
than a random UUID. It records byte SHA-256, ContentRef, output-raster identity, producer/encoder
revisions, and lineage to source, eligible generator raw, insert, mask, and packet. Its identity
projection binds those inputs and the exact output. Exact replay returns the existing occurrence;
conflicting bytes fail. It never replaces the raw generator occurrence and runs
`moodboard.verifier.outside-mask-rgb-exact.v1` immediately. Any protected difference is a producer
failure.

A passing composed result is described as:

> The compositor enforces preservation separately from the generator.

It is never described as intrinsic generator locality.

### Failure remains useful evidence

Studio presents separate occurrences and decisions:

```text
raw generator output -> structural/locality result
    if failed and the user continues:
confirmed raw crop -> deterministic RGB insert -> source-backed composite -> exact locality result
```

A failed raw occurrence remains inspectable. It is not deleted, relabeled as an intermediate
success, or hidden behind the composite. Retries create new occurrences and never mutate prior
verdicts.

### Locality is one gate, not total edit quality

The locality receipt makes no claim about:

- semantic correctness inside the mask;
- visual quality, board compatibility, or human preference;
- boundary seams, halos, lighting, perspective, or realism;
- whether the provider understood the instruction;
- compressed source/output file-byte identity; or
- provider capability beyond the measured occurrence.

Board compatibility may run after required gates pass. ADR-0015 additionally requires passed hard
gates and a scored frozen feature row before an occurrence can enter preference training. Failed
outputs may be inspected or placed in a separately labelled debug comparison, but never admitted
to the human preference corpus. An explicit acceptance event remains necessary for the north-star
metric.

### UI wording is tied to the producer

For a raw pass, Studio may say **Raw output passed the declared outside-mask rule**. For a raw fail,
it says **Raw output changed protected pixels** or names a separately registered perceptual rule.
For a composed pass, it says **Protected pixels are exact because the source-backed compositor
restored them**.

The compact view shows source, mask overlay, raw output/verdict, and selected final occurrence.
Operator details, counts, digests, and lineage are one disclosure away.

## Alternatives considered

**Trust the provider's mask parameter.** Rejected. A request parameter is intent, not evidence of
what returned pixels did.

**Verify only the final selected image.** Rejected. It erases whether preservation came from the
generator or compositor and hides failed paid attempts.

**Define SSIM 0.95 as a universal default.** Rejected. A product threshold needs a registered task
set, error costs, and protocol. This record defines no perceptual operator or threshold.

**Treat a composite pass as generator locality.** Rejected. Protected pixels are copied from the
source by a different producer.

**Use browser coordinates as the mask contract.** Rejected. Layout, zoom, device scale, and image
orientation make them nonportable. Integer source-raster bounds are authoritative.

**Feather outside the mask.** Rejected for v1. A future operation may declare a soft transition
band as editable; this one does not quietly weaken its protected region.

## Consequences

The final selected localized edit either preserves all protected canonical pixels or it does not.
Raw provider behavior remains separately inspectable.

Strict preservation may expose seams and reject edits that need global relighting. Those tasks need
a different operation policy. The narrow compositor gives up implicit resizing and feathering in
exchange for reproducible bytes and an exact claim.

No Khive or Lattice change is required. Moodboard stores bytes with existing asset identities and
computes locality in its application verifier. A shared server-side verifier would need a separate
owning-repository ADR.

## Acceptance conditions

This record remains Proposed until:

1. operation, operation-input delivery, source-raster, insert-raster, mask, exact-verifier,
   compositor, and encoder schemas are closed and versioned;
2. source/raster compilers reject unsupported frame, alpha, ICC, orientation, and dimension cases,
   and golden fixtures pin accepted conversions;
3. raster and mask domain-separated identities detect revision, shape, mode, source, count, and
   byte drift;
4. integer bounds are authoritative, UI overlays round-trip them, and normalized provenance never
   drives verification;
5. raw bytes are durably identified before any transform and structural failure makes locality
   `not_run`;
6. exact-verifier tests detect one changed protected channel and pass only with zero change/error;
7. any perceptual verifier is rejected unless separately registered with a complete operator and
   threshold contract;
8. compile-confirmation, insert/compiler, and compositor tests pin principal/time/projection,
   crop mapping, per-pixel replacement, confinement, raster identity, encoded PNG bytes,
   deterministic replay identity, and transitive rejection of any non-eligible provider ancestor;
9. UI tests distinguish raw-generator and compositor verdicts and retain every non-claim; and
10. failed required locality cannot be overridden by board compatibility, preference, or
    acceptance, while the end-to-end artifact retains every input, occurrence, and receipt.
