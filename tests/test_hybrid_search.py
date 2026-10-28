"""Hybrid search: keyword index, rank fusion, retriever behaviour, ingestion sync."""

import pytest
from langchain_core.documents import Document

from app.config import get_settings
from app.ingestion.pipeline import delete_document_vectors, ingest_bytes
from app.keyword_index import (
    UPSERT_BATCH,
    MemoryKeywordIndex,
    PineconeSparseIndex,
    query_coverage,
    tokenize,
)
from app.providers import get_keyword_index, get_vector_index, reset_caches
from app.rag.retriever import Retriever, reciprocal_rank_fusion

LONG_DOC = "# Battery\n\n" + "The battery string float voltage is 52 to 54 volts. " * 80


def _doc(text: str, **meta) -> Document:
    meta.setdefault("chunk_hash", text[:16])
    return Document(page_content=text, metadata=meta)


class StubIndex:
    """Stands in for either index: returns fixed results or raises."""

    def __init__(self, results=None, error: Exception | None = None) -> None:
        self.results = results or []
        self.error = error
        self.calls: list[tuple] = []

    def search(self, query, k, filters=None):
        self.calls.append((query, k, filters))
        if self.error:
            raise self.error
        return self.results[:k]


class FakePineconeIndex:
    def __init__(self, response=None) -> None:
        self.response = response
        self.upserts: list[tuple] = []
        self.deletes: list[tuple] = []
        self.searches: list[tuple] = []

    def upsert_records(self, namespace, records):
        self.upserts.append((namespace, records))

    def delete(self, ids, namespace):
        self.deletes.append((namespace, ids))

    def search(self, namespace, query):
        self.searches.append((namespace, query))
        return self.response


# -- tokenizer ---------------------------------------------------------------


def test_tokenize_keeps_codes_and_their_parts():
    tokens = tokenize("Raise the fault with code LOS-NET at 52.5 V")
    assert {"los-net", "los", "net", "52.5", "code"} <= set(tokens)
    assert "the" not in tokens


def test_query_coverage_counts_shared_keywords():
    assert query_coverage("configure BGP on a core router", "Reboot the customer router") == 0.25
    assert query_coverage("the of and", "anything at all") == 0.0


# -- in-memory BM25 ------------------------------------------------------------


def test_bm25_ranks_exact_code_first():
    idx = MemoryKeywordIndex()
    idx.upsert(
        [
            _doc("Check the light levels at the ONT.", doc_id="ont"),
            _doc("Raise a network fault with the code LOS-NET.", doc_id="ont"),
            _doc("Battery float voltage is 52 to 54 volts.", doc_id="cab"),
        ],
        ["a", "b", "c"],
    )
    hits = idx.search("What is LOS-NET?", k=3)
    assert hits[0][0].page_content.startswith("Raise a network fault")
    assert idx.search("the of and", k=3) == []


def test_bm25_filters_and_delete():
    idx = MemoryKeywordIndex()
    idx.upsert(
        [_doc("Battery voltage low.", doc_id="cab"), _doc("Battery cover screws.", doc_id="ont")],
        ["a", "b"],
    )
    only_cab = idx.search("battery", k=5, filters={"doc_id": ["cab"]})
    assert [d.metadata["doc_id"] for d, _ in only_cab] == ["cab"]
    idx.delete(["a", "missing"])
    assert idx.ids == {"b"}


# -- reciprocal rank fusion ------------------------------------------------------


def test_rrf_rewards_agreement_and_labels_where_each_chunk_came_from():
    a, b, c = _doc("alpha"), _doc("bravo"), _doc("charlie")
    fused = reciprocal_rank_fusion([("semantic", [a, b], 1.0), ("keyword", [c, b], 1.0)], k=60)
    assert fused[0].doc.page_content == "bravo"
    assert fused[0].score == pytest.approx(2 / 62)
    assert {f.doc.page_content: f.match for f in fused} == {
        "alpha": "semantic",
        "bravo": "both",
        "charlie": "keyword",
    }


def test_rrf_zero_weight_ignores_that_list():
    fused = reciprocal_rank_fusion([("semantic", [_doc("a")], 1.0), ("keyword", [_doc("b")], 0.0)])
    assert [f.doc.page_content for f in fused] == ["a"]


# -- retriever -------------------------------------------------------------------


def test_keyword_search_finds_exact_code_that_semantic_search_misses():
    code = _doc("[ont] Raise a network fault with the code LOS-NET and the meter reading.")
    other = _doc("[ont] Clean the connector end face and refit it.")
    dense = StubIndex([(other, 0.62), (code, 0.05)])  # code chunk is below the threshold
    keyword = StubIndex([(code, 7.3)])
    chunks = Retriever(dense, get_settings(), keyword).retrieve("When do I use the code LOS-NET?")
    by_text = {c.doc.page_content: c.match for c in chunks}
    assert by_text[code.page_content] == "keyword"
    assert by_text[other.page_content] == "semantic"


def test_keyword_only_hit_sharing_one_word_is_dropped():
    router = _doc("Reboot the customer router after the ONT is replaced.")
    retriever = Retriever(StubIndex([]), get_settings(), StubIndex([(router, 2.0)]))
    assert retriever.retrieve("How do I configure BGP on a core router?") == []


def test_keyword_failure_falls_back_to_semantic_results():
    doc = _doc("Battery float voltage is 52 to 54 volts.")
    broken = StubIndex(error=RuntimeError("pinecone is down"))
    chunks = Retriever(StubIndex([(doc, 0.7)]), get_settings(), broken).retrieve("battery voltage")
    assert [c.match for c in chunks] == ["semantic"]


def test_doc_filter_and_fetch_size_go_to_both_searches():
    dense, keyword = StubIndex(), StubIndex()
    Retriever(dense, get_settings(), keyword).retrieve("battery", doc_ids=["d1"])
    fetch_k = get_settings().retrieval_fetch_k
    assert dense.calls == keyword.calls == [("battery", fetch_k, {"doc_id": ["d1"]})]


def test_without_keyword_index_retrieval_is_semantic_only():
    doc = _doc("Battery float voltage is 52 to 54 volts.")
    chunks = Retriever(StubIndex([(doc, 0.7)]), get_settings()).retrieve("battery voltage")
    assert [(c.match, c.score) for c in chunks] == [("semantic", 0.7)]


# -- wiring --------------------------------------------------------------------


def test_ingest_and_delete_keep_both_indexes_in_step():
    result = ingest_bytes(LONG_DOC.encode(), "battery.md", "doc-a")
    expected = {f"doc-a#{i}" for i in range(result.chunks)}
    assert get_keyword_index().ids == expected
    assert set(get_vector_index().store.store.keys()) == expected

    delete_document_vectors("doc-a")
    assert get_keyword_index().ids == set()


def test_hybrid_search_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("ENABLE_HYBRID_SEARCH", "false")
    reset_caches()
    assert get_keyword_index() is None


def test_answer_sources_show_how_each_chunk_was_found(seeded, service):
    resp = service.answer("Which code do I raise if the drop cable tests good?", user="u1")
    matches = {s.match for s in resp.sources}
    assert matches and matches <= {"semantic", "keyword", "both"}
    assert matches & {"keyword", "both"}


# -- Pinecone sparse index (no network: fake Index object) -----------------------


def test_pinecone_sparse_upsert_sends_text_and_metadata_in_batches():
    fake = FakePineconeIndex()
    docs = [_doc(f"chunk {i}", doc_id="d1", page=None, id="x") for i in range(UPSERT_BATCH + 4)]
    PineconeSparseIndex(fake, "prod").upsert(docs, [f"d1#{i}" for i in range(len(docs))])
    assert [len(records) for _, records in fake.upserts] == [UPSERT_BATCH, 4]
    assert {ns for ns, _ in fake.upserts} == {"prod"}
    assert fake.upserts[0][1][0] == {
        "_id": "d1#0",
        "chunk_text": "chunk 0",
        "doc_id": "d1",
        "chunk_hash": "chunk 0",
    }


def test_pinecone_sparse_search_builds_query_and_parses_hits():
    hit = {
        "_id": "d1#3",
        "_score": 4.2,
        "fields": {"chunk_text": "LOS-NET fault", "doc_id": "d1", "source": "ont.md"},
    }
    fake = FakePineconeIndex({"result": {"hits": [hit]}})
    hits = PineconeSparseIndex(fake, "prod").search("LOS-NET", k=7, filters={"doc_id": ["d1"]})
    assert fake.searches == [
        (
            "prod",
            {"inputs": {"text": "LOS-NET"}, "top_k": 7, "filter": {"doc_id": {"$in": ["d1"]}}},
        )
    ]
    doc, score = hits[0]
    assert (doc.page_content, doc.id, score) == ("LOS-NET fault", "d1#3", 4.2)
    assert doc.metadata == {"doc_id": "d1", "source": "ont.md"}


def test_pinecone_sparse_delete_uses_namespace():
    fake = FakePineconeIndex()
    index = PineconeSparseIndex(fake, "prod")
    index.delete(["a", "b"])
    index.delete([])
    assert fake.deletes == [("prod", ["a", "b"])]
