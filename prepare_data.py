#!/usr/bin/env python3
"""
prepare_data.py - przygotowanie danych do RAG (projekt "Ministranci", etap E1)

Czyta SCENARIUSZ (PDF) i zapisuje gotowe chunki do JSONL: 1 chunk = 1 scena
(sceny dluzsze niz MAX_WORDS slow dzielone na czesci po akapitach). Samo parsowanie
scenariusza jest w module src/rag/screenplay.py (testy: tests/test_screenplay.py);
tu zostaje wejscie/wyjscie, raport i audyt.

Scenariusz jest jedynym zrodlem wiedzy RAG. Pozostale dokumenty produkcji (tabela
inscenizacyjna, listy rekwizytow i grafik, spis obiektow) NIE sa chunkowane - sluza tylko
do recznej budowy zlotego zbioru (patrz docs/notatki_dane.md). Ich parsery z etapu E1
sa w historii gita (commit 6a28f57).

Anonimizacja: strona tytulowa scenariusza (nazwisko rezysera) jest pomijana przy parsowaniu.
Na koncu dziala audyt WYNIKU: szuka e-maili, telefonow, kodow pocztowych, adresow i linkow.
Kod wyjscia != 0, gdy cos znajdzie.

Uzycie:
  uv run python prepare_data.py --in data/original --out data/processed

Wyniki: chunks.jsonl (do `make ingest`), raport.txt
"""
import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

from rag.screenplay import parse_screenplay, scene_chunks

# Audyt wyniku: opis -> wzorzec danych, ktore nie moga trafic do indeksu.
AUDIT_PATTERNS = {
    "e-mail": r"[\w.+-]+@[\w-]+\.[\w.]+",
    "telefon": r"(?<!\d)(?:\+?48[ -]?)?\d{3}[ -]\d{3}[ -]\d{3}(?!\d)",
    "kod pocztowy": r"(?<!s\. )(?<![\d-])\d{2}-\d{3}\b",
    "adres (ul./al./os.)": r"\b(?:ul|al|os)\.\s*[A-ZĄĆĘŁŃÓŚŹŻ]",
    "link": r"https?://|www\.",
    "numer mieszkania": r"[Mm]ieszkanie\s+nr\s+\d+",
}


def nfc(s):
    return unicodedata.normalize("NFC", s)


def find_file(indir, pattern):
    hits = [p for p in Path(indir).glob("*") if re.search(pattern, nfc(p.name), re.I)]
    if not hits:
        sys.exit(f"BLAD: nie znaleziono pliku pasujacego do /{pattern}/ w {indir}")
    return sorted(hits)[0]


def audit(chunks):
    """Trafienia wzorcow z AUDIT_PATTERNS w tekscie i metadanych chunkow: (id, rodzaj, tekst)."""
    hits = []
    pats = {k: re.compile(v) for k, v in AUDIT_PATTERNS.items()}
    for c in chunks:
        blob = c["tekst"] + " " + json.dumps(c["meta"], ensure_ascii=False)
        for name, rx in pats.items():
            for m in rx.finditer(blob):
                hits.append((c["id"], name, m.group(0)))
    return hits


def write_jsonl(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--in", dest="indir", required=True, help="katalog z ORYGINALNYMI plikami")
    ap.add_argument("--out", dest="outdir", required=True, help="katalog wyjsciowy (np. data/processed)")
    a = ap.parse_args()

    out = Path(a.outdir)
    out.mkdir(parents=True, exist_ok=True)
    f_scen = find_file(a.indir, r"MINISTRANCI.*WER|scenariusz")

    scenes = parse_screenplay(f_scen)
    removed = [s.number for s in scenes if s.removed]
    c_scen = scene_chunks(scenes, nfc(f_scen.name))

    write_jsonl(out / "chunks.jsonl", c_scen)

    # kontrola spojnosci
    ids = [c["id"] for c in c_scen]
    dup = {i for i in ids if ids.count(i) > 1}
    nums = [s.number for s in scenes]
    words = [len(c["tekst"].split()) for c in c_scen]

    rep = []
    rep.append("RAPORT prepare_data.py")
    rep.append(f"scenariusz : {nfc(f_scen.name)}")
    rep.append(f"chunki     : {len(c_scen)} (scen w pliku: {len(scenes)}, usunietych/skreslonych: {removed})")
    rep.append(f"slow w chunku min/mediana/max = "
               f"{min(words)}/{sorted(words)[len(words)//2]}/{max(words)}")
    rep.append(f"duplikaty numerow scen w scenariuszu: {sorted({n for n in nums if nums.count(n) > 1}) or 'brak'}")
    rep.append(f"duplikaty id chunkow: {sorted(dup) or 'brak'}")

    hits = audit(c_scen)
    rep.append("")
    rep.append(f"AUDYT (e-mail, telefon, adres, link): {len(hits)} trafien")
    for cid, kind, val in hits:
        rep.append(f"  [{cid}] {kind}: {val}")
    (out / "raport.txt").write_text("\n".join(rep) + "\n", encoding="utf-8")
    print("\n".join(rep))
    sys.exit(1 if hits else 0)


if __name__ == "__main__":
    main()
