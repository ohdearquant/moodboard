"""The command line surface: build, rank, retrieve, and report.

This module is the only one that sees the whole engine, and it introduces no new type. It
loads images, calls `encoders.py` to embed them, `conformal.py` to partition and score,
`abstain.py` to decide whether a score is honest, `board.py` to write and read the `brand.mb`
artifact, and `report.py` to validate and write the JSON document. Everything it emits is a
type defined in `report.py`.

`report --html` validates the named report contract and the manifest-owned viewer package, then
atomically publishes one self-contained offline HTML artifact. The browser bundle remains a
separate build artifact under ADR-0001; this module only invokes its verified Python inliner.

Decisions this layer had to make, because no record makes them
--------------------------------------------------------------

**`rank` needs the reference directory as well as the board.** ADR-0002 requires the report
to carry a reference catalogue with an inline thumbnail per reference, so a viewer with no
network access can show the images a score is closest to. A `brand.mb` carries embeddings and
content hashes, not pixels, so a report cannot be assembled from the board alone. `rank`
therefore takes `-r/--references` and verifies it against the board: the reference ids and
content hashes it reads must equal the board's own, in order, or the run stops. That turns
the extra argument into a checked input rather than an unchecked assumption, and it also
catches the case of a board scored against a directory that has since changed.

**`n_local` is the number of references in the candidate's category, without the candidate.**
`INTERFACES.md` pinned it as `len(candidate_category_members) + 1` until that document was
corrected on 2026-08-08 to match this module. That form was not adopted here, for the same
reason `abstain.py` did not adopt it: the report schema states the score
is `(1 + count) / (n_local + 1)`, `conformal_p_value` against `m` references returns
`(1 + count) / (m + 1)`, and ADR-0004's own worked arithmetic reads `1/(8+1) = 0.111` for a
sub-look of eight members. All three agree that `n_local` is the reference count. The pinned
`+ 1` would make the report's stated `n_local` disagree with the denominator of the score
printed beside it.

**Where the fitting parameters come from.** The interval level is read from
`eval/thresholds.json` (`interval_coverage.stated_level`). The cluster cut, the duplicate
cut, the neighbourhood cap and the minimum category size are not in that file, so each is
read from an optional `fit` object there if present and otherwise taken from the record that
states it, with the source printed at run time beside the value. This follows the arrangement
`abstain.py` already uses for the Tukey multiplier. A number whose source cannot be named is
worse than a number in the wrong place. Build resolves these values once and stores the complete
numeric policy in `brand.mb`; rank consumes the board policy and never reloads mutable defaults.

**The classical axis values on an asset are distances to that asset's exemplars.** ADR-0003
defines the three axes as distances between two images and says nothing about how an asset
relates to a set of references on them. Averaging over the whole board would make the number
insensitive at any realistic board size and would cost one axis computation per reference per
asset. The exemplars are the references the report already names as the explanation, so
`axes["palette"]` is the mean palette distance from the asset to exactly the references the
viewer will show beside it, and a reader can check it by eye.

**The far-outlier rule reads the board-wide augmented bag.** The score is computed inside the
candidate's category, per ADR-0004 rule 2, but rule 3 asks whether the candidate is anything
like the references at all, which is a question about the whole board. So the nonconformity
values passed to `check_far_outlier` come from the augmented bag of every reference plus the
candidate, which is what that argument is named for.

**`board.categories` describes one partition, and says so when there is more than one.** The
partition is a function of the augmented bag, so every candidate induces its own. In practice
every candidate induces the same reference-side partition; when they do not, the board lists
the first candidate's view and `board_stats.flags` carries
`category_partition_varies_by_candidate`.

**`board.supported_alpha` is the coarsest floor any asset in the report needed.** A single
board field cannot carry one floor per asset, and the useful reading is the request at which
no asset in this report would have been refused for resolution.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import math
import shlex
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _distribution_version
from pathlib import Path

import numpy as np
from jsonschema import ValidationError as JsonSchemaValidationError
from PIL import Image

from . import axes as axes_module
from .abstain import (
    AbstentionVerdict,
    category_n_eff,
    evaluate_abstention,
    load_abstention_thresholds,
)
from .board import (
    DEFAULT_FAR_OUTLIER_IQR_MULTIPLIER,
    DEFAULT_FAR_OUTLIER_IQR_MULTIPLIER_SOURCE,
    BrandBoard,
    ReferenceAssetLocation,
    build_board,
    read_board,
    write_board,
)
from .conformal import (
    CategoryPartition,
    conformal_p_value,
    duplicate_groups,
    kish_n_eff,
    loo_jackknife_plus_interval,
    nonconformity_scores,
    paired_score_difference_interval,
    partition_categories,
)
from .encoders import (
    KHIVE_REQUEST_MAX_BYTES,
    KHIVE_REQUEST_MAX_IMAGES,
    KHIVE_VISUAL_MATTE_RGB,
    ClassicalEncoder,
    Encoder,
    KhiveAsset,
    KhiveLatticeEncoder,
)
from .khive import KhiveClient
from .preference import (
    PreferenceCandidate,
    PreferenceFeatureArtifact,
    build_preference_features,
    write_preference_feature_artifact,
)
from .report import (
    AXIS_ORDER,
    SCHEMA_PATH_V1_1,
    SCHEMA_VERSION_V1_1,
    AbstainedAssetV1_1,
    AssetV1_1,
    BoardFitV1_1,
    BoardStats,
    BoardV1_1,
    CandidateImage,
    CandidateImageInput,
    Category,
    Comparisons,
    EngineProvenanceV1_1,
    EngineSourceProvenance,
    Exemplar,
    Interval,
    IntervalMethod,
    Leverage,
    ModelProvenance,
    ProvenanceV1_1,
    ReferenceEntry,
    ReportV1_1,
    RepresentationV1_1,
    SchemaProvenance,
    ScoredAssetV1_1,
    StyleModelInfo,
    Thumbnail,
    Tightness,
    axis_definitions_for,
    from_json_dict,
    read_report_bytes,
    report_schema_sha256,
    validate_report,
    write_report,
)
from .viewer import decode_report_document, inline_report

__all__ = ["main", "build_parser"]

ENGINE_NAME = "moodboard"
ENGINE_SOURCE_REPOSITORY = "https://github.com/ohdearquant/moodboard.git"

# The metric is `cosine` everywhere in the ADRs and the report schema pins it as a constant,
# so it is not a parameter a caller can move.
METRIC = "cosine"

# The image formats a reference or candidate directory is scanned for. A file handed directly
# on the command line is opened whatever its suffix; this set only decides what a directory
# scan picks up, and every file it skips is named on stderr rather than passed over silently.
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif"})

# The inline thumbnail ADR-0001 requires. Large enough to recognise an image at a glance in
# the viewer, small enough that a fifty-reference report stays a file someone can attach to a
# message.
THUMBNAIL_MAX_SIDE = 128
KHIVE_SOURCE_MAX_SIDE = 8192
"""The pack's source-header side ceiling, enforced before pixel conversion."""
KHIVE_RETAINED_VISUAL_MAX_BYTES = 256 * 1024 * 1024
"""Maximum cumulative RGB diagnostic arrays retained by one Khive-mode load."""

# `provenance.model.sha256` for an encoder with no downloaded checkpoint. The report schema
# deliberately does not constrain this field to a hex digest, because ADR-0003 says a claim
# with no authoritative published hash is renamed rather than stretched.
NO_CHECKPOINT = "no-checkpoint"

# The fitting parameters that eval/thresholds.json does not carry, with the record that
# states each. Read from the registry's optional `fit` object when it has one; see the module
# docstring.
_FIT_FALLBACKS: Mapping[str, tuple[float | int, str]] = {
    "k_cap": (5, "docs/adr/0003-style-representation.md"),
    "cluster_cut": (0.35, "docs/adr/0004-abstention.md"),
    "dup_cut": (0.05, "docs/adr/0005-reference-set.md"),
    "min_category_size": (5, "docs/adr/0004-abstention.md"),
}

DEFAULT_KHIVE_EXECUTABLE = "kkernel"
DEFAULT_KHIVE_ACTOR = "lambda:moodboard"
DEFAULT_KHIVE_NAMESPACE = "local"


# ---------------------------------------------------------------------------
# Fitting parameters
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FitParameters:
    """Every parameter that can move a score, with where each one came from.

    `sources` maps each field name to the file or ADR the value was read from, so `build` can
    print the provenance before freezing the policy in the board.
    """

    metric: str
    k_cap: int
    cluster_cut: float
    dup_cut: float
    min_category_size: int
    interval_level: float
    far_outlier_iqr_multiplier: float
    far_outlier_iqr_multiplier_source: str
    sources: Mapping[str, str]


def load_fit_parameters(thresholds_path: Path) -> FitParameters:
    """Read the fitting parameters, preferring `eval/thresholds.json` over the records.

    `interval_coverage.stated_level` is in the registry and is read from it. Four fitting
    constants are not, so each is read from an optional `fit` object in the same file if one
    is present and otherwise from the record that states it. Nothing here silently invents a
    number: every value carries the path or record it came from in `sources`.
    """
    with Path(thresholds_path).open(encoding="utf-8") as handle:
        registry = json.load(handle)

    registry_fit = registry.get("fit", {})
    if not isinstance(registry_fit, dict):
        raise ValueError("fit must be a JSON object when present")
    unknown_fit = set(registry_fit) - set(_FIT_FALLBACKS)
    if unknown_fit:
        raise ValueError(f"fit has unknown keys {sorted(unknown_fit)}")
    values: dict[str, float | int] = {}
    sources: dict[str, str] = {}
    for key, (fallback, record) in _FIT_FALLBACKS.items():
        if key in registry_fit:
            values[key] = registry_fit[key]
            sources[key] = str(thresholds_path)
        else:
            values[key] = fallback
            sources[key] = record

    def plain_positive_integer(value, field: str) -> int:
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError(f"{field} must be a positive plain JSON integer")
        return value

    def finite_number(
        value, field: str, *, minimum: float, maximum: float, strict: bool = False
    ) -> float:
        try:
            is_finite = math.isfinite(value)
            numeric = float(value)
        except (OverflowError, TypeError, ValueError):
            is_finite = False
            numeric = math.nan
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not is_finite:
            raise ValueError(f"{field} must be a finite plain JSON number")
        in_range = minimum < numeric < maximum if strict else minimum <= numeric <= maximum
        if not in_range:
            brackets = "strictly between" if strict else "in"
            raise ValueError(f"{field} must be {brackets} {minimum} and {maximum}")
        return numeric

    k_cap = plain_positive_integer(values["k_cap"], "fit.k_cap")
    min_category_size = plain_positive_integer(values["min_category_size"], "fit.min_category_size")
    cluster_cut = finite_number(values["cluster_cut"], "fit.cluster_cut", minimum=0, maximum=2)
    dup_cut = finite_number(values["dup_cut"], "fit.dup_cut", minimum=0, maximum=2)
    interval_level = finite_number(
        registry["interval_coverage"]["stated_level"],
        "interval_coverage.stated_level",
        minimum=0,
        maximum=1,
        strict=True,
    )
    sources["interval_level"] = str(thresholds_path)
    far_outlier = registry.get("abstention", {}).get("far_outlier", {})
    if not isinstance(far_outlier, dict):
        raise ValueError("abstention.far_outlier must be a JSON object when present")
    if "iqr_multiplier" in far_outlier:
        far_outlier_iqr_multiplier = finite_number(
            far_outlier["iqr_multiplier"],
            "abstention.far_outlier.iqr_multiplier",
            minimum=0,
            maximum=float("inf"),
        )
        far_outlier_iqr_multiplier_source = str(thresholds_path)
    else:
        far_outlier_iqr_multiplier = DEFAULT_FAR_OUTLIER_IQR_MULTIPLIER
        far_outlier_iqr_multiplier_source = DEFAULT_FAR_OUTLIER_IQR_MULTIPLIER_SOURCE
    sources["far_outlier_iqr_multiplier"] = far_outlier_iqr_multiplier_source

    return FitParameters(
        metric=METRIC,
        k_cap=k_cap,
        cluster_cut=cluster_cut,
        dup_cut=dup_cut,
        min_category_size=min_category_size,
        interval_level=interval_level,
        far_outlier_iqr_multiplier=far_outlier_iqr_multiplier,
        far_outlier_iqr_multiplier_source=far_outlier_iqr_multiplier_source,
        sources=sources,
    )


# ---------------------------------------------------------------------------
# Loading images
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LoadedImage:
    """One image on disk, with the identity fields the report and the board hash need."""

    path: Path
    item_id: str
    content_sha256: str
    mime: str
    width: int
    height: int
    array: np.ndarray


def _collect_image_paths(paths: Sequence[Path], stream) -> list[Path]:
    """Expand directories into their image files and pass through files as given.

    A directory scan is one level deep and takes only the suffixes in `IMAGE_SUFFIXES`. Every
    file it declines is named on `stream`, because a run that quietly ignored half its input
    and reported a clean result is the failure this loop is most likely to produce.
    """
    collected: list[Path] = []
    for path in paths:
        if path.is_dir():
            entries = sorted(entry for entry in path.iterdir() if entry.is_file())
            for entry in entries:
                if entry.suffix.lower() in IMAGE_SUFFIXES:
                    collected.append(entry)
                else:
                    print(f"skipping {entry}: not a recognised image suffix", file=stream)
        elif path.is_file():
            collected.append(path)
        else:
            raise FileNotFoundError(f"{path} is neither a file nor a directory")
    return collected


def _assign_ids(paths: Sequence[Path]) -> list[str]:
    """Name each image, preferring the bare file name and falling back to the whole path.

    A file name is what a person recognises in a report, but two directories can hold the same
    name. When they do, every id in the run becomes the path as it was given, so ids stay
    unique and stay consistent with each other rather than only the colliding pair changing.
    """
    names = [path.name for path in paths]
    if len(set(names)) == len(names):
        return names
    return [str(path) for path in paths]


def _load_image(
    path: Path,
    item_id: str,
    *,
    khive_visual: bool = False,
    source_data: bytes | None = None,
    khive_visual_bytes_remaining: int | None = None,
) -> LoadedImage:
    """Read one image, hashing the bytes on disk rather than the decoded pixels.

    The content hash enters the board hash, and ADR-0005 defines the board over the reference
    files' content, so the digest is of the file exactly as it sits on disk.
    """
    data = path.read_bytes() if source_data is None else source_data
    digest = hashlib.sha256(data).hexdigest()

    Image.init()
    with Image.open(io.BytesIO(data)) as handle:
        image_format = handle.format
        width, height = handle.size
        if khive_visual:
            if max(width, height) > KHIVE_SOURCE_MAX_SIDE:
                raise ValueError(
                    f"{path} is {width}x{height}; Khive accepts at most "
                    f"{KHIVE_SOURCE_MAX_SIDE} pixels on either side"
                )
            required_visual_bytes = width * height * 3
            if (
                khive_visual_bytes_remaining is not None
                and required_visual_bytes > khive_visual_bytes_remaining
            ):
                raise ValueError(
                    f"loading {path} would require {required_visual_bytes} retained RGB bytes, "
                    f"but only {khive_visual_bytes_remaining} remain in the Khive visual-array "
                    "budget"
                )
            rgba = np.asarray(handle.convert("RGBA"), dtype=np.uint8)
            matte = np.asarray(KHIVE_VISUAL_MATTE_RGB, dtype=np.uint32)
            array = np.empty((height, width, 3), dtype=np.uint8)
            for row_start in range(0, height, 32):
                row_end = min(row_start + 32, height)
                foreground = rgba[row_start:row_end, ..., :3].astype(np.uint32)
                alpha = rgba[row_start:row_end, ..., 3].astype(np.uint32)[..., None]
                array[row_start:row_end] = (
                    (foreground * alpha + matte * (255 - alpha) + 127) // 255
                ).astype(np.uint8)
        else:
            array = np.asarray(handle.convert("RGB"), dtype=np.uint8)
    if image_format is None:
        raise ValueError(f"{path} has no identifiable image format")
    mime = Image.MIME.get(image_format)
    if mime is None:
        raise ValueError(f"{path} is a {image_format} image, which has no registered MIME type")

    return LoadedImage(
        path=path,
        item_id=item_id,
        content_sha256=digest,
        mime=mime,
        width=width,
        height=height,
        array=array,
    )


def _load_all(paths: Sequence[Path], stream, *, khive_visual: bool = False) -> list[LoadedImage]:
    resolved = _collect_image_paths(paths, stream)
    if not resolved:
        raise ValueError(f"no image files found under {', '.join(str(p) for p in paths)}")
    ids = _assign_ids(resolved)
    if khive_visual:
        if len(resolved) > KHIVE_REQUEST_MAX_IMAGES:
            raise ValueError(
                f"Khive loading has {len(resolved)} images; at most "
                f"{KHIVE_REQUEST_MAX_IMAGES} asset occurrences are allowed"
            )
        source_bytes = 0
        retained_visual_bytes = 0
        loaded: list[LoadedImage] = []
        for index, (path, item_id) in enumerate(zip(resolved, ids, strict=True)):
            visual_remaining = KHIVE_RETAINED_VISUAL_MAX_BYTES - retained_visual_bytes
            if visual_remaining <= 0:
                raise ValueError(
                    f"Khive loading would exceed its retained RGB visual-array budget before "
                    f"image at index {index}; no bytes remain"
                )
            source_remaining = KHIVE_REQUEST_MAX_BYTES - source_bytes
            if source_remaining <= 0:
                raise ValueError(
                    f"Khive loading would exceed its decoded source-byte budget before image "
                    f"at index {index}; no bytes remain"
                )
            with path.open("rb") as handle:
                data = handle.read(source_remaining + 1)
            if len(data) > source_remaining:
                raise ValueError(
                    f"Khive loading would exceed {KHIVE_REQUEST_MAX_BYTES} decoded source "
                    f"bytes while reading image at index {index}"
                )
            image = _load_image(
                path,
                item_id,
                khive_visual=True,
                source_data=data,
                khive_visual_bytes_remaining=visual_remaining,
            )
            source_bytes += len(data)
            retained_visual_bytes += image.array.nbytes
            loaded.append(image)
        return loaded
    return [
        _load_image(path, item_id, khive_visual=khive_visual)
        for path, item_id in zip(resolved, ids, strict=True)
    ]


def _thumbnail(image: LoadedImage, max_side: int = THUMBNAIL_MAX_SIDE) -> Thumbnail:
    """A PNG thumbnail inline in the report, so the viewer needs no network access.

    PNG rather than the source format: the schema wants one declared MIME type per thumbnail
    and re-encoding every thumbnail the same way means the viewer handles one format, whatever
    mix of formats the board was built from.
    """
    picture = Image.fromarray(image.array)
    picture.thumbnail((max_side, max_side))
    buffer = io.BytesIO()
    picture.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return Thumbnail(
        mime="image/png",
        width=picture.width,
        height=picture.height,
        data_base64=encoded,
    )


def _candidate_image(image: LoadedImage) -> CandidateImage:
    """Bind a candidate's original identity to its separately encoded report preview."""

    return CandidateImage(
        content_sha256=image.content_sha256,
        mime=image.mime,
        width=image.width,
        height=image.height,
        thumbnail=_thumbnail(image),
    )


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _rfc3339_now() -> str:
    """The current time in the exact shape the report schema's pattern accepts."""
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _engine_version() -> str:
    try:
        return _distribution_version(ENGINE_NAME)
    except PackageNotFoundError:
        return "0+unknown"


def _engine_source_provenance() -> EngineSourceProvenance | None:
    """Resolve source identity in a checkout; installed wheels honestly omit it.

    ADR-0008 permits runtime source resolution as one complete arm. A Git command failure or a
    directory that is not a checkout is absence, not guessed provenance. When it is a checkout,
    tracked and untracked changes both make the recorded source dirty.
    """

    checkout = Path(__file__).resolve().parent.parent
    try:
        repository = subprocess.run(
            ["git", "-C", str(checkout), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=False,
        )
        if repository.returncode != 0:
            return None
        repository_root = Path(repository.stdout.strip()).resolve()
        if repository_root != checkout:
            # Git searches parent directories. An installed wheel can therefore sit below an
            # unrelated checkout; never label that checkout's HEAD as Moodboard provenance.
            return None
        revision = subprocess.run(
            ["git", "-C", str(checkout), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        if revision.returncode != 0:
            return None
        digest = revision.stdout.strip()
        if len(digest) not in {40, 64} or any(
            character not in "0123456789abcdef" for character in digest
        ):
            return None
        status = subprocess.run(
            ["git", "-C", str(checkout), "status", "--porcelain", "--untracked-files=normal"],
            capture_output=True,
            text=True,
            check=False,
        )
        if status.returncode != 0:
            return None
    except OSError:
        return None
    return EngineSourceProvenance(
        source_repository=ENGINE_SOURCE_REPOSITORY,
        source_revision=digest,
        source_dirty=bool(status.stdout),
    )


def _finite_float(text: str) -> float:
    """An argparse type that refuses NaN and infinity.

    `type=float` accepts the tokens `nan` and `inf`, and a NaN threshold makes every
    comparison downstream False, so a request the engine cannot honour would be silently
    treated as one it could. Rejecting at parse time keeps that out of the engine entirely.
    """
    value = float(text)
    if not math.isfinite(value):
        raise argparse.ArgumentTypeError(f"expected a finite number, got {text!r}")
    return value


def _quantiles(values: Sequence[float]) -> dict[str, float]:
    array = np.asarray(list(values), dtype=np.float64)
    return {
        "p10": float(np.quantile(array, 0.10, method="linear")),
        "p50": float(np.quantile(array, 0.50, method="linear")),
        "p90": float(np.quantile(array, 0.90, method="linear")),
    }


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------


def _make_encoder(args: argparse.Namespace) -> Encoder:
    """Construct only the explicitly selected representation.

    Keeping this selection in one function makes `build` and `rank` use the identical Khive
    attribution and namespace rules.  Merely supplying Khive configuration does not opt in;
    `classical` remains a zero-service default.
    """
    if args.encoder == "classical":
        return ClassicalEncoder()
    if args.encoder == "khive-lattice":
        return KhiveLatticeEncoder(_make_khive_client(args))
    raise ValueError(f"unknown encoder {args.encoder!r}")


def _make_khive_client(args: argparse.Namespace) -> KhiveClient:
    """Construct the one app-specific process client shared by inference and retrieval."""
    return KhiveClient(
        executable=args.khive_executable,
        actor=args.khive_actor,
        namespace=args.khive_namespace,
        config=args.khive_config,
    )


def _embed_loaded(encoder: Encoder, images: Sequence[LoadedImage]) -> np.ndarray:
    if isinstance(encoder, KhiveLatticeEncoder):
        return encoder.embed_source_assets(
            tuple(image.path for image in images),
            expected_sha256=tuple(image.content_sha256 for image in images),
            media_types=tuple(image.mime for image in images),
            names=tuple(image.item_id for image in images),
        )
    return encoder.embed([image.array for image in images])


def _cmd_build(args: argparse.Namespace, out, err) -> int:
    thresholds = load_abstention_thresholds(args.thresholds)
    fit = load_fit_parameters(thresholds.path)

    references = _load_all([args.reference_dir], err, khive_visual=args.encoder == "khive-lattice")
    n = len(references)
    if n < 2:
        raise ValueError(
            f"a board needs at least two references to have a neighbourhood at all; "
            f"{args.reference_dir} holds {n}"
        )
    ids = [image.item_id for image in references]
    if len(set(ids)) != n:
        raise ValueError("two references resolved to the same id; rename one of the files")

    encoder = _make_encoder(args)
    embeddings = _embed_loaded(encoder, references)
    locations: tuple[ReferenceAssetLocation, ...] = ()
    if isinstance(encoder, KhiveLatticeEncoder):
        locations = tuple(
            ReferenceAssetLocation(
                asset_id=asset.asset_id,
                content_ref=asset.content_ref,
                byte_identity=asset.byte_identity,
            )
            for asset in encoder.last_assets
        )

    groups = duplicate_groups(embeddings, fit.dup_cut)
    n_eff = kish_n_eff([len(group) for group in groups])
    k = min(fit.k_cap, n - 1)

    board = build_board(
        name=args.name if args.name is not None else Path(args.reference_dir).name,
        reference_ids=ids,
        reference_content_hashes=[image.content_sha256 for image in references],
        reference_embeddings=embeddings,
        model_repo=encoder.name,
        model_revision=encoder.revision,
        metric=fit.metric,
        k=k,
        k_cap=fit.k_cap,
        cluster_cut=fit.cluster_cut,
        dup_cut=fit.dup_cut,
        min_category_size=fit.min_category_size,
        interval_level=fit.interval_level,
        far_outlier_iqr_multiplier=fit.far_outlier_iqr_multiplier,
        far_outlier_iqr_multiplier_source=fit.far_outlier_iqr_multiplier_source,
        n_eff=n_eff,
        built_at=_rfc3339_now(),
        reference_asset_locations=locations,
    )
    write_board(board, args.output)

    _print_fit(fit, out)
    print(f"board {board.board_id}", file=out)
    print(f"  name           {board.name}", file=out)
    print(f"  references     {n}", file=out)
    print(f"  n_eff          {n_eff:.4f} over {len(groups)} near-duplicate groups", file=out)
    print(f"  finest alpha   {1.0 / (n_eff + 1.0):.4f}", file=out)
    print(f"  written to     {args.output}", file=out)
    return 0


def _print_fit(fit: FitParameters, out) -> None:
    print("fit parameters, each with the file it was read from:", file=out)
    print(f"  metric             {fit.metric}", file=out)
    print(f"  k cap              {fit.k_cap}  ({fit.sources['k_cap']})", file=out)
    print(f"  cluster cut        {fit.cluster_cut}  ({fit.sources['cluster_cut']})", file=out)
    print(f"  duplicate cut      {fit.dup_cut}  ({fit.sources['dup_cut']})", file=out)
    print(
        f"  min category size  {fit.min_category_size}  ({fit.sources['min_category_size']})",
        file=out,
    )
    print(
        f"  interval level     {fit.interval_level}  ({fit.sources['interval_level']})",
        file=out,
    )
    print(
        "  far-outlier IQR    "
        f"{fit.far_outlier_iqr_multiplier}  "
        f"({fit.sources['far_outlier_iqr_multiplier']})",
        file=out,
    )


def _print_board_fit(board: BrandBoard, out) -> None:
    """Print the immutable policy rank actually consumed from ``brand.mb``."""
    print("fit policy loaded from the verified board artifact:", file=out)
    print(f"  metric             {board.metric}", file=out)
    print(f"  effective k        {board.k}", file=out)
    print(f"  configured k cap   {board.k_cap}", file=out)
    print(f"  cluster cut        {board.cluster_cut}", file=out)
    print(f"  duplicate cut      {board.dup_cut}", file=out)
    print(f"  min category size  {board.min_category_size}", file=out)
    print(f"  interval level     {board.interval_level}", file=out)
    print(
        f"  far-outlier IQR    {board.far_outlier_iqr_multiplier}  "
        f"({board.far_outlier_iqr_multiplier_source})",
        file=out,
    )


# ---------------------------------------------------------------------------
# rank
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Candidate:
    """Everything computed for one candidate before the report is assembled."""

    image: LoadedImage
    embedding: np.ndarray
    partition: CategoryPartition
    verdict: AbstentionVerdict | None
    classical_axes: Mapping[str, float]
    exemplars: tuple[Exemplar, ...]
    n_local: int
    supported_alpha: float


def _verify_references(board: BrandBoard, references: Sequence[LoadedImage]) -> None:
    """Refuse a reference directory that is not the one the board was built from.

    The board carries ids and content hashes but no pixels, so `rank` has to be handed the
    images again. Comparing both sequences in order is what makes that a checked input: a
    renamed file, an edited file, an added file or a directory belonging to another board all
    stop the run here rather than producing a report whose catalogue does not describe the
    references its scores were computed against.
    """
    found_ids = tuple(image.item_id for image in references)
    found_hashes = tuple(image.content_sha256 for image in references)
    if found_ids != board.reference_ids or found_hashes != board.reference_content_hashes:
        missing = sorted(set(board.reference_ids) - set(found_ids))
        extra = sorted(set(found_ids) - set(board.reference_ids))
        detail = []
        if missing:
            detail.append(f"missing from the directory: {', '.join(missing)}")
        if extra:
            detail.append(f"present but not in the board: {', '.join(extra)}")
        if not detail:
            detail.append("the ids match but at least one file's contents have changed")
        raise ValueError(
            "the reference directory does not match the board it is being used with; "
            + "; ".join(detail)
        )


def _exemplars(
    reference_embeddings: np.ndarray,
    reference_ids: Sequence[str],
    candidate_embedding: np.ndarray,
    count: int,
) -> tuple[Exemplar, ...]:
    """The `count` references nearest the candidate, by cosine similarity.

    Rows are L2-normalised, so the similarity is the plain dot product. Ties are broken by
    ascending reference index, which is the same rule `conformal.nonconformity_scores` uses,
    so a tie does not resolve one way in the score and another way in the explanation.
    """
    similarity = np.asarray(reference_embeddings, dtype=np.float64) @ np.asarray(
        candidate_embedding, dtype=np.float64
    )
    # The wire is closed to [-1, 1]. Clamp before ordering so values that become the same
    # transmitted similarity retain catalogue order, exactly as ADR-0008 requires for ties.
    similarity = np.clip(similarity, -1.0, 1.0)
    order = np.argsort(-similarity, kind="stable")[:count]
    return tuple(
        Exemplar(
            reference_id=reference_ids[int(index)],
            similarity=float(similarity[int(index)]),
        )
        for index in order
    )


def _classical_axes(
    candidate: LoadedImage,
    references: Sequence[LoadedImage],
    exemplars: Sequence[Exemplar],
) -> dict[str, float]:
    """The three classical axis values for one asset: mean distance to its own exemplars.

    See the module docstring for why the comparison set is the exemplars. Each underlying
    distance is in [0, 1] by ADR-0003, so a mean over them is too, which is what the report
    schema requires of a classical axis value.
    """
    by_id = {image.item_id: image for image in references}
    functions = {
        "palette": axes_module.palette_distance,
        "tone": axes_module.tone_distance,
        "composition": axes_module.composition_distance,
    }
    values: dict[str, float] = {}
    for axis in AXIS_ORDER:
        distances = [
            functions[axis](candidate.array, by_id[exemplar.reference_id].array)
            for exemplar in exemplars
        ]
        values[axis] = float(np.clip(float(np.mean(distances)), 0.0, 1.0))
    return values


def _score_candidate(
    image: LoadedImage,
    embedding: np.ndarray,
    board: BrandBoard,
    reference_embeddings: np.ndarray,
    references: Sequence[LoadedImage],
    board_groups: Sequence[Sequence[int]],
    requested_alpha: float,
    exemplar_count: int,
) -> _Candidate:
    """Partition, judge and (if the judgement allows) prepare one candidate for scoring."""
    n_references = reference_embeddings.shape[0]
    partition = partition_categories(
        reference_embeddings,
        board.reference_content_hashes,
        embedding,
        image.content_sha256,
        board.cluster_cut,
        board.min_category_size,
    )

    # Rule 3's nonconformity values come from the board-wide augmented bag: the rule asks
    # whether the candidate resembles the references at all, which is a board question even
    # though the score itself is category-local.
    bag = np.vstack([reference_embeddings, np.asarray(embedding, dtype=np.float64).reshape(1, -1)])
    board_alphas = nonconformity_scores(bag, min(board.k, n_references))
    reference_alphas = board_alphas[:n_references]
    candidate_alpha = float(board_alphas[n_references])

    verdict = evaluate_abstention(
        partition,
        requested_alpha,
        candidate_alpha,
        reference_alphas,
        board_groups,
        far_outlier_iqr_multiplier=board.far_outlier_iqr_multiplier,
        far_outlier_iqr_multiplier_source=board.far_outlier_iqr_multiplier_source,
    )

    exemplars = _exemplars(
        reference_embeddings,
        board.reference_ids,
        embedding,
        min(exemplar_count, n_references),
    )
    classical = _classical_axes(image, references, exemplars)
    n_eff_local = category_n_eff(partition, board_groups)

    return _Candidate(
        image=image,
        embedding=np.asarray(embedding, dtype=np.float64),
        partition=partition,
        verdict=verdict,
        classical_axes=classical,
        exemplars=exemplars,
        n_local=len(partition.candidate_category_members),
        supported_alpha=1.0 / (n_eff_local + 1.0),
    )


def _competition_ranks(scored: Sequence[tuple[str, float]]) -> dict[str, int]:
    """ADR-0002's rank policy: larger score first, competition ranking, ties on ascending id.

    Two assets tied at rank 3 are followed by rank 5, which is what competition ranking means
    and is what the record states.
    """
    order = sorted(scored, key=lambda item: (-item[1], item[0]))
    ranks: dict[str, int] = {}
    for position, (asset_id, score) in enumerate(order):
        if position > 0 and order[position - 1][1] == score:
            ranks[asset_id] = ranks[order[position - 1][0]]
        else:
            ranks[asset_id] = position + 1
    return ranks


def _tie_pairs(
    candidates: Sequence[_Candidate],
    scores: Mapping[str, float],
    ranks: Mapping[str, int],
    mode: str,
) -> list[tuple[str, str]]:
    """Which pairs the tie test is run on.

    `all` is every pair of scored assets sharing a category, which is the complete answer and
    costs one leave-one-out refit per fold per pair. `adjacent` is every consecutive pair in
    the ranking that shares a category, which is what a person reading a ranking is deciding
    between. `none` runs no comparisons. Whichever is chosen is stated verbatim in the
    report's `comparisons.note`, so the document never implies more coverage than was measured.

    Two assets share a category when the reference sets they were scored against are the same
    set, not when their partitions gave them the same label. Each candidate is partitioned
    with itself in the bag, so two candidates can each land in a category their own partition
    named `c0` while those two categories hold different references, and the paired test needs
    one shared set of folds to be the comparison ADR-0002 defines.
    """
    if mode == "none":
        return []
    category = {
        candidate.image.item_id: tuple(candidate.partition.candidate_category_members)
        for candidate in candidates
    }
    scored_ids = sorted(scores, key=lambda asset_id: (ranks[asset_id], asset_id))

    pairs: list[tuple[str, str]] = []
    if mode == "adjacent":
        considered = list(zip(scored_ids, scored_ids[1:], strict=False))
    elif mode == "all":
        considered = [
            (first, second)
            for index, first in enumerate(scored_ids)
            for second in scored_ids[index + 1 :]
        ]
    else:
        raise ValueError(f"unknown tie-pair mode {mode!r}")

    for first, second in considered:
        if category[first] == category[second]:
            pairs.append((min(first, second), max(first, second)))
    return sorted(set(pairs))


def _tie_note(mode: str, pair_count: int) -> str:
    definition = (
        "Two assets are tied when the interval around their paired score difference, computed "
        "over shared leave-one-out folds, contains zero. Marginal-interval overlap is not the "
        "test and cannot define groups, because the relation is not transitive."
    )
    if mode == "none":
        return "No pairs were compared, so this report carries no tie information. " + definition
    if mode == "adjacent":
        return (
            f"{pair_count} pairs were compared: every consecutive pair in the ranking that "
            f"shares a category. Pairs further apart in the ranking were not tested and are "
            f"absent from this list rather than shown to differ. " + definition
        )
    return (
        f"{pair_count} pairs were compared: every pair of scored assets sharing a category. "
        + definition
    )


def _board_tightness(reference_embeddings: np.ndarray, k: int) -> tuple[Tightness, list[float]]:
    """The board's own spread, by leaving each reference out and scoring it against the rest.

    This is the quantity ADR-0002 calls the board's leave-one-out distribution and the reason
    a score is comparable across boards of different tightness.
    """
    n = reference_embeddings.shape[0]
    loo = [
        conformal_p_value(
            np.delete(reference_embeddings, index, axis=0), reference_embeddings[index], k
        )
        for index in range(n)
    ]
    array = np.asarray(loo, dtype=np.float64)
    tightness = Tightness(
        loo_mean=float(array.mean()),
        loo_sd=float(array.std(ddof=0)),
        loo_quantiles=_quantiles(loo),
    )
    return tightness, loo


def _board_leverage(
    reference_embeddings: np.ndarray,
    reference_ids: Sequence[str],
    baseline_mean: float,
    k: int,
) -> tuple[Leverage, ...]:
    """Per-reference leverage: how much the board tightens when that reference is removed.

    A reference sitting far from the others depresses everyone's leave-one-out score, so
    removing it raises the mean. Rank 1 is the reference whose removal raises it most, ties
    broken on ascending reference id. Needs at least three references, since a two-reference
    board leaves a one-reference board behind and a single reference has no distribution.
    """
    n = reference_embeddings.shape[0]
    if n < 3:
        return ()

    deltas: list[tuple[str, float]] = []
    for removed in range(n):
        reduced = np.delete(reference_embeddings, removed, axis=0)
        loo = [
            conformal_p_value(np.delete(reduced, index, axis=0), reduced[index], k)
            for index in range(reduced.shape[0])
        ]
        deltas.append((reference_ids[removed], float(np.mean(loo)) - baseline_mean))

    ordered = sorted(deltas, key=lambda item: (-item[1], item[0]))
    return tuple(
        Leverage(reference_id=reference_id, delta_tightness=delta, rank=position + 1)
        for position, (reference_id, delta) in enumerate(ordered)
    )


def _board_categories(
    candidates: Sequence[_Candidate], reference_ids: Sequence[str]
) -> tuple[tuple[Category, ...], dict[str, str], bool]:
    """The board's category listing, the id each asset carries, and whether they disagreed.

    ADR-0004 clusters the augmented bag rather than the references alone, precisely so the
    candidate is treated like a calibration point, so every candidate induces its own
    partition of the same references and two candidates can genuinely disagree about how the
    board divides. That is not a defect in the partition and it is measurable here: this run
    is where it becomes visible.

    A report is a single document, so it needs one list. Listing one candidate's partition
    would leave an asset from a different partition carrying a `category_id` that resolves in
    the list to a category it was not scored in, which is worse than saying nothing. So a
    category is identified by the set of references in it, the list is the union of every
    category any candidate induced, ids are assigned by ascending member set, and each asset
    carries the id of its own set. Every `assets[].category_id` therefore resolves to a
    category whose `member_ids` really are the references that asset was compared against, and
    when the candidates all agree the list is exactly the partition they agree on.
    `category_partition_varies_by_candidate` is the flag that says which of the two happened.
    """
    seen: set[tuple[int, ...]] = set()
    for candidate in candidates:
        for members in candidate.partition.all_categories.values():
            if members:
                seen.add(tuple(members))

    ordered = sorted(seen)
    identifiers = {members: f"c{index}" for index, members in enumerate(ordered)}

    assignment: dict[str, str] = {}
    for candidate in candidates:
        members = tuple(candidate.partition.candidate_category_members)
        if not members:
            raise ValueError(
                f"{candidate.image.item_id} landed in a category holding no references at all, "
                "which the report has no way to name; the board is too small to partition"
            )
        assignment[candidate.image.item_id] = identifiers[members]

    categories = tuple(
        Category(
            category_id=identifiers[members],
            n_local=len(members),
            member_ids=tuple(reference_ids[index] for index in members),
        )
        for members in ordered
    )
    varies = (
        len({frozenset(candidate.partition.all_categories.values()) for candidate in candidates})
        > 1
    )
    return categories, assignment, varies


def _preflight_preference_export(args: argparse.Namespace) -> None:
    """Reject invalid opt-in combinations before image ingest or durable Khive writes."""

    destination = args.preference_features_output
    if destination is None:
        return
    if args.encoder != "khive-lattice":
        raise ValueError(
            "--preference-features-output requires --encoder khive-lattice so every row "
            "has a durable Khive visual_asset identity"
        )
    destination = destination.resolve(strict=False)
    report_output = args.output.resolve(strict=False)
    if destination == report_output:
        raise ValueError("preference feature output must differ from the report output")
    if destination == args.board.resolve(strict=False):
        raise ValueError("preference feature output must differ from the brand.mb input")
    if destination.exists() and not destination.is_file():
        raise ValueError("preference feature output must be a file path, not a directory")
    ancestor = destination.parent
    while not ancestor.exists() and ancestor != ancestor.parent:
        ancestor = ancestor.parent
    if not ancestor.is_dir():
        raise ValueError("preference feature output has no directory ancestor")


def _build_rank_preference_candidates(
    *,
    candidates: Sequence[_Candidate],
    candidate_assets: Sequence[KhiveAsset],
    reference_embeddings: np.ndarray,
    board_groups: Sequence[Sequence[int]],
    scores: Mapping[str, float],
    intervals: Mapping[str, Interval],
    ranks: Mapping[str, int],
) -> tuple[PreferenceCandidate, ...]:
    """Freeze ADR-149 rows while complete rank-time geometry is still in memory."""

    if len(candidate_assets) != len(candidates):
        raise ValueError("Khive candidate asset locations do not align with ranked candidates")
    rows: list[PreferenceCandidate] = []
    for candidate, asset in zip(candidates, candidate_assets, strict=True):
        asset_id = candidate.image.item_id
        if candidate.verdict is not None:
            continue
        interval = intervals[asset_id]
        features = build_preference_features(
            candidate_embedding=candidate.embedding,
            reference_embeddings=reference_embeddings,
            local_member_indices=candidate.partition.candidate_category_members,
            style_conformal_p=scores[asset_id],
            style_interval=(interval.low, interval.high),
            local_effective_size=category_n_eff(candidate.partition, board_groups),
            palette_distance=candidate.classical_axes["palette"],
            tone_distance=candidate.classical_axes["tone"],
            composition_distance=candidate.classical_axes["composition"],
        )
        rows.append(
            PreferenceCandidate(
                label=asset_id,
                asset_id=asset.asset_id,
                content_ref=asset.content_ref,
                source_rank=ranks[asset_id],
                features=features,
            )
        )
    if len(rows) < 2:
        raise ValueError(
            "preference feature export needs at least two scored candidates after abstention"
        )
    return tuple(rows)


def _cmd_rank(args: argparse.Namespace, out, err) -> int:
    _preflight_preference_export(args)
    if args.exemplars != 3:
        raise ValueError(
            "report 1.1 requires --exemplars 3 so every asset carries exactly "
            "min(3, references.length) closest references"
        )
    board = read_board(args.board)
    if args.thresholds is not None:
        asserted_thresholds = load_abstention_thresholds(args.thresholds)
        asserted_fit = load_fit_parameters(asserted_thresholds.path)
        asserted_policy = (
            asserted_fit.metric,
            asserted_fit.k_cap,
            asserted_fit.cluster_cut,
            asserted_fit.dup_cut,
            asserted_fit.min_category_size,
            asserted_fit.interval_level,
            asserted_fit.far_outlier_iqr_multiplier,
        )
        stored_policy = (
            board.metric,
            board.k_cap,
            board.cluster_cut,
            board.dup_cut,
            board.min_category_size,
            board.interval_level,
            board.far_outlier_iqr_multiplier,
        )
        if asserted_policy != stored_policy:
            raise ValueError(
                f"{args.thresholds} describes a different fit policy than {args.board}; "
                "ranking always uses the immutable policy stored in the board"
            )

    references = _load_all([args.references], err, khive_visual=args.encoder == "khive-lattice")
    _verify_references(board, references)

    preference_candidates_loaded: list[LoadedImage] | None = None
    if args.preference_features_output is not None:
        preference_candidate_paths = _collect_image_paths(list(args.candidates), err)
        if len(preference_candidate_paths) < 2:
            raise ValueError(
                "preference feature export needs at least two candidate image inputs"
            )
        destination = args.preference_features_output.resolve(strict=False)
        source_paths = [
            image.path.resolve(strict=False) for image in references
        ] + [path.resolve(strict=False) for path in preference_candidate_paths]
        if destination in source_paths:
            raise ValueError("preference feature output must not replace an input image")
        preference_candidates_loaded = _load_all(
            preference_candidate_paths, err, khive_visual=True
        )
        if any(
            len(candidate.item_id.encode("utf-8")) > 512
            for candidate in preference_candidates_loaded
        ):
            raise ValueError(
                "preference feature candidate labels must be at most 512 UTF-8 bytes"
            )
        candidate_hashes = [
            candidate.content_sha256 for candidate in preference_candidates_loaded
        ]
        if len(set(candidate_hashes)) != len(candidate_hashes):
            raise ValueError(
                "preference feature candidate inputs must have unique source bytes"
            )

    encoder = _make_encoder(args)
    if (encoder.name, encoder.revision) != (board.model_repo, board.model_revision):
        raise ValueError(
            f"this build of the engine encodes with {encoder.name}/{encoder.revision}, but the "
            f"board was fitted with {board.model_repo}/{board.model_revision}; scores from two "
            "representations are not comparable"
        )

    reference_embeddings = np.asarray(board.reference_embeddings, dtype=np.float64)
    n_references = reference_embeddings.shape[0]
    board_groups = duplicate_groups(reference_embeddings, board.dup_cut)
    recomputed_n_eff = kish_n_eff([len(group) for group in board_groups])
    if not math.isclose(recomputed_n_eff, board.n_eff, rel_tol=1e-9, abs_tol=1e-9):
        raise ValueError(
            f"{args.board} records n_eff {board.n_eff!r} but its own stored embeddings give "
            f"{recomputed_n_eff!r}; the artifact is inconsistent with itself"
        )

    candidates_loaded = (
        _load_all(list(args.candidates), err, khive_visual=args.encoder == "khive-lattice")
        if preference_candidates_loaded is None
        else preference_candidates_loaded
    )
    candidate_embeddings = _embed_loaded(encoder, candidates_loaded)

    candidates = [
        _score_candidate(
            image,
            candidate_embeddings[index],
            board,
            reference_embeddings,
            references,
            board_groups,
            args.alpha,
            args.exemplars,
        )
        for index, image in enumerate(candidates_loaded)
    ]

    scores: dict[str, float] = {}
    intervals = {}
    for candidate in candidates:
        if candidate.verdict is not None:
            continue
        members = list(candidate.partition.candidate_category_members)
        if len(members) < 2:
            raise ValueError(
                f"{candidate.image.item_id} was admitted at alpha {args.alpha} into a category "
                f"holding {len(members)} reference(s), which cannot support a leave-one-out "
                "interval; ask for a coarser alpha or add references to that look"
            )
        category_embeddings = reference_embeddings[members]
        scores[candidate.image.item_id] = conformal_p_value(
            category_embeddings, candidate.embedding, board.k
        )
        intervals[candidate.image.item_id] = loo_jackknife_plus_interval(
            category_embeddings, candidate.embedding, board.k, board.interval_level
        )

    ranks = _competition_ranks([(asset_id, score) for asset_id, score in scores.items()])

    pairs = _tie_pairs(candidates, scores, ranks, args.tie_pairs)
    by_id = {candidate.image.item_id: candidate for candidate in candidates}
    ties: list[tuple[str, str]] = []
    for first, second in pairs:
        members = list(by_id[first].partition.candidate_category_members)
        difference = paired_score_difference_interval(
            reference_embeddings[members],
            by_id[first].embedding,
            by_id[second].embedding,
            board.k,
            board.interval_level,
        )
        if difference.low <= 0.0 <= difference.high:
            ties.append((first, second))

    categories, category_ids, partition_varies = _board_categories(candidates, board.reference_ids)
    assets = _build_assets(candidates, scores, intervals, ranks, category_ids)
    tightness, loo = _board_tightness(reference_embeddings, board.k)
    leverage = _board_leverage(
        reference_embeddings, board.reference_ids, tightness.loo_mean, board.k
    )

    flags: list[str] = []
    if tightness.loo_sd == 0.0:
        flags.append("degenerate_board")
    if recomputed_n_eff < n_references:
        flags.append("near_duplicate_references")
    if n_references <= board.k_cap:
        flags.append("fewer_references_than_the_estimator_wants")
    if not leverage:
        flags.append("leverage_unavailable_on_a_two_reference_board")
    if partition_varies:
        flags.append("category_partition_varies_by_candidate")

    supported_alpha = max(candidate.supported_alpha for candidate in candidates)

    report = ReportV1_1(
        schema_version=SCHEMA_VERSION_V1_1,
        board=BoardV1_1(
            id=board.board_id,
            name=board.name,
            n_references=n_references,
            n_eff=board.n_eff,
            requested_alpha=args.alpha,
            supported_alpha=supported_alpha,
            built_at=board.built_at,
            representation=RepresentationV1_1(
                style=StyleModelInfo(
                    model=board.model_repo, revision=board.model_revision, dim=board.model_dim
                ),
                axes=AXIS_ORDER,
                axis_definitions=axis_definitions_for(AXIS_ORDER),
            ),
            fit=BoardFitV1_1(
                metric=board.metric,
                k=board.k,
                k_cap=board.k_cap,
                cluster_cut=board.cluster_cut,
                dup_cut=board.dup_cut,
                min_category_size=board.min_category_size,
                interval_level=board.interval_level,
                far_outlier_iqr_multiplier=board.far_outlier_iqr_multiplier,
                far_outlier_iqr_multiplier_source=board.far_outlier_iqr_multiplier_source,
                interval=IntervalMethod(
                    method="loo-jackknife-plus", replicates=None, seed=args.seed
                ),
            ),
            categories=categories,
        ),
        board_stats=BoardStats(tightness=tightness, leverage=leverage, flags=tuple(flags)),
        references=tuple(
            ReferenceEntry(
                reference_id=image.item_id,
                content_sha256=image.content_sha256,
                mime=image.mime,
                width=image.width,
                height=image.height,
                thumbnail=_thumbnail(image),
            )
            for image in references
        ),
        assets=assets,
        comparisons=Comparisons(ties=tuple(ties), note=_tie_note(args.tie_pairs, len(pairs))),
        provenance=ProvenanceV1_1(
            engine=EngineProvenanceV1_1(
                name=ENGINE_NAME,
                version=_engine_version(),
                source=_engine_source_provenance(),
            ),
            model=ModelProvenance(
                repo=board.model_repo,
                revision=board.model_revision,
                sha256=(
                    encoder.descriptor.checkpoint_sha256
                    if isinstance(encoder, KhiveLatticeEncoder)
                    else NO_CHECKPOINT
                ),
            ),
            command=args.command_line,
            argv=args.argv,
            seed=args.seed,
            created_at=_rfc3339_now(),
            schema=SchemaProvenance(
                id="https://github.com/ohdearquant/moodboard/schema/report_v1_1.schema.json",
                sha256=report_schema_sha256(SCHEMA_PATH_V1_1),
            ),
        ),
    )

    preference_candidates: tuple[PreferenceCandidate, ...] | None = None
    if args.preference_features_output is not None:
        if not isinstance(encoder, KhiveLatticeEncoder):
            raise ValueError("preference feature export requires a Khive Lattice encoder")
        preference_candidates = _build_rank_preference_candidates(
            candidates=candidates,
            candidate_assets=encoder.last_assets,
            reference_embeddings=reference_embeddings,
            board_groups=board_groups,
            scores=scores,
            intervals=intervals,
            ranks=ranks,
        )

    write_report(
        report,
        args.output,
        candidate_inputs=tuple(
            CandidateImageInput(
                asset_id=image.item_id,
                content_sha256=image.content_sha256,
                mime=image.mime,
                width=image.width,
                height=image.height,
            )
            for image in candidates_loaded
        ),
    )

    if preference_candidates is not None:
        assert isinstance(encoder, KhiveLatticeEncoder)
        source_report_sha256 = hashlib.sha256(args.output.read_bytes()).hexdigest()
        descriptor = encoder.descriptor
        published_board = _make_khive_client(args).publish_board(
            name=f"Moodboard preference scope {board.board_id[:12]}",
            board_id=board.board_id,
            model_key=descriptor.model_key,
            descriptor_fingerprint=descriptor.fingerprint,
            source_report_sha256=source_report_sha256,
        )
        preference_artifact = PreferenceFeatureArtifact.build(
            board_entity_id=published_board.entity_id,
            board_id=board.board_id,
            model_key=descriptor.model_key,
            descriptor_fingerprint=descriptor.fingerprint,
            source_report_sha256=source_report_sha256,
            candidates=preference_candidates,
        )
        write_preference_feature_artifact(
            preference_artifact, args.preference_features_output
        )

    _print_board_fit(board, out)
    abstained = len(candidates) - len(scores)
    print(f"board {board.board_id}", file=out)
    print(f"  references     {n_references}, n_eff {board.n_eff:.4f}", file=out)
    print(f"  requested a    {args.alpha}", file=out)
    print(f"  supported a    {supported_alpha:.4f}", file=out)
    print(f"  scored         {len(scores)}", file=out)
    print(f"  abstained      {abstained}", file=out)
    print(f"  ties           {len(ties)} of {len(pairs)} pairs compared", file=out)
    print(f"  written to     {args.output}", file=out)
    if args.preference_features_output is not None:
        print(f"  preference features {args.preference_features_output}", file=out)
        print(f"  preference board    {published_board.entity_id}", file=out)
    return 0


def _build_assets(
    candidates: Sequence[_Candidate],
    scores: Mapping[str, float],
    intervals: Mapping[str, Interval],
    ranks: Mapping[str, int],
    category_ids: Mapping[str, str],
) -> tuple[AssetV1_1, ...]:
    """Scored assets in rank order, then abstained assets by ascending id.

    Abstained assets are not sorted to the end of the ranking, they are outside it: ADR-0002
    excludes them from ranking entirely, so they carry no rank and follow in an order that
    does not imply one.

    `category_ids` comes from `_board_categories`, so an asset's `category_id` names an entry
    of `board.categories` whose members really are the references it was compared against.
    """
    scored: list[ScoredAssetV1_1] = []
    abstained: list[AbstainedAssetV1_1] = []
    for candidate in candidates:
        asset_id = candidate.image.item_id
        source = str(candidate.image.path)
        if candidate.verdict is None:
            scored.append(
                ScoredAssetV1_1(
                    state="scored",
                    asset_id=asset_id,
                    source=source,
                    image=_candidate_image(candidate.image),
                    category_id=category_ids[asset_id],
                    n_local=candidate.n_local,
                    score=scores[asset_id],
                    interval=intervals[asset_id],
                    rank=ranks[asset_id],
                    axes={"style": scores[asset_id], **candidate.classical_axes},
                    exemplars=candidate.exemplars,
                    flags=(),
                )
            )
        else:
            abstained.append(
                AbstainedAssetV1_1(
                    state="abstained",
                    asset_id=asset_id,
                    source=source,
                    image=_candidate_image(candidate.image),
                    reason=candidate.verdict.reason,
                    explanation=candidate.verdict.explanation,
                    measurement=candidate.verdict.measurement,
                    category_id=category_ids[asset_id],
                    axes={"style": None, **candidate.classical_axes},
                    exemplars=candidate.exemplars,
                    flags=("abstained",),
                )
            )
    scored.sort(key=lambda asset: (asset.rank, asset.asset_id))
    abstained.sort(key=lambda asset: asset.asset_id)
    return tuple(scored) + tuple(abstained)


# ---------------------------------------------------------------------------
# retrieve and report
# ---------------------------------------------------------------------------


def _cmd_retrieve(args: argparse.Namespace, out, err) -> int:
    """Print Khive's ranked visual locators without interpreting them as calibrated fit."""
    result = _make_khive_client(args).search(args.asset_id, args.top_k)
    print(f"query          {result.query_asset_id}", file=out)
    print(f"descriptor     {result.descriptor.model_key}", file=out)
    print("experimental   true", file=out)
    print(f"hits           {len(result.hits)}", file=out)
    print("rank  cosine    asset_id                              content_ref", file=out)
    for hit in result.hits:
        # JSON string syntax is a compact deterministic terminal escape. A stored newline,
        # tab, or control byte remains data inside one row instead of forging CLI output.
        name = json.dumps(hit.name, ensure_ascii=True)
        print(
            f"{hit.rank:>4}  {hit.score:>+9.6f}  {hit.asset_id}  {hit.content_ref}  {name}",
            file=out,
        )
    return 0


def _cmd_report(args: argparse.Namespace, out, err) -> int:
    """Re-read a report, prove its contract, optionally inline it, and summarise it."""

    report_label = json.dumps(str(args.report), ensure_ascii=True)
    _, document = decode_report_document(read_report_bytes(Path(args.report)))
    try:
        report = from_json_dict(document)
    except JsonSchemaValidationError as error:
        location = "/" + "/".join(str(part) for part in error.absolute_path)
        raise ValueError(
            f"{report_label} fails its report schema at {location}: {error.message}"
        ) from error
    validate_report(report)
    if args.html is not None:
        inline_report(Path(args.report), Path(args.html))

    scored = [asset for asset in report.assets if asset.state == "scored"]
    abstained = [asset for asset in report.assets if asset.state == "abstained"]
    print(f"{report_label} is a valid schema {report.schema_version} report", file=out)
    print(f"  board          {report.board.id}", file=out)
    print(f"  references     {report.board.n_references}, n_eff {report.board.n_eff:.4f}", file=out)
    print(f"  requested a    {report.board.requested_alpha}", file=out)
    print(f"  supported a    {report.board.supported_alpha}", file=out)
    print(f"  scored         {len(scored)}", file=out)
    print(f"  abstained      {len(abstained)}", file=out)
    print(f"  ties           {len(report.comparisons.ties)}", file=out)
    if args.html is not None:
        html_label = json.dumps(str(args.html), ensure_ascii=True)
        print(f"  HTML written   {html_label}", file=out)
    for asset in scored:
        asset_id = json.dumps(asset.asset_id, ensure_ascii=True)
        print(
            f"  {asset.rank:>4}  {asset.score:.4f}  "
            f"[{asset.interval.low:.4f}, {asset.interval.high:.4f}]  {asset_id}",
            file=out,
        )
    for asset in abstained:
        asset_id = json.dumps(asset.asset_id, ensure_ascii=True)
        print(f"     -  {asset.reason:<15} {asset_id}", file=out)
    return 0


# ---------------------------------------------------------------------------
# The parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=ENGINE_NAME,
        description=(
            "Score how well a candidate image fits a reference moodboard, with a calibrated "
            "interval and a per-axis decomposition."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser(
        "build",
        help="embed a reference directory, fit the board, and write a brand.mb artifact",
    )
    build.add_argument("reference_dir", type=Path, help="directory of reference images")
    build.add_argument(
        "-o", "--output", type=Path, required=True, help="path to write the brand.mb artifact to"
    )
    build.add_argument("--name", default=None, help="board name; defaults to the directory name")
    build.add_argument(
        "--thresholds",
        type=Path,
        default=None,
        help="path to eval/thresholds.json; found by walking up from the package by default",
    )
    _add_encoder_arguments(build)
    build.set_defaults(handler=_cmd_build)

    rank = subparsers.add_parser(
        "rank",
        help="score candidates against a board and write a JSON report",
    )
    rank.add_argument(
        "candidates", type=Path, nargs="+", help="candidate image files or directories"
    )
    rank.add_argument("-b", "--board", type=Path, required=True, help="the brand.mb artifact")
    rank.add_argument(
        "-r",
        "--references",
        type=Path,
        required=True,
        help=(
            "the reference directory the board was built from; needed because the report "
            "carries the reference images and a brand.mb carries only their embeddings"
        ),
    )
    rank.add_argument(
        "-o", "--output", type=Path, required=True, help="path to write the JSON report to"
    )
    rank.add_argument(
        "--preference-features-output",
        type=Path,
        default=None,
        help=(
            "opt in to an atomic ADR-149 feature artifact and artifact/moodboard scope; "
            "requires --encoder khive-lattice"
        ),
    )
    rank.add_argument(
        "--alpha",
        type=_finite_float,
        default=0.05,
        help="the requested resolution; refused rather than rounded when the board is coarser",
    )
    rank.add_argument(
        "--exemplars",
        type=int,
        default=3,
        help=(
            "report 1.1 fixes this at 3; each asset carries min(3, reference_count) nearest "
            "references and its classical axes measure against that set"
        ),
    )
    rank.add_argument(
        "--tie-pairs",
        choices=("adjacent", "all", "none"),
        default="adjacent",
        dest="tie_pairs",
        help="which pairs the tie test runs on; the choice is recorded in the report",
    )
    rank.add_argument("--seed", type=int, default=0, help="recorded in the report's provenance")
    rank.add_argument(
        "--thresholds",
        type=Path,
        default=None,
        help=(
            "optional fit-policy assertion; rank uses the immutable policy in brand.mb and "
            "refuses when this registry disagrees"
        ),
    )
    _add_encoder_arguments(rank)
    rank.set_defaults(handler=_cmd_rank)

    retrieve = subparsers.add_parser(
        "retrieve",
        help="return exact Khive cosine neighbours and their immutable asset locators",
    )
    retrieve.add_argument("asset_id", help="bare canonical UUID of an ingested Khive visual_asset")
    retrieve.add_argument(
        "--top-k",
        type=int,
        default=20,
        help="number of non-self exact-cosine neighbours to return (1 through 100)",
    )
    _add_khive_arguments(retrieve)
    retrieve.set_defaults(handler=_cmd_retrieve)

    report = subparsers.add_parser(
        "report", help="validate and summarise a report, optionally writing offline HTML"
    )
    report.add_argument("report", type=Path, help="a JSON report written by moodboard rank")
    report.add_argument(
        "--html",
        type=Path,
        default=None,
        help="atomically write the validated report into the verified offline viewer package",
    )
    report.set_defaults(handler=_cmd_report)

    return parser


def _add_encoder_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--encoder",
        choices=("classical", "khive-lattice"),
        default="classical",
        help=(
            "representation backend; classical is offline and remains the default, while "
            "khive-lattice publishes assets and obtains Lattice embeddings through Khive"
        ),
    )
    _add_khive_arguments(parser)


def _add_khive_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--khive-executable",
        default=DEFAULT_KHIVE_EXECUTABLE,
        help="kkernel executable used for the explicit Khive operation",
    )
    parser.add_argument(
        "--khive-actor",
        default=DEFAULT_KHIVE_ACTOR,
        help="explicit Khive actor, also pinned with --expect-actor",
    )
    parser.add_argument(
        "--khive-namespace",
        default=DEFAULT_KHIVE_NAMESPACE,
        help="explicit Khive namespace for visual assets and retrieval",
    )
    parser.add_argument(
        "--khive-config",
        type=Path,
        default=None,
        help=(
            "optional khive.toml passed explicitly to kkernel; when absent, Khive's normal "
            "KHIVE_CONFIG/discovery fallback remains active"
        ),
    )


def main(argv: Sequence[str] | None = None, out=None, err=None) -> int:
    """The console entry point.

    Returns 0 on success and 1 on a failure the engine can describe.
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    # `provenance.command` records the moodboard invocation, which is not the same string as
    # the host process's argv whenever this is called in-process with an explicit argv. A
    # report that named the calling process's arguments would be describing a different run.
    args.argv = tuple(sys.argv) if argv is None else (ENGINE_NAME, *argv)
    args.command_line = shlex.join(args.argv)
    out = sys.stdout if out is None else out
    err = sys.stderr if err is None else err
    try:
        return args.handler(args, out, err)
    except (ValueError, OSError, KeyError) as error:
        print(f"{ENGINE_NAME}: {error}", file=err)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
