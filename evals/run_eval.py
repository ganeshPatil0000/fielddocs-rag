"""Offline evaluation against a golden question set.

Measures what matters for a grounded RAG system:

* retrieval_hit   - the expected document is among the retrieved sources
* citation_hit    - the expected document is actually cited in the answer
* keyword_recall  - the answer contains the key fact(s) (e.g. "-27 dBm")
* refusal_correct - unanswerable questions are refused, answerable ones are not
* cost / latency  - tokens and estimated USD per question

Usage:
    # In-process against whatever the environment points at (fake or Bedrock+Pinecone)
    python -m evals.run_eval --seed sample_docs

    # Against a deployed API
    python -m evals.run_eval --url https://<api-id>.execute-api.<region>.amazonaws.com \
        --token $TOKEN

    # Also record the run as a LangSmith experiment (needs LANGSMITH_API_KEY)
    python -m evals.run_eval --seed sample_docs --langsmith

Exits non-zero when a metric falls below its threshold, so CI can gate deploys.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
THRESHOLDS = {
    "retrieval_hit": 0.85,
    "citation_hit": 0.75,
    "keyword_recall": 0.7,
    "refusal_correct": 0.9,
}


def load_golden(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def local_target(seed: Path | None) -> Callable[[str], dict[str, Any]]:
    from app.api.main import get_rag_service

    if seed:
        from app.ingestion.cli import ingest_folder

        ingest_folder(seed)
    service = get_rag_service()
    return lambda q: service.answer(q, user="eval").model_dump()


def http_target(url: str, token: str | None) -> Callable[[str], dict[str, Any]]:
    import httpx

    headers = {"Authorization": f"Bearer {token}"} if token else {}
    client = httpx.Client(base_url=url.rstrip("/"), headers=headers, timeout=60)

    def call(q: str) -> dict[str, Any]:
        resp = client.post("/v1/chat", json={"question": q})
        resp.raise_for_status()
        return resp.json()

    return call


def score(item: dict[str, Any], out: dict[str, Any]) -> dict[str, float | None]:
    answerable = item.get("answerable", True)
    sources = {s["source"] for s in out.get("sources", [])}
    cited = {s["source"] for s in out.get("sources", []) if s.get("cited")}
    answer = out.get("answer", "").lower()
    result: dict[str, float | None] = {
        "refusal_correct": float(out.get("refused") is (not answerable)),
        "retrieval_hit": None,
        "citation_hit": None,
        "keyword_recall": None,
    }
    if answerable:
        result["retrieval_hit"] = float(item["expected_source"] in sources)
        result["citation_hit"] = float(item["expected_source"] in cited)
        kws = item.get("expected_keywords", [])
        result["keyword_recall"] = sum(k.lower() in answer for k in kws) / len(kws) if kws else None
    return result


def run(target: Callable[[str], dict[str, Any]], golden: list[dict[str, Any]]) -> dict[str, Any]:
    rows, latencies, cost, tokens = [], [], 0.0, 0
    for item in golden:
        started = time.perf_counter()
        out = target(item["question"])
        latencies.append((time.perf_counter() - started) * 1000)
        usage = out.get("usage", {})
        cost += usage.get("estimated_cost_usd", 0.0)
        tokens += usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
        s = score(item, out)
        rows.append({"id": item["id"], **s, "answer": out.get("answer", "")[:160]})

    summary: dict[str, Any] = {}
    for metric in THRESHOLDS:
        vals = [r[metric] for r in rows if r[metric] is not None]
        summary[metric] = round(sum(vals) / len(vals), 3) if vals else None
    summary["p50_latency_ms"] = round(statistics.median(latencies))
    summary["max_latency_ms"] = round(max(latencies))
    summary["total_tokens"] = tokens
    summary["estimated_cost_usd"] = round(cost, 6)
    return {"summary": summary, "rows": rows}


def run_langsmith(golden: list[dict[str, Any]], target: Callable[[str], dict[str, Any]]) -> None:
    from langsmith import Client

    client = Client()
    name = "fielddocs-golden-set"
    if not client.has_dataset(dataset_name=name):
        client.create_dataset(name, description="FieldDocs RAG golden questions")
        client.create_examples(
            dataset_name=name,
            examples=[
                {
                    "inputs": {"question": g["question"]},
                    "outputs": {k: v for k, v in g.items() if k != "question"},
                }
                for g in golden
            ],
        )

    def _metric(key: str):  # noqa: ANN202
        def evaluator(outputs: dict, reference_outputs: dict) -> dict:
            value = score({**reference_outputs, "question": ""}, outputs)[key]
            return {"key": key, "score": value}

        evaluator.__name__ = key
        return evaluator

    client.evaluate(
        lambda inputs: target(inputs["question"]),
        data=name,
        evaluators=[_metric(k) for k in THRESHOLDS],
        experiment_prefix="fielddocs",
        max_concurrency=2,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--golden", type=Path, default=HERE / "golden_set.jsonl")
    parser.add_argument("--seed", type=Path, help="ingest this folder first (in-process mode)")
    parser.add_argument("--url", help="evaluate a deployed API instead of in-process")
    parser.add_argument("--token", help="bearer token for --url")
    parser.add_argument("--langsmith", action="store_true")
    parser.add_argument("--no-gate", action="store_true", help="never exit non-zero")
    args = parser.parse_args()

    golden = load_golden(args.golden)
    target = http_target(args.url, args.token) if args.url else local_target(args.seed)

    if args.langsmith:
        run_langsmith(golden, target)

    report = run(target, golden)
    for row in report["rows"]:
        print(json.dumps(row))
    print("\nSUMMARY", json.dumps(report["summary"], indent=2))

    failed = [
        m
        for m, t in THRESHOLDS.items()
        if report["summary"][m] is not None and report["summary"][m] < t
    ]
    if failed and not args.no_gate:
        print(f"\nFAILED thresholds: {failed}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
