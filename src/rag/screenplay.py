"""Parsowanie scenariusza filmowego (PDF) na sceny i chunki (1 chunk = 1 scena).

Przepływ:
    PDF ──► linie z pozycją (`pdfplumber`) ──► `group_scenes` ──► sceny z akapitami
        ──► `scene_chunks` ──► rekordy JSONL (format czytany przez `prepared.py`)

Rozpoznawanie opiera się na układzie strony typowym dla scenariusza:
- nagłówek sceny: numer + opis WIELKIMI LITERAMI przy lewym marginesie,
  np. `12. WN. KOŚCIÓŁ. DZIEŃ`;
- rodzaj linii po wcięciu (`x0`): imię postaci (cue) > dialog > opis akcji.

Numeracja scen (patrz `docs/notatki_dane.md`): `6A` – scena dopisana po zamrożeniu numeracji,
`117*`, `117**` – kolejne miejsca jednej sekwencji (konwencja autora scenariusza).

`group_scenes` i funkcje pomocnicze nie zależą od PDF-a, więc da się je testować na kilku
linijkach tekstu (`tests/test_screenplay.py`).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NamedTuple

import pdfplumber

MAX_WORDS = 900  # dłuższe sceny dzielimy na części po akapitach (limit embeddera)
SKIP_PAGES = 1  # strona tytułowa (nazwisko reżysera) – anonimizacja

# Granice wcięć (x0 w punktach PDF) odczytane z układu scenariusza.
HEADING_MAX_X0 = 150  # nagłówek sceny zaczyna się przy lewym marginesie
CUE_MIN_X0 = 240  # imię postaci przed kwestią
DIALOG_MIN_X0 = 170  # tekst kwestii
PAGE_NUMBER_MIN_X0 = 450  # numer strony w prawym górnym rogu
PARAGRAPH_GAP = 8  # odstęp (w pt) między liniami akcji, od którego zaczyna się nowy akapit

# Numer sceny: cyfry, opcjonalna litera (6A) i opcjonalne gwiazdki (117*, 117**).
# Po numerze kropka – albo sama spacja, ale tylko po gwiazdkach (w scenariuszu jest „117** PL.”).
# Bez tego wyjątku zwykłe linie akcji zaczynające się liczbą („3 DNI PÓŹNIEJ”) byłyby nagłówkami.
SCENE_RE = re.compile(r"^(\d+[A-Z]?(?:\*{1,2})?)(?:\.|(?<=\*))\s+(\S.*)$")
PORA_RE = re.compile(r"\b(DZIEŃ/SEKWENCJA|DZIEŃ|NOC|WIECZÓR|ŚWIT)$")
REMOVED_RE = re.compile(r"USUNI[ĘE]TA|SKRE[ŚS]LONA")


class Line(NamedTuple):
    """Linia tekstu z PDF-a z pozycją na stronie."""

    page: int
    text: str
    x0: float
    top: float
    bottom: float


@dataclass
class Scene:
    """Scena ze scenariusza: nagłówek rozbity na pola + treść w akapitach."""

    number: str
    heading: str
    wn_pl: str | None
    location: str
    time_of_day: str | None
    page_from: int
    page_to: int
    paragraphs: list[str] = field(default_factory=list)

    @property
    def removed(self) -> bool:
        """Scena usunięta/skreślona (numer zostaje w scenariuszu, treści nie ma)."""
        return bool(REMOVED_RE.search(self.heading))


# --------------------------------------------------------------------------- pomocnicze


def clean(text: str | None) -> str:
    """Normalizacja Unicode (NFC) i zbędnych spacji."""
    text = unicodedata.normalize("NFC", text or "")
    return re.sub(r"[ \t ]+", " ", text).strip()


def match_heading(text: str, x0: float) -> tuple[str, str] | None:
    """(numer sceny, reszta nagłówka), jeśli linia jest nagłówkiem sceny; inaczej None."""
    m = SCENE_RE.match(text)
    if m and x0 < HEADING_MAX_X0 and m.group(2) == m.group(2).upper():
        return m.group(1), m.group(2)
    return None


def parse_heading(heading: str) -> tuple[str | None, str, str | None]:
    """Rozbija nagłówek `WN. KOŚCIÓŁ. DZIEŃ` na (WN/PL, miejsce akcji, pora dnia)."""
    parts = [p.strip() for p in re.split(r"\.\s*", heading) if p.strip()]
    wn_pl = None
    if parts and re.fullmatch(r"(WN|PL)(/(WN|PL))?", parts[0]):
        wn_pl = parts.pop(0)
    pora = None
    if parts:
        m = PORA_RE.search(parts[-1])
        if m:
            pora = m.group(1)
            rest = parts[-1][: m.start()].strip()
            if rest:
                parts[-1] = rest
            else:
                parts.pop()
    return wn_pl, ". ".join(parts), pora


def line_kind(x0: float) -> str:
    """Rodzaj linii po wcięciu: `cue` (imię postaci), `dialog` albo `action`."""
    if x0 >= CUE_MIN_X0:
        return "cue"
    if x0 >= DIALOG_MIN_X0:
        return "dialog"
    return "action"


def build_paragraphs(lines: list[Line]) -> list[str]:
    """Łączy linie sceny w akapity; kwestie zapisuje jako `POSTAĆ: tekst`."""
    paras: list[str] = []
    buf: list[str] = []
    kind: str | None = None
    cue: str | None = None
    last: Line | None = None

    def flush() -> None:
        nonlocal buf, kind, cue
        if buf:
            txt = " ".join(buf)
            paras.append(f"{cue}: {txt}" if kind == "dialog" and cue else txt)
        buf, kind, cue = [], None, None

    for ln in lines:
        k = line_kind(ln.x0)
        if k == "cue":
            flush()
            cue, kind = ln.text, "dialog"
        else:
            gap = ln.top - last.bottom if last and last.page == ln.page else float("inf")
            if k == "dialog":
                if kind != "dialog":
                    flush()
                    kind = "dialog"
            elif kind == "dialog" or (buf and gap > PARAGRAPH_GAP):
                flush()
            if k == "action":
                kind = "action"
            buf.append(ln.text)
        last = ln
    flush()
    return paras


def split_long(header: str, paragraphs: list[str], max_words: int = MAX_WORDS) -> list[str]:
    """Dzieli tekst sceny na części po akapitach; każda część dostaje nagłówek."""
    if sum(len(p.split()) for p in paragraphs) <= max_words:
        return [header + "\n" + "\n".join(paragraphs)]
    parts: list[list[str]] = []
    cur: list[str] = []
    n = 0
    for p in paragraphs:
        w = len(p.split())
        if cur and n + w > max_words:
            parts.append(cur)
            cur, n = [], 0
        cur.append(p)
        n += w
    if cur:
        parts.append(cur)
    k = len(parts)
    return [f"{header} (część {i}/{k})\n" + "\n".join(ps) for i, ps in enumerate(parts, 1)]


# --------------------------------------------------------------------------- sceny


def group_scenes(lines: Iterable[Line]) -> list[Scene]:
    """Grupuje linie w sceny: nagłówek otwiera nową scenę, kolejne linie są jej treścią.

    Pomija numery stron i tekst przed pierwszą sceną.
    """
    scenes: list[tuple[Scene, list[Line]]] = []
    for ln in lines:
        if not ln.text:
            continue
        if re.fullmatch(r"\d+", ln.text) and ln.x0 > PAGE_NUMBER_MIN_X0:
            continue
        heading = match_heading(ln.text, ln.x0)
        if heading:
            number, rest = heading
            wn_pl, location, pora = parse_heading(rest)
            scene = Scene(number, rest, wn_pl, location, pora, ln.page, ln.page)
            scenes.append((scene, []))
        elif scenes:
            scene, body = scenes[-1]
            scene.page_to = ln.page
            body.append(ln)
    for scene, body in scenes:
        scene.paragraphs = build_paragraphs(body)
    return [s for s, _ in scenes]


def read_lines(path: Path, skip_pages: int = SKIP_PAGES) -> list[Line]:
    """Linie tekstu z PDF-a (z pozycją), z pominięciem pierwszych `skip_pages` stron."""
    lines: list[Line] = []
    with pdfplumber.open(path) as pdf:
        for pno, page in enumerate(pdf.pages, 1):
            if pno <= skip_pages:
                continue
            for ln in page.extract_text_lines():
                lines.append(Line(pno, clean(ln["text"]), ln["x0"], ln["top"], ln["bottom"]))
    return lines


def parse_screenplay(path: Path) -> list[Scene]:
    """Sceny ze scenariusza w PDF-ie (łącznie z usuniętymi – patrz `Scene.removed`)."""
    return group_scenes(read_lines(path))


def scene_chunks(scenes: list[Scene], source_file: str) -> list[dict[str, Any]]:
    """Rekordy JSONL (format `prepared.py`) dla scen; usunięte sceny są pomijane."""
    chunks: list[dict[str, Any]] = []
    for s in scenes:
        if s.removed:
            continue
        loc = ". ".join(x for x in [s.wn_pl, s.location, s.time_of_day] if x)
        pages = str(s.page_from) if s.page_from == s.page_to else f"{s.page_from}-{s.page_to}"
        header = f"Scena {s.number} | {loc} | scenariusz, s. {pages}"
        texts = split_long(header, s.paragraphs or ["(brak opisu)"])
        for i, text in enumerate(texts, 1):
            chunks.append(
                {
                    "id": f"scenariusz-{s.number}" + (f"-{i}" if len(texts) > 1 else ""),
                    "dokument": "scenariusz",
                    "zrodlo": source_file,
                    "tekst": text,
                    "meta": {
                        "scena": s.number,
                        "obiekt": s.location,
                        "wn_pl": s.wn_pl,
                        "pora": s.time_of_day,
                        "strona": s.page_from,
                        "strona_do": s.page_to,
                    },
                }
            )
    return chunks
