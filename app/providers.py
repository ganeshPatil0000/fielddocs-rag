"""Factories for the LLMs, embeddings and the vector index.

Everything is created lazily and cached per process. In Lambda that means one
set of clients per warm container (created during the first request), which
keeps cold starts short and reuses HTTP connections across invocations.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, Any, Protocol

from botocore.config import Config
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.language_models.chat_models import BaseChatModel

from app.config import Settings, get_settings

if TYPE_CHECKING:
    from app.keyword_index import KeywordIndex

logger = logging.getLogger(__name__)

# Adaptive retries absorb Bedrock throttling (ThrottlingException) with backoff.
BOTO_CONFIG = Config(
    retries={"max_attempts": 6, "mode": "adaptive"},
    connect_timeout=5,
    read_timeout=60,
)


def _chat_model(settings: Settings, model_id: str, max_tokens: int) -> BaseChatModel:
    if settings.llm_provider == "fake":
        from app.fakes import ExtractiveFakeChatModel

        return ExtractiveFakeChatModel()

    from langchain_aws import ChatBedrockConverse

    kwargs: dict[str, Any] = {
        "model": model_id,
        "region_name": settings.aws_region,
        "temperature": settings.llm_temperature,
        "max_tokens": max_tokens,
        "config": BOTO_CONFIG,
    }
    if settings.bedrock_guardrail_id:
        kwargs["guardrails"] = {
            "guardrailIdentifier": settings.bedrock_guardrail_id,
            "guardrailVersion": settings.bedrock_guardrail_version,
            "trace": "enabled",
        }
    return ChatBedrockConverse(**kwargs)


@lru_cache
def get_llm() -> BaseChatModel:
    s = get_settings()
    return _chat_model(s, s.llm_model_id, s.llm_max_tokens)


@lru_cache
def get_utility_llm() -> BaseChatModel:
    s = get_settings()
    return _chat_model(s, s.utility_model_id, 300)


@lru_cache
def get_embeddings() -> Embeddings:
    s = get_settings()
    if s.llm_provider == "fake":
        from app.fakes import HashingEmbeddings

        return HashingEmbeddings(dimensions=s.embedding_dimensions)

    from langchain_aws import BedrockEmbeddings

    return BedrockEmbeddings(
        model_id=s.embedding_model_id,
        region_name=s.aws_region,
        dimensions=s.embedding_dimensions,
        model_kwargs={"normalize": True},
        config=BOTO_CONFIG,
    )


# ---------------------------------------------------------------------------
# Vector index: one small interface over Pinecone and the in-memory store so the
# rest of the code never branches on the backend.
# ---------------------------------------------------------------------------


class VectorIndex(Protocol):
    def upsert(self, documents: list[Document], ids: list[str]) -> None: ...

    def delete(self, ids: list[str]) -> None: ...

    def search(
        self, query: str, k: int, filters: dict[str, Any] | None = None
    ) -> list[tuple[Document, float]]: ...


def clean_metadata(meta: dict[str, Any]) -> dict[str, Any]:
    """Pinecone metadata accepts str/number/bool/list[str] only - no None."""
    out: dict[str, Any] = {}
    for key, value in meta.items():
        if value is None:
            continue
        if isinstance(value, (str, int, float, bool)):
            out[key] = value
        elif isinstance(value, (list, tuple)):
            out[key] = [str(v) for v in value]
        else:
            out[key] = str(value)
    return out


def pinecone_filter(filters: dict[str, Any] | None) -> dict[str, Any] | None:
    """{"doc_id": ["a", "b"]} -> {"doc_id": {"$in": ["a", "b"]}} (Pinecone filter syntax)."""
    if not filters:
        return None
    return {
        key: {"$in": list(val)} if isinstance(val, (list, tuple)) else {"$eq": val}
        for key, val in filters.items()
    }


@dataclass
class PineconeIndex:
    store: Any  # langchain_pinecone.PineconeVectorStore

    def upsert(self, documents: list[Document], ids: list[str]) -> None:
        if not documents:
            return
        self.store.add_texts(
            texts=[d.page_content for d in documents],
            metadatas=[clean_metadata(d.metadata) for d in documents],
            ids=ids,
            batch_size=64,
            # Pinecone's async mode uses a thread pool that is unreliable in
            # Lambda; synchronous upserts are simpler and fast enough here.
            async_req=False,
        )

    def delete(self, ids: list[str]) -> None:
        if ids:
            self.store.delete(ids=ids)

    def search(
        self, query: str, k: int, filters: dict[str, Any] | None = None
    ) -> list[tuple[Document, float]]:
        return self.store.similarity_search_with_score(query, k=k, filter=pinecone_filter(filters))


@dataclass
class MemoryIndex:
    store: Any  # langchain_core.vectorstores.InMemoryVectorStore

    def upsert(self, documents: list[Document], ids: list[str]) -> None:
        if documents:
            self.store.add_documents(documents, ids=ids)

    def delete(self, ids: list[str]) -> None:
        if ids:
            self.store.delete(ids=ids)

    def search(
        self, query: str, k: int, filters: dict[str, Any] | None = None
    ) -> list[tuple[Document, float]]:
        def _match(doc: Document) -> bool:
            for key, val in (filters or {}).items():
                allowed = val if isinstance(val, (list, tuple)) else [val]
                if doc.metadata.get(key) not in allowed:
                    return False
            return True

        return self.store.similarity_search_with_score(
            query, k=k, filter=_match if filters else None
        )


@lru_cache
def get_vector_index() -> VectorIndex:
    s = get_settings()
    embeddings = get_embeddings()
    if s.vector_store == "memory":
        from langchain_core.vectorstores import InMemoryVectorStore

        return MemoryIndex(InMemoryVectorStore(embedding=embeddings))

    from langchain_pinecone import PineconeVectorStore

    if s.pinecone_api_key is None:
        raise RuntimeError("PINECONE_API_KEY is not set (expected from SSM in AWS)")
    store = PineconeVectorStore(
        index_name=s.pinecone_index,
        embedding=embeddings,
        pinecone_api_key=s.pinecone_api_key.get_secret_value(),
        namespace=s.pinecone_namespace,
        text_key="text",
    )
    logger.info("pinecone index ready", extra={"index": s.pinecone_index})
    return PineconeIndex(store)


@lru_cache
def get_keyword_index() -> KeywordIndex | None:
    """Keyword half of hybrid search, or None when ENABLE_HYBRID_SEARCH=false."""
    s = get_settings()
    if not s.enable_hybrid_search:
        return None
    if s.vector_store == "memory":
        from app.keyword_index import MemoryKeywordIndex

        return MemoryKeywordIndex()

    from pinecone import Pinecone

    from app.keyword_index import PineconeSparseIndex

    if s.pinecone_api_key is None:
        raise RuntimeError("PINECONE_API_KEY is not set (expected from SSM in AWS)")
    client = Pinecone(api_key=s.pinecone_api_key.get_secret_value())
    logger.info("pinecone sparse index ready", extra={"index": s.pinecone_sparse_index})
    return PineconeSparseIndex(
        index=client.Index(s.pinecone_sparse_index), namespace=s.pinecone_namespace
    )


def reset_caches() -> None:
    """Used by tests to rebuild providers after changing settings."""
    from app.storage import get_store

    for fn in (
        get_settings,
        get_llm,
        get_utility_llm,
        get_embeddings,
        get_vector_index,
        get_keyword_index,
        get_store,
    ):
        fn.cache_clear()
