"""Core offline contracts for the opt-in OpenRouter real-provider evaluation.

The full lifecycle is exercised by ``test_openrouter_real_e2e_confirmation``. This module owns
shared deterministic media/response fixtures plus the quote, Keychain, and retired one-phase API
boundaries. No test accesses the network or macOS Keychain.
"""

from __future__ import annotations

import base64
import json
import subprocess
from collections.abc import Callable
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

import eval.openrouter_real_e2e as real_e2e
from eval.openrouter_real_e2e import (
    MAX_COST_USD,
    OpenRouterRealE2EError,
    load_openrouter_keychain_token,
    parse_openrouter_quote,
    run_openrouter_real_e2e,
)
from moodboard.openrouter import OpenRouterHttpResponse
from tests.test_openrouter_discovery import _DISCOVERY_BODY

_CREDENTIAL_PROFILE_ID = "00000000-0000-4000-8000-000000000005"
_TOKEN = "sk-or-v1-REAL-E2E-SECRET-SENTINEL-0123456789"


def _source_png() -> bytes:
    """One small deterministic 4:3 source with nontrivial protected pixels."""

    image = Image.new("RGB", (64, 48), (120, 185, 225))
    pixels = image.load()
    assert pixels is not None
    for y in range(24, 48):
        for x in range(64):
            pixels[x, y] = (74, 132, 70)
    for y in range(12, 38):
        for x in range(28, 36):
            pixels[x, y] = (91, 62, 35)
    encoded = BytesIO()
    image.save(encoded, format="PNG", optimize=False)
    return encoded.getvalue()


_SOURCE_BYTES = _source_png()
_OUTPUT_BYTES = _SOURCE_BYTES


def _response_body(
    *,
    cost: Decimal | None = Decimal("0.033"),
    currency: str | None = None,
) -> bytes:
    usage: dict[str, Any] = {}
    if cost is not None:
        usage["cost"] = int(cost) if cost == cost.to_integral() else float(str(cost))
    if currency is not None:
        usage["currency"] = currency
    document: dict[str, Any] = {
        "created": 1_786_930_000,
        "data": [
            {
                "b64_json": base64.b64encode(_OUTPUT_BYTES).decode("ascii"),
                "media_type": "image/png",
            }
        ],
    }
    if usage:
        document["usage"] = usage
    return json.dumps(document, separators=(",", ":")).encode("utf-8")


def _http_response(
    *,
    cost: Decimal | None = Decimal("0.033"),
    currency: str | None = None,
) -> OpenRouterHttpResponse:
    return OpenRouterHttpResponse(
        status=200,
        headers={"content-type": "application/json"},
        body=_response_body(cost=cost, currency=currency),
        elapsed_milliseconds=3210,
    )


def _uuid_supplier() -> Callable[[], str]:
    values = (f"70000000-0000-4000-8000-{index:012d}" for index in range(1, 100))
    return lambda: next(values)


def _assert_error_code(error: pytest.ExceptionInfo[OpenRouterRealE2EError], code: str) -> None:
    assert error.value.code == code
    assert _TOKEN not in str(error.value)
    assert _TOKEN not in repr(error.value)


def test_live_discovery_quote_is_exact_decimal_and_has_a_frozen_admission_limit() -> None:
    quote = parse_openrouter_quote(
        _DISCOVERY_BODY,
        input_count=1,
        output_count=1,
        resolution="1K",
    )

    assert Decimal("0.05") == MAX_COST_USD
    assert type(MAX_COST_USD) is Decimal
    assert quote == Decimal("0.033")
    assert type(quote) is Decimal
    assert quote.as_tuple() == Decimal("0.033").as_tuple()


def test_quote_requires_one_unambiguous_input_and_resolution_price() -> None:
    document = json.loads(_DISCOVERY_BODY)
    document["endpoints"][0]["pricing"].append(
        {"billable": "output_image", "unit": "image", "cost_usd": 0.03, "variant": "1k"}
    )

    with pytest.raises(OpenRouterRealE2EError) as raised:
        parse_openrouter_quote(
            json.dumps(document, separators=(",", ":")).encode("utf-8"),
            input_count=1,
            output_count=1,
            resolution="1K",
        )

    _assert_error_code(raised, "quote_ambiguous")


def test_keychain_lookup_uses_one_fixed_argv_without_shell_or_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[tuple[str, ...], dict[str, Any]]] = []

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((tuple(argv), kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout=f"{_TOKEN}\n", stderr="")

    monkeypatch.setattr(real_e2e.subprocess, "run", fake_run)

    token = load_openrouter_keychain_token(_CREDENTIAL_PROFILE_ID)

    assert token == _TOKEN
    assert len(calls) == 1
    argv, kwargs = calls[0]
    assert argv == (
        "/usr/bin/security",
        "find-generic-password",
        "-s",
        "OPENROUTER_API_KEY",
        "-a",
        "khive",
        "-w",
    )
    assert kwargs == {
        "capture_output": True,
        "check": False,
        "text": True,
        "timeout": 15,
    }


def test_keychain_failures_and_diagnostics_are_sanitized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failed(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        del kwargs
        return subprocess.CompletedProcess(
            argv,
            44,
            stdout=f"unusable {_TOKEN}",
            stderr=f"security diagnostic carrying {_TOKEN}",
        )

    monkeypatch.setattr(real_e2e.subprocess, "run", failed)

    with pytest.raises(OpenRouterRealE2EError) as raised:
        load_openrouter_keychain_token(_CREDENTIAL_PROFILE_ID)

    _assert_error_code(raised, "credential_unavailable")
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_wrong_credential_profile_never_invokes_keychain(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    def unexpected(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        del args, kwargs
        nonlocal calls
        calls += 1
        raise AssertionError("unsupported profile reached Keychain")

    monkeypatch.setattr(real_e2e.subprocess, "run", unexpected)

    with pytest.raises(OpenRouterRealE2EError) as raised:
        load_openrouter_keychain_token("00000000-0000-4000-8000-000000000099")

    _assert_error_code(raised, "credential_profile_unsupported")
    assert calls == 0


def test_retired_boolean_authorization_api_cannot_reach_any_external_boundary(
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    def unexpected(name: str) -> Callable[..., Any]:
        def called(*args: Any, **kwargs: Any) -> Any:
            del args, kwargs
            calls.append(name)
            raise AssertionError(f"retired API reached {name}")

        return called

    with pytest.raises(OpenRouterRealE2EError) as raised:
        run_openrouter_real_e2e(
            tmp_path / "retired",
            authorize_one_paid_call=True,
            _discovery_fetcher=unexpected("discovery"),
            _source_fetcher=unexpected("source"),
            _credential_loader=unexpected("credential"),
            _transport=unexpected("transport"),
            _clock=unexpected("clock"),
            _uuid4=unexpected("uuid"),
        )

    _assert_error_code(raised, "two_phase_confirmation_required")
    assert calls == []
