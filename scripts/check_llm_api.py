"""Check the configured LLM provider end to end before ingesting (costs a fraction of a cent).

    python scripts/check_llm_api.py

It reads .env exactly like the app does and uses the app's own model factories,
so a pass here means the API, the chat models and the embeddings will work:

1. the answer model and the helper model each reply to a tiny prompt,
2. the embedding model returns vectors of EMBEDDING_DIMENSIONS (the Pinecone
   index size, 1024), for one text and for a small batch.

The API key is never printed (only its length).
"""

from __future__ import annotations

import math
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.providers import get_embeddings, get_llm, get_utility_llm, reset_caches  # noqa: E402


def short_error(error: Exception) -> str:
    text = " ".join(str(error).split())
    return f"{type(error).__name__}: {text[:300]}"


def check_chat(label: str, model_id: str, factory: Any) -> bool:
    start = time.perf_counter()
    try:
        reply = factory().invoke("Reply with the single word OK.")
    except Exception as error:  # noqa: BLE001 - report every failure the same way
        print(f"  FAIL {label} ({model_id}): {short_error(error)}")
        return False
    seconds = time.perf_counter() - start
    usage = getattr(reply, "usage_metadata", None) or {}
    text = str(reply.content).strip().replace("\n", " ")[:40]
    print(
        f"  OK   {label} ({model_id}): {text!r} in {seconds:.1f}s, "
        f"tokens in/out {usage.get('input_tokens', '?')}/{usage.get('output_tokens', '?')}"
    )
    if not usage:
        print("       note: no token usage returned, so cost metrics will show 0")
    return True


def check_embeddings(expected: int, model_id: str) -> bool:
    try:
        single = get_embeddings().embed_query("ONT received power")
        batch = get_embeddings().embed_documents(["MAINS FAIL alarm", "loop resistance"])
    except Exception as error:  # noqa: BLE001
        print(f"  FAIL embeddings ({model_id}): {short_error(error)}")
        if "dimension" in str(error).lower() and get_settings().openai_send_dimensions:
            print("       try adding OPENAI_SEND_DIMENSIONS=false to .env and run again")
        return False
    norm = math.sqrt(sum(x * x for x in single))
    sizes = {len(single), *(len(v) for v in batch)}
    if sizes != {expected}:
        print(f"  FAIL embeddings ({model_id}): got sizes {sorted(sizes)}, need {expected}")
        print("       the Pinecone index is 1024-d, so these vectors cannot be stored")
        return False
    print(f"  OK   embeddings ({model_id}): {expected} numbers per text, length {norm:.3f}")
    return True


def main() -> int:
    reset_caches()
    s = get_settings()
    print(f"provider: {s.llm_provider}")
    if s.llm_provider == "openai":
        key = s.openai_api_key.get_secret_value() if s.openai_api_key else ""
        print(f"base url: {s.openai_base_url}")
        print(f"api key:  {'missing' if not key else f'set ({len(key)} characters)'}")
    elif s.llm_provider == "bedrock":
        print(f"region:   {s.aws_region} (for Bedrock, scripts/check_bedrock.py says more)")
    else:
        print("the fake provider needs no checks")
        return 0

    results = [
        check_chat("answer model", s.llm_model_id, get_llm),
        check_chat("helper model", s.utility_model_id, get_utility_llm),
        check_embeddings(s.embedding_dimensions, s.embedding_model_id),
    ]
    if all(results):
        print("All good: you can ingest documents and ask questions.")
        return 0
    print("Fix the FAIL lines above before ingesting documents.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
