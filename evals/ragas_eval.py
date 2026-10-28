"""RAGAS evaluation: LLM-judged quality scores for the whole RAG pipeline.

``run_eval.py`` checks hard facts cheaply (right document? cited? key number in
the answer? refused when it should be?). This script adds four RAGAS metrics,
each scored 0-1 by a judge LLM on Bedrock:

* faithfulness      - is every claim in the answer supported by the retrieved
                      chunks? (low = hallucination)
* answer_relevancy  - does the answer actually address the question?
* context_precision - are the chunks that matter ranked at the top?
* context_recall    - did retrieval find everything the reference answer needs?

The last two compare against the ``reference`` answer in golden_set.jsonl.
Unanswerable questions are skipped here; run_eval.py scores refusals.

It runs in-process against Bedrock + Pinecone from your .env (the judge needs a
real LLM), costs a few cents per run, and is not part of CI:

    pip install -e ".[eval]" -c requirements.lock
    python -m evals.ragas_eval                       # corpus already indexed
    python -m evals.ragas_eval --seed sample_docs    # ingest sample docs first
    python -m evals.ragas_eval --judge-model us.amazon.nova-pro-v1:0 --limit 5

Writes evals/reports/ragas-<timestamp>.json and exits non-zero when a metric
falls below its threshold.

RAGAS is pinned to 0.4.3 and used through its LangChain wrappers, which is
RAGAS's documented route for Bedrock models (it prints deprecation warnings
because v0.4 is moving to a new metrics API; we silence them).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
import warnings
from pathlib import Path
from typing import Any

from evals.run_eval import load_golden

HERE = Path(__file__).resolve().parent
REPORTS = HERE / "reports"

# Our metric name -> the name RAGAS reports it under.
RAGAS_NAMES = {
    "faithfulness": "faithfulness",
    "answer_relevancy": "answer_relevancy",
    "context_precision": "llm_context_precision_with_reference",
    "context_recall": "context_recall",
}
THRESHOLDS = {
    "faithfulness": 0.85,
    "answer_relevancy": 0.75,
    "context_precision": 0.7,
    "context_recall": 0.8,
}


class RecordingRetriever:
    """Wraps the service's retriever to capture exactly what the LLM was shown.

    The API response only carries 300-character snippets; RAGAS needs the full
    chunk text the answer was generated from.
    """

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.last: list[Any] = []

    def retrieve(self, query: str, doc_ids: list[str] | None = None) -> list[Any]:
        self.last = self.inner.retrieve(query, doc_ids=doc_ids)
        return self.last

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


def collect(
    service: Any, golden: list[dict[str, Any]], limit: int | None = None
) -> list[dict[str, Any]]:
    """Ask the answerable golden questions; keep question, answer, contexts, reference."""
    recorder = RecordingRetriever(service.retriever)
    service.retriever = recorder
    records: list[dict[str, Any]] = []
    try:
        for item in golden:
            if not item.get("answerable", True) or not item.get("reference"):
                continue
            if limit is not None and len(records) >= limit:
                break
            recorder.last = []
            out = service.answer(item["question"], user="ragas-eval")
            # A cached answer skips retrieval; fetch the contexts it was built from.
            chunks = recorder.last if not out.cached else recorder.inner.retrieve(item["question"])
            records.append(
                {
                    "id": item["id"],
                    "user_input": item["question"],
                    "response": out.answer,
                    "retrieved_contexts": [c.doc.page_content for c in chunks],
                    "reference": item["reference"],
                    "refused": out.refused,
                }
            )
    finally:
        service.retriever = recorder.inner
    return records


def _clean(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) else round(number, 4)


def summarize(
    scores: list[dict[str, Any]], records: list[dict[str, Any]]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Per-question rows plus the mean of each metric (NaN = judge failed, skipped)."""
    rows = []
    for record, score in zip(records, scores, strict=True):
        row: dict[str, Any] = {"id": record["id"], "refused": record["refused"]}
        for ours, theirs in RAGAS_NAMES.items():
            row[ours] = _clean(score.get(theirs))
        row["answer"] = record["response"][:160]
        rows.append(row)

    summary: dict[str, Any] = {}
    for metric in RAGAS_NAMES:
        values = [r[metric] for r in rows if r[metric] is not None]
        summary[metric] = round(sum(values) / len(values), 3) if values else None
    summary["questions"] = len(rows)
    summary["refused_answerable"] = sum(r["refused"] for r in rows)
    return summary, rows


def failed_metrics(summary: dict[str, Any]) -> list[str]:
    return [m for m, t in THRESHOLDS.items() if summary.get(m) is not None and summary[m] < t]


def _token_usage(llm_result: Any) -> Any:
    """Read Bedrock Converse token counts (LangChain usage_metadata) for RAGAS."""
    from ragas.cost import TokenUsage

    tokens_in = tokens_out = 0
    for generations in llm_result.generations:
        for generation in generations:
            meta = getattr(getattr(generation, "message", None), "usage_metadata", None) or {}
            tokens_in += int(meta.get("input_tokens", 0))
            tokens_out += int(meta.get("output_tokens", 0))
    return TokenUsage(input_tokens=tokens_in, output_tokens=tokens_out)


def judge(model_id: str, region: str) -> tuple[Any, Any]:
    """Bedrock judge LLM + Titan embeddings, wrapped for RAGAS."""
    from langchain_aws import ChatBedrockConverse
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper

    from app.providers import BOTO_CONFIG, get_embeddings

    llm = ChatBedrockConverse(
        model=model_id, region_name=region, temperature=0.0, max_tokens=2048, config=BOTO_CONFIG
    )
    return LangchainLLMWrapper(llm), LangchainEmbeddingsWrapper(get_embeddings())


def score_with_ragas(
    records: list[dict[str, Any]], judge_model: str, region: str, max_workers: int
) -> tuple[list[dict[str, Any]], tuple[int, int]]:
    from ragas import EvaluationDataset, RunConfig, SingleTurnSample, evaluate
    from ragas.metrics import (
        Faithfulness,
        LLMContextPrecisionWithReference,
        LLMContextRecall,
        ResponseRelevancy,
    )

    llm, embeddings = judge(judge_model, region)
    dataset = EvaluationDataset(
        samples=[
            SingleTurnSample(
                user_input=r["user_input"],
                response=r["response"],
                retrieved_contexts=r["retrieved_contexts"],
                reference=r["reference"],
            )
            for r in records
        ]
    )
    result = evaluate(
        dataset,
        metrics=[
            Faithfulness(),
            ResponseRelevancy(),
            LLMContextPrecisionWithReference(),
            LLMContextRecall(),
        ],
        llm=llm,
        embeddings=embeddings,
        # Few workers: new AWS accounts have low Bedrock request quotas.
        run_config=RunConfig(max_workers=max_workers, timeout=180, max_retries=6),
        token_usage_parser=_token_usage,
        raise_exceptions=False,
    )
    try:
        usage = result.total_tokens()
    except (IndexError, ValueError):  # no usage recorded
        return result.scores, (0, 0)
    usages = usage if isinstance(usage, list) else [usage]
    return result.scores, (
        sum(u.input_tokens for u in usages),
        sum(u.output_tokens for u in usages),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--golden", type=Path, default=HERE / "golden_set.jsonl")
    parser.add_argument("--seed", type=Path, help="ingest this folder first")
    parser.add_argument("--judge-model", help="Bedrock model that scores answers")
    parser.add_argument("--limit", type=int, help="only the first N answerable questions")
    parser.add_argument("--max-workers", type=int, default=2)
    parser.add_argument("--no-gate", action="store_true", help="never exit non-zero")
    args = parser.parse_args()

    from app.api.main import get_rag_service
    from app.config import get_settings
    from app.rag.service import _price

    settings = get_settings()
    if settings.llm_provider == "fake":
        print("RAGAS needs a real judge LLM: set LLM_PROVIDER=bedrock (see .env.example).")
        return 2
    judge_model = args.judge_model or settings.llm_model_id

    if args.seed:
        from app.ingestion.cli import ingest_folder

        ingest_folder(args.seed)

    golden = load_golden(args.golden)
    records = collect(get_rag_service(), golden, limit=args.limit)
    print(f"collected {len(records)} answers; judging with {judge_model} ...")

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        scores, (tokens_in, tokens_out) = score_with_ragas(
            records, judge_model, settings.aws_region, args.max_workers
        )

    summary, rows = summarize(scores, records)
    price_in, price_out = _price(judge_model)
    summary["judge_model"] = judge_model
    summary["judge_tokens"] = tokens_in + tokens_out
    summary["judge_cost_usd"] = round(tokens_in * price_in / 1e6 + tokens_out * price_out / 1e6, 4)

    for row in rows:
        print(json.dumps(row))
    print("\nRAGAS SUMMARY", json.dumps(summary, indent=2))

    REPORTS.mkdir(exist_ok=True)
    report = REPORTS / f"ragas-{time.strftime('%Y%m%d-%H%M%S')}.json"
    report.write_text(json.dumps({"summary": summary, "rows": rows}, indent=2))
    print(f"report: {report}")

    failed = failed_metrics(summary)
    if failed and not args.no_gate:
        print(f"\nFAILED thresholds: {failed}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
