"""Ingest a local folder directly (no S3/SQS) - for local development and backfills.

    python -m app.ingestion.cli sample_docs/

With VECTOR_STORE=pinecone and real AWS credentials this writes to the same
Pinecone index the deployed API reads from.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

from app.config import get_settings
from app.ingestion.pipeline import IngestResult, ingest_bytes
from app.observability import configure_logging


def stable_doc_id(path: Path) -> str:
    return hashlib.sha1(path.name.encode(), usedforsecurity=False).hexdigest()[:16]


def ingest_folder(folder: Path) -> list[IngestResult]:
    settings = get_settings()
    results = []
    for path in sorted(folder.rglob("*")):
        if path.is_file() and path.suffix.lower() in settings.allowed_extensions:
            results.append(
                ingest_bytes(path.read_bytes(), path.name, stable_doc_id(path), uploaded_by="cli")
            )
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    args = parser.parse_args(argv)
    configure_logging(get_settings().log_level)
    results = ingest_folder(args.folder)
    for r in results:
        print(
            f"{r.status:<10} chunks={r.chunks:<4} skipped={r.skipped!s:<5}"
            f" {r.doc_id} {r.error or ''}"
        )
    return 1 if any(r.status == "FAILED" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
