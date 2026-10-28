"""Idempotent ingestion: bytes -> chunks -> embeddings -> vector index -> registry.

SQS delivers *at least once*, so the same upload can be processed twice. The
pipeline is safe to repeat:
* unchanged content (same sha256) that is already INDEXED is skipped;
* vector ids are deterministic, so a re-run overwrites instead of duplicating;
* if a new version has fewer chunks, the leftover old chunk ids are deleted.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass

from app.config import get_settings
from app.ingestion.chunking import chunk_documents, chunk_ids
from app.ingestion.loaders import IngestionError, load_document
from app.providers import get_keyword_index, get_vector_index
from app.storage import DocStatus, get_store

logger = logging.getLogger(__name__)


def _indexes() -> list:
    """The vector index, plus the keyword index when hybrid search is on.

    Both use the same deterministic chunk ids, so they stay in step: a re-run
    overwrites both and a delete removes from both.
    """
    keyword = get_keyword_index()
    return [get_vector_index()] if keyword is None else [get_vector_index(), keyword]


@dataclass
class IngestResult:
    doc_id: str
    status: str
    chunks: int = 0
    skipped: bool = False
    error: str | None = None


def ingest_bytes(
    data: bytes,
    filename: str,
    doc_id: str,
    s3_key: str | None = None,
    uploaded_by: str | None = None,
) -> IngestResult:
    settings = get_settings()
    store = get_store()
    indexes = _indexes()
    started = time.perf_counter()
    content_hash = hashlib.sha256(data).hexdigest()

    existing = store.get_document(doc_id)
    if (
        existing
        and existing.get("content_hash") == content_hash
        and existing.get("status") == DocStatus.INDEXED
    ):
        logger.info("ingest skipped: unchanged", extra={"doc_id": doc_id})
        return IngestResult(doc_id, DocStatus.INDEXED, existing.get("chunk_count", 0), skipped=True)

    previous_chunks = int(existing.get("chunk_count", 0)) if existing else 0
    if existing:
        store.update_document(doc_id, status=DocStatus.PROCESSING, error="")
    else:
        store.put_document(
            {
                "doc_id": doc_id,
                "filename": filename,
                "s3_key": s3_key or "",
                "uploaded_by": uploaded_by or "unknown",
                "status": DocStatus.PROCESSING,
                "chunk_count": 0,
            }
        )

    try:
        pages = load_document(data, filename)
        chunks = chunk_documents(
            pages, doc_id, chunk_size=settings.chunk_size, chunk_overlap=settings.chunk_overlap
        )
        if not chunks:
            raise IngestionError(f"No usable text chunks in {filename}")
        ids = chunk_ids(doc_id, len(chunks))
        for index in indexes:
            index.upsert(chunks, ids)
        if previous_chunks > len(chunks):
            stale = chunk_ids(doc_id, previous_chunks, start=len(chunks))
            for index in indexes:
                index.delete(stale)
    except IngestionError as exc:
        store.update_document(doc_id, status=DocStatus.FAILED, error=str(exc)[:500])
        logger.warning("ingest failed (permanent)", extra={"doc_id": doc_id, "error": str(exc)})
        return IngestResult(doc_id, DocStatus.FAILED, error=str(exc))
    except Exception as exc:
        # Transient (throttling, network): mark and re-raise so SQS retries.
        store.update_document(doc_id, status=DocStatus.FAILED, error=f"retrying: {exc}"[:500])
        raise

    store.update_document(
        doc_id,
        status=DocStatus.INDEXED,
        chunk_count=len(chunks),
        content_hash=content_hash,
        page_count=len(pages),
        filename=filename,
        error="",
    )
    store.bump_corpus_version()
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "ingest complete",
        extra={"doc_id": doc_id, "chunks": len(chunks), "pages": len(pages), "ms": elapsed_ms},
    )
    return IngestResult(doc_id, DocStatus.INDEXED, len(chunks))


def delete_document_vectors(doc_id: str) -> int:
    """Remove a document's vectors; returns how many ids were deleted."""
    store = get_store()
    doc = store.get_document(doc_id)
    count = int(doc.get("chunk_count", 0)) if doc else 0
    if count:
        ids = chunk_ids(doc_id, count)
        for index in _indexes():
            index.delete(ids)
    store.bump_corpus_version()
    return count
