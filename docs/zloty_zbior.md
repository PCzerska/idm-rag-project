# Złoty zbiór i wyniki ewaluacji

Opis zbioru pytań do oceny systemu RAG „Ministranci” i omówienie pomiarów. Jak uruchomić
pomiar i ocenić odpowiedzi: [ewaluacja.md](ewaluacja.md).

## Zbiór pytań

**120 pytań** o scenariusz filmu, z oczekiwaną odpowiedzią i scenami, w których ona jest.

| plik | rola |
|---|---|
| `eval/pytania_ministranci.jsonl` | źródło – edytowane ręcznie |
| `eval/pytania.jsonl` | format startera (generowany przez `scripts/convert_questions.py`) |

Rekord źródłowy:

```json
{"id": "q001", "kategoria": "fakt_jedna_scena", "trudnosc": "latwe", "zbior": "dev",
 "pytanie": "Gdzie dzieje się scena 1 i czy to wnętrze, czy plener?", "typ": "fakt",
 "oczekiwane": "Sala Domu Kultury Katolickiej (widownia i scena); wnętrze (WN), dzień.",
 "zrodla": [{"dokument": "scenariusz", "scena": "1", "strona": 2,
             "fraza": "SALA DOMU KULTURY KATOLICKIEJ"}]}
```

| pole | znaczenie | w `pytania.jsonl` |
|---|---|---|
| `kategoria`, `trudnosc`, `typ` | patrz niżej | `tags = [kategoria, trudnosc, typ]` |
| `zbior` | `dev` / `test` | `split` |
| `pytanie`, `oczekiwane` | pytanie i wzorcowa odpowiedź | `question`, `answer` |
| `zrodla` | scena + strona + **fraza** z tekstu sceny | `source_ids = ["scenariusz#s1"]` |
| `elementy` | oczekiwane elementy listy (np. numery scen) | `elements` |
| – | oczekiwane liczby (typ `liczba`) | `numbers` (z `oczekiwane` albo `elementy`) |

`fraza` pozwala automatycznie sprawdzić, czy źródło nadal pasuje do indeksu po zmianie parsera
– tak wykryto scenę `117*`, wcześniej doklejaną do `117A`.

### Kategorie

| kategoria | n | id | co sprawdza | źródeł (mediana / max) |
|---|---|---|---|---|
| `fakt_jedna_scena` | 20 | q001–q020 | fakt z jednej sceny | 1 / 1 |
| `potoczne_parafrazy` | 10 | q021–q030 | to samo, ale potocznie („ile tego hajsu…”) – odporność embeddingu na styl | 1 / 3 |
| `kilka_zrodel` | 30 | q031–q050, q091–q100 | odpowiedź złożona z kilku scen | 2 / 6 |
| `ciaglosc_przedmiotu` | 20 | q051–q055, q076–q090 | gdzie pojawia się rekwizyt / wątek | 3 / 10 |
| `agregacja_liczenie` | 20 | q056–q075 | liczenie po całym scenariuszu („ile scen w sklepie?”) | 8 / 28 |
| `falszywa_przeslanka` | 5 | q116–q120 | pytanie z błędnym założeniem – oczekiwana korekta | 1 / 3 |
| `brak_w_dokumentach` | 10 | q106–q115 | pytanie o projekt, ale odpowiedzi nie ma w scenariuszu – odmowa | 0 |
| `spoza_zakresu` | 5 | q101–q105 | pytanie niezwiązane z filmem – odmowa | 0 |

Trudność: `latwe` 30, `srednie` 30, `trudne` 40, `brak` 20 (= trzy ostatnie kategorie).
Typ odpowiedzi: `fakt` 36, `wyjasnienie` 26, `liczba` 20, `lista` 18, `odmowa_*` 20.

Zbiór obejmuje 133 różne sceny (na 140 w indeksie). Wszystkie źródła wskazują scenariusz –
jedyne źródło wiedzy systemu i złotego zbioru.

### Ograniczenia wbudowane w zbiór

- **17 pytań ma więcej niż 5 oczekiwanych scen** (13 z `agregacja_liczenie`, do 28 scen).
  Przy `TOP_K=5` recall@5 nie może osiągnąć 1, a model nie widzi wszystkich scen – te pytania
  mierzą ograniczenie top-k, nie jakość embeddingu. Do nich są eksperymenty z filtrami po
  metadanych i agentem ([TODO](TODO.md#retrieval--eksperymenty-każdy-jako-osobna-kolekcja--flaga-porównanie-z-wynikiem-bazowym)).
- **`falszywa_przeslanka` ma źródła**, więc eval traktuje je jak pytania z odpowiedzią:
  poprawna korekta („Filip nie pali, pali Kurczak”) liczy się jako odpowiedź, a odmowa jako
  `false_refusal`. Grupa trudności `brak` łączy te 5 pytań z 15 odmowami – recall dla `brak`
  dotyczy tylko fałszywych przesłanek. O poprawności decyduje ocena ręczna.
- `count_ok` i `list_*` to heurystyki na tekście odpowiedzi (liczby i numery scen) – wymagają
  potwierdzenia w ocenie ręcznej.

### Otwarte kwestie

**Sceny z listy rozbieżności – zostają.** 19 pytań odwołuje się do scen, których numeracja nie
zgadza się między tabelą inscenizacyjną a scenariuszem (np. `40A`, `84B`, `103A`, `117*`):
q024, q058, q061–q064, q066, q069, q070, q074, q078, q083, q086, q087, q095, q096, q098–q100.
Wszystkie są oparte wyłącznie na scenariuszu, a scenariusz jest jedynym źródłem wiedzy systemu
i złotego zbioru, więc rozbieżność z tabelą ich nie dotyczy
([decyzja 2026-11-15](notatki_dane.md#decyzja-rola-dokumentów-2026-11-15)).

**Zbiór testowy** – jeszcze nie istnieje ([zasady](ewaluacja.md#zbiór-testowy--jak-go-napisać)).

## Wyniki E1

Pomiar referencyjny (2026-11-15): `eval/baseline/E1.json`, `E1.md`, `E1_ocena.csv`.

| parametr | wartość |
|---|---|
| indeks | `scenariusz_sceny`, 140 chunków, `chunking=prepared` (1 scena = 1 chunk) |
| embeddingi | `qwen3-embedding:0.6b` z query instruction |
| retrieval | dense, top-k = 5, bez filtrów |
| LLM | `qwen3.5:4b`, `temperature=0`, reasoning `none` |
| structured output | `auto` → `prompt` (120 odpowiedzi) |
| odmowa | ustalana w kodzie (`generate.is_refusal`) – przeliczone `rag rescore` |
| naprawa JSON | `structured.repair_quotes` – q088 zadane ponownie po wdrożeniu |
| zbiór | 120 pytań (`dev`) |

Pole `git_commit` = `f664aec-dirty`: pomiar uruchomiono na lokalnym commicie, który potem
cofnięto (kod bez zmian), a ocenę odmów przeliczono na zapisanych odpowiedziach. q088 (pierwotnie
`gen_error`) zadano ponownie po naprawie cudzysłowów i podmieniono jego wiersz. Wcześniejsza
wersja E1 (kolekcja `docs`, inny podział na chunki) była błędna i została usunięta – nie
porównujemy się do niej.

### Wyniki ogółem

| metryka | E1 |
|---|---|
| recall@1 / @3 / @5 | 0.34 / 0.54 / 0.57 |
| MRR | 0.68 |
| ctx_chars (top-5) | 5097 |
| refusal_ok | 0.82 |
| false_refusal ↓ | 0.21 |
| correct_refusal | 1.00 (15/15) |
| citation_hit | 0.69 |
| cited_recall | 0.47 |
| inline_cited | 0.78 |
| count_ok | 0.15 |
| list_p / list_r / list_f1 | 0.68 / 0.42 / 0.50 |
| gen_error ↓ | 0 |
| latency_s | 11.7 (max 28) |

### Per kategoria

| kategoria | n | recall@5 | MRR | false_refusal ↓ | citation_hit | cited_recall | t [s] |
|---|---|---|---|---|---|---|---|
| `potoczne_parafrazy` | 10 | 0.90 | 0.90 | 0.10 | 0.80 | 0.77 | 11.0 |
| `fakt_jedna_scena` | 20 | 0.80 | 0.65 | 0.05 | 0.70 | 0.70 | 8.3 |
| `falszywa_przeslanka` | 5 | 0.70 | 0.57 | 1.00 | 0.00 | 0.00 | 12.8 |
| `kilka_zrodel` | 30 | 0.57 | 0.62 | 0.27 | 0.67 | 0.49 | 13.5 |
| `ciaglosc_przedmiotu` | 20 | 0.45 | 0.61 | 0.05 | 0.85 | 0.44 | 13.7 |
| `agregacja_liczenie` | 20 | 0.30 | 0.81 | 0.30 | 0.65 | 0.20 | 10.9 |
| `brak_w_dokumentach` | 10 | – | – | – | – | – | 11.5 |
| `spoza_zakresu` | 5 | – | – | – | – | – | 11.2 |

`correct_refusal` = 1.00 w obu kategoriach odmów. `count_ok` (agregacja) = 0.15.

### Wnioski

**1. Retrieval: problemem jest liczba scen, a nie styl pytania.**

- Pytania z 1 źródłem: recall@1 = 0.61, recall@5 = 0.85. Pytania z ≤ 5 źródłami: recall@5 = 0.64,
  z > 5 (17 pytań): **0.22**.
- Parafrazy potoczne (recall@5 = 0.90) wypadają lepiej niż pytania „książkowe”, więc embedding
  radzi sobie ze stylem pytania.
- Agregacja ma wysoki MRR (0.81 – pierwsza scena zwykle trafiona) i niski recall@5 (0.30):
  przy top-5 model nie może policzyć 8–28 scen. Tu potrzebny jest filtr po metadanych lub agent,
  a nie lepszy embedding.
- W 20 ze 105 pytań z odpowiedzią żadna oczekiwana scena nie trafiła do top-5.

**2. Model odmawia za często: 22 fałszywe odmowy (21%).** Podział według `retrieved`:

| sytuacja | liczba | pytania |
|---|---|---|
| sceny **nie było** w kontekście (retrieval) | 12 | q014, q025, q033, q034, q036, q037, q038, q052, q064, q091, q094, q116 |
| scena **była** w kontekście (model) | 10 | q041, q057, q059, q072, q073, q074, q117, q118, q119, q120 |

Do tego 11 odpowiedzi bez cytatu oczekiwanej sceny (w 3 scena była w kontekście). Odmowy na
pytania bez odpowiedzi są bezbłędne (15/15) – model jest raczej zbyt ostrożny niż skłonny do
zmyślania. Kolumna `znalezione_sceny` w arkuszu pozwala wpisać dla każdego błędu
`brak_fragmentu` albo `zla_odmowa` / `blad_ekstrakcji`.

**3. Odmowę ustala kod, a nie model.** `qwen3.5:4b` w trybie `prompt` dokleja znaczniki
`[1][2][3]` także do odmów („W podanym kontekście nie ma informacji… [1][2][3]”). Gdy odmowę
rozpoznawano po samym braku cytatów, 11 odmów liczyło się jako odpowiedzi. Teraz odmowa to
formuła odmowy w **pierwszym** zdaniu albo brak cytatów, a cytaty odmowy się nie liczą.
Zastrzeżenie dopiero w dalszym zdaniu („X [1]. Nie ma informacji o Y”) to częściowa odpowiedź.
Heurystyka sprawdzona na wszystkich 119 odpowiedziach E1 (11 zmian, wszystkie trafne); przy
nowym modelu warto przejrzeć pole `refusal_in_text`.

**4. Fałszywe przesłanki: model odmawia zamiast korygować** (5/5 to odmowy, np. „nie ma
informacji o tym, w której scenie Filip pali”). Oczekiwana jest korekta („Filip nie pali,
próbuje Kurczak”). Eval liczy to jako `false_refusal`. Czy odmowa jest tu akceptowalna,
rozstrzyga ocena ręczna.

**5. Liczenie: `count_ok` = 0.15** – wynika wprost z punktu 1 (top-5 nie obejmuje wszystkich
scen). Lepszy prompt tego nie naprawi.

**6. Stabilność.** Po naprawie cudzysłowów (`structured.repair_quotes`, patrz wnioski E2,
punkt 5) 0 `gen_error`; wcześniej 1/120 (q088). Średnio 11.7 s na pytanie, cały pomiar około
24 min.

### Do zrobienia na podstawie E1

- **Ocena ręczna** `eval/baseline/E1_ocena.csv` (`ocena_auto` dla odmów jest już zgodna
  z tekstem odpowiedzi).
- ~~E2 `qwen3.5:9b`~~ – zrobione, patrz [Wyniki E2](#wyniki-e2-qwen359b).
- Retrieval: filtr / agent dla agregacji i ciągłości (punkt 1), hybrid BM25 dla 20 pytań bez
  trafienia w top-5 – patrz [TODO](TODO.md).

## Wyniki E2: `qwen3.5:9b`

Pomiar 2026-11-15: `eval/baseline/E2.json`, `E2.md`, `E2_ocena.csv`. Jedyna zmiana względem
E1 to LLM: ten sam indeks, embeddingi, top-k i zbiór. Retrieval jest identyczny (recall i MRR
co do liczby), więc różnice dotyczą wyłącznie generacji.

| parametr | wartość |
|---|---|
| LLM | `qwen3.5:9b`, `temperature=0`, reasoning `none` |
| structured output | `auto` → `prompt` (120) – `native` niedostępny już przy 1. pytaniu, tak samo jak przy 4b, więc tryb jest ten sam co w E1 |
| naprawa JSON | q040, q074, q088 (pierwotnie `gen_error`) zadane ponownie po wdrożeniu `structured.repair_quotes` |
| commit | `f664aec-dirty` (niezacommitowane zmiany, kod jak przy przeliczeniu E1) |

### E1 vs E2

| metryka | E1 (4b) | E2 (9b) |
|---|---|---|
| false_refusal ↓ | **0.21** | 0.22 |
| correct_refusal | 1.00 | 0.93 |
| citation_hit | 0.69 | **0.76** |
| cited_recall | 0.47 | **0.51** |
| count_ok | **0.15** | 0.05 |
| list_f1 | **0.50** | 0.39 |
| gen_error ↓ | 0 | 0 |
| latency_s ↓ | **11.7** | 50.1 (47.7 bez q074*) |
| czas pomiaru | ~24 min | ~94 min |

\* q074 przy ponownym zadaniu trwało 327 s (obciążony laptop); czasy pytań zadanych ponownie
nie pochodzą z ciągłego przebiegu.

| kategoria | false_refusal E1 → E2 | citation_hit E1 → E2 | t [s] E2 |
|---|---|---|---|
| `agregacja_liczenie` | 0.30 → **0.05** | 0.65 → **0.90** | 55.8* |
| `fakt_jedna_scena` | **0.05** → 0.20 | 0.70 → **0.80** | 57.2 |
| `ciaglosc_przedmiotu` | **0.05** → 0.15 | 0.85 → 0.85 | 52.5 |
| `kilka_zrodel` | **0.27** → 0.30 | 0.67 → 0.67 | 41.6 |
| `potoczne_parafrazy` | 0.10 → 0.10 | 0.80 → **0.90** | 51.0 |
| `falszywa_przeslanka` | 1.00 → 1.00 | 0.00 → 0.00 | 41.4 |
| `brak_w_dokumentach` | correct_refusal **1.00** → 0.90 | – | 51.4 |
| `spoza_zakresu` | correct_refusal 1.00 → 1.00 | – | 43.5 |

### Wnioski

**1. Większy model nie usuwa głównego problemu, bo leży on w retrievalu.** Z 23 fałszywych
odmów E2 aż 18 dotyczy pytań, w których oczekiwanej sceny nie było w kontekście (E1: 12 z 22).
9b częściej uczciwie odmawia, gdy kontekst nie zawiera odpowiedzi – np. q001 i q016, gdzie
retrieval nie znalazł sceny. Odpowiedzi bez cytatu oczekiwanej sceny: 2 (E1: 11).

**2. 9b częściej odpowiada na agregację, ale liczy gorzej.** Fałszywe odmowy w agregacji
spadły z 0.30 do 0.05, a `count_ok` z 0.15 do 0.05: model liczy sceny, które widzi w top-5,
i podaje za małą liczbę zamiast odmówić. Bez zmian w retrievalu (filtr/agent) żaden model
tego nie rozwiąże. `list_f1` spadło z tego samego powodu w ciągłości i kilku źródłach.

**3. Cytowania są lepsze, liczby gorsze.** `citation_hit` 0.76 i `cited_recall` 0.51 to
najlepsze wyniki (E1: 0.69 / 0.47), ale różnice (4–7 pp na ~100 pytaniach) to kilka pytań.

**4. 4× wolniej.** Średnio około 48 s na pytanie (w ciągłym przebiegu maksymalnie 242 s); pełny pomiar trwa około
półtorej godziny. Przy obecnym retrievalu zysk nie uzasadnia kosztu – **eksperymenty
z retrievalem robimy na 4b** (E1 jako odniesienie), a 9b sprawdzamy na końcu.

**5. Zepsuty JSON przy cudzysłowach – naprawione.** Pierwotnie 3 `gen_error` w E2 (q040, q074,
q088) i 1 w E1 (q088). Odtworzenie z zapisem surowej odpowiedzi potwierdziło przyczynę: model
otwiera cytat polskim `„`, a zamyka zwykłym `"` (`"answer": "Zdanie „to nie jest nasza sprawa"
mówi…"`), co kończy string JSON. Ponowienie nie pomagało – przy `temperature=0` model zwracał
identyczny JSON. Naprawa: `structured.repair_quotes` zamienia `„tekst"` na `„tekst”`, tylko gdy
zwykłe parsowanie się nie uda (poprawny JSON, także z `\"`, zostaje nietknięty); ponowienie
dostaje konkretną wskazówkę, a błąd zapisuje początek surowej odpowiedzi. Te 4 pytania zadano
ponownie: wszystkie dały poprawne odpowiedzi, retrieval bez zmian.

**6. Heurystyka odmowy na stylu 9b.** Pierwsza wersja reguły pomyliła się raz: q080
(„suma pieniędzy *nie jest określona* liczbowo…” w pierwszym zdaniu merytorycznej odpowiedzi).
Regułę zawężono: formy „nie jest podany / określony…”, „nie znajduje się”, „nie wspomina”,
„nie podaje” liczą się jako odmowa tylko obok słowa o źródle („w kontekście”, „w dokumentach”,
„w tekście”…). E1 i E2 przeliczono `rag rescore`; zmieniło się wyłącznie q080 w E2 (pozostałe
decyzje w obu pomiarach bez zmian). Uwaga: q119 (fałszywa przesłanka) to odmowa **z korektą**
(„nie ma informacji, że płaci… W scenie 75A Filip grozi…”), czyli zachowanie oczekiwane, a eval
liczy je jako `false_refusal` – rozstrzyga ocena ręczna.
