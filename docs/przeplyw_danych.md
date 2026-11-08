# Przepływ danych: od scenariusza do indeksu RAG

Jak PDF scenariusz zamienia się w chunki z metadanymi w Qdrancie. Ustalenia
o samych danych (rola dokumentów, numeracja scen, rozbieżności) są w
[notatki_dane.md](notatki_dane.md).

## Przegląd

```
data/raw/scenariusz.pdf
   │
   │  prepare_data.py  ──► src/rag/screenplay.py
   │    1. read_lines      linie z pozycją na stronie (pdfplumber), bez strony tytułowej
   │    2. group_scenes    nagłówek sceny otwiera scenę, kolejne linie to jej treść
   │    3. build_paragraphs  linie → akapity, kwestie jako „POSTAĆ: tekst”
   │    4. scene_chunks    1 scena = 1 chunk (+ nagłówek w tekście, metadane)
   │    5. audyt + raport
   ▼
data/processed/chunks.jsonl  (+ raport.txt)
   │
   │  make ingest DATA=data/processed/chunks.jsonl  ──► src/rag/ingest.py
   │    6. prepared.load_prepared   rekord JSONL → Chunk (source_ids, etykiety)
   │    7. embeddings.embed_documents
   │    8. store.upsert_chunks      wektor „dense” + payload
   ▼
Qdrant, kolekcja z .env (COLLECTION)
```

Tylko scenariusz trafia do indeksu. Pozostałe dokumenty produkcji (tabela inscenizacyjna,
listy, spis obiektów…) nie są przetwarzane – służą do budowy złotego zbioru.

## Uruchomienie

```bash
uv run python prepare_data.py --in data/raw --out data/processed
make ingest DATA=data/processed/chunks.jsonl ARGS=--recreate
```

- `--in` – katalog ze scenariuszem; plik jest wybierany po nazwie (`scenariusz` albo
  `MINISTRANCI…WER`).
- Ścieżkę do `chunks.jsonl` podawaj w `make ingest` jawnie. Domyślne `DATA_DIR=data` wczytałoby
  rekurencyjnie cały `data/` – także PDF z `data/raw/` (parserem startera, po stronach) i pliki
  demo z `data/sample/`.
- `--recreate` czyści kolekcję przed zapisem.

## Etap 1: odczyt PDF-a – `read_lines`

`pdfplumber` zwraca każdą linię tekstu z pozycją: numer strony, tekst, wcięcie `x0`, `top`,
`bottom` (punkty PDF). Tekst przechodzi przez `clean`: Unicode NFC, twarde spacje i tabulatory
→ spacja, obcięcie spacji.

**Strona 1 (tytułowa, nazwisko reżysera) jest pomijana** (`SKIP_PAGES = 1`) – to element
anonimizacji.

Pełna normalizacja startera (`src/rag/normalize.py`) nie jest stosowana – uzasadnienie
w [notatki_dane.md › Normalizacja tekstu](notatki_dane.md#normalizacja-tekstu).

## Etap 2: podział na sceny – `group_scenes`

Każda linia jest klasyfikowana:

1. **Numer strony** – sama liczba z `x0 > 450` → pomijana.
2. **Nagłówek sceny** (`match_heading`) – wszystkie warunki naraz:
   - zaczyna się numerem sceny: cyfry, opcjonalnie litera i gwiazdki (`12`, `6A`, `117*`, `117**`),
   - po numerze kropka (`12. `) albo – tylko po gwiazdkach – sama spacja (`117** PL.`),
   - reszta linii WIELKIMI LITERAMI,
   - `x0 < 150` (lewy margines).

   Nagłówek otwiera nową scenę.
3. **Treść** – każda inna linia należy do bieżącej sceny. Tekst przed pierwszą sceną jest
   odrzucany.

Nagłówek jest rozbijany po kropkach przez `parse_heading`:

```
117*. PL. DROGA PRZY KOŚCIELE. DZIEŃ
  │    │          │              └── pora dnia: DZIEŃ / NOC / WIECZÓR / ŚWIT / DZIEŃ/SEKWENCJA
  │    │          └── miejsce akcji (wszystko pomiędzy)
  │    └── WN / PL / PL/WN (wnętrze / plener), jeśli jest pierwszym członem
  └── numer sceny
```

Brakujący człon (np. `117A. UJĘCIE Z TELEFONU FILIPA` – bez WN/PL i pory) daje `None`.

Scena z `USUNIĘTA`/`SKREŚLONA` w nagłówku jest oznaczana jako usunięta (`Scene.removed`)
i nie dostaje chunka.

## Etap 3: akapity – `build_paragraphs`

Rodzaj linii zależy od wcięcia (`line_kind`); progi odpowiadają układowi tego scenariusza:

| `x0` | Rodzaj | Przykład | Co się dzieje |
| --- | --- | --- | --- |
| < 170 | akcja | `Filip wchodzi do kościoła.` | linie sklejane w akapit; odstęp > 8 pt = nowy akapit |
| 170–240 | dialog, didaskalia | `Gdzie on idzie?`, `(Szeptem)` | doklejane do bieżącej kwestii |
| ≥ 240 | postać | `PROBOSZCZ` | otwiera kwestię |

Wynik:

```
Skonsternowani ludzie idą za figurą. Proboszcz z monstrancją jest kompletnie zaskoczony.
PROBOSZCZ: Gdzie on idzie?
WIKARY: Nie wiem.
FILIP: (Szeptem) Pomóżcie mi.
```

`CUT TO:` (x0 ≈ 473) jest klasyfikowane jak postać, ale nie ma po nim dialogu, więc znika
z tekstu – to przejście montażowe, nie treść.

## Etap 4: chunki – `scene_chunks`

**1 scena = 1 chunk.** Tekst chunka zaczyna się nagłówkiem z numerem, miejscem i stroną,
a w drugiej linii – listą mówiących postaci. Embedding „widzi” więc też te informacje:

```
Scena 117* | PL. DROGA PRZY KOŚCIELE. DZIEŃ | scenariusz, s. 114
Postacie: PROBOSZCZ, WIKARY
Skonsternowani ludzie idą za figurą. …
```

**Postacie** (`scene_characters`, `character_names`) to imiona z linii przed kwestiami, bez
powtórzeń, w kolejności pierwszej kwestii:

- dopiski techniczne są odcinane: `GUCCI FACETIME`, `PROBOSZCZ O.S.`, `OJCIEC DOMINIKI GŁOS`,
  `… OFF`, `… NAGRANIE`, `… EKRAN`, `… GŁOŚNIK` → samo imię;
- `MAŁY KURCZAK/KURCZAK` (kwestia mówiona razem) → dwie postacie;
- literówki ze scenariusza poprawiane: `FIILP`, `FILIPA` → `FILIP`;
- `CUT TO:` i `KONIEC` (ta sama kolumna co imiona) nie są postaciami;
- scena bez kwestii → brak linii `Postacie:`, pole `postacie: []`.

Pozostałe zasady:

- Scena bez treści (wstawki: `95A`, `103A`, `117A`) → tekst `(brak opisu)` pod nagłówkiem.
- Scena dłuższa niż `MAX_WORDS = 900` słów → części po akapitach, każda z nagłówkiem
  i dopiskiem `(część i/k)` oraz linią `Postacie:`, id `scenariusz-40A-1`, `scenariusz-40A-2`…
  Na obecnych danych żadna scena nie przekracza limitu (najdłuższa, scena 1: 752 słowa).

Rekord w `chunks.jsonl` (jedna linia JSON):

```json
{"id": "scenariusz-117*", "dokument": "scenariusz", "zrodlo": "scenariusz.pdf",
 "tekst": "Scena 117* | PL. DROGA PRZY KOŚCIELE. DZIEŃ | scenariusz, s. 114\nPostacie: PROBOSZCZ, WIKARY\n…",
 "meta": {"scena": "117*", "obiekt": "DROGA PRZY KOŚCIELE", "wn_pl": "PL",
          "pora": "DZIEŃ", "strona": 114, "strona_do": 114,
          "postacie": ["PROBOSZCZ", "WIKARY"]}}
```

| Pole `meta` | Źródło |
| --- | --- |
| `scena` | numer z nagłówka |
| `obiekt`, `wn_pl`, `pora` | człony nagłówka (`parse_heading`) |
| `strona` | strona nagłówka |
| `strona_do` | strona ostatniej linii treści sceny |
| `postacie` | imiona przed kwestiami (`scene_characters`) |

## Etap 5: audyt i raport – `prepare_data.py`

- **Audyt** przeszukuje tekst i metadane każdego chunka: e-mail, telefon, kod pocztowy, adres
  (`ul./al./os.`), link, numer mieszkania. Niczego nie zmienia – zgłasza trafienia.
- **`raport.txt`**: liczba chunków i scen, usunięte sceny, długość chunków (min/mediana/max
  słów), duplikaty numerów scen i id chunków, wynik audytu.
- **Kod wyjścia 1, gdy audyt coś znalazł.** `chunks.jsonl` jest zapisany mimo to – nie
  indeksuj go, dopóki trafienia nie zostaną wyjaśnione.

## Etap 6: adapter – `prepared.load_prepared`

Przy `make ingest` pliki `.jsonl` omijają normalizację i chunking startera (`ingest.build_chunks`).
Adapter zamienia rekord na `Chunk` i dokłada pola wymagane przez starter:

| Pole | Wartość | Do czego |
| --- | --- | --- |
| `source` | `scenariusz` | etykieta źródła |
| `source_ids` | `["scenariusz#s117*"]` | ewaluacja – złoty zbiór wskazuje oczekiwane sceny tymi id |
| `page` | = `strona` | etykieta `scenariusz, s. 114` |
| `section` | `scena 117*` | etykieta `… › scena 117*` |
| `chunking` | `prepared` | składnik id punktu |
| `chunk_index` | = `id` rekordu | stabilne id punktu (uuid5) – ponowny ingest nadpisuje zamiast dublować |

Pola o wartości `None` są usuwane. `zrodlo` (nazwa pliku) nie trafia do payloadu.

`source_ids` odnoszą się do **sceny**, nie do chunka: części jednej długiej sceny mają to samo
id, więc recall@k liczy się per scena niezależnie od chunkingu.

## Etapy 7–8: embedding i zapis

- `embeddings.embed_documents` – wektory z modelu `EMBED_MODEL` (`.env`), w paczkach po 32.
- `store.upsert_chunks` – punkt z wektorem nazwanym `dense` i payloadem: metadane chunka,
  `text` i `embed_model`. `retrieve` porównuje `embed_model` z bieżącym ustawieniem i rzuca
  błąd, gdy kolekcję zbudowano innym modelem.

Pełny payload jednego punktu:

```json
{
  "text": "Scena 117* | PL. DROGA PRZY KOŚCIELE. DZIEŃ | scenariusz, s. 114\nPostacie: PROBOSZCZ, WIKARY\n…",
  "embed_model": "qwen3-embedding:0.6b",
  "scena": "117*", "obiekt": "DROGA PRZY KOŚCIELE", "wn_pl": "PL", "pora": "DZIEŃ",
  "strona": 114, "strona_do": 114, "postacie": ["PROBOSZCZ", "WIKARY"],
  "page": 114, "section": "scena 117*",
  "source": "scenariusz", "source_ids": ["scenariusz#s117*"],
  "chunking": "prepared", "chunk_index": "scenariusz-117*"
}
```

## Testy

Bez PDF-a, sieci i Qdranta (`make test`):

- [tests/test_screenplay.py](../tests/test_screenplay.py) – nagłówki (`117*`, `117**`,
  odrzucanie linii, które nie są nagłówkami), rozbicie nagłówka, akapity i dialogi, grupowanie
  w sceny (odtworzona strona 114), sceny usunięte, dzielenie długich scen, postacie (dopiski,
  `/`, literówki, `CUT TO:`; linia `Postacie:` w tekście i w każdej części długiej sceny).
- [tests/test_prepared.py](../tests/test_prepared.py) – `source_ids`, payload i etykiety,
  stabilność id.

## Ograniczenia

- **Progi wcięć i warunki nagłówka są dopasowane do tego PDF-a** (stałe na początku
  `screenplay.py`). Inny eksport scenariusza może wymagać ich korekty – sprawdź rozkład `x0`
  linii i raport.
- **Postacie = tylko mówiące.** Postać obecna, ale bez kwestii (np. Proboszcz w scenie 117)
  nie trafia na listę; postać mówiąca przez telefon (`OJCIEC DOMINIKI FACETIME`) – trafia,
  choć nie jest na miejscu akcji.
- **Imiona nie są scalane między wariantami**: `PROBOSZCZ` / `KSIĄDZ PROBOSZCZ`,
  `REKOLEKCJONISTA` / `KSIĄDZ REKOLEKCJONISTA`, `MATKA` / `MATKA FILIPA` to osobne wpisy –
  czy to te same osoby, wymaga decyzji (patrz notatki).
- Metadane (`obiekt`, `pora`, `wn_pl`, `postacie`) nie są jeszcze używane w wyszukiwaniu –
  nadają się do metadata filtering (TODO w `src/rag/retrieve.py`).
- Przypisy autora `*(kwestia do rozstrzygnięcia…)` i `QUICK CUT:` zostają w tekście.
