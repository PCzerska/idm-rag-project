"""Testy opisu eksperymentu (bez sieci): chunking z kolekcji, nazwa pliku, ranking, `--split`."""

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from qdrant_client import QdrantClient, models

from rag import eval as ev
from rag.config import load_settings
from rag.grade import load_sheet
from rag.store import DENSE, describe_collection


@pytest.fixture
def settings(monkeypatch):
    for name in ("COLLECTION", "LLM_MODEL", "EMBED_MODEL", "TOP_K"):
        monkeypatch.delenv(name, raising=False)
    s = load_settings(env_file=None)
    return replace(s, collection="sceny", llm_model="qwen3.5:4b", embed_model="emb:0.6b")


def test_describe_collection_reads_payload_not_env():
    client = QdrantClient(location=":memory:")
    client.create_collection(
        "sceny",
        vectors_config={DENSE: models.VectorParams(size=2, distance=models.Distance.COSINE)},
    )
    client.upsert(
        "sceny",
        points=[
            models.PointStruct(
                id=i,
                vector={DENSE: [1.0, 0.0]},
                payload={"chunking": "prepared", "embed_model": "e"},
            )
            for i in range(3)
        ],
    )
    info = describe_collection(client, "sceny")
    assert info == {"points": 3, "chunking": {"prepared": 3}, "embed_model": {"e": 3}}
    with pytest.raises(RuntimeError, match="nie istnieje"):
        describe_collection(client, "brak")


def test_experiment_records_models_chunking_and_label(settings, monkeypatch):
    monkeypatch.setattr(ev, "git_commit", lambda: "abc1234")
    index = {"points": 140, "chunking": {"prepared": 140}, "embed_model": {"emb:0.6b": 140}}

    retrieval = ev.describe_experiment(settings, index, "eval/pytania.jsonl")
    assert retrieval["chunking"] == "prepared" and retrieval["chunks"] == 140
    assert retrieval["embed_model"] == "emb:0.6b" and retrieval["llm_model"] is None
    assert retrieval["git_commit"] == "abc1234" and retrieval["split"] == "wszystkie"
    assert ev.experiment_label(retrieval, "eval/pytania.jsonl") == "pytania_sceny_prepared_emb-0.6b"

    full = ev.describe_experiment(settings, index, "eval/pytania.jsonl", split="dev", generate=True)
    assert full["llm_model"] == "qwen3.5:4b" and full["llm_temperature"] == settings.llm_temperature
    label = ev.experiment_label(full, "eval/pytania.jsonl")
    assert label == "pytania_dev_sceny_prepared_emb-0.6b_qwen3.5-4b"
    assert "| chunking (z kolekcji) | `prepared` |" in ev.experiment_markdown(full)


def test_rows_keep_retrieval_ranking_and_sheet_shows_context(monkeypatch, tmp_path):
    hits = [
        SimpleNamespace(text="a", source_ids=["scenariusz#s14"], score=0.81234),
        SimpleNamespace(text="b", source_ids=["scenariusz#s1"], score=0.7),
        SimpleNamespace(text="c", source_ids=["scenariusz#s9"], score=0.6),
    ]
    monkeypatch.setattr(ev, "retrieve", lambda q, s, k=None, collection=None: hits)
    item = ev.GoldItem(id="q1", question="?", answer="x", source_ids=["scenariusz#s1"], split="dev")
    res = ev.evaluate([item], SimpleNamespace(top_k=2))
    row = res["rows"][0]
    assert row["split"] == "dev"
    assert row["retrieved"][0] == {"rank": 1, "source_ids": ["scenariusz#s14"], "score": 0.8123}
    assert [r["rank"] for r in row["retrieved"]] == [1, 2, 3]

    row["answer"] = "odpowiedź"
    sheet = tmp_path / "ocena.csv"
    ev.write_grading_sheet(res, sheet, top_k=2)  # model widział tylko 2 pierwsze chunki
    assert load_sheet(sheet)[0]["znalezione_sceny"] == "14, 1"


def test_load_golden_split(tmp_path):
    path = tmp_path / "zbior.jsonl"
    rows = [
        {"id": "a", "question": "?", "answer": "x", "source_ids": [], "split": "dev"},
        {"id": "b", "question": "?", "answer": "x", "source_ids": [], "split": "test"},
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    assert [i.id for i in ev.load_golden(path)] == ["a", "b"]
    assert [i.id for i in ev.load_golden(path, split="test")] == ["b"]
    with pytest.raises(ValueError, match="dostępne: dev, test"):
        ev.load_golden(path, split="walidacja")


def test_save_results_puts_experiment_first(settings, tmp_path):
    results = ev.results_from_rows([{"id": "q1", "question": "?", "recall@1": 1.0}])
    experiment = {"golden": "g", "chunking": "prepared", "llm_model": None}
    path = ev.save_results(results, settings, tmp_path, "lbl", experiment)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert next(iter(data)) == "experiment" and data["experiment"]["chunking"] == "prepared"
    assert data["config"]["llm_api_key"] == "***"
    assert "## Eksperyment" in path.with_suffix(".md").read_text(encoding="utf-8")


def test_failed_generation_counts_as_wrong_and_run_continues(monkeypatch, tmp_path):
    """Zepsuty JSON przy jednym pytaniu nie przerywa przebiegu; pytanie liczy się jako nieudane."""
    from rag.structured import StructuredOutputError

    ok = SimpleNamespace(
        answer=SimpleNamespace(answer="Scena 1 [1]"),
        cited_numbers=[1],
        cited_hits=lambda: [SimpleNamespace(source_ids=["scenariusz#s1"])],
        retrieve_s=0.1,
        generate_s=1.0,
        mode="prompt",
        answered_from_context=True,
        has_inline_citations=True,
    )

    def fake_answer(q, s, k=None, collection=None):
        if q == "zepsute":
            raise StructuredOutputError("żaden tryb structured output nie zadziałał")
        return ok

    hit = SimpleNamespace(text="t", source_ids=["scenariusz#s1"], score=0.5)
    monkeypatch.setattr(ev, "retrieve", lambda q, s, k=None, collection=None: [hit])
    monkeypatch.setattr(ev, "answer_question", fake_answer)
    items = [
        ev.GoldItem(id="a", question="dobre", answer="x", source_ids=["scenariusz#s1"]),
        ev.GoldItem(id="b", question="zepsute", answer="x", source_ids=["scenariusz#s1"]),
    ]
    res = ev.evaluate(items, SimpleNamespace(top_k=5), generate=True)
    a, b = res["rows"]
    assert a["gen_error"] is False and b["gen_error"] is True
    assert b["refusal_ok"] is False and b["citation_hit"] is False and "żaden tryb" in b["error"]
    assert b["recall@1"] == 1.0  # retrieval liczy się normalnie
    assert res["summary"]["gen_error"] == 0.5 and res["summary"]["refusal_ok"] == 0.5
    assert ev.structured_modes(res["rows"]) == {"prompt": 1, "błąd": 1}
    assert ev.auto_grade(b) == ("bledna", "")

    sheet = tmp_path / "ocena.csv"
    ev.write_grading_sheet(res, sheet)
    assert load_sheet(sheet)[1]["odpowiedz_systemu"].startswith("[BŁĄD GENERACJI]")
