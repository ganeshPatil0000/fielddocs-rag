"""Keyword (lexical) search: the "keyword" half of hybrid retrieval.

Embeddings are good at meaning ("no light on the box" ~ "loss of signal") but
blur exact tokens together: fault codes (LOS-NET), alarm names (MAINS FAIL),
part numbers and readings. A keyword index finds chunks that contain the same
*words* as the question, and the retriever fuses both result lists.

Two interchangeable backends behind one small interface:

* ``PineconeSparseIndex`` - a separate Pinecone *sparse* index with Pinecone's
  hosted ``pinecone-sparse-english-v0`` model (integrated embedding: we send
  text, Pinecone turns it into sparse vectors). Records share their ids with
  the dense index, so results from both can be merged and deleted together.
* ``MemoryKeywordIndex`` - a small in-process BM25 for tests and offline demos.
"""

from __future__ import annotations

import math
import re
import threading
from collections import Counter
from dataclasses import dataclass
from typing import Any, Protocol

from langchain_core.documents import Document

# A token is a run of letters/digits, optionally joined by - _ . / so codes such
# as "los-net", "fw-2.1" or "52.5" survive as one token. Their parts are indexed
# too, so a search for "LOS" still finds "LOS-NET".
_TOKEN = re.compile(r"[a-z0-9]+(?:[-_./][a-z0-9]+)*")
_PART = re.compile(r"[a-z0-9]+")

STOPWORDS = frozenset(
    """a about after all also an and any are as at be been before but by can could
    do does doing for from had has have how i if in into is it its me my no not of
    on or our out over should so some than that the their them then there these
    they this those to up was we were what when where which while who why will
    with would you your""".split()
)


def tokenize(text: str) -> list[str]:
    """Lower-case word and code tokens, without stopwords."""
    tokens: list[str] = []
    for match in _TOKEN.findall(text.lower()):
        parts = _PART.findall(match)
        if len(parts) > 1:
            tokens.append(match)
        tokens.extend(parts)
    return [t for t in tokens if t not in STOPWORDS]


def query_coverage(query: str, text: str) -> float:
    """Share of the question's keywords that appear in ``text`` (0.0 - 1.0)."""
    wanted = set(tokenize(query))
    if not wanted:
        return 0.0
    return len(wanted & set(tokenize(text))) / len(wanted)


def matches_filters(metadata: dict[str, Any], filters: dict[str, Any] | None) -> bool:
    for key, val in (filters or {}).items():
        allowed = val if isinstance(val, (list, tuple)) else [val]
        if metadata.get(key) not in allowed:
            return False
    return True


class KeywordIndex(Protocol):
    def upsert(self, documents: list[Document], ids: list[str]) -> None: ...

    def delete(self, ids: list[str]) -> None: ...

    def search(
        self, query: str, k: int, filters: dict[str, Any] | None = None
    ) -> list[tuple[Document, float]]: ...


# ---------------------------------------------------------------------------
# In-memory BM25 (tests, laptop demos)
# ---------------------------------------------------------------------------


class MemoryKeywordIndex:
    """Okapi BM25 over the chunks in this process. Fine for thousands of chunks."""

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self._docs: dict[str, tuple[Document, Counter[str], int]] = {}
        self._lock = threading.Lock()

    @property
    def ids(self) -> set[str]:
        return set(self._docs)

    def upsert(self, documents: list[Document], ids: list[str]) -> None:
        with self._lock:
            for doc, doc_id in zip(documents, ids, strict=True):
                tokens = tokenize(doc.page_content)
                stored = Document(page_content=doc.page_content, metadata=dict(doc.metadata))
                self._docs[doc_id] = (stored, Counter(tokens), len(tokens))

    def delete(self, ids: list[str]) -> None:
        with self._lock:
            for doc_id in ids:
                self._docs.pop(doc_id, None)

    def search(
        self, query: str, k: int, filters: dict[str, Any] | None = None
    ) -> list[tuple[Document, float]]:
        terms = set(tokenize(query))
        with self._lock:
            docs = list(self._docs.values())
        if not terms or not docs:
            return []

        total = len(docs)
        avg_len = sum(length for _, _, length in docs) / total or 1.0
        doc_freq = Counter(t for _, tf, _ in docs for t in terms if t in tf)

        scored: list[tuple[Document, float]] = []
        for doc, tf, length in docs:
            if not matches_filters(doc.metadata, filters):
                continue
            score = 0.0
            for term in terms:
                freq = tf.get(term, 0)
                if not freq:
                    continue
                idf = math.log(1 + (total - doc_freq[term] + 0.5) / (doc_freq[term] + 0.5))
                norm = self.k1 * (1 - self.b + self.b * length / avg_len)
                score += idf * freq * (self.k1 + 1) / (freq + norm)
            if score > 0:
                scored.append((doc, score))
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:k]


# ---------------------------------------------------------------------------
# Pinecone sparse index (deployed)
# ---------------------------------------------------------------------------

TEXT_FIELD = "chunk_text"  # must match the index's field_map (scripts/create_pinecone_index.py)
_RESERVED_FIELDS = {"_id", "id", TEXT_FIELD}
# Pinecone accepts at most 96 records per upsert when it embeds them itself.
UPSERT_BATCH = 96


def _field(obj: Any, key: str) -> Any:
    """Read a key from a dict or a Pinecone response model."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(key)
    try:
        return obj[key]
    except (KeyError, TypeError, AttributeError):
        return getattr(obj, key, None)


@dataclass
class PineconeSparseIndex:
    index: Any  # pinecone Index for a sparse index with integrated embedding
    namespace: str

    def upsert(self, documents: list[Document], ids: list[str]) -> None:
        from app.providers import clean_metadata

        records = []
        for doc, doc_id in zip(documents, ids, strict=True):
            meta = {
                k: v for k, v in clean_metadata(doc.metadata).items() if k not in _RESERVED_FIELDS
            }
            records.append({"_id": doc_id, TEXT_FIELD: doc.page_content, **meta})
        for start in range(0, len(records), UPSERT_BATCH):
            self.index.upsert_records(self.namespace, records[start : start + UPSERT_BATCH])

    def delete(self, ids: list[str]) -> None:
        if ids:
            self.index.delete(ids=ids, namespace=self.namespace)

    def search(
        self, query: str, k: int, filters: dict[str, Any] | None = None
    ) -> list[tuple[Document, float]]:
        from app.providers import pinecone_filter

        search_query: dict[str, Any] = {"inputs": {"text": query}, "top_k": k}
        pc_filter = pinecone_filter(filters)
        if pc_filter:
            search_query["filter"] = pc_filter
        response = self.index.search(namespace=self.namespace, query=search_query)

        results: list[tuple[Document, float]] = []
        for hit in _field(_field(response, "result"), "hits") or []:
            fields = dict(_field(hit, "fields") or {})
            text = str(fields.pop(TEXT_FIELD, ""))
            doc = Document(page_content=text, metadata=fields, id=str(_field(hit, "_id")))
            results.append((doc, float(_field(hit, "_score") or 0.0)))
        return results
