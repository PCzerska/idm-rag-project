import pytest

from rag.generate import Answer, RagResult, best_quote, inline_markers, is_refusal
from rag.retrieve import Hit


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("[2], [3], [4]", [2, 3, 4]),
        ("2, 3", [2, 3]),
        (["[1]", "3"], [1, 3]),
        ([1, 2], [1, 2]),
        (4, [4]),
        (None, []),
        ("", []),
    ],
)
def test_repair_citations(raw, expected):
    assert Answer.model_validate({"answer": "x", "citations": raw}).citations == expected


def test_repair_from_json_string_like_qwen_returns():
    raw = '{"answer": "Tekst [2].", "citations": "[2], [3]"}'
    assert Answer.model_validate_json(raw).citations == [2, 3]


def test_inline_markers():
    assert inline_markers("A [2]. B [1], [2]. C [10].") == [2, 1, 10]


def _result(answer: str, citations: list[int], n_hits: int = 3) -> RagResult:
    hits = [
        Hit(text=f"chunk {i}", score=0.5, metadata={"source": f"f{i}.md"}) for i in range(n_hits)
    ]
    return RagResult("q", Answer(answer=answer, citations=citations), hits, "tool", 0.1, 1.0)


def test_cited_numbers_merge_inline_and_list_and_drop_invalid():
    r = _result("Coś [1]. Coś innego [7].", citations=[3])
    assert r.cited_numbers == [1, 3]
    assert r.has_inline_citations
    assert [i for i, _ in r.retrieved_only()] == [2]


def test_refusal_is_empty_citations():
    r = _result("W dokumentach nie ma tej informacji.", citations=[])
    assert not r.answered_from_context
    assert not r.has_inline_citations


# Przykłady z E1 (qwen3.5:4b, tryb prompt): odmowy z doklejonymi znacznikami i częściowe odpowiedzi.
@pytest.mark.parametrize(
    "text, refusal",
    [
        ("W podanym kontekście nie ma informacji o nazwisku Księdza Proboszcza [1][2][3].", True),
        ("W tekście nie ma informacji o nazwie pieśni [1].", True),
        ("W scenie 49 Filip czyta z Lekcjonarza, a konkretny fragment nie jest podany [5].", True),
        ("W podanym kontekście nie są podane prawdziwe imiona tych postaci [1].", True),
        ("Dokument nie zawiera informacji o tym. Inne zdanie [2].", True),
        ("Nie wiem.", True),
        # zastrzeżenie dopiero w drugim zdaniu = częściowa odpowiedź, nie odmowa
        (
            "Procesja pędzi przez osiedle [1]. Dokument nie zawiera informacji o zmianie kierunku.",
            False,
        ),
        ("Scena 84B ma adnotację [4]. W pozostałych scenach nie ma informacji o tym.", False),
        ("Filip rzuca kamieniami w okno [2].", False),
        ("Nie pali Filip, tylko Kurczak [1].", False),  # korekta fałszywej przesłanki
        # „nie jest określony / nie podaje” bez słowa o źródle = zwykły opis (E2, q080)
        (
            "Po pierwszej akcji suma pieniędzy nie jest określona liczbowo [1]. Po drugiej 7000 zł.",
            False,
        ),
        ("Filip nie podaje ręki Proboszczowi [2].", False),
        ("Kontekst nie wspomina o tym, kto zamyka kościół [1].", True),
    ],
)
def test_is_refusal(text, refusal):
    assert is_refusal(text) is refusal


def test_refusal_with_markers_is_not_an_answer():
    """Małe modele dopisują [1][2] do odmowy – nie liczy się to jako odpowiedź z kontekstu."""
    r = _result("W podanym kontekście nie ma informacji o tym [1][2].", citations=[1, 2])
    assert r.cited_numbers == [1, 2] and r.refused and not r.answered_from_context
    assert _result("Odpowiedź [1].", citations=[1]).answered_from_context


def test_best_quote_picks_overlapping_sentence():
    text = "Qdrant to baza wektorowa. Okno kontekstu ustawiamy na 8192 tokeny. Koniec."
    assert best_quote(text, "Okno kontekstu powinno mieć 8192 tokeny [1].") == (
        "Okno kontekstu ustawiamy na 8192 tokeny."
    )


def test_best_quote_joins_broken_lines_and_skips_headings():
    text = "## Fixed-size\n\nChunking fixed-size tnie tekst na kawałki o stałej\nliczbie znaków. Inne zdanie."
    assert best_quote(text, "fixed-size tnie tekst na kawałki [1]") == (
        "Chunking fixed-size tnie tekst na kawałki o stałej liczbie znaków."
    )
