#!/usr/bin/env python3
"""Fetch PACS and build its manifest.

    uv run --with pyarrow datasets/pacs/fetch.py

PACS crosses four domains (photo, art painting, cartoon, sketch) with seven
object classes. That crossing is why this project uses it: domain stands for
style, class stands for content, every combination is present, so both families
of pairs the content-invariance test needs can be built without hand labelling.

ACQUISITION ROUTE, and why it is not the one this project first recorded.

The route named in the original protocol was the download helper in the
DomainBed benchmark suite, which fetches a Google Drive object. Checked on
2026-08-07 it returns HTTP 404 with a 1,652 byte HTML error page. The paper's
own project page returns 404 as well, and the lab's download site did not
resolve on the same check. All three were probed with a reachable control in the
same call, so this is the routes being dead rather than the network being down.

So the route used here is a mirror on the Hugging Face hub, pinned to an exact
revision and an exact file checksum. That is a weaker provenance story than the
original release and the LICENCE.md beside this file says so plainly rather than
inheriting a claim the mirror does not make.

The counts below are asserted, not printed. A file can carry the right checksum
and still be the wrong dataset for this protocol if its domain or class
vocabulary differs, and a printed count is a line a reader skims.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _common import Expected, FetchError, download, write_checksums, write_manifest  # noqa: E402

HERE = Path(__file__).resolve().parent
RAW = HERE.parent / "_raw"

REPO = "flwrlabs/pacs"
REVISION = "394113073258ead631f617d2e13bb377c0715c4b"
SHARD = "data/train-00000-of-00001.parquet"
URL = f"https://huggingface.co/datasets/{REPO}/resolve/{REVISION}/{SHARD}"

EXPECTED = Expected(
    sha256="4fc041ee92eec6043fe6e2859e8bdd138e5f958bc621afd153879812cbe65ff5",
    size=191_395_900,
    magic=b"PAR1",
)

# From the dataset card, which is the expectation source for these numbers.
# The card's prose sentence lists only six classes and omits "house"; its
# machine-readable label map lists seven and is what is used here. Where a
# source disagrees with itself, the structured field wins and the disagreement
# gets written down.
EXPECTED_TOTAL = 9_991
EXPECTED_DOMAINS = {
    "photo": 1_670,
    "art_painting": 2_048,
    "cartoon": 2_344,
    "sketch": 3_929,
}
EXPECTED_LABELS = {
    0: "dog", 1: "elephant", 2: "giraffe", 3: "guitar",
    4: "horse", 5: "house", 6: "person",
}


def main() -> int:
    import hashlib

    import pyarrow.parquet as pq

    print(f"PACS <- {REPO} @ {REVISION[:12]}")
    local = RAW / "pacs-train-00000.parquet"
    download(URL, local, EXPECTED)
    print(f"  verified {local.stat().st_size:,} bytes")

    table = pq.read_table(local, columns=["image", "domain", "label"])
    n = table.num_rows
    if n != EXPECTED_TOTAL:
        raise FetchError(f"expected {EXPECTED_TOTAL} rows, parquet has {n}")

    images = table.column("image").to_pylist()
    domains = table.column("domain").to_pylist()
    labels = table.column("label").to_pylist()

    rows, seen = [], set()
    for i, (img, dom, lab) in enumerate(zip(images, domains, labels)):
        blob = img["bytes"] if isinstance(img, dict) else img
        if not blob:
            raise FetchError(f"row {i} carries no image bytes")
        digest = hashlib.sha256(blob).hexdigest()
        rows.append(
            {
                "id": f"pacs-{i:05d}",
                "sha256": digest,
                "style_group": dom,          # the protocol's style factor
                "content_group": EXPECTED_LABELS[lab],  # the protocol's content factor
                "source_path": (img.get("path") if isinstance(img, dict) else None),
                "bytes": len(blob),
            }
        )
        seen.add(digest)

    by_domain = Counter(r["style_group"] for r in rows)
    if dict(by_domain) != EXPECTED_DOMAINS:
        raise FetchError(
            "domain split does not match the published one.\n"
            f"  published: {EXPECTED_DOMAINS}\n"
            f"  found:     {dict(by_domain)}\n"
            "The checksum matched, so this is the file that was pinned. A "
            "different split means the published description is wrong, which "
            "has to be resolved before any measurement is taken on it."
        )

    by_label = Counter(r["content_group"] for r in rows)
    if set(by_label) != set(EXPECTED_LABELS.values()):
        raise FetchError(f"class vocabulary is {sorted(by_label)}")

    # Exact duplicates across domains would inflate the cross-style family with
    # pairs that are trivially identical, so the count is recorded rather than
    # discovered later. It is reported, not fatal: PACS is assembled from
    # overlapping public sources and some duplication is expected.
    dupes = n - len(seen)

    manifest = HERE / "manifest.jsonl"
    written = write_manifest(rows, manifest)
    write_checksums([manifest], HERE / "checksums.sha256", relative_to=HERE)

    print(f"  manifest: {written} rows")
    print(f"  style groups:   {dict(sorted(by_domain.items()))}")
    print(f"  content groups: {dict(sorted(by_label.items()))}")
    print(f"  exact-duplicate images: {dupes}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except FetchError as exc:
        print(f"\nFETCH FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
