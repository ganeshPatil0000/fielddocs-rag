"""Duplicate detection by content hash (SHA-256).

Why a hash and not file times: ctime/mtime describe a file on one disk. A browser
upload does not carry ctime, mtime changes whenever a file is copied or saved
without edits, and two different files can share a timestamp. The SHA-256 of the
bytes changes if and only if the content changes, so it is the only reliable
"same file?" test. (Sync tools use size + mtime only as a cheap hint before
hashing; here every upload is hashed anyway.)

Ownership: the store keeps one HASH item per content hash naming the document
that owns it, written with a conditional put. If two identical files are
uploaded at the same moment, only one claim wins; the other upload is marked
DUPLICATE and never indexed. A claim whose owner is gone or failed is stale and
is taken over, so the index heals itself without a cleanup job.
"""

from __future__ import annotations

import time
from typing import Any

from app.storage import DocStatus, Store

ACTIVE = {DocStatus.PENDING, DocStatus.PROCESSING}
# An ingest that has not finished after this long has crashed (the Lambda limit is 5 min).
STUCK_AFTER_S = 15 * 60


def recent(doc: dict[str, Any]) -> bool:
    return time.time() - float(doc.get("updated_at") or 0) < STUCK_AFTER_S


def holds(doc: dict[str, Any] | None, content_hash: str) -> bool:
    """Does this registry entry really own ``content_hash``?"""
    if not doc:
        return False
    if doc.get("status") == DocStatus.INDEXED and doc.get("content_hash") == content_hash:
        return True
    # Still being indexed (the hash is only recorded when indexing finishes), or
    # waiting for an SQS retry after a temporary error.
    retrying = str(doc.get("error") or doc.get("update_error") or "").startswith("retrying:")
    in_flight = doc.get("status") in ACTIVE or doc.get("update_status") in ACTIVE or retrying
    return in_flight and recent(doc)


def _indexed_with(store: Store, content_hash: str, doc_id: str) -> dict[str, Any] | None:
    """A Ready document with this content that has no HASH item (indexed before
    duplicate detection existed, or by another process's in-memory store)."""
    for doc in store.list_documents():
        if (
            doc.get("doc_id") != doc_id
            and doc.get("status") == DocStatus.INDEXED
            and doc.get("content_hash") == content_hash
        ):
            return doc
    return None


def find_duplicate(store: Store, content_hash: str, doc_id: str | None = None) -> dict | None:
    """Read-only check (used by the API before an upload): the document that already
    has this exact content, or None."""
    owner = store.hash_owner(content_hash)
    if owner and owner != doc_id:
        doc = store.get_document(owner)
        if holds(doc, content_hash):
            return doc
    return _indexed_with(store, content_hash, doc_id or "")


def claim(store: Store, content_hash: str, doc_id: str) -> dict | None:
    """Claim ``content_hash`` for ``doc_id`` (used by ingestion).

    Returns None when the claim succeeded, or the registry entry of the document
    that already owns this content (then ``doc_id`` is a duplicate).
    """
    for _ in range(3):  # a few rounds in case several uploads race for a stale claim
        owner = store.claim_hash(content_hash, doc_id)
        if owner == doc_id:
            break
        other = store.get_document(owner)
        if holds(other, content_hash):
            return other
        if store.claim_hash(content_hash, doc_id, replace=owner) == doc_id:
            break  # took over a stale claim
    else:
        return store.get_document(store.hash_owner(content_hash) or "")

    legacy = _indexed_with(store, content_hash, doc_id)
    if legacy:
        # Hand the hash to the document that was there first.
        store.claim_hash(content_hash, str(legacy["doc_id"]), replace=doc_id)
        return legacy
    return None
