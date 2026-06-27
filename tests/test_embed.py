"""
Tests for embed_documents checkpoint/resume logic.
No real model is loaded — embed() is patched with a deterministic fake.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from common import Document


DIM = 4


def _make_docs(n: int) -> list[Document]:
    return [
        Document(
            id=f"doc:{i}",
            text=f"text {i}",
            source_type="conversation",
            title="T",
            timestamp="2024-01-01",
            source="claude_ai",
            project=None,
        )
        for i in range(n)
    ]


def _fake_embed(texts, batch_size=64):
    """Returns a unique deterministic vector per text."""
    for i, t in enumerate(texts):
        yield np.full(DIM, float(i), dtype="float32")


def _patch_model(fake_embed_fn=_fake_embed):
    model = MagicMock()
    model.embed.side_effect = fake_embed_fn
    get_model = MagicMock(return_value=model)
    return patch("embed.get_model", get_model), model


@pytest.fixture()
def docs():
    return _make_docs(10)


def test_no_checkpoint_embeds_all(tmp_path, docs):
    ctx, model = _patch_model()
    with ctx:
        from embed import embed_documents

        vectors = embed_documents(docs)

    assert vectors.shape == (10, DIM)
    model.embed.assert_called_once()


def test_checkpoint_written_after_batch(tmp_path, docs):
    ckpt = tmp_path / "ckpt.npz"
    ctx, _ = _patch_model()
    with ctx:
        from embed import embed_documents

        embed_documents(docs, checkpoint_path=ckpt, cache_key="model:1.0")

    assert ckpt.exists()
    saved = np.load(ckpt, allow_pickle=False)
    assert saved["vectors"].shape[0] == 10


def test_resume_skips_completed(tmp_path, docs):
    ckpt = tmp_path / "ckpt.npz"
    cache_key = "model:1.0"

    # Pre-populate checkpoint with first 6 docs already embedded
    pre_vectors = np.zeros((6, DIM), dtype="float32")
    np.savez(ckpt, vectors=pre_vectors, cache_key=np.array(cache_key))

    embed_calls = []

    def tracking_embed(texts, batch_size=64):
        embed_calls.extend(texts)
        yield from _fake_embed(texts, batch_size)

    ctx, _ = _patch_model(tracking_embed)
    with ctx:
        from embed import embed_documents

        vectors = embed_documents(docs, checkpoint_path=ckpt, cache_key=cache_key)

    assert vectors.shape == (10, DIM)
    # Only the remaining 4 docs should have been passed to the model
    assert len(embed_calls) == 4


def test_cache_key_mismatch_starts_fresh(tmp_path, docs):
    ckpt = tmp_path / "ckpt.npz"
    pre_vectors = np.zeros((6, DIM), dtype="float32")
    np.savez(ckpt, vectors=pre_vectors, cache_key=np.array("old-model:1.0"))

    embed_calls = []

    def tracking_embed(texts, batch_size=64):
        embed_calls.extend(texts)
        yield from _fake_embed(texts, batch_size)

    ctx, _ = _patch_model(tracking_embed)
    with ctx:
        from embed import embed_documents

        vectors = embed_documents(docs, checkpoint_path=ckpt, cache_key="new-model:2.0")

    assert vectors.shape == (10, DIM)
    assert len(embed_calls) == 10


def test_corrupt_checkpoint_starts_fresh(tmp_path, docs):
    ckpt = tmp_path / "ckpt.npz"
    ckpt.write_bytes(b"not a valid npz file")

    ctx, _ = _patch_model()
    with ctx:
        from embed import embed_documents

        vectors = embed_documents(docs, checkpoint_path=ckpt, cache_key="model:1.0")

    assert vectors.shape == (10, DIM)
