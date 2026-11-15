"""Walidacja złotego zbioru `eval/pytania.jsonl`: schemat, źródła, rozkład pytań.

Testy pomijają się, gdy plików nie ma (np. na czystym klonie repo).
"""

import json
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
QUESTIONS = ROOT / "eval" / "pytania.jsonl"
INDEX = ROOT / "data" / "processed" / "chunks.jsonl"
REFUSAL_CATEGORIES = {"spoza_zakresu", "brak_w_dokumentach"}

needs_questions = pytest.mark.skipif(not QUESTIONS.exists(), reason="brak eval/pytania.jsonl")


def read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


@pytest.fixture(scope="module")
def items() -> list[dict]:
    return read_jsonl(QUESTIONS)


@needs_questions
def test_schema(items):
    required = {"id", "question", "answer", "source_ids", "tags", "needs_review"}
    for q in items:
        assert required <= set(q), f"{q.get('id')}: brakujące pola {required - set(q)}"
        assert q["question"].strip(), q["id"]
        assert q["answer"].strip(), q["id"]
        assert isinstance(q["source_ids"], list), q["id"]
        assert len(q["tags"]) >= 2, f"{q['id']}: tags = [kategoria, trudnosc, ...]"


@needs_questions
def test_unique_ids_and_questions(items):
    ids = [q["id"] for q in items]
    assert len(set(ids)) == len(ids), "powtórzone id"
    questions = [q["question"] for q in items]
    assert len(set(questions)) == len(questions), "powtórzone pytania"


@needs_questions
def test_dev_distribution(items):
    """Zbiór dev: 30 łatwych, 30 średnich, 40 trudnych, 20 bez odpowiedzi (=120).

    Pytania `test` dopisywane później nie zmieniają dev – stąd filtr po `split`.
    """
    dev = [q for q in items if q.get("split") == "dev"]
    assert Counter(q["tags"][1] for q in dev) == {
        "latwe": 30,
        "srednie": 30,
        "trudne": 40,
        "brak": 20,
    }


@needs_questions
def test_known_categories_and_difficulties(items):
    """Pytania test używają tych samych kategorii i poziomów trudności co dev."""
    dev = [q for q in items if q.get("split") == "dev"]
    categories = {q["tags"][0] for q in dev}
    difficulties = {q["tags"][1] for q in dev}
    for q in items:
        assert q["tags"][0] in categories, f"{q['id']}: nieznana kategoria {q['tags'][0]!r}"
        assert q["tags"][1] in difficulties, f"{q['id']}: nieznana trudność {q['tags'][1]!r}"


@needs_questions
def test_test_split_has_answers_and_refusals(items):
    """Zbiór test (jeśli już jest) musi mieć pytania z odpowiedzią i pytania odmowne."""
    test = [q for q in items if q.get("split") == "test"]
    if not test:
        pytest.skip("brak pytań test")
    assert any(q["source_ids"] for q in test), "test bez pytań z odpowiedzią w scenariuszu"
    assert any(q["tags"][0] in REFUSAL_CATEGORIES for q in test), "test bez pytań odmownych"


@needs_questions
def test_refusals_have_no_sources_and_others_have(items):
    """Pusty source_ids = oczekiwana odmowa (tak liczy to eval.refusal_ok)."""
    for q in items:
        if q["tags"][0] in REFUSAL_CATEGORIES:
            assert q["source_ids"] == [], f"{q['id']}: odmowa nie może mieć źródeł"
        else:
            assert q["source_ids"], f"{q['id']}: pytanie z odpowiedzią bez źródeł"


@needs_questions
def test_split_is_dev_or_test(items):
    """Każde pytanie należy do `dev` (eksperymenty) albo `test` (tylko pomiar końcowy)."""
    for q in items:
        assert q.get("split") in {"dev", "test"}, f"{q['id']}: split={q.get('split')!r}"


@needs_questions
def test_count_questions_have_expected_numbers(items):
    """Pytania typu `liczba` muszą mieć `numbers`, bo z nich eval liczy count_ok."""
    for q in items:
        if len(q["tags"]) > 2 and q["tags"][2] == "liczba":
            assert q.get("numbers"), f"{q['id']}: pytanie typu liczba bez oczekiwanej liczby"


@needs_questions
@pytest.mark.skipif(not INDEX.exists(), reason="brak data/processed/chunks.jsonl")
def test_sources_exist_in_index(items):
    """Źródło spoza indeksu psuje recall@k, więc takie pytanie musi mieć needs_review=true."""
    known = {f"scenariusz#s{r['meta']['scena']}" for r in read_jsonl(INDEX)}
    for q in items:
        missing = [s for s in q["source_ids"] if s not in known]
        if missing:
            assert q["needs_review"], (
                f"{q['id']}: źródła {missing} spoza indeksu, a needs_review=false"
            )
