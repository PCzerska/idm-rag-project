"""Podsumowanie ręcznej oceny odpowiedzi z arkusza CSV (`eval/results/*_ocena.csv`).

Człowiek wypełnia kolumny:
- `ocena`: poprawna / bledna / poprawna_odmowa,
- `przyczyna_bledu` (dla błędnych): brak_fragmentu / blad_ekstrakcji / blad_cypher /
  halucynacja / zla_odmowa.

Poprawne = `poprawna` albo `poprawna_odmowa`. Pytania bez oceny są liczone osobno (nie wchodzą do
dokładności). Z `--use-auto` brakujące oceny uzupełnia kolumna `ocena_auto` (tylko tam, gdzie wynik
był jednoznaczny: odmowy i liczby).
"""

from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path
from typing import Any

GRADES = ("poprawna", "bledna", "poprawna_odmowa")
CORRECT = {"poprawna", "poprawna_odmowa"}
CAUSES = ("brak_fragmentu", "blad_ekstrakcji", "blad_cypher", "halucynacja", "zla_odmowa")


def load_sheet(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f, delimiter=";"))


def final_grade(row: dict[str, str], use_auto: bool = False) -> str | None:
    """Ocena ręczna, a przy `use_auto` bez oceny ręcznej – podpowiedź automatyczna; inaczej None."""
    manual = (row.get("ocena") or "").strip().lower()
    if manual:
        if manual not in GRADES:
            raise ValueError(f"{row.get('id')}: nieznana ocena „{manual}” (dozwolone: {', '.join(GRADES)})")
        return manual
    auto = (row.get("ocena_auto") or "").strip().lower()
    return auto if use_auto and auto in GRADES else None


def _cause(row: dict[str, str], use_auto: bool) -> str:
    cause = (row.get("przyczyna_bledu") or "").strip().lower()
    if not cause and use_auto and not (row.get("ocena") or "").strip():
        cause = (row.get("przyczyna_auto") or "").strip().lower()
    if cause and cause not in CAUSES:
        raise ValueError(f"{row.get('id')}: nieznana przyczyna „{cause}” (dozwolone: {', '.join(CAUSES)})")
    return cause


def _stats(rows: list[dict[str, str]], use_auto: bool) -> dict[str, Any]:
    grades = [final_grade(r, use_auto) for r in rows]
    graded = [g for g in grades if g]
    correct = sum(g in CORRECT for g in graded)
    return {
        "n": len(rows),
        "graded": len(graded),
        "correct": correct,
        "accuracy": round(correct / len(graded), 3) if graded else None,
    }


def summarize_grades(rows: list[dict[str, str]], use_auto: bool = False) -> dict[str, Any]:
    """Dokładność ogółem, per kategoria i per trudność + liczność przyczyn błędów."""

    def by(column: str) -> dict[str, dict[str, Any]]:
        groups: dict[str, list[dict[str, str]]] = {}
        for r in rows:
            groups.setdefault(r.get(column) or "-", []).append(r)
        return {name: _stats(rs, use_auto) for name, rs in sorted(groups.items())}

    causes = Counter(
        _cause(r, use_auto)
        for r in rows
        if final_grade(r, use_auto) == "bledna" and _cause(r, use_auto)
    )
    wrong_without_cause = sum(
        final_grade(r, use_auto) == "bledna" and not _cause(r, use_auto) for r in rows
    )
    return {
        "overall": _stats(rows, use_auto),
        "by_category": by("kategoria"),
        "by_difficulty": by("trudnosc"),
        "causes": dict(causes),
        "wrong_without_cause": wrong_without_cause,
        "correct_refusals": sum(final_grade(r, use_auto) == "poprawna_odmowa" for r in rows),
    }
