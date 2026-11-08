"""Testy parsowania scenariusza (screenplay.py) – bez PDF-a, na liniach z pozycją."""

import pytest

from rag.screenplay import (
    Line,
    build_paragraphs,
    group_scenes,
    match_heading,
    parse_heading,
    scene_chunks,
    split_long,
)

# Wcięcia jak w scenariuszu „Ministranci”.
HEAD, ACTION, DIALOG, CUE = 108, 108, 180, 250


def action(text, page=1, top=0.0):
    return Line(page, text, ACTION, top, top + 10)


# --------------------------------------------------------------------------- nagłówki


@pytest.mark.parametrize(
    ("text", "number"),
    [
        ("12. WN. KOŚCIÓŁ. DZIEŃ", "12"),
        ("6A. KLATKA SCHODOWA W BLOKU FILIPA. DZIEŃ", "6A"),
        ("117*. PL. DROGA PRZY KOŚCIELE. DZIEŃ", "117*"),
        ("117** PL. OSIEDLE. DZIEŃ", "117**"),  # w scenariuszu bez kropki po gwiazdkach
    ],
)
def test_match_heading_scene_numbers(text, number):
    assert match_heading(text, HEAD)[0] == number


@pytest.mark.parametrize(
    "text",
    [
        "3 DNI PÓŹNIEJ",  # liczba bez kropki i bez gwiazdki – to nie nagłówek
        "12. Filip wchodzi do kościoła.",  # nie WIELKIMI LITERAMI
        "*(kwestia do rozstrzygnięcia czy Filip ustawia telefon.",  # przypis z gwiazdką
    ],
)
def test_match_heading_rejects_non_headings(text):
    assert match_heading(text, HEAD) is None


def test_match_heading_requires_left_margin():
    assert match_heading("12. WN. KOŚCIÓŁ. DZIEŃ", DIALOG) is None


@pytest.mark.parametrize(
    ("heading", "expected"),
    [
        ("WN. KOŚCIÓŁ. DZIEŃ", ("WN", "KOŚCIÓŁ", "DZIEŃ")),
        ("PL/WN. SAMOCHÓD REKOLEKCJONISTY. DZIEŃ", ("PL/WN", "SAMOCHÓD REKOLEKCJONISTY", "DZIEŃ")),
        ("WN. KOŚCIÓŁ. SALKA MINISTRANTÓW. DZIEŃ", ("WN", "KOŚCIÓŁ. SALKA MINISTRANTÓW", "DZIEŃ")),
        ("UJĘCIE Z TELEFONU FILIPA", (None, "UJĘCIE Z TELEFONU FILIPA", None)),
    ],
)
def test_parse_heading(heading, expected):
    assert parse_heading(heading) == expected


# --------------------------------------------------------------------------- akapity


def test_build_paragraphs_dialog_and_action():
    lines = [
        Line(1, "Gucci odbiera telefon.", ACTION, 0, 10),
        Line(1, "PROBOSZCZ", CUE, 15, 25),
        Line(1, "Gdzie on idzie?", DIALOG, 25, 35),
        Line(1, "Zawróć go.", DIALOG, 35, 45),
        Line(1, "Wikary rusza.", ACTION, 50, 60),
    ]
    assert build_paragraphs(lines) == [
        "Gucci odbiera telefon.",
        "PROBOSZCZ: Gdzie on idzie? Zawróć go.",
        "Wikary rusza.",
    ]


def test_build_paragraphs_gap_starts_new_action_paragraph():
    lines = [action("Linia pierwsza", top=0), action("ciąg dalszy", top=10), action("Nowy", top=40)]
    assert build_paragraphs(lines) == ["Linia pierwsza ciąg dalszy", "Nowy"]


# --------------------------------------------------------------------------- sceny


def test_group_scenes_separates_starred_scenes():
    """Strona 114: 117A (sama wstawka), 117* i 117** – wcześniej sklejane w jedną scenę 117A."""
    lines = [
        Line(114, "117A. UJĘCIE Z TELEFONU FILIPA", HEAD, 0, 10),
        Line(114, "117*. PL. DROGA PRZY KOŚCIELE. DZIEŃ", HEAD, 20, 30),
        Line(114, "Skonsternowani ludzie idą za figurą.", ACTION, 40, 50),
        Line(114, "117** PL. OSIEDLE. DZIEŃ", HEAD, 60, 70),
        Line(114, "Cała procesja pędzi przez osiedle.", ACTION, 80, 90),
        Line(115, "Gucci przyspiesza.", ACTION, 100, 110),
    ]
    scenes = group_scenes(lines)
    assert [s.number for s in scenes] == ["117A", "117*", "117**"]
    assert scenes[0].paragraphs == []
    assert scenes[1].paragraphs == ["Skonsternowani ludzie idą za figurą."]
    assert scenes[1].location == "DROGA PRZY KOŚCIELE"
    assert (scenes[2].page_from, scenes[2].page_to) == (114, 115)


def test_group_scenes_skips_page_numbers_and_text_before_first_scene():
    lines = [
        action("Tekst przed pierwszą sceną"),
        Line(1, "1. WN. SALA. DZIEŃ", HEAD, 10, 20),
        Line(1, "7", 555, 0, 10),  # numer strony w prawym rogu
        action("Treść sceny.", top=30),
    ]
    (scene,) = group_scenes(lines)
    assert scene.paragraphs == ["Treść sceny."]


def test_removed_scene_has_no_chunk():
    lines = [Line(1, "7. USUNIĘTA", HEAD, 0, 10), Line(1, "8. WN. KOŚCIÓŁ. NOC", HEAD, 20, 30)]
    scenes = group_scenes(lines)
    assert [s.removed for s in scenes] == [True, False]
    chunks = scene_chunks(scenes, "s.pdf")
    assert [c["meta"]["scena"] for c in chunks] == ["8"]
    assert chunks[0]["tekst"] == "Scena 8 | WN. KOŚCIÓŁ. NOC | scenariusz, s. 1\n(brak opisu)"


# --------------------------------------------------------------------------- chunki


def test_split_long_keeps_short_scene_whole():
    assert split_long("Scena 1", ["a b", "c"], max_words=5) == ["Scena 1\na b\nc"]


def test_split_long_splits_on_paragraphs_with_header():
    parts = split_long("Scena 1", ["a b c", "d e f", "g"], max_words=4)
    assert parts == ["Scena 1 (część 1/2)\na b c", "Scena 1 (część 2/2)\nd e f\ng"]


def test_scene_chunks_ids_for_long_scene():
    lines = [Line(1, "40A. WN. POKÓJ FILIPA. NOC", HEAD, 0, 10)]
    lines += [action(" ".join(["słowo"] * 500), top=100 * i) for i in range(1, 3)]
    chunks = scene_chunks(group_scenes(lines), "s.pdf")
    assert [c["id"] for c in chunks] == ["scenariusz-40A-1", "scenariusz-40A-2"]
    assert {c["meta"]["scena"] for c in chunks} == {"40A"}
