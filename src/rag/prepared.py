"""Gotowe chunki z `prepare_data.py` (JSONL) – bez parsowania i chunkingu w starterze.

Format rekordu: {"id", "dokument", "zrodlo", "tekst", "meta": {...}}. Każdy rekord to jeden
gotowy chunk (jedna scena albo część długiej sceny), więc pomijamy `normalize_blocks`
i `chunk_blocks`.

Jedynym źródłem wiedzy RAG jest SCENARIUSZ. Pozostałe dokumenty (tabela inscenizacyjna,
rekwizyty, grafiki, spis obiektów) nie są chunkowane – służą tylko do budowy złotego zbioru.

`source_ids` są niezależne od chunkingu i odwołują się do SCEN: `scenariusz#s12`. Długa scena
podzielona na części ma we wszystkich częściach to samo id, więc recall@k liczy się per scena.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from rag.chunking import Chunk


def source_ids_for_prepared(rec: dict[str, Any]) -> list[str]:
    """Identyfikator źródła chunka scenariusza: `scenariusz#s<numer sceny>`."""
    return [f"scenariusz#s{rec['meta']['scena']}"]


def _label_fields(rec: dict[str, Any]) -> dict[str, Any]:
    """`page` i `section` – z nich `Hit.label()` składa np. `scenariusz, s. 14 › scena 12`."""
    meta = rec.get("meta", {})
    return {"page": meta.get("strona"), "section": f"scena {meta['scena']}"}


def load_prepared(path: Path) -> list[Chunk]:
    """Wczytuje JSONL z `prepare_data.py` (chunki scenariusza) jako gotowe `Chunk`-i."""
    chunks: list[Chunk] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        meta = {k: v for k, v in rec.get("meta", {}).items() if v is not None}
        meta.update({k: v for k, v in _label_fields(rec).items() if v is not None})
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
