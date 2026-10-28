from app.ingestion.pipeline import delete_document_vectors, ingest_bytes
from app.providers import get_vector_index
from app.storage import DocStatus, get_store

LONG_DOC = "# Battery\n\n" + "The battery string float voltage is 52 to 54 volts. " * 80
SHORT_DOC = "# Battery\n\nReplace batteries every 4 years."


def _vector_ids() -> set[str]:
    return set(get_vector_index().store.store.keys())


def test_ingest_indexes_and_registers_document():
    result = ingest_bytes(LONG_DOC.encode(), "battery.md", "doc-a", uploaded_by="tester")
    assert result.status == DocStatus.INDEXED and result.chunks > 1

    doc = get_store().get_document("doc-a")
    assert doc["status"] == DocStatus.INDEXED
    assert doc["chunk_count"] == result.chunks
    assert {f"doc-a#{i}" for i in range(result.chunks)} == _vector_ids()
    assert get_store().get_corpus_version() == 1


def test_reingesting_identical_content_is_skipped():
    ingest_bytes(LONG_DOC.encode(), "battery.md", "doc-a")
    again = ingest_bytes(LONG_DOC.encode(), "battery.md", "doc-a")
    assert again.skipped
    assert get_store().get_corpus_version() == 1  # no cache invalidation for a no-op


def test_shorter_new_version_removes_stale_chunks():
    first = ingest_bytes(LONG_DOC.encode(), "battery.md", "doc-a")
    second = ingest_bytes(SHORT_DOC.encode(), "battery.md", "doc-a")
    assert second.chunks < first.chunks
    assert _vector_ids() == {f"doc-a#{i}" for i in range(second.chunks)}


def test_bad_file_is_marked_failed_without_raising():
    result = ingest_bytes(b"MZ\x90\x00", "tool.exe", "doc-x")
    assert result.status == DocStatus.FAILED
    assert "Unsupported" in get_store().get_document("doc-x")["error"]


def test_delete_document_vectors():
    result = ingest_bytes(LONG_DOC.encode(), "battery.md", "doc-a")
    assert delete_document_vectors("doc-a") == result.chunks
    assert _vector_ids() == set()
