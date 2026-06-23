#!/usr/bin/env python3
"""
Embedding step: turn Documents into vectors with a local sentence-transformers
model. No DB yet — running this directly just times the run and reports
shape/throughput, as a checkpoint before wiring it into the DB writer.

Usage:
    python3 embed.py [export_dir]
"""

import sys
import time
from pathlib import Path

import numpy as np

from ingest import build_documents, Document

# bge models are trained with an asymmetric convention: passages are embedded
# as-is, but queries get a fixed instruction prefix to push them into the same
# representation space as a "thing worth retrieving for this question." Only
# applies to embed_query, not embed_documents.
MODEL_NAME = "BAAI/bge-base-en-v1.5"
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

_model = None


def get_model():
    """Load the embedding model once, preferring the M1's GPU (MPS) with a
    fallback to CPU if MPS errors on this model — MPS op support is less
    mature than CUDA's, so this isn't guaranteed in advance."""
    from sentence_transformers import SentenceTransformer
    global _model
    if _model is not None:
        return _model

    for device in ("mps", "cpu"):
        try:
            model = SentenceTransformer(MODEL_NAME, device=device)
            model.encode(["warmup"])  # exercise the model before committing to this device
            # stderr, not stdout: when this runs inside server.py as an MCP
            # stdio server, stdout is the JSON-RPC protocol channel — any
            # stray print() there corrupts the stream for the client.
            print(f"embedding model loaded on device={device!r}", file=sys.stderr)
            _model = model
            return model
        except Exception as e:
            if device == "mps":
                print(f"device={device!r} failed ({e}); falling back", file=sys.stderr)
    raise RuntimeError(f"could not load {MODEL_NAME} on mps or cpu")


def embed_documents(docs: list[Document]) -> np.ndarray:
    """Embed document text as-is (no prefix) — these are the passages."""
    model = get_model()
    texts = [d.text for d in docs]
    return model.encode(texts, batch_size=64, show_progress_bar=True, normalize_embeddings=True)


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
