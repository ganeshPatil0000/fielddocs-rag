"""Typed application settings.

Every knob is an environment variable so the same container image runs locally,
in tests, and in each AWS environment. Secrets (Pinecone / LangSmith keys) are
injected from SSM Parameter Store at cold start - see ``app.ssm``.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    # --- runtime -------------------------------------------------------------
    app_name: str = "fielddocs-rag"
    app_env: Literal["local", "test", "dev", "staging", "prod"] = "local"
    log_level: str = "INFO"
    aws_region: str = Field(default="ap-southeast-2", validation_alias="AWS_REGION")

    # --- providers -----------------------------------------------------------
    # "bedrock" talks to AWS; "fake" is fully offline (tests, laptop demos).
    llm_provider: Literal["bedrock", "fake"] = "bedrock"
    # Global inference profile: Nova 2 Lite has no in-region on-demand option in Sydney.
    llm_model_id: str = "global.amazon.nova-2-lite-v1:0"
    # Cheaper model for helper calls (question rewriting, grounding judge).
    utility_model_id: str = "amazon.nova-micro-v1:0"
    llm_temperature: float = 0.0
    llm_max_tokens: int = 700
    embedding_model_id: str = "amazon.titan-embed-text-v2:0"
    embedding_dimensions: int = 1024
    bedrock_guardrail_id: str | None = None
    bedrock_guardrail_version: str = "DRAFT"

    # --- vector store --------------------------------------------------------
    vector_store: Literal["pinecone", "memory"] = "pinecone"
    pinecone_api_key: SecretStr | None = None
    pinecone_index: str = "fielddocs"
    pinecone_namespace: str = "default"

    # --- hybrid search (semantic + keyword) ---------------------------------
    # Keyword search uses a second, sparse Pinecone index (same ids/namespace).
    enable_hybrid_search: bool = True
    pinecone_sparse_index: str = "fielddocs-sparse"
    # Reciprocal Rank Fusion: score = sum(weight / (rrf_k + rank)) over both lists.
    hybrid_rrf_k: int = Field(default=60, ge=1, le=1000)
    hybrid_keyword_weight: float = Field(default=1.0, ge=0.0, le=5.0)
    # A chunk found ONLY by keyword search must contain at least this share of
    # the question's keywords; stops one shared word ("router") pulling in noise.
    hybrid_min_keyword_coverage: float = Field(default=0.5, ge=0.0, le=1.0)

    # --- storage (registry, chat history, cache) -----------------------------
    storage_backend: Literal["dynamodb", "memory"] = "dynamodb"
    dynamodb_table: str = "fielddocs-dev"
    documents_bucket: str | None = None
    raw_prefix: str = "raw/"

    # --- retrieval & generation ---------------------------------------------
    retrieval_k: int = Field(default=5, ge=1, le=20)
    retrieval_fetch_k: int = Field(default=12, ge=1, le=50)
    min_relevance_score: float = Field(default=0.35, ge=-1.0, le=1.0)
    max_context_chars: int = 9000
    enable_rerank: bool = False
    rerank_model_arn: str | None = None
    enable_grounding_check: bool = False
    strict_grounding: bool = True
    history_turns: int = Field(default=3, ge=0, le=10)
    history_ttl_seconds: int = 60 * 60 * 24
    cache_ttl_seconds: int = 60 * 60

    # --- ingestion -----------------------------------------------------------
    chunk_size: int = 1000
    chunk_overlap: int = 150
    max_upload_mb: int = 25
    allowed_extensions: tuple[str, ...] = (".pdf", ".docx", ".txt", ".md")

    # --- api / security ------------------------------------------------------
    auth_mode: Literal["cognito", "none"] = "cognito"
    admin_group: str = "admin"
    max_question_chars: int = 2000
    cors_origins: list[str] = ["*"]
    local_seed_dir: str | None = None

    # --- observability -------------------------------------------------------
    langsmith_tracing: bool = Field(default=False, validation_alias="LANGSMITH_TRACING")
    metrics_namespace: str = "FieldDocsRAG"

    @model_validator(mode="after")
    def _validate(self) -> Settings:
        if self.retrieval_fetch_k < self.retrieval_k:
            self.retrieval_fetch_k = self.retrieval_k
        if self.app_env in {"dev", "staging", "prod"}:
            if self.auth_mode == "none":
                raise ValueError("AUTH_MODE=none is only allowed for local/test environments")
            if self.llm_provider == "fake" or self.vector_store == "memory":
                raise ValueError(
                    "Fake/in-memory providers are not allowed in deployed environments"
                )
        if self.enable_rerank and not self.rerank_model_arn:
            raise ValueError("ENABLE_RERANK=true requires RERANK_MODEL_ARN")
        return self

    @property
    def is_deployed(self) -> bool:
        return self.app_env in {"dev", "staging", "prod"}


@lru_cache
def get_settings() -> Settings:
    # Export .env into os.environ too, so libraries that read the environment
    # directly (LangSmith, boto3) see the same values locally. Real environment
    # variables always win; in Lambda there is no .env file.
    from dotenv import load_dotenv

    load_dotenv(override=False)
    return Settings()
