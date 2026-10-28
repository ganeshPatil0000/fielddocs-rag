"""RAG orchestration: history -> rewrite -> cache -> retrieve -> generate -> validate.

Cost controls built into the flow:
* no relevant chunks  -> refuse without calling the LLM at all;
* exact-repeat questions (same corpus version) are served from cache;
* follow-up rewriting and the optional grounding judge use the cheaper
  utility model; the answer model only runs once per question;
* context is capped at MAX_CONTEXT_CHARS, output at LLM_MAX_TOKENS.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langsmith import traceable
from langsmith.run_helpers import get_current_run_tree

from app.config import Settings
from app.observability import emit_metrics
from app.rag.prompts import NO_ANSWER, answer_prompt, condense_prompt, grounding_prompt
from app.rag.retriever import RetrievedChunk, Retriever, format_context
from app.rag.validation import parse_grounding_verdict, validate_citations
from app.schemas import ChatResponse, Source, Usage
from app.storage import Store

logger = logging.getLogger(__name__)

# USD per 1M tokens (input, output). Used for the EstimatedCostUSD metric only;
# check https://aws.amazon.com/bedrock/pricing/ for your region.
PRICES_PER_MTOK: dict[str, tuple[float, float]] = {
    "nova-2-lite": (0.30, 2.50),
    "nova-micro": (0.035, 0.14),
    "nova-lite": (0.06, 0.24),
    "nova-pro": (0.80, 3.20),
    "llama3-1-8b": (0.22, 0.22),
}


def _price(model_id: str) -> tuple[float, float]:
    for key, price in PRICES_PER_MTOK.items():
        if key in model_id:
            return price
    return (0.0, 0.0)


def _normalise(text: str) -> str:
    return " ".join(text.lower().split()).rstrip("?.! ")


class RagService:
    def __init__(
        self,
        settings: Settings,
        llm: BaseChatModel,
        utility_llm: BaseChatModel,
        retriever: Retriever,
        store: Store,
    ) -> None:
        self.settings = settings
        self.llm = llm
        self.utility_llm = utility_llm
        self.retriever = retriever
        self.store = store

    # -- helpers ------------------------------------------------------------
    def _track(self, usage: Usage, message: Any, model_id: str) -> None:
        meta = getattr(message, "usage_metadata", None) or {}
        tin, tout = int(meta.get("input_tokens", 0)), int(meta.get("output_tokens", 0))
        pin, pout = _price(model_id)
        usage.input_tokens += tin
        usage.output_tokens += tout
        usage.llm_calls += 1
        usage.estimated_cost_usd = round(
            usage.estimated_cost_usd + tin * pin / 1e6 + tout * pout / 1e6, 8
        )

    def _condense(self, question: str, history: list[tuple[str, str]], usage: Usage) -> str:
        transcript = "\n".join(f"Engineer: {q}\nAssistant: {a[:400]}" for q, a in history)
        messages = condense_prompt.format_messages(history=transcript, question=question)
        result = self.utility_llm.invoke(messages)
        self._track(usage, result, self.settings.utility_model_id)
        rewritten = str(result.content).strip().strip('"')
        return rewritten if 3 <= len(rewritten) <= 500 else question

    def _cache_key(self, question: str, doc_ids: list[str] | None) -> str:
        payload = {
            "q": _normalise(question),
            "docs": sorted(doc_ids or []),
            "v": self.store.get_corpus_version(),
            "m": self.settings.llm_model_id,
            "k": self.settings.retrieval_k,
            "t": self.settings.min_relevance_score,
            "h": self.retriever.hybrid,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    @staticmethod
    def _sources(chunks: list[RetrievedChunk], cited: list[int]) -> list[Source]:
        out = []
        for i, chunk in enumerate(chunks, start=1):
            meta = chunk.doc.metadata
            text = chunk.doc.page_content.split("\n", 1)[-1]
            out.append(
                Source(
                    index=i,
                    doc_id=chunk.doc_id,
                    source=str(meta.get("source", "unknown")),
                    page=int(meta["page"]) if meta.get("page") else None,
                    section=meta.get("section") or None,
                    score=round(chunk.score, 4),
                    match=chunk.match,
                    snippet=text[:300],
                    cited=i in cited,
                )
            )
        return out

    # -- main entrypoint ----------------------------------------------------
    @traceable(
        name="rag_answer",
        run_type="chain",
        process_inputs=lambda inputs: {k: v for k, v in inputs.items() if k != "self"},
    )
    def answer(
        self,
        question: str,
        user: str,
        session_id: str | None = None,
        doc_ids: list[str] | None = None,
    ) -> ChatResponse:
        started = time.perf_counter()
        run = get_current_run_tree()
        trace_id = str(run.id) if run is not None else str(uuid.uuid4())
        usage = Usage()
        s = self.settings

        history = self.store.get_history(user, session_id, s.history_turns) if session_id else []
        standalone = self._condense(question, history, usage) if history else question

        cache_key = self._cache_key(standalone, doc_ids)
        cached = self.store.cache_get(cache_key)
        if cached:
            response = ChatResponse(**{**cached, "trace_id": trace_id, "cached": True})
            response.session_id = session_id
            response.usage = usage
            self._finish(response, user, session_id, question, started, cache_key=None)
            return response

        chunks = self.retriever.retrieve(standalone, doc_ids=doc_ids)
        if not chunks:
            response = ChatResponse(
                answer=NO_ANSWER,
                sources=[],
                grounded=True,
                refused=True,
                trace_id=trace_id,
                session_id=session_id,
                usage=usage,
            )
            self._finish(response, user, session_id, question, started, cache_key=None)
            return response

        context = format_context(chunks)
        messages = answer_prompt.format_messages(context=context, question=standalone)
        result = self.llm.invoke(messages)
        self._track(usage, result, s.llm_model_id)

        checked = validate_citations(str(result.content), len(chunks), strict=s.strict_grounding)
        grounded, answer = checked.grounded, checked.answer

        if s.enable_grounding_check and not checked.is_refusal:
            verdict = self.utility_llm.invoke(
                grounding_prompt.format_messages(context=context, answer=answer)
            )
            self._track(usage, verdict, s.utility_model_id)
            grounded, reason = parse_grounding_verdict(str(verdict.content))
            if not grounded:
                logger.warning("grounding check failed", extra={"reason": reason})
                if s.strict_grounding:
                    answer = NO_ANSWER

        refused = answer == NO_ANSWER
        response = ChatResponse(
            answer=answer,
            sources=[] if refused else self._sources(chunks, checked.cited),
            grounded=grounded,
            refused=refused,
            trace_id=trace_id,
            session_id=session_id,
            usage=usage,
        )
        # Only cache answers we would be happy to serve again.
        self._finish(
            response,
            user,
            session_id,
            question,
            started,
            cache_key=cache_key if (grounded and not refused) else None,
        )
        return response

    def _finish(
        self,
        response: ChatResponse,
        user: str,
        session_id: str | None,
        question: str,
        started: float,
        cache_key: str | None,
    ) -> None:
        response.latency_ms = int((time.perf_counter() - started) * 1000)
        if session_id:
            self.store.append_turn(user, session_id, question, response.answer)
        if cache_key:
            payload = response.model_dump(exclude={"trace_id", "session_id", "usage", "cached"})
            self.store.cache_put(cache_key, payload, self.settings.cache_ttl_seconds)
        emit_metrics(
            self.settings.metrics_namespace,
            {
                "Questions": 1,
                "Refusals": int(response.refused),
                "Ungrounded": int(not response.grounded),
                "CacheHits": int(response.cached),
                "InputTokens": response.usage.input_tokens,
                "OutputTokens": response.usage.output_tokens,
                "EstimatedCostUSD": response.usage.estimated_cost_usd,
                "LatencyMs": response.latency_ms,
            },
            {"Service": "api", "Environment": self.settings.app_env},
            unit_overrides={"LatencyMs": "Milliseconds", "EstimatedCostUSD": "None"},
        )
        logger.info(
            "answered",
            extra={
                "trace_id": response.trace_id,
                "refused": response.refused,
                "grounded": response.grounded,
                "cached": response.cached,
                "sources": len(response.sources),
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
                "latency_ms": response.latency_ms,
            },
        )
