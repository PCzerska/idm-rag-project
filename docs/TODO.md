# TODO – system RAG „Ministranci”

Lista zadań rozwojowych systemu. Uzasadnienia i wyniki: [notatki_dane.md](notatki_dane.md),
[notatki_testy.md](notatki_testy.md), opis przepływu danych: [przeplyw_danych.md](przeplyw_danych.md).

## Stan obecny

- Źródło wiedzy: scenariusz (PDF) → 140 chunków (1 scena = 1 chunk, linia `Postacie:` w tekście
  i pole `postacie` w metadanych) → kolekcja `scenariusz_sceny` w Qdrancie.
- Retrieval: dense (`qwen3-embedding:0.6b`), top-k = 5, bez filtrów.
- `ask` (odpowiedź z cytowaniami) i `agent` (narzędzie `search_docs`) działają na tej kolekcji.

## Generowanie odpowiedzi

- [ ] **Cytaty pod odpowiedzią** – `best_quote` (`src/rag/generate.py`) dokleja nagłówek sceny
      i linię `Postacie:` do pierwszego zdania („46 Filip kończy…”). Pomijać je przy wyborze
      cytatu + test. ([szczegóły](notatki_testy.md#błąd-cytaty-zaczynają-się-od-numeru-strony))

## Ewaluacja

- [ ] **Złoty zbiór** (`eval/*.jsonl`, `source_ids` w formacie `scenariusz#sN`):
  - [ ] format, liczba pytań, podział dev/test;
  - [ ] typy pytań: numer sceny, postacie, miejsce/wydarzenie, sekwencja wielu scen,
        agregujące (oznaczone osobno – mierzą ograniczenie top-k);
  - [ ] źródła: tabela inscenizacyjna, listy rekwizytów i grafik, spis obiektów;
  - [ ] pominąć sceny z rozbieżnościami ([lista](notatki_dane.md#sceny-wykluczone-ze-złotego-zbioru-rozbieżności-tabela--scenariusz));
  - [ ] sprawdzić scenę `84B` (brak `84A`) – wykluczyć, jeśli tabela się nie zgadza;
  - [ ] bez nazwisk statystów i adresów z dokumentów produkcji.
- [ ] **Wynik bazowy** – `make eval` dla `scenariusz_sceny` (recall@k, MRR; potem `--generate`
      dla jakości odpowiedzi). Punkt odniesienia dla wszystkich eksperymentów niżej.

## Przygotowanie danych – decyzje otwarte

- [ ] Scalanie wariantów imion: `PROBOSZCZ` / `KSIĄDZ PROBOSZCZ`, `REKOLEKCJONISTA` /
      `KSIĄDZ REKOLEKCJONISTA`, `MATKA` / `MATKA FILIPA`.
- [ ] Postacie niemówiące: sceny bez dialogu (np. 40A) mają pustą listę `postacie` – czy
      uzupełniać je z opisu akcji.

## Retrieval – eksperymenty (każdy jako osobna kolekcja / flaga, porównanie z wynikiem bazowym)

- [ ] Hybrid search dense + BM25 – pytania o numery scen, imiona, rekwizyty (dense słabo trafia
      w `40A`); uwaga na tokenizację `117*` / `117**`.
- [ ] Metadata filtering + indeksy payloadu dla `scena`, `obiekt`, `postacie`.
- [ ] Filtr `postacie = X` w narzędziu agenta – pytania „we wszystkich scenach”, których top-k
      nie obsłuży.
- [ ] Sekwencje wielu scen (np. 117 / 117* / 117**): większe k albo dołączanie sąsiednich scen.
- [ ] Mniejsze chunki (~150 słów z nagłówkiem sceny).
- [ ] Instrukcja zapytania dla embeddera (query instruction).
- [ ] Inny model embeddingów (wymaga ponownego ingestu i aktualizacji README).

## Testy automatyczne

- [ ] `prepare_data.py`: audyt danych osobowych (wzorce, kod wyjścia 1 przy trafieniach), raport.
- [ ] `ingest`: ścieżka plików `.jsonl` → `prepared.load_prepared` (bez normalizacji i chunkingu
      startera).

## Do potwierdzenia z produkcją

- [ ] Znaczenie symboli numeracji scen (`A`/`B`, `*`/`**`) – teraz wywnioskowane z konwencji
      ([notatki_dane.md](notatki_dane.md#numeracja-scen)).

## Zrobione

- [x] Parsowanie scenariusza w `src/rag/screenplay.py` (testy: `tests/test_screenplay.py`).
      Naprawione rozpoznawanie scen `117*` i `117**` (wcześniej doklejane do 117A) –
      140 chunków zamiast 138.
- [x] Chunkowany jest tylko scenariusz; parsery pozostałych dokumentów usunięte z
      `prepare_data.py` (dostępne w historii gita, commit 6a28f57).
- [x] Postacie w chunkach: linia `Postacie:` w tekście i pole `postacie` w metadanych.
- [x] Indeksowanie w osobnej kolekcji `scenariusz_sceny` (ingest z `--recreate`).
