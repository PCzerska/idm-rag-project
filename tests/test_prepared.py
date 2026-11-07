"""Testy wczytywania gotowych chunków (prepared.py) – bez sieci."""

import json

from rag.chunking import Chunk
from rag.prepared import load_prepared, source_ids_for_prepared


def _write(tmp_path, records):
    path = tmp_path / "chunks.jsonl"
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records), encoding="utf-8")
    return path


RECORDS = [
    {
        "id": "scenariusz-12",
        "dokument": "scenariusz",
        "zrodlo": "s.pdf",
        "tekst": "Scena 12 ...",
        "meta": {"scena": "12", "strona": 14, "obiekt": "SZKOŁA", "pora": None},
    },
    {
        "id": "rekwizyty-03",
        "dokument": "rekwizyty",
        "zrodlo": "r.docx",
        "tekst": "Rekwizyty ...",
        "meta": {"sceny": ["3", "4"], "obiekt": "BUS"},
    },
    {
        "id": "tabela-dzien-3",
        "dokument": "tabela",
        "zrodlo": "t.pdf",
        "tekst": "Dzień 3 ...",
        "meta": {
            "dzien": "3",
            "data": "21.08.2024",
            "sceny": ["97", "59"],
            "wiersze": [{"x": 1}],
            "sumy": [],
        },
    },
    {
        "id": "obiekt-10",
        "dokument": "obiekty",
        "zrodlo": "o.pdf",
        "tekst": "Obiekt 10 ...",
        "meta": {"nr": 10, "obiekt": "Kościół"},
    },
]


def test_source_ids_are_scene_based():
    ids = [source_ids_for_prepared(r) for r in RECORDS]
    assert ids[0] == ["scenariusz#s12"]
    assert ids[1] == ["rekwizyty#s3", "rekwizyty#s4"]
    assert ids[2] == ["tabela#d3", "tabela#s97", "tabela#s59"]
    assert ids[3] == ["obiekty#o10"]


def test_load_prepared_metadata_and_label(tmp_path):
    chunks = load_prepared(_write(tmp_path, RECORDS))
    assert all(isinstance(c, Chunk) for c in chunks)
    scena, rek, tab, obj = chunks
    assert scena.metadata["source"] == "scenariusz"
    assert scena.metadata["page"] == 14 and scena.metadata["section"] == "scena 12"
    assert "pora" not in scena.metadata  # None nie trafia do payloadu
    assert rek.metadata["section"] == "sceny 3, 4"
    assert tab.metadata["section"] == "dzień zdjęciowy 3 (21.08.2024)"
    assert "wiersze" not in tab.metadata and "sumy" not in tab.metadata
    assert obj.metadata["section"] == "obiekt 10"


def test_ids_are_unique_and_stable(tmp_path):
    path = _write(tmp_path, RECORDS)
    first = [c.id for c in load_prepared(path)]
    assert len(set(first)) == len(first)
    assert first == [c.id for c in load_prepared(path)]  # ponowny ingest → te same id


def test_skips_blank_lines(tmp_path):
    path = tmp_path / "c.jsonl"
    path.write_text(json.dumps(RECORDS[0], ensure_ascii=False) + "\n\n", encoding="utf-8")
    assert len(load_prepared(path)) == 1
