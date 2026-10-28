"""Offline stand-ins for Bedrock, used by tests and `LLM_PROVIDER=fake` demos.

They are deliberately *behavioural* fakes, not mocks: the hashing embedder gives
real lexical similarity (so retrieval tests mean something) and the extractive
chat model answers from the provided sources with a citation, the same contract
the real model is prompted to follow.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Any

from langchain_core.embeddings import Embeddings
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

_WORD = re.compile(r"[a-z0-9]+")
_STOP = {
    "a",
    "an",
    "the",
    "is",
    "are",
    "was",
    "to",
    "of",
    "and",
    "or",
    "in",
    "on",
    "for",
    "with",
    "what",
    "how",
    "do",
    "i",
    "if",
    "it",
    "be",
    "by",
    "at",
    "as",
    "this",
    "that",
    "should",
    "can",
    "my",
    "when",
    "which",
    "from",
    "you",
    "your",
    "does",
    "there",
}


def _tokens(text: str) -> list[str]:
    return [t for t in _WORD.findall(text.lower()) if t not in _STOP and len(t) > 1]


class HashingEmbeddings(Embeddings):
    """Deterministic bag-of-words embedding (feature hashing + L2 norm)."""

    def __init__(self, dimensions: int = 512) -> None:
        self.dimensions = dimensions

    def _embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dimensions
        for tok in _tokens(text):
            digest = hashlib.md5(tok.encode(), usedforsecurity=False).digest()
            idx = int.from_bytes(digest[:4], "big") % self.dimensions
            vec[idx] += 1.0 if digest[4] % 2 == 0 else -1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


_SOURCE_BLOCK = re.compile(r"\[(\d+)\][^\n]*\n(.*?)(?=\n\[\d+\]|\Z)", re.S)


class ExtractiveFakeChatModel(BaseChatModel):
    """Answers by extracting the best-matching sentence from source [n]."""

    @property
    def _llm_type(self) -> str:
        return "extractive-fake"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        system = " ".join(str(m.content) for m in messages if m.type == "system")
        human = str(messages[-1].content)
        if "TASK: grounding check" in system:
            text = '{"grounded": true, "reason": "fake model"}'
        elif "TASK: rewrite a follow-up" in system:
            match = re.search(r"Follow-up question:\s*(.+?)\n", human, re.S)
            text = match.group(1).strip() if match else human
        else:
            text = self._answer(human)
        tokens_in = sum(len(str(m.content)) for m in messages) // 4
        message = AIMessage(
            content=text,
            usage_metadata={
                "input_tokens": tokens_in,
                "output_tokens": len(text) // 4,
                "total_tokens": tokens_in + len(text) // 4,
            },
        )
        return ChatResult(generations=[ChatGeneration(message=message)])

    @staticmethod
    def _answer(human: str) -> str:
        from app.rag.prompts import NO_ANSWER

        context = human.split("<sources>", 1)[-1].split("</sources>", 1)[0]
        question = human.rsplit("Question:", 1)[-1]
        q_tokens = set(_tokens(question))
        best: tuple[float, str, str] = (0.0, "", "")
        for num, body in _SOURCE_BLOCK.findall(context):
            for sentence in re.split(r"(?<=[.!?])\s+|\n+", body):
                overlap = len(q_tokens & set(_tokens(sentence)))
                if overlap > best[0]:
                    best = (overlap, num, sentence.strip(" -*#"))
        if best[0] == 0:
            return NO_ANSWER
        return f"{best[2]} [{best[1]}]"
