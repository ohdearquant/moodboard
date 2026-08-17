"""Durable, one-use Studio confirmation authority for paid provider dispatch.

The ledger is a persistence substrate for a trusted Studio backend.  Recording authority or an
explicit confirmation is therefore a server-side operation; provider execution only inspects and
consumes rows that already exist.  The database intentionally lives outside any challenge
directory so rolling local run artifacts back cannot restore a spent authorization.
"""

from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import os
import sqlite3
import stat
import time
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import asdict, dataclass
from functools import cache
from pathlib import Path
from typing import Any, Final, Literal, NoReturn

from moodboard.contracts import (
    canonical_json_bytes,
    compute_document_identity,
    is_canonical_utc_timestamp,
)

__all__ = [
    "LEDGER_REVISION",
    "ConfirmationConsumptionResult",
    "ConfirmationLedgerEntry",
    "ConfirmationRecordResult",
    "StudioConfirmationLedger",
    "StudioConfirmationLedgerError",
    "StudioSessionAuthority",
]

LEDGER_REVISION: Final = "moodboard.studio-confirmation-ledger.v1"

_APPLICATION_ID: Final = 0x4D425343  # MBSC
_USER_VERSION: Final = 1
_MAX_DATABASE_BYTES: Final = 64 * 1024 * 1024
_MAX_DOCUMENT_BYTES: Final = 4 * 1024 * 1024
_MAX_TREE_DEPTH: Final = 32
_MAX_TREE_NODES: Final = 20_000
_CONTEXT_VERSION: Final = "moodboard.openrouter-real-e2e-confirmation-context.v1"
_CHALLENGE_VERSION: Final = "moodboard.openrouter-real-e2e-confirmation-challenge.v1"
_DIGEST_LENGTH: Final = 64

_ERROR_CODES: Final = frozenset(
    {
        "ledger_security_error",
        "ledger_corruption",
        "ledger_version_unsupported",
        "ledger_busy",
        "authority_invalid",
        "authority_conflict",
        "authority_inactive",
        "confirmation_invalid",
        "confirmation_conflict",
        "confirmation_not_registered",
        "confirmation_already_consumed",
        "confirmation_expired",
        "confirmation_persistence_failed",
        "confirmation_persistence_ambiguous",
    }
)


class StudioConfirmationLedgerError(Exception):
    """Stable, value-free failure raised by the Studio confirmation ledger."""

    def __init__(self, code: str) -> None:
        measured = code if type(code) is str and code in _ERROR_CODES else "ledger_corruption"
        self.code = measured
        super().__init__(measured)


def _fail(code: str) -> NoReturn:
    raise StudioConfirmationLedgerError(code) from None


@dataclass(frozen=True, slots=True)
class StudioSessionAuthority:
    principal_id: str
    studio_session_id: str
    creative_session_id: str
    authority_epoch: int
    active_from: str
    expires_at: str


@dataclass(frozen=True, slots=True)
class ConfirmationLedgerEntry:
    confirmation_context_id: str
    challenge_id: str
    compact_summary_id: str
    principal_id: str
    studio_session_id: str
    creative_session_id: str
    authority_epoch: int
    confirmed_at: str
    expires_at: str
    state: Literal["available", "consumed"]
    consumed_at: str | None


@dataclass(frozen=True, slots=True)
class ConfirmationRecordResult:
    created: bool
    entry: ConfirmationLedgerEntry


@dataclass(frozen=True, slots=True)
class ConfirmationConsumptionResult:
    created: bool
    generation_post_authorized: bool
    entry: ConfirmationLedgerEntry


_SCHEMA_SQL: Final = """
CREATE TABLE session_authorities (
    studio_session_id TEXT NOT NULL,
    authority_epoch INTEGER NOT NULL CHECK (authority_epoch BETWEEN 1 AND 2147483647),
    principal_id TEXT NOT NULL,
    creative_session_id TEXT NOT NULL,
    active_from TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    canonical_sha256 TEXT NOT NULL,
    document_json BLOB NOT NULL,
    PRIMARY KEY (studio_session_id, authority_epoch)
);
CREATE TABLE session_revocations (
    studio_session_id TEXT NOT NULL,
    authority_epoch INTEGER NOT NULL,
    revoked_at TEXT NOT NULL,
    PRIMARY KEY (studio_session_id, authority_epoch),
    FOREIGN KEY (studio_session_id, authority_epoch)
      REFERENCES session_authorities(studio_session_id, authority_epoch)
);
CREATE TABLE confirmation_grants (
    confirmation_context_id TEXT PRIMARY KEY,
    challenge_id TEXT NOT NULL UNIQUE,
    compact_summary_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    studio_session_id TEXT NOT NULL,
    creative_session_id TEXT NOT NULL,
    authority_epoch INTEGER NOT NULL,
    confirmed_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    context_sha256 TEXT NOT NULL,
    context_json BLOB NOT NULL,
    challenge_sha256 TEXT NOT NULL,
    challenge_json BLOB NOT NULL,
    FOREIGN KEY (studio_session_id, authority_epoch)
      REFERENCES session_authorities(studio_session_id, authority_epoch)
);
CREATE TABLE confirmation_consumptions (
    confirmation_context_id TEXT PRIMARY KEY,
    challenge_id TEXT NOT NULL UNIQUE,
    consumed_at TEXT NOT NULL,
    FOREIGN KEY (confirmation_context_id)
      REFERENCES confirmation_grants(confirmation_context_id),
    FOREIGN KEY (challenge_id)
      REFERENCES confirmation_grants(challenge_id)
);
"""

_TABLES: Final = frozenset(
    {
        "session_authorities",
        "session_revocations",
        "confirmation_grants",
        "confirmation_consumptions",
    }
)
_TRIGGERS: Final = frozenset(
    f"{table}_{operation}_immutable" for table in _TABLES for operation in ("update", "delete")
)


def _immutable_trigger_sql(table: str, operation: str) -> str:
    return (
        f"CREATE TRIGGER {table}_{operation}_immutable BEFORE {operation.upper()} ON {table} "
        "BEGIN SELECT RAISE(ABORT, 'immutable confirmation row'); END"
    )


@cache
def _expected_schema_objects() -> dict[tuple[str, str], str]:
    """Render SQLite's canonical stored SQL for this exact schema and runtime."""

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


def _validate_uuid(value: Any) -> str:
    if type(value) is not str:
        _fail("confirmation_invalid")
    invalid = False
    try:
        parsed = uuid.UUID(value)
    except (AttributeError, TypeError, ValueError):
        invalid = True
        parsed = None
    if invalid or parsed is None:
        _fail("confirmation_invalid")
    if str(parsed) != value:
        _fail("confirmation_invalid")
    return value


def _validate_digest(value: Any) -> str:
    if (
        type(value) is not str
        or len(value) != _DIGEST_LENGTH
        or any(character not in "0123456789abcdef" for character in value)
    ):
        _fail("confirmation_invalid")
    return value


def _validate_timestamp(value: Any, *, code: str = "confirmation_invalid") -> str:
    if type(value) is not str or not is_canonical_utc_timestamp(value):
        _fail(code)
    return value


def _timestamp_key(value: str) -> tuple[str, int]:
    fraction = value[20:-1] if len(value) > 20 else ""
    return value[:19], int(fraction.ljust(9, "0") or "0")


def _bounded_tree(value: Any) -> None:
    stack: list[tuple[Any, int, bool]] = [(value, 1, False)]
    active: set[int] = set()
    nodes = 0
    while stack:
        current, depth, leaving = stack.pop()
        if leaving:
            active.remove(id(current))
            continue
        nodes += 1
        if depth > _MAX_TREE_DEPTH or nodes > _MAX_TREE_NODES:
            _fail("confirmation_invalid")
        if isinstance(current, Mapping):
            identity = id(current)
            if identity in active:
                _fail("confirmation_invalid")
            active.add(identity)
            stack.append((current, depth, True))
            try:
                items = tuple(current.items())
            except Exception:
                _fail("confirmation_invalid")
            stack.extend((key, depth + 1, False) for key, _item in items)
            stack.extend((item, depth + 1, False) for _key, item in items)
        elif isinstance(current, (list, tuple)):
            identity = id(current)
            if identity in active:
                _fail("confirmation_invalid")
            active.add(identity)
            stack.append((current, depth, True))
            stack.extend((item, depth + 1, False) for item in current)
        elif current is None or type(current) in {str, int, float, bool}:
            continue
        else:
            _fail("confirmation_invalid")


def _canonical_document(
    value: Mapping[str, Any],
    *,
    schema_version: str,
    identity_field: str,
    expected_keys: frozenset[str],
) -> tuple[dict[str, Any], bytes, str]:
    failed = False
    document: dict[str, Any] | None = None
    try:
        _bounded_tree(value)
        document = copy.deepcopy(dict(value))
    except Exception:
        failed = True
    if failed or document is None:
        _fail("confirmation_invalid")
    if set(document) != expected_keys or document.get("schema_version") != schema_version:
        _fail("confirmation_invalid")
    identity = _validate_digest(document.get(identity_field))
    canonical_failed = False
    expected = ""
    canonical = b""
    try:
        expected = compute_document_identity(
            document,
            schema_version=schema_version,
            identity_field=identity_field,
        )
        canonical = canonical_json_bytes(document)
    except BaseException:
        canonical_failed = True
    if canonical_failed:
        _fail("confirmation_invalid")
    if identity != expected or not 1 <= len(canonical) <= _MAX_DOCUMENT_BYTES:
        _fail("confirmation_invalid")
    return document, canonical, hashlib.sha256(canonical).hexdigest()


_CONTEXT_KEYS: Final = frozenset(
    {
        "schema_version",
        "confirmation_context_id",
        "challenge_id",
        "compact_summary_id",
        "decision",
        "authorized_generation_post_count",
        "principal_id",
        "studio_session_id",
        "creative_session_id",
        "confirmed_at",
    }
)
_CHALLENGE_KEYS: Final = frozenset(
    {
        "schema_version",
        "challenge_id",
        "compact_summary_id",
        "prepared_at",
        "expires_at",
        "directory_binding",
        "artifacts",
        "quoted_cost_usd",
        "quote_admission_limit_usd",
        "spend_limit_kind",
        "creative_session_id",
        "generation_run_id",
        "attempt_id",
        "capability_snapshot_id",
        "wire_body_sha256",
        "wire_body_byte_count",
        "packet_projection",
    }
)


@dataclass(frozen=True, slots=True)
class _CanonicalGrant:
    context: dict[str, Any]
    context_bytes: bytes
    context_sha256: str
    challenge: dict[str, Any]
    challenge_bytes: bytes
    challenge_sha256: str


def _canonical_grant(context: Mapping[str, Any], challenge: Mapping[str, Any]) -> _CanonicalGrant:
    context_document, context_bytes, context_sha256 = _canonical_document(
        context,
        schema_version=_CONTEXT_VERSION,
        identity_field="confirmation_context_id",
        expected_keys=_CONTEXT_KEYS,
    )
    challenge_document, challenge_bytes, challenge_sha256 = _canonical_document(
        challenge,
        schema_version=_CHALLENGE_VERSION,
        identity_field="challenge_id",
        expected_keys=_CHALLENGE_KEYS,
    )
    if (
        context_document.get("challenge_id") != challenge_document.get("challenge_id")
        or context_document.get("compact_summary_id")
        != challenge_document.get("compact_summary_id")
        or context_document.get("creative_session_id")
        != challenge_document.get("creative_session_id")
        or context_document.get("decision") != "approve_one_paid_call"
        or type(context_document.get("authorized_generation_post_count")) is not int
        or context_document.get("authorized_generation_post_count") != 1
    ):
        _fail("confirmation_invalid")
    for name in ("principal_id", "studio_session_id", "creative_session_id"):
        _validate_uuid(context_document.get(name))
    for name in ("creative_session_id", "generation_run_id", "attempt_id"):
        _validate_uuid(challenge_document.get(name))
    _validate_digest(challenge_document.get("compact_summary_id"))
    _validate_digest(challenge_document.get("capability_snapshot_id"))
    _validate_digest(challenge_document.get("wire_body_sha256"))
    if (
        type(challenge_document.get("wire_body_byte_count")) is not int
        or not 1 <= challenge_document["wire_body_byte_count"] <= 32 * 1024 * 1024
    ):
        _fail("confirmation_invalid")
    prepared_at = _validate_timestamp(challenge_document.get("prepared_at"))
    confirmed_at = _validate_timestamp(context_document.get("confirmed_at"))
    expires_at = _validate_timestamp(challenge_document.get("expires_at"))
    if not (
        _timestamp_key(prepared_at) <= _timestamp_key(confirmed_at) <= _timestamp_key(expires_at)
    ):
        _fail("confirmation_invalid")
    return _CanonicalGrant(
        context=context_document,
        context_bytes=context_bytes,
        context_sha256=context_sha256,
        challenge=challenge_document,
        challenge_bytes=challenge_bytes,
        challenge_sha256=challenge_sha256,
    )


def _canonical_authority(
    authority: StudioSessionAuthority,
) -> tuple[StudioSessionAuthority, bytes, str]:
    if type(authority) is not StudioSessionAuthority:
        _fail("authority_invalid")
    principal_id = _validate_uuid(authority.principal_id)
    studio_session_id = _validate_uuid(authority.studio_session_id)
    creative_session_id = _validate_uuid(authority.creative_session_id)
    if (
        type(authority.authority_epoch) is not int
        or not 1 <= authority.authority_epoch <= 2**31 - 1
    ):
        _fail("authority_invalid")
    active_from = _validate_timestamp(authority.active_from, code="authority_invalid")
    expires_at = _validate_timestamp(authority.expires_at, code="authority_invalid")
    if _timestamp_key(active_from) >= _timestamp_key(expires_at):
        _fail("authority_invalid")
    canonical_authority = StudioSessionAuthority(
        principal_id=principal_id,
        studio_session_id=studio_session_id,
        creative_session_id=creative_session_id,
        authority_epoch=authority.authority_epoch,
        active_from=active_from,
        expires_at=expires_at,
    )
    canonical = canonical_json_bytes(asdict(canonical_authority))
    return canonical_authority, canonical, hashlib.sha256(canonical).hexdigest()


def _parse_stored_document(raw: Any) -> Mapping[str, Any]:
    failed = False
    document: Any = None

    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    def reject_constant(_value: str) -> NoReturn:
        raise ValueError("non-finite number")

    try:
        measured = bytes(raw)
        document = json.loads(
            measured.decode("utf-8", errors="strict"),
            object_pairs_hook=object_pairs,
            parse_constant=reject_constant,
        )
        _bounded_tree(document)
        if not isinstance(document, Mapping) or canonical_json_bytes(document) != measured:
            failed = True
    except BaseException:
        failed = True
    if failed or not isinstance(document, Mapping):
        _fail("ledger_corruption")
    return document


def _commit(connection: sqlite3.Connection) -> None:
    """Commit wrapper retained as the explicit lost-ack fault-injection seam."""

    connection.execute("COMMIT")


def _path_components(path: Path) -> Iterator[Path]:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        yield current


def _validate_secure_parent(path: Path) -> None:
    for component in _path_components(path.parent):
        failed = False
        try:
            metadata = component.lstat()
        except OSError:
            failed = True
            metadata = None
        if failed or metadata is None:
            _fail("ledger_security_error")
        if stat.S_ISLNK(metadata.st_mode):
            _fail("ledger_security_error")
    failed = False
    try:
        metadata = path.parent.stat()
    except OSError:
        failed = True
        metadata = None
    if failed or metadata is None:
        _fail("ledger_security_error")
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        _fail("ledger_security_error")


def _validate_private_file(
    path: Path,
    *,
    allow_missing: bool,
    allow_empty: bool = False,
) -> None:
    missing = False
    failed = False
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        missing = True
        metadata = None
    except OSError:
        failed = True
        metadata = None
    if missing:
        if allow_missing:
            return
        _fail("ledger_security_error")
    if failed or metadata is None:
        _fail("ledger_security_error")
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or (metadata.st_size != 0 and not 0 < metadata.st_size <= _MAX_DATABASE_BYTES)
        or (metadata.st_size == 0 and not allow_empty)
    ):
        _fail("ledger_security_error")


def _file_metadata(path: Path) -> os.stat_result:
    failed = False
    try:
        metadata = path.lstat()
    except OSError:
        failed = True
        metadata = None
    if failed or metadata is None:
        _fail("ledger_security_error")
    return metadata


def _safe_path(value: str | os.PathLike[str]) -> Path:
    try:
        raw = os.fspath(value)
    except BaseException:
        _fail("ledger_security_error")
    if (
        type(raw) is not str
        or raw in {"", ":memory:"}
        or raw.startswith("file:")
        or not os.path.isabs(raw)
        or raw != os.path.normpath(raw)
    ):
        _fail("ledger_security_error")
    path = Path(raw)
    _validate_secure_parent(path)
    # allow_empty: a zero-byte remnant of an interrupted bootstrap is constructible and is
    # adopted by the next initialize, exactly like a missing file — both require the same
    # owner-only write access and neither holds recorded history.
    _validate_private_file(path, allow_missing=True, allow_empty=True)
    for suffix in ("-wal", "-shm", "-journal", ".init.lock"):
        _validate_private_file(Path(f"{path}{suffix}"), allow_missing=True, allow_empty=True)
    return path


class StudioConfirmationLedger:
    """Owner-private append-only Studio authority and confirmation-consumption ledger."""

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        busy_timeout_ms: int = 5000,
    ) -> None:
        path_failed = False
        measured: Path | None = None
        try:
            measured = _safe_path(path)
        except BaseException:
            path_failed = True
        if path_failed or measured is None:
            _fail("ledger_security_error")
        if type(busy_timeout_ms) is not int or not 1 <= busy_timeout_ms <= 60_000:
            _fail("ledger_security_error")
        self._path = measured
        self._busy_timeout_ms = busy_timeout_ms

    @property
    def path(self) -> Path:
        return self._path

    def _validate_parent(self) -> None:
        _validate_secure_parent(self._path)

    @staticmethod
    def _validate_file(path: Path, *, allow_empty: bool = False) -> None:
        _validate_private_file(path, allow_missing=False, allow_empty=allow_empty)

    def _create_file(self) -> bool:
        self._validate_parent()
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        failed = False
        try:
            descriptor = os.open(self._path, flags, 0o600)
        except FileExistsError:
            self._validate_file(self._path, allow_empty=True)
            # A zero-byte remnant of an interrupted bootstrap is adopted and re-initialized.
            # Serialized by the bootstrap lock; once the schema commits the file is
            # non-empty, so a later opener takes the verify path instead.
            return _file_metadata(self._path).st_size == 0
        except OSError:
            failed = True
            descriptor = None
        if failed or descriptor is None:
            _fail("ledger_security_error")
        finalized = True
        try:
            os.close(descriptor)
            os.chmod(self._path, 0o600)
        except OSError:
            finalized = False
        if not finalized:
            self._discard_failed_new_ledger()
            _fail("ledger_security_error")
        return True

    def _discard_failed_new_ledger(self) -> None:
        for suffix in ("-shm", "-wal", "-journal", ""):
            candidate = Path(f"{self._path}{suffix}")
            with suppress(OSError):
                metadata = candidate.lstat()
                if (
                    stat.S_ISREG(metadata.st_mode)
                    and metadata.st_uid == os.getuid()
                    and metadata.st_nlink == 1
                ):
                    candidate.unlink()

    @contextmanager
    def _bootstrap_lock(self) -> Iterator[None]:
        lock_path = Path(f"{self._path}.init.lock")
        _validate_private_file(lock_path, allow_missing=True, allow_empty=True)
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        failed = False
        try:
            descriptor = os.open(lock_path, flags, 0o600)
        except OSError:
            failed = True
            descriptor = None
        if failed or descriptor is None:
            _fail("ledger_security_error")
        deadline = time.monotonic() + self._busy_timeout_ms / 1000
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or metadata.st_nlink != 1
                or stat.S_IMODE(metadata.st_mode) != 0o600
            ):
                _fail("ledger_security_error")
            while True:
                timed_out = False
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        timed_out = True
                if timed_out:
                    _fail("ledger_busy")
                if not timed_out:
                    time.sleep(0.01)
            yield
        finally:
            with suppress(OSError):
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _connect(self, *, initialize: bool = False) -> sqlite3.Connection:
        self._validate_parent()
        created = self._create_file() if initialize else False
        if not self._path.exists() or self._path.is_symlink():
            _fail("ledger_security_error")
        self._validate_file(self._path, allow_empty=created)
        before = _file_metadata(self._path)
        connection: sqlite3.Connection | None = None
        failure_code: str | None = None
        try:
            connection = sqlite3.connect(
                self._path,
                timeout=self._busy_timeout_ms / 1000,
                isolation_level=None,
            )
            connection.row_factory = sqlite3.Row
            connection.enable_load_extension(False)
            if hasattr(connection, "setlimit"):
                connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, _MAX_DOCUMENT_BYTES + 4096)
                connection.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 256 * 1024)
                connection.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)
                connection.setlimit(sqlite3.SQLITE_LIMIT_TRIGGER_DEPTH, 8)
            connection.execute(f"PRAGMA busy_timeout={self._busy_timeout_ms}")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA trusted_schema=OFF")
            connection.execute("PRAGMA mmap_size=0")
            if created:
                self._configure_durability(connection)
                self._initialize_schema(connection)
            else:
                self._verify_metadata(connection)
                self._configure_durability(connection)
            self._verify_metadata(connection)
            after = _file_metadata(self._path)
            if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                _fail("ledger_security_error")
            for suffix in ("-wal", "-shm"):
                sidecar = Path(f"{self._path}{suffix}")
                if sidecar.exists() or sidecar.is_symlink():
                    self._validate_file(sidecar, allow_empty=True)
        except StudioConfirmationLedgerError:
            if connection is not None:
                with suppress(sqlite3.Error):
                    connection.close()
            if created:
                self._discard_failed_new_ledger()
            raise
        except sqlite3.OperationalError as error:
            if connection is not None:
                with suppress(sqlite3.Error):
                    connection.close()
            if created:
                self._discard_failed_new_ledger()
            failure_code = "ledger_busy" if "locked" in str(error).lower() else "ledger_corruption"
        except sqlite3.DatabaseError:
            if connection is not None:
                with suppress(sqlite3.Error):
                    connection.close()
            if created:
                self._discard_failed_new_ledger()
            failure_code = "ledger_corruption"
        if failure_code is not None:
            _fail(failure_code)
        assert connection is not None
        return connection

    @staticmethod
    def _configure_durability(connection: sqlite3.Connection) -> None:
        mode = connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]
        if str(mode).lower() != "wal":
            _fail("ledger_security_error")
        connection.execute("PRAGMA synchronous=FULL")
        if connection.execute("PRAGMA synchronous").fetchone()[0] != 2:
            _fail("ledger_security_error")
        page_size = connection.execute("PRAGMA page_size").fetchone()[0]
        if type(page_size) is not int or page_size <= 0:
            _fail("ledger_corruption")
        maximum_pages = _MAX_DATABASE_BYTES // page_size
        observed = connection.execute(f"PRAGMA max_page_count={maximum_pages}").fetchone()[0]
        if observed != maximum_pages:
            _fail("ledger_security_error")
        connection.execute("PRAGMA wal_autocheckpoint=1000")
        connection.execute(f"PRAGMA journal_size_limit={_MAX_DATABASE_BYTES}")

    @staticmethod
    def _initialize_schema(connection: sqlite3.Connection) -> None:
        triggers = ";\n".join(
            _immutable_trigger_sql(table, operation)
            for table in sorted(_TABLES)
            for operation in ("update", "delete")
        )
        try:
            connection.executescript(
                "BEGIN IMMEDIATE;\n"
                f"{_SCHEMA_SQL}\n"
                f"{triggers};\n"
                f"PRAGMA application_id={_APPLICATION_ID};\n"
                f"PRAGMA user_version={_USER_VERSION};\n"
                "COMMIT;"
            )
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

    @staticmethod
    def _verify_metadata(connection: sqlite3.Connection) -> None:
        application_id = connection.execute("PRAGMA application_id").fetchone()[0]
        user_version = connection.execute("PRAGMA user_version").fetchone()[0]
        if application_id != _APPLICATION_ID:
            _fail("ledger_corruption")
        if user_version != _USER_VERSION:
            _fail("ledger_version_unsupported")
        objects = connection.execute(
            "SELECT type, name, sql FROM sqlite_master "
            "WHERE type IN ('table', 'index', 'trigger') AND sql IS NOT NULL"
        ).fetchall()
        tables = {row["name"] for row in objects if row["type"] == "table"}
        triggers = {row["name"] for row in objects if row["type"] == "trigger"}
        if tables != _TABLES or triggers != _TRIGGERS:
            _fail("ledger_corruption")
        actual_schema = {(row["type"], row["name"]): row["sql"] for row in objects}
        if actual_schema != _expected_schema_objects():
            _fail("ledger_corruption")

    def _begin(self, *, initialize: bool = False) -> sqlite3.Connection:
        if initialize:
            with self._bootstrap_lock():
                connection = self._connect(initialize=True)
        else:
            connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError:
            connection.close()
        else:
            return connection
        _fail("ledger_busy")

    @staticmethod
    def _rollback_close(connection: sqlite3.Connection) -> None:
        if connection.in_transaction:
            with suppress(sqlite3.Error):
                connection.execute("ROLLBACK")
        with suppress(sqlite3.Error):
            connection.close()

    def record_session_authority(self, authority: StudioSessionAuthority) -> bool:
        measured, canonical, sha256 = _canonical_authority(authority)
        connection = self._begin(initialize=True)
        try:
            rows = connection.execute(
                "SELECT * FROM session_authorities WHERE studio_session_id=? "
                "ORDER BY authority_epoch",
                (measured.studio_session_id,),
            ).fetchall()
            rows = [self._validate_authority_row(row) for row in rows]
            exact = next(
                (row for row in rows if row["authority_epoch"] == measured.authority_epoch),
                None,
            )
            if exact is not None:
                if (
                    exact["canonical_sha256"] != sha256
                    or bytes(exact["document_json"]) != canonical
                ):
                    _fail("authority_conflict")
                connection.execute("ROLLBACK")
                connection.close()
                return False
            if rows:
                latest = rows[-1]
                if (
                    measured.authority_epoch <= latest["authority_epoch"]
                    or measured.principal_id != latest["principal_id"]
                    or measured.creative_session_id != latest["creative_session_id"]
                ):
                    _fail("authority_conflict")
            connection.execute(
                "INSERT INTO session_authorities VALUES (?,?,?,?,?,?,?,?)",
                (
                    measured.studio_session_id,
                    measured.authority_epoch,
                    measured.principal_id,
                    measured.creative_session_id,
                    measured.active_from,
                    measured.expires_at,
                    sha256,
                    canonical,
                ),
            )
            _commit(connection)
            connection.close()
            return True
        except StudioConfirmationLedgerError:
            self._rollback_close(connection)
            raise
        except BaseException:
            self._rollback_close(connection)
        _fail("confirmation_persistence_failed")

    def revoke_session(
        self,
        studio_session_id: str,
        *,
        authority_epoch: int,
        revoked_at: str,
    ) -> bool:
        session_id = _validate_uuid(studio_session_id)
        if type(authority_epoch) is not int or not 1 <= authority_epoch <= 2**31 - 1:
            _fail("authority_invalid")
        timestamp = _validate_timestamp(revoked_at, code="authority_invalid")
        connection = self._begin()
        try:
            authority = connection.execute(
                "SELECT * FROM session_authorities WHERE studio_session_id=? AND authority_epoch=?",
                (session_id, authority_epoch),
            ).fetchone()
            if authority is None:
                _fail("authority_invalid")
            authority = self._validate_authority_row(authority)
            if not (
                _timestamp_key(authority["active_from"])
                <= _timestamp_key(timestamp)
                <= _timestamp_key(authority["expires_at"])
            ):
                _fail("authority_invalid")
            existing = connection.execute(
                "SELECT revoked_at FROM session_revocations "
                "WHERE studio_session_id=? AND authority_epoch=?",
                (session_id, authority_epoch),
            ).fetchone()
            if existing is not None:
                if existing["revoked_at"] != timestamp:
                    _fail("authority_conflict")
                connection.execute("ROLLBACK")
                connection.close()
                return False
            connection.execute(
                "INSERT INTO session_revocations VALUES (?,?,?)",
                (session_id, authority_epoch, timestamp),
            )
            _commit(connection)
            connection.close()
            return True
        except StudioConfirmationLedgerError:
            self._rollback_close(connection)
            raise
        except BaseException:
            self._rollback_close(connection)
        _fail("confirmation_persistence_failed")

    def record_explicit_confirmation(
        self,
        context: Mapping[str, Any],
        challenge: Mapping[str, Any],
        *,
        authority_epoch: int,
    ) -> ConfirmationRecordResult:
        grant = _canonical_grant(context, challenge)
        if type(authority_epoch) is not int or not 1 <= authority_epoch <= 2**31 - 1:
            _fail("authority_invalid")
        connection = self._begin()
        try:
            authority = self._current_authority(
                connection,
                studio_session_id=grant.context["studio_session_id"],
            )
            if (
                authority["authority_epoch"] != authority_epoch
                or authority["principal_id"] != grant.context["principal_id"]
                or authority["creative_session_id"] != grant.context["creative_session_id"]
                or self._is_revoked(connection, authority)
            ):
                _fail("authority_inactive")
            confirmed_at = grant.context["confirmed_at"]
            expires_at = min(
                grant.challenge["expires_at"],
                authority["expires_at"],
                key=_timestamp_key,
            )
            if not (
                _timestamp_key(authority["active_from"])
                <= _timestamp_key(confirmed_at)
                <= _timestamp_key(expires_at)
            ):
                _fail("authority_inactive")
            existing_context = connection.execute(
                "SELECT * FROM confirmation_grants WHERE confirmation_context_id=?",
                (grant.context["confirmation_context_id"],),
            ).fetchone()
            existing_challenge = connection.execute(
                "SELECT * FROM confirmation_grants WHERE challenge_id=?",
                (grant.challenge["challenge_id"],),
            ).fetchone()
            existing = existing_context or existing_challenge
            if existing is not None:
                if not (
                    existing["confirmation_context_id"] == grant.context["confirmation_context_id"]
                    and existing["challenge_id"] == grant.challenge["challenge_id"]
                ):
                    _fail("confirmation_conflict")
                self._validate_grant_row(existing, grant, authority)
                entry = self._entry(connection, existing)
                connection.execute("ROLLBACK")
                connection.close()
                return ConfirmationRecordResult(created=False, entry=entry)
            connection.execute(
                "INSERT INTO confirmation_grants VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    grant.context["confirmation_context_id"],
                    grant.challenge["challenge_id"],
                    grant.context["compact_summary_id"],
                    grant.context["principal_id"],
                    grant.context["studio_session_id"],
                    grant.context["creative_session_id"],
                    authority_epoch,
                    confirmed_at,
                    expires_at,
                    grant.context_sha256,
                    grant.context_bytes,
                    grant.challenge_sha256,
                    grant.challenge_bytes,
                ),
            )
            row = connection.execute(
                "SELECT * FROM confirmation_grants WHERE confirmation_context_id=?",
                (grant.context["confirmation_context_id"],),
            ).fetchone()
            assert row is not None
            entry = self._entry(connection, row)
            _commit(connection)
            connection.close()
            return ConfirmationRecordResult(created=True, entry=entry)
        except StudioConfirmationLedgerError:
            self._rollback_close(connection)
            raise
        except BaseException:
            self._rollback_close(connection)
        _fail("confirmation_persistence_failed")

    @staticmethod
    def _validate_authority_row(row: sqlite3.Row) -> sqlite3.Row:
        invalid = False
        canonical = b""
        sha256 = ""
        try:
            authority = StudioSessionAuthority(
                principal_id=row["principal_id"],
                studio_session_id=row["studio_session_id"],
                creative_session_id=row["creative_session_id"],
                authority_epoch=row["authority_epoch"],
                active_from=row["active_from"],
                expires_at=row["expires_at"],
            )
            _measured, canonical, sha256 = _canonical_authority(authority)
        except BaseException:
            invalid = True
        if (
            invalid
            or row["canonical_sha256"] != sha256
            or type(row["document_json"]) is not bytes
            or row["document_json"] != canonical
        ):
            _fail("ledger_corruption")
        return row

    @classmethod
    def _authority_for_epoch(
        cls,
        connection: sqlite3.Connection,
        *,
        studio_session_id: str,
        authority_epoch: int,
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM session_authorities WHERE studio_session_id=? AND authority_epoch=?",
            (studio_session_id, authority_epoch),
        ).fetchone()
        if row is None:
            _fail("ledger_corruption")
        return cls._validate_authority_row(row)

    @classmethod
    def _current_authority(
        cls, connection: sqlite3.Connection, *, studio_session_id: str
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM session_authorities WHERE studio_session_id=? "
            "ORDER BY authority_epoch DESC LIMIT 1",
            (studio_session_id,),
        ).fetchone()
        if row is None:
            _fail("authority_inactive")
        return cls._validate_authority_row(row)

    @staticmethod
    def _is_revoked(connection: sqlite3.Connection, authority: sqlite3.Row) -> bool:
        row = connection.execute(
            "SELECT * FROM session_revocations WHERE studio_session_id=? AND authority_epoch=?",
            (authority["studio_session_id"], authority["authority_epoch"]),
        ).fetchone()
        if row is None:
            return False
        invalid = False
        try:
            revoked_at = _validate_timestamp(row["revoked_at"], code="ledger_corruption")
            invalid = not (
                row["studio_session_id"] == authority["studio_session_id"]
                and row["authority_epoch"] == authority["authority_epoch"]
                and _timestamp_key(authority["active_from"])
                <= _timestamp_key(revoked_at)
                <= _timestamp_key(authority["expires_at"])
            )
        except BaseException:
            invalid = True
        if invalid:
            _fail("ledger_corruption")
        return True

    @staticmethod
    def _grant_identity_matches(
        row: sqlite3.Row, grant: _CanonicalGrant, authority_epoch: int
    ) -> bool:
        return (
            row["confirmation_context_id"] == grant.context["confirmation_context_id"]
            and row["challenge_id"] == grant.challenge["challenge_id"]
            and row["authority_epoch"] == authority_epoch
            and row["context_sha256"] == grant.context_sha256
            and bytes(row["context_json"]) == grant.context_bytes
            and row["challenge_sha256"] == grant.challenge_sha256
            and bytes(row["challenge_json"]) == grant.challenge_bytes
        )

    @staticmethod
    def _validate_grant_row(
        row: sqlite3.Row,
        grant: _CanonicalGrant,
        authority: sqlite3.Row,
    ) -> sqlite3.Row:
        expected_expiry = min(
            grant.challenge["expires_at"],
            authority["expires_at"],
            key=_timestamp_key,
        )
        if (
            not StudioConfirmationLedger._grant_identity_matches(
                row,
                grant,
                authority["authority_epoch"],
            )
            or row["compact_summary_id"] != grant.context["compact_summary_id"]
            or row["principal_id"] != grant.context["principal_id"]
            or row["studio_session_id"] != grant.context["studio_session_id"]
            or row["creative_session_id"] != grant.context["creative_session_id"]
            or row["confirmed_at"] != grant.context["confirmed_at"]
            or row["expires_at"] != expected_expiry
        ):
            _fail("ledger_corruption")
        return row

    @staticmethod
    def _entry(connection: sqlite3.Connection, grant: sqlite3.Row) -> ConfirmationLedgerEntry:
        consumption = connection.execute(
            "SELECT * FROM confirmation_consumptions WHERE confirmation_context_id=?",
            (grant["confirmation_context_id"],),
        ).fetchone()
        if consumption is not None:
            invalid = False
            try:
                consumed_at = _validate_timestamp(
                    consumption["consumed_at"],
                    code="ledger_corruption",
                )
                invalid = not (
                    consumption["confirmation_context_id"] == grant["confirmation_context_id"]
                    and consumption["challenge_id"] == grant["challenge_id"]
                    and _timestamp_key(grant["confirmed_at"])
                    <= _timestamp_key(consumed_at)
                    <= _timestamp_key(grant["expires_at"])
                )
            except BaseException:
                invalid = True
            if invalid:
                _fail("ledger_corruption")
        return ConfirmationLedgerEntry(
            confirmation_context_id=grant["confirmation_context_id"],
            challenge_id=grant["challenge_id"],
            compact_summary_id=grant["compact_summary_id"],
            principal_id=grant["principal_id"],
            studio_session_id=grant["studio_session_id"],
            creative_session_id=grant["creative_session_id"],
            authority_epoch=grant["authority_epoch"],
            confirmed_at=grant["confirmed_at"],
            expires_at=grant["expires_at"],
            state="consumed" if consumption is not None else "available",
            consumed_at=consumption["consumed_at"] if consumption is not None else None,
        )

    def _load_matching_grant(
        self, connection: sqlite3.Connection, grant: _CanonicalGrant
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM confirmation_grants WHERE confirmation_context_id=?",
            (grant.context["confirmation_context_id"],),
        ).fetchone()
        if row is None:
            _fail("confirmation_not_registered")
        authority = self._authority_for_epoch(
            connection,
            studio_session_id=row["studio_session_id"],
            authority_epoch=row["authority_epoch"],
        )
        return self._validate_grant_row(row, grant, authority)

    def _validate_available(
        self,
        connection: sqlite3.Connection,
        grant: sqlite3.Row,
        *,
        measured_at: str,
    ) -> None:
        authority = self._current_authority(
            connection,
            studio_session_id=grant["studio_session_id"],
        )
        if (
            authority["authority_epoch"] != grant["authority_epoch"]
            or authority["principal_id"] != grant["principal_id"]
            or authority["creative_session_id"] != grant["creative_session_id"]
            or self._is_revoked(connection, authority)
        ):
            _fail("authority_inactive")
        key = _timestamp_key(measured_at)
        if key < _timestamp_key(grant["confirmed_at"]) or key > _timestamp_key(grant["expires_at"]):
            _fail("confirmation_expired")

    def inspect_confirmation(
        self,
        context: Mapping[str, Any],
        challenge: Mapping[str, Any],
        *,
        inspected_at: str,
    ) -> ConfirmationLedgerEntry:
        grant = _canonical_grant(context, challenge)
        measured_at = _validate_timestamp(inspected_at)
        connection = self._connect()
        try:
            connection.execute("BEGIN")
            row = self._load_matching_grant(connection, grant)
            entry = self._entry(connection, row)
            if entry.state == "available":
                self._validate_available(connection, row, measured_at=measured_at)
            connection.execute("ROLLBACK")
            connection.close()
            return entry
        except StudioConfirmationLedgerError:
            self._rollback_close(connection)
            raise
        except BaseException:
            self._rollback_close(connection)
        _fail("ledger_corruption")

    def consume_confirmation(
        self,
        context: Mapping[str, Any],
        challenge: Mapping[str, Any],
        *,
        consumed_at: str,
    ) -> ConfirmationConsumptionResult:
        grant = _canonical_grant(context, challenge)
        measured_at = _validate_timestamp(consumed_at)
        connection = self._begin()
        committed = False
        failure_code: str | None = None
        try:
            row = self._load_matching_grant(connection, grant)
            entry = self._entry(connection, row)
            if entry.state == "consumed":
                connection.execute("ROLLBACK")
                connection.close()
                return ConfirmationConsumptionResult(
                    created=False,
                    generation_post_authorized=False,
                    entry=entry,
                )
            self._validate_available(connection, row, measured_at=measured_at)
            connection.execute(
                "INSERT INTO confirmation_consumptions VALUES (?,?,?)",
                (row["confirmation_context_id"], row["challenge_id"], measured_at),
            )
            entry = ConfirmationLedgerEntry(
                confirmation_context_id=entry.confirmation_context_id,
                challenge_id=entry.challenge_id,
                compact_summary_id=entry.compact_summary_id,
                principal_id=entry.principal_id,
                studio_session_id=entry.studio_session_id,
                creative_session_id=entry.creative_session_id,
                authority_epoch=entry.authority_epoch,
                confirmed_at=entry.confirmed_at,
                expires_at=entry.expires_at,
                state="consumed",
                consumed_at=measured_at,
            )
            _commit(connection)
            committed = True
            connection.close()
            return ConfirmationConsumptionResult(
                created=True,
                generation_post_authorized=True,
                entry=entry,
            )
        except StudioConfirmationLedgerError:
            self._rollback_close(connection)
            raise
        except BaseException:
            ambiguous = committed
            if not ambiguous:
                try:
                    ambiguous = not connection.in_transaction
                except BaseException:
                    ambiguous = True
            self._rollback_close(connection)
            failure_code = (
                "confirmation_persistence_ambiguous"
                if ambiguous
                else "confirmation_persistence_failed"
            )
        assert failure_code is not None
        _fail(failure_code)

    def verify_integrity(self) -> None:
        connection = self._connect()
        unexpected = False
        try:
            connection.execute("BEGIN")
            result = connection.execute("PRAGMA integrity_check").fetchone()[0]
            if result != "ok":
                _fail("ledger_corruption")
            foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
            if foreign_keys:
                _fail("ledger_corruption")

            previous_by_session: dict[str, sqlite3.Row] = {}
            authorities = connection.execute(
                "SELECT * FROM session_authorities ORDER BY studio_session_id, authority_epoch"
            ).fetchall()
            for authority in authorities:
                authority = self._validate_authority_row(authority)
                previous = previous_by_session.get(authority["studio_session_id"])
                if previous is not None and (
                    authority["authority_epoch"] <= previous["authority_epoch"]
                    or authority["principal_id"] != previous["principal_id"]
                    or authority["creative_session_id"] != previous["creative_session_id"]
                ):
                    _fail("ledger_corruption")
                previous_by_session[authority["studio_session_id"]] = authority

            for revocation in connection.execute("SELECT * FROM session_revocations").fetchall():
                authority = self._authority_for_epoch(
                    connection,
                    studio_session_id=revocation["studio_session_id"],
                    authority_epoch=revocation["authority_epoch"],
                )
                if not self._is_revoked(connection, authority):
                    _fail("ledger_corruption")

            consumed = 0
            grants = connection.execute("SELECT * FROM confirmation_grants").fetchall()
            for row in grants:
                invalid = False
                grant: _CanonicalGrant | None = None
                try:
                    context = _parse_stored_document(row["context_json"])
                    challenge = _parse_stored_document(row["challenge_json"])
                    grant = _canonical_grant(context, challenge)
                except BaseException:
                    invalid = True
                if invalid or grant is None:
                    _fail("ledger_corruption")
                authority = self._authority_for_epoch(
                    connection,
                    studio_session_id=row["studio_session_id"],
                    authority_epoch=row["authority_epoch"],
                )
                self._validate_grant_row(row, grant, authority)
                if self._entry(connection, row).state == "consumed":
                    consumed += 1
            observed_consumptions = connection.execute(
                "SELECT COUNT(*) FROM confirmation_consumptions"
            ).fetchone()[0]
            if observed_consumptions != consumed:
                _fail("ledger_corruption")
            connection.execute("ROLLBACK")
        except StudioConfirmationLedgerError:
            self._rollback_close(connection)
            raise
        except BaseException:
            self._rollback_close(connection)
            unexpected = True
        else:
            connection.close()
        if unexpected:
            _fail("ledger_corruption")
