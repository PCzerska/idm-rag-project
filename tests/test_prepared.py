"""Testy wczytywania gotowych chunków (prepared.py) – bez sieci."""

import json

from rag.chunking import Chunk
from rag.prepared import load_prepared, source_ids_for_prepared


def _write(tmp_path, records):
    path = tmp_path / "chunks.jsonl"
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records), encoding="utf-8")
    return path


SCENE_12 = {
    "id": "scenariusz-12",
    "dokument": "scenariusz",
    "zrodlo": "s.pdf",
    "tekst": "Scena 12 ...",
    "meta": {"scena": "12", "strona": 14, "obiekt": "SZKOŁA", "pora": None},
}
# Długa scena podzielona przez prepare_data.py na dwie części (to samo źródło = ta sama scena).
SCENE_40A_PARTS = [
    {
        "id": f"scenariusz-40A-{i}",
        "dokument": "scenariusz",
        "zrodlo": "s.pdf",
        "tekst": f"Scena 40A (część {i}/2) ...",
        "meta": {"scena": "40A", "strona": 46, "obiekt": "POKÓJ FILIPA", "pora": "NOC"},
    }
    for i in (1, 2)
]


def test_source_ids_are_scene_based():
    assert source_ids_for_prepared(SCENE_12) == ["scenariusz#s12"]
    # Części jednej sceny mają to samo źródło → recall@k liczy się per scena.
    assert {tuple(source_ids_for_prepared(r)) for r in SCENE_40A_PARTS} == {("scenariusz#s40A",)}


def test_load_prepared_metadata_and_label(tmp_path):
    (chunk,) = load_prepared(_write(tmp_path, [SCENE_12]))
    assert isinstance(chunk, Chunk)
    assert chunk.metadata["source"] == "scenariusz"
    assert chunk.metadata["source_ids"] == ["scenariusz#s12"]
    assert chunk.metadata["page"] == 14 and chunk.metadata["section"] == "scena 12"
    assert chunk.metadata["obiekt"] == "SZKOŁA"
    assert "pora" not in chunk.metadata  # None nie trafia do payloadu


def test_ids_are_unique_and_stable(tmp_path):
    path = _write(tmp_path, [SCENE_12, *SCENE_40A_PARTS])
    first = [c.id for c in load_prepared(path)]
    assert len(set(first)) == len(first)  # części tej samej sceny to osobne punkty w Qdrancie
    assert first == [c.id for c in load_prepared(path)]  # ponowny ingest → te same id


def test_skips_blank_lines(tmp_path):
    path = tmp_path / "c.jsonl"
    path.write_text(json.dumps(SCENE_12, ensure_ascii=False) + "\n\n", encoding="utf-8")
    assert len(load_prepared(path)) == 1
