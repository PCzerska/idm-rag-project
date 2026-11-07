"""Gotowe chunki z `scripts/prepare_data.py` (JSONL) – bez parsowania i chunkingu w starterze.

Format rekordu: {"id", "dokument", "zrodlo", "tekst", "meta": {...}}. Każdy rekord to jeden
gotowy chunk (scena / wiersz listy rekwizytów / dzień zdjęciowy / obiekt), więc pomijamy
`normalize_blocks` i `chunk_blocks`.

`source_ids` są niezależne od chunkingu i odwołują się do SCEN (wymaganie projektu: źródło =
dokument + scena, nie chunk). Chunk z wieloma scenami (lista rekwizytów, dzień zdjęciowy) ma
po jednym id na scenę, więc recall@k liczy się tak samo dla wszystkich dokumentów:
    scenariusz#s12      – scena 12 w scenariuszu
    rekwizyty#s12       – wiersz listy rekwizytów, w którym jest scena 12
    grafiki#s12         – grafiki potrzebne w scenie 12 (lista grafik)
    tabela#s12          – dzień zdjęciowy, w którym jest scena 12
    tabela#d3           – dzień zdjęciowy nr 3 (pytania o datę, np. 21.08)
    obiekty#o10         – obiekt nr 10 ze spisu obiektów
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from rag.chunking import Chunk

# Duże, zagnieżdżone dane tylko dla grafu (E2) – nie ładujemy ich do payloadu Qdranta.
_SKIP_META = {"wiersze", "sumy"}


def source_ids_for_prepared(rec: dict[str, Any]) -> list[str]:
    doc, meta = rec["dokument"], rec.get("meta", {})
    if doc == "scenariusz":
        return [f"scenariusz#s{meta['scena']}"]
    if doc == "rekwizyty":
        return [f"rekwizyty#s{s}" for s in meta.get("sceny", [])] or ["rekwizyty#ogolne"]
    if doc == "grafiki":
        return [f"grafiki#s{meta['scena']}"]
    if doc == "tabela":
        return [f"tabela#d{meta['dzien']}"] + [f"tabela#s{s}" for s in meta.get("sceny", [])]
    if doc == "obiekty":
        return [f"obiekty#o{meta['nr']}"]
    return [doc]


def _label_fields(rec: dict[str, Any]) -> dict[str, Any]:
    """`page` i `section` – z nich `Hit.label()` składa np. `scenariusz, s. 14 › scena 12`."""
    doc, meta = rec["dokument"], rec.get("meta", {})
    if doc == "scenariusz":
        return {"page": meta.get("strona"), "section": f"scena {meta['scena']}"}
    if doc == "rekwizyty":
        sceny = ", ".join(meta.get("sceny", [])) or "ogólne"
        return {"section": f"sceny {sceny}"}
    if doc == "grafiki":
        return {"section": f"scena {meta['scena']}"}
    if doc == "tabela":
        return {"section": f"dzień zdjęciowy {meta['dzien']} ({meta['data']})"}
    if doc == "obiekty":
        return {"section": f"obiekt {meta['nr']}"}
    return {}


def load_prepared(path: Path) -> list[Chunk]:
    """Wczytuje JSONL z `prepare_data.py` jako gotowe `Chunk`-i."""
    chunks: list[Chunk] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        meta = {
            k: v for k, v in rec.get("meta", {}).items() if k not in _SKIP_META and v is not None
        }
        meta.update(_label_fields(rec))
        meta.update(
            source=rec["dokument"],
            source_ids=source_ids_for_prepared(rec),
            chunking="prepared",
            # `Chunk.id` = uuid5(source|chunking|chunk_index) → stabilne id rekordu zamiast
            # pozycji w pliku (ponowny ingest nadpisuje punkty, a dwa pliki nie kolidują).
            chunk_index=rec["id"],
        )
        chunks.append(Chunk(text=rec["tekst"], metadata=meta))
    return chunks
