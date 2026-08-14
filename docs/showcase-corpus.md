# Governed public-domain visual corpus

The visual-evidence workflow uses a reviewed 15-image corpus, not PACS, WikiArt, search-result
thumbnails, or unverified web images. The source catalogue is
[`moodboard/demo_sources_v1.json`](../moodboard/demo_sources_v1.json): three apple photographs,
four lemon photographs, four Claude Lorrain paintings, and four Vincent van Gogh paintings.
The photographs come from Wikimedia Commons; the paintings and their object metadata come
from The Metropolitan Museum of Art Open Access API.

This is a small integration and retrieval corpus. It is not evidence for broad aesthetic
accuracy, artist attribution, or production ranking quality.

## Acquire one immutable run

```bash
uv run python -m moodboard.demo_data \
  --catalog moodboard/demo_sources_v1.json \
  --output .cache/showcase-public-domain-v1/run-20260812 \
  --retrieved-at 2026-08-12T17:00:00Z
```

Omit `--retrieved-at` for the current UTC second. Supplying it makes a rehearsed run exactly
replayable. The output directory must not exist: acquisition never replaces an earlier run.
Images remain under the gitignored `.cache/` tree and are never committed.

The command queries Wikimedia Commons `imageinfo.extmetadata` or the Met object API before
downloading each image. It fails closed when a page/object id, source page, title, artist,
public-domain flag, licence identity, or image URL differs from the reviewed catalogue. It
also rejects non-HTTPS sources, redirects outside the provider, non-image responses, invalid
image bytes, dimensions over 8,192 pixels on a side or 40 million pixels total, and responses
over 24 MiB.

All work happens in a sibling staging directory. Only after all 15 items pass does one rename
publish the directory containing:

- `assets/`: bounded source images named by stable asset id;
- `manifest.json`: canonical UTF-8 JSON following
  [`demo_manifest_v1.schema.json`](../moodboard/schema/demo_manifest_v1.schema.json);
- `manifest.sha256`: SHA-256 of the exact manifest bytes.

Every manifest asset carries its source and final download URLs, source page, provider object
id where applicable, title, artist, licence id/URL/public-domain evidence, retrieval time,
optional ETag and Last-Modified value, byte count and image dimensions, SHA-256, and Khive
BlobStore v1 BLAKE3-256 `content_ref`. ETag is evidence when the server supplies it; a missing
ETag is represented as `null`, never invented. The content digests remain authoritative.

The Met API can return unrelated measurement arrays in different orders on consecutive
requests. The manifest hashes only the closed metadata projection that acquisition validates,
so irrelevant provider ordering cannot move an otherwise identical locked run. A changed
governed field or image byte still changes the manifest or blocks acquisition.

## Licence boundary

The seven Wikimedia items are either marked `Public domain` (PDM 1.0) or `CC0` by official
Commons metadata. The eight Met objects have `isPublicDomain: true` and are used under the
Met Open Access/CC0 boundary. The catalogue records the exact API evidence expected for every
item. Although these sources permit reuse, repository policy keeps downloaded image bytes and
run manifests local; only the acquisition recipe and public source catalogue are committed.
