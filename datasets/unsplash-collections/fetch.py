#!/usr/bin/env python3
"""Fetch the Unsplash Lite metadata and build the brand-photography manifest.

    uv run datasets/unsplash-collections/fetch.py

This is the dataset behind the second content-invariance measurement, the one
that matters most: the first is run on domains that differ by medium, and a
photograph is never confused with a sketch. Commercial photography styles differ
by lighting, grade, grain and framing, which is a far smaller signal, and a
representation can pass the coarse test and be useless here.

WHAT THIS SCRIPT DOWNLOADS: metadata only. Unsplash permits commercial and
non-commercial use of the photos and is explicit that the dataset "cannot be
used to redistribute the images contained within". So the manifest carries the
photo id and its URL, and images are fetched at preparation time. See
LICENCE.md.

THE STYLE GROUPING IS THE PHOTOGRAPHER, NOT THE COLLECTION, and that is a
correction to this project's first protocol. The reasoning and the measurements
that forced it are in DATASETS.md; the short version is that collection
membership was assumed to be a human grouping of a coherent look, and the data
does not support that. Both groupings are written to the manifest so the
question stays re-openable without another download.
"""

from __future__ import annotations

import collections
import csv
import subprocess
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _common import Expected, FetchError, download, write_checksums, write_manifest  # noqa: E402

HERE = Path(__file__).resolve().parent
RAW = HERE.parent / "_raw"
WORK = RAW / "unsplash"

URL = "https://unsplash.com/data/lite/latest"

# The URL is a moving target: "latest" is republished periodically. The pin below
# is the release this project measured. A mismatch is not a broken download, it
# means upstream published a new release, and it is fatal on purpose — a
# measurement taken on one release is not comparable to one taken on another,
# and the failure has to be someone's decision rather than a silent re-baseline.
EXPECTED = Expected(
    sha256="aa0fcb859040ed64e93817d1d878d0c6f861763283261ba1a6aa5d8d4af6aec6",
    size=320_024_071,
    magic=b"PK\x03\x04",
)
RELEASE_NOTE = "release dated 2026-06-25, obtained 2026-08-07"

NEEDED = ["photos.tsv000", "collections.tsv000", "keywords.tsv000", "TERMS.md"]

# A group is subject-dominated when some keyword held by at least half its
# members appears at more than this multiple of its corpus rate. Lift is
# measured against EVERY keyword, deliberately.
#
# The obvious filter — highest within-group share — does not work here. Unsplash
# tags are dense, 36 per photo on average, and the corpus is nature-heavy:
# "nature" sits on 70.9% of all photos and "outdoors" on 66.2%. Raw share
# therefore measures corpus ubiquity rather than subject concentration and
# rejects every group, good ones included.
#
# Lift fixes that by itself and needs no help. A keyword on 70.9% of the corpus
# cannot reach 3x without appearing on 213% of a group, so ubiquitous tags are
# structurally incapable of flagging anything. An earlier version of this file
# ALSO screened candidate keywords by a 5% ubiquity cutoff, which was redundant
# against lift and actively harmful: it discarded real signal in the band just
# above the cutoff and admitted 73 extra groups, among them a photographer whose
# work is 78% "wallpaper" against a 6.7% corpus rate — an 11.6x concentration,
# waved through as subject-diverse. The cutoff survives below for choosing which
# keywords to RECORD as a photo's subject, where dropping "nature" is right
# because it carries no information. Two uses, two treatments.
RECORD_UBIQUITY_CUTOFF = 0.05

MIN_SHARE = 0.50
MAX_LIFT = 3.0
MIN_GROUP = 15


def _read_tsv(path: Path):
    with path.open(newline="", encoding="utf-8") as fh:
        yield from csv.DictReader(fh, delimiter="\t")


def main() -> int:
    csv.field_size_limit(10**9)
    archive = RAW / "unsplash-lite-latest.zip"
    print(f"Unsplash Lite <- {URL}  ({RELEASE_NOTE})")
    download(URL, archive, EXPECTED)
    print(f"  verified {archive.stat().st_size:,} bytes")

    WORK.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        present = set(zf.namelist())
        missing = [n for n in NEEDED if n not in present]
        if missing:
            raise FetchError(f"archive is missing {missing}; layout changed")
        for name in NEEDED:
            if not (WORK / name).exists():
                zf.extract(name, WORK)

    photos = {r["photo_id"]: r for r in _read_tsv(WORK / "photos.tsv000")}
    if not photos:
        raise FetchError("photos.tsv000 parsed to zero rows")
    n_photos = len(photos)
    print(f"  photos: {n_photos:,}")

    kw: dict[str, set[str]] = collections.defaultdict(set)
    df: collections.Counter = collections.Counter()
    for r in _read_tsv(WORK / "keywords.tsv000"):
        pid = r["photo_id"]
        if pid not in photos:
            continue
        k = (r.get("keyword") or "").strip().lower()
        if k:
            kw[pid].add(k)
            df[k] += 1
    if not df:
        raise FetchError("keywords.tsv000 yielded no keywords for these photos")
    base = {k: c / n_photos for k, c in df.items()}
    discriminative = {k for k, rate in base.items() if rate < RECORD_UBIQUITY_CUTOFF}
    print(f"  keywords: {len(df):,} distinct, {len(discriminative):,} recordable "
          f"(below the {RECORD_UBIQUITY_CUTOFF:.0%} ubiquity cutoff)")

    in_collections: dict[str, list[str]] = collections.defaultdict(list)
    coll_title: dict[str, str] = {}
    for r in _read_tsv(WORK / "collections.tsv000"):
        pid = r["photo_id"]
        if pid in photos and r.get("collection_type") == "collection":
            in_collections[pid].append(r["collection_id"])
            coll_title[r["collection_id"]] = r.get("collection_title", "")

    by_photographer: dict[str, list[str]] = collections.defaultdict(list)
    for pid, r in photos.items():
        u = (r.get("photographer_username") or "").strip()
        if u:
            by_photographer[u].append(pid)

    def subject_lift(members: list[str]) -> tuple[float, str | None]:
        cnt: collections.Counter = collections.Counter()
        for p in members:
            cnt.update(kw.get(p, ()))
        best, name = 0.0, None
        for k, c in cnt.items():
            if c / len(members) < MIN_SHARE:
                continue
            lift = (c / len(members)) / max(base.get(k, 1e-9), 1e-9)
            if lift > best:
                best, name = lift, k
        return best, name

    eligible = {u: m for u, m in by_photographer.items() if len(m) >= MIN_GROUP}
    usable: dict[str, list[str]] = {}
    for u, m in eligible.items():
        # A group carrying no keywords at all is EXCLUDED, not passed. Scoring it
        # zero and admitting it is how the earlier collection-based filter let
        # junk through: no measurable subject read as subject diversity, and the
        # top scorers were collections named "Misc", "Ideas" and "Objects".
        if not any(kw.get(p) for p in m):
            continue
        lift, _ = subject_lift(m)
        if lift < MAX_LIFT:
            usable[u] = m

    print(f"  photographers: {len(by_photographer):,} total, "
          f"{len(eligible)} with >={MIN_GROUP} photos, "
          f"{len(usable)} passing the <{MAX_LIFT}x subject-lift filter")
    if len(usable) < 20:
        raise FetchError(
            f"only {len(usable)} usable style groups; the brand measurement needs "
            "enough groups to build boards and hold out members"
        )

    rows = []
    for u, members in sorted(usable.items()):
        for pid in sorted(members):
            r = photos[pid]
            subject = sorted(k for k in kw.get(pid, ()) if k in discriminative)
            rows.append(
                {
                    "id": pid,
                    "image_url": r.get("photo_image_url", ""),
                    "style_group": u,                       # photographer
                    "content_keywords": subject,
                    "content_group": subject[0] if subject else None,
                    "collections": sorted(in_collections.get(pid, [])),
                    "width": r.get("photo_width"),
                    "height": r.get("photo_height"),
                    "camera_make": r.get("exif_camera_make") or None,
                    "camera_model": r.get("exif_camera_model") or None,
                }
            )

    manifest = HERE / "manifest.jsonl"
    written = write_manifest(rows, manifest)
    write_checksums([manifest], HERE / "checksums.sha256", relative_to=HERE)

    sizes = sorted((len(m) for m in usable.values()), reverse=True)
    print(f"  manifest: {written:,} rows across {len(usable)} style groups "
          f"(largest {sizes[0]}, median {sizes[len(sizes) // 2]}, smallest {sizes[-1]})")
    print(f"  images are NOT downloaded here; see LICENCE.md")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except FetchError as exc:
        print(f"\nFETCH FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
