"""RAGAS runner plumbing, tested offline (no ragas package and no judge LLM needed)."""

import math

from evals.ragas_eval import (
    HERE,
    RAGAS_NAMES,
    RecordingRetriever,
    collect,
    failed_metrics,
    summarize,
)
from evals.run_eval import load_golden

GOLDEN = [
    {
        "id": "q01",
        "question": "What received optical power is considered healthy at the ONT?",
        "reference": "Healthy received optical power at the ONT is between -8 dBm and -27 dBm.",
    },
    {"id": "q08", "question": "What voltage triggers the cabinet BATTERY LOW alarm?"},
    {"id": "u01", "question": "What is the annual leave policy?", "answerable": False},
]


def test_every_answerable_golden_question_has_a_reference_answer():
    golden = load_golden(HERE / "golden_set.jsonl")
    answerable = [g for g in golden if g.get("answerable", True)]
    assert answerable and all(g.get("reference") for g in answerable)


def test_collect_records_full_contexts_and_skips_questions_without_reference(seeded, service):
    original = service.retriever
    records = collect(service, GOLDEN)

    assert [r["id"] for r in records] == ["q01"]
    record = records[0]
    assert record["reference"] == GOLDEN[0]["reference"]
    assert record["retrieved_contexts"] and any(
        "-27 dBm" in c for c in record["retrieved_contexts"]
    )
    # Full chunk text, not the 300-character snippet the API returns.
    assert max(len(c) for c in record["retrieved_contexts"]) > 300
    assert service.retriever is original  # wrapper removed afterwards


def test_cached_answers_still_get_their_contexts(seeded, service):
    first = collect(service, GOLDEN[:1])
    second = collect(service, GOLDEN[:1])  # served from the answer cache
    assert second[0]["retrieved_contexts"] == first[0]["retrieved_contexts"]


def test_limit_stops_early(seeded, service):
    golden = [{**GOLDEN[0], "id": f"q{i}"} for i in range(3)]
    assert len(collect(service, golden, limit=2)) == 2


def test_recording_retriever_passes_other_attributes_through():
    class Inner:
        hybrid = True

        def retrieve(self, query, doc_ids=None):
            return [query, doc_ids]

    recorder = RecordingRetriever(Inner())
    assert recorder.hybrid is True
    assert recorder.retrieve("q", doc_ids=["d"]) == ["q", ["d"]]
    assert recorder.last == ["q", ["d"]]


def test_summarize_averages_and_ignores_failed_judgements():
    records = [
        {"id": "a", "response": "x", "refused": False},
        {"id": "b", "response": "y", "refused": True},
    ]
    scores = [
        {RAGAS_NAMES["faithfulness"]: 1.0, RAGAS_NAMES["answer_relevancy"]: 0.9,
         RAGAS_NAMES["context_precision"]: 1.0, RAGAS_NAMES["context_recall"]: 1.0},
        {RAGAS_NAMES["faithfulness"]: math.nan, RAGAS_NAMES["answer_relevancy"]: 0.1,
         RAGAS_NAMES["context_precision"]: 0.5, RAGAS_NAMES["context_recall"]: None},
    ]  # fmt: skip
    summary, rows = summarize(scores, records)

    assert summary["faithfulness"] == 1.0  # NaN = judge failed -> skipped
    assert summary["answer_relevancy"] == 0.5
    assert summary["context_precision"] == 0.75
    assert summary["context_recall"] == 1.0
    assert summary["questions"] == 2 and summary["refused_answerable"] == 1
    assert rows[1]["faithfulness"] is None and rows[1]["refused"] is True
    assert failed_metrics(summary) == ["answer_relevancy"]
