# Notatki z testów ręcznych

Spostrzeżenia z ręcznych zapytań `make ask` / `make agent` – materiał do budowy złotego zbioru
i listy eksperymentów. Wyniki automatycznej ewaluacji: `eval/results/` (ignorowane przez git).

## Test 1 (2026-11-08) – wersja bazowa

Konfiguracja: kolekcja `scenariusz_sceny` (140 chunków, 1 scena = 1 chunk, linia `Postacie:`
w tekście), `qwen3-embedding:0.6b`, top-k = 5, dense retrieval bez filtrów.
Decyzje indeksowania: [notatki_dane.md](notatki_dane.md#decyzje-indeksowanie-2026-11-08).

| Pytanie | Wynik | Wniosek |
| --- | --- | --- |
| `ask` „Co dzieje się podczas procesji z figurą?” | Poprawna odpowiedź ze sceny 117. 117** pobrana, ale niecytowana; 117* niepobrana. Score ~0,49–0,54. | Sekwencja z kilku scen (117 → 117* → 117**) nie mieści się w całości w top-5. |
| `ask` „Kto występuje w scenie 40A?” | Odpowiedź „Filip” poprawna, ale scena 40A dopiero na 4. miejscu (0,488), za 5, 61A i 103A. | Embedding nie łapie numerów scen. 40A nie ma dialogu, więc `postacie = []`; model wywnioskował postać z opisu akcji. |
| `ask` „Gdzie Filip rozmawia z proboszczem?” | Trzy trafne miejsca (sceny 106, 56, 121), score ~0,76–0,79. | Linia `Postacie: PROBOSZCZ, FILIP` prawdopodobnie pomaga – wyraźnie wyższe score niż w innych pytaniach. |
| `agent` „W których scenach pojawia się Gucci i co robi?” | Jedno wywołanie `search_docs` (k = 10); odpowiedź wymienia 9 scen. Gucci mówi w 49 scenach, w tekście pojawia się w 69. | Pytania „we wszystkich scenach” są poza zasięgiem top-k: retrieval zwraca najwyżej k chunków. |

### Wnioski

1. **Pytania o numer sceny** – dense retrieval słabo trafia w dokładny token (`40A`). Argument za
   hybrid search (decyzja 5) albo filtrem `scena = N` (decyzja 6).
2. **Pytania o postacie** – działają dobrze; decyzja 4 (postacie w chunku) się opłaciła.
3. **Ograniczenie listy postaci** – tylko postacie mówiące; sceny bez dialogu (np. 40A) mają pustą
   listę, choć ktoś w nich występuje.
4. **Pytania agregujące** („we wszystkich scenach”, „ile razy”) – top-k z definicji ich nie obsłuży.
   Propozycja: w złotym zbiorze osobny typ pytań pokazujący to ograniczenie; jako eksperyment –
   filtr `postacie = X` w narzędziu agenta (rozwinięcie decyzji 6).
5. **Sekwencje wielu scen** (117 / 117* / 117**) – część scen wypada poza top-5. Do sprawdzenia
   w ewaluacji: większe k albo dołączanie sąsiednich scen.
6. **Czas** – retrieval zwykle ~1 s, jednorazowo 6,8 s (prawdopodobnie ponowne ładowanie modelu
   embeddingów w Ollamie). Generacja 5–14 s.

### Błąd: cytaty zaczynają się od numeru strony

Cytaty pod odpowiedzią wyglądają tak: „46 Filip kończy odbieranie pliku.”, „102-103 Postacie:
PROBOSZCZ, MATKA FILIPA, FILIP Filip z Matką…”. Przyczyna: `best_quote`
(`src/rag/generate.py`) dzieli tekst na zdania po kropce, a w nagłówku `scenariusz, s. 46` kropka
po „s.” kończy „zdanie”, więc reszta nagłówka i linia `Postacie:` doklejają się do pierwszego
zdania treści. Wpływa tylko na wyświetlanie cytatu, nie na retrieval.
Poprawka: przy wyborze cytatu pomijać nagłówek sceny i linię `Postacie:` (+ test).

### Do złotego zbioru

- Typy pytań: o numer sceny, o postacie, o miejsce/wydarzenie, o sekwencję wielu scen,
  agregujące (oznaczone osobno – mierzą ograniczenie, nie jakość retrievalu).
- Pytania z tego testu nadają się jako wzory typów, ale nie wprost: sceny 117/117*/117** i 40A są
  wykluczone przez rozbieżności z tabelą ([notatki_dane.md](notatki_dane.md)) – do złotego zbioru
  analogiczne pytania o inne sceny.
