"""RED transport and credential-erasure contracts for the real OpenRouter harness.

Every boundary in this module is replaced with an in-memory fake.  These tests must never read
Keychain state or create a socket; they pin the exact one-shot HTTPS shape and require credential
material to be absent from the exception graph that a caller can inspect after a failure.
"""

from __future__ import annotations

import contextlib
import subprocess
import traceback
from collections.abc import Iterator, Mapping, Sequence
from io import BytesIO
from pathlib import Path
from types import TracebackType
from typing import Any

import pytest
from PIL import Image

import eval.openrouter_real_e2e as real_e2e
from eval.openrouter_real_e2e import (
    OpenRouterRealE2EError,
    direct_openrouter_https_transport,
    load_openrouter_keychain_token,
)
from tests.test_openrouter_real_e2e_confirmation import _execute, _prepare, _write_context

_TOKEN = "sk-or-v1-TRANSPORT-SECRET-SENTINEL-0123456789"
_REQUEST_BODY = b'{"model":"qwen/qwen-image-3","n":1}'
_RESPONSE_BODY = b'{"created":1786930000,"data":[]}'
_PRODUCTION_ROOT = Path(real_e2e.__file__).resolve().parents[1]


class _FakeSocket:
    def __init__(self, events: list[tuple[Any, ...]]) -> None:
        self._events = events

    def settimeout(self, seconds: float) -> None:
        self._events.append(("socket.settimeout", seconds))


class _FakeResponse:
    def __init__(
        self,
        *,
        status: int,
        body: bytes,
        headers: Sequence[tuple[str, str]],
        events: list[tuple[Any, ...]],
    ) -> None:
        self.status = status
        self._body = body
        self._headers = tuple(headers)
        self._events = events
        self._read = False

    def getheaders(self) -> list[tuple[str, str]]:
        self._events.append(("response.getheaders",))
        return list(self._headers)

    def getheader(self, name: str) -> str | None:
        self._events.append(("response.getheader", name))
        lowered = name.lower()
        matches = [value for key, value in self._headers if key.lower() == lowered]
        return matches[0] if matches else None

    def read1(self, amount: int) -> bytes:
        self._events.append(("response.read1", amount))
        if self._read:
            return b""
        self._read = True
        return self._body


class _FakeConnection:
    def __init__(
        self,
        *,
        response: _FakeResponse,
        events: list[tuple[Any, ...]],
        connect_failure: BaseException | None = None,
    ) -> None:
        self.sock: _FakeSocket | None = _FakeSocket(events)
        self._response = response
        self._events = events
        self._connect_failure = connect_failure

    def set_debuglevel(self, level: int) -> None:
        self._events.append(("connection.set_debuglevel", level))

    def connect(self) -> None:
        self._events.append(("connection.connect",))
        if self._connect_failure is not None:
            raise self._connect_failure

    def putrequest(self, method: str, path: str, **kwargs: Any) -> None:
        self._events.append(("connection.putrequest", method, path, kwargs))

    def putheader(self, name: str, value: str) -> None:
        self._events.append(("connection.putheader", name, value))

    def endheaders(self, body: bytes) -> None:
        self._events.append(("connection.endheaders", body))

    def getresponse(self) -> _FakeResponse:
        self._events.append(("connection.getresponse",))
        return self._response

    def close(self) -> None:
        self._events.append(("connection.close",))


def _install_direct_https_fakes(
    monkeypatch: pytest.MonkeyPatch,
    *,
    status: int = 200,
    response_body: bytes = _RESPONSE_BODY,
    response_headers: Sequence[tuple[str, str]] | None = None,
    connect_failure: BaseException | None = None,
) -> tuple[list[tuple[Any, ...]], list[_FakeConnection], object]:
    events: list[tuple[Any, ...]] = []
    connections: list[_FakeConnection] = []
    context = object()
    headers = response_headers or (
        ("Content-Length", str(len(response_body))),
        ("Content-Type", "application/json"),
    )

    def connection_factory(
        host: str,
        port: int,
        *,
        timeout: float,
        context: object,
    ) -> _FakeConnection:
        events.append(("HTTPSConnection", host, port, timeout, context))
        response = _FakeResponse(
            status=status,
            body=response_body,
            headers=headers,
            events=events,
        )
        connection = _FakeConnection(
            response=response,
            events=events,
            connect_failure=connect_failure,
        )
        connections.append(connection)
        return connection

    @contextlib.contextmanager
    def wall_deadline(seconds: float) -> Iterator[None]:
        events.append(("wall_deadline.enter", seconds))
        try:
            yield
        finally:
            events.append(("wall_deadline.exit", seconds))

    monotonic_values = iter((100.0, 100.25, 100.5))
    monkeypatch.setattr(real_e2e, "_tls_context", lambda: context)
    monkeypatch.setattr(real_e2e, "_wall_deadline", wall_deadline)
    monkeypatch.setattr(real_e2e.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(real_e2e.http.client, "HTTPSConnection", connection_factory)
    return events, connections, context


def _events_named(events: Sequence[tuple[Any, ...]], name: str) -> list[tuple[Any, ...]]:
    return [event for event in events if event[0] == name]


def _safe_repr(value: object) -> str:
    try:
        return repr(value)
    except Exception as error:  # pragma: no cover - defensive inspection only
        return f"<repr-failed:{type(error).__name__}>"


def _exception_graph_material(error: BaseException) -> str:
    """Render public exception state plus locals from production frames only.

    Test-frame locals necessarily contain the sentinel used by the assertion, so they are omitted.
    Cause and context exception messages are still traversed regardless of their source frame.
    """

    material: list[str] = []
    pending: list[BaseException] = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        material.extend(
            (
                type(current).__name__,
                str(current),
                _safe_repr(current),
                _safe_repr(current.args),
            )
        )
        for linked in (current.__cause__, current.__context__):
            if linked is not None:
                pending.append(linked)
        trace: TracebackType | None = current.__traceback__
        while trace is not None:
            frame = trace.tb_frame
            frame_path = Path(frame.f_code.co_filename).resolve()
            if frame_path.is_relative_to(_PRODUCTION_ROOT) and "tests" not in frame_path.parts:
                material.append(frame.f_code.co_name)
                material.extend(
                    f"{name}={_safe_repr(value)}" for name, value in frame.f_locals.items()
                )
            trace = trace.tb_next
    return "\n".join(material)


def _source_png() -> bytes:
    image = Image.new("RGB", (64, 48), (105, 160, 205))
    encoded = BytesIO()
    image.save(encoded, format="PNG", optimize=False)
    return encoded.getvalue()


def test_direct_transport_uses_one_exact_origin_post_and_one_body_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events, connections, tls_context = _install_direct_https_fakes(monkeypatch)

    result = direct_openrouter_https_transport(body=_REQUEST_BODY, bearer_token=_TOKEN)

    assert result.status == 200
    assert result.headers == {"content-type": "application/json"}
    assert result.body == _RESPONSE_BODY
    assert result.elapsed_milliseconds == 500
    assert len(connections) == 1
    assert _events_named(events, "HTTPSConnection") == [
        ("HTTPSConnection", "openrouter.ai", 443, 10.0, tls_context)
    ]
    assert _events_named(events, "connection.connect") == [("connection.connect",)]
    assert _events_named(events, "connection.putrequest") == [
        (
            "connection.putrequest",
            "POST",
            "/api/v1/images",
            {"skip_accept_encoding": True},
        )
    ]
    assert _events_named(events, "connection.putheader") == [
        ("connection.putheader", "Authorization", f"Bearer {_TOKEN}"),
        ("connection.putheader", "Content-Type", "application/json"),
        ("connection.putheader", "Accept", "application/json"),
        ("connection.putheader", "Accept-Encoding", "identity"),
        ("connection.putheader", "Connection", "close"),
        ("connection.putheader", "Content-Length", str(len(_REQUEST_BODY))),
    ]
    assert _events_named(events, "connection.endheaders") == [
        ("connection.endheaders", _REQUEST_BODY)
    ]
    assert _events_named(events, "connection.getresponse") == [("connection.getresponse",)]
    assert _events_named(events, "wall_deadline.enter") == [("wall_deadline.enter", 210.0)]
    assert _events_named(events, "socket.settimeout") == [("socket.settimeout", 209.75)]


def test_direct_transport_returns_redirect_without_following_or_retrying(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events, connections, _ = _install_direct_https_fakes(
        monkeypatch,
        status=307,
        response_headers=(
            ("Content-Length", str(len(_RESPONSE_BODY))),
            ("Content-Type", "application/json"),
            ("Location", "https://attacker.invalid/capture"),
        ),
    )

    result = direct_openrouter_https_transport(body=_REQUEST_BODY, bearer_token=_TOKEN)

    assert result.status == 307
    assert len(connections) == 1
    assert len(_events_named(events, "connection.connect")) == 1
    assert len(_events_named(events, "connection.putrequest")) == 1
    assert len(_events_named(events, "connection.endheaders")) == 1
    assert len(_events_named(events, "connection.getresponse")) == 1


@pytest.mark.parametrize(
    "response_headers",
    [
        (("Content-Length", "2"), ("Content-Length", "2")),
        (("Content-Length", "2"), ("Transfer-Encoding", "chunked")),
        (("Content-Length", "2"), ("Content-Encoding", "gzip")),
    ],
)
def test_direct_transport_rejects_ambiguous_or_encoded_response_framing_after_one_send(
    monkeypatch: pytest.MonkeyPatch,
    response_headers: Sequence[tuple[str, str]],
) -> None:
    events, connections, _ = _install_direct_https_fakes(
        monkeypatch,
        response_body=b"{}",
        response_headers=response_headers,
    )

    with pytest.raises(RuntimeError, match="^OpenRouter transport failed$") as raised:
        direct_openrouter_https_transport(body=_REQUEST_BODY, bearer_token=_TOKEN)

    assert raised.value.__cause__ is None
    assert len(connections) == 1
    assert len(_events_named(events, "connection.connect")) == 1
    assert len(_events_named(events, "connection.endheaders")) == 1
    assert len(_events_named(events, "connection.getresponse")) == 1


def test_direct_transport_classifies_wall_expiry_as_timeout_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events, connections, _ = _install_direct_https_fakes(
        monkeypatch,
        connect_failure=real_e2e._DeadlineExpired("synthetic wall expiry"),
    )

    with pytest.raises(TimeoutError, match="^OpenRouter transport deadline exceeded$") as raised:
        direct_openrouter_https_transport(body=_REQUEST_BODY, bearer_token=_TOKEN)

    assert type(raised.value) is TimeoutError
    assert raised.value.__cause__ is None
    assert len(connections) == 1
    assert len(_events_named(events, "connection.connect")) == 1
    assert _events_named(events, "connection.putrequest") == []
    assert _events_named(events, "connection.endheaders") == []


def test_keychain_nonzero_exit_drops_secret_from_exception_graph_and_traceback_locals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[tuple[str, ...], Mapping[str, Any]]] = []

    def failed(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((tuple(argv), dict(kwargs)))
        return subprocess.CompletedProcess(
            argv,
            44,
            stdout=f"unusable:{_TOKEN}",
            stderr=f"security diagnostic:{_TOKEN}",
        )

    monkeypatch.setattr(real_e2e.subprocess, "run", failed)

    with pytest.raises(OpenRouterRealE2EError) as raised:
        load_openrouter_keychain_token(real_e2e.CREDENTIAL_PROFILE_ID)

    assert raised.value.code == "credential_unavailable"
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert _TOKEN not in _exception_graph_material(raised.value)
    assert calls == [
        (
            (
                "/usr/bin/security",
                "find-generic-password",
                "-s",
                "OPENROUTER_API_KEY",
                "-a",
                "khive",
                "-w",
            ),
            {
                "capture_output": True,
                "check": False,
                "text": True,
                "timeout": 15,
            },
        )
    ]


def test_injected_credential_failure_drops_secret_cause_context_and_production_locals(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    transport_calls = 0

    def failing_credential_loader(_profile_id: str) -> str:
        raise RuntimeError(f"injected credential diagnostic:{_TOKEN}")

    def forbidden_transport(**_kwargs: Any) -> Any:
        nonlocal transport_calls
        transport_calls += 1
        raise AssertionError("credential failure reached transport")

    # This is an additional tripwire: even an accidental default-boundary lookup must remain fake.
    monkeypatch.setattr(
        real_e2e.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("test attempted a real Keychain lookup")
        ),
    )

    with pytest.raises(OpenRouterRealE2EError) as raised:
        challenge_dir = tmp_path / "credential-failure"
        _prepare(challenge_dir, source_bytes=_source_png())
        context_path = _write_context(challenge_dir)
        _execute(
            challenge_dir,
            context_path,
            credential_loader=failing_credential_loader,
            transport=forbidden_transport,
        )

    assert raised.value.code == "credential_unavailable"
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert _TOKEN not in _exception_graph_material(raised.value)
    assert transport_calls == 0
    rendered = "".join(traceback.format_exception(raised.value))
    assert _TOKEN not in rendered
    assert _TOKEN.encode() not in b"".join(
        path.read_bytes() for path in (tmp_path / "credential-failure").rglob("*") if path.is_file()
    )


def test_transport_failure_drops_bearer_from_exception_graph_and_production_locals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events, connections, _ = _install_direct_https_fakes(
        monkeypatch,
        connect_failure=RuntimeError(f"socket diagnostic:{_TOKEN}"),
    )

    with pytest.raises(RuntimeError, match="^OpenRouter transport failed$") as raised:
        direct_openrouter_https_transport(body=_REQUEST_BODY, bearer_token=_TOKEN)

    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert _TOKEN not in _exception_graph_material(raised.value)
    assert len(connections) == 1
    assert len(_events_named(events, "connection.connect")) == 1
    assert _events_named(events, "connection.putrequest") == []


@pytest.mark.parametrize("exception_type", [RuntimeError, KeyboardInterrupt])
def test_post_keychain_failure_never_reaches_public_exception_graph_or_artifacts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    exception_type: type[BaseException],
) -> None:
    challenge_dir = tmp_path / f"post-key-{exception_type.__name__}"
    _prepare(challenge_dir, source_bytes=_source_png())
    context_path = _write_context(challenge_dir)

    def explode(*_args: Any, **_kwargs: Any) -> Any:
        raise exception_type(f"post-key diagnostic:{_TOKEN}")

    monkeypatch.setattr(real_e2e, "AttemptJournal", explode)

    with pytest.raises(OpenRouterRealE2EError) as raised:
        _execute(
            challenge_dir,
            context_path,
            credential_loader=lambda _profile: _TOKEN,
            transport=lambda **_kwargs: (_ for _ in ()).throw(
                AssertionError("journal failure reached transport")
            ),
        )

    assert raised.value.code == "execution_failed"
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert _TOKEN not in _exception_graph_material(raised.value)
    for path in challenge_dir.rglob("*"):
        if path.is_file() and not path.is_symlink():
            assert _TOKEN.encode() not in path.read_bytes()
