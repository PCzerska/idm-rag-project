#!/usr/bin/env python3
"""
prepare_data.py - przygotowanie danych do RAG (projekt "Ministranci", etap E1)

Czyta 4 oryginalne dokumenty i zapisuje gotowe chunki do JSONL:
  scenariusz (PDF)            -> 1 chunk = 1 scena
  lista rekwizytow (DOCX)     -> 1 chunk = 1 wiersz listy (grupa scen)
  lista grafik (DOCX)         -> 1 chunk = 1 scena (opcjonalna)
  tabela inscenizacyjna (PDF) -> 1 chunk = 1 dzien zdjeciowy
  spis obiektow (PDF)         -> 1 chunk = 1 obiekt (BEZ adresow)

Anonimizacja jest wbudowana: kolumna adresow w spisie obiektow w ogole nie jest
czytana, strona tytulowa scenariusza jest pomijana, a zamiany z <in>/zamiany.txt
podmieniaja wskazane frazy (pliki lokalne, poza repo). Na koncu dziala audyt: szuka e-maili, telefonow, adresow i
slow z listy zakazanej w WYNIKU. Kod wyjscia != 0, gdy cos znajdzie.

Uzycie:
  pip install pdfplumber python-docx
  python prepare_data.py --in data/original --out data/processed
  (opcjonalnie) data/original/zakazane.txt i data/original/zamiany.txt - patrz nizej

Wyniki: chunks.jsonl (wszystkie chunki w jednym pliku), raport.txt
"""
import argparse
import glob
import json
import re
import sys
import unicodedata
from pathlib import Path

import pdfplumber
from docx import Document

# --------------------------------------------------------------------------
# KONFIGURACJA
# --------------------------------------------------------------------------

# Zamiany tekstu (wzorzec regex -> zamiennik) i lista slow zakazanych NIE sa w kodzie, zeby repo
# nie zawieralo danych, ktore anonimizujemy. Leza w lokalnych plikach obok oryginalow
# (katalog --in, ignorowany przez git):
#   zamiany.txt  - jedna zamiana w linii: wzorzec_regex => zamiennik   (np. "JAN KOWALSKI => statysta")
#   zakazane.txt - jedno slowo/fraza w linii (nazwiska, ulice, miasta) - audyt szuka ich w WYNIKU
# Linie puste i zaczynajace sie od # sa pomijane. Mozna tez podac plik przez --zakazane.
REPLACEMENTS = []

# Audyt wyniku: wzorzec -> opis. Dopisz tu nazwiska ekipy/aktorow
# (albo podaj plik przez --zakazane, jedno slowo/fraza w linii).
AUDIT_PATTERNS = {
    "e-mail": r"[\w.+-]+@[\w-]+\.[\w.]+",
    "telefon": r"(?<!\d)(?:\+?48[ -]?)?\d{3}[ -]\d{3}[ -]\d{3}(?!\d)",
    "kod pocztowy": r"(?<!s\. )(?<![\d-])\d{2}-\d{3}\b",
    "adres (ul./al./os.)": r"\b(?:ul|al|os)\.\s*[A-ZĄĆĘŁŃÓŚŹŻ]",
    "link": r"https?://|www\.",
    "numer mieszkania": r"[Mm]ieszkanie\s+nr\s+\d+",
}
AUDIT_WORDS = []  # uzupelniane z zakazane.txt

MAX_WORDS = 900          # dluzsze sceny dzielimy na czesci (limit embeddera)
SCENARIUSZ_SKIP_PAGES = 1  # strona tytulowa (nazwisko rezysera)

# --------------------------------------------------------------------------
# POMOCNICZE
# --------------------------------------------------------------------------


def read_lines(path):
    """Linie pliku bez pustych i komentarzy (#); brak pliku = pusta lista."""
    p = Path(path)
    if not p.exists():
        return []
    out = []
    for ln in p.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#"):
            out.append(ln)
    return out


def load_replacements(indir):
    reps = []
    for ln in read_lines(Path(indir) / "zamiany.txt"):
        if "=>" not in ln:
            sys.exit(f"BLAD: zamiany.txt - linia bez '=>': {ln!r}")
        pat, rep = (x.strip() for x in ln.split("=>", 1))
        reps.append((pat, rep))
    return reps


def nfc(s):
    return unicodedata.normalize("NFC", s)


def clean(s):
    s = nfc(s or "")
    s = re.sub(r"[ \t\u00a0]+", " ", s)
    return s.strip()


def norm_scene(s):
    """'13a' -> '13A', '117*' zostaje."""
    return clean(s).upper()


def find_file(indir, pattern):
    hits = [p for p in Path(indir).glob("*") if re.search(pattern, nfc(p.name), re.I)]
    if not hits:
        sys.exit(f"BLAD: nie znaleziono pliku pasujacego do /{pattern}/ w {indir}")
    return sorted(hits)[0]


def apply_replacements(text):
    for pat, rep in REPLACEMENTS:
        text = re.sub(pat, rep, text, flags=re.I)
    return text


def split_long(header, paragraphs, max_words=MAX_WORDS):
    """Dzieli tekst na czesci po akapitach; kazda czesc dostaje naglowek."""
    total = sum(len(p.split()) for p in paragraphs)
    if total <= max_words:
        return [header + "\n" + "\n".join(paragraphs)]
    parts, cur, n = [], [], 0
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


# --------------------------------------------------------------------------
# 1. SCENARIUSZ
# --------------------------------------------------------------------------

SCENE_RE = re.compile(r"^(\d+[A-Z]?)\.\s+(\S.*)$")
PORA_RE = re.compile(r"\b(DZIEŃ/SEKWENCJA|DZIEŃ|NOC|WIECZÓR|ŚWIT)$")
REMOVED_RE = re.compile(r"USUNI[ĘE]TA|SKRE[ŚS]LONA")


def parse_heading(h):
    parts = [p.strip() for p in re.split(r"\.\s*", h) if p.strip()]
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


def parse_scenariusz(path):
    scenes, cur = [], None
    prev = None  # (page, bottom, kind)
    with pdfplumber.open(path) as pdf:
        for pno, page in enumerate(pdf.pages, 1):
            if pno <= SCENARIUSZ_SKIP_PAGES:
                continue
            for ln in page.extract_text_lines():
                t = clean(ln["text"])
                x0 = ln["x0"]
                if not t:
                    continue
                if re.fullmatch(r"\d+", t) and x0 > 450:  # numer strony
                    continue
                m = SCENE_RE.match(t)
                if m and x0 < 150 and m.group(2) == m.group(2).upper():
                    if cur:
                        scenes.append(cur)
                    wn_pl, obiekt, pora = parse_heading(m.group(2))
                    cur = dict(scena=m.group(1), naglowek=m.group(2), wn_pl=wn_pl,
                               obiekt=obiekt, pora=pora, strona=pno, strona_do=pno,
                               lines=[])
                    prev = None
                    continue
                if cur is None:
                    continue
                cur["strona_do"] = pno
                kind = "cue" if x0 >= 240 else "dialog" if x0 >= 170 else "action"
                cur["lines"].append((kind, t, ln["top"], ln["bottom"], pno))
    if cur:
        scenes.append(cur)

    out = []
    for s in scenes:
        paras = build_paragraphs(s.pop("lines"))
        s["akapity"] = paras
        s["usunieta"] = bool(REMOVED_RE.search(s["naglowek"]))
        out.append(s)
    return out


def build_paragraphs(lines):
    """Laczy linie w akapity; kwestie jako 'POSTAC: tekst'."""
    paras, buf, bkind, cue = [], [], None, None
    last = None

    def flush():
        nonlocal buf, bkind, cue
        if buf:
            txt = " ".join(buf)
            paras.append(f"{cue}: {txt}" if bkind == "dialog" and cue else txt)
        buf, bkind, cue = [], None, None

    for kind, t, top, bottom, pno in lines:
        if kind == "cue":
            flush()
            cue = t
            bkind = "dialog"
            last = (pno, bottom)
            continue
        gap = (top - last[1]) if last and last[0] == pno else 99
        if kind == "dialog":
            if bkind != "dialog":
                flush()
                bkind = "dialog"
            buf.append(t)
        else:  # action
            if bkind == "dialog" or (buf and gap > 8):
                flush()
            bkind = "action"
            buf.append(t)
        last = (pno, bottom)
    flush()
    return paras


def scenariusz_chunks(scenes, fname):
    chunks, removed = [], []
    for s in scenes:
        if s["usunieta"]:
            removed.append(s["scena"])
            continue
        loc = ". ".join(x for x in [s["wn_pl"], s["obiekt"], s["pora"]] if x)
        strony = str(s["strona"]) if s["strona"] == s["strona_do"] else f'{s["strona"]}-{s["strona_do"]}'
        header = f'Scena {s["scena"]} | {loc} | scenariusz, s. {strony}'
        texts = split_long(header, s["akapity"] or ["(brak opisu)"])
        for i, t in enumerate(texts, 1):
            chunks.append(dict(
                id=f'scenariusz-{s["scena"]}' + (f"-{i}" if len(texts) > 1 else ""),
                dokument="scenariusz", zrodlo=fname, tekst=t,
                meta=dict(scena=s["scena"], obiekt=s["obiekt"], wn_pl=s["wn_pl"],
                          pora=s["pora"], strona=s["strona"], strona_do=s["strona_do"]),
            ))
    return chunks, removed


# --------------------------------------------------------------------------
# 2. LISTA REKWIZYTOW (DOCX)
# --------------------------------------------------------------------------

def cell_items(cell):
    items = []
    for p in cell.paragraphs:
        for part in re.split(r"[\n\v]", p.text):
            part = clean(part)
            if part:
                items.append(part)
    return items


def rekwizyty_chunks(path, fname):
    doc = Document(path)
    chunks, n = [], 0
    for tb in doc.tables:
        for row in tb.rows:
            cells = [cell_items(c) for c in row.cells]
            if len(cells) < 3:
                continue
            first = " ".join(cells[0])
            if first.lower().startswith("sceny"):
                continue  # naglowek
            if not any(cells):
                continue
            sceny = [norm_scene(x) for x in re.findall(r"\b\d+[A-Za-z]?\b", first)]
            obiekt = " ".join(cells[1]).replace("*", "").strip() or "(rekwizyty ogólne)"
            rekw = "; ".join(cells[2])
            ilosc = "; ".join(cells[3]) if len(cells) > 3 else ""
            uwagi = "; ".join(cells[4]) if len(cells) > 4 else ""
            if not (rekw or ilosc or uwagi):
                continue
            n += 1
            lines = [f'Lista rekwizytów | sceny: {", ".join(sceny) if sceny else "—"} | obiekt: {obiekt}',
                     f"Rekwizyty: {rekw}"]
            if ilosc:
                lines.append(f"Ilość: {ilosc}")
            if uwagi:
                lines.append(f"Uwagi: {uwagi}")
            chunks.append(dict(
                id=f"rekwizyty-{n:02d}", dokument="rekwizyty", zrodlo=fname,
                tekst="\n".join(lines),
                meta=dict(sceny=sceny, obiekt=obiekt, wiersz=n),
            ))
    return chunks


def grafiki_chunks(path, fname):
    """Lista grafik (DOCX): 1 chunk = 1 scena (wszystkie jej grafiki, z obiektem).

    Komorki SCENA i OBIEKT sa scalone pionowo, wiec python-docx powtarza ich wartosc w kazdym
    wierszu; puste wartosci uzupelniamy z poprzedniego wiersza. Ta sama scena moze wystapic
    w kilku miejscach (np. 24) - laczymy ja w jeden chunk. Ostatnia, nienazwana kolumna
    zawiera znaczniki 'x' (znaczenie nieznane) - zapisujemy je tylko w metadanych.
    """
    doc = Document(path)
    by_scene, order = {}, []
    scena = obiekt = ""
    for tb in doc.tables:
        for row in tb.rows:
            cells = [" ".join(cell_items(c)) for c in row.cells]
            cells += [""] * (6 - len(cells))
            if cells[0].upper().startswith("SCENA"):
                continue  # naglowek
            sc, ob, graf, ilosc, uwagi, znacz = (clean(x) for x in cells[:6])
            if sc:
                scena = norm_scene(sc)
            if ob:
                obiekt = ob
            if not graf:
                continue
            if not scena:
                continue
            if scena not in by_scene:
                by_scene[scena] = []
                order.append(scena)
            by_scene[scena].append(dict(obiekt=obiekt, grafika=graf, ilosc=ilosc, uwagi=uwagi,
                                        x=bool(znacz)))
    chunks = []
    for sc in order:
        items = by_scene[sc]
        obiekty = list(dict.fromkeys(i["obiekt"] for i in items if i["obiekt"]))
        lines = [f'Lista grafik | scena {sc} | obiekt: {"; ".join(obiekty) or "—"}']
        for i in items:
            extra = [f'ilość: {i["ilosc"]}' if i["ilosc"] else "", f'uwagi: {i["uwagi"]}' if i["uwagi"] else ""]
            extra = " | ".join(e for e in extra if e)
            lines.append(f'- grafika: {i["grafika"]}' + (f" | {extra}" if extra else ""))
        chunks.append(dict(
            id=f"grafiki-{sc}", dokument="grafiki", zrodlo=fname, tekst="\n".join(lines),
            meta=dict(scena=sc, obiekty=obiekty, liczba_grafik=len(items),
                      znacznik_x=any(i["x"] for i in items)),
        ))
    return chunks


# --------------------------------------------------------------------------
# 3. TABELA INSCENIZACYJNA (PDF, uklad geometryczny)
# --------------------------------------------------------------------------

DATE_RE = re.compile(r"\d{1,2}\.\d{2}\.\d{4}")
# granice kolumn (x) odczytane z krawedzi tabeli
COLS = [("scena", 64, 79), ("lokacja", 79, 112), ("statysci_ilosc", 112, 142),
        ("statysci_role", 142, 204), ("epizody_ilosc", 204, 219),
        ("epizody_grupy", 219, 273), ("kaskaderzy", 273, 318), ("pojazdy", 318, 356),
        ("rekwizyty", 356, 410), ("specjalne_zwierzeta", 410, 467),
        ("uwagi", 467, 521), ("charakteryzacja_kostiumy", 521, 600)]
COL_LABELS = {
    "lokacja": "lokacja", "statysci_ilosc": "statyści (ilość)", "statysci_role": "statyści/role",
    "epizody_ilosc": "epizody (ilość)", "epizody_grupy": "epizody/grupy/aktorzy tła",
    "kaskaderzy": "kaskaderzy", "pojazdy": "pojazdy", "rekwizyty": "rekwizyty",
    "specjalne_zwierzeta": "rekwizyty specjalne/zwierzęta", "uwagi": "uwagi",
    "charakteryzacja_kostiumy": "charakteryzacja/kostiumy",
}
TOTAL_WORDS = {"RAZEM", "SUMA", "ZAMÓWIENIE"}


def col_of(x0):
    for name, a, b in COLS:
        if a <= x0 < b:
            return name
    return None


def parse_tabela(path):
    """Zwraca (lista dni, ostrzezenia). Dzien = dict(dzien, data, wiersze, sumy)."""
    days, warns = {}, []
    with pdfplumber.open(path) as pdf:
        for pno, pg in enumerate(pdf.pages, 1):
            # use_text_flow: znaki w kolejnosci strumienia PDF - domyslne sortowanie po pozycji
            # rozbijalo slowa w niektorych komorkach na pojedyncze litery
            words = pg.extract_words(use_text_flow=True)
            # daty (kolumna 'DATA') i numery dni
            dates = []
            for w in sorted(words, key=lambda w: w["top"]):
                if w["x0"] < 50 and DATE_RE.fullmatch(w["text"]):
                    num = next((v["text"] for v in words
                                if 50 <= v["x0"] < 64 and abs(v["top"] - w["top"]) < 1.5
                                and v["text"].isdigit()), None)
                    dates.append((w["top"], w["text"], num))
            if not dates:
                continue
            # wiersze = prostokaty kolumny 'DZIEN ZDJ' (x 46.4-64.6)
            rows = sorted({(round(r["top"], 1), round(r["bottom"], 1)) for r in pg.rects
                           if abs(r["x0"] - 46.4) < 0.8 and abs(r["x1"] - 64.6) < 0.8
                           and r["top"] > 90 - 40})
            rows = [r for r in rows if r[1] - r[0] > 5]
            # wiersz naglowka tabeli (strona 1) pomijamy: zaczyna sie od 'DATA'
            row_data = []
            for top, bottom in rows:
                rw = [w for w in words if top - 0.5 <= (w["top"] + w["bottom"]) / 2 <= bottom + 0.5
                      and w["x0"] >= 64]
                if not rw:
                    continue
                labels = {w["text"] for w in rw if w["x0"] < 100}
                row_data.append(dict(top=top, bottom=bottom, words=rw,
                                     total=bool(labels & TOTAL_WORDS)))
            # koniec dnia = dol grupy wierszy sum (RAZEM/ZAMOWIENIE)
            ends = []
            for i, r in enumerate(row_data):
                nxt = row_data[i + 1] if i + 1 < len(row_data) else None
                if r["total"] and not (nxt and nxt["total"]):
                    ends.append(r["bottom"])
            n = len(dates)
            if len(ends) >= n:
                bounds = ends[:n]
                spans = [(0, bounds[0])] + [(bounds[i], bounds[i + 1]) for i in range(n - 1)]
            else:
                bounds = ends
                spans = [(0, bounds[0])] + [(bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1)]
                spans.append((bounds[-1] if bounds else 0, 10 ** 6))
            if len(spans) != n:
                warns.append(f"tabela s.{pno}: {n} dat vs {len(spans)} przedzialow - sprawdz recznie")
                continue
            for (dtop, dtxt, dnum), (s0, s1) in zip(dates, spans):
                c = dtop + 10
                if not (s0 - 15 <= c <= s1 + 15) and s1 < 10 ** 6:
                    warns.append(f"tabela s.{pno}: data {dtxt} poza przedzialem ({s0:.0f}-{s1:.0f}, y={c:.0f})")
                key = dtxt
                if key not in days:
                    days[key] = dict(dzien=str(len(days) + 1), data=dtxt, wiersze=[], sumy=[], strony=set())
                    if dnum and dnum != days[key]["dzien"]:
                        warns.append(f"tabela s.{pno}: data {dtxt} ma numer dnia {dnum}, kolejnosc daje {days[key]['dzien']}")
                day = days[key]
                day["strony"].add(pno)
                for r in row_data:
                    mid = (r["top"] + r["bottom"]) / 2
                    if not (s0 <= mid < s1):
                        continue
                    if r["total"]:
                        day["sumy"].extend(parse_total_rows(r["words"]))
                    else:
                        row = parse_scene_row(r["words"])
                        if row:
                            day["wiersze"].append(row)
    return list(days.values()), warns


def join_words(ws):
    ws = sorted(ws, key=lambda w: (round(w["top"] / 3), w["x0"]))
    return clean(" ".join(w["text"] for w in ws))


def parse_scene_row(words):
    cells = {}
    for w in words:
        c = col_of(w["x0"])
        if c:
            cells.setdefault(c, []).append(w)
    scena_txt = join_words(cells.get("scena", []))
    scen = [norm_scene(x.replace(" ", "")) for x in
            re.findall(r"\d+(?:\s?[a-hj-zA-HJ-Z](?![a-zA-Z]))?\*?", scena_txt)]
    row = {k: join_words(v) for k, v in cells.items() if k != "scena"}
    row = {k: v for k, v in row.items() if v}
    if not scen and not row:
        return None
    return dict(sceny=scen, **row)


def parse_total_rows(words):
    """Prostokat sum obejmuje 2 linie (RAZEM + ZAMOWIENIE) - rozdzielamy je po y."""
    out = []
    for lab in sorted((w for w in words if w["x0"] < 100 and w["text"] in TOTAL_WORDS),
                      key=lambda w: w["top"]):
        nums = {}
        for w in words:
            c = col_of(w["x0"])
            if (c in ("statysci_ilosc", "epizody_ilosc") and re.fullmatch(r"\d+", w["text"])
                    and abs(w["top"] - lab["top"]) < 2.5):
                nums[c] = int(w["text"])
        out.append(dict(typ=lab["text"], statysci=nums.get("statysci_ilosc"),
                        epizody=nums.get("epizody_ilosc")))
    return out


def tabela_chunks(days, fname):
    def sort_key(d):
        return int(d["dzien"]) if d["dzien"] and d["dzien"].isdigit() else 999
    chunks = []
    for d in sorted(days, key=sort_key):
        lines = [f'Tabela inscenizacyjna | dzień zdjęciowy {d["dzien"]} | data: {d["data"]}']
        wszystkie = []
        for r in d["wiersze"]:
            wszystkie.extend(r["sceny"])
            parts = [f'{lab}: {r[k]}' for k, lab in COL_LABELS.items() if r.get(k)]
            lines.append(f'- scena {", ".join(r["sceny"]) or "?"} | ' + " | ".join(parts))
        for s in d["sumy"]:
            vals = [f"{n} {v}" for n, v in (("statyści", s["statysci"]), ("epizody", s["epizody"])) if v is not None]
            lines.append(f'  [{s["typ"].lower()}: {", ".join(vals) or "brak liczb"}]')
        uniq = list(dict.fromkeys(wszystkie))
        lines.insert(1, f'Sceny tego dnia: {", ".join(uniq)}')
        chunks.append(dict(
            id=f'tabela-dzien-{d["dzien"]}', dokument="tabela", zrodlo=fname,
            tekst=apply_replacements("\n".join(lines)),
            meta=dict(dzien=d["dzien"], data=d["data"], sceny=uniq,
                      strony=sorted(d["strony"]), wiersze=d["wiersze"], sumy=d["sumy"]),
        ))
    return chunks


# --------------------------------------------------------------------------
# 4. SPIS OBIEKTOW (PDF) - BEZ ADRESOW
# --------------------------------------------------------------------------

def spis_chunks(path, fname):
    chunks = []
    last = None
    with pdfplumber.open(path) as pdf:
        for pg in pdf.pages:
            for tb in pg.find_tables():
                for r in tb.extract():
                    if len(r) < 4:
                        continue
                    lp, obiekt, _adres_POMIJAMY, ogr = [clean(x) for x in r[:4]]
                    if re.search(r"L+\.?P+", lp) or "OBIEKT" in obiekt.upper() and len(obiekt) < 12:
                        continue
                    if "SPIS" in obiekt.upper() or not (lp or obiekt):
                        continue
                    alt = False
                    if lp.upper() == "LUB" or obiekt.upper() == "LUB":
                        continue  # separator 'LUB' przed obiektem alternatywnym
                    if not lp and obiekt == obiekt.upper():
                        continue  # powtorzony naglowek tabeli na kolejnej stronie
                    if not lp and obiekt and last:
                        lp, alt = last, True  # wiersz alternatywy (np. druga parafia)
                    if not lp.isdigit():
                        continue
                    last = lp
                    obiekt = obiekt.replace("\n", " ")
                    first, _, rest = ogr.partition("\n")
                    if (rest and chunks and re.fullmatch(r"\d{1,2}\.\d{2}\.\d{2,4} termin niedostępny\.?", first.strip())
                            and (chunks[-1]["meta"]["ograniczenia"] or "").rstrip().endswith("niedostępny.")):
                        # tekst z komorki poprzedniego obiektu wypadl w PDF-ie do kolejnego wiersza
                        chunks[-1]["meta"]["ograniczenia"] += " " + first.strip()
                        chunks[-1]["tekst"] += " " + first.strip()
                        ogr = rest
                    ogr = ogr.replace("\n", " ")
                    t = f"Spis obiektów | obiekt nr {lp}: {obiekt}"
                    if ogr:
                        t += f"\nOgraniczenia obiektowe: {ogr}"
                    chunks.append(dict(
                        id=f"obiekt-{lp}" + ("-alt" if alt else ""), dokument="obiekty",
                        zrodlo=fname, tekst=t,
                        meta=dict(nr=int(lp), obiekt=obiekt, ograniczenia=ogr or None)))
    return chunks


# --------------------------------------------------------------------------
# AUDYT
# --------------------------------------------------------------------------

def audit(chunks, extra_words):
    hits = []
    pats = {k: re.compile(v) for k, v in AUDIT_PATTERNS.items()}
    words = [w.lower() for w in AUDIT_WORDS + extra_words]
    for c in chunks:
        blob = c["tekst"] + " " + json.dumps(c["meta"], ensure_ascii=False)
        for name, rx in pats.items():
            for m in rx.finditer(blob):
                hits.append((c["id"], name, m.group(0)))
        low = blob.lower()
        for w in words:
            if w in low:
                hits.append((c["id"], "slowo zakazane", w))
    return hits


# --------------------------------------------------------------------------
# MAIN
# --------------------------------------------------------------------------

def write_jsonl(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--in", dest="indir", required=True, help="katalog z ORYGINALNYMI plikami")
    ap.add_argument("--out", dest="outdir", required=True, help="katalog wyjsciowy (np. data/processed)")
    ap.add_argument("--zakazane", help="plik z zakazanymi slowami (domyslnie <in>/zakazane.txt)")
    a = ap.parse_args()

    out = Path(a.outdir)
    out.mkdir(parents=True, exist_ok=True)
    global REPLACEMENTS
    REPLACEMENTS = load_replacements(a.indir)
    extra = read_lines(a.zakazane) if a.zakazane else read_lines(Path(a.indir) / "zakazane.txt")

    rep = []
    if not extra:
        rep.append("UWAGA: brak zakazane.txt - audyt sprawdza tylko wzorce ogolne (e-mail, telefon, adres), nie nazwiska ani ulice")
    if not REPLACEMENTS:
        rep.append("UWAGA: brak zamiany.txt - zadne frazy nie zostaly zamienione")
    f_scen = find_file(a.indir, r"MINISTRANCI.*WER|scenariusz")
    f_rek = find_file(a.indir, r"rekwizyt.*\.docx$")
    f_tab = find_file(a.indir, r"inscenizacyjna.*\.pdf$")
    f_spis = find_file(a.indir, r"spis.*obiekt.*\.pdf$")
    f_graf = next((p for p in sorted(Path(a.indir).glob("*"))
                   if re.search(r"grafik.*\.docx$", nfc(p.name), re.I)), None)

    scenes = parse_scenariusz(f_scen)
    c_scen, removed = scenariusz_chunks(scenes, nfc(f_scen.name))
    c_rek = rekwizyty_chunks(f_rek, nfc(f_rek.name))
    days, warns = parse_tabela(f_tab)
    c_tab = tabela_chunks(days, nfc(f_tab.name))
    c_spis = spis_chunks(f_spis, nfc(f_spis.name))
    c_graf = grafiki_chunks(f_graf, nfc(f_graf.name)) if f_graf else []

    allc = [json.loads(apply_replacements(json.dumps(c, ensure_ascii=False)))
            for c in c_scen + c_rek + c_graf + c_tab + c_spis]
    c_scen, c_rek, c_graf, c_tab, c_spis = (
        [c for c in allc if c["dokument"] == d]
        for d in ("scenariusz", "rekwizyty", "grafiki", "tabela", "obiekty"))

    write_jsonl(out / "chunks.jsonl", allc)

    # kontrola spojnosci
    ids = [c["id"] for c in allc]
    dup = {i for i in ids if ids.count(i) > 1}
    nums = [s["scena"] for s in scenes]
    scen_ids = {s["scena"] for s in scenes if not s["usunieta"]}
    tab_scenes = {x for c in c_tab for x in c["meta"]["sceny"]}
    rek_scenes = {x for c in c_rek for x in c["meta"]["sceny"]}
    words = [len(c["tekst"].split()) for c in allc]

    rep.append("RAPORT prepare_data.py")
    rep.append(f"scenariusz : {len(c_scen)} chunkow (scen w pliku: {len(scenes)}, usunietych/skreslonych: {removed})")
    rep.append(f"rekwizyty  : {len(c_rek)} chunkow")
    rep.append(f"grafiki    : {len(c_graf)} chunkow" if f_graf else "grafiki    : brak pliku (pominieto)")
    rep.append(f"tabela     : {len(c_tab)} dni zdjeciowych (dni: {[d['dzien'] for d in sorted(days, key=lambda d: int(d['dzien'] or 999))]})")
    rep.append(f"obiekty    : {len(c_spis)} chunkow")
    rep.append(f"RAZEM      : {len(allc)} chunkow; slow w chunku min/mediana/max = "
               f"{min(words)}/{sorted(words)[len(words)//2]}/{max(words)}")
    graf_scenes = {c["meta"]["scena"] for c in c_graf}
    rep.append(f"sceny z listy grafik, ktorych nie ma w scenariuszu: {sorted(graf_scenes - scen_ids - set(removed)) or 'brak'}")
    rep.append(f"duplikaty numerow scen w scenariuszu: {sorted({n for n in nums if nums.count(n) > 1}) or 'brak'}")
    rep.append(f"duplikaty id chunkow: {sorted(dup) or 'brak'}")
    rep.append(f"sceny z tabeli, ktorych nie ma w scenariuszu: {sorted(tab_scenes - scen_ids - set(removed)) or 'brak'}")
    rep.append(f"sceny z listy rekwizytow, ktorych nie ma w scenariuszu: {sorted(rek_scenes - scen_ids - set(removed)) or 'brak'}")
    rep.append(f"sceny ze scenariusza bez wpisu w tabeli: {sorted(scen_ids - tab_scenes, key=lambda x: (len(x), x)) or 'brak'}")
    for w in warns:
        rep.append("OSTRZEZENIE: " + w)

    hits = audit(allc, extra)
    rep.append("")
    rep.append(f"AUDYT ANONIMIZACJI: {len(hits)} trafien")
    for cid, kind, val in hits:
        rep.append(f"  [{cid}] {kind}: {val}")
    (out / "raport.txt").write_text("\n".join(rep) + "\n", encoding="utf-8")
    print("\n".join(rep))
    sys.exit(1 if hits else 0)


if __name__ == "__main__":
    main()