"""Idempotent, versioned ingestion: bytes -> chunks -> embeddings -> indexes -> registry.

SQS delivers *at least once*, so the same upload can be processed twice. The
pipeline is safe to repeat:
* unchanged content (same sha256) that is already INDEXED is skipped;
* vector ids are deterministic, so a re-run overwrites instead of duplicating.

Replacing a document (a new file for an existing doc_id) never mixes versions:
1. the new version's chunks are written under new ids (``<doc_id>#v2#<n>``)
   while the current version keeps answering questions;
2. only when every chunk is stored does one registry write move ``version``
   forward (and the answer cache is invalidated);
3. the old version's chunks are then deleted.
Retrieval only serves chunks whose version matches the registry, so a crash at
any step leaves either the old or the new version answering, never a mix. A
new file that can't be read (e.g. a scanned PDF) is rejected and the current
version stays live.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field

from app.config import get_settings
from app.ingestion import duplicates
from app.ingestion.chunking import chunk_documents, chunk_ids
from app.ingestion.loaders import IngestionError, load_document
from app.providers import get_keyword_index, get_vector_index
from app.storage import DocStatus, get_store, live_version

logger = logging.getLogger(__name__)


def _indexes() -> list:
    """The vector index, plus the keyword index when hybrid search is on.

    Both use the same deterministic chunk ids, so they stay in step: a re-run
    overwrites both and a delete removes from both.
    """
    keyword = get_keyword_index()
    return [get_vector_index()] if keyword is None else [get_vector_index(), keyword]


def _delete_chunks(ids: list[str], doc_id: str) -> None:
    """Best effort: chunks left behind are never served (wrong version) and are
    overwritten the next time that version is written."""
    if not ids:
        return
    for index in _indexes():
        try:
            index.delete(ids)
        except Exception:  # noqa: BLE001
            logger.warning("chunk cleanup failed", extra={"doc_id": doc_id}, exc_info=True)


@dataclass
class IngestResult:
    doc_id: str
    status: str
    chunks: int = 0
    skipped: bool = False
    error: str | None = None
    version: int = 1
    replaced: bool = False
    # Set when the content is already indexed as another document.
    duplicate_of: str | None = None
    # S3 objects no document version points to any more (old file, rejected file).
    obsolete_s3_keys: list[str] = field(default_factory=list)


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
    current = live_version(existing)
    if existing and current is not None and existing.get("content_hash") == content_hash:
        logger.info("ingest skipped: unchanged", extra={"doc_id": doc_id})
        if existing.get("update_status"):  # a replace with the same file: nothing to do
            store.update_document(doc_id, update_status="", update_error="", pending_filename="")
        stale = [s3_key] if s3_key and s3_key != existing.get("s3_key") else []
        return IngestResult(
            doc_id,
            DocStatus.INDEXED,
            existing.get("chunk_count", 0),
            skipped=True,
            version=current,
            obsolete_s3_keys=stale,
        )

    replacing = existing is not None and current is not None
    new_file_key = [s3_key] if s3_key and s3_key != (existing or {}).get("s3_key") else []

    # Same bytes as another document? Then this upload adds nothing but a second
    # copy that could later drift out of date, so it is not indexed.
    original = duplicates.claim(store, content_hash, doc_id)
    if original is not None:
        name = original.get("filename") or original["doc_id"]
        message = f"Same content as {name}, which is already indexed."
        logger.info("ingest skipped: duplicate", extra={"doc_id": doc_id, "of": original["doc_id"]})
        if replacing:
            store.update_document(doc_id, update_status=DocStatus.FAILED, update_error=message)
        elif existing:
            store.update_document(
                doc_id, status=DocStatus.DUPLICATE, error=message, duplicate_of=original["doc_id"]
            )
        else:
            store.put_document(
                {
                    "doc_id": doc_id,
                    "filename": filename,
                    "s3_key": s3_key or "",
                    "uploaded_by": uploaded_by or "unknown",
                    "status": DocStatus.DUPLICATE,
                    "error": message,
                    "duplicate_of": original["doc_id"],
                    "chunk_count": 0,
                    "version": 1,
                }
            )
        return IngestResult(
            doc_id,
            DocStatus.DUPLICATE,
            error=message,
            version=current or 1,
            duplicate_of=original["doc_id"],
            # Nothing points to the uploaded copy; the original keeps its own file.
            obsolete_s3_keys=new_file_key,
        )

    if replacing:
        version = current + 1
        store.update_document(
            doc_id, update_status=DocStatus.PROCESSING, update_error="", pending_filename=filename
        )
    else:
        # First ingest (or a retry of one): nothing is being served yet.
        version = int(existing.get("version") or 1) if existing else 1
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
                    "version": version,
                }
            )

    ids: list[str] = []
    try:
        pages = load_document(data, filename)
        chunks = chunk_documents(
            pages,
            doc_id,
            chunk_size=settings.chunk_size,
            chunk_overlap=settings.chunk_overlap,
            version=version,
        )
        if not chunks:
            raise IngestionError(f"No usable text chunks in {filename}")
        ids = chunk_ids(doc_id, len(chunks), version=version)
        for index in indexes:
            index.upsert(chunks, ids)
    except IngestionError as exc:
        message = str(exc)[:500]
        logger.warning("ingest failed (permanent)", extra={"doc_id": doc_id, "error": message})
        store.release_hash(content_hash, doc_id)  # this content was never indexed
        if replacing:
            # The current version keeps answering; tell the admin why the new file was rejected.
            _delete_chunks(ids, doc_id)
            store.update_document(doc_id, update_status=DocStatus.FAILED, update_error=message)
            return IngestResult(
                doc_id,
                DocStatus.FAILED,
                error=message,
                version=current,
                obsolete_s3_keys=new_file_key,
            )
        store.update_document(doc_id, status=DocStatus.FAILED, error=message)
        return IngestResult(doc_id, DocStatus.FAILED, error=message, version=version)
    except Exception as exc:
        # Transient (throttling, network): mark and re-raise so SQS retries. Chunks
        # already written for the new version are never served (wrong version).
        note = f"retrying: {exc}"[:500]
        if replacing:
            store.update_document(doc_id, update_status=DocStatus.PROCESSING, update_error=note)
        else:
            store.update_document(doc_id, status=DocStatus.FAILED, error=note)
        raise

    previous_chunks = int(existing.get("chunk_count", 0)) if existing else 0
    previous_key = existing.get("s3_key") if existing else None
    previous_hash = existing.get("content_hash") if existing else None
    # The switch: one registry write makes the new version the one answers use.
    store.update_document(
        doc_id,
        status=DocStatus.INDEXED,
        version=version,
        chunk_count=len(chunks),
        content_hash=content_hash,
        page_count=len(pages),
        filename=filename,
        s3_key=s3_key or previous_key or "",
        error="",
        update_status="",
        update_error="",
        pending_filename="",
        duplicate_of="",
    )
    store.bump_corpus_version()
    if previous_hash and previous_hash != content_hash:
        store.release_hash(previous_hash, doc_id)  # the old content is no longer indexed

    obsolete: list[str] = []
    if replacing:
        _delete_chunks(chunk_ids(doc_id, previous_chunks, version=current), doc_id)
        if previous_key and s3_key and previous_key != s3_key:
            obsolete.append(previous_key)
    elif previous_chunks > len(chunks):
        # Retry of a first ingest that produced more chunks last time.
        _delete_chunks(
            chunk_ids(doc_id, previous_chunks, start=len(chunks), version=version), doc_id
        )

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "ingest complete",
        extra={
            "doc_id": doc_id,
            "version": version,
            "replaced": replacing,
            "chunks": len(chunks),
            "pages": len(pages),
            "ms": elapsed_ms,
        },
    )
    return IngestResult(
        doc_id,
        DocStatus.INDEXED,
        len(chunks),
        version=version,
        replaced=replacing,
        obsolete_s3_keys=obsolete,
    )


def delete_document_vectors(doc_id: str) -> int:
    """Remove a document's vectors; returns how many ids were deleted."""
    store = get_store()
    doc = store.get_document(doc_id)
    count = int(doc.get("chunk_count", 0)) if doc else 0
    if doc and doc.get("content_hash"):
        store.release_hash(str(doc["content_hash"]), doc_id)
    if count:
        ids = chunk_ids(doc_id, count, version=int(doc.get("version") or 1))
        for index in _indexes():
            index.delete(ids)
    store.bump_corpus_version()
    return count
