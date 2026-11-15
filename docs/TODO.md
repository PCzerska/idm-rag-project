# TODO – system RAG „Ministranci”

Lista zadań rozwojowych systemu. Uzasadnienia i wyniki: [notatki_dane.md](notatki_dane.md),
[notatki_testy.md](notatki_testy.md), opis przepływu danych: [przeplyw_danych.md](przeplyw_danych.md),
ewaluacja: [ewaluacja.md](ewaluacja.md) (jak mierzyć), [zloty_zbior.md](zloty_zbior.md) (zbiór i wyniki).

## Stan obecny

- Źródło wiedzy: scenariusz (PDF) → 140 chunków (1 scena = 1 chunk, linia `Postacie:` w tekście
  i pole `postacie` w metadanych) → kolekcja `scenariusz_sceny` w Qdrancie.
- Retrieval: dense (`qwen3-embedding:0.6b`), top-k = 5, bez filtrów.
- `ask` (odpowiedź z cytowaniami) i `agent` (narzędzie `search_docs`) działają na tej kolekcji.
- Złoty zbiór: 120 pytań (`eval/pytania_ministranci.jsonl`, wszystkie `dev`); pomiar
  referencyjny E1 (`qwen3.5:4b`) w `eval/baseline/` – [omówienie](zloty_zbior.md#wyniki-e1).

## Generowanie odpowiedzi

- [ ] **Cytaty pod odpowiedzią** – `best_quote` (`src/rag/generate.py`) dokleja nagłówek sceny
      i linię `Postacie:` do pierwszego zdania („46 Filip kończy…”). Pomijać je przy wyborze
      cytatu + test. ([szczegóły](notatki_testy.md#błąd-cytaty-zaczynają-się-od-numeru-strony))

## Ewaluacja

- [ ] **Złoty zbiór** (`eval/*.jsonl`, `source_ids` w formacie `scenariusz#sN`) –
      [opis](zloty_zbior.md):
  - [x] format (`pytania_ministranci.jsonl` → `convert_questions.py` → `pytania.jsonl`),
        120 pytań, 8 kategorii, 4 poziomy trudności;
  - [x] typy pytań: fakt z jednej sceny, kilka źródeł, ciągłość przedmiotu, agregacja
        (oznaczone osobno – mierzą ograniczenie top-k), parafrazy potoczne, odmowy;
  - [x] podział: pole `zbior`/`split`, wszystkie 120 pytań = `dev`;
  - [ ] **zbiór `test`** – napisać przed strojeniem pod błędy z dev (zasady:
        [ewaluacja.md](ewaluacja.md#podział-dev--test));
  - [x] **źródło: tylko scenariusz** – inne dokumenty produkcji nie są używane
        ([decyzja](notatki_dane.md#decyzja-rola-dokumentów-2026-11-15)); 19 pytań o sceny z listy
        rozbieżności tabela ↔ scenariusz (np. `40A`, `84B`, `103A`, `117*`) zostaje;
  - [ ] bez nazwisk statystów i adresów z dokumentów produkcji.
- [x] **Wynik bazowy E1** – `scenariusz_sceny`, `qwen3.5:4b`, retrieval + generacja
      (`eval/baseline/E1.*`) – [omówienie](zloty_zbior.md#wyniki-e1).
- [ ] **Ocena ręczna E1** – wypełnić `ocena` / `przyczyna_bledu` w `eval/baseline/E1_ocena.csv`,
      potem `uv run rag grade` i wynik do [zloty_zbior.md](zloty_zbior.md).
- [x] **Odmowa ustalana w kodzie** (`generate.is_refusal`: formuła odmowy w 1. zdaniu albo brak
      cytatów) – wcześniej odmowy z doklejonymi `[1][2][3]` liczyły się jako odpowiedzi
      (11 w E1). E1 przeliczony `rag rescore` ([szczegóły](ewaluacja.md#odmowa)).
- [x] **E2: `qwen3.5:9b`** – ten sam indeks i zbiór (`eval/baseline/E2.*`,
      [omówienie](zloty_zbior.md#wyniki-e2-qwen359b)): lepsze cytowania, gorsze liczenie, 4× wolniej;
      główne błędy to retrieval → eksperymenty dalej na 4b.
- [ ] **Ocena ręczna E2** (`eval/baseline/E2_ocena.csv`) – opcjonalnie, po E1.
- [x] **Zepsuty JSON w trybie `prompt` przy cudzysłowach „…”** – model zamykał „ zwykłym `"`;
      `structured.repair_quotes` + wskazówka przy ponowieniu + surowa odpowiedź w błędzie.
      q088 (E1) i q040, q074, q088 (E2) zadane ponownie – 0 `gen_error`.
- [x] **Zawężenie heurystyki odmowy** – „nie jest podany/określony…”, „nie znajduje się”,
      „nie wspomina”, „nie podaje” tylko obok słowa o źródle (fałszywy alarm q080 w E2).
      E1 i E2 przeliczone `rag rescore` – zmieniło się tylko q080 w E2.

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
- [ ] Scena `84B` bez `84A` w scenariuszu – informacyjnie, nie wpływa na system ani złoty zbiór.

## Zrobione

- [x] Parsowanie scenariusza w `src/rag/screenplay.py` (testy: `tests/test_screenplay.py`).
      Naprawione rozpoznawanie scen `117*` i `117**` (wcześniej doklejane do 117A) –
      140 chunków zamiast 138.
- [x] Chunkowany jest tylko scenariusz; parsery pozostałych dokumentów usunięte z
      `prepare_data.py` (dostępne w historii gita, commit 6a28f57).
- [x] Postacie w chunkach: linia `Postacie:` w tekście i pole `postacie` w metadanych.
- [x] Indeksowanie w osobnej kolekcji `scenariusz_sceny` (ingest z `--recreate`).
