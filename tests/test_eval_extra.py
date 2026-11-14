"""Testy rozszerzonej ewaluacji (bez sieci): ocena liczb i list, grupy, arkusz oceny ręcznej."""
import csv
from types import SimpleNamespace

import pytest

from rag import eval as ev
from rag.grade import final_grade, load_sheet, summarize_grades


def fake_hit(text="t", source_ids=()):
    """Atrapa trafienia: eval używa tylko .text i .source_ids (bez zależności od konstruktora Hit)."""
    return SimpleNamespace(text=text, source_ids=list(source_ids), score=0.0)


def item(**kw):
    base = dict(id="q1", question="?", answer="x", source_ids=["scenariusz#s1"],
                tags=["kat", "latwe", "fakt"])
    base.update(kw)
    return ev.GoldItem(**base)


# ------------------------------------------------------------------ liczby i numery scen


def test_numbers_in_ignores_markers_and_scene_suffixes():
    assert ev.numbers_in("Jest 6 scen [1]: 26, 27 i 66A.") == {6, 26, 27}
    assert ev.numbers_in("Koszt 1,5 tys.") == {1}  # część po przecinku nie liczy się osobno


def test_count_ok_requires_all_numbers():
    assert ev.count_ok("Jest 13 scen nocnych.", [13])
    assert not ev.count_ok("Jest 12 scen nocnych.", [13])
    assert ev.count_ok("WN: 88, PL: 39, mieszane: 5", [88, 39, 5])
    assert not ev.count_ok("WN: 88, PL: 39", [88, 39, 5])
    assert not ev.count_ok("brak liczby", [])


def test_scene_refs_skip_units_and_markers():
    text = "W 6 scenach: 13A [1], 19 oraz 117*. Filip ma 14 lat, a koperta 500 zł."
    assert ev.scene_refs(text) == {"13A", "19", "117*"}


def test_list_scores_precision_recall():
    s = ev.list_scores("Sceny 13A, 19 i 46.", ["13A", "19", "46", "61"])
    assert s["list_p"] == 1.0 and s["list_r"] == 0.75
    assert s["list_f1"] == pytest.approx(0.857, abs=1e-3)
    s = ev.list_scores("Sceny 13A, 19, 20.", ["13A", "19"])
    assert s["list_p"] == pytest.approx(0.667, abs=1e-3) and s["list_r"] == 1.0
    assert ev.list_scores("nic", ["1"])["list_f1"] == 0.0


def test_auto_scores_by_kind():
    liczba = item(tags=["agregacja", "trudne", "liczba"], numbers=[4], elements=["44", "93"])
    assert ev.auto_scores(liczba, "4 sceny") == {"count_ok": True}
    lista = item(tags=["ciaglosc", "srednie", "lista"], elements=["1", "49"])
    assert ev.auto_scores(lista, "sceny 1 i 49")["list_f1"] == 1.0
    opisowa = item(tags=["kilka_zrodel", "srednie", "lista"], elements=["Brolin", "Zanieś"])
    assert ev.auto_scores(opisowa, "Brolin i Zanieś") == {}  # elementy nie są numerami scen
    assert ev.auto_scores(item(tags=["fakt", "latwe", "fakt"]), "x") == {}


# ----------------------------------------------------------------------- przebieg i grupy


def fake_result(text, cited_ids):
    hits = [fake_hit("t", [sid]) for sid in cited_ids]
    return SimpleNamespace(
        answer=SimpleNamespace(answer=text),
        cited_numbers=list(range(1, len(hits) + 1)),
        cited_hits=lambda: hits,
        retrieve_s=0.5,
        generate_s=1.0,
        mode="native",
        answered_from_context=bool(hits),
        has_inline_citations=bool(hits),
    )


def test_evaluate_groups_and_refusals(monkeypatch):
    items = [
        item(id="a", source_ids=["scenariusz#s1"], tags=["fakt", "latwe", "fakt"]),
        item(id="b", source_ids=["scenariusz#s2"], tags=["fakt", "latwe", "fakt"]),
        item(id="c", source_ids=[], tags=["brak_w_dokumentach", "brak", "odmowa_brak"]),
        item(id="d", source_ids=[], tags=["spoza_zakresu", "brak", "odmowa_spoza"]),
        item(id="e", source_ids=["scenariusz#s3"], tags=["agregacja", "trudne", "liczba"],
             numbers=[4], elements=["3"]),
    ]
    answers = {
        "a": fake_result("Tak [1]", ["scenariusz#s1"]),          # poprawnie
        "b": fake_result("Nie wiem", []),                         # fałszywa odmowa
        "c": fake_result("Wymyślona odpowiedź [1]", ["scenariusz#s9"]),  # halucynacja
        "d": fake_result("Brak informacji", []),                  # poprawna odmowa
        "e": fake_result("Są 4 sceny [1]", ["scenariusz#s3"]),
    }
    order = iter(items)
    current = {}

    def fake_retrieve(q, settings, k=None, collection=None):
        current["item"] = next(order)
        src = current["item"].source_ids
        return [fake_hit("abc", src)] if src else [fake_hit("zzz", ["scenariusz#s9"])]

    monkeypatch.setattr(ev, "retrieve", fake_retrieve)
    monkeypatch.setattr(ev, "answer_question", lambda q, s, k=None, collection=None: answers[current["item"].id])

    settings = SimpleNamespace(top_k=5, llm_model="m", collection="c")  # evaluate używa tylko top_k
    res = ev.evaluate(items, settings, generate=True)
    rows = {r["id"]: r for r in res["rows"]}
    assert rows["a"]["refusal_ok"] and rows["a"]["false_refusal"] is False and rows["a"]["citation_hit"]
    assert rows["b"]["false_refusal"] is True and not rows["b"]["refusal_ok"]
    assert rows["c"]["correct_refusal"] is False      # odpowiedział, choć nie powinien
    assert rows["d"]["correct_refusal"] is True
    assert rows["e"]["count_ok"] is True
    assert "recall@1" not in rows["c"]                  # brak źródeł -> bez recall

    s = res["summary"]
    assert s["questions"] == 5
    assert s["false_refusal"] == pytest.approx(1 / 3, abs=1e-3)   # b z (a, b, e)
    assert s["correct_refusal"] == 0.5                             # d z (c, d)
    assert set(res["by_difficulty"]) == {"latwe", "brak", "trudne"}
    assert res["by_difficulty"]["latwe"]["false_refusal"] == 0.5
    assert res["by_category"]["agregacja"]["count_ok"] == 1.0
    assert "## Wyniki per trudność" in ev.to_markdown(res, "t")


def test_auto_grade():
    assert ev.auto_grade({"false_refusal": True}) == ("bledna", "zla_odmowa")
    assert ev.auto_grade({"correct_refusal": True}) == ("poprawna_odmowa", "")
    assert ev.auto_grade({"correct_refusal": False}) == ("bledna", "halucynacja")
    assert ev.auto_grade({"count_ok": True}) == ("poprawna", "")
    assert ev.auto_grade({"count_ok": False}) == ("bledna", "")
    assert ev.auto_grade({}) == ("", "")


# -------------------------------------------------------------------- arkusz i ocena ręczna


def test_sheet_roundtrip_and_grade_summary(tmp_path):
    rows = [
        {"id": "a", "question": "Pytanie ąę?", "expected": "x", "answer": "y",
         "cited_sources": ["scenariusz#s1"], "expected_sources": ["scenariusz#s1", "scenariusz#s2"],
         "category": "fakt", "difficulty": "latwe", "kind": "fakt", "false_refusal": False},
        {"id": "b", "question": "Pytanie 2", "expected": "x", "answer": "Brak",
         "cited_sources": [], "expected_sources": [],
         "category": "spoza_zakresu", "difficulty": "brak", "kind": "odmowa_spoza", "correct_refusal": True},
        {"id": "c", "question": "Pytanie 3", "expected": "x", "answer": "Nie wiem",
         "cited_sources": [], "expected_sources": ["scenariusz#s5"],
         "category": "fakt", "difficulty": "latwe", "kind": "fakt", "false_refusal": True},
    ]
    path = tmp_path / "sheet.csv"
    ev.write_grading_sheet({"rows": rows}, path)
    assert path.read_bytes().startswith(b"\xef\xbb\xbf")        # BOM dla Excela
    sheet = load_sheet(path)
    assert sheet[0]["pytanie"] == "Pytanie ąę?" and sheet[0]["cytowane_sceny"] == "1"
    assert sheet[0]["oczekiwane_sceny"] == "1, 2"
    assert [r["ocena_auto"] for r in sheet] == ["", "poprawna_odmowa", "bledna"]

    # bez ocen ręcznych nic nie jest ocenione
    assert summarize_grades(sheet)["overall"]["graded"] == 0
    # ręczne oceny
    sheet[0]["ocena"] = "poprawna"
    sheet[2]["ocena"] = "bledna"
    sheet[2]["przyczyna_bledu"] = "zla_odmowa"
    summary = summarize_grades(sheet)
    assert summary["overall"] == {"n": 3, "graded": 2, "correct": 1, "accuracy": 0.5}
    assert summary["causes"] == {"zla_odmowa": 1}
    assert summary["by_category"]["fakt"]["accuracy"] == 0.5
    # z --use-auto brakująca ocena (b) jest uzupełniona podpowiedzią
    auto = summarize_grades(sheet, use_auto=True)
    assert auto["overall"]["graded"] == 3 and auto["correct_refusals"] == 1


def test_invalid_grade_is_rejected():
    with pytest.raises(ValueError):
        final_grade({"id": "x", "ocena": "dobrze"})
    with pytest.raises(ValueError):
        summarize_grades([{"id": "x", "kategoria": "k", "trudnosc": "t",
                           "ocena": "bledna", "przyczyna_bledu": "nie wiem"}])


def test_on_row_callback_and_partial_results(monkeypatch):
    """Postęp: on_row woła się po każdym pytaniu; z samych wierszy da się złożyć wynik częściowy."""
    items = [item(id="a"), item(id="b")]
    monkeypatch.setattr(
        ev, "retrieve", lambda q, s, k=None, collection=None: [fake_hit("abc", ["scenariusz#s1"])]
    )
    seen = []
    res = ev.evaluate(
        items, SimpleNamespace(top_k=5), on_row=lambda i, n, row: seen.append((i, n, row["id"]))
    )
    assert seen == [(1, 2, "a"), (2, 2, "b")]
    partial = ev.results_from_rows(res["rows"][:1])
    assert partial["summary"]["questions"] == 1 and set(partial["by_difficulty"]) == {"latwe"}


def test_spelled_numbers():
    """Model często pisze liczby słownie – count_ok musi je rozpoznawać."""
    assert ev.numbers_in("Są trzy sceny.") == {3}
    assert ev.numbers_in("dwadzieścia osiem scen") == {28}
    assert ev.numbers_in("w dwudziestu ośmiu scenach") == {28}
    assert ev.numbers_in("Jest ich czterech: 13, 19") == {4, 13, 19}
    assert ev.numbers_in("jeden z ministrantów") == set()  # „jeden” to zaimek, nie liczba
    assert ev.count_ok("W scenariuszu są trzy sceny pod blokiem.", [3])
    assert not ev.count_ok("Są dwie sceny.", [3])


def test_rescore_changes_only_auto_metrics():
    gold = item(id="q", tags=["agregacja", "trudne", "liczba"], numbers=[3])
    data = {"rows": [{"id": "q", "answer": "Są trzy sceny.", "category": "agregacja",
                      "difficulty": "trudne", "count_ok": False}]}
    out = ev.rescore(data, [gold])
    assert data["rows"][0]["answer"] == "Są trzy sceny."      # odpowiedź bez zmian
    assert data["rows"][0]["count_ok"] is True                # ocena automatyczna przeliczona
    assert out["summary"]["count_ok"] == 1.0
