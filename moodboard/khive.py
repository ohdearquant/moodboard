"""Fail-closed process adapter for Moodboard's small Khive verb surface.

This is intentionally not a general Khive SDK.  It knows how to submit a sequence of JSON
operations through ``kkernel exec`` and how to prove that the saved JSONL has exactly one
successful, ordered result per operation. It owns the typed visual-retrieval result boundary;
embedding-array interpretation remains in :mod:`moodboard.encoders`.
"""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
import tempfile
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from moodboard.encoders import VisualDescriptor

__all__ = [
    "KhiveClient",
    "KhiveProtocolError",
    "KhiveSearchHit",
    "KhiveSearchRequest",
    "KhiveSearchResult",
]

_HEX = frozenset("0123456789abcdef")
_SEARCH_RESULT_KEYS = frozenset({"query_asset_id", "descriptor", "experimental", "hits"})
_SEARCH_HIT_KEYS = frozenset({"asset_id", "score", "rank", "name", "content_ref"})
_MODEL_RESULT_KEYS = frozenset({"descriptor", "experimental"})
_DEFAULT_SEARCH_TOP_K = 20
_MAX_SEARCH_TOP_K = 100


class KhiveProtocolError(ValueError):
    """Khive completed (or partially completed) without satisfying the wire contract."""


def _require_exact_keys(value: Mapping[str, Any], expected: frozenset[str], field: str) -> None:
    actual = frozenset(value)
    if actual == expected:
        return
    unknown = sorted(actual - expected)
    missing = sorted(expected - actual)
    details = []
    if unknown:
        details.append(f"unknown keys {unknown}")
    if missing:
        details.append(f"missing keys {missing}")
    raise KhiveProtocolError(f"{field} has " + " and ".join(details))


def _canonical_uuid(value: Any, field: str, error_type: type[ValueError]) -> str:
    try:
        parsed = uuid.UUID(value) if isinstance(value, str) else None
    except ValueError as error:
        raise error_type(f"{field} must be a bare canonical UUID") from error
    if parsed is None or str(parsed) != value:
        raise error_type(f"{field} must be a bare canonical UUID")
    return value


def _is_hex_digest(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= _HEX


def _parse_visual_descriptor(value: Any) -> VisualDescriptor:
    """Reuse the encoder's one frozen descriptor parser without a second wire schema.

    The import is deliberately deferred. ``encoders`` imports this process adapter, so a
    module-level reverse import would create a cycle; every public client call happens after
    both modules are initialized. Keeping one parser is what makes model, ingest, and search
    agree on the exact closed descriptor identity.
    """
    from moodboard.encoders import VisualDescriptor

    return VisualDescriptor.parse(value)


def _parse_model_descriptor(value: Any) -> VisualDescriptor:
    if not isinstance(value, dict):
        raise KhiveProtocolError("moodboard.model result must be an object")
    _require_exact_keys(value, _MODEL_RESULT_KEYS, "moodboard.model result")
    if value.get("experimental") is not True:
        raise KhiveProtocolError("moodboard.model must explicitly report experimental=true")
    return _parse_visual_descriptor(value.get("descriptor"))


@dataclass(frozen=True, slots=True)
class KhiveSearchRequest:
    """The complete argument surface of ``moodboard.search`` v1."""

    asset_id: str
    top_k: int | None = None

    def __post_init__(self) -> None:
        _canonical_uuid(self.asset_id, "moodboard.search asset_id", ValueError)
        if self.top_k is not None and (
            not _plain_int(self.top_k) or not 1 <= self.top_k <= _MAX_SEARCH_TOP_K
        ):
            raise ValueError(
                f"moodboard.search top_k must be an integer from 1 through {_MAX_SEARCH_TOP_K}"
            )

    @property
    def effective_top_k(self) -> int:
        return _DEFAULT_SEARCH_TOP_K if self.top_k is None else self.top_k

    def to_arguments(self) -> dict[str, Any]:
        arguments: dict[str, Any] = {"asset_id": self.asset_id}
        if self.top_k is not None:
            arguments["top_k"] = self.top_k
        return arguments


@dataclass(frozen=True, slots=True)
class KhiveSearchHit:
    """One exact-cosine visual neighbour and its immutable Khive locator."""

    asset_id: str
    score: float
    rank: int
    name: str
    content_ref: str

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "score": self.score,
            "rank": self.rank,
            "name": self.name,
            "content_ref": self.content_ref,
        }


@dataclass(frozen=True, slots=True)
class KhiveSearchResult:
    """Validated ``moodboard.search`` result in the discovered descriptor space."""

    query_asset_id: str
    descriptor: VisualDescriptor
    experimental: Literal[True]
    hits: tuple[KhiveSearchHit, ...]

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "query_asset_id": self.query_asset_id,
            "descriptor": self.descriptor.to_json_dict(),
            "experimental": self.experimental,
            "hits": [hit.to_json_dict() for hit in self.hits],
        }


@dataclass(frozen=True, slots=True)
class _KhiveOperation:
    """One operation in the JSON form accepted by ``kkernel exec --ops-file``."""

    tool: str
    args: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not self.tool or not isinstance(self.tool, str):
            raise ValueError("a Khive operation needs a non-empty tool name")

    def to_json_dict(self) -> dict[str, Any]:
        return {"tool": self.tool, "args": dict(self.args)}


def _reject_json_constant(token: str) -> None:
    raise ValueError(f"non-standard JSON constant {token!r}")


def _json_loads(text: str, *, source: str) -> Any:
    try:
        return json.loads(text, parse_constant=_reject_json_constant)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise KhiveProtocolError(f"{source} is not strict JSON: {error}") from error


def _plain_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


class KhiveClient:
    """Invoke the Khive Moodboard pack with pinned attribution and namespace.

    The executable may be a real ``kkernel`` or a contract-compatible test double.  No
    operation payload is placed in argv: both input and output travel through private temporary
    files, which avoids platform argument limits for base64-encoded images.
    """

    def __init__(
        self,
        *,
        executable: str | Path,
        actor: str,
        namespace: str,
        config: str | Path | None = None,
    ) -> None:
        self.executable = str(executable)
        self.actor = actor
        self.namespace = namespace
        self.config = None if config is None else str(config)
        self._model_descriptor: VisualDescriptor | None = None
        for field, value in (
            ("executable", self.executable),
            ("actor", self.actor),
            ("namespace", self.namespace),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Khive {field} must be a non-empty string")
        if self.config is not None and not self.config.strip():
            raise ValueError("Khive config must be absent or a non-empty path")

    def model(self) -> Any:
        """Return the raw model result after binding its closed descriptor identity."""
        result = self._execute((_KhiveOperation("moodboard.model", {}),))[0]
        descriptor = _parse_model_descriptor(result)
        if (
            self._model_descriptor is not None
            and descriptor.canonical_json != self._model_descriptor.canonical_json
        ):
            raise KhiveProtocolError(
                "moodboard.model descriptor drifted within one Khive client session"
            )
        self._model_descriptor = descriptor
        return result

    def ingest(self, arguments: Sequence[Mapping[str, Any]]) -> tuple[Any, ...]:
        """Submit one ordered `moodboard.ingest` batch and return its raw results."""
        return self._execute(
            tuple(_KhiveOperation("moodboard.ingest", argument) for argument in arguments)
        )

    def search(self, asset_id: str, top_k: int | None = None) -> KhiveSearchResult:
        """Return exact visual neighbours without assigning them coherence semantics.

        Discovery happens before the first search and the returned descriptor must match it
        byte-for-canonical-byte. The method exposes only the Moodboard pack's one retrieval
        request rather than a generic Khive verb executor.
        """
        request = KhiveSearchRequest(asset_id=asset_id, top_k=top_k)
        if self._model_descriptor is None:
            self.model()
        assert self._model_descriptor is not None  # established by model() or an earlier call
        value = self._execute((_KhiveOperation("moodboard.search", request.to_arguments()),))[0]
        return self._parse_search_result(value, request, self._model_descriptor)

    @staticmethod
    def _parse_search_result(
        value: Any,
        request: KhiveSearchRequest,
        expected_descriptor: VisualDescriptor,
    ) -> KhiveSearchResult:
        if not isinstance(value, dict):
            raise KhiveProtocolError("moodboard.search result must be an object")
        _require_exact_keys(value, _SEARCH_RESULT_KEYS, "moodboard.search result")
        query_asset_id = _canonical_uuid(
            value.get("query_asset_id"),
            "moodboard.search result query_asset_id",
            KhiveProtocolError,
        )
        if query_asset_id != request.asset_id:
            raise KhiveProtocolError(
                "moodboard.search result query_asset_id does not match the requested asset"
            )
        if value.get("experimental") is not True:
            raise KhiveProtocolError(
                "moodboard.search result must explicitly report experimental=true"
            )
        descriptor = _parse_visual_descriptor(value.get("descriptor"))
        if descriptor.canonical_json != expected_descriptor.canonical_json:
            raise KhiveProtocolError(
                "moodboard.search result has descriptor drift from moodboard.model"
            )

        raw_hits = value.get("hits")
        if not isinstance(raw_hits, list):
            raise KhiveProtocolError("moodboard.search result hits must be an array")
        if len(raw_hits) > request.effective_top_k:
            raise KhiveProtocolError(
                f"moodboard.search returned {len(raw_hits)} hits, more hits than requested "
                f"({request.effective_top_k})"
            )

        hits: list[KhiveSearchHit] = []
        seen_asset_ids: set[str] = set()
        previous_score = math.inf
        for index, raw_hit in enumerate(raw_hits):
            if not isinstance(raw_hit, dict):
                raise KhiveProtocolError(f"moodboard.search hit {index} must be an object")
            _require_exact_keys(raw_hit, _SEARCH_HIT_KEYS, f"moodboard.search hit {index}")
            hit_asset_id = _canonical_uuid(
                raw_hit.get("asset_id"),
                f"moodboard.search hit {index} asset_id",
                KhiveProtocolError,
            )
            if hit_asset_id == query_asset_id:
                raise KhiveProtocolError(
                    f"moodboard.search hit {index} must exclude the query asset"
                )
            if hit_asset_id in seen_asset_ids:
                raise KhiveProtocolError(
                    f"moodboard.search hit {index} has duplicate asset_id {hit_asset_id}"
                )
            seen_asset_ids.add(hit_asset_id)

            raw_score = raw_hit.get("score")
            if isinstance(raw_score, bool) or not isinstance(raw_score, (int, float)):
                raise KhiveProtocolError(
                    f"moodboard.search hit {index} score must be a plain JSON number"
                )
            try:
                score = float(raw_score)
            except (OverflowError, TypeError, ValueError) as error:
                raise KhiveProtocolError(
                    f"moodboard.search hit {index} score must be a finite cosine in [-1,1]"
                ) from error
            if not math.isfinite(score) or not -1.0 <= score <= 1.0:
                raise KhiveProtocolError(
                    f"moodboard.search hit {index} score must be a finite cosine in [-1,1]"
                )
            if score > previous_score:
                raise KhiveProtocolError("moodboard.search hits must be in descending cosine order")
            previous_score = score

            rank = raw_hit.get("rank")
            if not _plain_int(rank) or rank != index + 1:
                raise KhiveProtocolError(
                    f"moodboard.search hit {index} must carry one-based contiguous rank {index + 1}"
                )
            name = raw_hit.get("name")
            if not isinstance(name, str) or not name.strip() or len(name.encode("utf-8")) > 512:
                raise KhiveProtocolError(
                    f"moodboard.search hit {index} name must be a non-empty UTF-8 string of "
                    "at most 512 bytes"
                )
            content_ref = raw_hit.get("content_ref")
            if not _is_hex_digest(content_ref):
                raise KhiveProtocolError(
                    f"moodboard.search hit {index} content_ref must be 64 lowercase "
                    "hexadecimal characters"
                )
            hits.append(
                KhiveSearchHit(
                    asset_id=hit_asset_id,
                    score=score,
                    rank=rank,
                    name=name,
                    content_ref=content_ref,
                )
            )

        return KhiveSearchResult(
            query_asset_id=query_asset_id,
            descriptor=descriptor,
            experimental=True,
            hits=tuple(hits),
        )

    def _execute(self, operations: Sequence[_KhiveOperation]) -> tuple[Any, ...]:
        """Return one result per operation, or expose no result and raise.

        ``--strict`` makes Khive signal a failed row in its process status.  The checks below
        are still required: a truncated result file, a mismatched manifest, or an executable
        that does not honour strict mode must not be accepted just because its status is zero.
        """
        submitted = tuple(operations)
        if not submitted:
            return ()

        with tempfile.TemporaryDirectory(prefix="moodboard-khive-") as directory:
            root = Path(directory)
            ops_path = root / "ops.jsonl"
            save_path = root / "results.jsonl"
            try:
                with ops_path.open("w", encoding="utf-8", newline="\n") as stream:
                    for operation in submitted:
                        json.dump(
                            operation.to_json_dict(),
                            stream,
                            sort_keys=True,
                            separators=(",", ":"),
                            ensure_ascii=False,
                            allow_nan=False,
                        )
                        stream.write("\n")
            except (TypeError, ValueError) as error:
                raise ValueError(
                    f"Khive operation arguments are not strict JSON: {error}"
                ) from error

            command = [
                self.executable,
                "exec",
            ]
            if self.config is not None:
                command.extend(["--config", self.config])
            command.extend(
                [
                    "--ops-file",
                    str(ops_path),
                    "--save-file",
                    str(save_path),
                    "--namespace",
                    self.namespace,
                    "--actor",
                    self.actor,
                    "--expect-actor",
                    self.actor,
                    "--presentation",
                    "verbose",
                    "--output-format",
                    "json",
                    "--strict",
                ]
            )
            completed = subprocess.run(command, capture_output=True, text=True, check=False)
            if completed.returncode != 0:
                detail = completed.stderr.strip()
                suffix = f": {detail}" if detail else ""
                raise KhiveProtocolError(
                    f"kkernel exec returned exit status {completed.returncode}{suffix}"
                )

            manifest_lines = [line for line in completed.stdout.splitlines() if line.strip()]
            if len(manifest_lines) != 1:
                raise KhiveProtocolError(
                    "kkernel exec must print exactly one JSON manifest line when --save-file "
                    f"is used; received {len(manifest_lines)} non-blank lines"
                )
            manifest = _json_loads(manifest_lines[0], source="kkernel save manifest")
            if not isinstance(manifest, dict):
                raise KhiveProtocolError("kkernel save manifest must be a JSON object")
            if not save_path.is_file():
                raise KhiveProtocolError("kkernel reported success but wrote no result JSONL file")

            payload = save_path.read_bytes()
            self._validate_manifest(manifest, save_path, payload, len(submitted))
            rows = self._parse_rows(payload, submitted)
            return tuple(row["result"] for row in rows)

    @staticmethod
    def _validate_manifest(
        manifest: Mapping[str, Any], save_path: Path, payload: bytes, expected_rows: int
    ) -> None:
        manifest_path = manifest.get("path")
        if not isinstance(manifest_path, str) or not Path(manifest_path).is_absolute():
            raise KhiveProtocolError(
                "kkernel save manifest path must be an absolute path to the result file"
            )
        try:
            returned_target = Path(manifest_path).resolve(strict=True)
            requested_target = save_path.resolve(strict=True)
        except OSError as error:
            raise KhiveProtocolError(
                f"kkernel save manifest path cannot be resolved: {error}"
            ) from error
        if returned_target != requested_target:
            raise KhiveProtocolError(
                "kkernel save manifest path does not resolve to the requested --save-file"
            )
        rows = manifest.get("rows")
        if not _plain_int(rows) or rows != expected_rows:
            raise KhiveProtocolError(
                f"kkernel manifest describes {rows!r} result rows; expected {expected_rows}"
            )
        checksum = manifest.get("checksum")
        measured = hashlib.sha256(payload).hexdigest()
        if checksum != measured:
            raise KhiveProtocolError(
                f"kkernel result checksum mismatch: manifest {checksum!r}, measured {measured!r}"
            )
        summary = manifest.get("summary")
        if not isinstance(summary, dict):
            raise KhiveProtocolError("kkernel save manifest has no result summary object")
        expected_summary = {
            "aborted": 0,
            "failed": 0,
            "succeeded": expected_rows,
            "total": expected_rows,
        }
        for key, expected in expected_summary.items():
            value = summary.get(key)
            if not _plain_int(value) or value != expected:
                if key in {"failed", "aborted"} and value:
                    raise KhiveProtocolError(f"kkernel manifest reported failure: {key}={value!r}")
                raise KhiveProtocolError(
                    f"kkernel manifest summary has {key}={value!r}; expected {expected}"
                )

    @staticmethod
    def _parse_rows(payload: bytes, submitted: Sequence[_KhiveOperation]) -> list[dict[str, Any]]:
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as error:
            raise KhiveProtocolError(f"kkernel result JSONL is not UTF-8: {error}") from error
        lines = text.splitlines()
        if len(lines) != len(submitted) or any(not line.strip() for line in lines):
            raise KhiveProtocolError(
                f"kkernel result JSONL has {len(lines)} rows; expected {len(submitted)}"
            )

        parsed: list[dict[str, Any]] = []
        for index, (line, operation) in enumerate(zip(lines, submitted, strict=True)):
            row = _json_loads(line, source=f"kkernel result JSONL row {index}")
            if not isinstance(row, dict):
                raise KhiveProtocolError(f"kkernel result JSONL row {index} is not an object")
            if row.get("tool") != operation.tool:
                raise KhiveProtocolError(
                    f"kkernel result row {index} is for {row.get('tool')!r}, expected "
                    f"{operation.tool!r}; batch order is not trustworthy"
                )
            if row.get("ok") is not True:
                detail = row.get("error", "no error detail")
                raise KhiveProtocolError(
                    f"kkernel operation {index} ({operation.tool}) reported failure: {detail}"
                )
            if "error" in row:
                raise KhiveProtocolError(
                    f"kkernel operation {index} ({operation.tool}) reports ok=true but also "
                    "carries an error field"
                )
            if "aborted" in row and row.get("aborted") is not False:
                raise KhiveProtocolError(
                    f"kkernel operation {index} ({operation.tool}) reports ok=true but also "
                    f"carries aborted={row.get('aborted')!r}"
                )
            if "result" not in row:
                raise KhiveProtocolError(
                    f"kkernel operation {index} ({operation.tool}) has no result field"
                )
            parsed.append(row)
        return parsed
