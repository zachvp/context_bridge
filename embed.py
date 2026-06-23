#!/usr/bin/env python3
"""
Embedding step: turn Documents into vectors with a local sentence-transformers
model. No DB yet — running this directly just times the run and reports
shape/throughput, as a checkpoint before wiring it into the DB writer.

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
# representation space as a "thing worth retrieving for this question." Only
# applies to embed_query, not embed_documents.
#
# Must be a HuggingFace model ID compatible with sentence-transformers.
# Override via CONTEXT_BRIDGE_MODEL in .env.
# WARNING: changing the model after building a DB triggers a full rebuild on
# the next run (the meta table mismatch is detected automatically).
MODEL_NAME = os.environ.get("CONTEXT_BRIDGE_MODEL", "BAAI/bge-base-en-v1.5")
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

# Number of documents per embedding batch. Reduce if you hit OOM during build.
# Override via CONTEXT_BRIDGE_BATCH_SIZE in .env.
BATCH_SIZE = int(os.environ.get("CONTEXT_BRIDGE_BATCH_SIZE", "64"))

_model = None

_DEVICES = ("cuda", "mps", "cpu")


def _model_is_cached(model_name: str) -> bool:
    cache_root = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"
    return (cache_root / f"models--{model_name.replace('/', '--')}").exists()


def get_model():
    """Load the embedding model once. Tries CUDA, then MPS (Apple Silicon),
    then CPU. Each device is exercised with a warmup encode before committing —
    MPS and CUDA op support can fail at runtime even if the device is present."""
    from sentence_transformers import SentenceTransformer

    global _model
    if _model is not None:
        return _model

    if not _model_is_cached(MODEL_NAME):
        # stderr, not stdout: stdout is the MCP stdio JSON-RPC channel in server.py.
        print(
            f"  first run: downloading {MODEL_NAME} (~440 MB from HuggingFace)...",
            file=sys.stderr,
        )
        print(
            "  cached at ~/.cache/huggingface/ after this — subsequent runs are instant.",
            file=sys.stderr,
        )

    for i, device in enumerate(_DEVICES):
        try:
            model = SentenceTransformer(MODEL_NAME, device=device)
            model.encode(["warmup"])
            print(f"embedding model loaded on device={device!r}", file=sys.stderr)
            _model = model
            return model
        except Exception as e:
            if i < len(_DEVICES) - 1:
                print(f"device={device!r} failed ({e}); trying next", file=sys.stderr)
    raise RuntimeError(f"could not load {MODEL_NAME} on any device (tried {', '.join(_DEVICES)})")


def embed_documents(docs: list[Document]) -> np.ndarray:
    """Embed document text as-is (no prefix) — these are the passages."""
    model = get_model()
    texts = [d.text for d in docs]
    return model.encode(texts, batch_size=BATCH_SIZE, show_progress_bar=True, normalize_embeddings=True)


def embed_query(text: str) -> np.ndarray:
    """Embed a search query — prefixed per bge's asymmetric convention."""
    model = get_model()
    return model.encode([QUERY_PREFIX + text], normalize_embeddings=True)[0]


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
