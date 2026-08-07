"""Shared plumbing for the dataset fetch scripts.

Every dataset in this repository ships a manifest, a checksum file and a fetch
script, and never the image files themselves. This module holds the three things
all of those scripts need: a download that survives a transient network failure,
a verification step that refuses to return a partial or substituted file, and
the manifest and checksum writers.

The verification is the point. Both acquisition routes originally recorded for
PACS now return a 404 page, and a 404 page is a perfectly valid file: it
downloads without error, it has a plausible size, and only its content says it is
wrong. A fetch script that checks the exit code and moves on will happily hand a
1,652 byte error page to a preparation step and report success.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Sequence

CHUNK = 1 << 20


class FetchError(RuntimeError):
    """Raised when a source cannot be obtained or does not verify.

    Always fatal. There is no partial-set path: a dataset that half arrived is
    not a smaller dataset, it is an unknown one, and every measurement taken on
    it would be unreproducible in a way nothing downstream could detect.
    """


@dataclass(frozen=True)
class Expected:
    """What a downloaded file has to be before anything may use it."""

    sha256: str | None = None
    size: int | None = None
    # Rejects an error page served with a 200. Checked before the hash so the
    # failure message can say what arrived rather than only that it mismatched.
    magic: bytes | None = None


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def verify(path: Path, expected: Expected) -> str:
    """Check a downloaded file against what was expected. Raise, or return its hash."""
    if not path.exists():
        raise FetchError(f"{path} does not exist after download")

    actual_size = path.stat().st_size
    if actual_size == 0:
        raise FetchError(f"{path} is empty")

    if expected.magic is not None:
        with path.open("rb") as fh:
            head = fh.read(len(expected.magic))
        if head != expected.magic:
            preview = head[:64]
            raise FetchError(
                f"{path} does not start with the expected bytes.\n"
                f"  expected: {expected.magic!r}\n"
                f"  found:    {preview!r}\n"
                "This is what a 404 page or a login redirect looks like when it "
                "is saved to disk under the name you asked for."
            )

    if expected.size is not None and actual_size != expected.size:
        raise FetchError(
            f"{path} is {actual_size} bytes, expected {expected.size}. "
            "A short file is usually a truncated transfer; a long one usually "
            "means the source changed."
        )

    actual_sha = sha256_file(path)
    if expected.sha256 is not None and actual_sha != expected.sha256:
        raise FetchError(
            f"{path} checksum mismatch.\n"
            f"  expected: {expected.sha256}\n"
            f"  found:    {actual_sha}\n"
            "The source has changed since this pin was recorded. Do not paper "
            "over this by updating the pin: find out what changed first, because "
            "every measurement committed under the old pin was taken on "
            "different data."
        )
    return actual_sha


def download(url: str, dest: Path, expected: Expected, *, attempts: int = 6) -> str:
    """Download `url` to `dest`, resuming and retrying, then verify or raise.

    Retries are not optional politeness. A single-shot download of this file
    failed once here after 5 ms with a connection error, at a moment when the
    host was demonstrably reachable on the retry. A fetch script without retries
    turns a blip into a dead-source report, and a dead-source report is the kind
    of finding that gets believed and written down.
    """
    if shutil.which("curl") is None:
        raise FetchError("curl is required and was not found on PATH")

    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        try:
            existing = verify(dest, expected)
        except FetchError:
            pass  # partial or stale, fall through and re-fetch
        else:
            print(f"  already present and verified: {dest.name}")
            return existing

    last: str = ""
    for attempt in range(1, attempts + 1):
        proc = subprocess.run(
            [
                "curl", "-sS", "-L",
                "--retry", "4", "--retry-all-errors", "--retry-delay", "3",
                "-C", "-", "--max-time", "3600",
                "-o", str(dest), url,
            ],
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0:
            break
        last = (proc.stderr or "").strip()
        print(f"  attempt {attempt}/{attempts} failed: {last}", file=sys.stderr)
        if attempt < attempts:
            time.sleep(3 * attempt)
    else:
        raise FetchError(f"could not download {url} after {attempts} attempts: {last}")

    return verify(dest, expected)


def write_manifest(rows: Iterable[dict], path: Path) -> int:
    """Write one JSON object per line, key-sorted so the file is diffable.

    Returns the row count. Callers assert on it: a manifest builder that silently
    produced nothing is the single most likely way this pipeline goes wrong,
    because an empty manifest propagates as a clean run with no measurements in
    it rather than as an error.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
            n += 1
    if n == 0:
        path.unlink(missing_ok=True)
        raise FetchError(f"refusing to write an empty manifest at {path}")
    return n


def _checksum_lines(files: Sequence[Path], *, relative_to: Path) -> str:
    lines = [f"{sha256_file(f)}  {f.relative_to(relative_to)}" for f in sorted(files)]
    return "\n".join(lines) + "\n"


def write_checksums(files: Sequence[Path], path: Path, *, relative_to: Path) -> None:
    """Write a sha256sum-format file, so it can be checked without this repo.

    MAINTAINER COMMAND ONLY. A fetch script must call verify_checksums instead.
    Writing here during an ordinary fetch is what makes the reproducibility claim
    vacuous: the rebuilt manifest overwrites the expectation with its own hash, so
    a drifted upstream produces a green run and a modified committed file rather
    than the loud failure DATASETS.md promises.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_checksum_lines(files, relative_to=relative_to))


def verify_checksums(files: Sequence[Path], path: Path, *, relative_to: Path) -> None:
    """Compare rebuilt outputs against the committed expectation, and fail loudly.

    This is the check the repository's central reproducibility claim rests on:
    "anyone rebuilding gets exactly the same thing, and it fails loudly when they
    do not". Nothing was performing it. A missing expectation file is also fatal
    rather than an invitation to create one, because a fetch that writes the file
    it is supposed to be checked against cannot fail.
    """
    rebuilt = _checksum_lines(files, relative_to=relative_to)
    if not path.exists():
        raise FetchError(
            f"no committed checksum at {path}. The expectation is what makes a "
            "rebuild checkable; generate it deliberately with --write-checksums "
            "and commit it, rather than having a fetch create its own."
        )
    committed = path.read_text()
    if committed == rebuilt:
        return

    want = dict(reversed(ln.split("  ", 1)) for ln in committed.splitlines() if ln)
    got = dict(reversed(ln.split("  ", 1)) for ln in rebuilt.splitlines() if ln)
    detail = []
    for name in sorted(set(want) | set(got)):
        if want.get(name) != got.get(name):
            detail.append(f"  {name}\n    committed: {want.get(name, '(absent)')}"
                          f"\n    rebuilt:   {got.get(name, '(absent)')}")
    raise FetchError(
        "rebuilt output does not match the committed checksum.\n"
        + "\n".join(detail)
        + "\n\nThe upstream source, this script, or the protocol changed. Resolve "
        "which before taking any measurement on this set. If the change is "
        "intended, re-run with --write-checksums and commit that in its own "
        "commit saying what moved."
    )


def read_manifest(path: Path) -> Iterator[dict]:
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)
