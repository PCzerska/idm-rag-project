# Notatki o danych projektu „Ministranci”

Ustalenia z analizy dokumentów źródłowych (2026-11-08). Wnioski o znaczeniu symboli są
wywnioskowane z konwencji produkcji filmowej i treści scen – dokumenty nie mają legendy.
Do potwierdzenia z produkcją.

Jak scenariusz jest przetwarzany na chunki – krok po kroku: [przeplyw_danych.md](przeplyw_danych.md).
Lista zadań zespołu: [TODO.md](TODO.md).
Wyniki testów ręcznych: [notatki_testy.md](notatki_testy.md).

## Decyzja: rola dokumentów (2026-11-15)

- **Scenariusz = jedyne źródło wiedzy** – zarówno dla systemu (indeks w Qdrancie), jak i dla
  złotego zbioru: pytania, oczekiwane odpowiedzi i źródła (`scenariusz#sN`) pochodzą wyłącznie
  ze scenariusza.
- **Pozostałe dokumenty produkcji** (tabela inscenizacyjna, rekwizyty, grafiki, spis obiektów)
  **nie są używane** – ani w indeksie, ani w złotym zbiorze.
- Skoro system i złoty zbiór opierają się na tym samym dokumencie, **rozbieżności między
  scenariuszem a tabelą nie wpływają na działanie ani ocenę systemu**. Sceny z listy rozbieżności
  (np. `40A`, `84B`, `103A`, `117*`) mogą występować w złotym zbiorze – obecnie odwołuje się
  do nich 19 pytań, wszystkie oparte na scenariuszu.
- Wcześniej (2026-11-08) pozostałe dokumenty miały służyć do budowy złotego zbioru, a sceny
  z rozbieżnościami były z niego wykluczone. Zasada przestała być potrzebna, gdy zbiór powstał
  w całości ze scenariusza. **Jeśli kiedyś wrócimy do innych dokumentów**, pytania oparte na nich
  nie mogą dotyczyć scen z [listy rozbieżności](#rozbieżności-tabela--scenariusz).

## Dane wejściowe

- Scenariusz: `data/raw/scenariusz.pdf` (ignorowany przez git) – oryginalnie
  `MINISTRANCI WER_REAL_12.08.pdf`, wersja realizacyjna z 12.08.2024.
  Uruchomienie: `uv run python prepare_data.py --in data/raw --out data/processed`.
- Pozostałe dokumenty: `../dane/ministranci/` (poza repo) – tabela, listy rekwizytów i grafik,
  spis obiektów, lista obsady, breakdown VFX, `SS _ Ministranci 18.07.pdf`, książka obiektów.
  Nie są przetwarzane ani używane w złotym zbiorze (decyzja wyżej).
- Anonimizacja scenariusza: pomijana strona tytułowa (nazwisko reżysera) + audyt wyniku
  (e-mail, telefon, kod pocztowy, adres, link). Listy zamian i słów zakazanych z E1 usunięte –
  dotyczyły wyłącznie adresów ze spisu obiektów i nazwisk statystów z tabeli, nie scenariusza.
- Wynik: 140 chunków scenariusza (146 scen, 6 usuniętych); słów w chunku min/mediana/max = 12/98/752;
  92 sceny z mówiącymi postaciami, 41 postaci.

## Decyzje: indeksowanie (2026-11-08)

Przyjęte:

1. **Osobna kolekcja z nazwą wariantu**, np. `scenariusz_sceny` (w `.env`: `COLLECTION=…`), zamiast
   wspólnej `docs` z plikami demo startera. Kolejne warianty indeksować obok
   (`make ingest … ARGS="--collection scenariusz_<wariant>"`), żeby porównywać je w ewaluacji
   bez przebudowy.
2. **Po każdej zmianie chunków ingest z `--recreate`.** `upsert` dodaje i nadpisuje punkty, ale nie
   usuwa starych – bez `--recreate` w kolekcji zostałyby nieaktualne chunki (np. po zmianie id).
3. **Granulacja: 1 scena = 1 chunk** jako wersja bazowa. Mniejsze fragmenty (np. ~150 słów
   z nagłówkiem sceny) – później, jako osobna kolekcja; `source_ids` liczą się per scena, więc
   wyniki będą porównywalne. Uwaga: długość chunków różni się ~20× (10%/50%/90% = 33/95/232 słowa).
4. **Postacie w chunku** – zrobione: linia `Postacie: …` w tekście (druga linia, w każdej części
   długiej sceny) i pole `postacie` w metadanych. Szczegóły: [przeplyw_danych.md](przeplyw_danych.md#etap-4-chunki--scene_chunks).
   Warianty imion (np. `PROBOSZCZ` / `KSIĄDZ PROBOSZCZ`) są teraz osobno – decyzja otwarta,
   patrz [TODO.md](TODO.md#przygotowanie-danych--decyzje-otwarte).

Na później:

5. **Hybrid search (dense + BM25).** Pytania o scenariusz zawierają dokładne tokeny (numery scen
   `40A`, `117*`, imiona, rekwizyty), z którymi embeddingi radzą sobie słabo. Szkielet: TODO(hybrid)
   w `src/rag/retrieve.py` i `src/rag/store.py`. Uwaga na tokenizację `117*`/`117**` (BM25
   prawdopodobnie odetnie gwiazdki).
6. **Metadata filtering i indeksy payloadu.** Dodać indeksy dla `scena`, `obiekt` (i `postacie`) –
   teraz są tylko dla `source` i `chunking`. Rozważyć regułę: pytanie z „scena N” → filtr
   `scena = N` (uwaga: może za mocno zawęzić pytania o sąsiednie sceny). Szkielet:
   TODO(metadata-filtering) w `src/rag/retrieve.py`.

## Normalizacja tekstu

README opisuje ścieżkę startera `PDF/MD/TXT → normalizacja → chunking → embedding`. **Chunki
scenariusza tę normalizację (`src/rag/normalize.py`) omijają** – `ingest.build_chunks` dla plików
`.jsonl` woła `prepared.load_prepared` i pomija `normalize_blocks` oraz chunking startera.

Zamiast niej `src/rag/screenplay.py` robi lekką normalizację dopasowaną do scenariusza:

- `clean` – Unicode NFC, twarde spacje i tabulatory → spacja, obcięcie spacji;
- `build_paragraphs` – sklejanie linii w akapity (naprawa łamania wierszy z PDF-a);
- pomijanie numerów stron i tekstu przed pierwszą sceną.

Dlaczego bez normalizacji startera (sprawdzone na 140 chunkach, 2026-11-08):

- problemów, które naprawia `basic_clean`, w scenariuszu nie ma: 0 ligatur, 0 miękkich dywizów
  i twardych spacji, 0 wyrazów przenoszonych dywizem, 0 rozstrzelonych liter, tekst już w NFC
  (scenariusz to PDF wygenerowany z edytora, nie skan ani wyjustowany druk);
- `join_split_words` zmieniłoby 3 chunki, z czego 2 błędnie: `Jezu staczamy` → `Jezustaczamy`,
  `parafia nie` → `parafianie` (trzeci: `jak byś` → `jakbyś` – zmiana zapisu autora w dialogu).
  Heurystyka „słownik z korpusu” pomaga przy PDF-ach z rozbitymi wyrazami, a w czystym tekście
  skleja poprawne pary słów.

Wniosek: normalizacja startera nie jest potrzebna i by szkodziła. Wrócić do tego tylko przy
zmianie źródła (inny eksport PDF, skan) – wtedy powtórzyć powyższą kontrolę.

## Numeracja scen

| Symbol | Gdzie | Znaczenie |
| --- | --- | --- |
| `6A`, `109B` | scenariusz | **Nowa scena** dopisana po zamrożeniu numeracji (własne miejsce akcji, nie część sceny bazowej). |
| `USUNIĘTA` | scenariusz | Scena wycięta, numer zostaje (7, 34, 39, 68–70). |
| `37A`, `37B`, `17B`, `115A`, `118A` | tylko tabela | **Część sceny** ze scenariusza, rozbitej na plany/dni zdjęciowe (np. 37B = konfesjonał, dzień 6). |
| `117*`, `117**` | scenariusz | Kolejne miejsca jednej sekwencji (procesja): 117 pod kościołem → 117* droga przy kościele → 117** osiedle. Konwencja autorska. |
| `117*` | tabela | Tabela nie rozróżnia `*`/`**`: dzień 27 (pod kościołem) = 117*, dzień 17 (OSIEDLE) = 117**. |

Wniosek: **ta sama litera znaczy co innego w scenariuszu i w tabeli** (np. `17A` w tabeli to
„Sala Ministrantów” = scena 17 ze scenariusza). Nie mapować automatycznie po numerze.

## Rozbieżności tabela ↔ scenariusz

Lista informacyjna – nie wpływa na system ani na złoty zbiór (oba opierają się wyłącznie na
scenariuszu). Ma znaczenie tylko przy ewentualnym powrocie do tabeli lub innych dokumentów.

- Tylko w tabeli: `17B`, `37A`, `37B`, `115A`, `118A`, `117*`.
- Ze scenariusza bez wpisu w tabeli: `6A`, `16A`, `40A`, `78A`, `103A`, `109A`, `109B`, `113A`, `55`.
- Niezgodna numeracja między dokumentami: `17`, `17A` (w tabeli „Sala Ministrantów” = scena 17
  ze scenariusza), `103` (dzień 30 „dom Gucciego” = 103A), `117`, `117*`, `117**`.
- `84B` (brak `84A` w scenariuszu) – osobliwość numeracji samego scenariusza; scena `84B` jest
  w indeksie i w złotym zbiorze traktowana jak każda inna. Znaczenie do potwierdzenia
  z produkcją ([TODO](TODO.md#do-potwierdzenia-z-produkcją)).
