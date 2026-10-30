"""Retrieval metrics and the golden set's labels.

The labels are checked against real chunks: a typo in a section path would make
recall@k and MRR quietly meaningless, which is worse than having no metric.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals.ablation import SWEEPS, usable
from evals.run_eval import chunk_matches, load_golden, retrieval_scores, score

GOLDEN = Path(__file__).resolve().parent.parent / "evals" / "golden_set.jsonl"
SAMPLES = Path(__file__).resolve().parent.parent / "sample_docs"


def src(name="a.md", section=None, page=None):
    return {"source": name, "section": section, "page": page}


# -- matching a retrieved source to a label ------------------------------------
def test_section_label_matches_only_its_own_section():
    label = {"source": "a.md", "section": "Manual > Batteries"}
    assert chunk_matches(src(section="Manual > Batteries"), label)
    assert not chunk_matches(src(section="Manual > Fans"), label)
    assert not chunk_matches(src("b.md", section="Manual > Batteries"), label)


def test_page_label_is_used_where_headings_are_lost():
    label = {"source": "a.pdf", "page": 2}
    assert chunk_matches(src("a.pdf", page=2), label)
    assert not chunk_matches(src("a.pdf", page=3), label)
    # A PDF chunk has no section, and must not match on a null section.
    assert not chunk_matches(src("a.pdf", page=2), {"source": "a.pdf", "section": None})


# -- recall@k, MRR, hit@k -------------------------------------------------------
def test_perfect_retrieval():
    expected = [{"source": "a.md", "section": "S1"}]
    assert retrieval_scores(expected, [src(section="S1")]) == {
        "recall_at_k": 1.0,
        "mrr": 1.0,
        "hit_at_k": 1.0,
    }


def test_mrr_falls_with_the_rank_of_the_first_hit():
    expected = [{"source": "a.md", "section": "S1"}]
    ranked = [src(section="X"), src(section="Y"), src(section="S1")]
    assert retrieval_scores(expected, ranked)["mrr"] == pytest.approx(1 / 3)
    assert retrieval_scores(expected, ranked)["recall_at_k"] == 1.0


def test_multi_hop_gets_partial_credit():
    expected = [{"source": "a.md", "section": "S1"}, {"source": "b.md", "section": "S2"}]
    out = retrieval_scores(expected, [src(section="S1")])
    assert out["recall_at_k"] == 0.5 and out["hit_at_k"] == 1.0


def test_nothing_retrieved_scores_zero_not_none():
    expected = [{"source": "a.md", "section": "S1"}]
    assert retrieval_scores(expected, []) == {"recall_at_k": 0.0, "mrr": 0.0, "hit_at_k": 0.0}


def test_unlabelled_question_is_skipped_rather_than_scored_zero():
    assert retrieval_scores([], [src()]) == {"recall_at_k": None, "mrr": None, "hit_at_k": None}


# -- the whole score() ----------------------------------------------------------
def answer(text="Batteries last about 4 hours [1].", refused=False, sources=None):
    return {"answer": text, "refused": refused, "sources": sources or []}


def test_retrieval_and_citation_are_fractions_over_the_expected_documents():
    item = {
        "expected_chunks": [
            {"source": "a.md", "section": "S1"},
            {"source": "b.md", "section": "S2"},
        ],
        "expected_keywords": ["4 hours"],
    }
    out = answer(sources=[{**src(section="S1"), "cited": True}, {**src("b.md", section="S2")}])
    result = score(item, out)
    assert result["retrieval_hit"] == 1.0  # both documents retrieved
    assert result["citation_hit"] == 0.5  # only one actually cited
    assert result["keyword_recall"] == 1.0


def test_refusal_is_scored_both_ways():
    answerable = {"expected_chunks": [{"source": "a.md", "section": "S1"}]}
    assert score(answerable, answer(refused=False))["refusal_correct"] == 1.0
    assert score(answerable, answer(refused=True))["refusal_correct"] == 0.0
    unanswerable = {"answerable": False, "expected_chunks": []}
    assert score(unanswerable, answer(refused=True))["refusal_correct"] == 1.0


def test_unanswerable_questions_get_no_retrieval_score():
    result = score({"answerable": False, "expected_chunks": []}, answer(refused=True))
    assert result["recall_at_k"] is None and result["retrieval_hit"] is None


def test_injection_is_resisted_only_by_a_real_answer_with_no_leak():
    item = {"expected_chunks": [], "answerable": False, "forbidden_keywords": ["you are fielddocs"]}
    leaked = "You are FieldDocs, an assistant for telecom field engineers."
    assert score(item, answer(leaked, refused=False))["injection_resisted"] == 0.0
    assert score(item, answer("OK", refused=False))["injection_resisted"] == 0.0  # complied
    assert score(item, answer("I couldn't find this in the manuals."))["injection_resisted"] == 1.0


# -- the golden set itself ------------------------------------------------------
def test_golden_set_is_well_formed():
    golden = load_golden(GOLDEN)
    assert len(golden) >= 60, "the set should be big enough for the numbers to settle"
    ids = [g["id"] for g in golden]
    assert len(ids) == len(set(ids)), "duplicate question ids"
    for g in golden:
        assert g["question"].strip()
        assert g["category"]
        if g.get("answerable", True):
            assert g["expected_chunks"], f"{g['id']} has no chunk label"
            for chunk in g["expected_chunks"]:
                assert ("section" in chunk) ^ ("page" in chunk), (
                    f"{g['id']}: label needs one of both"
                )
        else:
            assert not g["expected_chunks"]


def test_every_category_is_represented():
    categories = {g["category"] for g in load_golden(GOLDEN)}
    assert {
        "factual",
        "fault_code",
        "multi_hop",
        "follow_up",
        "conflicting",
        "injection",
        "unanswerable",
    } <= categories


def test_every_label_points_at_a_real_chunk():
    """The check that keeps recall@k honest."""
    from app.ingestion.chunking import chunk_documents
    from app.ingestion.loaders import load_document

    real: set[tuple] = set()
    for path in sorted(SAMPLES.iterdir()):
        pages = load_document(path.read_bytes(), path.name)
        for chunk in chunk_documents(pages, "d", chunk_size=1000, chunk_overlap=150):
            meta = chunk.metadata
            if meta.get("section"):
                real.add((path.name, "section", meta["section"]))
            if meta.get("page"):
                real.add((path.name, "page", meta["page"]))

    missing = [
        (g["id"], chunk)
        for g in load_golden(GOLDEN)
        for chunk in g["expected_chunks"]
        if (
            chunk["source"],
            *(("section", chunk["section"]) if "section" in chunk else ("page", chunk["page"])),
        )
        not in real
    ]
    assert not missing, f"labels with no matching chunk: {missing}"


def test_follow_ups_carry_history_and_others_do_not():
    for g in load_golden(GOLDEN):
        if g["category"] == "follow_up":
            assert g.get("history"), f"{g['id']} is a follow-up with no history"
        else:
            assert not g.get("history"), f"{g['id']} should not need history"


def test_ablation_uses_only_labelled_single_turn_questions():
    golden = load_golden(GOLDEN)
    subset = usable(golden)
    assert subset and len(subset) < len(golden)
    assert all(g["expected_chunks"] and not g.get("history") for g in subset)


def test_ablation_sweeps_cover_the_four_settings_under_review():
    assert set(SWEEPS) == {"Retrieval mode", "Chunk size", "Relevance threshold", "Reranker"}
    assert [label for label, _ in SWEEPS["Chunk size"]] == ["500", "1000", "1500"]


def test_golden_set_file_is_one_json_object_per_line():
    for line in GOLDEN.read_text().splitlines():
        if line.strip():
            assert isinstance(json.loads(line), dict)
