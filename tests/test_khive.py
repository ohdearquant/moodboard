"""The Khive process adapter and Lattice-backed encoder, without a running Khive.

The fake executable implements the same `kkernel exec --ops-file --save-file` transport the
real adapter uses.  Its model vectors are fixtures, not a substitute implementation of the
visual model; these tests measure ordering and fail-closed wire behaviour only.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import stat
import subprocess
import sys
import textwrap
import zipfile
from pathlib import Path

import numpy as np
import pytest
from blake3 import blake3
from PIL import Image

import moodboard.encoders as encoders_module
from moodboard import cli
from moodboard.board import read_board
from moodboard.encoders import KHIVE_ADAPTER_REVISION, KhiveLatticeEncoder, VisualDescriptor
from moodboard.khive import (
    KhiveClient,
    KhiveProtocolError,
    KhiveSearchHit,
    KhiveSearchResult,
)
from moodboard.preference import read_preference_feature_artifact

FAKE_KKERNEL = r"""#!/usr/bin/env python3
import base64
import hashlib
import json
import os
import pathlib
import sys
import uuid

from blake3 import blake3


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def descriptor(revision="weights-r1"):
    identity = {
        "schema_version": "moodboard.visual-descriptor.v1",
        "model_name": "qwen3.5-vlm-pooled-visual",
        "model_revision": revision,
        "checkpoint_sha256": "1" * 64,
        "inference": {"provider": "lattice-embed", "version": "0.9.0"},
        "preprocessing": {
            "revision": "moodboard-qwen35-srgb-pad32-max448-v1",
            "max_side": 448,
            "alignment": 32,
            "matte_rgb": [128, 128, 128],
            "resample": "lanczos3",
        },
        "prompt": {
            "revision": "moodboard-style-retrieval-v1",
            "sha256": "a67ae9b539c243f498c75f1ea9f19e7018860948087728d6f8e65b34eef6a66e",
        },
        "pooling": "mean_visual_tokens",
        "dimensions": 4,
        "normalization": "l2",
    }
    fingerprint = hashlib.sha256(canonical(identity).encode()).hexdigest()
    return {
        **identity,
        "model_key": f"moodboard_{fingerprint}_4",
        "fingerprint": fingerprint,
    }


args = sys.argv[1:]
if not args or args[0] != "exec":
    raise SystemExit(90)

def value(flag):
    return args[args.index(flag) + 1]

ops_path = pathlib.Path(value("--ops-file"))
save_path = pathlib.Path(value("--save-file"))
ops = [json.loads(line) for line in ops_path.read_text().splitlines()]

log_path = os.environ.get("FAKE_KKERNEL_LOG")
if log_path:
    with open(log_path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"argv": args, "ops": ops}, separators=(",", ":")) + "\n")

mode = os.environ.get("FAKE_KKERNEL_MODE", "ok")
rows = []
ingest_index = 0
for op in ops:
    tool = op["tool"]
    if tool in {"kg.create", "moodboard.model", "moodboard.ingest", "moodboard.search"}:
        if op["args"].get("namespace") != value("--namespace"):
            print(
                "operation namespace does not match kkernel attribution namespace",
                file=sys.stderr,
            )
            raise SystemExit(91)
    if tool == "moodboard.model":
        result = {"descriptor": descriptor(), "experimental": True}
        if mode == "model-extra-key":
            result["future"] = True
        elif mode == "model-missing-key":
            result.pop("experimental")
    elif tool == "moodboard.ingest":
        vector = [0.0, 0.0, 0.0, 0.0]
        vector[ingest_index % 4] = 1.0
        if mode == "constant-vector":
            vector = [1.0, 0.0, 0.0, 0.0]
        used_descriptor = descriptor("weights-r2") if mode == "drift" else descriptor()
        if mode == "wrong-dimension":
            vector = vector[:-1]
        elif mode == "bad-norm":
            vector = [0.5, 0.0, 0.0, 0.0]
        elif mode == "non-finite":
            vector = [float("nan"), 0.0, 0.0, 0.0]
        elif mode == "boolean-coordinate":
            vector = [True, 0.0, 0.0, 0.0]
        elif mode == "nested-coordinate":
            vector = [[1.0], [0.0], [0.0], [0.0]]
        elif mode == "enormous-integer":
            vector = [10 ** 400, 0, 0, 0]
        image_bytes = base64.b64decode(op["args"]["image_base64"], validate=True)
        result = {
            "asset_id": str(uuid.UUID(int=ingest_index + 1)),
            "content_ref": blake3(image_bytes).hexdigest(),
            "created": True,
            "indexed": True,
            "descriptor": used_descriptor,
            "experimental": True,
            "embedding": vector,
        }
        if mode == "ingest-extra-key":
            result["future"] = True
        elif mode == "ingest-missing-key":
            result.pop("created")
        ingest_index += 1
    elif tool == "kg.create":
        result = {
            "id": "00000000-0000-4000-8000-000000000100",
            "namespace": op["args"]["namespace"],
            "created_at": "2026-08-12T16:00:00+00:00",
            "updated_at": "2026-08-12T16:00:00+00:00",
            "kind": "artifact",
            "entity_type": "moodboard",
            "name": op["args"]["name"],
            "description": op["args"]["description"],
            "properties": op["args"]["properties"],
            "tags": op["args"]["tags"],
            "deleted_at": None,
            "merged_into": None,
            "merge_event_id": None,
            "content_ref": None,
        }
    elif tool == "moodboard.search":
        query_asset_id = op["args"]["asset_id"]
        hits = [
            {
                "asset_id": "00000000-0000-0000-0000-000000000010",
                "score": 0.75,
                "rank": 1,
                "name": "nearest visual",
                "content_ref": "a" * 64,
            },
            {
                "asset_id": "00000000-0000-0000-0000-000000000011",
                "score": -0.25,
                "rank": 2,
                "name": "second visual",
                "content_ref": "b" * 64,
            },
        ]
        if op["args"]["namespace"] == "foreign-namespace":
            hits = []
        result = {
            "query_asset_id": query_asset_id,
            "descriptor": descriptor("weights-r2")
            if mode == "search-descriptor-drift"
            else descriptor(),
            "experimental": True,
            "hits": hits,
        }
        if mode == "search-extra-key":
            result["future"] = True
        elif mode == "search-missing-key":
            result.pop("hits")
        elif mode == "search-query-mismatch":
            result["query_asset_id"] = "00000000-0000-0000-0000-000000000099"
        elif mode == "search-not-experimental":
            result["experimental"] = False
        elif mode == "search-hits-not-list":
            result["hits"] = {}
        elif mode == "search-hit-extra-key":
            hits[0]["future"] = True
        elif mode == "search-hit-missing-key":
            hits[0].pop("content_ref")
        elif mode == "search-self-hit":
            hits[0]["asset_id"] = query_asset_id
        elif mode == "search-duplicate-hit":
            hits[1]["asset_id"] = hits[0]["asset_id"]
        elif mode == "search-bad-hit-uuid":
            hits[0]["asset_id"] = "NOT-A-UUID"
        elif mode == "search-bad-content-ref":
            hits[0]["content_ref"] = "A" * 64
        elif mode == "search-bool-score":
            hits[0]["score"] = True
        elif mode == "search-huge-score":
            hits[0]["score"] = 10 ** 400
        elif mode == "search-out-of-range-score":
            hits[0]["score"] = -1.0001
        elif mode == "search-bad-rank":
            hits[0]["rank"] = 2
        elif mode == "search-bool-rank":
            hits[0]["rank"] = True
        elif mode == "search-swapped-hits":
            hits.reverse()
        elif mode == "search-score-order":
            hits[1]["score"] = 0.9
        elif mode == "search-empty-name":
            hits[0]["name"] = "  "
        elif mode == "search-null-name":
            hits[0]["name"] = None
        elif mode == "search-bad-name-type":
            hits[0]["name"] = 7
        elif mode == "search-long-name":
            hits[0]["name"] = "x" * 513
        elif mode == "search-control-name":
            hits[0]["name"] = "line one\nline two\t\x1b[31m"
        elif mode == "search-too-many-hits":
            pass
    else:
        result = {}
    rows.append({"ok": True, "result": result, "tool": tool, "usage": {}})

if mode == "partial":
    rows = rows[:-1]
if mode in {"failed-row", "failed-row-hidden"} and rows:
    rows[-1] = {"ok": False, "error": "inference failed", "tool": rows[-1]["tool"]}
if mode == "wrong-tool" and rows:
    rows[-1]["tool"] = "moodboard.some-other-result"
if mode == "success-with-error" and rows:
    rows[-1]["error"] = "contradictory failure"
if mode == "success-with-aborted" and rows:
    rows[-1]["aborted"] = True
if mode == "swapped-success" and len(rows) > 1:
    rows.reverse()

if mode == "malformed-jsonl":
    payload = b'{"ok":true\n'
else:
    payload = b"".join(
        (json.dumps(row, separators=(",", ":")) + "\n").encode() for row in rows
    )
save_path.write_bytes(payload)
checksum = hashlib.sha256(payload).hexdigest()
if mode == "bad-checksum":
    checksum = "0" * 64
manifest_path = save_path
if mode == "path-alias":
    manifest_path = save_path.with_name("alias-results.jsonl")
    manifest_path.symlink_to(save_path)
elif mode == "wrong-path":
    manifest_path = save_path.with_name("different-results.jsonl")
    manifest_path.write_bytes(payload)
manifest = {
    "path": str(manifest_path),
    "rows": len(rows),
    "per_column_null_counts": {},
    "schema_fingerprint": "fake",
    "checksum": checksum,
    "summary": {
        "aborted": 0,
        "failed": sum(not row.get("ok", False) for row in rows),
        "succeeded": sum(row.get("ok", False) for row in rows),
        "total": len(rows),
    },
}
if mode == "failed-row-hidden":
    manifest["summary"] = {"aborted": 0, "failed": 0, "succeeded": len(rows), "total": len(rows)}
print(json.dumps(manifest, separators=(",", ":")))
if mode == "nonzero":
    print("fake failure", file=sys.stderr)
    raise SystemExit(7)
"""


@pytest.fixture
def fake_kkernel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    executable = tmp_path / "kkernel-fake"
    executable.write_text(textwrap.dedent(FAKE_KKERNEL), encoding="utf-8")
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    log = tmp_path / "calls.jsonl"
    monkeypatch.setenv("FAKE_KKERNEL_LOG", str(log))
    return executable, log


def _client(executable: Path) -> KhiveClient:
    return KhiveClient(
        executable=executable,
        actor="lambda:moodboard-tests",
        namespace="moodboard-tests",
    )


def _calls(log: Path) -> list[dict]:
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


def test_encoder_batches_in_order_and_keeps_large_image_bytes_out_of_argv(fake_kkernel):
    executable, log = fake_kkernel
    encoder = KhiveLatticeEncoder(_client(executable))
    rng = np.random.default_rng(20260808)
    images = [
        rng.integers(0, 256, size=(700, 700, 3), dtype=np.uint8),
        np.full((19, 31, 3), (20, 40, 60), dtype=np.uint8),
    ]

    embedded = encoder.embed_assets(images, names=("large.png", "small.png"))

    assert embedded.shape == (2, 4)
    np.testing.assert_array_equal(embedded, np.eye(4, dtype=np.float32)[:2])
    assert [asset.asset_id for asset in encoder.last_assets] == [
        "00000000-0000-0000-0000-000000000001",
        "00000000-0000-0000-0000-000000000002",
    ]
    assert {asset.byte_identity for asset in encoder.last_assets} == {"canonical-png-rendition"}
    calls = _calls(log)
    assert [call["ops"][0]["tool"] for call in calls] == [
        "moodboard.model",
        "moodboard.ingest",
    ]
    ingest = calls[1]
    assert [op["args"]["name"] for op in ingest["ops"]] == ["large.png", "small.png"]
    assert len(ingest["ops"][0]["args"]["image_base64"]) > 1_000_000
    assert max(map(len, ingest["argv"])) < 1_000
    assert "--ops-file" in ingest["argv"]
    assert "--save-file" in ingest["argv"]
    assert "--strict" in ingest["argv"]
    assert ingest["argv"][ingest["argv"].index("--actor") + 1] == "lambda:moodboard-tests"
    assert ingest["argv"][ingest["argv"].index("--expect-actor") + 1] == ("lambda:moodboard-tests")
    assert ingest["argv"][ingest["argv"].index("--namespace") + 1] == "moodboard-tests"
    assert {op["args"]["namespace"] for op in ingest["ops"]} == {"moodboard-tests"}
    assert not Path(ingest["argv"][ingest["argv"].index("--ops-file") + 1]).exists()
    assert not Path(ingest["argv"][ingest["argv"].index("--save-file") + 1]).exists()


def test_client_passes_an_explicit_config_and_keeps_environment_fallback_optional(
    fake_kkernel, tmp_path
):
    executable, log = fake_kkernel
    config = tmp_path / "khive.toml"
    config.write_text('[runtime]\npacks = ["kg", "moodboard"]\n', encoding="utf-8")

    KhiveLatticeEncoder(
        KhiveClient(
            executable=executable,
            actor="lambda:configured",
            namespace="configured",
            config=config,
        )
    )
    KhiveLatticeEncoder(_client(executable))

    configured, fallback = _calls(log)
    assert configured["argv"][configured["argv"].index("--config") + 1] == str(config)
    assert "--config" not in fallback["argv"]


def test_client_search_returns_closed_typed_ranked_asset_locators(fake_kkernel):
    executable, log = fake_kkernel
    query = "00000000-0000-0000-0000-000000000001"

    result = _client(executable).search(query, 2)

    assert isinstance(result, KhiveSearchResult)
    assert result.query_asset_id == query
    assert result.experimental is True
    assert result.descriptor.model_name == "qwen3.5-vlm-pooled-visual"
    assert result.hits == (
        KhiveSearchHit(
            asset_id="00000000-0000-0000-0000-000000000010",
            score=0.75,
            rank=1,
            name="nearest visual",
            content_ref="a" * 64,
        ),
        KhiveSearchHit(
            asset_id="00000000-0000-0000-0000-000000000011",
            score=-0.25,
            rank=2,
            name="second visual",
            content_ref="b" * 64,
        ),
    )
    model, search = _calls(log)
    assert model["ops"] == [{"args": {"namespace": "moodboard-tests"}, "tool": "moodboard.model"}]
    assert search["ops"] == [
        {
            "args": {"asset_id": query, "namespace": "moodboard-tests", "top_k": 2},
            "tool": "moodboard.search",
        }
    ]
    assert search["argv"][search["argv"].index("--actor") + 1] == "lambda:moodboard-tests"
    assert search["argv"][search["argv"].index("--expect-actor") + 1] == ("lambda:moodboard-tests")


def test_client_search_omits_the_optional_top_k_and_uses_the_pack_default(fake_kkernel):
    executable, log = fake_kkernel
    query = "00000000-0000-0000-0000-000000000001"

    result = _client(executable).search(query)

    assert len(result.hits) == 2
    assert _calls(log)[1]["ops"][0]["args"] == {
        "asset_id": query,
        "namespace": "moodboard-tests",
    }


def test_operation_arguments_cannot_override_the_configured_storage_namespace(fake_kkernel):
    executable, log = fake_kkernel

    with pytest.raises(ValueError, match="conflicts with the configured Khive namespace"):
        _client(executable).ingest(
            ({"image_base64": "", "namespace": "a-different-storage-namespace"},)
        )

    assert not log.exists()


def test_foreign_namespace_search_keeps_global_uuid_lookup_and_returns_no_candidates(
    fake_kkernel,
):
    executable, log = fake_kkernel
    query = "00000000-0000-0000-0000-000000000001"
    client = KhiveClient(
        executable=executable,
        actor="lambda:moodboard-tests",
        namespace="foreign-namespace",
    )

    result = client.search(query, top_k=100)

    assert result.query_asset_id == query
    assert result.hits == ()
    assert {
        operation["args"]["namespace"] for call in _calls(log) for operation in call["ops"]
    } == {"foreign-namespace"}


@pytest.mark.parametrize("top_k", [0, 101, True, 1.5, "2"])
def test_client_search_rejects_invalid_top_k_before_running_kkernel(fake_kkernel, top_k):
    executable, log = fake_kkernel

    with pytest.raises(ValueError, match="top_k"):
        _client(executable).search("00000000-0000-0000-0000-000000000001", top_k)

    assert not log.exists()


@pytest.mark.parametrize(
    "asset_id",
    ["", "not-a-uuid", "00000000000000000000000000000001", "AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA"],
)
def test_client_search_rejects_noncanonical_query_ids_before_running_kkernel(
    fake_kkernel, asset_id
):
    executable, log = fake_kkernel

    with pytest.raises(ValueError, match="canonical UUID"):
        _client(executable).search(asset_id, 2)

    assert not log.exists()


@pytest.mark.parametrize(
    ("mode", "message"),
    [
        ("search-extra-key", "unknown keys"),
        ("search-missing-key", "missing keys"),
        ("search-query-mismatch", "query_asset_id"),
        ("search-not-experimental", "experimental=true"),
        ("search-hits-not-list", "hits must be an array"),
        ("search-hit-extra-key", "unknown keys"),
        ("search-hit-missing-key", "missing keys"),
        ("search-self-hit", "must exclude the query"),
        ("search-duplicate-hit", "duplicate asset_id"),
        ("search-bad-hit-uuid", "canonical UUID"),
        ("search-bad-content-ref", "content_ref"),
        ("search-bool-score", "plain JSON number"),
        ("search-huge-score", "finite cosine"),
        ("search-out-of-range-score", "finite cosine"),
        ("search-bad-rank", "one-based contiguous rank"),
        ("search-bool-rank", "one-based contiguous rank"),
        ("search-swapped-hits", "one-based contiguous rank"),
        ("search-score-order", "descending cosine"),
        ("search-empty-name", "name"),
        ("search-null-name", "name"),
        ("search-bad-name-type", "name"),
        ("search-long-name", "name"),
    ],
)
def test_client_search_rejects_malformed_or_reordered_results(
    fake_kkernel, monkeypatch, mode, message
):
    executable, _ = fake_kkernel
    monkeypatch.setenv("FAKE_KKERNEL_MODE", mode)

    with pytest.raises(KhiveProtocolError, match=message):
        _client(executable).search("00000000-0000-0000-0000-000000000001", 2)


def test_client_search_rejects_descriptor_drift_from_model_discovery(fake_kkernel, monkeypatch):
    executable, _ = fake_kkernel
    monkeypatch.setenv("FAKE_KKERNEL_MODE", "search-descriptor-drift")

    with pytest.raises(KhiveProtocolError, match="descriptor drift"):
        _client(executable).search("00000000-0000-0000-0000-000000000001", 2)


def test_client_search_rejects_more_hits_than_requested(fake_kkernel, monkeypatch):
    executable, _ = fake_kkernel
    monkeypatch.setenv("FAKE_KKERNEL_MODE", "search-too-many-hits")

    with pytest.raises(KhiveProtocolError, match="more hits than requested"):
        _client(executable).search("00000000-0000-0000-0000-000000000001", 1)


@pytest.mark.parametrize(
    ("mode", "message"),
    [
        ("nonzero", "exit status 7"),
        ("partial", "result rows"),
        ("malformed-jsonl", "JSONL"),
        ("failed-row", "reported failure"),
        ("failed-row-hidden", "operation 0.*reported failure"),
        ("wrong-tool", "batch order"),
        ("bad-checksum", "checksum"),
        ("success-with-error", "ok=true.*error field"),
        ("success-with-aborted", "ok=true.*aborted=True"),
    ],
)
def test_transport_corruption_fails_the_whole_batch(fake_kkernel, monkeypatch, mode, message):
    executable, _ = fake_kkernel
    encoder = KhiveLatticeEncoder(_client(executable))
    monkeypatch.setenv("FAKE_KKERNEL_MODE", mode)

    with pytest.raises(KhiveProtocolError, match=message):
        encoder.embed([np.zeros((8, 8, 3), dtype=np.uint8)])


def test_manifest_path_accepts_an_alias_of_the_requested_result_file(fake_kkernel, monkeypatch):
    executable, _ = fake_kkernel
    encoder = KhiveLatticeEncoder(_client(executable))
    monkeypatch.setenv("FAKE_KKERNEL_MODE", "path-alias")

    result = encoder.embed([np.zeros((8, 8, 3), dtype=np.uint8)])

    assert result.shape == (1, 4)


def test_manifest_path_rejects_a_different_existing_result_file(fake_kkernel, monkeypatch):
    executable, _ = fake_kkernel
    encoder = KhiveLatticeEncoder(_client(executable))
    monkeypatch.setenv("FAKE_KKERNEL_MODE", "wrong-path")

    with pytest.raises(KhiveProtocolError, match="manifest path"):
        encoder.embed([np.zeros((8, 8, 3), dtype=np.uint8)])


def test_descriptor_drift_is_rejected_even_when_each_descriptor_is_well_formed(
    fake_kkernel, monkeypatch
):
    executable, _ = fake_kkernel
    encoder = KhiveLatticeEncoder(_client(executable))
    monkeypatch.setenv("FAKE_KKERNEL_MODE", "drift")

    with pytest.raises(KhiveProtocolError, match="descriptor drift"):
        encoder.embed([np.zeros((8, 8, 3), dtype=np.uint8)])


def test_successful_same_verb_rows_cannot_be_swapped(fake_kkernel, monkeypatch):
    executable, _ = fake_kkernel
    encoder = KhiveLatticeEncoder(_client(executable))
    monkeypatch.setenv("FAKE_KKERNEL_MODE", "swapped-success")

    with pytest.raises(KhiveProtocolError, match="reordered or cross-wired"):
        encoder.embed(
            [
                np.zeros((8, 8, 3), dtype=np.uint8),
                np.full((8, 8, 3), 255, dtype=np.uint8),
            ]
        )


@pytest.mark.parametrize(
    ("mode", "message"),
    [("model-extra-key", "unknown keys"), ("model-missing-key", "missing keys")],
)
def test_model_result_shape_is_closed(fake_kkernel, monkeypatch, mode, message):
    executable, _ = fake_kkernel
    monkeypatch.setenv("FAKE_KKERNEL_MODE", mode)

    with pytest.raises(KhiveProtocolError, match=message):
        KhiveLatticeEncoder(_client(executable))


@pytest.mark.parametrize(
    ("mode", "message"),
    [("ingest-extra-key", "unknown keys"), ("ingest-missing-key", "missing keys")],
)
def test_ingest_result_shape_is_closed(fake_kkernel, monkeypatch, mode, message):
    executable, _ = fake_kkernel
    encoder = KhiveLatticeEncoder(_client(executable))
    monkeypatch.setenv("FAKE_KKERNEL_MODE", mode)

    with pytest.raises(KhiveProtocolError, match=message):
        encoder.embed([np.zeros((8, 8, 3), dtype=np.uint8)])


@pytest.mark.parametrize(
    ("mode", "message"),
    [("wrong-dimension", "dimension"), ("bad-norm", "unit-normalized"), ("non-finite", "JSON")],
)
def test_invalid_embedding_rows_are_rejected(fake_kkernel, monkeypatch, mode, message):
    executable, _ = fake_kkernel
    encoder = KhiveLatticeEncoder(_client(executable))
    monkeypatch.setenv("FAKE_KKERNEL_MODE", mode)

    with pytest.raises(KhiveProtocolError, match=message):
        encoder.embed([np.zeros((8, 8, 3), dtype=np.uint8)])


@pytest.mark.parametrize(
    ("mode", "message"),
    [
        ("boolean-coordinate", "plain JSON numbers"),
        ("nested-coordinate", "plain JSON numbers"),
        ("enormous-integer", "not numeric"),
    ],
)
def test_embedding_coordinates_are_strict_plain_finite_numbers(
    fake_kkernel, monkeypatch, mode, message
):
    executable, _ = fake_kkernel
    encoder = KhiveLatticeEncoder(_client(executable))
    monkeypatch.setenv("FAKE_KKERNEL_MODE", mode)

    with pytest.raises(KhiveProtocolError, match=message):
        encoder.embed([np.zeros((8, 8, 3), dtype=np.uint8)])


def test_empty_embed_has_protocol_shape_without_an_ingest_call(fake_kkernel):
    executable, log = fake_kkernel
    encoder = KhiveLatticeEncoder(_client(executable))

    embedded = encoder.embed([])

    assert embedded.shape == (0, 4)
    assert embedded.dtype == np.float32
    assert encoder.last_assets == ()
    assert len(_calls(log)) == 1  # model discovery only


def test_descriptor_canonicalization_has_a_cross_language_golden_fingerprint(fake_kkernel):
    executable, _ = fake_kkernel
    descriptor = KhiveLatticeEncoder(_client(executable)).descriptor

    assert descriptor.fingerprint == (
        "5d62815b1b662fa926c58aaaf58553e3d842b615cd90f431fe6e7c1bd782ea0b"
    )
    assert descriptor.model_key == (
        "moodboard_5d62815b1b662fa926c58aaaf58553e3d842b615cd90f431fe6e7c1bd782ea0b_4"
    )
    assert KHIVE_ADAPTER_REVISION == "moodboard-khive-adapter-v2"

    synthetic = descriptor.to_json_dict()
    synthetic.pop("fingerprint")
    synthetic.pop("model_key")
    synthetic["prompt"]["sha256"] = "2" * 64
    assert hashlib.sha256(encoders_module._canonical_json(synthetic).encode()).hexdigest() == (
        "b57fb3cf43da387cde12425e6d7d442af269ba37ecabfbe4c975cb80abdf56e5"
    )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("model_name", "another-visual-model", "model_name"),
        ("inference_version", "0.7.1", "inference"),
        ("prompt_sha256", "3" * 64, "prompt"),
    ],
)
def test_descriptor_v1_rejects_known_semantic_fields_with_different_values(
    fake_kkernel, field, value, message
):
    executable, _ = fake_kkernel
    document = KhiveLatticeEncoder(_client(executable)).descriptor.to_json_dict()
    if field == "inference_version":
        document["inference"]["version"] = value
    elif field == "prompt_sha256":
        document["prompt"]["sha256"] = value
    else:
        document[field] = value
    identity = {
        key: nested for key, nested in document.items() if key not in {"fingerprint", "model_key"}
    }
    fingerprint = hashlib.sha256(
        encoders_module._canonical_json(identity).encode("utf-8")
    ).hexdigest()
    document["fingerprint"] = fingerprint
    document["model_key"] = f"moodboard_{fingerprint}_4"

    with pytest.raises(KhiveProtocolError, match=message):
        VisualDescriptor.parse(document)


@pytest.mark.parametrize("container", ["root", "inference", "preprocessing", "prompt"])
def test_descriptor_v1_rejects_unknown_keys_even_when_the_fingerprint_covers_them(
    fake_kkernel, container
):
    executable, _ = fake_kkernel
    document = KhiveLatticeEncoder(_client(executable)).descriptor.to_json_dict()
    document.pop("model_key")
    document.pop("fingerprint")
    target = document if container == "root" else document[container]
    target["future_field"] = "must require a new schema version"
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"))
    fingerprint = hashlib.sha256(canonical.encode()).hexdigest()
    document["fingerprint"] = fingerprint
    document["model_key"] = f"moodboard_{fingerprint}_4"

    with pytest.raises(KhiveProtocolError, match="unknown keys"):
        VisualDescriptor.parse(document)


def test_input_arrays_are_not_mutated(fake_kkernel):
    executable, _ = fake_kkernel
    image = np.linspace(0.0, 1.0, 17 * 23 * 3).reshape(17, 23, 3)
    before = image.copy()

    KhiveLatticeEncoder(_client(executable)).embed([image])

    np.testing.assert_array_equal(image, before)


def test_programmatic_array_rendition_is_byte_deterministic(fake_kkernel):
    executable, log = fake_kkernel
    image = np.linspace(0.0, 1.0, 29 * 37 * 4).reshape(29, 37, 4)
    encoder = KhiveLatticeEncoder(_client(executable))

    encoder.embed([image])
    encoder.embed([image.copy()])

    ingests = [call for call in _calls(log) if call["ops"][0]["tool"] == "moodboard.ingest"]
    assert len(ingests) == 2
    assert (
        ingests[0]["ops"][0]["args"]["image_base64"]
        == (ingests[1]["ops"][0]["args"]["image_base64"])
    )


def test_programmatic_rgba_png_and_content_ref_have_a_cross_platform_golden(fake_kkernel):
    executable, log = fake_kkernel
    rgba = np.array(
        [
            [[255, 0, 1, 0], [2, 253, 4, 64], [5, 6, 250, 128]],
            [[9, 10, 11, 192], [17, 33, 65, 254], [127, 128, 129, 255]],
        ],
        dtype=np.uint8,
    )
    encoder = KhiveLatticeEncoder(_client(executable))

    encoder.embed([rgba])

    encoded = _calls(log)[1]["ops"][0]["args"]["image_base64"]
    assert encoded == (
        "iVBORw0KGgoAAAANSUhEUgAAAAMAAAACCAYAAACddGYaAAAAJUlEQVR4AQEaAOX/AP8AAQAC/QRABQb6"
        "gAAJCgvAESFB/n+Agf9dnQiXzbSDoAAAAABJRU5ErkJggg=="
    )
    rendition = base64.b64decode(encoded, validate=True)
    assert blake3(rendition).hexdigest() == (
        "19cf290107a8a725b1dd47b0c2ede6e98e6fa9f917e156738cd5a93f98271ce5"
    )
    assert encoder.last_assets[0].content_ref == (
        "19cf290107a8a725b1dd47b0c2ede6e98e6fa9f917e156738cd5a93f98271ce5"
    )
    with Image.open(io.BytesIO(rendition)) as decoded:
        assert decoded.size == (3, 2)
        assert decoded.mode == "RGBA"
        np.testing.assert_array_equal(np.asarray(decoded), rgba)
    assert encoder.revision == f"{encoder.descriptor.fingerprint}+{KHIVE_ADAPTER_REVISION}"


def test_programmatic_png_is_identical_across_fresh_python_processes():
    script = """
import base64
import numpy as np
from moodboard.encoders import _png_bytes
rgba = np.array([
    [[255, 0, 1, 0], [2, 253, 4, 64], [5, 6, 250, 128]],
    [[9, 10, 11, 192], [17, 33, 65, 254], [127, 128, 129, 255]],
], dtype=np.uint8)
print(base64.b64encode(_png_bytes(rgba, 0)).decode())
"""

    first = subprocess.check_output([sys.executable, "-c", script], text=True).strip()
    second = subprocess.check_output([sys.executable, "-c", script], text=True).strip()

    assert first == second
    assert first == (
        "iVBORw0KGgoAAAANSUhEUgAAAAMAAAACCAYAAACddGYaAAAAJUlEQVR4AQEaAOX/AP8AAQAC/QRABQb6"
        "gAAJCgvAESFB/n+Agf9dnQiXzbSDoAAAAABJRU5ErkJggg=="
    )


def test_duplicate_payloads_submit_once_and_fan_back_in_original_order(fake_kkernel):
    executable, log = fake_kkernel
    image = np.arange(6 * 7 * 4, dtype=np.uint8).reshape(6, 7, 4)
    encoder = KhiveLatticeEncoder(_client(executable))

    embedded = encoder.embed_assets(
        [image, image.copy()],
        names=("first-name-wins.png", "duplicate-name.png"),
        captions=("first caption wins", "ignored duplicate caption"),
    )

    ingest_calls = [call for call in _calls(log) if call["ops"][0]["tool"] == "moodboard.ingest"]
    assert len(ingest_calls) == 1
    assert len(ingest_calls[0]["ops"]) == 1
    assert ingest_calls[0]["ops"][0]["args"]["name"] == "first-name-wins.png"
    assert ingest_calls[0]["ops"][0]["args"]["caption"] == "first caption wins"
    np.testing.assert_array_equal(embedded[0], embedded[1])
    assert encoder.last_assets[0].asset_id == encoder.last_assets[1].asset_id
    assert encoder.last_assets[0].content_ref == encoder.last_assets[1].content_ref
    assert encoder.last_assets[0].created is True
    assert encoder.last_assets[1].created is False


def test_unique_image_count_budget_fails_before_submitting_ingest(fake_kkernel):
    executable, log = fake_kkernel
    encoder = KhiveLatticeEncoder(_client(executable))
    images = [np.full((1, 1, 3), value, dtype=np.uint8) for value in range(65)]

    with pytest.raises(ValueError, match="at most 64"):
        encoder.embed(images)

    assert encoder.last_assets == ()
    assert len(_calls(log)) == 1


def test_total_decoded_byte_budget_fails_before_base64_or_ingest(
    fake_kkernel, tmp_path, monkeypatch
):
    executable, log = fake_kkernel
    source = tmp_path / "source.png"
    Image.fromarray(np.zeros((4, 5, 3), dtype=np.uint8)).save(source)
    data = source.read_bytes()
    monkeypatch.setattr(encoders_module, "KHIVE_REQUEST_MAX_BYTES", len(data) - 1)
    encoder = KhiveLatticeEncoder(_client(executable))

    with pytest.raises(ValueError, match="decoded-byte"):
        encoder.embed_source_assets(
            (source,),
            expected_sha256=(hashlib.sha256(data).hexdigest(),),
            media_types=("image/png",),
        )

    assert encoder.last_assets == ()
    assert len(_calls(log)) == 1


def test_programmatic_budget_stops_before_encoding_a_later_array(fake_kkernel, monkeypatch):
    executable, log = fake_kkernel
    first = np.zeros((1, 1, 3), dtype=np.uint8)
    second = np.ones((1, 1, 3), dtype=np.uint8)
    original_png_bytes = encoders_module._png_bytes
    first_size = len(original_png_bytes(first, 0))
    encoded_indexes: list[int] = []

    def recording_png_bytes(image, index):
        encoded_indexes.append(index)
        return original_png_bytes(image, index)

    monkeypatch.setattr(encoders_module, "KHIVE_REQUEST_MAX_BYTES", first_size)
    monkeypatch.setattr(encoders_module, "_png_bytes", recording_png_bytes)
    encoder = KhiveLatticeEncoder(_client(executable))

    with pytest.raises(ValueError, match="decoded bytes"):
        encoder.embed([first, second])

    assert encoded_indexes == [0]
    assert encoder.last_assets == ()
    assert len(_calls(log)) == 1


def test_source_budget_stops_before_opening_a_later_path(fake_kkernel, tmp_path, monkeypatch):
    executable, log = fake_kkernel
    first = tmp_path / "first.png"
    first.write_bytes(b"exactly-full")
    missing_later_path = tmp_path / "must-not-be-opened.png"
    monkeypatch.setattr(encoders_module, "KHIVE_REQUEST_MAX_BYTES", len(first.read_bytes()))
    encoder = KhiveLatticeEncoder(_client(executable))

    with pytest.raises(ValueError, match="no bytes remain"):
        encoder.embed_source_assets(
            (first, missing_later_path),
            expected_sha256=(
                hashlib.sha256(first.read_bytes()).hexdigest(),
                hashlib.sha256(b"different bytes").hexdigest(),
            ),
            media_types=("image/png", "image/png"),
        )

    assert not missing_later_path.exists()
    assert encoder.last_assets == ()
    assert len(_calls(log)) == 1


def test_duplicate_source_budget_rejects_before_opening_an_unreadable_repeat(
    fake_kkernel, tmp_path, monkeypatch
):
    executable, log = fake_kkernel
    first = tmp_path / "first.png"
    first.write_bytes(b"eight123")
    missing_duplicate = tmp_path / "duplicate-must-not-open.png"
    digest = hashlib.sha256(first.read_bytes()).hexdigest()
    monkeypatch.setattr(encoders_module, "KHIVE_REQUEST_MAX_BYTES", 12)
    encoder = KhiveLatticeEncoder(_client(executable))

    with pytest.raises(ValueError, match="before duplicate source image"):
        encoder.embed_source_assets(
            (first, missing_duplicate),
            expected_sha256=(digest, digest),
            media_types=("image/png", "image/png"),
        )

    assert not missing_duplicate.exists()
    assert encoder.last_assets == ()
    assert len(_calls(log)) == 1


def test_programmatic_rgba_preserves_alpha_for_the_descriptor_pinned_server_matte(fake_kkernel):
    executable, log = fake_kkernel
    rgba = np.zeros((3, 4, 4), dtype=np.uint8)
    rgba[..., :3] = (255, 0, 0)
    rgba[..., 3] = 0

    KhiveLatticeEncoder(_client(executable)).embed([rgba])

    ingest = _calls(log)[1]["ops"][0]["args"]
    with Image.open(io.BytesIO(base64.b64decode(ingest["image_base64"], validate=True))) as image:
        assert image.mode == "RGBA"
        assert image.getpixel((0, 0)) == (255, 0, 0, 0)


def test_programmatic_wide_integer_dtype_fails_before_ingest(fake_kkernel):
    executable, log = fake_kkernel
    encoder = KhiveLatticeEncoder(_client(executable))

    with pytest.raises(ValueError, match="dtype uint16"):
        encoder.embed([np.full((4, 5, 3), 65_535, dtype=np.uint16)])

    assert len(_calls(log)) == 1


@pytest.mark.parametrize(
    "value",
    [np.nextafter(0.0, -1.0), np.nextafter(1.0, 2.0)],
)
def test_programmatic_float_pixels_outside_closed_unit_interval_fail(fake_kkernel, value):
    executable, log = fake_kkernel
    encoder = KhiveLatticeEncoder(_client(executable))

    with pytest.raises(ValueError, match=r"outside \[0,1\]"):
        encoder.embed([np.full((4, 5, 3), value, dtype=np.float64)])

    assert len(_calls(log)) == 1


def test_source_asset_path_ingests_exact_bytes_bound_to_the_expected_hash(fake_kkernel, tmp_path):
    executable, log = fake_kkernel
    source = tmp_path / "source.jpg"
    Image.fromarray(np.full((17, 23, 3), (11, 72, 201), dtype=np.uint8)).save(
        source, format="JPEG", quality=83
    )
    source_bytes = source.read_bytes()
    encoder = KhiveLatticeEncoder(_client(executable))

    embedded = encoder.embed_source_assets(
        (source,),
        expected_sha256=(hashlib.sha256(source_bytes).hexdigest(),),
        media_types=("image/jpeg",),
        names=("source.jpg",),
    )

    assert embedded.shape == (1, 4)
    assert encoder.last_assets[0].byte_identity == "source-bytes"
    ingest = _calls(log)[1]["ops"][0]["args"]
    assert base64.b64decode(ingest["image_base64"], validate=True) == source_bytes
    assert ingest["media_type"] == "image/jpeg"


def test_source_asset_path_rejects_a_file_changed_after_the_hash_was_read(fake_kkernel, tmp_path):
    executable, log = fake_kkernel
    source = tmp_path / "source.png"
    source.write_bytes(b"first bytes")
    expected = hashlib.sha256(source.read_bytes()).hexdigest()
    encoder = KhiveLatticeEncoder(_client(executable))
    source.write_bytes(b"changed bytes")

    with pytest.raises(ValueError, match="changed after it was loaded"):
        encoder.embed_source_assets(
            (source,),
            expected_sha256=(expected,),
            media_types=("image/png",),
        )

    assert len(_calls(log)) == 1  # descriptor discovery only; no ingest side effect


def test_source_asset_path_rejects_an_unsupported_mime_before_ingest(fake_kkernel, tmp_path):
    executable, log = fake_kkernel
    source = tmp_path / "source.bmp"
    Image.fromarray(np.zeros((3, 4, 3), dtype=np.uint8)).save(source)
    encoder = KhiveLatticeEncoder(_client(executable))

    with pytest.raises(ValueError, match="supports image/png, image/jpeg, and image/webp"):
        encoder.embed_source_assets(
            (source,),
            expected_sha256=(hashlib.sha256(source.read_bytes()).hexdigest(),),
            media_types=("image/bmp",),
        )

    assert len(_calls(log)) == 1


def test_khive_transparent_visual_rendition_matches_embedding_thumbnail_and_axes(
    fake_kkernel, tmp_path
):
    executable, log = fake_kkernel
    red_path = tmp_path / "hidden-red.png"
    blue_path = tmp_path / "hidden-blue.png"
    red = np.zeros((2, 3, 4), dtype=np.uint8)
    blue = np.zeros((2, 3, 4), dtype=np.uint8)
    red[..., :3] = (255, 0, 0)
    blue[..., :3] = (0, 0, 255)
    red[..., 3] = 0
    blue[..., 3] = 0
    Image.fromarray(red, mode="RGBA").save(red_path)
    Image.fromarray(blue, mode="RGBA").save(blue_path)

    khive_red = cli._load_image(red_path, "red", khive_visual=True)
    khive_blue = cli._load_image(blue_path, "blue", khive_visual=True)
    classical_red = cli._load_image(red_path, "red")
    classical_blue = cli._load_image(blue_path, "blue")

    expected = np.full((2, 3, 3), 128, dtype=np.uint8)
    np.testing.assert_array_equal(khive_red.array, expected)
    np.testing.assert_array_equal(khive_blue.array, expected)
    assert not np.array_equal(classical_red.array, classical_blue.array)

    thumbnail = cli._thumbnail(khive_red)
    with Image.open(io.BytesIO(base64.b64decode(thumbnail.data_base64))) as image:
        np.testing.assert_array_equal(np.asarray(image.convert("RGB")), expected)

    axes = cli._classical_axes(
        khive_red,
        (khive_blue,),
        (cli.Exemplar(reference_id="blue", similarity=1.0),),
    )
    assert axes == {"palette": 0.0, "tone": 0.0, "composition": 0.0}

    KhiveLatticeEncoder(_client(executable)).embed([khive_red.array])
    embedding_png = base64.b64decode(
        _calls(log)[1]["ops"][0]["args"]["image_base64"], validate=True
    )
    with Image.open(io.BytesIO(embedding_png)) as image:
        np.testing.assert_array_equal(np.asarray(image.convert("RGB")), expected)


def test_khive_loader_rejects_compressed_pixel_expansion_before_conversion(tmp_path, monkeypatch):
    source = tmp_path / "compressed.png"
    Image.fromarray(np.zeros((100, 100, 3), dtype=np.uint8)).save(source, optimize=True)
    converted: list[str | None] = []
    original_convert = Image.Image.convert

    def recording_convert(image, *args, **kwargs):
        converted.append(image.format)
        return original_convert(image, *args, **kwargs)

    monkeypatch.setattr(cli, "KHIVE_RETAINED_VISUAL_MAX_BYTES", 100 * 100 * 3 - 1)
    monkeypatch.setattr(Image.Image, "convert", recording_convert)

    with pytest.raises(ValueError, match="retained RGB bytes"):
        cli._load_all((source,), io.StringIO(), khive_visual=True)

    assert converted == []


def test_khive_loader_bounds_source_read_before_pillow_decode(tmp_path, monkeypatch):
    source = tmp_path / "oversized.png"
    source.write_bytes(b"x" * 128)
    monkeypatch.setattr(cli, "KHIVE_REQUEST_MAX_BYTES", 16)

    def must_not_decode(*args, **kwargs):
        raise AssertionError("Pillow decode must not run after the bounded source read rejects")

    monkeypatch.setattr(cli, "_load_image", must_not_decode)

    with pytest.raises(ValueError, match="decoded source bytes"):
        cli._load_all((source,), io.StringIO(), khive_visual=True)


def test_khive_loader_does_not_decode_after_cumulative_visual_budget_is_full(tmp_path, monkeypatch):
    first = tmp_path / "a.png"
    second = tmp_path / "b.png"
    Image.fromarray(np.zeros((4, 5, 3), dtype=np.uint8)).save(first)
    Image.fromarray(np.ones((4, 5, 3), dtype=np.uint8)).save(second)
    loaded_paths: list[Path] = []
    original_load_image = cli._load_image

    def recording_load_image(path, item_id, **kwargs):
        loaded_paths.append(path)
        return original_load_image(path, item_id, **kwargs)

    monkeypatch.setattr(cli, "KHIVE_RETAINED_VISUAL_MAX_BYTES", 4 * 5 * 3)
    monkeypatch.setattr(cli, "_load_image", recording_load_image)

    with pytest.raises(ValueError, match="no bytes remain"):
        cli._load_all((first, second), io.StringIO(), khive_visual=True)

    assert loaded_paths == [first]


def _two_reference_directory(root: Path) -> Path:
    root.mkdir()
    Image.fromarray(np.full((24, 31, 3), (200, 40, 20), dtype=np.uint8)).save(root / "a.png")
    Image.fromarray(np.full((24, 31, 3), (20, 40, 200), dtype=np.uint8)).save(root / "b.png")
    return root


def test_cli_defaults_remain_classical_and_do_not_touch_a_configured_khive_path(tmp_path):
    references = _two_reference_directory(tmp_path / "references")
    board_path = tmp_path / "classical.mb"

    status = cli.main(
        [
            "build",
            str(references),
            "--output",
            str(board_path),
            "--khive-executable",
            str(tmp_path / "does-not-exist"),
        ]
    )

    assert status == 0
    board = read_board(board_path)
    assert board.model_repo == "classical-v1"
    assert board.reference_asset_locations == ()


def test_cli_khive_opt_in_pins_config_and_persists_reference_locations(fake_kkernel, tmp_path):
    executable, log = fake_kkernel
    references = _two_reference_directory(tmp_path / "references")
    board_path = tmp_path / "khive.mb"

    status = cli.main(
        [
            "build",
            str(references),
            "--output",
            str(board_path),
            "--encoder",
            "khive-lattice",
            "--khive-executable",
            str(executable),
            "--khive-actor",
            "lambda:cli-test",
            "--khive-namespace",
            "cli-test",
            "--khive-config",
            str(tmp_path / "khive.toml"),
        ]
    )

    assert status == 0
    board = read_board(board_path)
    assert board.model_repo == "khive:qwen3.5-vlm-pooled-visual"
    assert board.model_revision == KhiveLatticeEncoder(_client(executable)).revision
    assert len(board.reference_asset_locations) == 2
    assert {location.byte_identity for location in board.reference_asset_locations} == {
        "source-bytes"
    }
    calls = _calls(log)
    build_ingest = next(call for call in calls if call["ops"][0]["tool"] == "moodboard.ingest")
    assert build_ingest["argv"][build_ingest["argv"].index("--actor") + 1] == "lambda:cli-test"
    assert build_ingest["argv"][build_ingest["argv"].index("--namespace") + 1] == "cli-test"
    assert {op["args"]["namespace"] for op in build_ingest["ops"]} == {"cli-test"}
    assert build_ingest["argv"][build_ingest["argv"].index("--config") + 1] == str(
        tmp_path / "khive.toml"
    )
    submitted = {op["args"]["name"]: op["args"] for op in build_ingest["ops"]}
    for path in sorted(references.glob("*.png")):
        assert base64.b64decode(submitted[path.name]["image_base64"], validate=True) == (
            path.read_bytes()
        )


def test_rank_parser_exposes_the_same_explicit_encoder_and_khive_configuration(tmp_path):
    parser = cli.build_parser()
    default = parser.parse_args(
        [
            "rank",
            "candidate.png",
            "--board",
            "brand.mb",
            "--references",
            "references",
            "--output",
            "report.json",
        ]
    )
    selected = parser.parse_args(
        [
            "rank",
            "candidate.png",
            "--board",
            "brand.mb",
            "--references",
            "references",
            "--output",
            "report.json",
            "--encoder",
            "khive-lattice",
            "--khive-executable",
            str(tmp_path / "kkernel"),
            "--khive-actor",
            "lambda:rank",
            "--khive-namespace",
            "rank-space",
            "--khive-config",
            str(tmp_path / "khive.toml"),
        ]
    )

    assert default.encoder == "classical"
    assert selected.encoder == "khive-lattice"
    assert selected.khive_actor == "lambda:rank"
    assert selected.khive_namespace == "rank-space"
    assert selected.khive_config == tmp_path / "khive.toml"
    assert default.khive_config is None


def test_rank_parser_exposes_opt_in_preference_feature_artifact() -> None:
    args = cli.build_parser().parse_args(
        [
            "rank",
            "candidate.png",
            "--board",
            "brand.mb",
            "--references",
            "references",
            "--output",
            "report.json",
            "--preference-features-output",
            "preference-features.json",
        ]
    )

    assert args.preference_features_output == Path("preference-features.json")


def test_rank_preference_export_requires_khive_before_reading_inputs(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    artifact = tmp_path / "features.json"
    error = io.StringIO()

    status = cli.main(
        [
            "rank",
            str(tmp_path / "missing-candidate.png"),
            "--board",
            str(tmp_path / "missing.mb"),
            "--references",
            str(tmp_path / "missing-references"),
            "--output",
            str(report),
            "--preference-features-output",
            str(artifact),
        ],
        out=io.StringIO(),
        err=error,
    )

    assert status == 1
    assert "requires --encoder khive-lattice" in error.getvalue()
    assert not report.exists() and not artifact.exists()


def test_rank_preference_export_rejects_colliding_output_before_khive(
    fake_kkernel, tmp_path: Path
) -> None:
    executable, log = fake_kkernel
    output = tmp_path / "same.json"
    error = io.StringIO()

    status = cli.main(
        [
            "rank",
            str(tmp_path / "missing-candidate.png"),
            "--board",
            str(tmp_path / "missing.mb"),
            "--references",
            str(tmp_path / "missing-references"),
            "--output",
            str(output),
            "--preference-features-output",
            str(output),
            "--encoder",
            "khive-lattice",
            "--khive-executable",
            str(executable),
        ],
        out=io.StringIO(),
        err=error,
    )

    assert status == 1
    assert "must differ from the report output" in error.getvalue()
    assert not log.exists()


def test_rank_exports_real_geometry_only_after_valid_report(
    fake_kkernel, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable, log = fake_kkernel
    monkeypatch.setenv("FAKE_KKERNEL_MODE", "constant-vector")
    references = _two_reference_directory(tmp_path / "references")
    candidates = _two_reference_directory(tmp_path / "candidates")
    board_path = tmp_path / "khive.mb"
    report_path = tmp_path / "report.json"
    feature_path = tmp_path / "preference-features.json"
    khive_options = [
        "--encoder",
        "khive-lattice",
        "--khive-executable",
        str(executable),
        "--khive-actor",
        "lambda:preference-cli",
        "--khive-namespace",
        "preference-cli",
    ]
    assert (
        cli.main(
            ["build", str(references), "--output", str(board_path), *khive_options],
            out=io.StringIO(),
            err=io.StringIO(),
        )
        == 0
    )

    publish_saw_valid_report: list[bool] = []
    original_publish = KhiveClient.publish_board

    def checked_publish(client, **arguments):
        document = json.loads(report_path.read_text(encoding="utf-8"))
        publish_saw_valid_report.append(document["schema_version"] == "1.1")
        return original_publish(client, **arguments)

    monkeypatch.setattr(KhiveClient, "publish_board", checked_publish)
    out = io.StringIO()
    error = io.StringIO()
    status = cli.main(
        [
            "rank",
            str(candidates),
            "--board",
            str(board_path),
            "--references",
            str(references),
            "--output",
            str(report_path),
            "--alpha",
            "0.5",
            "--preference-features-output",
            str(feature_path),
            *khive_options,
        ],
        out=out,
        err=error,
    )

    assert status == 0, error.getvalue()
    assert publish_saw_valid_report == [True]
    artifact = read_preference_feature_artifact(feature_path)
    board = read_board(board_path)
    assert artifact.board_entity_id == "00000000-0000-4000-8000-000000000100"
    assert artifact.board_id == board.board_id
    assert artifact.source_report_sha256 == hashlib.sha256(report_path.read_bytes()).hexdigest()
    assert len(artifact.candidates) == 2
    report_document = json.loads(report_path.read_text(encoding="utf-8"))
    report_assets = {asset["asset_id"]: asset for asset in report_document["assets"]}
    for candidate in artifact.candidates:
        reported = report_assets[candidate.label]
        assert candidate.features.values.shape == (10,)
        assert np.isfinite(candidate.features.values).all()
        assert candidate.features.values[0] == 1.0
        assert candidate.features.values[1] == 1.0
        assert candidate.features.values[2] == 1.0
        assert candidate.features.values[3] == pytest.approx(reported["score"])
        assert candidate.features.values[4] == pytest.approx(
            reported["interval"]["high"] - reported["interval"]["low"]
        )
        assert candidate.features.values[5] == 1.0
        assert candidate.features.values[6] == 0.5
        np.testing.assert_allclose(
            candidate.features.values[7:],
            [1.0 - reported["axes"][axis] for axis in ("palette", "tone", "composition")],
            rtol=0.0,
            atol=1e-7,
        )
    create = next(
        call for call in _calls(log) if call["ops"][0]["tool"] == "kg.create"
    )
    create_args = create["ops"][0]["args"]
    assert create_args["namespace"] == "preference-cli"
    assert create_args["properties"]["board_id"] == board.board_id
    assert create_args["properties"]["source_report_sha256"] == artifact.source_report_sha256
    assert f"preference features {feature_path}" in out.getvalue()


def test_retrieve_parser_exposes_a_focused_khive_only_surface(tmp_path):
    parser = cli.build_parser()

    args = parser.parse_args(
        [
            "retrieve",
            "00000000-0000-0000-0000-000000000001",
            "--top-k",
            "7",
            "--khive-executable",
            str(tmp_path / "kkernel"),
            "--khive-actor",
            "lambda:retrieve",
            "--khive-namespace",
            "retrieve-space",
            "--khive-config",
            str(tmp_path / "khive.toml"),
        ]
    )

    assert args.asset_id == "00000000-0000-0000-0000-000000000001"
    assert args.top_k == 7
    assert args.khive_executable == str(tmp_path / "kkernel")
    assert args.khive_actor == "lambda:retrieve"
    assert args.khive_namespace == "retrieve-space"
    assert args.khive_config == tmp_path / "khive.toml"
    assert not hasattr(args, "encoder")


def test_retrieve_cli_reports_ranked_khive_locators_without_coherence_semantics(
    fake_kkernel, tmp_path
):
    executable, log = fake_kkernel
    query = "00000000-0000-0000-0000-000000000001"
    out = io.StringIO()
    err = io.StringIO()

    status = cli.main(
        [
            "retrieve",
            query,
            "--top-k",
            "2",
            "--khive-executable",
            str(executable),
            "--khive-actor",
            "lambda:retrieve-cli",
            "--khive-namespace",
            "retrieve-cli",
            "--khive-config",
            str(tmp_path / "khive.toml"),
        ],
        out=out,
        err=err,
    )

    assert status == 0
    assert err.getvalue() == ""
    rendered = out.getvalue()
    assert f"query          {query}" in rendered
    assert "descriptor     moodboard_" in rendered
    assert "cosine" in rendered
    assert "0.750000" in rendered
    assert "-0.250000" in rendered
    assert "00000000-0000-0000-0000-000000000010" in rendered
    assert "a" * 64 in rendered
    assert "nearest visual" in rendered
    assert "coherence" not in rendered.lower()
    assert "style fit" not in rendered.lower()
    model, search = _calls(log)
    assert model["argv"][model["argv"].index("--config") + 1] == str(tmp_path / "khive.toml")
    assert search["ops"][0] == {
        "tool": "moodboard.search",
        "args": {"asset_id": query, "namespace": "retrieve-cli", "top_k": 2},
    }


def test_retrieve_cli_json_escapes_control_characters_in_server_names(fake_kkernel, monkeypatch):
    executable, _ = fake_kkernel
    monkeypatch.setenv("FAKE_KKERNEL_MODE", "search-control-name")
    out = io.StringIO()

    status = cli.main(
        [
            "retrieve",
            "00000000-0000-0000-0000-000000000001",
            "--top-k",
            "2",
            "--khive-executable",
            str(executable),
        ],
        out=out,
        err=io.StringIO(),
    )

    assert status == 0
    rendered = out.getvalue()
    assert len(rendered.splitlines()) == 7
    assert '"line one\\nline two\\t\\u001b[31m"' in rendered
    assert "line one\nline two" not in rendered


def test_rank_rejects_an_inconsistent_board_before_ingesting_candidates(fake_kkernel, tmp_path):
    executable, log = fake_kkernel
    references = _two_reference_directory(tmp_path / "references")
    board_path = tmp_path / "khive.mb"
    khive_options = [
        "--encoder",
        "khive-lattice",
        "--khive-executable",
        str(executable),
        "--khive-actor",
        "lambda:integrity-test",
        "--khive-namespace",
        "integrity-test",
    ]
    assert (
        cli.main(
            ["build", str(references), "--output", str(board_path), *khive_options],
            out=io.StringIO(),
        )
        == 0
    )
    with zipfile.ZipFile(board_path) as archive:
        meta = json.loads(archive.read("meta.json"))
        embeddings = archive.read("embeddings.npy")
    meta["n_eff"] = 999.0
    with zipfile.ZipFile(board_path, mode="w") as archive:
        archive.writestr("meta.json", json.dumps(meta))
        archive.writestr("embeddings.npy", embeddings)

    candidate = tmp_path / "candidate.png"
    Image.fromarray(np.full((20, 20, 3), 127, dtype=np.uint8)).save(candidate)
    ingest_calls_before = sum(call["ops"][0]["tool"] == "moodboard.ingest" for call in _calls(log))
    error = io.StringIO()

    status = cli.main(
        [
            "rank",
            str(candidate),
            "--board",
            str(board_path),
            "--references",
            str(references),
            "--output",
            str(tmp_path / "report.json"),
            *khive_options,
        ],
        out=io.StringIO(),
        err=error,
    )

    assert status == 1
    assert "n_eff must be finite and lie" in error.getvalue()
    ingest_calls_after = sum(call["ops"][0]["tool"] == "moodboard.ingest" for call in _calls(log))
    assert ingest_calls_after == ingest_calls_before
