"""Ewaluacja retrievalu (i opcjonalnie generacji) na złotym zbiorze.

Format złotego zbioru (JSONL, jeden obiekt na linię):
    {"id": "q1", "question": "...", "answer": "...", "source_ids": ["scenariusz#s12"],
     "tags": ["fakt_jedna_scena", "latwe", "fakt"], "needs_review": false,
     "elements": ["13A", "19"], "numbers": [4]}

`source_ids` to identyfikatory źródeł niezależne od chunkingu (patrz `ingest.source_ids_for`).
Pusta lista oznacza pytanie spoza dokumentów – oczekujemy, że model odmówi odpowiedzi.
`tags` = [kategoria, trudność, typ odpowiedzi]; wyniki są raportowane też per kategoria i trudność.
`elements` (lista oczekiwanych elementów) i `numbers` (oczekiwane liczby) służą do automatycznej
oceny pytań typu `lista` i `liczba`; pozostałe pytania ocenia się ręcznie (arkusz CSV).

Metryki retrievalu (liczone tylko dla pytań z niepustym `source_ids`):
- recall@k: jaka część oczekiwanych źródeł pojawiła się w top-k chunkach,
- MRR (Mean Reciprocal Rank): średnia z 1/pozycja pierwszego trafnego chunka (0, gdy brak).
- ctx_chars: ile znaków kontekstu trafia do modelu (top-k chunków) – przy porównywaniu
  strategii chunkingu recall@k ma sens tylko obok tego budżetu (większe chunki = wyższy recall).

Metryki generacji (`--generate`):
- refusal_ok: model odpowiedział na pytanie z dokumentów / odmówił na pytanie spoza nich,
- false_refusal: odsetek odmów na pytania, które MAJĄ odpowiedź w dokumentach (im niżej, tym lepiej),
- correct_refusal: odsetek poprawnych odmów na pytania spoza dokumentów (im wyżej, tym lepiej),
- citation_hit: wśród cytowanych chunków jest oczekiwane źródło (którekolwiek),
- cited_recall: jaka część oczekiwanych źródeł została zacytowana,
- inline_cited: odpowiedź ma w tekście znaczniki [n] przy twierdzeniach,
- count_ok: (pytania `liczba`) oczekiwana liczba występuje w odpowiedzi – heurystyka,
- list_p / list_r / list_f1: (pytania `lista` z numerami scen) zgodność numerów scen w odpowiedzi
  z oczekiwanymi – heurystyka (wyłapuje numery scen z tekstu), do potwierdzenia ręcznie,
- latency_s: czas retrieval + generacja.
"""

from __future__ import annotations

import csv
import json
import re
import statistics
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from rag.config import Settings
from rag.generate import answer_question
from rag.retrieve import Hit, retrieve


@dataclass
class GoldItem:
    id: str
    question: str
    answer: str
    source_ids: list[str]
    tags: list[str] = field(default_factory=list)
    needs_review: bool = False
    elements: list[str] = field(default_factory=list)
    numbers: list[int] = field(default_factory=list)

    @property
    def category(self) -> str:
        return self.tags[0] if self.tags else "-"

    @property
    def difficulty(self) -> str:
        return self.tags[1] if len(self.tags) > 1 else "-"

    @property
    def kind(self) -> str:
        """Typ odpowiedzi: fakt / lista / liczba / wyjasnienie / odmowa_* (trzeci tag)."""
        return self.tags[2] if len(self.tags) > 2 else "-"


def load_golden(path: Path) -> list[GoldItem]:
    """Wczytuje złoty zbiór z pliku JSONL (puste linie są pomijane)."""
    items = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            items.append(GoldItem(**json.loads(line)))
    return items


# --------------------------------------------------------------------------- metryki


def recall_at_k(retrieved: list[list[str]], relevant: list[str], k: int) -> float:
    """Ułamek oczekiwanych źródeł obecnych w top-k (każdy chunk może mieć kilka source_ids)."""
    found = {sid for ids in retrieved[:k] for sid in ids}
    return len(found & set(relevant)) / len(relevant)


def reciprocal_rank(retrieved: list[list[str]], relevant: list[str]) -> float:
    """1 / pozycja pierwszego chunka zawierającego którekolwiek oczekiwane źródło."""
    for rank, ids in enumerate(retrieved, start=1):
        if set(ids) & set(relevant):
            return 1.0 / rank
    return 0.0


# ------------------------------------------------- automatyczna ocena liczb i list scen

_MARKER = re.compile(r"\[\d+\]")
_INTEGER = re.compile(r"(?<![\w.,])\d+(?!\w)")
_SCENE_LABEL = re.compile(r"^\d{1,3}[A-Z]?\*{0,2}$")
# numer sceny w tekście; pomijamy liczby przy jednostkach („12 lat”, „500 zł”, „6 scen”)
_UNITS = r"(?:zł|zl|lat|groszy|km|kilometr\w*|tys\w*|proc\w*|godz\w*|raz\w*|scen\w*|sztuk\w*|min\w*|%)"
_SCENE_TOKEN = re.compile(
    r"(?<![\w*.,#])(\d{1,3}[A-Za-z]?\*{0,2})(?![\w*])(?!\s*" + _UNITS + r"\b)", re.IGNORECASE
)


# Liczby zapisane słownie (model często pisze „trzy sceny”, nie „3 sceny”). Obsługujemy 2–50 w
# najczęstszych odmianach; „jeden/jedna” pomijamy (to też zaimek: „jeden z ministrantów”).
_PL_UNITS = {
    "dwa": 2, "dwie": 2, "dwóch": 2, "dwu": 2, "trzy": 3, "trzech": 3, "cztery": 4, "czterech": 4,
    "pięć": 5, "pięciu": 5, "sześć": 6, "sześciu": 6, "siedem": 7, "siedmiu": 7,
    "osiem": 8, "ośmiu": 8, "dziewięć": 9, "dziewięciu": 9,
}  # fmt: skip
_PL_TEENS = {
    "dziesięć": 10, "dziesięciu": 10, "jedenaście": 11, "jedenastu": 11, "dwanaście": 12,
    "dwunastu": 12, "trzynaście": 13, "trzynastu": 13, "czternaście": 14, "czternastu": 14,
    "piętnaście": 15, "piętnastu": 15, "szesnaście": 16, "szesnastu": 16, "siedemnaście": 17,
    "siedemnastu": 17, "osiemnaście": 18, "osiemnastu": 18, "dziewiętnaście": 19,
    "dziewiętnastu": 19,
}  # fmt: skip
_PL_TENS = {
    "dwadzieścia": 20, "dwudziestu": 20, "trzydzieści": 30, "trzydziestu": 30,
    "czterdzieści": 40, "czterdziestu": 40, "pięćdziesiąt": 50, "pięćdziesięciu": 50,
}  # fmt: skip
_WORD = re.compile(r"[a-ząćęłńóśźż]+")


def spelled_numbers(text: str) -> set[int]:
    """Liczby zapisane słownie, np. „trzy” -> 3, „dwadzieścia osiem” / „dwudziestu ośmiu” -> 28."""
    words = _WORD.findall(text.lower())
    found: set[int] = set()
    i = 0
    while i < len(words):
        word = words[i]
        if word in _PL_TENS:
            value = _PL_TENS[word]
            if i + 1 < len(words) and words[i + 1] in _PL_UNITS:
                value += _PL_UNITS[words[i + 1]]
                i += 1
            found.add(value)
        elif word in _PL_TEENS:
            found.add(_PL_TEENS[word])
        elif word in _PL_UNITS:
            found.add(_PL_UNITS[word])
        i += 1
    return found


def numbers_in(text: str) -> set[int]:
    """Liczby całkowite w tekście: cyframi (bez znaczników [n]) i słownie."""
    digits = {int(n) for n in _INTEGER.findall(_MARKER.sub(" ", text))}
    return digits | spelled_numbers(text)


def scene_refs(text: str) -> set[str]:
    """Numery scen wymienione w tekście, np. {'13A', '19', '117*'} (heurystyka)."""
    return {m.upper() for m in _SCENE_TOKEN.findall(_MARKER.sub(" ", text))}


def count_ok(answer: str, numbers: list[int]) -> bool:
    """Czy WSZYSTKIE oczekiwane liczby występują w odpowiedzi."""
    return bool(numbers) and set(numbers) <= numbers_in(answer)


def list_scores(answer: str, elements: list[str]) -> dict[str, float]:
    """Precision / recall / F1 numerów scen w odpowiedzi względem oczekiwanych (`elements`)."""
    expected = {e.upper() for e in elements}
    found = scene_refs(answer)
    hits = len(found & expected)
    p = hits / len(found) if found else 0.0
    r = hits / len(expected)
    f1 = 2 * p * r / (p + r) if p + r else 0.0
    return {"list_p": round(p, 3), "list_r": round(r, 3), "list_f1": round(f1, 3)}


def auto_scores(item: GoldItem, answer: str) -> dict[str, Any]:
    """Automatyczna ocena tam, gdzie się da: `liczba` -> count_ok, `lista` numerów scen -> P/R/F1."""
    if item.kind == "liczba" and item.numbers:
        return {"count_ok": count_ok(answer, item.numbers)}
    if item.kind == "lista" and item.elements and all(_SCENE_LABEL.match(e) for e in item.elements):
        return list_scores(answer, item.elements)
    return {}


# --------------------------------------------------------------------------- przebieg


DEFAULT_KS = (1, 3, 5)


def results_from_rows(rows: list[dict[str, Any]], ks: tuple[int, ...] = DEFAULT_KS) -> dict[str, Any]:
    """Składa wynik (wiersze + średnie + grupy) z gotowych wierszy – także z przerwanego przebiegu."""
    return {
        "rows": rows,
        "summary": summarize(rows, ks),
        "by_category": group_summary(rows, ks, "category"),
        "by_difficulty": group_summary(rows, ks, "difficulty"),
    }


def evaluate(
    items: list[GoldItem],
    settings: Settings,
    ks: tuple[int, ...] = DEFAULT_KS,
    generate: bool = False,
    collection: str | None = None,
    on_row: Callable[[int, int, dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Liczy metryki dla całego złotego zbioru. Zwraca wyniki per pytanie, średnie i podział na grupy.

    `on_row(numer, razem, wiersz)` jest wołane po każdym pytaniu (pasek postępu, zapis częściowy).
    """
    max_k = max(ks)
    rows: list[dict[str, Any]] = []
    for position, item in enumerate(items, start=1):
        row: dict[str, Any] = {
            "id": item.id,
            "question": item.question,
            "expected": item.answer,
            "expected_sources": item.source_ids,
            "tags": item.tags,
            "category": item.category,
            "difficulty": item.difficulty,
            "kind": item.kind,
        }
        hits: list[Hit] = retrieve(item.question, settings, k=max_k, collection=collection)
        if generate:
            result = answer_question(
                item.question, settings, k=settings.top_k, collection=collection
            )
            cited = {sid for h in result.cited_hits() for sid in h.source_ids}
            expected = set(item.source_ids)
            answerable = bool(expected)
            row |= {
                "answer": result.answer.answer,
                "citations": result.cited_numbers,
                "cited_sources": sorted(cited),
                "latency_s": result.retrieve_s + result.generate_s,
                "structured_mode": result.mode,
                # Odmowa poprawna, gdy pytanie jest spoza dokumentów (pusty source_ids).
                "refusal_ok": result.answered_from_context == answerable,
                "false_refusal": (not result.answered_from_context) if answerable else None,
                "correct_refusal": None if answerable else (not result.answered_from_context),
                "citation_hit": bool(cited & expected) if answerable else None,
                "cited_recall": round(len(cited & expected) / len(expected), 3) if answerable else None,
                # Czy odpowiedź ma znaczniki [n] w tekście (tylko dla pytań z odpowiedzią).
                "inline_cited": result.has_inline_citations if answerable else None,
            }
            row |= auto_scores(item, result.answer.answer)
        row["ctx_chars"] = sum(len(h.text) for h in hits[: settings.top_k])
        if item.source_ids:
            retrieved = [h.source_ids for h in hits]
            for k in ks:
                row[f"recall@{k}"] = recall_at_k(retrieved, item.source_ids, k)
            row["mrr"] = reciprocal_rank(retrieved, item.source_ids)
        rows.append(row)
        if on_row:
            on_row(position, len(items), row)
    return results_from_rows(rows, ks)


def rescore(data: dict[str, Any], items: list[GoldItem], ks: tuple[int, ...] = DEFAULT_KS) -> dict[str, Any]:
    """Przelicza automatyczne metryki (liczby, listy) na zapisanym przebiegu – bez ponownego pytania modelu.

    Odpowiedzi modelu zostają te same; zmienia się tylko ocena automatyczna (np. po poprawce heurystyki).
    """
    by_id = {item.id: item for item in items}
    for row in data["rows"]:
        item = by_id.get(row["id"])
        if item is None or "answer" not in row:
            continue
        for key in ("count_ok", "list_p", "list_r", "list_f1"):
            row.pop(key, None)
        row |= auto_scores(item, row["answer"])
    return results_from_rows(data["rows"], ks)


def _mean(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [float(r[key]) for r in rows if r.get(key) is not None]
    return round(statistics.mean(values), 3) if values else None


METRIC_KEYS = [
    "mrr",
    "ctx_chars",
    "refusal_ok",
    "false_refusal",
    "correct_refusal",
    "citation_hit",
    "cited_recall",
    "inline_cited",
    "count_ok",
    "list_p",
    "list_r",
    "list_f1",
    "latency_s",
]


def summarize(rows: list[dict[str, Any]], ks: tuple[int, ...]) -> dict[str, Any]:
    """Średnie metryk po wszystkich pytaniach (puste metryki pomijamy)."""
    summary: dict[str, Any] = {"questions": len(rows)}
    for key in [*(f"recall@{k}" for k in ks), *METRIC_KEYS]:
        summary[key] = _mean(rows, key)
    return summary


def group_summary(rows: list[dict[str, Any]], ks: tuple[int, ...], by: str) -> dict[str, dict]:
    """Te same średnie osobno dla każdej wartości pola `by` (np. category, difficulty)."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        groups.setdefault(str(r.get(by, "-")), []).append(r)
    return {name: summarize(rs, ks) for name, rs in sorted(groups.items())}


def _columns(summary: dict[str, Any]) -> list[str]:
    return [c for c in summary if c != "questions" and summary[c] is not None]


def to_markdown(results: dict[str, Any], title: str) -> str:
    """Tabele wyników w Markdownie (do wklejenia w raport / slajdy)."""
    rows, summary = results["rows"], results["summary"]
    cols = _columns(summary)
    lines = [f"# {title}", "", "| id | pytanie | " + " | ".join(cols) + " |"]
    lines.append("|" + "---|" * (len(cols) + 2))
    for r in rows:
        values = " | ".join(format_cell(r.get(c)) for c in cols)
        lines.append(f"| {r['id']} | {r['question']} | {values} |")
    means = " | ".join(f"**{format_cell(summary[c])}**" for c in cols)
    lines.append(f"| **średnia** | {summary['questions']} pytań | {means} |")
    for heading, key in (("Wyniki per kategoria", "by_category"), ("Wyniki per trudność", "by_difficulty")):
        groups = results.get(key)
        if not groups:
            continue
        gcols = [c for c in cols if any(g.get(c) is not None for g in groups.values())]
        lines += ["", f"## {heading}", "", "| grupa | n | " + " | ".join(gcols) + " |"]
        lines.append("|" + "---|" * (len(gcols) + 2))
        for name, g in groups.items():
            values = " | ".join(format_cell(g.get(c)) for c in gcols)
            lines.append(f"| {name} | {g['questions']} | {values} |")
    return "\n".join(lines) + "\n"


def format_cell(value: Any) -> str:
    """Wartość metryki jako krótki tekst (tabela w terminalu i w Markdownie)."""
    if value is None:
        return "–"
    if isinstance(value, bool):
        return "✓" if value else "✗"
    if isinstance(value, float):
        return f"{value:.0f}" if value >= 100 else f"{value:.2f}"
    return str(value)


# ----------------------------------------------------------- arkusz do oceny ręcznej

SHEET_COLUMNS = [
    "id", "kategoria", "trudnosc", "typ", "pytanie", "oczekiwane", "odpowiedz_systemu",
    "cytowane_sceny", "oczekiwane_sceny", "ocena_auto", "przyczyna_auto",
    "ocena", "przyczyna_bledu", "uwagi",
]  # fmt: skip


def auto_grade(row: dict[str, Any]) -> tuple[str, str]:
    """Wstępna ocena i przyczyna tam, gdzie wynik jest jednoznaczny; w pozostałych przypadkach pusto."""
    if row.get("false_refusal"):
        return "bledna", "zla_odmowa"
    if row.get("correct_refusal") is True:
        return "poprawna_odmowa", ""
    if row.get("correct_refusal") is False:
        return "bledna", "halucynacja"
    if row.get("count_ok") is not None:
        return ("poprawna", "") if row["count_ok"] else ("bledna", "")
    return "", ""


def write_grading_sheet(results: dict[str, Any], path: Path) -> None:
    """CSV (średnik, UTF-8 z BOM – otwiera się w Excelu) do ręcznej oceny odpowiedzi.

    Kolumny `ocena` (poprawna / bledna / poprawna_odmowa) i `przyczyna_bledu`
    (brak_fragmentu / blad_ekstrakcji / blad_cypher / halucynacja / zla_odmowa) wypełnia człowiek;
    `ocena_auto` to tylko podpowiedź. Podsumowanie: `uv run rag grade <plik>`.
    """
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=SHEET_COLUMNS, delimiter=";")
        writer.writeheader()
        for r in results["rows"]:
            grade, cause = auto_grade(r)
            writer.writerow(
                {
                    "id": r["id"],
                    "kategoria": r.get("category", ""),
                    "trudnosc": r.get("difficulty", ""),
                    "typ": r.get("kind", ""),
                    "pytanie": r["question"],
                    "oczekiwane": r.get("expected", ""),
                    "odpowiedz_systemu": r.get("answer", ""),
                    "cytowane_sceny": ", ".join(s.split("#s")[-1] for s in r.get("cited_sources", [])),
                    "oczekiwane_sceny": ", ".join(s.split("#s")[-1] for s in r.get("expected_sources", [])),
                    "ocena_auto": grade,
                    "przyczyna_auto": cause,
                    "ocena": "",
                    "przyczyna_bledu": "",
                    "uwagi": "",
                }
            )


def save_results(results: dict[str, Any], settings: Settings, out_dir: Path, label: str) -> Path:
    """Zapisuje wyniki do `eval/results/<timestamp>_<label>.json` (+ `.md`, + arkusz `_ocena.csv`)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = out_dir / f"{stamp}_{label}.json"
    config = asdict(settings) | {"llm_api_key": "***", "embed_api_key": "***"}
    path.write_text(
        json.dumps({"config": config, **results}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    title = f"{label} · {settings.llm_model} · {stamp}"
    path.with_suffix(".md").write_text(to_markdown(results, title), encoding="utf-8")
    if any("answer" in r for r in results["rows"]):
        write_grading_sheet(results, path.with_name(f"{stamp}_{label}_ocena.csv"))
    return path

