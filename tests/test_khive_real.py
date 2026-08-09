"""Opt-in smoke tests for the real kkernel file transport and Moodboard pack.

The ordinary suite never requires Khive or a model. Set ``MOODBOARD_REAL_KKERNEL`` to an
absolute path to a freshly built binary to exercise the checkpoint-free ops-file/save-file
boundary. Set ``MOODBOARD_REAL_KHIVE_MODEL=1`` as well only when the Moodboard pack, BlobStore,
and model environment are configured.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from moodboard.encoders import KhiveLatticeEncoder
from moodboard.khive import KhiveClient, KhiveProtocolError, _KhiveOperation

_REAL_KKERNEL = os.environ.get("MOODBOARD_REAL_KKERNEL")
pytestmark = pytest.mark.skipif(
    not _REAL_KKERNEL,
    reason="set MOODBOARD_REAL_KKERNEL to opt into the real kkernel transport smoke",
)


def _real_client() -> KhiveClient:
    config = os.environ.get("MOODBOARD_REAL_KHIVE_CONFIG")
    return KhiveClient(
        executable=Path(_REAL_KKERNEL) if _REAL_KKERNEL is not None else "kkernel",
        actor="lambda:moodboard-real-smoke",
        namespace="moodboard-real-smoke",
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
