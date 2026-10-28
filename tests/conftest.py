"""Test configuration: everything runs offline (fake LLM, in-memory stores)."""

import os
from pathlib import Path

# Must be set before any `app.*` import reads settings.
os.environ.update(
    {
        "APP_ENV": "test",
        "LLM_PROVIDER": "fake",
        "VECTOR_STORE": "memory",
        "STORAGE_BACKEND": "memory",
        "AUTH_MODE": "none",
        "EMBEDDING_DIMENSIONS": "512",
        # Hashing embeddings give lower cosine scores than Titan.
        "MIN_RELEVANCE_SCORE": "0.18",
        "AWS_REGION": "us-east-1",
        "AWS_DEFAULT_REGION": "us-east-1",
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "LANGSMITH_TRACING": "false",
        "LOG_LEVEL": "WARNING",
    }
)
for key in ("SSM_PARAMETER_PREFIX", "DOCUMENTS_BUCKET", "LOCAL_SEED_DIR", "PINECONE_API_KEY"):
    os.environ.pop(key, None)

import pytest  # noqa: E402

SAMPLE_DOCS = Path(__file__).resolve().parent.parent / "sample_docs"


@pytest.fixture(autouse=True)
def fresh_state():
    """Rebuild settings, providers and stores for every test."""
    from app.providers import reset_caches

    reset_caches()
    try:
        from app.api import main

        main.get_rag_service.cache_clear()
    except ImportError:
        pass
    yield
    reset_caches()


@pytest.fixture
def seeded():
    from app.ingestion.cli import ingest_folder

    results = ingest_folder(SAMPLE_DOCS)
    assert results and all(r.status == "INDEXED" for r in results)
    return results


@pytest.fixture
def service():
    from app.api.main import get_rag_service

    return get_rag_service()
