# Firefly measured loop

The offline report carries a separate, closed Firefly results bridge. It does not replace or
relabel the Pixel RAG engine artifact: that artifact retains its registered historical outputs,
while this bridge projects the later authenticated-web experiment and its real Khive/Lattice
registrations.

## Recorded byte identities

The projector accepts only these byte identities:

- replacement iteration record: `a55e262a0d2752f7946028248359842864ec26932d66f4e6b76eb2233bf3fce5`
- restyle record: `9d7f75fdc63de6147a97d326dc01b85602e45474fb40c662f8352891bf0129c7`
- Khive ingest/search/restart summary: `86c31855c9c2107d2688a54173434ad17292eb24a86484a613031602d9453abb`

Bridge contract `moodboard.viewer-firefly-measured-loop-bridge.v2` also projects the immutable
apple source as a closed identity: asset `fruit_apple_garden`, SHA-256
`3bda38b4304152f813f6bea37dc236f95670fbea5da4731903d9ce8cfaa8ae23`, BLAKE3/Khive
ContentRef `d9c1a0e3e6a5a72a9da252a0ea9fb4616c9099dd20cdc65ea00ffc29d14f23a8`, 645,201
bytes, 1280×960, JPEG. The projector independently checks the replacement record's full
source identity and the restyle record's source SHA-256 and dimensions before emitting that
shared identity. Neither local source path is copied into the bridge.

The captured generator was **Gemini 2.5 (Nano Banana), a Google partner model served through
Adobe Firefly**. It ran in the user's authenticated Adobe Firefly web Edit surface. “Uses 0
credits” is the UI string captured for this run, not a pricing promise. No native Firefly API and
no premium Gemini 3.1 generation were used.

The replacement timeline keeps three distinct facts visible:

1. A square output was structurally rejected because the source is 4:3.
2. The raw 4:3 generator output failed the preregistered outside-mask SSIM gate:
   `0.174819482254 < 0.95`.
3. The selected Firefly background-removal cutout was deterministically composited only inside
   the allowed region. Its SSIM value of `1.0` remains a historical gate result, while the bridge
   now reports the stronger compositor invariant independently: across all 432,192 protected
   pixels, `changed_pixel_count = 0` and `max_abs_channel_error = 0`. This is **by construction**,
   not proof of intrinsic generator locality or aesthetic quality.

The exact comparison is recomputed during projection from the frozen source JPEG and selected
PNG after decoding both as 1280×960 RGB. It is bound to source SHA-256
`3bda38b4304152f813f6bea37dc236f95670fbea5da4731903d9ce8cfaa8ae23`, selected-output
SHA-256 `53b601c226fa9997fcce2e7e8bfeb80f4a1e6322d25e7d5293ea4436c2c9d35d`, and the
row-major one-byte-per-pixel mask SHA-256
`09f9072f646ef8d99af30736210a57f2de448e8ca90fbff07a07edd7bd5eef4b`. The mask is the
frozen half-open rectangle `[230, 1152) × [48, 912)` in source coordinates, with `1` meaning
inside the editable region and `0` meaning protected. Missing or drifted image bytes, dimensions,
formats, modes, mask semantics, or any nonzero protected-pixel difference abort projection. The
claim is decoded RGB equality outside the mask; it is not whole-file byte equality between JPEG
and PNG, and boundary/seam quality remains unmeasured.

The global restyle remains `not_computed`: its pixel diagnostics are descriptive and do not
validate style, semantics, or user preference. Routed public-domain references informed prompt
wording; neither reference was attached as a generator image input.

All three output byte identities are registered in an isolated Khive namespace with 1024D Qwen
visual descriptors from `lattice-embed` 0.9.0. The first-process and restarted-process search
result files are byte-identical. Khive's default KG text engine may also initialize; the narrower
claim here is specifically the Moodboard visual descriptor attached to these three assets.

## Reproduction boundary

The large source images and original output images remain in the ignored demo cache. The
tracked projector is reviewable and emits bounded deterministic JPEG preview derivatives, each
with its own SHA-256 next to the original registered identity:

```bash
python eval/showcase_firefly_projection.py --write /tmp/firefly-bridge.json
python -m moodboard.firefly_viewer --check /tmp/firefly-bridge.json
npm --prefix viewer run firefly:check
```

The projector refuses to overwrite an existing destination. The viewer build runs the Python
semantic/identity checker before TypeScript and Vite, so a stale or drifted bridge fails closed.
