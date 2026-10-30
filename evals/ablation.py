"""One-factor-at-a-time ablation over the retrieval settings.

Answers the questions a reviewer actually asks: does hybrid search earn its
keep, what chunk size works best, where should the relevance threshold sit, and
does the reranker help.

Only the *retrieval* metrics are measured (recall@k, MRR, hit@k). No answer is
generated, so a full sweep costs embeddings and vector search rather than one
LLM call per question. That is the right trade: these four settings change which
chunks reach the model, and nothing else.

Questions needing a conversation (``history``) or expecting a refusal are
skipped, because neither tells you anything about ranking.

    python -m evals.ablation --seed sample_docs
    python -m evals.ablation --seed sample_docs --out evals/reports/ablation.md

Each variant re-ingests the corpus when the chunking changes, so a sweep takes a
few minutes against real embeddings.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent

# Settings that change how documents are split, so the corpus must be rebuilt.
REINGEST_KEYS = {"CHUNK_SIZE", "CHUNK_OVERLAP", "EMBEDDING_MODEL_ID", "EMBEDDING_DIMENSIONS"}

SWEEPS: dict[str, list[tuple[str, dict[str, str]]]] = {
    "Retrieval mode": [
        ("semantic only", {"ENABLE_HYBRID_SEARCH": "false"}),
        ("hybrid (RRF)", {"ENABLE_HYBRID_SEARCH": "true"}),
    ],
    "Chunk size": [
        ("500", {"CHUNK_SIZE": "500", "CHUNK_OVERLAP": "75"}),
        ("1000", {"CHUNK_SIZE": "1000", "CHUNK_OVERLAP": "150"}),
        ("1500", {"CHUNK_SIZE": "1500", "CHUNK_OVERLAP": "225"}),
    ],
    "Relevance threshold": [
        ("0.25", {"MIN_RELEVANCE_SCORE": "0.25"}),
        ("0.35", {"MIN_RELEVANCE_SCORE": "0.35"}),
        ("0.45", {"MIN_RELEVANCE_SCORE": "0.45"}),
    ],
    "Reranker": [
        ("off", {"ENABLE_RERANK": "false"}),
        ("on", {"ENABLE_RERANK": "true"}),
    ],
}

METRICS = ("recall_at_k", "mrr", "hit_at_k")


def usable(golden: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Questions that have chunk labels and need no conversation."""
    return [g for g in golden if g.get("expected_chunks") and not g.get("history")]


def reset_corpus() -> None:
    """Drop every indexed document so the next ingest really re-chunks.

    Without this the pipeline sees an unchanged content hash and skips the file,
    which would silently make every chunk-size variant identical.
    """
    from app.ingestion.pipeline import delete_document_vectors
    from app.storage import get_store

    store = get_store()
    for doc in store.list_documents():
        delete_document_vectors(doc["doc_id"])
        store.delete_document(doc["doc_id"])


def measure(seed: Path, golden: list[dict[str, Any]], reingest: bool) -> dict[str, Any]:
    """Build a retriever from the current environment and score retrieval only."""
    from app.config import get_settings
    from app.ingestion.cli import ingest_folder
    from app.providers import get_keyword_index, get_vector_index, reset_caches
    from app.rag.retriever import Retriever
    from app.storage import get_store
    from evals.run_eval import retrieval_scores

    reset_caches()
    settings = get_settings()
    store = get_store()
    # An in-memory store starts empty after reset_caches, so it always needs
    # seeding; a real store keeps its corpus and is only rebuilt when the
    # chunking changed.
    if reingest or not store.list_documents():
        reset_corpus()
        ingest_folder(seed)

    retriever = Retriever(get_vector_index(), settings, get_keyword_index(), registry=store)
    started = time.perf_counter()
    rows = []
    for item in golden:
        chunks = retriever.retrieve(item["question"])
        sources = [
            {
                "source": c.doc.metadata.get("source"),
                "section": c.doc.metadata.get("section"),
                "page": c.doc.metadata.get("page"),
            }
            for c in chunks
        ]
        rows.append({"id": item["id"], **retrieval_scores(item["expected_chunks"], sources)})

    elapsed = (time.perf_counter() - started) / max(len(golden), 1) * 1000
    out: dict[str, Any] = {
        m: round(sum(r[m] for r in rows) / len(rows), 3) if rows else None for m in METRICS
    }
    out["ms_per_query"] = round(elapsed, 1)
    return out


def table(name: str, results: list[tuple[str, dict[str, Any]]]) -> str:
    header = f"| {name} | recall@k | MRR | hit@k | ms/query |\n| --- | --- | --- | --- | --- |"
    scores = [r["recall_at_k"] or 0 for _, r in results]
    best = max(scores) if scores else 0
    unique_best = scores.count(best) == 1 and len(results) > 1
    lines = []
    for label, r in results:
        mark = " **(best)**" if unique_best and (r["recall_at_k"] or 0) == best else ""
        cells = " | ".join(str(r[m]) for m in (*METRICS, "ms_per_query"))
        lines.append(f"| {label}{mark} | {cells} |")
    return "\n".join([header, *lines])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--golden", type=Path, default=HERE / "golden_set.jsonl")
    parser.add_argument("--seed", type=Path, default=Path("sample_docs"))
    parser.add_argument("--out", type=Path, help="write the markdown table here")
    parser.add_argument("--only", help="run a single sweep by name, e.g. 'Chunk size'")
    args = parser.parse_args()

    from evals.run_eval import load_golden

    golden = usable(load_golden(args.golden))
    if not golden:
        print("no questions with chunk labels", file=sys.stderr)
        return 2

    baseline = dict(os.environ)
    sweeps = {k: v for k, v in SWEEPS.items() if not args.only or k == args.only}
    sections: list[str] = []

    for name, variants in sweeps.items():
        print(f"\n=== {name} ===", file=sys.stderr)
        results: list[tuple[str, dict[str, Any]]] = []
        for label, overrides in variants:
            os.environ.clear()
            os.environ.update(baseline, **overrides)
            if name == "Reranker" and overrides.get("ENABLE_RERANK") == "true":
                if not os.environ.get("RERANK_MODEL_ARN"):
                    print("  skipped 'on': RERANK_MODEL_ARN is not set", file=sys.stderr)
                    continue
            print(f"  {label} ...", file=sys.stderr, flush=True)
            results.append(
                (label, measure(args.seed, golden, reingest=bool(REINGEST_KEYS & overrides.keys())))
            )
        if results:
            sections.append(f"### {name}\n\n{table(name, results)}")

    os.environ.clear()
    os.environ.update(baseline)

    body = "\n\n".join(sections)
    head = (
        f"# Retrieval ablation\n\n"
        f"{len(golden)} labelled questions, retrieval only (no answer generation). "
        f"`k` is `RETRIEVAL_K`. One factor varied at a time from the current `.env`.\n"
    )
    print("\n" + head + "\n" + body)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(head + "\n" + body + "\n")
        print(f"\nwrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
