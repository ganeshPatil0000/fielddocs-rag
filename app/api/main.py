"""FastAPI application (served by Lambda via Mangum, or uvicorn locally)."""

from __future__ import annotations

import logging
import re
import time
import uuid
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.api.auth import Principal, get_principal, require_admin
from app.config import get_settings
from app.ingestion.loaders import extension
from app.ingestion.pipeline import delete_document_vectors, ingest_bytes
from app.observability import configure_logging, emit_metrics, flush_traces, request_id_var
from app.providers import (
    BOTO_CONFIG,
    get_keyword_index,
    get_llm,
    get_utility_llm,
    get_vector_index,
)
from app.rag.retriever import Retriever
from app.rag.service import RagService
from app.schemas import (
    ChatRequest,
    ChatResponse,
    DocumentOut,
    FeedbackRequest,
    UploadUrlRequest,
    UploadUrlResponse,
)
from app.storage import DocStatus, get_store

logger = logging.getLogger(__name__)
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")
_UUID = re.compile(r"^[0-9a-fA-F-]{32,36}$")


@lru_cache
def get_rag_service() -> RagService:
    settings = get_settings()
    return RagService(
        settings=settings,
        llm=get_llm(),
        utility_llm=get_utility_llm(),
        retriever=Retriever(get_vector_index(), settings, get_keyword_index()),
        store=get_store(),
    )


@lru_cache
def _s3() -> Any:
    import boto3

    return boto3.client("s3", config=BOTO_CONFIG)


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ANN201
    settings = get_settings()
    if settings.local_seed_dir and not settings.is_deployed:
        # In-memory mode has nothing indexed at startup; seed it from a folder.
        from app.ingestion.cli import ingest_folder

        results = await run_in_threadpool(ingest_folder, Path(settings.local_seed_dir))
        logger.info("seeded local documents", extra={"count": len(results)})
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    app = FastAPI(
        title="FieldDocs RAG API",
        version=__version__,
        lifespan=lifespan,
        # Interactive docs only outside prod.
        docs_url=None if settings.app_env == "prod" else "/docs",
        redoc_url=None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):  # noqa: ANN001, ANN202
        event = request.scope.get("aws.event") or {}
        rid = (
            event.get("requestContext", {}).get("requestId")
            or request.headers.get("x-request-id")
            or uuid.uuid4().hex
        )
        request_id_var.set(rid)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            flush_traces()
        response.headers["x-request-id"] = rid
        logger.info(
            "request",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "latency_ms": int((time.perf_counter() - started) * 1000),
            },
        )
        return response

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error")
        emit_metrics(
            settings.metrics_namespace,
            {"UnhandledErrors": 1},
            {"Service": "api", "Environment": settings.app_env},
        )
        # Never leak stack traces or internals to clients.
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal error", "request_id": request_id_var.get()},
        )

    # -- routes -------------------------------------------------------------
    @app.get("/health", tags=["ops"])
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__, "env": settings.app_env}

    @app.post("/v1/chat", response_model=ChatResponse, tags=["chat"])
    def chat(body: ChatRequest, principal: Principal = Depends(get_principal)) -> ChatResponse:
        if len(body.question) > settings.max_question_chars:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Question too long")
        return get_rag_service().answer(
            body.question.strip(),
            user=principal.sub,
            session_id=body.session_id,
            doc_ids=body.doc_ids,
        )

    @app.post("/v1/feedback", status_code=202, tags=["chat"])
    def feedback(body: FeedbackRequest, principal: Principal = Depends(get_principal)) -> dict:
        logger.info("feedback", extra={"trace_id": body.trace_id, "score": body.score})
        emit_metrics(
            settings.metrics_namespace,
            {"FeedbackPositive": body.score, "FeedbackNegative": 1 - body.score},
            {"Service": "api", "Environment": settings.app_env},
        )
        if settings.langsmith_tracing and _UUID.match(body.trace_id):
            try:
                from langsmith.run_trees import get_cached_client

                get_cached_client().create_feedback(
                    run_id=body.trace_id,
                    key="user_score",
                    score=body.score,
                    comment=body.comment,
                    source_info={"user": principal.sub},
                )
            except Exception:  # noqa: BLE001 - feedback must not fail the request
                logger.warning("langsmith feedback failed", exc_info=True)
        return {"status": "accepted"}

    @app.post("/v1/documents/upload-url", response_model=UploadUrlResponse, tags=["documents"])
    def upload_url(
        body: UploadUrlRequest, principal: Principal = Depends(require_admin)
    ) -> UploadUrlResponse:
        if not settings.documents_bucket:
            raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "No documents bucket configured")
        if extension(body.filename) not in settings.allowed_extensions:
            raise HTTPException(
                status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                f"Allowed types: {', '.join(settings.allowed_extensions)}",
            )
        doc_id = uuid.uuid4().hex[:16]
        filename = _SAFE_NAME.sub("_", body.filename)[-150:]
        key = f"{settings.raw_prefix}{doc_id}/{filename}"
        expires = 900
        # A presigned POST (not PUT) lets S3 itself enforce the size limit.
        post = _s3().generate_presigned_post(
            Bucket=settings.documents_bucket,
            Key=key,
            Fields={"Content-Type": body.content_type, "x-amz-meta-uploaded-by": principal.sub},
            Conditions=[
                ["content-length-range", 1, settings.max_upload_mb * 1024 * 1024],
                {"Content-Type": body.content_type},
                {"x-amz-meta-uploaded-by": principal.sub},
            ],
            ExpiresIn=expires,
        )
        get_store().put_document(
            {
                "doc_id": doc_id,
                "filename": filename,
                "s3_key": key,
                "uploaded_by": principal.sub,
                "status": DocStatus.PENDING,
                "chunk_count": 0,
            }
        )
        return UploadUrlResponse(
            doc_id=doc_id, key=key, url=post["url"], fields=post["fields"], expires_in=expires
        )

    @app.get("/v1/documents", response_model=list[DocumentOut], tags=["documents"])
    def list_documents(_: Principal = Depends(get_principal)) -> list[DocumentOut]:
        docs = sorted(get_store().list_documents(), key=lambda d: d.get("created_at", 0))
        return [DocumentOut(**d) for d in docs]

    @app.get("/v1/documents/{doc_id}", response_model=DocumentOut, tags=["documents"])
    def get_document(doc_id: str, _: Principal = Depends(get_principal)) -> DocumentOut:
        doc = get_store().get_document(doc_id)
        if not doc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
        return DocumentOut(**doc)

    @app.delete("/v1/documents/{doc_id}", status_code=204, tags=["documents"])
    def delete_document(doc_id: str, _: Principal = Depends(require_admin)) -> None:
        store = get_store()
        doc = store.get_document(doc_id)
        if not doc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
        delete_document_vectors(doc_id)
        if doc.get("s3_key") and settings.documents_bucket:
            _s3().delete_object(Bucket=settings.documents_bucket, Key=doc["s3_key"])
        store.delete_document(doc_id)
        logger.info("document deleted", extra={"doc_id": doc_id})

    if not settings.is_deployed:
        # Local convenience: ingest a file synchronously without S3/SQS.
        @app.post("/v1/documents/upload", response_model=DocumentOut, tags=["documents"])
        async def upload_local(
            file: UploadFile = File(...), principal: Principal = Depends(require_admin)
        ) -> DocumentOut:
            name = _SAFE_NAME.sub("_", file.filename or "upload.txt")
            if extension(name) not in settings.allowed_extensions:
                raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Unsupported type")
            data = await file.read()
            doc_id = uuid.uuid4().hex[:16]
            await run_in_threadpool(ingest_bytes, data, name, doc_id, None, principal.sub)
            doc = get_store().get_document(doc_id) or {"doc_id": doc_id, "status": "UNKNOWN"}
            return DocumentOut(**doc)

    return app


app = create_app()
