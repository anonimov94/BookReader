# Historia zmian

## [0.2.0] — 2026-10-15

Aktualizacja wydajności i niezawodności. Wszystkie funkcje z wersji 0.1.0 zostały zachowane.

### Naprawione
- **Tłumaczenie nie obejmuje już dużych części książki naraz.** Wcześniej program tłumaczył zawsze
  40 akapitów do przodu (przy krótkich akapitach — kilka stron i kolejne rozdziały). Teraz tłumaczy tylko
  to, co lektor przeczyta w najbliższym czasie (domyślnie ~2 minuty tekstu), i nie tłumaczy akapitów,
  które już minąłeś.
- **Instalacja XTTS kończyła się błędem** (`isin_mps_friendly`, a potem brak `torchcodec`): nowsze
  `transformers` 5.x i PyTorch 2.9+ są niezgodne z XTTS. Instalator przypina teraz `transformers<5`
  i PyTorch 2.8 (CUDA 12.8 — obsługuje karty RTX 50xx) i na koniec sprawdza wczytywanie nagrań.
- **Ollama blokowała pliki programu.** Uruchomiona z BookReadera ładowała jego biblioteki
  (np. `msvcp140.dll`), przez co nie dało się go zaktualizować. Ollama, jej instalator i proces XTTS
  startują teraz z czystym środowiskiem.
- **Dane zapisywały się w ukrytym folderze** przy uruchamianiu programu przez inne aplikacje
  (wirtualizacja `%LOCALAPPDATA%`). Dodany tryb przenośny — patrz niżej.

### Nowe
- **Wyprzedzenie tłumaczenia do wyboru**: *Tłumaczenie → Ustawienia tłumaczenia → Tłumacz z wyprzedzeniem* —
  od „tylko bieżący akapit”, przez 1/2/5/15 minut czytania, po „cały bieżący rozdział”.
- **Samonaprawa XTTS**: przy starcie program sprawdza dodatek XTTS; jeśli nie działa (albo pochodzi ze
  starszej, niezgodnej instalacji), sam usuwa kolidujące pakiety i instaluje go ponownie. Gdy to nie
  wystarczy — czyści środowisko i instaluje od zera, zachowując pobrany model i Twoje próbki głosów.
  Błąd XTTS w trakcie pracy oznacza dodatek do naprawy przy następnym uruchomieniu.
- **Tryb przenośny**: folder `data` obok `BookReader.exe` przechowuje wszystkie dane, dodatki i ustawienia
  (można przenieść cały folder na inny dysk lub pendrive). Zmienna `BOOKREADER_HOME` pozwala wskazać
  dowolny folder danych.
- **Instalator dodatków** (od tej wersji w wydaniu): Ollama, modele tłumaczące, głosy Piper, OCR i XTTS
  jednym przyciskiem, bez konsoli.

### Szybsze działanie
- **Płynniejsze czytanie**: zmiana tempa i wysokości głosu jest liczona z wyprzedzeniem w wątkach
  generujących, a nie w przerwie między zdaniami.
- **Wolne głosy bez zacięć**: bufor przygotowanych zdań rośnie sam (4–10), gdy głos generuje się wolniej
  niż czyta (głosy wielojęzyczne Edge, XTTS); 4 równoległe zapytania zamiast 3.
- **Tekst nie skacze**: przetłumaczony akapit jest podmieniany w oknie w miejscu, zamiast przebudowy
  całego rozdziału przy każdej porcji tłumaczenia.
- **Szybszy start**: lista głosów Edge jest zapamiętywana na dysku (odświeżana co tydzień) — okno nie czeka
  na sieć i działa też offline.
- **Natychmiastowe ponowne otwieranie książek**: przetworzone EPUB/PDF są zapamiętywane na dysku.
- **Tłumacz bez przestojów**: Ollama trzyma model w pamięci karty przez 30 minut (koniec z ponownym
  wczytywaniem modelu po pauzie); tłumaczenia zapisywane do bazy paczkami (SQLite w trybie WAL).
- **Eksport audiobooka**: tempo i wysokość liczone równolegle w 4 wątkach.
- **XTTS**: inferencja w trybie `torch.inference_mode` (szybciej, mniej pamięci karty).
- Wewnętrznie: indeks akapitów zamiast przeszukiwania wszystkich zdań przy każdym zdaniu.

## [0.1.0] — 2026-10-15

Pierwsze wydanie: czytanie EPUB/PDF (z OCR skanów), lektorzy Edge / Piper / XTTS, regulacja głosu na żywo,
tłumaczenie EN→PL w locie (Ollama / DeepL) ze słownikiem postaci, zakładki, zapisane ustawienia lektora,
wyłącznik czasowy, słownik wymowy, eksport M4B/MP3, wersja .exe.
