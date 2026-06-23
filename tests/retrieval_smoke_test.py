#!/usr/bin/env python3
"""
Retrieval quality smoke test (beat 4). Unlike smoke_test.py (structural
invariants on the parse/chunk stage), this is inherently fuzzy — semantic
search either finds the right thread in the top-k or it doesn't, and
"close but ranked #6" isn't really a failure of the same kind as a bug.

So: each case checks whether an expected title substring shows up anywhere
in the top-k results, and the test fails only if the overall pass rate drops
below a threshold — not on any single miss. A pass rate regression here is
the signal to revisit chunk size/overlap/model choice, before any MCP work.

Usage:
    python3 retrieval_smoke_test.py
"""

import logging
import sys
from pathlib import Path

from query import search

LOG_PATH = Path(__file__).parent / "retrieval_smoke_test.log"
PASS_RATE_THRESHOLD = 0.8

# (query, expected title substring, top_k) — pulled from known recurring
# topics in the actual archive, confirmed present via earlier inspection.
CASES = [
    ("substrate dictionary semantic primitives", "Miniaturizing language models", 5),
    ("synthwave grid shader visualization", "Synthwave grid shader", 5),
    ("DJVIZ music visualizer outline", "DJVIZ", 5),
    ("puffbuster substance design", "Puffbuster", 5),
    ("sol_reason rosetta stone nucleotide DNA", "sol_reason", 5),
    ("disco reference in pulp fiction", "Disco reference in Pulp Fiction", 5),
    ("self-hosted music server", "self-hosted music server", 5),
    ("how does the board game go work", "How does the game Go work", 5),
]

log = logging.getLogger("retrieval_smoke_test")


def configure_logging() -> None:
    log.setLevel(logging.DEBUG)
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(message)s"))
    file_handler = logging.FileHandler(LOG_PATH, mode="w")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(console)
    log.addHandler(file_handler)


def main() -> int:
    passed = 0
    for query, expected_substring, top_k in CASES:
        hits = search(query, top_k=top_k)
        titles = [h.title for h in hits]
        hit = any(expected_substring.lower() in t.lower() for t in titles)
        if hit:
            passed += 1
            log.info("[ok] %r -> found %r in top %d", query, expected_substring, top_k)
        else:
            log.warning("[miss] %r -> %r not in top %d: %s", query, expected_substring, top_k, titles)

    rate = passed / len(CASES)
    log.info("\npass rate: %d/%d (%.0f%%)", passed, len(CASES), rate * 100)

    if rate < PASS_RATE_THRESHOLD:
        log.error("pass rate below threshold (%.0f%%)", PASS_RATE_THRESHOLD * 100)
        return 1
    log.info("pass rate meets threshold (%.0f%%)", PASS_RATE_THRESHOLD * 100)
    return 0


if __name__ == "__main__":
    configure_logging()
    sys.exit(main())
