"""Replacing a document: versioned chunks, a safe switch, and no mixing of versions."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from langchain_core.documents import Document

from app.config import get_settings
from app.ingestion.chunking import chunk_ids, chunk_version
from app.ingestion.pipeline import delete_document_vectors, ingest_bytes
from app.providers import get_keyword_index, get_vector_index
from app.rag.retriever import Retriever
from app.storage import DocStatus, MemoryStore, get_store

OLD = "# Battery\n\nReplace the street cabinet batteries every 4 years."
NEW = "# Battery\n\nReplace the street cabinet batteries every 5 years after a load test."


def _vector_ids() -> set[str]:
    return set(get_vector_index().store.store.keys())


def _answers_from(question: str) -> list[str]:
    from app.api.main import get_rag_service

    service = get_rag_service()
    return [c.doc.page_content for c in service.retriever.retrieve(question)]


# -- chunk ids -----------------------------------------------------------------
def test_version_one_keeps_the_original_ids():
    assert chunk_ids("abc", 2) == ["abc#0", "abc#1"]
    assert chunk_ids("abc", 2, version=1) == ["abc#0", "abc#1"]
    assert chunk_ids("abc", 2, version=3) == ["abc#v3#0", "abc#v3#1"]


def test_chunk_version_reads_pinecone_floats_and_old_chunks():
    assert chunk_version({"doc_version": 2.0}) == 2
    assert chunk_version({}) == 1  # indexed before versioning existed
    assert chunk_version({"doc_version": "junk"}) == 1


# -- pipeline --------------------------------------------------------------------
def test_replace_switches_to_the_new_version():
    first = ingest_bytes(OLD.encode(), "battery.md", "doc-a", s3_key="raw/doc-a/battery.md")
    assert first.version == 1 and not first.replaced
    version_before = get_store().get_corpus_version()

    second = ingest_bytes(NEW.encode(), "battery-v2.md", "doc-a", s3_key="raw/doc-a/battery-v2.md")

    assert second.replaced and second.version == 2
    doc = get_store().get_document("doc-a")
    assert doc["status"] == DocStatus.INDEXED and doc["version"] == 2
    assert doc["filename"] == "battery-v2.md" and doc["s3_key"] == "raw/doc-a/battery-v2.md"
    assert doc["update_status"] == "" and doc["chunk_count"] == second.chunks
    assert _vector_ids() == set(chunk_ids("doc-a", second.chunks, version=2))
    assert get_store().get_corpus_version() > version_before  # answer cache invalidated
    assert second.obsolete_s3_keys == ["raw/doc-a/battery.md"]


def test_replace_keeps_the_same_file_name_without_deleting_it():
    ingest_bytes(OLD.encode(), "battery.md", "doc-a", s3_key="raw/doc-a/battery.md")
    second = ingest_bytes(NEW.encode(), "battery.md", "doc-a", s3_key="raw/doc-a/battery.md")
    assert second.replaced and second.obsolete_s3_keys == []  # S3 already overwrote it


def test_every_replace_gets_a_new_version():
    ingest_bytes(OLD.encode(), "battery.md", "doc-a")
    ingest_bytes(NEW.encode(), "battery.md", "doc-a")
    third = ingest_bytes((NEW + " Use gloves.").encode(), "battery.md", "doc-a")
    assert third.version == 3
    assert _vector_ids() == set(chunk_ids("doc-a", third.chunks, version=3))


def test_rejected_new_file_keeps_the_current_version_answering():
    ingest_bytes(OLD.encode(), "battery.md", "doc-a", s3_key="raw/doc-a/battery.md")
    ids_before = _vector_ids()

    result = ingest_bytes(b"MZ\x90\x00", "tool.exe", "doc-a", s3_key="raw/doc-a/tool.exe")

    assert result.status == DocStatus.FAILED and not result.replaced
    doc = get_store().get_document("doc-a")
    assert doc["status"] == DocStatus.INDEXED and doc["version"] == 1
    assert doc["update_status"] == DocStatus.FAILED and "Unsupported" in doc["update_error"]
    assert doc["filename"] == "battery.md"
    assert _vector_ids() == ids_before
    assert result.obsolete_s3_keys == ["raw/doc-a/tool.exe"]  # nothing points to it
    assert any("4 years" in text for text in _answers_from("How often to replace batteries?"))


def test_replacing_with_the_same_file_changes_nothing():
    ingest_bytes(OLD.encode(), "battery.md", "doc-a")
    get_store().update_document("doc-a", update_status=DocStatus.PENDING, pending_filename="x.md")
    version_before = get_store().get_corpus_version()

    again = ingest_bytes(OLD.encode(), "battery.md", "doc-a")

    assert again.skipped and again.version == 1
    assert get_store().get_document("doc-a")["update_status"] == ""
    assert get_store().get_corpus_version() == version_before


def test_failure_half_way_never_mixes_versions(monkeypatch):
    ingest_bytes(OLD.encode(), "battery.md", "doc-a")
    keyword = get_keyword_index()
    assert keyword is not None

    # The new version reaches the vector index, then the keyword index fails.
    def broken(*args, **kwargs):
        raise ConnectionError("keyword index unreachable")

    monkeypatch.setattr(keyword, "upsert", broken)
    with pytest.raises(ConnectionError):
        ingest_bytes(NEW.encode(), "battery.md", "doc-a")

    doc = get_store().get_document("doc-a")
    assert doc["version"] == 1 and doc["status"] == DocStatus.INDEXED
    assert doc["update_status"] == DocStatus.PROCESSING
    assert doc["update_error"].startswith("retrying:")
    # Half-written version 2 chunks exist but are never used for answers.
    assert any("#v2#" in i for i in _vector_ids())
    texts = _answers_from("How often to replace batteries?")
    assert texts and all("5 years" not in t for t in texts)

    # SQS retries the message: this time it completes and answers switch.
    monkeypatch.undo()
    retry = ingest_bytes(NEW.encode(), "battery.md", "doc-a")
    assert retry.version == 2 and retry.replaced
    assert _vector_ids() == set(chunk_ids("doc-a", retry.chunks, version=2))
    texts = _answers_from("How often to replace batteries?")
    assert texts and all("4 years" not in t for t in texts)


def test_delete_after_replace_removes_the_current_version():
    ingest_bytes(OLD.encode(), "battery.md", "doc-a")
    second = ingest_bytes(NEW.encode(), "battery.md", "doc-a")
    assert delete_document_vectors("doc-a") == second.chunks
    assert _vector_ids() == set()


# -- retrieval ---------------------------------------------------------------------
class StubIndex:
    def __init__(self, results) -> None:
        self.results = results

    def search(self, query, k, filters=None):
        return self.results[:k]


def _chunk(text: str, doc_id: str, version: float | None) -> Document:
    meta = {"doc_id": doc_id, "chunk_hash": text[:16]}
    if version is not None:
        meta["doc_version"] = version
    return Document(page_content=text, metadata=meta)


def _registry(**docs: dict) -> MemoryStore:
    store = MemoryStore()
    for doc_id, fields in docs.items():
        store.put_document({"doc_id": doc_id, **fields})
    return store


def _shared_settings():
    # DynamoDB is the shared registry in AWS; strict rules apply there.
    return get_settings().model_copy(update={"storage_backend": "dynamodb"})


def test_only_the_current_version_is_served():
    old = _chunk("battery every 4 years", "d1", 1.0)
    new = _chunk("battery every 5 years", "d1", 2.0)
    registry = _registry(d1={"status": DocStatus.INDEXED, "version": 2})
    retriever = Retriever(
        StubIndex([(old, 0.9), (new, 0.8)]), _shared_settings(), registry=registry
    )
    assert [c.doc.page_content for c in retriever.retrieve("battery")] == ["battery every 5 years"]


def test_unchanged_text_in_both_versions_still_comes_from_the_current_one():
    # Same text -> same chunk_hash. The stale copy must not hide the current one.
    stale = _chunk("battery voltage 52 V", "d1", 1.0)
    current = _chunk("battery voltage 52 V", "d1", 2.0)
    registry = _registry(d1={"status": DocStatus.INDEXED, "version": 2})
    retriever = Retriever(
        StubIndex([(stale, 0.9), (current, 0.9)]), _shared_settings(), registry=registry
    )
    chunks = retriever.retrieve("battery")
    assert len(chunks) == 1 and chunk_version(chunks[0].doc.metadata) == 2


def test_documents_still_indexing_for_the_first_time_are_not_served():
    chunk = _chunk("fan starts at 35 C", "d2", None)
    registry = _registry(d2={"status": DocStatus.PROCESSING, "version": 1})
    retriever = Retriever(StubIndex([(chunk, 0.9)]), _shared_settings(), registry=registry)
    assert retriever.retrieve("fan") == []


def test_unknown_documents_depend_on_the_registry_type():
    chunk = _chunk("fan starts at 35 C", "gone", None)
    strict = Retriever(StubIndex([(chunk, 0.9)]), _shared_settings(), registry=MemoryStore())
    assert strict.retrieve("fan") == []  # deleted in the shared registry
    local = Retriever(StubIndex([(chunk, 0.9)]), get_settings(), registry=MemoryStore())
    assert len(local.retrieve("fan")) == 1  # CLI-ingested, not in this process's registry


def test_no_registry_means_no_version_filter():
    chunk = _chunk("fan starts at 35 C", "d3", 7.0)
    assert len(Retriever(StubIndex([(chunk, 0.9)]), _shared_settings()).retrieve("fan")) == 1


# -- API ---------------------------------------------------------------------------
def _client() -> TestClient:
    from app.api.main import create_app

    return TestClient(create_app())


def test_local_replace_endpoint_switches_answers():
    client = _client()
    up = client.post("/v1/documents/upload", files={"file": ("battery.md", OLD.encode())})
    doc_id = up.json()["doc_id"]

    resp = client.post(
        f"/v1/documents/{doc_id}/replace", files={"file": ("battery.md", NEW.encode())}
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["doc_id"] == doc_id and body["version"] == 2 and body["status"] == "INDEXED"
    answer = client.post(
        "/v1/chat", json={"question": "How often to replace the cabinet batteries?"}
    )
    snippets = " ".join(s["snippet"] for s in answer.json()["sources"])
    assert "5 years" in snippets and "4 years" not in snippets


def test_replace_url_needs_a_ready_document():
    client = _client()
    assert (
        client.post("/v1/documents/nope/replace-url", json={"filename": "a.md"}).status_code == 404
    )
    get_store().put_document({"doc_id": "busy", "status": DocStatus.PROCESSING, "chunk_count": 0})
    resp = client.post("/v1/documents/busy/replace-url", json={"filename": "a.md"})
    assert resp.status_code == 409


def test_replace_url_refuses_while_a_new_version_is_indexing():
    ingest_bytes(OLD.encode(), "battery.md", "doc-a")
    get_store().update_document("doc-a", update_status=DocStatus.PROCESSING)
    resp = _client().post("/v1/documents/doc-a/replace-url", json={"filename": "a.md"})
    assert resp.status_code == 409


def test_replace_url_without_a_bucket_is_501_so_the_ui_uses_local_replace():
    ingest_bytes(OLD.encode(), "battery.md", "doc-a")
    resp = _client().post("/v1/documents/doc-a/replace-url", json={"filename": "a.md"})
    assert resp.status_code == 501
    assert get_store().get_document("doc-a").get("update_status") in (None, "")


def test_replace_url_signs_the_documents_own_folder(monkeypatch):
    monkeypatch.setenv("DOCUMENTS_BUCKET", "docs-bucket")
    from app.api import main
    from app.providers import reset_caches

    reset_caches()

    class FakeS3:
        def generate_presigned_post(self, Bucket, Key, Fields, Conditions, ExpiresIn):  # noqa: N803
            return {"url": f"https://{Bucket}.s3.amazonaws.com", "fields": {"key": Key, **Fields}}

    monkeypatch.setattr(main, "_s3", lambda: FakeS3())
    ingest_bytes(OLD.encode(), "battery.md", "doc-a")

    resp = _client().post(
        "/v1/documents/doc-a/replace-url",
        json={"filename": "battery v2.md", "content_type": "text/markdown"},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["key"] == "raw/doc-a/battery_v2.md"
    doc = get_store().get_document("doc-a")
    assert doc["update_status"] == DocStatus.PENDING and doc["pending_filename"] == "battery_v2.md"
    assert doc["version"] == 1  # still the live version until indexing finishes


def test_document_list_shows_version_and_update_progress():
    ingest_bytes(OLD.encode(), "battery.md", "doc-a")
    get_store().update_document("doc-a", update_status=DocStatus.FAILED, update_error="bad file")
    [doc] = _client().get("/v1/documents").json()
    assert doc["version"] == 1
    assert doc["update_status"] == "FAILED" and doc["update_error"] == "bad file"
