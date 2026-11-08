# Notatki o danych projektu „Ministranci”

Ustalenia z analizy dokumentów źródłowych (2026-11-08). Wnioski o znaczeniu symboli są
wywnioskowane z konwencji produkcji filmowej i treści scen – dokumenty nie mają legendy.
Do potwierdzenia z produkcją.

## Decyzja: rola dokumentów

- **Scenariusz = jedyne źródło wiedzy RAG** (indeksowany w Qdrancie).
- **Tabela inscenizacyjna, rekwizyty, grafiki, spis obiektów = materiał do budowy złotego zbioru**
  (pytania i oczekiwane odpowiedzi), nie trafiają do indeksu.
- **Sceny z rozbieżnościami między dokumentami nie są używane w złotym zbiorze** – dzięki temu
  rozbieżności nie wpływają na działanie ani ocenę systemu (lista niżej).

## Dane wejściowe

- Katalog z oryginałami: `../dane/ministranci/` (poza repo). Pliki rozpoznawane po nazwie przez
  `prepare_data.py` (czyta tylko scenariusz: `MINISTRANCI WER_REAL_12.08.pdf`).
- Pozostałe pliki (tabela, listy rekwizytów i grafik, spis obiektów, `SS _ Ministranci 18.07.pdf`,
  `Ministranci_książka_obiektów.pptx`) nie są przetwarzane – tylko materiał do złotego zbioru.
- Anonimizacja scenariusza: pomijana strona tytułowa (nazwisko reżysera) + audyt wyniku
  (e-mail, telefon, kod pocztowy, adres, link). Listy zamian i słów zakazanych z E1 usunięte –
  dotyczyły wyłącznie adresów ze spisu obiektów i nazwisk statystów z tabeli, nie scenariusza.
- Wynik: 140 chunków scenariusza (146 scen, 6 usuniętych); słów w chunku min/mediana/max = 12/95/743.

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

## Sceny wykluczone ze złotego zbioru (rozbieżności tabela ↔ scenariusz)

- Tylko w tabeli: `17B`, `37A`, `37B`, `115A`, `118A`, `117*`.
- Ze scenariusza bez wpisu w tabeli: `6A`, `16A`, `40A`, `78A`, `103A`, `109A`, `109B`, `113A`, `55`.
- Niezgodna numeracja między dokumentami: `17`, `17A` (w tabeli „Sala Ministrantów” = scena 17
  ze scenariusza), `103` (dzień 30 „dom Gucciego” = 103A), `117`, `117*`, `117**`.
- Do sprawdzenia: `84B` (brak `84A` w scenariuszu) – wykluczyć, jeśli dane w tabeli się nie zgadzają.

## Do zrobienia

- [x] Sceny `117*` i `117**` były doklejane do 117A (wzorzec nagłówka nie dopuszczał gwiazdek).
      Naprawione; parsowanie przeniesione do `src/rag/screenplay.py`, testy w `tests/test_screenplay.py`.
      Wynik: 140 chunków (było 138), poza 117A/117*/117** bez zmian.
- [x] Chunkować tylko scenariusz: parsery pozostałych dokumentów usunięte z `prepare_data.py`
      (są w historii gita, commit 6a28f57); `prepared.py` i testy dotyczą tylko scenariusza.
