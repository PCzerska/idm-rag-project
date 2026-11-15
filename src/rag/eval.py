"""Ewaluacja retrievalu (i opcjonalnie generacji) na złotym zbiorze.

Format złotego zbioru (JSONL, jeden obiekt na linię):
    {"id": "q1", "question": "...", "answer": "...", "source_ids": ["scenariusz#s12"],
     "tags": ["fakt_jedna_scena", "latwe", "fakt"], "needs_review": false,
     "elements": ["13A", "19"], "numbers": [4], "split": "dev"}

`source_ids` to identyfikatory źródeł niezależne od chunkingu (patrz `ingest.source_ids_for`).
Pusta lista oznacza pytanie spoza dokumentów – oczekujemy, że model odmówi odpowiedzi.
`tags` = [kategoria, trudność, typ odpowiedzi]; wyniki są raportowane też per kategoria i trudność.
`elements` (lista oczekiwanych elementów) i `numbers` (oczekiwane liczby) służą do automatycznej
oceny pytań typu `lista` i `liczba`; pozostałe pytania ocenia się ręcznie (arkusz CSV).
`split` to część zbioru: `dev` (eksperymenty) albo `test` (tylko pomiar końcowy); `--split`
w `rag eval` wybiera jedną z nich.

Każdy wiersz wyniku zapisuje też `retrieved` – znalezione chunki w kolejności rankingu (źródła +
score), żeby przy ocenie ręcznej odróżnić „retrieval nie znalazł sceny” od „model jej nie użył”.
Plik wyniku zaczyna się od bloku `experiment` (model, embeddingi, chunking odczytany z kolekcji,
commit) – patrz `describe_experiment`.

Metryki retrievalu (liczone tylko dla pytań z niepustym `source_ids`):
- recall@k: jaka część oczekiwanych źródeł pojawiła się w top-k chunkach,
- MRR (Mean Reciprocal Rank): średnia z 1/pozycja pierwszego trafnego chunka (0, gdy brak).
- ctx_chars: ile znaków kontekstu trafia do modelu (top-k chunków) – przy porównywaniu
  strategii chunkingu recall@k ma sens tylko obok tego budżetu (większe chunki = wyższy recall).

Metryki generacji (`--generate`):
Odmowę ustala kod (`generate.is_refusal`: formuła odmowy w 1. zdaniu albo brak cytatów), nie model.
Znaczniki [n] doklejone do odmowy nie liczą się jako cytaty.

- refusal_ok: model odpowiedział na pytanie z dokumentów / odmówił na pytanie spoza nich,
- false_refusal: odsetek odmów na pytania, które MAJĄ odpowiedź w dokumentach (im niżej, tym lepiej),
- correct_refusal: odsetek poprawnych odmów na pytania spoza dokumentów (im wyżej, tym lepiej),
- citation_hit: wśród cytowanych chunków jest oczekiwane źródło (którekolwiek),
- cited_recall: jaka część oczekiwanych źródeł została zacytowana,
- inline_cited: odpowiedź ma w tekście znaczniki [n] przy twierdzeniach,
- count_ok: (pytania `liczba`) oczekiwana liczba występuje w odpowiedzi – heurystyka,
- list_p / list_r / list_f1: (pytania `lista` z numerami scen) zgodność numerów scen w odpowiedzi
  z oczekiwanymi – heurystyka (wyłapuje numery scen z tekstu), do potwierdzenia ręcznie,
- gen_error: model nie dał poprawnej odpowiedzi strukturalnej (np. zepsuty JSON po ponowieniu);
  takie pytanie liczy się jako nieudane (brak odpowiedzi, cytatów i poprawnej odmowy), a przebieg
  idzie dalej,
- latency_s: czas retrieval + generacja.
"""

from __future__ import annotations

import csv
import json
import re
import statistics
import subprocess
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from rag.config import Settings
from rag.generate import answer_question, is_refusal
from rag.retrieve import Hit, retrieve
from rag.structured import StructuredOutputError


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
    split: str = ""

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


def load_golden(path: Path, split: str | None = None) -> list[GoldItem]:
    """Wczytuje złoty zbiór z pliku JSONL (puste linie są pomijane), opcjonalnie tylko część `split`."""
    items = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            items.append(GoldItem(**json.loads(line)))
    if split:
        available = sorted({i.split for i in items if i.split})
        items = [i for i in items if i.split == split]
        if not items:
            raise ValueError(
                f"W {path} nie ma pytań z split={split!r} (dostępne: {', '.join(available) or 'brak'}). "
                "Sprawdź pole `split` w złotym zbiorze albo uruchom eval bez --split."
            )
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
_UNITS = (
    r"(?:zł|zl|lat|groszy|km|kilometr\w*|tys\w*|proc\w*|godz\w*|raz\w*|scen\w*|sztuk\w*|min\w*|%)"
)
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


def results_from_rows(
    rows: list[dict[str, Any]], ks: tuple[int, ...] = DEFAULT_KS
) -> dict[str, Any]:
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
            "split": item.split,
        }
        hits: list[Hit] = retrieve(item.question, settings, k=max_k, collection=collection)
        if generate:
            answerable = bool(item.source_ids)
            try:
                result = answer_question(
                    item.question, settings, k=settings.top_k, collection=collection
                )
            except StructuredOutputError as err:
                row |= failed_generation(item, str(err))
                result = None
        if generate and result is not None:
            cited = {sid for h in result.cited_hits() for sid in h.source_ids}
            row |= {
                "answer": result.answer.answer,
                "citations": result.cited_numbers,
                "cited_sources": sorted(cited),  # surowe – także znaczniki doklejone do odmowy
                "latency_s": result.retrieve_s + result.generate_s,
                "structured_mode": result.mode,
                # Czy odpowiedź ma znaczniki [n] w tekście (tylko dla pytań z odpowiedzią).
                "inline_cited": result.has_inline_citations if answerable else None,
                "gen_error": False,
            }
            row |= answer_scores(item, row)
            row |= auto_scores(item, result.answer.answer)
        row["ctx_chars"] = sum(len(h.text) for h in hits[: settings.top_k])
        # Ranking retrievalu (top-max_k); do kontekstu modelu trafia pierwsze TOP_K z nich.
        row["retrieved"] = [
            {"rank": rank, "source_ids": h.source_ids, "score": round(h.score, 4)}
            for rank, h in enumerate(hits, start=1)
        ]
        if item.source_ids:
            retrieved = [h.source_ids for h in hits]
            for k in ks:
                row[f"recall@{k}"] = recall_at_k(retrieved, item.source_ids, k)
            row["mrr"] = reciprocal_rank(retrieved, item.source_ids)
        rows.append(row)
        if on_row:
            on_row(position, len(items), row)
    return results_from_rows(rows, ks)


def answer_scores(item: GoldItem, row: dict[str, Any]) -> dict[str, Any]:
    """Metryki odmów i cytowań z zapisanej odpowiedzi (`answer`, `citations`, `cited_sources`).

    Odmowę ustala kod: formuła odmowy w pierwszym zdaniu albo brak cytatów. Cytaty odmowy się nie
    liczą – „nie ma informacji [1][2][3]” nie jest trafnym cytowaniem. Używane też w `rescore`.
    """
    refusal_text = is_refusal(row["answer"])
    answered = bool(row["citations"]) and not refusal_text
    cited = set(row["cited_sources"]) if answered else set()
    expected = set(item.source_ids)
    answerable = bool(expected)
    return {
        "refusal_in_text": refusal_text,
        # Odmowa poprawna, gdy pytanie jest spoza dokumentów (pusty source_ids).
        "refusal_ok": answered == answerable,
        "false_refusal": (not answered) if answerable else None,
        "correct_refusal": None if answerable else (not answered),
        "citation_hit": bool(cited & expected) if answerable else None,
        "cited_recall": round(len(cited & expected) / len(expected), 3) if answerable else None,
    }


def failed_generation(item: GoldItem, error: str) -> dict[str, Any]:
    """Wiersz dla pytania, na które model nie dał poprawnej odpowiedzi: liczymy je jako nieudane.

    Nie pomijamy go w średnich – pominięcie zawyżyłoby wynik akurat na trudnych pytaniach.
    """
    answerable = bool(item.source_ids)
    row: dict[str, Any] = {
        "answer": "",
        "error": error[:500],
        "citations": [],
        "cited_sources": [],
        "latency_s": None,
        "structured_mode": None,
        "refusal_ok": False,
        "false_refusal": None,
        "correct_refusal": None if answerable else False,
        "citation_hit": False if answerable else None,
        "cited_recall": 0.0 if answerable else None,
        "inline_cited": False if answerable else None,
        "gen_error": True,
    }
    return row | auto_scores(item, "")


def rescore(
    data: dict[str, Any], items: list[GoldItem], ks: tuple[int, ...] = DEFAULT_KS
) -> dict[str, Any]:
    """Przelicza automatyczne metryki na zapisanym przebiegu – bez ponownego pytania modelu.

    Odpowiedzi modelu zostają te same; zmienia się ocena automatyczna (liczby, listy, odmowy
    i cytowania – np. po poprawce heurystyki). Retrieval (recall, MRR) zostaje bez zmian.
    """
    by_id = {item.id: item for item in items}
    for row in data["rows"]:
        item = by_id.get(row["id"])
        if item is None or "answer" not in row or row.get("gen_error"):
            continue
        for key in ("count_ok", "list_p", "list_r", "list_f1"):
            row.pop(key, None)
        if "citations" in row:  # wiersz z pełnego przebiegu (nie atrapa / stary format)
            row |= answer_scores(item, row)
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
    "gen_error",
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


def to_markdown(
    results: dict[str, Any], title: str, experiment: dict[str, Any] | None = None
) -> str:
    """Tabele wyników w Markdownie (do wklejenia w raport / slajdy)."""
    rows, summary = results["rows"], results["summary"]
    cols = _columns(summary)
    lines = [f"# {title}", ""]
    if experiment:
        lines += [
            "## Eksperyment",
            "",
            experiment_markdown(experiment),
            "## Wyniki per pytanie",
            "",
        ]
    lines.append("| id | pytanie | " + " | ".join(cols) + " |")
    lines.append("|" + "---|" * (len(cols) + 2))
    for r in rows:
        values = " | ".join(format_cell(r.get(c)) for c in cols)
        lines.append(f"| {r['id']} | {r['question']} | {values} |")
    means = " | ".join(f"**{format_cell(summary[c])}**" for c in cols)
    lines.append(f"| **średnia** | {summary['questions']} pytań | {means} |")
    for heading, key in (
        ("Wyniki per kategoria", "by_category"),
        ("Wyniki per trudność", "by_difficulty"),
    ):
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
    "cytowane_sceny", "oczekiwane_sceny", "znalezione_sceny", "ocena_auto", "przyczyna_auto",
    "ocena", "przyczyna_bledu", "uwagi",
]  # fmt: skip


def auto_grade(row: dict[str, Any]) -> tuple[str, str]:
    """Wstępna ocena i przyczyna tam, gdzie wynik jest jednoznaczny; w pozostałych przypadkach pusto."""
    if row.get("gen_error"):
        return "bledna", ""
    if row.get("false_refusal"):
        return "bledna", "zla_odmowa"
    if row.get("correct_refusal") is True:
        return "poprawna_odmowa", ""
    if row.get("correct_refusal") is False:
        return "bledna", "halucynacja"
    if row.get("count_ok") is not None:
        return ("poprawna", "") if row["count_ok"] else ("bledna", "")
    return "", ""


def _scene(source_id: str) -> str:
    return source_id.split("#s")[-1]


def _retrieved_scenes(row: dict[str, Any], top_k: int | None) -> str:
    """Sceny z kontekstu modelu w kolejności rankingu, np. „14, 3, 5” (puste dla starych wyników)."""
    hits = row.get("retrieved", [])[:top_k] if top_k else row.get("retrieved", [])
    return ", ".join(_scene(s) for h in hits for s in h["source_ids"])


def write_grading_sheet(results: dict[str, Any], path: Path, top_k: int | None = None) -> None:
    """CSV (średnik, UTF-8 z BOM – otwiera się w Excelu) do ręcznej oceny odpowiedzi.

    Kolumny `ocena` (poprawna / bledna / poprawna_odmowa) i `przyczyna_bledu`
    (brak_fragmentu / blad_ekstrakcji / blad_cypher / halucynacja / zla_odmowa) wypełnia człowiek;
    `ocena_auto` to tylko podpowiedź. `znalezione_sceny` = top-k chunków, które dostał model
    (`brak_fragmentu`, gdy nie ma wśród nich oczekiwanej sceny). Podsumowanie: `uv run rag grade`.
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
                    "odpowiedz_systemu": r.get("answer", "")
                    or (f"[BŁĄD GENERACJI] {r['error']}" if r.get("error") else ""),
                    "cytowane_sceny": ", ".join(_scene(s) for s in r.get("cited_sources", [])),
                    "oczekiwane_sceny": ", ".join(_scene(s) for s in r.get("expected_sources", [])),
                    "znalezione_sceny": _retrieved_scenes(r, top_k),
                    "ocena_auto": grade,
                    "przyczyna_auto": cause,
                    "ocena": "",
                    "przyczyna_bledu": "",
                    "uwagi": "",
                }
            )


def git_commit() -> str | None:
    """Skrócony hash commita (z dopiskiem `-dirty` przy niezacommitowanych zmianach) albo None."""
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    return f"{commit}-dirty" if dirty else commit


def describe_experiment(
    settings: Settings,
    index: dict[str, Any],
    golden: str,
    split: str | None = None,
    generate: bool = False,
) -> dict[str, Any]:
    """Opis eksperymentu zapisywany przy każdym wyniku: CO mierzyliśmy, żeby wyniki były porównywalne.

    `index` to wynik `store.describe_collection` – chunking i model embeddingów odczytane z kolekcji
    (to, co faktycznie przeszukujemy), a nie z `.env`.
    """
    chunking = index.get("chunking", {})
    return {
        "golden": golden,
        "split": split or "wszystkie",
        "collection": settings.collection,
        "chunking": "+".join(sorted(chunking)) or "?",
        "chunks": index.get("points"),
        "embed_model": settings.embed_model,
        "embed_query_instruction": settings.embed_query_instruction,
        "index_embed_model": "+".join(sorted(index.get("embed_model", {}))) or "?",
        "top_k": settings.top_k,
        "llm_model": settings.llm_model if generate else None,
        "llm_temperature": settings.llm_temperature if generate else None,
        "llm_reasoning_effort": settings.llm_reasoning_effort if generate else None,
        "structured_output": settings.structured_output if generate else None,
        "git_commit": git_commit(),
    }


def structured_modes(rows: list[dict[str, Any]]) -> dict[str, int] | None:
    """Ile odpowiedzi dał każdy tryb structured output (np. {"prompt": 119, "błąd": 1})."""
    modes: dict[str, int] = {}
    for r in rows:
        if "answer" in r:
            name = r.get("structured_mode") or "błąd"
            modes[name] = modes.get(name, 0) + 1
    return modes or None


def experiment_label(experiment: dict[str, Any], golden: str) -> str:
    """Nazwa pliku wyniku: `<zbiór>[_<split>]_<kolekcja>_<chunking>_<embedding>[_<LLM>]`."""

    def slug(value: Any) -> str:
        return re.sub(r"[^\w.-]+", "-", str(value)).strip("-")

    parts = [Path(golden).stem]
    if experiment["split"] != "wszystkie":
        parts.append(experiment["split"])
    parts += [experiment["collection"], experiment["chunking"], experiment["embed_model"]]
    if experiment.get("llm_model"):
        parts.append(experiment["llm_model"])
    return "_".join(slug(p) for p in parts)


def experiment_markdown(experiment: dict[str, Any]) -> str:
    """Tabela „Eksperyment” na początek pliku .md."""
    names = {
        "golden": "złoty zbiór",
        "split": "część zbioru",
        "collection": "kolekcja",
        "chunking": "chunking (z kolekcji)",
        "chunks": "liczba chunków",
        "embed_model": "model embeddingów",
        "embed_query_instruction": "query instruction",
        "index_embed_model": "embeddingi w kolekcji",
        "top_k": "top-k (kontekst)",
        "llm_model": "LLM",
        "llm_temperature": "temperature",
        "llm_reasoning_effort": "reasoning effort",
        "structured_output": "structured output",
        "structured_modes": "użyte tryby structured output",
        "git_commit": "commit",
    }
    lines = ["| parametr | wartość |", "|---|---|"]
    for key, value in experiment.items():
        if value is not None:
            lines.append(f"| {names.get(key, key)} | `{value}` |")
    return "\n".join(lines) + "\n"


def save_results(
    results: dict[str, Any],
    settings: Settings,
    out_dir: Path,
    label: str,
    experiment: dict[str, Any] | None = None,
) -> Path:
    """Zapisuje wyniki do `eval/results/<timestamp>_<label>.json` (+ `.md`, + arkusz `_ocena.csv`)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = out_dir / f"{stamp}_{label}.json"
    config = asdict(settings) | {"llm_api_key": "***", "embed_api_key": "***"}
    head = {"experiment": experiment} if experiment else {}
    path.write_text(
        json.dumps({**head, "config": config, **results}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    title = f"{label} · {stamp}"
    path.with_suffix(".md").write_text(to_markdown(results, title, experiment), encoding="utf-8")
    if any("answer" in r for r in results["rows"]):
        write_grading_sheet(results, path.with_name(f"{stamp}_{label}_ocena.csv"), settings.top_k)
    return path
