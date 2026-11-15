"""Generacja odpowiedzi na podstawie znalezionych chunków (część „G” w RAG).

Model dostaje ponumerowane fragmenty kontekstu [1], [2], ... i:
- wstawia znaczniki [n] w tekście odpowiedzi przy każdym twierdzeniu,
- zwraca listę numerów cytowanych fragmentów (`citations`).
Mapowanie numer → źródło (plik, strona, cytat) robimy w kodzie, więc model nie może
„wymyślić” nazwy pliku, numeru strony ani treści cytatu.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field, field_validator

from rag.config import Settings
from rag.llm import llm_client
from rag.retrieve import Hit, retrieve
from rag.structured import complete_structured

SYSTEM_PROMPT = """Jesteś asystentem odpowiadającym na pytania WYŁĄCZNIE na podstawie podanego kontekstu.
Zasady:
- Odpowiadaj po polsku, zwięźle i konkretnie.
- Przy KAŻDYM twierdzeniu wstaw w tekście numer fragmentu w nawiasach kwadratowych, np. „... 8192 tokeny [2].”
- W polu citations podaj listę liczb – numery wszystkich użytych fragmentów, np. [2, 3].
- Jeśli kontekst nie zawiera odpowiedzi, napisz wprost, że w dokumentach nie ma tej informacji,
  i zostaw pustą listę citations. Nie zgaduj.
"""

_MARKER = re.compile(r"\[(\d+)\]")


class Answer(BaseModel):
    """Schemat odpowiedzi (z niego generujemy JSON Schema dla structured output).

    Celowo tylko dwa pola – im prostszy schemat, tym rzadziej mały model się myli.
    Odmowę rozpoznaje kod (`is_refusal` + brak cytatów), a nie osobna flaga od modelu.
    """

    answer: str = Field(description="Odpowiedź ze znacznikami źródeł w tekście, np. [1].")
    citations: list[int] = Field(
        default_factory=list, description="Numery fragmentów kontekstu użytych w odpowiedzi."
    )

    @field_validator("citations", mode="before")
    @classmethod
    def repair_citations(cls, value: Any) -> Any:
        """Krok naprawy PRZED walidacją: małe modele często zwracają "[2], [3]" zamiast [2, 3].

        Akceptujemy string ("[2], [3]", "2, 3"), listę (["[2]", 3]) i pojedynczą liczbę:
        zamieniamy wszystko na tekst i wyciągamy z niego liczby całkowite (słowa są ignorowane).
        """
        if value is None:
            return []
        if isinstance(value, list):
            value = " ".join(map(str, value))
        return [int(n) for n in re.findall(r"\d+", str(value))]


# Formuły odmowy po polsku. Szukamy ich tylko w PIERWSZYM zdaniu: „X [1]. Nie ma informacji o Y”
# to częściowa odpowiedź, a „Nie ma informacji o X [1][2]” – odmowa, mimo doklejonych znaczników.
# Jednoznaczne formuły odmowy:
_REFUSAL = re.compile(
    r"\b(?:"
    r"nie ma (?:tu |w nim |w nich )?(?:tej |takiej |żadnej |konkretnej )?informacj"
    r"|brak (?:tej |takiej |żadnej )?informacj"
    r"|nie zawiera\w* (?:\w+ ){0,3}informacj"
    r"|nie wiem\b"
    r"|nie mogę (?:odpowiedzieć|ustalić|stwierdzić)"
    r"|nie (?:da się|można) (?:\w+ )?(?:ustalić|stwierdzić|określić|odpowiedzieć)"
    r")",
    re.IGNORECASE,
)
# Formy, które występują też w zwykłym opisie („suma nie jest określona”, „Filip nie podaje
# ręki”) – odmowa tylko wtedy, gdy zdanie mówi o kontekście / dokumentach.
_NOT_GIVEN = re.compile(
    r"\b(?:"
    r"nie (?:jest|są|został\w*|zostało) (?:\w+ ){0,2}"
    r"(?:podan|wymienion|opisan|wspomnian|określon|wskazan)\w*"
    r"|nie znajduj\w* się"
    r"|nie wspomina\w*"
    r"|nie podaj\w*"
    r")",
    re.IGNORECASE,
)
_SOURCE_WORD = re.compile(
    r"\b(?:kontek\w*|dokument\w*|tekst\w*|fragment\w*|scenariusz\w*|źródł\w*|materiał\w*)",
    re.IGNORECASE,
)


def first_sentence(text: str) -> str:
    """Pierwsze zdanie odpowiedzi bez znaczników [n]."""
    flat = _MARKER.sub("", text).strip()
    return re.split(r"(?<=[.!?])\s+", flat, maxsplit=1)[0]


def is_refusal(text: str) -> bool:
    """Czy odpowiedź jest odmową – formuła typu „w kontekście nie ma informacji…” w 1. zdaniu.

    Formy „nie jest podany / określony…”, „nie znajduje się”, „nie wspomina”, „nie podaje” liczą się
    tylko obok słowa o źródle (kontekst, dokument, tekst, fragment, scenariusz).

    Potrzebne, bo małe modele dopisują znaczniki [1][2][3] także do odmów, więc sam brak
    cytatów nie wystarcza do rozpoznania odmowy. Heurystyka – do sprawdzenia w ocenie ręcznej.
    """
    sentence = first_sentence(text)
    if _REFUSAL.search(sentence):
        return True
    return bool(_NOT_GIVEN.search(sentence) and _SOURCE_WORD.search(sentence))


def inline_markers(text: str) -> list[int]:
    """Numery ze znaczników [n] w tekście odpowiedzi (w kolejności, bez powtórzeń)."""
    return list(dict.fromkeys(int(n) for n in _MARKER.findall(text)))


def _words(text: str) -> set[str]:
    return {w.lower() for w in re.findall(r"\w{4,}", text)}


def best_quote(text: str, answer: str, max_len: int = 200) -> str:
    """Zdanie z chunka, które ma najwięcej wspólnych słów z odpowiedzią (cytat dociągany w kodzie).

    Pomijamy nagłówki Markdowna i sklejamy linie, bo PDF/MD łamią zdania w środku.
    """
    lines = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
    flat = re.sub(r"\s+", " ", " ".join(lines)).strip()
    sentences = [s for s in re.split(r"(?<=[.!?;])\s+", flat) if s]
    if not sentences:
        return ""
    answer_words = _words(answer)
    best = max(sentences, key=lambda s: len(answer_words & _words(s)))
    return best if len(best) <= max_len else best[: max_len - 1] + "…"


@dataclass
class RagResult:
    """Wynik pełnego zapytania RAG."""

    question: str
    answer: Answer
    hits: list[Hit]
    mode: str
    retrieve_s: float
    generate_s: float

    @property
    def cited_numbers(self) -> list[int]:
        """Poprawne numery cytowanych fragmentów: znaczniki z tekstu ∪ lista `citations`."""
        numbers = inline_markers(self.answer.answer) + self.answer.citations
        return sorted({n for n in numbers if 1 <= n <= len(self.hits)})

    @property
    def has_inline_citations(self) -> bool:
        """Czy tekst odpowiedzi zawiera co najmniej jeden poprawny znacznik [n]."""
        return any(1 <= n <= len(self.hits) for n in inline_markers(self.answer.answer))

    @property
    def refused(self) -> bool:
        """Odmowa = formuła odmowy w pierwszym zdaniu albo brak cytatów."""
        return is_refusal(self.answer.answer) or not self.cited_numbers

    @property
    def answered_from_context(self) -> bool:
        """Odpowiedź oparta na kontekście = cytuje co najmniej jeden fragment i nie jest odmową."""
        return not self.refused

    def cited_hits(self) -> list[Hit]:
        """Chunki cytowane przez model."""
        return [self.hits[n - 1] for n in self.cited_numbers]

    def retrieved_only(self) -> list[tuple[int, Hit]]:
        """Chunki pobrane w retrievalu, ale niecytowane (numer, chunk)."""
        cited = set(self.cited_numbers)
        return [(i, h) for i, h in enumerate(self.hits, start=1) if i not in cited]


def format_context(hits: list[Hit]) -> str:
    """Kontekst dla modelu: ponumerowane fragmenty z etykietą źródła."""
    return "\n\n".join(f"[{i}] ({hit.label()})\n{hit.text}" for i, hit in enumerate(hits, start=1))


def build_messages(question: str, hits: list[Hit]) -> list[dict[str, str]]:
    """Wiadomości dla chat completions: instrukcja systemowa + kontekst + pytanie."""
    user = f"Kontekst:\n{format_context(hits)}\n\nPytanie: {question}"
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]


def answer_question(
    question: str, settings: Settings, k: int | None = None, collection: str | None = None
) -> RagResult:
    """Retrieval top-k + generacja odpowiedzi ze structured output."""
    t0 = time.perf_counter()
    hits = retrieve(question, settings, k=k, collection=collection)
    t1 = time.perf_counter()
    answer, mode = complete_structured(
        llm_client(settings), settings, build_messages(question, hits), Answer
    )
    t2 = time.perf_counter()
    return RagResult(question, answer, hits, mode, round(t1 - t0, 2), round(t2 - t1, 2))
