"""Opt-in smoke tests for the real kkernel file transport and Moodboard pack.

The ordinary suite never requires Khive or a model. Set ``MOODBOARD_REAL_KKERNEL`` to an
absolute path to a freshly built binary to exercise the checkpoint-free ops-file/save-file
boundary. Set ``MOODBOARD_REAL_KHIVE_MODEL=1`` as well only when the Moodboard pack, BlobStore,
and model environment are configured.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest

from moodboard.encoders import KhiveLatticeEncoder
from moodboard.khive import KhiveClient, KhiveProtocolError, _KhiveOperation

_REAL_KKERNEL = os.environ.get("MOODBOARD_REAL_KKERNEL")
pytestmark = pytest.mark.skipif(
    not _REAL_KKERNEL,
    reason="set MOODBOARD_REAL_KKERNEL to opt into the real kkernel transport smoke",
)


def _real_client(*, namespace: str = "moodboard-real-smoke") -> KhiveClient:
    config = os.environ.get("MOODBOARD_REAL_KHIVE_CONFIG")
    return KhiveClient(
        executable=Path(_REAL_KKERNEL) if _REAL_KKERNEL is not None else "kkernel",
        actor="lambda:moodboard-real-smoke",
        namespace=namespace,
        config=Path(config) if config else None,
    )


def test_real_kkernel_preserves_a_checkpoint_free_whoami_result_through_save_file():
    result = _real_client()._execute((_KhiveOperation("whoami", {}),))

    assert len(result) == 1
    assert isinstance(result[0], dict)


def test_real_kkernel_unknown_verb_fails_closed():
    with pytest.raises(KhiveProtocolError):
        _real_client()._execute((_KhiveOperation("moodboard.__missing_smoke_verb", {}),))


@pytest.mark.skipif(
    os.environ.get("MOODBOARD_REAL_KHIVE_MODEL") != "1",
    reason="set MOODBOARD_REAL_KHIVE_MODEL=1 only with a configured checkpoint and BlobStore",
)
def test_real_moodboard_model_descriptor_matches_the_python_contract():
    encoder = KhiveLatticeEncoder(_real_client())

    assert encoder.dim == encoder.descriptor.dimensions
    assert encoder.name == "khive:qwen3.5-vlm-pooled-visual"


@pytest.mark.skipif(
    os.environ.get("MOODBOARD_REAL_KHIVE_MODEL") != "1",
    reason="set MOODBOARD_REAL_KHIVE_MODEL=1 only with a configured checkpoint and BlobStore",
)
def test_real_named_namespace_persists_ingests_and_scopes_retrieval():
    namespace = "moodboard-real-smoke-named"
    client = _real_client(namespace=namespace)
    encoder = KhiveLatticeEncoder(client)
    images = (
        np.full((32, 32, 3), (210, 45, 80), dtype=np.uint8),
        np.full((32, 32, 3), (190, 60, 95), dtype=np.uint8),
    )

    embedded = encoder.embed_assets(images, names=("namespace-a.png", "namespace-b.png"))

    assert embedded.shape == (2, encoder.dim)
    assert len(encoder.last_assets) == 2
    query, peer = (asset.asset_id for asset in encoder.last_assets)
    result = client.search(query, top_k=100)
    assert peer in {hit.asset_id for hit in result.hits}

    foreign_namespace = _real_client(namespace=f"{namespace}-isolated")
    isolated = foreign_namespace.search(query, top_k=100)
    assert isolated.query_asset_id == query
    assert isolated.hits == ()
