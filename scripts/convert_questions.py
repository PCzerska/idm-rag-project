#!/usr/bin/env python3
"""Konwersja zestawu pytań (pytania_ministranci.jsonl) do formatu startera + kontrola źródeł.

Wejście : eval/pytania_ministranci.jsonl  (pola: id, kategoria, trudnosc, zbior, pytanie, typ,
          oczekiwane, zrodla[{scena, strona, fraza}], elementy)
Wyjście : eval/pytania.jsonl              (format GoldItem: id, question, answer, source_ids,
          tags, needs_review, elements, numbers, split)

- source_ids = `scenariusz#s<scena>` (tak samo jak `prepared.source_ids_for_prepared`),
- tags = [kategoria, trudnosc, typ] (kategoria jest pierwszym tagiem, jak w starym zestawie),
- split = zbior (`dev` – eksperymenty, `test` – tylko pomiar końcowy; domyślnie `dev`),
- każde źródło ma `fraza` z oryginalnego tekstu sceny. Skrypt sprawdza ją w tekście chunków:
  * sceny nie ma w indeksie, ale fraza jest w innym chunku (np. 117* scalone z 117A)
    -> źródło zostaje przepięte na ten chunk (komunikat „przepięto”),
  * sceny nie ma w indeksie i frazy nie ma nigdzie (np. scena usunięta)
    -> needs_review=true,
  * scena jest, ale frazy w jej chunku nie ma (parser coś zgubił lub zmienił tekst)
    -> ostrzeżenie „fraza nie znaleziona” (needs_review bez zmian; sprawdź ręcznie).

  uv run python scripts/convert_questions.py --force
  (Windows: najpierw `chcp 65001`, żeby polskie znaki w komunikatach wyświetlały się poprawnie)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REFUSAL_CATEGORIES = {"spoza_zakresu", "brak_w_dokumentach"}


def read_jsonl(path: Path) -> list[dict]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def norm(text: str) -> str:
    """Małe litery, spójne cudzysłowy, jedna spacja – do porównywania fraz z tekstem chunków."""
    text = (
        text.replace("\u2019", "'")
        .replace("\u201e", '"')
        .replace("\u201d", '"')
        .replace("\u201c", '"')
    )
    return re.sub(r"\s+", " ", text).strip().lower()


def scene_labels(label: str) -> list[str]:
    """„26/27” -> ["26", "27"]; zwykła etykieta zostaje bez zmian."""
    return [part.strip() for part in str(label).split("/") if part.strip()]


def sid(scene: str) -> str:
    return f"scenariusz#s{scene}"


class Index:
    """Tekst chunków pogrupowany po scenach (długa scena = kilka chunków z tym samym id)."""

    def __init__(self, rows: list[dict]):
        self.order: list[str] = []
        texts: dict[str, list[str]] = defaultdict(list)
        for r in rows:
            scene = str(r["meta"]["scena"])
            if scene not in texts:
                self.order.append(scene)
            texts[scene].append(r.get("tekst", ""))
        self.text = {s: norm(" ".join(parts)) for s, parts in texts.items()}

    def has(self, scene: str) -> bool:
        return scene in self.text

    def contains(self, scene: str, phrase: str, regex: bool) -> bool:
        body = self.text.get(scene, "")
        return bool(re.search(phrase, body)) if regex else norm(phrase) in body

    def find(self, phrase: str, regex: bool) -> list[str]:
        return [s for s in self.order if self.contains(s, phrase, regex)]


def expected_numbers(q: dict) -> list[int]:
    """Oczekiwane liczby dla pytań `liczba`: pierwsza liczba odpowiedzi („13: 11, 24…”) albo,
    gdy odpowiedź nie zaczyna się od liczby (WN/PL/mieszane), liczby z pola `elementy`."""
    if q["typ"] != "liczba":
        return []
    first = re.match(r"\s*(\d+)\b", q["oczekiwane"])
    if first:
        return [int(first.group(1))]
    return [int(e) for e in q.get("elementy", []) if str(e).isdigit()]


def convert(q: dict, index: Index | None, fallback: list[str], notes: dict) -> dict:
    ids: list[str] = []
    missing: list[str] = []

    def add(scene: str) -> None:
        if sid(scene) not in ids:
            ids.append(sid(scene))

    for z in q["zrodla"]:
        phrase, regex = z.get("fraza"), bool(z.get("regex"))
        for scene in scene_labels(z["scena"]):
            if index is None or index.has(scene):
                add(scene)
                if index is not None and phrase and not index.contains(scene, phrase, regex):
                    notes["phrase"].append((q["id"], scene, phrase))
                continue
            found = index.find(phrase, regex) if phrase else []
            if 1 <= len(found) <= 2:
                for other in found:
                    add(other)
                notes["remap"].append((q["id"], scene, found))
            else:
                missing.append(scene)

    if not ids and not missing and q["kategoria"] not in REFUSAL_CATEGORIES:
        ids = [sid(s) for s in fallback]
        notes["fallback"].append(q["id"])
    for scene in missing:
        add(scene)
    return {
        "id": q["id"],
        "question": q["pytanie"],
        "answer": q["oczekiwane"],
        "source_ids": ids,
        "tags": [q["kategoria"], q["trudnosc"], q["typ"]],
        "needs_review": bool(missing),
        "elements": [str(e) for e in q.get("elementy", [])],
        "numbers": expected_numbers(q),
        "split": q.get("zbior", "dev"),
        "_missing": missing,
    }


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src", default="eval/pytania_ministranci.jsonl")
    ap.add_argument("--out", default="eval/pytania.jsonl")
    ap.add_argument("--index", default="data/processed/chunks.jsonl", help="wynik prepare_data.py")
    ap.add_argument("--force", action="store_true", help="nadpisz istniejące --out")
    a = ap.parse_args()

    src, out, idx_path = ROOT / a.src, ROOT / a.out, ROOT / a.index
    if out.exists() and not a.force:
        sys.exit(
            f"{out} już istnieje – zmień mu nazwę (np. na pytania_stare.jsonl) albo użyj --force."
        )
    items = read_jsonl(src)

    index: Index | None = None
    fallback = ["1", "122"]
    if idx_path.exists():
        rows = read_jsonl(idx_path)
        index = Index(rows)
        fallback = [index.order[0], index.order[-1]]
        print(f"Indeks: {len(rows)} rekordów, {len(index.order)} różnych scen")
        print("sceny w indeksie:", ", ".join(index.order), "\n")
    else:
        print(f"UWAGA: brak {idx_path} – pomijam kontrolę źródeł.\n")

    notes: dict = {"remap": [], "phrase": [], "fallback": []}
    converted = [convert(q, index, fallback, notes) for q in items]
    flagged = [(r["id"], r["_missing"], r["question"]) for r in converted if r["_missing"]]
    for r in converted:
        r.pop("_missing")
    out.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in converted) + "\n", encoding="utf-8"
    )

    print(f"Zapisano {len(converted)} pytań -> {out}")
    print("trudność :", dict(Counter(r["tags"][1] for r in converted)))
    print("kategoria:", dict(Counter(r["tags"][0] for r in converted)))
    print("zbiór    :", dict(Counter(r["split"] for r in converted)))
    for qid in notes["fallback"]:
        print(f"UWAGA {qid}: brak źródeł (pytanie o cały scenariusz) – przyjęto {fallback}")
    if notes["remap"]:
        print(f"\nPrzepięto źródła (sceny bez własnego chunka): {len(notes['remap'])}")
        for qid, scene, found in notes["remap"]:
            print(f"  {qid}: scena {scene} -> chunk sceny {', '.join(found)}")
    if flagged:
        print(f"\nPytania ze źródłami spoza indeksu (needs_review=true): {len(flagged)}")
        for qid, missing, question in flagged:
            print(f"  {qid}: brak {missing} | {question[:70]}")
    if notes["phrase"]:
        print(f"\nFraza nie znaleziona w chunku sceny (sprawdź ręcznie): {len(notes['phrase'])}")
        for qid, scene, phrase in notes["phrase"]:
            print(f"  {qid}: scena {scene} | „{phrase[:60]}”")
    if index is not None and not (flagged or notes["phrase"]):
        print("\nWszystkie źródła i frazy zgodne z indeksem.")


if __name__ == "__main__":
    main()
