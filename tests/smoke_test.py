#!/usr/bin/env python3
"""
Smoke test for the parse/chunk stage (ingest.build_documents). Runs against
the real unzipped export and asserts the structural invariants we've been
checking by eye so far. No embeddings/DB involved — this gates that stage
before it's worth building further.

Checks are split into two severities:
  - hard:  structural invariants that must always hold, regardless of what's
           actually in the archive (empty text, duplicate ids, chunk size
           caps, determinism). A failure here fails the run.
  - soft:  signals about *this* archive's content (known project present,
           memory doc non-trivial). Useful, but content drifts over time as
           you have more conversations — a failure here is logged as a
           warning, not a hard failure.

Usage:
    python3 smoke_test.py [export_dir]

Output goes to both the console and smoke_test.log (next to this script).
"""

import logging
import sys
from collections import Counter
from pathlib import Path

from ingest import build_documents, MAX_CHUNK_CHARS

LOG_PATH = Path(__file__).parent / "smoke_test.log"

# Known-present, not exhaustive: the project this pipeline exists for must
# have at least these docs, but adding a 4th doc to it later isn't a failure.
KNOWN_SOL_REASON_DOCS = {
    "sol_reason/string.py",
    "sol_reason/CLAUDE.md",
    "sol_reason/string_myth.md",
}

log = logging.getLogger("smoke_test")

hard_failures: list[str] = []
soft_failures: list[str] = []


def check(label: str, condition: bool, detail: str = "", *, severity: str = "hard") -> None:
    if condition:
        log.info("[ok] %s", label)
        return
    if severity == "hard":
        log.error("[FAIL] %s%s", label, f" — {detail}" if detail else "")
        hard_failures.append(label)
    else:
        log.warning("[warn] %s%s", label, f" — {detail}" if detail else "")
        soft_failures.append(label)


def main(export_dir: Path) -> int:
    docs, conversation_count, skipped = build_documents(export_dir)
    by_type = Counter(d.source_type for d in docs)

    # --- hard: basic shape ---
    check("at least one doc produced", len(docs) > 0)
    check("no empty text in any doc", all(d.text.strip() for d in docs))
    check("no empty title in any doc", all(d.title.strip() for d in docs))

    ids = [d.id for d in docs]
    check("all doc ids unique", len(ids) == len(set(ids)), f"{len(ids) - len(set(ids))} duplicates")

    # --- hard: conversation accounting — every input conversation is either
    #     represented by >=1 chunk, or explicitly counted as skipped ---
    represented = {d.id.rsplit(":", 1)[0] for d in docs if d.source_type == "conversation"}
    check(
        "conversation count + skipped accounts for all input conversations",
        len(represented) + skipped == conversation_count,
        f"{len(represented)} represented + {skipped} skipped != {conversation_count} total",
    )

    # --- hard: chunk size cap is a structural property of the pipeline
    #     (MAX_CHUNK_CHARS), not of this archive's content ---
    conv_docs = [d for d in docs if d.source_type == "conversation"]
    oversized = [d for d in conv_docs if len(d.text) > MAX_CHUNK_CHARS]
    check(
        "no conversation chunk exceeds MAX_CHUNK_CHARS",
        len(oversized) == 0,
        f"{len(oversized)} chunks over {MAX_CHUNK_CHARS} chars",
    )

    # --- hard: determinism — rebuilding from the same archive twice must
    #     agree on everything except the memory doc's synthetic timestamp ---
    docs2, conversation_count2, skipped2 = build_documents(export_dir)
    comparable = lambda ds: [(d.id, d.text, d.source_type, d.title) for d in ds]
    check(
        "rebuild from the same export is deterministic",
        comparable(docs) == comparable(docs2) and conversation_count == conversation_count2 and skipped == skipped2,
    )

    # --- soft: this archive's content, expected to drift over time ---
    sol_reason_titles = {d.title for d in docs if d.source_type == "project_doc" and d.title.startswith("sol_reason/")}
    check(
        "sol_reason project docs include the known set",
        KNOWN_SOL_REASON_DOCS <= sol_reason_titles,
        f"missing {KNOWN_SOL_REASON_DOCS - sol_reason_titles}",
        severity="soft",
    )

    memory_docs = [d for d in docs if d.source_type == "memory"]
    check("exactly one memory doc", len(memory_docs) == 1)
    check(
        "memory doc is non-trivial length",
        bool(memory_docs) and len(memory_docs[0].text) > 100,
        severity="soft",
    )

    log.info("%d docs, %d total %s", len(docs), sum(by_type.values()), dict(by_type))
    if hard_failures or soft_failures:
        log.info("%d hard failure(s), %d soft warning(s)", len(hard_failures), len(soft_failures))
    else:
        log.info("all checks passed")
    return 1 if hard_failures else 0


def configure_logging() -> None:
    log.setLevel(logging.DEBUG)
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(message)s"))
    file_handler = logging.FileHandler(LOG_PATH, mode="w")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(console)
    log.addHandler(file_handler)


if __name__ == "__main__":
    configure_logging()
    export_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent.parent / "data" / "inspect"
    sys.exit(main(export_dir))
