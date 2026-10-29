"""Request / response models shared by the API and the RAG service."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field

SESSION_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"
DOC_ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"


class ChatRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    session_id: str | None = Field(default=None, pattern=SESSION_PATTERN)
    doc_ids: list[Annotated[str, Field(pattern=DOC_ID_PATTERN)]] | None = Field(
        default=None, max_length=20, description="Optional: only search these documents"
    )


class Source(BaseModel):
    index: int
    doc_id: str
    source: str
    page: int | None = None
    section: str | None = None
    score: float = Field(
        description="Cosine similarity, or the fused rank score when hybrid search is on"
    )
    match: str = Field(
        default="semantic", description="Which search found it: semantic, keyword or both"
    )
    snippet: str
    cited: bool = False


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    llm_calls: int = 0
    estimated_cost_usd: float = 0.0


class ChatResponse(BaseModel):
    answer: str
    sources: list[Source]
    grounded: bool
    refused: bool
    cached: bool = False
    trace_id: str
    session_id: str | None = None
    usage: Usage = Field(default_factory=Usage)
    latency_ms: int = 0


class UploadUrlRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=200)
    content_type: str = Field(default="application/octet-stream", max_length=100)
    sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
        description="SHA-256 of the file, so an exact duplicate is refused before upload "
        "(ingestion checks the real bytes again)",
    )


class UploadUrlResponse(BaseModel):
    doc_id: str
    key: str
    url: str
    fields: dict[str, str]
    expires_in: int


class DocumentOut(BaseModel):
    doc_id: str
    filename: str | None = None
    status: str
    chunk_count: int = 0
    page_count: int | None = None
    uploaded_by: str | None = None
    error: str | None = None
    created_at: int | None = None
    updated_at: int | None = None
    # Version answers currently use; goes up by one each time the file is replaced.
    version: int = 1
    # Progress of a replacement: PENDING / PROCESSING / FAILED, or empty when none.
    update_status: str | None = None
    update_error: str | None = None
    pending_filename: str | None = None
    # For DUPLICATE documents: the document that already has this content.
    duplicate_of: str | None = None


class FeedbackRequest(BaseModel):
    trace_id: str = Field(min_length=8, max_length=64)
    score: int = Field(ge=0, le=1, description="1 = helpful, 0 = not helpful")
    comment: str | None = Field(default=None, max_length=1000)
