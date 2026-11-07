#!/usr/bin/env python3
"""Stratyfikowany podzial pytan na zbior walidacyjny (40) i testowy (80).

Wejscie : eval/pytania.jsonl (wszystkie 120 pytan, format startera)
Wyjscie : eval/walidacyjny.jsonl, eval/testowy.jsonl

Zasady:
- pytania, ktore juz maja tag "walidacyjny", zostaja w walidacyjnym (np. te zadane recznie
  podczas testow systemu - nie wolno ich uzywac do pomiaru koncowego);
- brakujace miejsca w walidacyjnym dopelniane losowo (ustalone ziarno) w kazdej kategorii;
- reszta trafia do testowego.
Uruchom ponownie po dopisaniu/zmianie pytan - przy tym samym ziarnie podzial jest powtarzalny.

  python scripts/split_questions.py                 # domyslnie ziarno 42
  python scripts/split_questions.py --seed 7
"""
import argparse
import json
import random
from collections import Counter
from pathlib import Path

QUOTA_VAL = {"fakt": 10, "kilka_zrodel": 10, "agregacja": 7, "ciaglosc": 5, "parafraza": 3, "spoza_zakresu": 5}
QUOTA_ALL = {"fakt": 30, "kilka_zrodel": 30, "agregacja": 20, "ciaglosc": 15, "parafraza": 10, "spoza_zakresu": 15}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src", default="eval/pytania.jsonl")
    ap.add_argument("--outdir", default="eval")
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()

    items = [json.loads(l) for l in Path(a.src).read_text(encoding="utf-8").splitlines() if l.strip()]
    cat = lambda q: q["tags"][0]  # kategoria = pierwszy tag
    counts = Counter(map(cat, items))
    assert dict(counts) == QUOTA_ALL, f"liczba pytan w kategoriach {dict(counts)} != {QUOTA_ALL}"
    assert len({q["id"] for q in items}) == len(items), "powtorzone id"

    rng = random.Random(a.seed)
    val_ids = {q["id"] for q in items if "walidacyjny" in q["tags"]}
    for c, need in QUOTA_VAL.items():
        have = [q for q in items if cat(q) == c and q["id"] in val_ids]
        assert len(have) <= need, f"kategoria {c}: {len(have)} pytan juz w walidacyjnym, limit {need}"
        pool = sorted((q["id"] for q in items if cat(q) == c and q["id"] not in val_ids))
        val_ids.update(rng.sample(pool, need - len(have)))

    val, test = [], []
    for q in items:
        split = "walidacyjny" if q["id"] in val_ids else "testowy"
        q = {**q, "tags": [cat(q), split]}
        (val if split == "walidacyjny" else test).append(q)
    assert len(val) == 40 and len(test) == 80

    out = Path(a.outdir)
    for name, rows in (("walidacyjny.jsonl", val), ("testowy.jsonl", test)):
        (out / name).write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    print(f"walidacyjny: {len(val)} {dict(Counter(map(cat, val)))}")
    print(f"testowy    : {len(test)} {dict(Counter(map(cat, test)))}")


if __name__ == "__main__":
    main()
