# Firefly measured loop

The offline report carries a separate, closed Firefly evidence bridge. It does not replace or
relabel the Pixel RAG engine artifact: that artifact retains its registered historical outputs,
while this bridge projects the later authenticated-web experiment and its real Khive/Lattice
registrations.

## Frozen evidence

The projector accepts only these byte identities:

- replacement iteration evidence: `a55e262a0d2752f7946028248359842864ec26932d66f4e6b76eb2233bf3fce5`
- restyle evidence: `9d7f75fdc63de6147a97d326dc01b85602e45474fb40c662f8352891bf0129c7`
- Khive ingest/search/restart summary: `86c31855c9c2107d2688a54173434ad17292eb24a86484a613031602d9453abb`

The captured generator was **Gemini 2.5 (Nano Banana), a Google partner model served through
Adobe Firefly**. It ran in the user's authenticated Adobe Firefly web Edit surface. “Uses 0
credits” is the UI string captured for this run, not a pricing promise. No native Firefly API and
no premium Gemini 3.1 generation were used.

The replacement timeline keeps three distinct facts visible:

1. A square output was structurally rejected because the governed source is 4:3.
2. The raw 4:3 generator output failed the preregistered outside-mask SSIM gate:
   `0.174819482254 < 0.95`.
3. The selected Firefly background-removal cutout was deterministically composited only inside
   the allowed region. Outside pixels come from the source, so its `1.0` locality pass is
   **by construction**, not evidence of intrinsic generator locality or aesthetic quality.

The global restyle remains `not_computed`: its pixel diagnostics are descriptive and do not
validate style, semantics, or user preference. Routed public-domain references informed prompt
wording; neither reference was attached as a generator image input.

All three output byte identities are registered in an isolated Khive namespace with 1024D Qwen
visual descriptors from `lattice-embed` 0.9.0. The first-process and restarted-process search
result files are byte-identical. Khive's default KG text engine may also initialize; the narrower
claim here is specifically the Moodboard visual descriptor attached to these three assets.

## Reproduction boundary

The large source evidence and original output images remain in the ignored demo cache. The
tracked projector is reviewable and emits bounded deterministic JPEG preview derivatives, each
with its own SHA-256 next to the original registered identity:

```bash
python eval/adobe_demo_firefly_projection.py --write /tmp/firefly-bridge.json
python -m moodboard.firefly_viewer --check /tmp/firefly-bridge.json
npm --prefix viewer run firefly:check
```

The projector refuses to overwrite an existing destination. The viewer build runs the Python
semantic/identity checker before TypeScript and Vite, so a stale or drifted bridge fails closed.
