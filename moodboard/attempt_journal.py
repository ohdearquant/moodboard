"""Durable local journal for ADR-0014 provider attempts.

The journal grants at most one *local* send authorization for a non-idempotent
attempt.  It does not perform provider I/O and does not claim provider execution,
billing, or delivery exactly once.  A dispatch claim and its ``submitted`` event
are committed in one SQLite transaction before the caller may touch the network.

Terminal ``succeeded`` events are deliberately rejected here until the separate
media/evidence gate can durably prove every referenced output occurrence.
"""

from __future__ import annotations

import base64
import copy
import fcntl
import hashlib
import json
import os
import re
import sqlite3
import stat
import time
import uuid
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field, fields
from functools import cache
from pathlib import Path
from typing import Any, Final, TypeAlias

from blake3 import blake3

from moodboard.attempt_state import AttemptState, AttemptStateError, reduce_attempt_events
from moodboard.contracts import (
    ContractIdentityError,
    canonical_json_bytes,
    is_canonical_utc_timestamp,
)
from moodboard.provider_artifacts import (
    EVENT_VERSION,
    GenerationAttempt,
    GenerationAttemptEvent,
    GenerationRun,
    ProviderArtifact,
    ProviderArtifactError,
    ProviderCapabilitySnapshot,
    ProviderReceipt,
    from_json_dict,
    seal_provider_artifact,
    to_json_dict,
)

__all__ = [
    "AttemptJournal",
    "AttemptJournalError",
    "DispatchClaimConflictError",
    "DispatchClaimResult",
    "EventAppendResult",
    "ImmutableRecordConflictError",
    "JournalCorruptionError",
    "JournalNotFoundError",
    "JournalSecurityError",
    "JournalVersionError",
    "ProviderEvidenceConflictError",
    "ProviderResponsePublishResult",
    "RegistrationResult",
    "StaleAttemptHeadError",
    "StoredProviderResponse",
]

ArtifactInput: TypeAlias = ProviderArtifact | Mapping[str, Any]

_APPLICATION_ID: Final = 0x4D424A31
_USER_VERSION: Final = 2
_MAX_DATABASE_BYTES: Final = 64 * 1024 * 1024
_MAX_DOCUMENT_BYTES: Final = 2 * 1024 * 1024
_MAX_WIRE_REQUEST_BYTES: Final = 32 * 1024 * 1024
_MAX_SQLITE_VALUE_BYTES: Final = 32 * 1024 * 1024
_MAX_PROVIDER_RAW_BYTES: Final = 24 * 1024 * 1024
_MAX_PROVIDER_OUTPUT_BYTES: Final = 16 * 1024 * 1024
_MAX_PROVIDER_OUTPUT_TOTAL_BYTES: Final = 16 * 1024 * 1024
_MAX_PROVIDER_OUTPUTS: Final = 8
_MAX_TREE_DEPTH: Final = 64
_MAX_TREE_NODES: Final = 20_000
_DIGEST_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_HIGH_CONFIDENCE_SECRET_PATTERNS: Final = (
    re.compile(r"sk-or-v1-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)authorization\s*:\s*bearer\s+\S+"),
    re.compile(r"(?i)[?&](?:api_key|access_token|signature|sig|x-amz-signature)=[^&\s]+"),
)
_TABLES: Final = {
    "generation_runs",
    "generation_attempts",
    "attempt_events",
    "non_idempotent_dispatch_claims",
    "provider_responses",
    "provider_response_outputs",
}
_TRIGGERS: Final = {
    f"{table}_{operation}_immutable" for table in _TABLES for operation in ("update", "delete")
}
_EXPECTED_COLUMNS: Final = {
    "generation_runs": (
        ("generation_run_id", "TEXT"),
        ("canonical_sha256", "TEXT"),
        ("document_json", "BLOB"),
    ),
    "generation_attempts": (
        ("attempt_id", "TEXT"),
        ("generation_run_id", "TEXT"),
        ("ordinal", "INTEGER"),
        ("canonical_sha256", "TEXT"),
        ("document_json", "BLOB"),
    ),
    "attempt_events": (
        ("attempt_id", "TEXT"),
        ("sequence", "INTEGER"),
        ("attempt_event_id", "TEXT"),
        ("state", "TEXT"),
        ("canonical_sha256", "TEXT"),
        ("document_json", "BLOB"),
    ),
    "non_idempotent_dispatch_claims": (
        ("attempt_id", "TEXT"),
        ("dispatch_claim_id", "TEXT"),
        ("submitted_event_id", "TEXT"),
        ("capability_snapshot_id", "TEXT"),
        ("expected_head_event_id", "TEXT"),
        ("expected_next_sequence", "INTEGER"),
        ("wire_request_sha256", "TEXT"),
        ("wire_request_byte_count", "INTEGER"),
        ("claim_sha256", "TEXT"),
        ("claim_json", "BLOB"),
        ("capability_sha256", "TEXT"),
        ("capability_json", "BLOB"),
    ),
    "provider_responses": (
        ("attempt_id", "TEXT"),
        ("provider_receipt_id", "TEXT"),
        ("response_event_id", "TEXT"),
        ("receipt_sha256", "TEXT"),
        ("receipt_json", "BLOB"),
        ("raw_content_ref", "TEXT"),
        ("raw_content_sha256", "TEXT"),
        ("raw_byte_count", "INTEGER"),
        ("raw_bytes", "BLOB"),
    ),
    "provider_response_outputs": (
        ("attempt_id", "TEXT"),
        ("output_index", "INTEGER"),
        ("provider_receipt_id", "TEXT"),
        ("content_ref", "TEXT"),
        ("content_sha256", "TEXT"),
        ("byte_count", "INTEGER"),
        ("payload_bytes", "BLOB"),
    ),
}
_CLAIM_KEYS: Final = frozenset(
    {
        "schema_version",
        "dispatch_claim_id",
        "attempt_id",
        "submitted_event_id",
        "capability_snapshot_id",
        "expected_head_event_id",
        "expected_next_sequence",
        "request_key_sha256",
        "normalized_request_id",
        "wire_request_sha256",
        "wire_request_byte_count",
        "claimed_at",
    }
)


class AttemptJournalError(RuntimeError):
    """Base class for stable journal failures."""


class JournalSecurityError(AttemptJournalError):
    """A path, value, or filesystem boundary is unsafe for durable evidence."""


class JournalCorruptionError(AttemptJournalError):
    """Stored bytes or relational state do not revalidate."""


class JournalVersionError(AttemptJournalError):
    """The file is not this journal version."""


class JournalNotFoundError(AttemptJournalError):
    """A required immutable parent artifact is absent."""


class ImmutableRecordConflictError(AttemptJournalError):
    """An immutable identity or slot already contains different bytes."""


class StaleAttemptHeadError(AttemptJournalError):
    """The supplied compare-and-append token is not the current head."""


class DispatchClaimConflictError(AttemptJournalError):
    """A dispatch claim is invalid, already occupied, or not replay-equivalent."""


class ProviderEvidenceConflictError(AttemptJournalError):
    """An attempt already has different immutable provider-response evidence."""


@dataclass(frozen=True, slots=True)
class RegistrationResult:
    created: bool
    artifact: GenerationRun | GenerationAttempt


@dataclass(frozen=True, slots=True)
class EventAppendResult:
    created: bool
    event: GenerationAttemptEvent
    state: AttemptState


@dataclass(frozen=True, slots=True)
class DispatchClaimResult:
    created: bool
    send_authorized: bool
    dispatch_claim_id: str
    submitted_event: GenerationAttemptEvent
    state: AttemptState


@dataclass(frozen=True, slots=True)
class StoredProviderResponse:
    """A verified receipt and its exact private payloads."""

    receipt: ProviderReceipt
    event: GenerationAttemptEvent
    raw_response_bytes: bytes | None = field(repr=False)
    output_bytes: tuple[bytes, ...] = field(repr=False)


@dataclass(frozen=True, slots=True)
class ProviderResponsePublishResult:
    """Outcome of an atomic provider-response publication or exact replay."""

    created: bool
    receipt: ProviderReceipt
    event: GenerationAttemptEvent
    state: AttemptState


def _reject_json_constant(value: str) -> None:
    del value
    raise ValueError("non-I-JSON numeric constant")


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _tree_depth_and_nodes(value: Any) -> tuple[int, int]:
    stack: list[tuple[Any, int, bool]] = [(value, 1, False)]
    active: set[int] = set()
    maximum = 0
    nodes = 0
    while stack:
        current, depth, exiting = stack.pop()
        if exiting:
            active.remove(id(current))
            continue
        maximum = max(maximum, depth)
        nodes += 1
        if maximum > _MAX_TREE_DEPTH or nodes > _MAX_TREE_NODES:
            raise JournalSecurityError("journal input exceeds the bounded JSON envelope")
        if isinstance(current, Mapping):
            identity = id(current)
            if identity in active:
                raise JournalSecurityError("journal input contains a recursive object")
            active.add(identity)
            stack.append((current, depth, True))
            stack.extend((item, depth + 1, False) for item in current.values())
        elif isinstance(current, (list, tuple)):
            identity = id(current)
            if identity in active:
                raise JournalSecurityError("journal input contains a recursive array")
            active.add(identity)
            stack.append((current, depth, True))
            stack.extend((item, depth + 1, False) for item in current)
    return maximum, nodes


class _SecretScanner:
    def __init__(self, forbidden_secrets: Iterable[str]) -> None:
        variants: set[bytes] = set()
        try:
            values = tuple(forbidden_secrets)
        except (RecursionError, TypeError) as error:
            raise JournalSecurityError(
                "forbidden_secrets must be a bounded string iterable"
            ) from error
        if len(values) > 32:
            raise JournalSecurityError("too many active secret sentinels")
        for value in values:
            if not isinstance(value, str) or not 8 <= len(value) <= 8192:
                raise JournalSecurityError("active secret sentinels must be bounded strings")
            raw = value.encode("utf-8")
            standard_base64 = base64.b64encode(raw)
            hex_value = raw.hex()
            variants.update(
                {
                    raw,
                    standard_base64,
                    standard_base64.rstrip(b"="),
                    base64.urlsafe_b64encode(raw),
                    base64.urlsafe_b64encode(raw).rstrip(b"="),
                    hex_value.encode("ascii"),
                    hex_value.upper().encode("ascii"),
                }
            )
        self._variants = tuple(sorted(variants, key=len, reverse=True))

    def scan(self, value: Any) -> None:
        _tree_depth_and_nodes(value)
        stack = [value]
        while stack:
            current = stack.pop()
            if isinstance(current, Mapping):
                stack.extend(current.keys())
                stack.extend(current.values())
            elif isinstance(current, (list, tuple)):
                stack.extend(current)
            elif isinstance(current, str):
                self.scan_bytes(current.encode("utf-8", errors="strict"))
            elif isinstance(current, bytes):
                self.scan_bytes(current)

    def scan_bytes(self, raw: bytes) -> None:
        if type(raw) is not bytes or len(raw) > _MAX_DOCUMENT_BYTES:
            raise JournalSecurityError("journal value exceeds the persistence bound")
        self._scan_unbounded_bytes(raw)

    def scan_evidence_bytes(self, raw: bytes, *, maximum: int) -> None:
        if type(raw) is not bytes or not 1 <= len(raw) <= maximum:
            raise JournalSecurityError("provider evidence exceeds its persistence bound")
        self._scan_unbounded_bytes(raw)

    def _scan_unbounded_bytes(self, raw: bytes) -> None:
        if any(secret in raw for secret in self._variants):
            raise JournalSecurityError("journal input contains a forbidden credential")
        text = raw.decode("utf-8", errors="ignore")
        if any(pattern.search(text) for pattern in _HIGH_CONFIDENCE_SECRET_PATTERNS):
            raise JournalSecurityError("journal input contains a credential-like value")


def _validate_uuid(value: str, field: str) -> None:
    try:
        parsed = uuid.UUID(value)
    except (AttributeError, TypeError, ValueError) as error:
        raise AttemptJournalError(f"{field} must be a canonical UUID") from error
    if str(parsed) != value:
        raise AttemptJournalError(f"{field} must be a canonical UUID")


def _validate_digest(value: str, field: str) -> None:
    if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
        raise AttemptJournalError(f"{field} must be a lowercase SHA-256 digest")


def _validate_timestamp(value: str, field: str) -> None:
    if not is_canonical_utc_timestamp(value):
        raise AttemptJournalError(f"{field} must be a real canonical UTC timestamp")


def _timestamp_key(value: str) -> tuple[str, int]:
    fraction = value[20:-1] if len(value) > 20 else ""
    return value[:19], int(fraction.ljust(9, "0") or "0")


def _validate_artifact_timestamp(artifact: ProviderArtifact) -> None:
    if isinstance(artifact, GenerationRun | GenerationAttempt):
        _validate_timestamp(artifact.created_at, "created_at")
    elif isinstance(artifact, GenerationAttemptEvent):
        _validate_timestamp(artifact.recorded_at, "recorded_at")
    elif isinstance(artifact, ProviderCapabilitySnapshot):
        _validate_timestamp(artifact.captured_at, "captured_at")
    elif isinstance(artifact, ProviderReceipt):
        _validate_timestamp(artifact.received_at, "received_at")


def _canonical_artifact(
    value: ArtifactInput,
    expected_type: type[GenerationRun]
    | type[GenerationAttempt]
    | type[GenerationAttemptEvent]
    | type[ProviderCapabilitySnapshot]
    | type[ProviderReceipt],
    scanner: _SecretScanner,
) -> tuple[ProviderArtifact, bytes, str]:
    if isinstance(
        value,
        (
            GenerationRun,
            GenerationAttempt,
            GenerationAttemptEvent,
            ProviderCapabilitySnapshot,
            ProviderReceipt,
        ),
    ):
        scanner.scan({field.name: getattr(value, field.name) for field in fields(value)})
    else:
        scanner.scan(value)
    artifact: ProviderArtifact | None = None
    canonical: bytes | None = None
    try:
        if isinstance(value, expected_type):
            document = to_json_dict(value)
            artifact = from_json_dict(document)
        elif isinstance(value, Mapping):
            document = copy.deepcopy(dict(value))
            artifact = from_json_dict(document)
        else:
            raise TypeError("unsupported artifact value")
        if not isinstance(artifact, expected_type):
            raise TypeError("wrong provider-artifact branch")
        _validate_artifact_timestamp(artifact)
        canonical = canonical_json_bytes(to_json_dict(artifact))
    except (
        ContractIdentityError,
        ProviderArtifactError,
        RecursionError,
        TypeError,
        ValueError,
    ):
        # Raise only after the caught validator exception leaves scope.  Some third-party
        # validation errors quote rejected values; retaining them as __cause__/__context__ would
        # defeat the journal's secret-safe public failure surface.
        artifact = None
        canonical = None
    if artifact is None or canonical is None:
        raise AttemptJournalError("provider artifact is not valid for this journal operation")
    scanner.scan_bytes(canonical)
    if len(canonical) > _MAX_DOCUMENT_BYTES:
        raise JournalSecurityError("journal artifact exceeds the persistence bound")
    return artifact, canonical, hashlib.sha256(canonical).hexdigest()


def _path_components(path: Path) -> Iterable[Path]:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        yield current


def _validate_secure_parent(path: Path) -> None:
    parent = path.parent
    for component in _path_components(parent):
        try:
            metadata = component.lstat()
        except FileNotFoundError as error:
            raise JournalSecurityError("journal parent must already exist") from error
        if stat.S_ISLNK(metadata.st_mode):
            raise JournalSecurityError("journal path may not traverse a symlink")
    metadata = parent.stat()
    if not stat.S_ISDIR(metadata.st_mode):
        raise JournalSecurityError("journal parent must be a directory")
    if metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
        raise JournalSecurityError("journal parent must be owner-only")


def _validate_regular_private_file(path: Path, *, allow_missing: bool) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        if allow_missing:
            return
        raise
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise JournalSecurityError("journal files must be regular and not symlinks")
    if metadata.st_uid != os.getuid() or metadata.st_nlink != 1 or metadata.st_mode & 0o077:
        raise JournalSecurityError("journal files must be private, owned, and unaliased")
    if metadata.st_size > _MAX_DATABASE_BYTES:
        raise JournalSecurityError("journal file exceeds the local resource bound")


def _safe_path(value: str | os.PathLike[str]) -> Path:
    raw = os.fspath(value)
    if not isinstance(raw, str) or raw in {":memory:", ""} or raw.startswith("file:"):
        raise JournalSecurityError("journal requires a canonical filesystem path")
    path = Path(raw)
    if not path.is_absolute() or path != Path(os.path.normpath(raw)):
        raise JournalSecurityError("journal path must be absolute and normalized")
    _validate_secure_parent(path)
    _validate_regular_private_file(path, allow_missing=True)
    for suffix in ("-wal", "-shm", "-journal", ".init.lock"):
        _validate_regular_private_file(Path(f"{path}{suffix}"), allow_missing=True)
    return path


_SCHEMA_SQL = """
CREATE TABLE generation_runs (
    generation_run_id TEXT PRIMARY KEY,
    canonical_sha256 TEXT NOT NULL,
    document_json BLOB NOT NULL
);
CREATE TABLE generation_attempts (
    attempt_id TEXT PRIMARY KEY,
    generation_run_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal BETWEEN 1 AND 2147483647),
    canonical_sha256 TEXT NOT NULL,
    document_json BLOB NOT NULL,
    UNIQUE (generation_run_id, ordinal),
    FOREIGN KEY (generation_run_id) REFERENCES generation_runs(generation_run_id)
);
CREATE TABLE attempt_events (
    attempt_id TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK (sequence BETWEEN 1 AND 5),
    attempt_event_id TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL,
    canonical_sha256 TEXT NOT NULL,
    document_json BLOB NOT NULL,
    PRIMARY KEY (attempt_id, sequence),
    UNIQUE (attempt_id, attempt_event_id),
    FOREIGN KEY (attempt_id) REFERENCES generation_attempts(attempt_id)
);
CREATE TABLE non_idempotent_dispatch_claims (
    attempt_id TEXT PRIMARY KEY,
    dispatch_claim_id TEXT NOT NULL UNIQUE,
    submitted_event_id TEXT NOT NULL UNIQUE,
    capability_snapshot_id TEXT NOT NULL,
    expected_head_event_id TEXT NOT NULL,
    expected_next_sequence INTEGER NOT NULL,
    wire_request_sha256 TEXT NOT NULL,
    wire_request_byte_count INTEGER NOT NULL,
    claim_sha256 TEXT NOT NULL,
    claim_json BLOB NOT NULL,
    capability_sha256 TEXT NOT NULL,
    capability_json BLOB NOT NULL,
    FOREIGN KEY (attempt_id) REFERENCES generation_attempts(attempt_id),
    FOREIGN KEY (attempt_id, submitted_event_id)
      REFERENCES attempt_events(attempt_id, attempt_event_id)
);
CREATE TABLE provider_responses (
    attempt_id TEXT PRIMARY KEY,
    provider_receipt_id TEXT NOT NULL UNIQUE,
    response_event_id TEXT NOT NULL UNIQUE,
    receipt_sha256 TEXT NOT NULL,
    receipt_json BLOB NOT NULL,
    raw_content_ref TEXT,
    raw_content_sha256 TEXT,
    raw_byte_count INTEGER,
    raw_bytes BLOB,
    UNIQUE (attempt_id, provider_receipt_id),
    CHECK (
      (raw_content_ref IS NULL AND raw_content_sha256 IS NULL
       AND raw_byte_count IS NULL AND raw_bytes IS NULL)
      OR
      (raw_content_ref IS NOT NULL AND raw_content_sha256 IS NOT NULL
       AND raw_byte_count BETWEEN 1 AND 25165824 AND raw_bytes IS NOT NULL)
    ),
    FOREIGN KEY (attempt_id) REFERENCES generation_attempts(attempt_id),
    FOREIGN KEY (attempt_id, response_event_id)
      REFERENCES attempt_events(attempt_id, attempt_event_id)
);
CREATE TABLE provider_response_outputs (
    attempt_id TEXT NOT NULL,
    output_index INTEGER NOT NULL CHECK (output_index BETWEEN 0 AND 7),
    provider_receipt_id TEXT NOT NULL,
    content_ref TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    byte_count INTEGER NOT NULL CHECK (byte_count BETWEEN 1 AND 16777216),
    payload_bytes BLOB NOT NULL,
    PRIMARY KEY (attempt_id, output_index),
    UNIQUE (provider_receipt_id, output_index),
    FOREIGN KEY (attempt_id, provider_receipt_id)
      REFERENCES provider_responses(attempt_id, provider_receipt_id)
);
"""


def _immutable_trigger_sql(table: str, operation: str) -> str:
    return (
        f"CREATE TRIGGER {table}_{operation}_immutable BEFORE {operation.upper()} ON {table} "
        "BEGIN SELECT RAISE(ABORT, 'immutable journal row'); END"
    )


@cache
def _expected_schema_objects() -> dict[tuple[str, str], str]:
    """Render SQLite's canonical stored SQL for this exact schema on this runtime."""

    connection = sqlite3.connect(":memory:", isolation_level=None)
    try:
        connection.executescript(_SCHEMA_SQL)
        for table in sorted(_TABLES):
            for operation in ("update", "delete"):
                connection.execute(_immutable_trigger_sql(table, operation))
        rows = connection.execute(
            "SELECT type, name, sql FROM sqlite_master "
            "WHERE type IN ('table', 'index', 'trigger') AND sql IS NOT NULL"
        ).fetchall()
        return {(row[0], row[1]): row[2] for row in rows}
    finally:
        connection.close()


class AttemptJournal:
    """File-backed immutable attempt journal with a permanent non-idempotent send slot."""

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        busy_timeout_ms: int = 5000,
        forbidden_secrets: Iterable[str] = (),
    ) -> None:
        self._path = _safe_path(path)
        if not isinstance(busy_timeout_ms, int) or isinstance(busy_timeout_ms, bool):
            raise JournalSecurityError("busy_timeout_ms must be an integer")
        if not 1 <= busy_timeout_ms <= 60_000:
            raise JournalSecurityError("busy_timeout_ms is outside the safe bound")
        self._busy_timeout_ms = busy_timeout_ms
        self._scanner = _SecretScanner(forbidden_secrets)

    @property
    def path(self) -> Path:
        return self._path

    def _prepare_filesystem(self) -> bool:
        _validate_secure_parent(self._path)
        if self._path.exists() or self._path.is_symlink():
            _validate_regular_private_file(self._path, allow_missing=False)
            return False
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(self._path, flags, 0o600)
        except FileExistsError:
            _validate_regular_private_file(self._path, allow_missing=False)
            return False
        else:
            os.close(descriptor)
            os.chmod(self._path, 0o600)
            return True

    def _connect(self, *, initialize: bool = False) -> sqlite3.Connection:
        created = self._prepare_filesystem() if initialize else False
        if not self._path.exists():
            raise JournalNotFoundError("attempt journal has not been initialized")
        _validate_regular_private_file(self._path, allow_missing=False)
        before_open = self._path.lstat()
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                self._path,
                timeout=self._busy_timeout_ms / 1000,
                isolation_level=None,
            )
            connection.row_factory = sqlite3.Row
            connection.enable_load_extension(False)
            if hasattr(connection, "setlimit"):
                connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, _MAX_SQLITE_VALUE_BYTES)
                connection.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 256 * 1024)
                connection.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)
                connection.setlimit(sqlite3.SQLITE_LIMIT_TRIGGER_DEPTH, 16)
            connection.execute(f"PRAGMA busy_timeout={self._busy_timeout_ms}")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA trusted_schema=OFF")
            connection.execute("PRAGMA mmap_size=0")
            connection.execute("PRAGMA cell_size_check=ON")
            if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                raise JournalSecurityError("SQLite foreign keys are not active")
            if connection.execute("PRAGMA trusted_schema").fetchone()[0] != 0:
                raise JournalSecurityError("SQLite trusted schema could not be disabled")
            if created:
                self._configure_durable_pragmas(connection)
                self._initialize_schema(connection)
            else:
                # Authenticate an existing file as this exact journal before issuing any
                # persistent journal-mode pragma.  A rejected unrelated SQLite database must
                # remain byte/sidecar neutral.
                self._verify_metadata(connection)
                self._configure_durable_pragmas(connection)
            self._verify_metadata(connection)
        except AttemptJournalError:
            if connection is not None:
                with suppress(sqlite3.Error):
                    connection.close()
            if created:
                self._discard_failed_new_journal()
            raise
        except (sqlite3.DatabaseError, sqlite3.OperationalError) as error:
            if connection is not None:
                with suppress(sqlite3.Error):
                    connection.close()
            if created:
                # A failed first initialization is not a usable journal.  The path contains no
                # caller evidence yet, so remove only the exact file this constructor created.
                self._discard_failed_new_journal()
            raise JournalCorruptionError("attempt journal could not be opened safely") from error
        assert connection is not None
        try:
            after_open = self._path.lstat()
            if (before_open.st_dev, before_open.st_ino) != (
                after_open.st_dev,
                after_open.st_ino,
            ):
                raise JournalSecurityError("journal path identity changed while opening")
            for suffix in ("", "-wal", "-shm"):
                candidate = Path(f"{self._path}{suffix}")
                if candidate.exists():
                    _validate_regular_private_file(candidate, allow_missing=False)
        except AttemptJournalError:
            with suppress(sqlite3.Error):
                connection.close()
            raise
        return connection

    @staticmethod
    def _configure_durable_pragmas(connection: sqlite3.Connection) -> None:
        mode = connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]
        if str(mode).lower() != "wal":
            raise JournalSecurityError("SQLite refused WAL mode")
        connection.execute("PRAGMA synchronous=FULL")
        if connection.execute("PRAGMA synchronous").fetchone()[0] != 2:
            raise JournalSecurityError("SQLite refused FULL synchronous mode")
        page_size = connection.execute("PRAGMA page_size").fetchone()[0]
        if not isinstance(page_size, int) or page_size <= 0:
            raise JournalSecurityError("SQLite returned an invalid page size")
        maximum_pages = _MAX_DATABASE_BYTES // page_size
        observed_limit = connection.execute(f"PRAGMA max_page_count={maximum_pages}").fetchone()[0]
        if observed_limit != maximum_pages:
            raise JournalSecurityError("SQLite refused the journal page-count bound")
        connection.execute("PRAGMA wal_autocheckpoint=1000")
        connection.execute(f"PRAGMA journal_size_limit={_MAX_DATABASE_BYTES}")

    def _discard_failed_new_journal(self) -> None:
        for suffix in ("-shm", "-wal", "-journal", ""):
            candidate = Path(f"{self._path}{suffix}")
            with suppress(OSError):
                metadata = candidate.lstat()
                if stat.S_ISREG(metadata.st_mode) and metadata.st_uid == os.getuid():
                    candidate.unlink()

    def _initialize_schema(self, connection: sqlite3.Connection) -> None:
        trigger_sql = ";\n".join(
            _immutable_trigger_sql(table, operation)
            for table in sorted(_TABLES)
            for operation in ("update", "delete")
        )
        try:
            # ``executescript`` controls its own transaction boundary, so include the complete
            # bootstrap transaction in the script instead of surrounding it with execute().
            connection.executescript(
                "BEGIN IMMEDIATE;\n"
                f"{_SCHEMA_SQL}\n"
                f"{trigger_sql};\n"
                f"PRAGMA application_id={_APPLICATION_ID};\n"
                f"PRAGMA user_version={_USER_VERSION};\n"
                "COMMIT;"
            )
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

    def _verify_metadata(self, connection: sqlite3.Connection) -> None:
        application_id = connection.execute("PRAGMA application_id").fetchone()[0]
        user_version = connection.execute("PRAGMA user_version").fetchone()[0]
        if application_id != _APPLICATION_ID or user_version != _USER_VERSION:
            raise JournalVersionError("file is not a supported Moodboard attempt journal")
        objects = connection.execute(
            "SELECT type, name, sql FROM sqlite_master "
            "WHERE type IN ('table', 'index', 'trigger') AND sql IS NOT NULL"
        ).fetchall()
        tables = {row["name"] for row in objects if row["type"] == "table"}
        triggers = {row["name"] for row in objects if row["type"] == "trigger"}
        if tables != _TABLES or triggers != _TRIGGERS:
            raise JournalCorruptionError("attempt journal schema does not match version two")
        actual_schema = {(row["type"], row["name"]): row["sql"] for row in objects}
        if actual_schema != _expected_schema_objects():
            raise JournalCorruptionError("attempt journal schema SQL fingerprint drifted")
        for table, expected in _EXPECTED_COLUMNS.items():
            actual = tuple(
                (row["name"], row["type"].upper())
                for row in connection.execute(f'PRAGMA table_info("{table}")').fetchall()
            )
            if actual != expected:
                raise JournalCorruptionError("attempt journal columns do not match version two")

    def _begin(self) -> sqlite3.Connection:
        with self._bootstrap_lock():
            connection = self._connect(initialize=True)
        try:
            connection.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as error:
            connection.close()
            raise AttemptJournalError("attempt journal is busy") from error
        return connection

    @contextmanager
    def _bootstrap_lock(self) -> Iterator[None]:
        lock_path = Path(f"{self._path}.init.lock")
        _validate_regular_private_file(lock_path, allow_missing=True)
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(lock_path, flags, 0o600)
        except OSError as error:
            raise JournalSecurityError("journal bootstrap lock is unsafe") from error
        deadline = time.monotonic() + self._busy_timeout_ms / 1000
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or metadata.st_nlink != 1
                or metadata.st_mode & 0o077
            ):
                raise JournalSecurityError("journal bootstrap lock is not owner-only")
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError as error:
                    if time.monotonic() >= deadline:
                        raise AttemptJournalError("journal bootstrap lock is busy") from error
                    time.sleep(0.01)
            yield
        finally:
            with suppress(OSError):
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _begin_read(self) -> sqlite3.Connection:
        connection = self._connect()
        try:
            connection.execute("BEGIN")
        except sqlite3.Error as error:
            connection.close()
            raise AttemptJournalError("attempt journal read snapshot could not start") from error
        return connection

    @staticmethod
    def _commit(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("COMMIT")
        except sqlite3.Error as error:
            raise AttemptJournalError(
                "journal commit acknowledgement is ambiguous; no send is authorized"
            ) from error
        finally:
            connection.close()

    @staticmethod
    def _rollback(connection: sqlite3.Connection) -> None:
        with suppress(sqlite3.Error):
            if connection.in_transaction:
                connection.execute("ROLLBACK")
        with suppress(sqlite3.Error):
            connection.close()

    def _decode_artifact(
        self,
        raw: bytes,
        expected_sha256: str,
        expected_type: type[GenerationRun]
        | type[GenerationAttempt]
        | type[GenerationAttemptEvent]
        | type[ProviderCapabilitySnapshot]
        | type[ProviderReceipt],
    ) -> ProviderArtifact:
        if not isinstance(raw, bytes) or len(raw) > _MAX_DOCUMENT_BYTES:
            raise JournalCorruptionError("stored artifact exceeds its byte bound")
        if hashlib.sha256(raw).hexdigest() != expected_sha256:
            raise JournalCorruptionError("stored artifact digest does not match")
        try:
            document = self._decode_canonical_json(raw)
            artifact = from_json_dict(document)
            _validate_artifact_timestamp(artifact)
        except (
            AttemptJournalError,
            UnicodeDecodeError,
            ValueError,
            TypeError,
            RecursionError,
        ) as error:
            raise JournalCorruptionError("stored artifact does not revalidate") from error
        if not isinstance(artifact, expected_type):
            raise JournalCorruptionError("stored artifact branch disagrees with its table")
        return artifact

    @staticmethod
    def _row_blob(row: sqlite3.Row, field: str) -> bytes:
        value = row[field]
        if not isinstance(value, bytes):
            raise JournalCorruptionError("stored journal value has the wrong SQLite type")
        return value

    def _decode_canonical_json(self, raw: bytes) -> dict[str, Any]:
        try:
            self._scanner.scan_bytes(raw)
            document = json.loads(
                raw.decode("utf-8", errors="strict"),
                object_pairs_hook=_reject_duplicate_pairs,
                parse_constant=_reject_json_constant,
            )
            _tree_depth_and_nodes(document)
            if not isinstance(document, dict) or canonical_json_bytes(document) != raw:
                raise ValueError("stored JSON is not a canonical object")
            return document
        except JournalSecurityError as error:
            raise JournalCorruptionError(
                "stored journal bytes violate the secret boundary"
            ) from error

    def _decode_claim(self, row: sqlite3.Row) -> dict[str, Any]:
        raw = self._row_blob(row, "claim_json")
        if len(raw) > _MAX_DOCUMENT_BYTES or hashlib.sha256(raw).hexdigest() != row["claim_sha256"]:
            raise JournalCorruptionError("dispatch claim digest does not match")
        try:
            document = self._decode_canonical_json(raw)
            if set(document) != _CLAIM_KEYS:
                raise ValueError("dispatch claim shape drifted")
            if document["schema_version"] != "moodboard.non-idempotent-dispatch-claim.v1":
                raise ValueError("dispatch claim schema drifted")
            _validate_uuid(document["dispatch_claim_id"], "dispatch_claim_id")
            _validate_uuid(document["attempt_id"], "attempt_id")
            for field in (
                "submitted_event_id",
                "capability_snapshot_id",
                "expected_head_event_id",
                "request_key_sha256",
                "normalized_request_id",
                "wire_request_sha256",
            ):
                _validate_digest(document[field], field)
            _validate_timestamp(document["claimed_at"], "claimed_at")
            if (
                not isinstance(document["expected_next_sequence"], int)
                or isinstance(document["expected_next_sequence"], bool)
                or not 1 <= document["expected_next_sequence"] <= 5
            ):
                raise ValueError("dispatch claim sequence is invalid")
            if (
                not isinstance(document["wire_request_byte_count"], int)
                or isinstance(document["wire_request_byte_count"], bool)
                or not 1 <= document["wire_request_byte_count"] <= _MAX_WIRE_REQUEST_BYTES
            ):
                raise ValueError("dispatch claim byte count is invalid")
        except (AttemptJournalError, KeyError, TypeError, ValueError) as error:
            raise JournalCorruptionError("stored dispatch claim does not revalidate") from error
        redundant = {
            "attempt_id": row["attempt_id"],
            "dispatch_claim_id": row["dispatch_claim_id"],
            "submitted_event_id": row["submitted_event_id"],
            "capability_snapshot_id": row["capability_snapshot_id"],
            "expected_head_event_id": row["expected_head_event_id"],
            "expected_next_sequence": row["expected_next_sequence"],
            "wire_request_sha256": row["wire_request_sha256"],
            "wire_request_byte_count": row["wire_request_byte_count"],
        }
        if any(document[field] != value for field, value in redundant.items()):
            raise JournalCorruptionError("dispatch claim columns disagree with canonical bytes")
        return document

    def _load_run(self, connection: sqlite3.Connection, run_id: str) -> GenerationRun:
        row = connection.execute(
            "SELECT generation_run_id, canonical_sha256, document_json "
            "FROM generation_runs WHERE generation_run_id=?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise JournalNotFoundError("generation run is not registered")
        artifact = self._decode_artifact(
            self._row_blob(row, "document_json"), row["canonical_sha256"], GenerationRun
        )
        assert isinstance(artifact, GenerationRun)
        if artifact.generation_run_id != row["generation_run_id"]:
            raise JournalCorruptionError("generation run row identity drifted")
        return artifact

    def _load_attempt(self, connection: sqlite3.Connection, attempt_id: str) -> GenerationAttempt:
        row = connection.execute(
            "SELECT attempt_id, generation_run_id, ordinal, canonical_sha256, document_json "
            "FROM generation_attempts WHERE attempt_id=?",
            (attempt_id,),
        ).fetchone()
        if row is None:
            raise JournalNotFoundError("generation attempt is not registered")
        artifact = self._decode_artifact(
            self._row_blob(row, "document_json"), row["canonical_sha256"], GenerationAttempt
        )
        assert isinstance(artifact, GenerationAttempt)
        if (
            artifact.attempt_id != row["attempt_id"]
            or artifact.generation_run_id != row["generation_run_id"]
            or artifact.ordinal != row["ordinal"]
        ):
            raise JournalCorruptionError("generation attempt row identity drifted")
        if (
            artifact.ordinal != 1
            or artifact.retry_of is not None
            or artifact.fallback_of is not None
        ):
            raise JournalCorruptionError(
                "stored retry/fallback attempt is unsupported by the P0 journal"
            )
        return artifact

    def _load_events(
        self, connection: sqlite3.Connection, attempt_id: str
    ) -> list[GenerationAttemptEvent]:
        rows = connection.execute(
            "SELECT attempt_id, sequence, attempt_event_id, state, canonical_sha256, document_json "
            "FROM attempt_events WHERE attempt_id=? ORDER BY sequence LIMIT 6",
            (attempt_id,),
        ).fetchall()
        if len(rows) > 5:
            raise JournalCorruptionError("attempt event history exceeds the v1 bound")
        events: list[GenerationAttemptEvent] = []
        for row in rows:
            artifact = self._decode_artifact(
                self._row_blob(row, "document_json"),
                row["canonical_sha256"],
                GenerationAttemptEvent,
            )
            assert isinstance(artifact, GenerationAttemptEvent)
            if (
                artifact.attempt_id != row["attempt_id"]
                or artifact.sequence != row["sequence"]
                or artifact.attempt_event_id != row["attempt_event_id"]
                or artifact.state != row["state"]
            ):
                raise JournalCorruptionError("attempt event row identity drifted")
            if artifact.state == "succeeded":
                raise JournalCorruptionError(
                    "stored succeeded event has no durable evidence-gate record"
                )
            events.append(artifact)
        return events

    @staticmethod
    def _run_attempt_binding(run: GenerationRun, attempt: GenerationAttempt) -> None:
        pairs = (
            (attempt.generation_run_id, run.generation_run_id),
            (attempt.intent_packet_id, run.intent_packet_id),
            (attempt.requested_provider, run.requested_provider),
            (attempt.requested_model, run.requested_model),
            (attempt.provider_route_policy_id, run.provider_route_policy_id),
        )
        if any(left != right for left, right in pairs):
            raise AttemptJournalError("generation attempt does not bind its registered run")

    def _load_bound_attempt(
        self, connection: sqlite3.Connection, attempt_id: str
    ) -> GenerationAttempt:
        attempt = self._load_attempt(connection, attempt_id)
        run = self._load_run(connection, attempt.generation_run_id)
        try:
            self._run_attempt_binding(run, attempt)
        except AttemptJournalError as error:
            raise JournalCorruptionError(
                "stored generation attempt does not bind its immutable run"
            ) from error
        return attempt

    def _verify_claim_for_attempt(
        self,
        connection: sqlite3.Connection,
        attempt: GenerationAttempt,
        events: list[GenerationAttemptEvent],
    ) -> sqlite3.Row | None:
        row = connection.execute(
            "SELECT * FROM non_idempotent_dispatch_claims WHERE attempt_id=?",
            (attempt.attempt_id,),
        ).fetchone()
        submitted = [event for event in events if event.state == "submitted"]
        if bool(row) != bool(submitted):
            raise JournalCorruptionError("claim and submitted event must coexist")
        if row is None:
            return None
        if len(submitted) != 1 or submitted[0].attempt_event_id != row["submitted_event_id"]:
            raise JournalCorruptionError("claim points at the wrong submitted event")
        claim = self._decode_claim(row)
        submitted_event = submitted[0]
        submitted_index = submitted_event.sequence - 1
        if (
            submitted_event.sequence != claim["expected_next_sequence"]
            or submitted_index <= 0
            or submitted_index >= len(events)
        ):
            raise JournalCorruptionError("dispatch claim sequence has no exact predecessor")
        predecessor = events[submitted_index - 1]
        if (
            predecessor.state != "prepared"
            or predecessor.attempt_event_id != claim["expected_head_event_id"]
        ):
            raise JournalCorruptionError("dispatch claim head is not the prepared predecessor")
        capability = self._decode_artifact(
            self._row_blob(row, "capability_json"),
            row["capability_sha256"],
            ProviderCapabilitySnapshot,
        )
        assert isinstance(capability, ProviderCapabilitySnapshot)
        try:
            self._capability_binding(attempt, capability)
        except DispatchClaimConflictError as error:
            raise JournalCorruptionError(
                "stored dispatch capability does not bind its attempt"
            ) from error
        if (
            claim["capability_snapshot_id"] != capability.capability_snapshot_id
            or claim["request_key_sha256"] != attempt.request_key_sha256
            or claim["normalized_request_id"] != attempt.normalized_request_id
        ):
            raise JournalCorruptionError("dispatch claim authority disagrees with its attempt")
        expected_event = seal_provider_artifact(
            {
                "schema_version": EVENT_VERSION,
                "attempt_id": attempt.attempt_id,
                "sequence": claim["expected_next_sequence"],
                "state": "submitted",
                "recorded_at": claim["claimed_at"],
                "detail": {"kind": "submitted", "provider_handle": None},
            }
        )
        if expected_event != submitted_event:
            raise JournalCorruptionError("submitted event is not the claim-derived occurrence")
        return row

    @staticmethod
    def _provider_receipt_binding(
        attempt: GenerationAttempt,
        receipt: ProviderReceipt,
        predecessor: AttemptState,
    ) -> None:
        if (
            receipt.attempt_id != attempt.attempt_id
            or receipt.normalized_request_id != attempt.normalized_request_id
            or receipt.requested_provider != attempt.requested_provider
            or receipt.requested_model != attempt.requested_model
            or receipt.selected_route_id != attempt.selected_route_id
            or receipt.http_status != 200
        ):
            raise ProviderEvidenceConflictError(
                "provider receipt does not bind the immutable attempt"
            )
        if predecessor.last_recorded_at is not None and _timestamp_key(
            receipt.received_at
        ) < _timestamp_key(predecessor.last_recorded_at):
            raise ProviderEvidenceConflictError(
                "provider receipt time regresses behind the immutable attempt"
            )
        if (
            predecessor.provider_handle is not None
            and receipt.provider_handle != predecessor.provider_handle
        ):
            raise ProviderEvidenceConflictError(
                "provider receipt changed the immutable provider handle"
            )

    def _validated_provider_payloads(
        self,
        receipt: ProviderReceipt,
        raw_response_bytes: bytes | None,
        output_bytes: tuple[bytes, ...],
    ) -> None:
        if type(output_bytes) is not tuple or len(output_bytes) > _MAX_PROVIDER_OUTPUTS:
            raise JournalSecurityError("provider output evidence count is outside the bound")
        if len(receipt.outputs) != len(output_bytes):
            raise ProviderEvidenceConflictError(
                "provider output count does not match the immutable receipt"
            )
        total = 0
        for index, payload in enumerate(output_bytes):
            if type(payload) is not bytes:
                raise JournalSecurityError("provider output evidence must be exact bytes")
            self._scanner.scan_evidence_bytes(payload, maximum=_MAX_PROVIDER_OUTPUT_BYTES)
            total += len(payload)
            if total > _MAX_PROVIDER_OUTPUT_TOTAL_BYTES:
                raise JournalSecurityError("provider output evidence exceeds the cumulative bound")
            output = receipt.outputs[index] if index < len(receipt.outputs) else None
            if not isinstance(output, Mapping) or (
                output.get("output_index") != index
                or output.get("content_ref") != blake3(payload).hexdigest()
                or output.get("content_sha256") != hashlib.sha256(payload).hexdigest()
                or output.get("byte_count") != len(payload)
            ):
                raise ProviderEvidenceConflictError(
                    "provider output bytes do not match the immutable receipt"
                )

        raw_claim = receipt.raw_response
        if raw_claim.get("state") == "not_retained":
            if raw_response_bytes is not None:
                raise ProviderEvidenceConflictError(
                    "a not-retained provider receipt cannot persist raw response bytes"
                )
            return
        if raw_response_bytes is None:
            raise ProviderEvidenceConflictError(
                "a retained provider receipt requires exact raw bytes"
            )
        if type(raw_response_bytes) is not bytes:
            raise JournalSecurityError("raw provider response must be exact bytes")
        self._scanner.scan_evidence_bytes(raw_response_bytes, maximum=_MAX_PROVIDER_RAW_BYTES)
        if (
            raw_claim.get("state") != "retained"
            or raw_claim.get("content_ref") != blake3(raw_response_bytes).hexdigest()
            or raw_claim.get("content_sha256") != hashlib.sha256(raw_response_bytes).hexdigest()
            or raw_claim.get("byte_count") != len(raw_response_bytes)
        ):
            raise ProviderEvidenceConflictError(
                "raw provider response bytes do not match the immutable receipt"
            )

    def _decode_evidence_blob(self, value: Any, *, maximum: int, description: str) -> bytes:
        if type(value) is not bytes or not 1 <= len(value) <= maximum:
            raise JournalCorruptionError(f"stored {description} exceeds its byte bound")
        try:
            self._scanner.scan_evidence_bytes(value, maximum=maximum)
        except JournalSecurityError as error:
            raise JournalCorruptionError(
                f"stored {description} violates the secret boundary"
            ) from error
        return value

    def _load_provider_response(
        self,
        connection: sqlite3.Connection,
        attempt: GenerationAttempt,
        events: list[GenerationAttemptEvent],
        row: sqlite3.Row,
    ) -> StoredProviderResponse:
        receipt_artifact = self._decode_artifact(
            self._row_blob(row, "receipt_json"), row["receipt_sha256"], ProviderReceipt
        )
        assert isinstance(receipt_artifact, ProviderReceipt)
        receipt = receipt_artifact
        if (
            row["attempt_id"] != attempt.attempt_id
            or row["provider_receipt_id"] != receipt.provider_receipt_id
        ):
            raise JournalCorruptionError("provider response row identity drifted")
        response_events = [event for event in events if event.state == "response_received"]
        if len(response_events) != 1:
            raise JournalCorruptionError(
                "provider response evidence requires one response_received event"
            )
        event = response_events[0]
        predecessor = self._reduce_stored(attempt, events[: event.sequence - 1])
        try:
            self._provider_receipt_binding(attempt, receipt, predecessor)
        except ProviderEvidenceConflictError as error:
            raise JournalCorruptionError(
                "stored provider receipt does not bind its attempt"
            ) from error
        expected_event = seal_provider_artifact(
            {
                "schema_version": EVENT_VERSION,
                "attempt_id": attempt.attempt_id,
                "sequence": event.sequence,
                "state": "response_received",
                "recorded_at": receipt.received_at,
                "detail": {
                    "kind": "response_received",
                    "provider_receipt_id": receipt.provider_receipt_id,
                },
            }
        )
        if (
            not isinstance(expected_event, GenerationAttemptEvent)
            or event != expected_event
            or row["response_event_id"] != event.attempt_event_id
        ):
            raise JournalCorruptionError(
                "provider response event does not match its immutable receipt"
            )

        raw_claim = receipt.raw_response
        raw_value = row["raw_bytes"]
        if raw_claim.get("state") == "not_retained":
            if any(
                row[field] is not None
                for field in (
                    "raw_content_ref",
                    "raw_content_sha256",
                    "raw_byte_count",
                    "raw_bytes",
                )
            ):
                raise JournalCorruptionError(
                    "not-retained provider response unexpectedly stores raw bytes"
                )
            raw_bytes: bytes | None = None
        else:
            raw_bytes = self._decode_evidence_blob(
                raw_value,
                maximum=_MAX_PROVIDER_RAW_BYTES,
                description="raw provider response",
            )
            if (
                row["raw_content_ref"] != raw_claim.get("content_ref")
                or row["raw_content_sha256"] != raw_claim.get("content_sha256")
                or row["raw_byte_count"] != raw_claim.get("byte_count")
                or row["raw_content_ref"] != blake3(raw_bytes).hexdigest()
                or row["raw_content_sha256"] != hashlib.sha256(raw_bytes).hexdigest()
                or row["raw_byte_count"] != len(raw_bytes)
            ):
                raise JournalCorruptionError(
                    "stored raw provider response disagrees with its receipt"
                )

        output_rows = connection.execute(
            "SELECT attempt_id, output_index, provider_receipt_id, content_ref, "
            "content_sha256, byte_count, payload_bytes "
            "FROM provider_response_outputs WHERE attempt_id=? ORDER BY output_index LIMIT 9",
            (attempt.attempt_id,),
        ).fetchall()
        if len(output_rows) != len(receipt.outputs) or len(output_rows) > _MAX_PROVIDER_OUTPUTS:
            raise JournalCorruptionError("stored provider output count disagrees with its receipt")
        payloads: list[bytes] = []
        total = 0
        for index, (output_row, output_claim) in enumerate(
            zip(output_rows, receipt.outputs, strict=True)
        ):
            if not isinstance(output_claim, Mapping):
                raise JournalCorruptionError("stored provider output claim is not an object")
            payload = self._decode_evidence_blob(
                output_row["payload_bytes"],
                maximum=_MAX_PROVIDER_OUTPUT_BYTES,
                description="provider output",
            )
            total += len(payload)
            if total > _MAX_PROVIDER_OUTPUT_TOTAL_BYTES:
                raise JournalCorruptionError("stored provider outputs exceed the cumulative bound")
            if (
                output_row["attempt_id"] != attempt.attempt_id
                or output_row["output_index"] != index
                or output_row["provider_receipt_id"] != receipt.provider_receipt_id
                or output_row["content_ref"] != output_claim.get("content_ref")
                or output_row["content_sha256"] != output_claim.get("content_sha256")
                or output_row["byte_count"] != output_claim.get("byte_count")
                or output_claim.get("output_index") != index
                or output_row["content_ref"] != blake3(payload).hexdigest()
                or output_row["content_sha256"] != hashlib.sha256(payload).hexdigest()
                or output_row["byte_count"] != len(payload)
            ):
                raise JournalCorruptionError("stored provider output disagrees with its receipt")
            payloads.append(payload)
        return StoredProviderResponse(receipt, event, raw_bytes, tuple(payloads))

    def _verify_provider_response_for_attempt(
        self,
        connection: sqlite3.Connection,
        attempt: GenerationAttempt,
        events: list[GenerationAttemptEvent],
    ) -> StoredProviderResponse | None:
        row = connection.execute(
            "SELECT * FROM provider_responses WHERE attempt_id=?",
            (attempt.attempt_id,),
        ).fetchone()
        response_events = [event for event in events if event.state == "response_received"]
        if row is None:
            orphan = connection.execute(
                "SELECT 1 FROM provider_response_outputs WHERE attempt_id=? LIMIT 1",
                (attempt.attempt_id,),
            ).fetchone()
            if response_events or orphan is not None:
                raise JournalCorruptionError(
                    "response_received event and provider evidence must coexist"
                )
            return None
        if len(response_events) != 1:
            raise JournalCorruptionError(
                "provider evidence and response_received event must coexist"
            )
        return self._load_provider_response(connection, attempt, events, row)

    @staticmethod
    def _reduce_stored(
        attempt: GenerationAttempt, events: list[GenerationAttemptEvent]
    ) -> AttemptState:
        try:
            return reduce_attempt_events(attempt, events)
        except AttemptStateError as error:
            raise JournalCorruptionError("stored attempt history is not reducible") from error

    def register_run(self, value: ArtifactInput) -> RegistrationResult:
        artifact, canonical, digest = _canonical_artifact(value, GenerationRun, self._scanner)
        assert isinstance(artifact, GenerationRun)
        connection = self._begin()
        try:
            existing = connection.execute(
                "SELECT canonical_sha256, document_json FROM generation_runs "
                "WHERE generation_run_id=?",
                (artifact.generation_run_id,),
            ).fetchone()
            if existing is not None:
                if self._row_blob(existing, "document_json") != canonical:
                    raise ImmutableRecordConflictError(
                        "generation run identity already has other bytes"
                    )
                stored = self._load_run(connection, artifact.generation_run_id)
                self._commit(connection)
                return RegistrationResult(False, stored)
            connection.execute(
                "INSERT INTO generation_runs VALUES (?, ?, ?)",
                (artifact.generation_run_id, digest, canonical),
            )
            self._commit(connection)
            return RegistrationResult(True, artifact)
        except AttemptJournalError:
            self._rollback(connection)
            raise
        except sqlite3.Error as error:
            self._rollback(connection)
            raise AttemptJournalError("generation run registration failed") from error
        except BaseException:
            self._rollback(connection)
            raise

    def register_attempt(self, value: ArtifactInput) -> RegistrationResult:
        artifact, canonical, digest = _canonical_artifact(value, GenerationAttempt, self._scanner)
        assert isinstance(artifact, GenerationAttempt)
        if (
            artifact.ordinal != 1
            or artifact.retry_of is not None
            or artifact.fallback_of is not None
        ):
            raise AttemptJournalError(
                "P0 journal defers retry/fallback attempts and accepts only ordinal one"
            )
        connection = self._begin()
        try:
            run = self._load_run(connection, artifact.generation_run_id)
            self._run_attempt_binding(run, artifact)
            existing = connection.execute(
                "SELECT canonical_sha256, document_json FROM generation_attempts "
                "WHERE attempt_id=?",
                (artifact.attempt_id,),
            ).fetchone()
            if existing is not None:
                if self._row_blob(existing, "document_json") != canonical:
                    raise ImmutableRecordConflictError(
                        "generation attempt identity already has other bytes"
                    )
                stored = self._load_attempt(connection, artifact.attempt_id)
                self._commit(connection)
                return RegistrationResult(False, stored)
            occupied = connection.execute(
                "SELECT attempt_id FROM generation_attempts "
                "WHERE generation_run_id=? AND ordinal=?",
                (artifact.generation_run_id, artifact.ordinal),
            ).fetchone()
            if occupied is not None:
                raise ImmutableRecordConflictError("generation run ordinal is already occupied")
            connection.execute(
                "INSERT INTO generation_attempts VALUES (?, ?, ?, ?, ?)",
                (
                    artifact.attempt_id,
                    artifact.generation_run_id,
                    artifact.ordinal,
                    digest,
                    canonical,
                ),
            )
            self._commit(connection)
            return RegistrationResult(True, artifact)
        except AttemptJournalError:
            self._rollback(connection)
            raise
        except sqlite3.Error as error:
            self._rollback(connection)
            raise AttemptJournalError("generation attempt registration failed") from error
        except BaseException:
            self._rollback(connection)
            raise

    def read_run(self, generation_run_id: str) -> GenerationRun:
        _validate_uuid(generation_run_id, "generation_run_id")
        connection = self._connect()
        try:
            return self._load_run(connection, generation_run_id)
        except sqlite3.Error as error:
            raise JournalCorruptionError("generation run could not be read") from error
        finally:
            connection.close()

    def read_attempt(self, attempt_id: str) -> GenerationAttempt:
        _validate_uuid(attempt_id, "attempt_id")
        connection = self._connect()
        try:
            return self._load_bound_attempt(connection, attempt_id)
        except sqlite3.Error as error:
            raise JournalCorruptionError("generation attempt could not be read") from error
        finally:
            connection.close()

    def read_events(self, attempt_id: str) -> tuple[GenerationAttemptEvent, ...]:
        _validate_uuid(attempt_id, "attempt_id")
        connection = self._begin_read()
        try:
            attempt = self._load_bound_attempt(connection, attempt_id)
            events = self._load_events(connection, attempt_id)
            self._reduce_stored(attempt, events)
            self._verify_claim_for_attempt(connection, attempt, events)
            self._verify_provider_response_for_attempt(connection, attempt, events)
            result = tuple(events)
            self._commit(connection)
            return result
        except AttemptJournalError:
            self._rollback(connection)
            raise
        except sqlite3.Error as error:
            self._rollback(connection)
            raise JournalCorruptionError("attempt events could not be read") from error
        except BaseException:
            self._rollback(connection)
            raise

    def read_state(self, attempt_id: str) -> AttemptState:
        _validate_uuid(attempt_id, "attempt_id")
        connection = self._begin_read()
        try:
            attempt = self._load_bound_attempt(connection, attempt_id)
            events = self._load_events(connection, attempt_id)
            state = self._reduce_stored(attempt, events)
            self._verify_claim_for_attempt(connection, attempt, events)
            self._verify_provider_response_for_attempt(connection, attempt, events)
            self._commit(connection)
            return state
        except AttemptJournalError:
            self._rollback(connection)
            raise
        except sqlite3.Error as error:
            self._rollback(connection)
            raise JournalCorruptionError("attempt state could not be read") from error
        except BaseException:
            self._rollback(connection)
            raise

    @staticmethod
    def _check_head(
        state: AttemptState,
        expected_head_event_id: str | None,
        expected_next_sequence: int,
    ) -> None:
        if (
            state.head_event_id != expected_head_event_id
            or state.next_sequence != expected_next_sequence
        ):
            raise StaleAttemptHeadError("attempt compare-and-append head is stale")

    def append_event(
        self,
        value: ArtifactInput,
        *,
        expected_head_event_id: str | None,
        expected_next_sequence: int,
    ) -> EventAppendResult:
        if expected_head_event_id is not None:
            _validate_digest(expected_head_event_id, "expected_head_event_id")
        if (
            not isinstance(expected_next_sequence, int)
            or isinstance(expected_next_sequence, bool)
            or not 1 <= expected_next_sequence <= 5
        ):
            raise AttemptJournalError("expected_next_sequence must be a bounded integer")
        artifact, canonical, digest = _canonical_artifact(
            value, GenerationAttemptEvent, self._scanner
        )
        assert isinstance(artifact, GenerationAttemptEvent)
        if artifact.state == "submitted":
            raise DispatchClaimConflictError(
                "submitted is reserved for the atomic dispatch-claim transaction"
            )
        if artifact.state == "response_received":
            raise AttemptJournalError(
                "response_received requires the atomic provider-evidence transaction"
            )
        if artifact.state == "succeeded":
            raise AttemptJournalError("succeeded requires the later durable terminal gate")
        connection = self._begin()
        try:
            attempt = self._load_bound_attempt(connection, artifact.attempt_id)
            events = self._load_events(connection, artifact.attempt_id)
            state = self._reduce_stored(attempt, events)
            self._verify_claim_for_attempt(connection, attempt, events)
            self._verify_provider_response_for_attempt(connection, attempt, events)
            occupied = connection.execute(
                "SELECT canonical_sha256, document_json FROM attempt_events "
                "WHERE attempt_id=? AND sequence=?",
                (artifact.attempt_id, artifact.sequence),
            ).fetchone()
            if occupied is not None:
                if self._row_blob(occupied, "document_json") != canonical:
                    raise ImmutableRecordConflictError("attempt event slot already has other bytes")
                stored = next(event for event in events if event.sequence == artifact.sequence)
                self._commit(connection)
                return EventAppendResult(False, stored, state)
            self._check_head(state, expected_head_event_id, expected_next_sequence)
            try:
                next_state = reduce_attempt_events(attempt, [*events, artifact])
            except AttemptStateError as error:
                raise AttemptJournalError(
                    "attempt event violates the transition contract"
                ) from error
            try:
                connection.execute(
                    "INSERT INTO attempt_events VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        artifact.attempt_id,
                        artifact.sequence,
                        artifact.attempt_event_id,
                        artifact.state,
                        digest,
                        canonical,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise ImmutableRecordConflictError(
                    "attempt event identity is already used by another immutable slot"
                ) from error
            self._commit(connection)
            return EventAppendResult(True, artifact, next_state)
        except AttemptJournalError:
            self._rollback(connection)
            raise
        except sqlite3.Error as error:
            self._rollback(connection)
            raise AttemptJournalError("attempt event append failed") from error
        except BaseException:
            self._rollback(connection)
            raise

    @staticmethod
    def _capability_binding(
        attempt: GenerationAttempt, capability: ProviderCapabilitySnapshot
    ) -> None:
        if (
            capability.capability_snapshot_id != attempt.capability_snapshot_id
            or capability.provider != attempt.requested_provider
            or capability.requested_model != attempt.requested_model
            or capability.adapter_revision != attempt.adapter_revision
        ):
            raise DispatchClaimConflictError("capability does not bind the immutable attempt")
        idempotency = capability.idempotency
        if (
            idempotency["provider_accepts_key"] is not False
            or idempotency["ambiguous_transport_retransmit_safe"] is not False
            or idempotency["deduplication_scope"] is not None
            or idempotency["retention_seconds"] is not None
        ):
            raise DispatchClaimConflictError(
                "non-idempotent claim requires a capability with no safe retransmission"
            )

    def claim_non_idempotent_dispatch(
        self,
        attempt_id: str,
        capability_value: ArtifactInput,
        *,
        expected_head_event_id: str,
        expected_next_sequence: int,
        dispatch_claim_id: str,
        claimed_at: str,
        wire_request_sha256: str,
        wire_request_byte_count: int,
    ) -> DispatchClaimResult:
        # Scan and validate every caller-controlled value before lazy database creation.
        self._scanner.scan(
            {
                "attempt_id": attempt_id,
                "expected_head_event_id": expected_head_event_id,
                "dispatch_claim_id": dispatch_claim_id,
                "claimed_at": claimed_at,
                "wire_request_sha256": wire_request_sha256,
            }
        )
        _validate_uuid(attempt_id, "attempt_id")
        _validate_uuid(dispatch_claim_id, "dispatch_claim_id")
        _validate_digest(expected_head_event_id, "expected_head_event_id")
        _validate_digest(wire_request_sha256, "wire_request_sha256")
        _validate_timestamp(claimed_at, "claimed_at")
        if (
            not isinstance(expected_next_sequence, int)
            or isinstance(expected_next_sequence, bool)
            or not 1 <= expected_next_sequence <= 5
        ):
            raise DispatchClaimConflictError("expected_next_sequence is outside the v1 bound")
        if (
            not isinstance(wire_request_byte_count, int)
            or isinstance(wire_request_byte_count, bool)
            or not 1 <= wire_request_byte_count <= _MAX_WIRE_REQUEST_BYTES
        ):
            raise DispatchClaimConflictError("wire request byte count is outside the bound")
        try:
            capability, capability_bytes, capability_sha = _canonical_artifact(
                capability_value, ProviderCapabilitySnapshot, self._scanner
            )
        except JournalSecurityError:
            raise
        except AttemptJournalError as error:
            raise DispatchClaimConflictError(
                "capability is invalid or does not bind the attempt"
            ) from error
        assert isinstance(capability, ProviderCapabilitySnapshot)
        submitted_raw = seal_provider_artifact(
            {
                "schema_version": EVENT_VERSION,
                "attempt_id": attempt_id,
                "sequence": expected_next_sequence,
                "state": "submitted",
                "recorded_at": claimed_at,
                "detail": {"kind": "submitted", "provider_handle": None},
            }
        )
        assert isinstance(submitted_raw, GenerationAttemptEvent)
        submitted_bytes = canonical_json_bytes(to_json_dict(submitted_raw))
        submitted_sha = hashlib.sha256(submitted_bytes).hexdigest()
        claim_document = {
            "schema_version": "moodboard.non-idempotent-dispatch-claim.v1",
            "dispatch_claim_id": dispatch_claim_id,
            "attempt_id": attempt_id,
            "submitted_event_id": submitted_raw.attempt_event_id,
            "capability_snapshot_id": capability.capability_snapshot_id,
            "expected_head_event_id": expected_head_event_id,
            "expected_next_sequence": expected_next_sequence,
            "request_key_sha256": None,
            "normalized_request_id": None,
            "wire_request_sha256": wire_request_sha256,
            "wire_request_byte_count": wire_request_byte_count,
            "claimed_at": claimed_at,
        }
        connection = self._begin()
        try:
            attempt = self._load_bound_attempt(connection, attempt_id)
            self._capability_binding(attempt, capability)
            claim_document["request_key_sha256"] = attempt.request_key_sha256
            claim_document["normalized_request_id"] = attempt.normalized_request_id
            self._scanner.scan(claim_document)
            claim_bytes = canonical_json_bytes(claim_document)
            self._scanner.scan_bytes(claim_bytes)
            claim_sha = hashlib.sha256(claim_bytes).hexdigest()
            events = self._load_events(connection, attempt_id)
            state = self._reduce_stored(attempt, events)
            existing = self._verify_claim_for_attempt(connection, attempt, events)
            self._verify_provider_response_for_attempt(connection, attempt, events)
            if existing is not None:
                if self._row_blob(existing, "claim_json") != claim_bytes:
                    raise DispatchClaimConflictError(
                        "attempt already has a different dispatch claim"
                    )
                if self._row_blob(existing, "capability_json") != capability_bytes:
                    raise DispatchClaimConflictError("dispatch capability bytes changed on replay")
                stored = next(
                    (
                        event
                        for event in events
                        if event.attempt_event_id == existing["submitted_event_id"]
                    ),
                    None,
                )
                if stored is None or stored != submitted_raw:
                    raise JournalCorruptionError("dispatch claim and submitted event disagree")
                self._commit(connection)
                return DispatchClaimResult(False, False, dispatch_claim_id, stored, state)
            self._check_head(state, expected_head_event_id, expected_next_sequence)
            if state.state != "prepared":
                raise DispatchClaimConflictError("dispatch claim requires a prepared attempt")
            try:
                next_state = reduce_attempt_events(attempt, [*events, submitted_raw])
            except AttemptStateError as error:
                raise DispatchClaimConflictError(
                    "submitted event violates attempt state"
                ) from error
            try:
                connection.execute(
                    "INSERT INTO attempt_events VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        attempt_id,
                        submitted_raw.sequence,
                        submitted_raw.attempt_event_id,
                        submitted_raw.state,
                        submitted_sha,
                        submitted_bytes,
                    ),
                )
                connection.execute(
                    "INSERT INTO non_idempotent_dispatch_claims VALUES "
                    "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        attempt_id,
                        dispatch_claim_id,
                        submitted_raw.attempt_event_id,
                        capability.capability_snapshot_id,
                        expected_head_event_id,
                        expected_next_sequence,
                        wire_request_sha256,
                        wire_request_byte_count,
                        claim_sha,
                        claim_bytes,
                        capability_sha,
                        capability_bytes,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise DispatchClaimConflictError(
                    "dispatch claim identity or immutable slot is already occupied"
                ) from error
            self._commit(connection)
            return DispatchClaimResult(True, True, dispatch_claim_id, submitted_raw, next_state)
        except AttemptJournalError:
            self._rollback(connection)
            raise
        except sqlite3.Error as error:
            self._rollback(connection)
            raise AttemptJournalError("dispatch claim transaction failed") from error
        except BaseException:
            self._rollback(connection)
            raise

    def read_provider_response(self, attempt_id: str) -> StoredProviderResponse:
        """Read and revalidate one attempt's immutable private provider evidence."""

        _validate_uuid(attempt_id, "attempt_id")
        connection = self._begin_read()
        try:
            attempt = self._load_bound_attempt(connection, attempt_id)
            events = self._load_events(connection, attempt_id)
            self._reduce_stored(attempt, events)
            self._verify_claim_for_attempt(connection, attempt, events)
            stored = self._verify_provider_response_for_attempt(connection, attempt, events)
            if stored is None:
                raise JournalNotFoundError("provider response evidence is not stored")
            self._commit(connection)
            return stored
        except AttemptJournalError:
            self._rollback(connection)
            raise
        except sqlite3.Error as error:
            self._rollback(connection)
            raise JournalCorruptionError("provider response evidence could not be read") from error
        except BaseException:
            self._rollback(connection)
            raise

    def publish_provider_response(
        self,
        receipt_value: ArtifactInput,
        raw_response_bytes: bytes | None,
        output_bytes: tuple[bytes, ...],
        *,
        expected_head_event_id: str,
        expected_next_sequence: int,
    ) -> ProviderResponsePublishResult:
        """Atomically store a receipt, exact private bytes, and ``response_received``."""

        self._scanner.scan(
            {
                "expected_head_event_id": expected_head_event_id,
                "expected_next_sequence": expected_next_sequence,
            }
        )
        _validate_digest(expected_head_event_id, "expected_head_event_id")
        if (
            not isinstance(expected_next_sequence, int)
            or isinstance(expected_next_sequence, bool)
            or not 1 <= expected_next_sequence <= 5
        ):
            raise AttemptJournalError("expected_next_sequence must be a bounded integer")
        receipt_artifact, receipt_bytes, receipt_sha = _canonical_artifact(
            receipt_value, ProviderReceipt, self._scanner
        )
        assert isinstance(receipt_artifact, ProviderReceipt)
        receipt = receipt_artifact
        self._validated_provider_payloads(receipt, raw_response_bytes, output_bytes)

        connection = self._begin()
        try:
            attempt = self._load_bound_attempt(connection, receipt.attempt_id)
            events = self._load_events(connection, attempt.attempt_id)
            state = self._reduce_stored(attempt, events)
            self._verify_claim_for_attempt(connection, attempt, events)
            stored = self._verify_provider_response_for_attempt(connection, attempt, events)

            # Lost acknowledgements and concurrent exact publishers must recover from the
            # immutable row before a now-stale compare-and-append token is considered.
            if stored is not None:
                if (
                    stored.receipt != receipt
                    or stored.raw_response_bytes != raw_response_bytes
                    or stored.output_bytes != output_bytes
                ):
                    raise ProviderEvidenceConflictError(
                        "attempt already has different provider-response evidence"
                    )
                self._commit(connection)
                return ProviderResponsePublishResult(False, stored.receipt, stored.event, state)

            self._provider_receipt_binding(attempt, receipt, state)
            self._check_head(state, expected_head_event_id, expected_next_sequence)
            if state.state not in {"submitted", "outcome_unknown"}:
                raise ProviderEvidenceConflictError(
                    "provider response requires a submitted or outcome_unknown attempt"
                )
            response_artifact = seal_provider_artifact(
                {
                    "schema_version": EVENT_VERSION,
                    "attempt_id": attempt.attempt_id,
                    "sequence": expected_next_sequence,
                    "state": "response_received",
                    "recorded_at": receipt.received_at,
                    "detail": {
                        "kind": "response_received",
                        "provider_receipt_id": receipt.provider_receipt_id,
                    },
                }
            )
            if not isinstance(response_artifact, GenerationAttemptEvent):
                raise AttemptJournalError("response_received event could not be derived")
            response_bytes = canonical_json_bytes(to_json_dict(response_artifact))
            self._scanner.scan_bytes(response_bytes)
            response_sha = hashlib.sha256(response_bytes).hexdigest()
            try:
                next_state = reduce_attempt_events(attempt, [*events, response_artifact])
            except AttemptStateError as error:
                raise ProviderEvidenceConflictError(
                    "provider response violates the attempt transition contract"
                ) from error

            raw_claim = receipt.raw_response
            if raw_claim.get("state") == "retained":
                raw_content_ref = raw_claim.get("content_ref")
                raw_content_sha256 = raw_claim.get("content_sha256")
                raw_byte_count = raw_claim.get("byte_count")
            else:
                raw_content_ref = None
                raw_content_sha256 = None
                raw_byte_count = None
            try:
                connection.execute(
                    "INSERT INTO attempt_events VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        attempt.attempt_id,
                        response_artifact.sequence,
                        response_artifact.attempt_event_id,
                        response_artifact.state,
                        response_sha,
                        response_bytes,
                    ),
                )
                connection.execute(
                    "INSERT INTO provider_responses "
                    "(attempt_id, provider_receipt_id, response_event_id, receipt_sha256, "
                    "receipt_json, raw_content_ref, raw_content_sha256, raw_byte_count, raw_bytes) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        attempt.attempt_id,
                        receipt.provider_receipt_id,
                        response_artifact.attempt_event_id,
                        receipt_sha,
                        receipt_bytes,
                        raw_content_ref,
                        raw_content_sha256,
                        raw_byte_count,
                        raw_response_bytes,
                    ),
                )
                for index, payload in enumerate(output_bytes):
                    output_claim = receipt.outputs[index]
                    assert isinstance(output_claim, Mapping)
                    connection.execute(
                        "INSERT INTO provider_response_outputs "
                        "(attempt_id, output_index, provider_receipt_id, content_ref, "
                        "content_sha256, byte_count, payload_bytes) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            attempt.attempt_id,
                            index,
                            receipt.provider_receipt_id,
                            output_claim["content_ref"],
                            output_claim["content_sha256"],
                            output_claim["byte_count"],
                            payload,
                        ),
                    )
            except sqlite3.IntegrityError as error:
                raise ProviderEvidenceConflictError(
                    "provider-response identity or immutable slot is already occupied"
                ) from error
            self._commit(connection)
            return ProviderResponsePublishResult(True, receipt, response_artifact, next_state)
        except AttemptJournalError:
            self._rollback(connection)
            raise
        except sqlite3.Error as error:
            self._rollback(connection)
            raise AttemptJournalError("provider response transaction failed") from error
        except BaseException:
            self._rollback(connection)
            raise

    def verify_integrity(self) -> None:
        connection = self._begin_read()
        try:
            if connection.execute("PRAGMA quick_check(1)").fetchone()[0] != "ok":
                raise JournalCorruptionError("SQLite quick_check failed")
            if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise JournalCorruptionError("SQLite foreign-key integrity failed")
            for row in connection.execute("SELECT generation_run_id FROM generation_runs"):
                self._load_run(connection, row["generation_run_id"])
            for row in connection.execute("SELECT attempt_id FROM generation_attempts"):
                attempt = self._load_bound_attempt(connection, row["attempt_id"])
                events = self._load_events(connection, attempt.attempt_id)
                self._reduce_stored(attempt, events)
                self._verify_claim_for_attempt(connection, attempt, events)
                self._verify_provider_response_for_attempt(connection, attempt, events)
            self._commit(connection)
        except AttemptJournalError:
            self._rollback(connection)
            raise
        except sqlite3.Error as error:
            self._rollback(connection)
            raise JournalCorruptionError("attempt journal integrity scan failed") from error
        except BaseException:
            self._rollback(connection)
            raise
