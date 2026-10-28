"""Retrieval: vector search -> relevance threshold -> dedupe -> (rerank) -> budget."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from langchain_core.documents import Document

from app.config import Settings
from app.providers import BOTO_CONFIG, VectorIndex

logger = logging.getLogger(__name__)


@dataclass
class RetrievedChunk:
    doc: Document
    score: float

    @property
    def doc_id(self) -> str:
        return str(self.doc.metadata.get("doc_id", ""))


class Retriever:
    def __init__(self, index: VectorIndex, settings: Settings) -> None:
        self.index = index
        self.settings = settings
        self._reranker = None

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
        return [RetrievedChunk(chunks[r["index"]].doc, float(r["relevance_score"])) for r in ranked]

    def retrieve(self, query: str, doc_ids: list[str] | None = None) -> list[RetrievedChunk]:
        s = self.settings
        k = s.retrieval_fetch_k if s.enable_rerank else s.retrieval_k
        filters = {"doc_id": doc_ids} if doc_ids else None
        raw = self.index.search(query, k=k, filters=filters)

        seen: set[str] = set()
        chunks: list[RetrievedChunk] = []
        for doc, score in raw:
            key = str(doc.metadata.get("chunk_hash") or doc.page_content[:200])
            if score < s.min_relevance_score or key in seen:
                continue
            seen.add(key)
            chunks.append(RetrievedChunk(doc, float(score)))

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
                "candidates": len(raw),
                "kept": len(kept),
                "top_score": round(kept[0].score, 3) if kept else None,
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
