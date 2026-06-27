#!/usr/bin/env python3
"""
Embedding step: turn Documents into vectors with a local fastembed model
(ONNX-backed, no PyTorch required). No DB yet — running this directly just
times the run and reports shape/throughput, as a checkpoint before wiring it
into the DB writer.

Usage:
    python3 embed.py [export_dir]
"""

import os
import sys
import time
from pathlib import Path

import numpy as np

from ingest import build_documents, Document

# bge models are trained with an asymmetric convention: passages are embedded
# as-is, but queries get a fixed instruction prefix to push them into the same
# representation space as a "thing worth retrieving for this question."
# fastembed handles this automatically via query_embed() vs embed().
#
# Must be a model ID supported by fastembed. Override via CONTEXT_BRIDGE_MODEL.
# WARNING: changing the model after building a DB triggers a full rebuild on
# the next run (the meta table mismatch is detected automatically).
MODEL_NAME = os.environ.get("CONTEXT_BRIDGE_MODEL", "BAAI/bge-base-en-v1.5")

# Number of documents per embedding batch. Reduce if you hit OOM during build.
# Override via CONTEXT_BRIDGE_BATCH_SIZE in .env.
BATCH_SIZE = int(os.environ.get("CONTEXT_BRIDGE_BATCH_SIZE", "64"))

_model = None


def _model_is_cached(model_name: str) -> bool:
    cache_root = Path(os.environ.get("FASTEMBED_CACHE_PATH", Path.home() / ".cache" / "fastembed"))
    slug = model_name.replace("/", "_")
    return any(cache_root.glob(f"{slug}*"))


def get_model():
    """Load the embedding model once (ONNX/CPU via fastembed)."""
    from fastembed import TextEmbedding

    global _model
    if _model is not None:
        return _model

    if not _model_is_cached(MODEL_NAME):
        # stderr, not stdout: stdout is the MCP stdio JSON-RPC channel in server.py.
        print(
            f"  first run: downloading {MODEL_NAME} (~130 MB)...",
            file=sys.stderr,
        )
        print(
            "  cached at ~/.cache/fastembed/ after this — subsequent runs are instant.",
            file=sys.stderr,
        )

    _model = TextEmbedding(MODEL_NAME)
    print("embedding model loaded (ONNX/CPU)", file=sys.stderr)
    return _model


def embed_documents(
    docs: list[Document],
    checkpoint_path: "Path | None" = None,
    cache_key: str = "",
) -> np.ndarray:
    """Embed document text as-is (no prefix) — these are the passages.

    If checkpoint_path is given, resume from a prior interrupted run when the
    cache_key matches (format: "<model>:<export_mtime>"). Progress is saved
    after every batch so Ctrl-C only loses at most one batch of work.
    """
    from pathlib import Path

    model = get_model()
    texts = [d.text for d in docs]
    total = len(texts)

    # --- resume ---
    vectors: list = []
    resume_from = 0
    if checkpoint_path is not None and Path(checkpoint_path).exists():
        try:
            ckpt = np.load(checkpoint_path, allow_pickle=False)
            if ckpt["cache_key"].item() == cache_key:
                vectors = list(ckpt["vectors"])
                resume_from = len(vectors)
                print(f"  resuming from checkpoint: {resume_from}/{total} already embedded", file=sys.stderr)
            else:
                print("  checkpoint cache_key mismatch — starting fresh", file=sys.stderr)
        except Exception as e:
            print(f"  checkpoint unreadable ({e}) — starting fresh", file=sys.stderr)

    # --- embed remaining ---
    remaining = texts[resume_from:]
    for i, vec in enumerate(model.embed(remaining, batch_size=BATCH_SIZE), 1):
        vectors.append(vec)
        absolute = resume_from + i
        if i % BATCH_SIZE == 0 or absolute == total:
            print(f"  embedded {absolute}/{total} docs", file=sys.stderr)
            if checkpoint_path is not None:
                np.savez(
                    checkpoint_path,
                    vectors=np.array(vectors, dtype="float32"),
                    cache_key=np.array(cache_key),
                )

    return np.array(vectors, dtype="float32")


def embed_query(text: str) -> np.ndarray:
    """Embed a search query — fastembed applies the bge prefix automatically."""
    model = get_model()
    return np.array(list(model.query_embed(text)))[0]


def main(export_dir: Path) -> None:
    docs, _, _ = build_documents(export_dir)
    print(f"embedding {len(docs)} docs...")

    start = time.time()
    vectors = embed_documents(docs)
    elapsed = time.time() - start

    print(f"\nvectors shape: {vectors.shape}")
    print(f"elapsed: {elapsed:.1f}s ({len(docs) / elapsed:.0f} docs/sec)")


if __name__ == "__main__":
    export_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "data" / "inspect"
    main(export_dir)
