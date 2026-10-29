"""LLM_PROVIDER=openai (any OpenAI-compatible API, e.g. AIRouter), tested offline.

HTTP is intercepted at the HTTP transport, so these tests check the real requests
the app sends (URL, model, token limit, dimensions) without a network or a key.
"""

from __future__ import annotations

import importlib
import json
from typing import Any

import pytest
from pydantic import ValidationError

from app import providers
from app.config import Settings

BASE_URL = "https://router.example.test/v1"


def openai_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "app_env": "test",
        "llm_provider": "openai",
        "openai_base_url": BASE_URL,
        "openai_api_key": "test-key-not-real",
        "llm_model_id": "amazon/nova-2-lite",
        "utility_model_id": "amazon/nova-micro",
        "embedding_model_id": "amazon/titan-embed-text-v2",
        "embedding_dimensions": 4,
        "vector_store": "memory",
        "storage_backend": "memory",
        "auth_mode": "none",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # ignore the developer's .env


@pytest.fixture
def use_settings(monkeypatch):
    def apply(settings: Settings) -> Settings:
        monkeypatch.setattr(providers, "get_settings", lambda: settings)
        providers.get_llm.cache_clear()
        providers.get_utility_llm.cache_clear()
        providers.get_embeddings.cache_clear()
        return settings

    return apply


@pytest.fixture
def router(monkeypatch):
    """Fake OpenAI-compatible server. Returns the list of (path, json body) it received."""
    calls: list[tuple[str, dict[str, Any]]] = []

    def reply(status: int, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        return status, payload

    def answer(request: Any) -> tuple[int, dict[str, Any]]:
        body = json.loads(request.read() or b"{}")
        calls.append((request.url.path, body))
        assert request.headers["authorization"] == "Bearer test-key-not-real"
        if request.url.path.endswith("/chat/completions"):
            return reply(
                200,
                {
                    "id": "chatcmpl-1",
                    "object": "chat.completion",
                    "created": 0,
                    "model": body["model"],
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "OK"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 12, "completion_tokens": 1, "total_tokens": 13},
                },
            )
        if request.url.path.endswith("/embeddings"):
            size = body.get("dimensions", 4)
            data = [
                {"object": "embedding", "index": i, "embedding": [0.5] * size}
                for i, _ in enumerate(body["input"])
            ]
            return reply(
                200,
                {
                    "object": "list",
                    "data": data,
                    "model": body["model"],
                    "usage": {"prompt_tokens": 3, "total_tokens": 3},
                },
            )
        return reply(404, {"error": {"message": "not found"}})

    def patch(module: Any) -> None:
        def handle_request(self: Any, request: Any) -> Any:
            status, payload = answer(request)
            return module.Response(
                status,
                content=json.dumps(payload).encode(),
                headers={"content-type": "application/json"},
            )

        monkeypatch.setattr(module.HTTPTransport, "handle_request", handle_request)

    # The OpenAI SDK sends requests with httpx (older versions) or httpx2 (newer).
    patched = 0
    for name in ("httpx", "httpx2"):
        try:
            patch(importlib.import_module(name))
            patched += 1
        except (ImportError, AttributeError):
            continue
    assert patched, "neither httpx nor httpx2 is installed"
    return calls


def test_openai_provider_needs_a_base_url():
    with pytest.raises(ValidationError, match="OPENAI_BASE_URL"):
        openai_settings(openai_base_url=None)


def test_openai_provider_is_allowed_in_deployed_environments():
    s = openai_settings(app_env="dev", auth_mode="cognito", vector_store="pinecone")
    assert s.llm_provider == "openai"


def test_missing_api_key_fails_with_a_clear_message(use_settings):
    use_settings(openai_settings(openai_api_key=None))
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        providers.get_llm()


def test_chat_request_goes_to_the_router_with_model_and_token_limit(use_settings, router):
    use_settings(openai_settings(llm_max_tokens=700))

    reply = providers.get_llm().invoke("Reply with OK")

    path, body = router[-1]
    assert path == "/v1/chat/completions"
    assert body["model"] == "amazon/nova-2-lite"
    assert body["temperature"] == 0.0
    assert body["max_completion_tokens"] == 700
    assert reply.content == "OK"
    # Token usage feeds the cost metrics in RagService._track.
    assert reply.usage_metadata["input_tokens"] == 12
    assert reply.usage_metadata["output_tokens"] == 1


def test_helper_model_uses_the_utility_model_id(use_settings, router):
    use_settings(openai_settings())
    providers.get_utility_llm().invoke("Rewrite this")
    assert router[-1][1]["model"] == "amazon/nova-micro"
    assert router[-1][1]["max_completion_tokens"] == 300


def test_embeddings_send_text_dimensions_and_float_format(use_settings, router):
    use_settings(openai_settings())

    vectors = providers.get_embeddings().embed_documents(["MAINS FAIL", "loop resistance"])

    path, body = router[-1]
    assert path == "/v1/embeddings"
    assert body["model"] == "amazon/titan-embed-text-v2"
    assert body["input"] == ["MAINS FAIL", "loop resistance"]  # text, not token ids
    assert body["dimensions"] == 4
    assert body["encoding_format"] == "float"
    assert [len(v) for v in vectors] == [4, 4]


def test_dimensions_can_be_left_out_for_routers_that_reject_it(use_settings, router):
    use_settings(openai_settings(openai_send_dimensions=False))
    providers.get_embeddings().embed_query("ONT power")
    assert "dimensions" not in router[-1][1]


def test_embedding_batches_are_split(use_settings, router):
    use_settings(openai_settings())
    providers.get_embeddings().embed_documents([f"chunk {i}" for i in range(130)])
    batch_sizes = [len(body["input"]) for path, body in router if path.endswith("/embeddings")]
    assert batch_sizes == [64, 64, 2]
