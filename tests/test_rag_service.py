from langchain_core.language_models.fake_chat_models import FakeListChatModel

from app.config import get_settings
from app.providers import get_utility_llm, get_vector_index
from app.rag.prompts import NO_ANSWER
from app.rag.retriever import Retriever
from app.rag.service import RagService
from app.storage import get_store


class CountingLLM(FakeListChatModel):
    calls: int = 0

    def invoke(self, *args, **kwargs):  # noqa: ANN002, ANN003
        self.calls += 1
        return super().invoke(*args, **kwargs)


def _service(llm, utility=None) -> RagService:
    s = get_settings()
    return RagService(
        settings=s,
        llm=llm,
        utility_llm=utility or get_utility_llm(),
        retriever=Retriever(get_vector_index(), s),
        store=get_store(),
    )


def test_answers_with_citation_from_the_right_document(seeded, service):
    resp = service.answer("What received power is healthy at the ONT?", user="u1")
    assert not resp.refused and resp.grounded
    assert "-27 dBm" in resp.answer
    cited = [s for s in resp.sources if s.cited]
    assert cited and cited[0].source == "fttp_ont_install_guide.md"
    assert resp.usage.llm_calls == 1


def test_out_of_scope_question_is_refused_without_calling_the_llm(seeded):
    llm = CountingLLM(responses=["should not be used [1]"])
    resp = _service(llm).answer("Who won the football world cup in 1966?", user="u1")
    assert resp.refused and resp.answer == NO_ANSWER
    assert llm.calls == 0


def test_hallucinated_citation_is_blocked(seeded):
    llm = CountingLLM(responses=["Batteries last 20 years [9]."])
    resp = _service(llm).answer("How often should cabinet batteries be replaced?", user="u1")
    assert resp.refused and resp.answer == NO_ANSWER
    assert not resp.grounded


def test_repeat_question_is_served_from_cache(seeded):
    llm = CountingLLM(responses=["Replace them every 4 years [1]."] * 2)
    svc = _service(llm)
    first = svc.answer("How often should cabinet batteries be replaced?", user="u1")
    second = svc.answer("how often should cabinet batteries be replaced", user="u2")
    assert not first.cached and second.cached
    assert second.answer == first.answer
    assert llm.calls == 1


def test_new_ingest_invalidates_cache(seeded):
    from app.ingestion.pipeline import ingest_bytes

    llm = CountingLLM(responses=["Replace them every 4 years [1]."] * 2)
    svc = _service(llm)
    svc.answer("How often should cabinet batteries be replaced?", user="u1")
    ingest_bytes(b"# Note\n\nNew cabinet guidance about filters.", "note.md", "doc-new")
    svc.answer("How often should cabinet batteries be replaced?", user="u1")
    assert llm.calls == 2


def test_follow_up_uses_history_rewrite(seeded):
    utility = CountingLLM(responses=["What is the minimum bend radius for the fibre drop cable?"])
    svc = _service(CountingLLM(responses=["It is 30 mm [1]."] * 2), utility=utility)
    svc.answer("Where should I mount the ONT?", user="u1", session_id="s1")
    resp = svc.answer("And what about its bend radius?", user="u1", session_id="s1")
    assert utility.calls == 1
    assert not resp.refused
    assert any(s.source == "fttp_ont_install_guide.md" for s in resp.sources)


def test_history_is_isolated_per_user(seeded):
    store = get_store()
    _service(CountingLLM(responses=["Mount it inside [1]."])).answer(
        "Where should I mount the ONT?", user="alice", session_id="s1"
    )
    assert store.get_history("alice", "s1", 3)
    assert store.get_history("mallory", "s1", 3) == []


def test_doc_filter_restricts_search(seeded):
    from pathlib import Path

    from app.ingestion.cli import stable_doc_id

    copper = stable_doc_id(Path("copper_line_fault_diagnosis.md"))
    resp = _service(CountingLLM(responses=["x [1]"])).answer(
        "What received power is healthy at the ONT?", user="u1", doc_ids=[copper]
    )
    assert all(s.doc_id == copper for s in resp.sources)
