# Ewaluacja – jak przeprowadzić pomiar

Instrukcja pomiaru jakości systemu RAG „Ministranci”: od złotego zbioru do ręcznej oceny.
Opis samego zbioru i omówienie wyników: [zloty_zbior.md](zloty_zbior.md). README w korzeniu
repo to instrukcja startera od prowadzących – opisuje przykładowy zbiór `golden_sample.jsonl`;
w projekcie obowiązuje to, co poniżej.

## Przegląd

```
eval/pytania_ministranci.jsonl     ← edytujemy ręcznie (polskie pola, źródła z frazą)
   │  scripts/convert_questions.py  (kontrola fraz w chunkach, przepinanie scen)
   ▼
eval/pytania.jsonl                 ← format startera (GoldItem), generowany – nie edytować
   │  make eval [ARGS=--generate]   ──► src/rag/eval.py
   ▼
eval/results/<czas>_<etykieta>.json / .md / _ocena.csv   (poza gitem)
   │  ręczna ocena w arkuszu CSV
   │  uv run rag grade <arkusz>     ──► src/rag/grade.py
   ▼
dokładność odpowiedzi ogółem / per kategoria / per trudność + przyczyny błędów
   │  pomiar referencyjny kopiujemy do eval/baseline/ (w gicie)
```

## Krok 0: indeks, na którym mierzymy

Wynik zależy od tego, **co jest w kolekcji**, a nie od `.env`. Domyślna kolekcja startera
(`COLLECTION=docs`) to inny indeks – pomiar na niej nie jest porównywalny z E1.

Ustawienia projektu w `.env` (różnią się od `.env.example` startera):

```bash
QDRANT_URL=http://localhost:6333   # make up
COLLECTION=scenariusz_sceny        # 1 scena = 1 chunk (chunking="prepared")
DATA_DIR=data/processed
LLM_MODEL=qwen3.5:4b               # model bazowy; porównania przez zmienną w powłoce
```

Budowa indeksu (szczegóły w [przeplyw_danych.md](przeplyw_danych.md)):

```bash
make up
uv run python prepare_data.py --in data/raw --out data/processed
make ingest DATA=data/processed/chunks.jsonl ARGS="--collection scenariusz_sceny --recreate"
```

`rag eval` na starcie wypisuje, co znalazł w kolekcji, np.
`Kolekcja scenariusz_sceny: 140 chunków · chunking prepared · embeddingi qwen3-embedding:0.6b`.
Jeśli liczba chunków albo chunking się nie zgadza – przerwij i przebuduj indeks.

## Krok 1: złoty zbiór

Zmiany wprowadzamy **tylko** w `eval/pytania_ministranci.jsonl`, potem:

```bash
uv run python scripts/convert_questions.py --force
```

Skrypt szuka każdej `fraza` w tekście chunków z `data/processed/chunks.jsonl`:

| komunikat | znaczenie | co zrobić |
|---|---|---|
| `przepięto` | sceny nie ma w indeksie, fraza jest w innym chunku | sprawdzić, czy przepięcie jest sensowne |
| `needs_review=true` | sceny ani frazy nie ma w indeksie | poprawić źródło albo usunąć pytanie |
| `fraza nie znaleziona` | scena jest, frazy w niej nie ma | poprawić frazę (literówka, inny cudzysłów) |

Na końcu powinno być: `Wszystkie źródła i frazy zgodne z indeksem.` `make test` sprawdza schemat,
rozkład trudności, pole `split` i to, czy źródła istnieją w indeksie.

### Podział dev / test

Pole `zbior` (w `pytania.jsonl`: `split`) mówi, do czego służy pytanie:

- `dev` – eksperymenty: na tych pytaniach oglądamy błędy i poprawiamy system. Obecnie
  **wszystkie 120 pytań**.
- `test` – tylko pomiar końcowy. Zbioru testowego jeszcze nie ma; zasady:
  - pytania testowe piszemy **zanim** zaczniemy poprawiać prompt i retrieval pod konkretne błędy
    z dev, najlepiej osoba, która tych błędów nie analizowała, z podobnym rozkładem kategorii;
  - po napisaniu zbiór jest zamrożony – nie oglądamy jego błędów i nie stroimy pod niego systemu;
  - uruchamiamy go raz na wybranej konfiguracji (i na E1 jako odniesienie).

Wybór części: `make eval ARGS="--split dev"`. Bez `--split` liczone są wszystkie pytania.

## Krok 2: pomiar

```bash
make eval                                 # sam retrieval: recall@1/3/5, MRR (~1 min)
make eval ARGS=--generate                 # + odpowiedzi modelu (~10 s/pytanie przy 4b)
LLM_MODEL=qwen3.5:9b make eval ARGS=--generate     # inny model bez zmiany .env
make eval ARGS="--generate --sample 12"   # szybka próba (nie pełny pomiar)
make eval GOLDEN=eval/golden_sample.jsonl # przykładowy zbiór startera
```

`make eval` używa `GOLDEN=eval/pytania.jsonl` (Makefile). Ctrl+C zapisuje wyniki częściowe
(`_czesciowe` w nazwie). Generacja działa z `temperature=0`.

### Co zapisuje każdy pomiar

Nazwa pliku: `<czas>_<zbiór>[_<split>]_<kolekcja>_<chunking>_<embedding>[_<LLM>]`, np.
`20261115-120000_pytania_scenariusz_sceny_prepared_qwen3-embedding-0.6b_qwen3.5-4b.json`.

| plik | zawartość |
|---|---|
| `.json` | `experiment` (co mierzyliśmy), `config` (pełne ustawienia, bez kluczy), `rows` (wiersz na pytanie), `summary`, `by_category`, `by_difficulty` |
| `.md` | tabela „Eksperyment” + tabele wyników (do raportu) |
| `_ocena.csv` | arkusz do ręcznej oceny (tylko z `--generate`) |

Blok `experiment`:

| pole | skąd |
|---|---|
| `collection`, `chunking`, `chunks`, `index_embed_model` | **odczytane z payloadu kolekcji** (`store.describe_collection`) |
| `embed_model`, `embed_query_instruction`, `top_k` | ustawienia zapytania |
| `llm_model`, `llm_temperature`, `llm_reasoning_effort`, `structured_output` | ustawienia generacji (puste bez `--generate`) |
| `golden`, `split` | złoty zbiór i jego część |
| `git_commit` | commit kodu (`-dirty` = niezacommitowane zmiany – takiego pomiaru nie traktuj jako referencyjnego) |

Wiersz pytania ma oprócz metryk pole `retrieved`: ranking chunków (`rank`, `source_ids`,
`score`) – model dostaje pierwsze `top_k` z nich.

## Metryki

Retrieval (pytania z niepustym `source_ids`):

| metryka | znaczenie |
|---|---|
| `recall@k` | część oczekiwanych scen w top-k chunkach |
| `mrr` | 1 / pozycja pierwszego trafnego chunka |
| `ctx_chars` | znaki kontekstu w top-k – porównując chunking, patrz na recall razem z nim |

Generacja (`--generate`):

| metryka | znaczenie | lepiej |
|---|---|---|
| `refusal_ok` | odpowiedział na pytanie z dokumentów / odmówił na pytanie spoza nich | ↑ |
| `false_refusal` | odmowa, choć odpowiedź jest w dokumentach | ↓ |
| `correct_refusal` | odmowa na pytanie spoza dokumentów | ↑ |
| `citation_hit` | któryś cytowany chunk jest oczekiwanym źródłem | ↑ |
| `cited_recall` | część oczekiwanych źródeł zacytowana | ↑ |
| `inline_cited` | odpowiedź ma znaczniki `[n]` | ↑ |
| `count_ok` | (typ `liczba`) oczekiwana liczba jest w odpowiedzi – heurystyka | ↑ |
| `list_p/r/f1` | (typ `lista` scen) zgodność numerów scen – heurystyka | ↑ |
| `gen_error` | model nie dał poprawnej odpowiedzi strukturalnej (zepsuty JSON po ponowieniu) | ↓ |
| `latency_s` | retrieval + generacja | ↓ |

Metryki generacji nie mówią, czy odpowiedź jest **poprawna** – to daje dopiero ocena ręczna.

### Odmowa

Odmowę ustala kod (`generate.is_refusal`), nie model: odpowiedź jest odmową, gdy jej
**pierwsze zdanie** zawiera formułę odmowy albo gdy nie cytuje żadnego fragmentu:

- formuły jednoznaczne – zawsze: „nie ma informacji”, „brak informacji”, „nie zawiera
  informacji”, „nie wiem”, „nie mogę odpowiedzieć”, „nie da się ustalić”;
- formy występujące też w opisie – tylko obok słowa o źródle (kontekst, dokument, tekst,
  fragment, scenariusz): „nie jest podany / określony / wymieniony…”, „nie znajduje się”,
  „nie wspomina”, „nie podaje”. Dzięki temu „suma *nie jest określona* liczbowo” to odpowiedź,
  a „konkretny fragment *nie jest podany w kontekście*” – odmowa.
 Znaczniki `[n]` doklejone do
odmowy nie liczą się jako cytaty (małe modele dopisują `[1][2][3]` także do odmów).
Zastrzeżenie dopiero w dalszym zdaniu („X [1]. Nie ma informacji o Y”) to częściowa odpowiedź.
Wiersz wyniku ma pole `refusal_in_text` – przy nowym modelu warto przejrzeć, czy heurystyka
nie myli się na jego stylu odpowiedzi. Z tej samej reguły korzysta `rag ask` (przy odmowie nie
pokazuje źródeł).

Pytanie z `gen_error` nie przerywa pomiaru: liczy się jako nieudane (brak odpowiedzi, cytatów
i poprawnej odmowy – nie jest pomijane, bo to zawyżyłoby wynik), w arkuszu ma
`[BŁĄD GENERACJI] …` i `ocena_auto=bledna`, a treść błędu (z początkiem surowej odpowiedzi
modelu) jest w polu `error` w `.json`. Typowy błąd – polski cudzysłów „…" zamknięty zwykłym `"` –
tryb `prompt` naprawia lokalnie (`structured.repair_quotes`), bez ponownego pytania modelu.
Inna awaria (np. Ollama przestała odpowiadać) zapisuje wyniki częściowe jak Ctrl+C.

### Tryb structured output

`STRUCTURED_OUTPUT=auto` wybiera tryb przy pierwszym użyciu modelu i zapisuje go w
`.cache/structured_output.json` (osobno dla każdego modelu). Ten sam model może dostać inny tryb
na innym komputerze – np. `qwen3.5:4b` w pierwszym pomiarze E1 działał w trybie `native`, a przy
nowym na innym laptopie dostał `prompt` (prawdopodobna przyczyna: inna wersja / runner Ollamy –
patrz AGENTS.md o MLX), w którym częściej psuje JSON. Dlatego `experiment.structured_modes`
zapisuje, ile odpowiedzi dał każdy tryb; porównując pomiary, sprawdź, czy tryb był ten sam.
`make reset-cache` wymusza ponowne wykrycie.

## Krok 3: ocena ręczna

Arkusz `_ocena.csv`: średnik, UTF-8 z BOM. Excel/Numbers – import z separatorem „;”; pandas:

```python
df = pd.read_csv(path, sep=";", encoding="utf-8-sig")
df.to_csv(path, sep=";", index=False, encoding="utf-8-sig")   # zapis bez zmiany formatu
```

| kolumna | kto | znaczenie |
|---|---|---|
| `odpowiedz_systemu` | eval | odpowiedź modelu |
| `cytowane_sceny` | eval | sceny, które model zacytował |
| `oczekiwane_sceny` | złoty zbiór | gdzie jest odpowiedź |
| `znalezione_sceny` | eval | top-k scen, które model dostał w kontekście (w kolejności rankingu) |
| `ocena_auto`, `przyczyna_auto` | eval | podpowiedź tam, gdzie wynik jest jednoznaczny (odmowy, liczby) |
| `ocena` | człowiek | `poprawna` / `bledna` / `poprawna_odmowa` |
| `przyczyna_bledu` | człowiek | dla `bledna`, patrz niżej |
| `uwagi` | człowiek | dowolnie |

Przyczyny błędów:

| przyczyna | kiedy |
|---|---|
| `brak_fragmentu` | oczekiwanej sceny nie ma w `znalezione_sceny` – błąd retrievalu |
| `blad_ekstrakcji` | scena była w kontekście, ale model wyciągnął z niej złą informację |
| `halucynacja` | odpowiedź zawiera fakty, których nie ma w kontekście |
| `zla_odmowa` | model odmówił, choć odpowiedź była w kontekście |
| `blad_cypher` | zarezerwowane na etap z grafem wiedzy (błędne zapytanie Cypher); w RAG-u wektorowym nieużywane |

Podsumowanie:

```bash
uv run rag grade eval/baseline/E1_ocena.csv              # tylko oceny ręczne
uv run rag grade eval/baseline/E1_ocena.csv --use-auto   # braki uzupełnia ocena_auto
```

`ocena_auto` dla odmów wynika z rozpoznania odmowy w kodzie (patrz [Odmowa](#odmowa)) –
to heurystyka, więc przy ocenie ręcznej warto ją potwierdzić.

Po poprawce heurystyk (`count_ok`, `list_*`, rozpoznawanie odmów) można przeliczyć zapisany przebieg bez pytania
modelu: `uv run rag rescore <plik.json>` (tworzy `_rescored.json` i nowy arkusz – ręczne oceny
trzeba przenieść).

## Krok 4: pomiar referencyjny i porównania

`eval/results/` jest poza gitem. Pomiar, do którego się porównujemy, kopiujemy do
`eval/baseline/` pod krótką nazwą (`E1.json`, `E1.md`, `E1_ocena.csv`) i commitujemy razem
z omówieniem w [zloty_zbior.md](zloty_zbior.md).

Zasady porównań:

- jedna zmiana na eksperyment (model **albo** chunking **albo** retrieval), reszta jak w E1;
- zmiana chunkingu lub modelu embeddingów = nowa kolekcja (ingest z `--collection`) – blok
  `experiment` pokaże, na czym mierzono;
- porównujemy na tym samym złotym zbiorze i tej samej części (`split`);
- pomiar z `git_commit` zakończonym na `-dirty` nie jest referencyjny.
