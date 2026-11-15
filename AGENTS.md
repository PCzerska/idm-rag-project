# AGENTS.md

Kontekst dla agentów kodujących (Claude Code, Codex, Cursor, Copilot…) pracujących w tym repo.
Czytaj w całości przed zmianami. Instrukcje dla ludzi: `README.md` (starter od prowadzących)
i `docs/` (projekt „Ministranci”).

## Cel projektu

Starter RAG dla studentów kursu „Inżynieria dużych modeli językowych” (WEiTI PW). Priorytetem jest
**czytelność dydaktyczna**: student ma zrozumieć cały przepływ, czytając kod. Wybieraj proste,
jawne rozwiązania zamiast sprytnych abstrakcji. Repo musi działać na laptopach 8–16 GB
(macOS/Windows/Linux).

## Projekt na starterze

Repo to starter z kursu rozwijany w projekt **„Ministranci”**: RAG nad scenariuszem filmu (PDF).
Scenariusz jest dzielony na sceny poza starterem (`prepare_data.py` → `screenplay.py`,
1 scena = 1 chunk), a ingest wczytuje gotowe chunki (`prepared.py`) z pominięciem
`normalize.py` i `chunking.py`. Dokumentacja projektu: `docs/` (przepływ danych, notatki
o danych, ewaluacja, złoty zbiór, TODO).

## Architektura

```
prepare_data.py ──► screenplay.py          PDF scenariusza → data/processed/chunks.jsonl
cli.py ──► ingest.py ──► normalize.py ──► chunking.py ──► embeddings.py ──► store.py (Qdrant)
   │          └───────► prepared.py (pliki .jsonl: gotowe chunki, bez normalizacji i chunkingu)
   ├─► generate.py ──► retrieve.py ──► embeddings.py, store.py
   │        └────────► structured.py ──► llm.py (OpenAI SDK, base_url z .env)
   ├─► agent.py ────► retrieve.py (narzędzie search_docs), llm.py
   ├─► eval.py ─────► retrieve.py, generate.py, store.describe_collection (opis eksperymentu)
   ├─► grade.py ────► arkusz ręcznej oceny (CSV) → dokładność, przyczyny błędów
   └─► check.py ────► wszystko powyżej (smoke test)
config.py – jedyne źródło ustawień (dataclass `Settings` z .env)
scripts/convert_questions.py – eval/pytania_ministranci.jsonl → eval/pytania.jsonl
```

Kluczowe kontrakty:
- **Chunk payload** w Qdrancie: `text`, `source`, `page`|`section`, `source_ids` (lista), `chunking`,
  `chunk_index`, `embed_model`. Wektor nazwany `dense` (miejsce na sparse `bm25`).
  Chunki scen (`chunking="prepared"`) mają zamiast `page`/`section` metadane sceny: `scena`,
  `obiekt`, `wn_pl`, `pora`, `strona`, `strona_do`, `postacie` (opis: `docs/przeplyw_danych.md`).
- **`source_ids`** są niezależne od chunkingu (`plik.pdf#p3`, `plik.md#Sekcja`, `plik.txt`;
  w projekcie sceny: `scenariusz#s12`, `scenariusz#s117*`); złote zbiory w `eval/*.jsonl` się
  do nich odwołują. Zmiana formatu = aktualizacja złotych zbiorów.
- **Złoty zbiór projektu**: źródłem jest `eval/pytania_ministranci.jsonl` (edytowany ręcznie),
  `eval/pytania.jsonl` generuje `scripts/convert_questions.py` – nie edytuj go ręcznie.
  `tags = [kategoria, trudnosc, typ]`, `split` = `dev` | `test` (test tylko do pomiaru końcowego).
- **Wynik ewaluacji** zaczyna się od bloku `experiment` (chunking i embeddingi odczytane
  z kolekcji, LLM, top-k, commit); każdy wiersz ma `retrieved` (ranking). Pomiary referencyjne
  leżą w `eval/baseline/` (w gicie), bieżące w `eval/results/` (poza gitem). Szczegóły:
  `docs/ewaluacja.md`.
- **Cytowania**: model zwraca numery fragmentów kontekstu (`Answer.citations`), mapowanie na źródła
  robi kod (`RagResult.cited_hits`). Nie każ modelowi przepisywać nazw plików.
- **Odmowę** rozpoznaje kod (`generate.is_refusal`: formuła odmowy w 1. zdaniu albo brak cytatów),
  nie model ani sam brak cytatów – małe modele dopisują `[1][2]` do odmów. Zmiana reguły =
  `rag rescore` pomiaru referencyjnego.
- **Structured output**: zawsze przez `structured.complete_structured()` (`auto`: native → prompt;
  `tool` tylko jawnie w `.env`). Nie wywołuj `response_format` bezpośrednio – nie działa na Ollamie z runnerem MLX.

## Komendy

```bash
make setup | pull | up | down          # środowisko
make check                             # smoke test (wymaga Ollamy i Qdranta)
make ingest [DATA=...] [ARGS=...]      # uv run rag ingest
make ask Q="..." | make agent Q="..."  # uv run rag ask / agent
make eval [ARGS=--generate]            # uv run rag eval --golden $(GOLDEN), domyślnie eval/pytania.jsonl
uv run rag grade <arkusz.csv>          # podsumowanie ręcznej oceny
uv run rag rescore <wynik.json>        # przelicz heurystyki bez pytania modelu
uv run python scripts/convert_questions.py --force   # po zmianie pytania_ministranci.jsonl
make test                              # pytest, bez sieci – uruchamiaj po każdej zmianie
make lint                              # ruff check + ruff format --check (src, tests, scripts)
```

Przed zakończeniem pracy: `make lint && make test`. Jeśli zmieniasz retrieval, chunking albo prompt –
także `make eval` i porównaj z pomiarem referencyjnym w `eval/baseline/` (E1).

## Konwencje

- Python 3.12, `uv` (zależności tylko przez `uv add`), typy w sygnaturach, docstring w każdym
  module i publicznej funkcji. Ruff (`line-length = 100`).
- Docstringi, komentarze i komunikaty dla użytkownika **po polsku**, ale terminy techniczne
  **po angielsku**: embedding, chunk, chunking, retrieval, reranking, bag of words, prompt, tool calling.
  Nie tłumacz ich („osadzenie”, „fragmentacja” – źle).
- Małe moduły o jednej odpowiedzialności; nowe techniki jako osobne funkcje włączane flagą/ustawieniem,
  a nie przepisanie istniejących.
- Nowe ustawienie = pole w `Settings` + wpis w `.env.example` z komentarzem + opis w `docs/`.
- Testy jednostkowe nie mogą wymagać sieci, Ollamy ani Qdranta (atrapy jak w `tests/test_structured.py`,
  Qdrant `:memory:`).
- Błędy dla użytkownika: `RuntimeError`/`ValueError` z komunikatem „co się stało + jak naprawić”
  (CLI wypisuje je bez tracebacka).
- Commity małe i logiczne, opis po polsku lub angielsku, bez sekretów.
- Dokumentacja projektu idzie do `docs/`; nowa funkcja/komenda = aktualizacja odpowiedniego pliku
  w `docs/` (README zostaje bez zmian, patrz niżej).

## Czego NIE robić

- **Nie zmieniaj `README.md`** – to instrukcja startera od prowadzących. Opis zmian projektu
  piszemy w `docs/`.

- **Nie uruchamiaj Ollamy w Dockerze** – na Macu brak dostępu do Metal (GPU). `docker-compose.yml`
  jest tylko dla Qdranta.
- **Nie dodawaj LangChaina, LlamaIndexa ani innych frameworków RAG/agentowych** – cały sens repo
  to jawny, zrozumiały kod. LLM i embeddingi wyłącznie przez SDK `openai` z `base_url`.
- **Nie instaluj niczego globalnie** (`pip install`, `brew install`, `npm -g`) bez pytania użytkownika.
- **Nie commituj** `.env`, kluczy API, `data/raw/*` (poza `manifest.json`/`.gitkeep`), `eval/results/*`, `.qdrant/`.
- Nie zmieniaj `EMBED_MODEL` w kodzie ani domyślnych wartości bez ponownego ingestu i aktualizacji `docs/`.
- Nie ustawiaj domyślnie thinkingu (`LLM_REASONING_EFFORT`) na nic innego niż `none` – qwen3.5
  z thinkingiem odpowiada ~100× wolniej.
- Nie zakładaj, że `response_format` / `tool_choice` są respektowane przez każdy serwer.
- Nie wstawiaj do kodu twardych adresów, kluczy ani nazw modeli poza `config.py`.
