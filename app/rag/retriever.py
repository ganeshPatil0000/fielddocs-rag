"""Retrieval: semantic + keyword search -> fusion -> dedupe -> (rerank) -> budget.

Hybrid search runs two searches and merges them with Reciprocal Rank Fusion:

* semantic (dense vectors) finds chunks with the same *meaning*;
* keyword (sparse vectors / BM25) finds chunks with the same *words* - fault
  codes such as LOS-NET, alarm names and readings that embeddings blur.

RRF only looks at each list's rank order, so the two very different score
scales (cosine similarity vs sparse/BM25 weights) never need normalising.
If the keyword index is down, retrieval falls back to semantic-only results.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from langchain_core.documents import Document

from app.config import Settings
from app.keyword_index import KeywordIndex, query_coverage
from app.observability import emit_metrics
from app.providers import BOTO_CONFIG, VectorIndex

logger = logging.getLogger(__name__)


@dataclass
class RetrievedChunk:
    doc: Document
    # Cosine similarity in semantic-only mode; the fused RRF score in hybrid mode.
    score: float
    # Which search found it: "semantic", "keyword" or "both".
    match: str = "semantic"

    @property
    def doc_id(self) -> str:
        return str(self.doc.metadata.get("doc_id", ""))


def chunk_key(doc: Document) -> str:
    """Identity of a chunk across both indexes (same text -> same hash)."""
    return str(doc.metadata.get("chunk_hash") or doc.page_content[:200])


def reciprocal_rank_fusion(
    ranked_lists: list[tuple[str, list[Document], float]], k: int = 60
) -> list[RetrievedChunk]:
    """Merge best-first lists of (label, documents, weight) into one ranking.

    Each document scores ``sum(weight / (k + rank))`` over the lists it is in,
    so a chunk that both searches agree on rises to the top.
    """
    scores: dict[str, float] = {}
    docs: dict[str, Document] = {}
    found_by: dict[str, set[str]] = {}
    for label, ranked, weight in ranked_lists:
        if weight <= 0:
            continue
        seen: set[str] = set()
        for rank, doc in enumerate(ranked, start=1):
            key = chunk_key(doc)
            if key in seen:
                continue
            seen.add(key)
            scores[key] = scores.get(key, 0.0) + weight / (k + rank)
            docs.setdefault(key, doc)
            found_by.setdefault(key, set()).add(label)

    fused = [
        RetrievedChunk(
            docs[key],
            score,
            "both" if len(found_by[key]) > 1 else next(iter(found_by[key])),
        )
        for key, score in scores.items()
    ]
    fused.sort(key=lambda chunk: chunk.score, reverse=True)
    return fused


class Retriever:
    def __init__(
        self,
        index: VectorIndex,
        settings: Settings,
        keyword_index: KeywordIndex | None = None,
    ) -> None:
        self.index = index
        self.settings = settings
        self.keyword_index = keyword_index
        self._reranker = None

    @property
    def hybrid(self) -> bool:
        return self.keyword_index is not None

    def _rerank(self, query: str, chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
        if self._reranker is None:
            from langchain_aws import BedrockRerank

            self._reranker = BedrockRerank(
                model_arn=self.settings.rerank_model_arn,
                top_n=self.settings.retrieval_k,
                region_name=self.settings.aws_region,
                config=BOTO_CONFIG,
            )
        ranked = self._reranker.rerank([c.doc for c in chunks], query)
        return [
            RetrievedChunk(
                chunks[r["index"]].doc, float(r["relevance_score"]), chunks[r["index"]].match
            )
            for r in ranked
        ]

    def _semantic(self, query: str, k: int, filters: dict | None) -> list[RetrievedChunk]:
        seen: set[str] = set()
        chunks: list[RetrievedChunk] = []
        for doc, score in self.index.search(query, k=k, filters=filters):
            key = chunk_key(doc)
            if score < self.settings.min_relevance_score or key in seen:
                continue
            seen.add(key)
            chunks.append(RetrievedChunk(doc, float(score)))
        return chunks

    def _keyword(self, query: str, k: int, filters: dict | None) -> list[Document]:
        if self.keyword_index is None:
            return []
        try:
            hits = self.keyword_index.search(query, k=k, filters=filters)
        except Exception:  # noqa: BLE001 - keyword search must never break an answer
            logger.warning("keyword search failed; using semantic results only", exc_info=True)
            emit_metrics(
                self.settings.metrics_namespace,
                {"KeywordSearchErrors": 1},
                {"Service": "api", "Environment": self.settings.app_env},
            )
            return []
        return [doc for doc, _ in hits]

    def retrieve(self, query: str, doc_ids: list[str] | None = None) -> list[RetrievedChunk]:
        s = self.settings
        # Fetch extra candidates when something re-orders them afterwards.
        k = s.retrieval_fetch_k if (s.enable_rerank or self.hybrid) else s.retrieval_k
        filters = {"doc_id": doc_ids} if doc_ids else None

        semantic = self._semantic(query, k, filters)
        keyword: list[Document] = []
        if self.hybrid:
            keyword = self._keyword(query, k, filters)
            fused = reciprocal_rank_fusion(
                [
                    ("semantic", [c.doc for c in semantic], 1.0),
                    ("keyword", keyword, s.hybrid_keyword_weight),
                ],
                k=s.hybrid_rrf_k,
            )
            # Semantic hits already passed the relevance threshold. Keyword-only
            # hits must share enough of the question's keywords to be kept.
            chunks = [
                c
                for c in fused
                if c.match != "keyword"
                or query_coverage(query, c.doc.page_content) >= s.hybrid_min_keyword_coverage
            ]
        else:
            chunks = semantic

        if s.enable_rerank and len(chunks) > 1:
            chunks = self._rerank(query, chunks)
        chunks = chunks[: s.retrieval_k]

        # Hard cap on prompt size = hard cap on cost per question.
        budget, kept = s.max_context_chars, []
        for chunk in chunks:
            if len(chunk.doc.page_content) > budget:
                break
            budget -= len(chunk.doc.page_content)
            kept.append(chunk)
        logger.info(
            "retrieval",
            extra={
                "mode": "hybrid" if self.hybrid else "semantic",
                "semantic_hits": len(semantic),
                "keyword_hits": len(keyword),
                "kept": len(kept),
                "matches": [c.match for c in kept],
                "top_score": round(kept[0].score, 4) if kept else None,
            },
        )
        return kept


def format_context(chunks: list[RetrievedChunk]) -> str:
    blocks = []
    for i, chunk in enumerate(chunks, start=1):
        meta = chunk.doc.metadata
        where = meta.get("source", "unknown")
        if meta.get("page"):
            where += f", page {int(meta['page'])}"
        blocks.append(f"[{i}] {where}\n{chunk.doc.page_content}")
    return "\n\n".join(blocks)
