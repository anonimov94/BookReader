# BookReader

Czytnik książek **EPUB i PDF na głos** dla Windows — z naturalnymi lektorami, pełną regulacją głosu,
tłumaczeniem angielskich książek na polski w locie, eksportem do audiobooka i klonowaniem głosu.

![status](https://img.shields.io/badge/platforma-Windows%2010%2F11-blue) ![python](https://img.shields.io/badge/python-3.12-blue)

---

## Spis treści

- [Możliwości](#możliwości)
- [Szybki start (gotowy program .exe)](#szybki-start-gotowy-program-exe)
- [Instalator dodatków](#instalator-dodatków)
- [Obsługa](#obsługa)
- [Tłumaczenie w locie](#tłumaczenie-w-locie)
- [Lektorzy i silniki mowy](#lektorzy-i-silniki-mowy)
- [Eksport audiobooka](#eksport-audiobooka)
- [Gdzie program trzyma dane (tryb przenośny)](#gdzie-program-trzyma-dane-tryb-przenośny)
- [Uruchamianie ze źródeł](#uruchamianie-ze-źródeł)
- [Budowanie .exe](#budowanie-exe)
- [Architektura](#architektura)
- [Rozwiązywanie problemów](#rozwiązywanie-problemów)
- [Licencje i uwagi prawne](#licencje-i-uwagi-prawne)

---

## Możliwości

| | |
|---|---|
| 📚 **Formaty** | EPUB (rozdziały ze spisu treści, bez przypisów), PDF (usuwanie nagłówków/stopek/numerów stron, sklejanie dzielonych wyrazów i akapitów przez strony), **skany PDF przez OCR** (polski + angielski) |
| 🗣️ **Lektorzy** | Microsoft Edge Neural (online, 14 głosów czytających po polsku), Piper (offline), XTTS v2 (GPU, **klonowanie własnego głosu** z nagrania) |
| 🎚️ **Regulacja głosu na żywo** | prędkość, wysokość, głośność, niskie / średnie / wysokie tony, pauzy po zdaniu i akapicie — zmiany słychać od razu |
| 🌍 **Tłumaczenie EN → PL w locie** | lokalnie przez Ollama (Gemma 3, Bielik…) albo DeepL; słownik postaci z płcią narratora i bohaterów, widok dwujęzyczny, ręczne poprawki, porównanie tłumaczy |
| 🔖 **Wygoda** | podświetlanie czytanego zdania, zapamiętywanie miejsca w każdej książce, zakładki, zapisane ustawienia lektora, wyłącznik czasowy, ostatnio otwierane |
| 🔤 **Słownik wymowy** | własne reguły „pisownia → wymowa” (globalne i dla jednej książki, także wyrażenia regularne) |
| 🎧 **Eksport** | M4B (jeden plik z rozdziałami i okładką) lub MP3 (plik na rozdział) |
| 📦 **Instalator dodatków** | wszystko (Ollama, modele, głosy, OCR, XTTS) instaluje się jednym przyciskiem, bez konsoli |

## Szybki start (gotowy program .exe)

1. Pobierz `BookReader-<data>-setup.exe` (lub archiwum `.7z`) z [Releases](../../releases)
   albo zbuduj go samodzielnie ([Budowanie .exe](#budowanie-exe)).
2. Uruchom `setup.exe` i wskaż folder — program rozpakuje się do `BookReader\`.
3. Uruchom `BookReader\BookReader.exe`.
4. Przy pierwszym starcie pojawi się **Instalator dodatków** — zaznacz, co chcesz, i kliknij *Zainstaluj zaznaczone*
   (możesz też pominąć; głosy Edge działają od razu).
5. **Plik → Otwórz…** (Ctrl+O), wybierz książkę i naciśnij **spację**.

> Windows SmartScreen może ostrzec o nieznanym wydawcy (program nie jest podpisany cyfrowo):
> *Więcej informacji → Uruchom mimo to*.

## Instalator dodatków

**Narzędzia → Instalator dodatków** — jedno okno z polami wyboru, pokazuje rozmiar do pobrania i wolne miejsce,
postęp i dziennik instalacji. Nic nie trzeba wpisywać w konsoli.

| Dodatek | Rozmiar | Do czego |
|---|---|---|
| Polskie głosy offline (Piper: Gosia, Darkman) | ~130 MB | czytanie bez internetu |
| OCR (polski + angielski) | ~30 MB | skanowane PDF-y |
| Ollama | ~1 GB | lokalny tłumacz (instalowany po cichu, uruchamiany automatycznie) |
| Gemma 3 12B | ~8 GB | zalecany model tłumaczący |
| Bielik 11B | ~7 GB | polski model, do porównania |
| XTTS v2 | ~5,5 GB | klonowanie głosu, dodatkowi lektorzy (zalecana karta NVIDIA) |

XTTS instaluje się do **osobnego, przenośnego środowiska Pythona** (PyTorch 2.8 + CUDA 12.8 + coqui-tts) —
nie zmienia nic w systemie ani w innych instalacjach Pythona. Ponowne kliknięcie *Zainstaluj* naprawia
uszkodzoną instalację bez pobierania wszystkiego od nowa.

## Obsługa

| Skrót | Działanie |
|---|---|
| `Spacja` | czytaj / pauza |
| `←` / `→` | poprzednie / następne zdanie |
| `Ctrl+←` / `Ctrl+→` | poprzedni / następny akapit |
| podwójne kliknięcie w tekst | czytaj od tego zdania |
| `Ctrl+O` | otwórz książkę |
| `Ctrl+B` | dodaj zakładkę |
| `Ctrl+T` | włącz / wyłącz tłumaczenie na polski |
| `Ctrl+Shift+E` | popraw tłumaczenie bieżącego akapitu |
| `Ctrl+P` | słownik wymowy |
| `Ctrl+M` | głosy i modele |
| `Ctrl+E` | eksport audiobooka |

**Panel lektora** (po prawej): filtr silnika i płci, wybór lektora, *Odsłuchaj próbkę*, suwaki, zapisane ustawienia
(np. „Wieczorem”, „Kryminał”). Lektor jest pamiętany osobno dla książek polskich i angielskich.

**Wyłącznik czasowy**: *Czytanie → Wyłącznik czasowy* — 5–90 minut lub do końca rozdziału.

## Tłumaczenie w locie

Po otwarciu angielskiej książki program zapyta, czy tłumaczyć ją na polski (później: `Ctrl+T`).

- Tłumaczy **całymi akapitami z kontekstem** poprzedniego fragmentu, zaczynając od miejsca, w którym czytasz,
  ok. 40 akapitów do przodu. Pierwszy akapit idzie osobno, żeby lektor ruszył szybko.
- Przy pierwszym tłumaczeniu model buduje **słownik postaci**: imiona i nazwy, płeć bohaterów, narracja
  pierwszo- czy trzecioosobowa i **płeć narratora** (żeby było „powiedziałem” / „powiedziałam”), gatunek, forma
  zwracania się. Wszystko można poprawić: *Tłumaczenie → Słownik postaci i stylu*.
- Tłumaczenia są zapisywane — ponowne czytanie i eksport nie tłumaczą drugi raz.
- *Widok dwujęzyczny* pokazuje oryginał pod tłumaczeniem; każdy akapit można poprawić ręcznie
  albo przetłumaczyć ponownie (akapit lub cały rozdział).
- *Porównaj tłumaczy* tłumaczy ten sam fragment kilkoma modelami obok siebie (z czasem) i pozwala wybrać najlepszy.

**Tłumacze**: Ollama (offline, bez limitów; zalecana karta z ~10 GB pamięci) lub DeepL API
(*Tłumaczenie → Ustawienia tłumaczenia*, darmowy klucz: 500 tys. znaków miesięcznie).

## Lektorzy i silniki mowy

| Silnik | Jakość | Internet | Uwagi |
|---|---|---|---|
| **Edge Neural** | ★★★★★ | tak | Zofia, Marek + 12 głosów wielojęzycznych (Ava, Andrew, Emma, Brian, Seraphina, Florian…); działa od razu |
| **Piper** | ★★★★ | nie | szybki, na procesorze; głosy pobierane w *Głosy i modele* |
| **XTTS v2** | ★★★★★ | nie | GPU; wbudowani lektorzy + **Twoje głosy** z nagrań WAV (6–30 s czystej mowy) |

Prędkość i wysokość są realizowane cyfrowo (Rubber Band), więc suwaki działają identycznie dla każdego silnika.
Wygenerowane zdania są zapamiętywane na dysku — ponowne odtworzenie jest natychmiastowe.

## Eksport audiobooka

**Plik → Eksport audiobooka** (`Ctrl+E`): wybór rozdziałów, format **M4B** (jeden plik, rozdziały, okładka,
jakość 32–192 kbit/s) lub **MP3** (plik na rozdział z tagami). Używa bieżącego lektora, suwaków i słownika wymowy.
Przy włączonym tłumaczeniu najpierw tłumaczy wybrane rozdziały. ffmpeg jest wbudowany.

## Gdzie program trzyma dane (tryb przenośny)

- **Tryb przenośny**: jeśli obok `BookReader.exe` istnieje folder `data`, wszystko (dodatki, głosy, ustawienia,
  zakładki, tłumaczenia, pamięć podręczna) trafia tam. Taki folder można przenieść na inny dysk lub pendrive.
  Aby włączyć: utwórz pusty folder `data` obok `BookReader.exe`.
- W przeciwnym razie: `%LOCALAPPDATA%\BookReader`.

```
data\
  library.json          postęp, zakładki, ustawienia, słowniki książek
  translations.sqlite   zapisane tłumaczenia
  pronunciation.json    globalny słownik wymowy
  cache\audio\          wygenerowane zdania (FLAC)   cache\ocr\  wyniki OCR
  voices\piper\         głosy Piper                  voices\xtts\  Twoje próbki głosu
  tessdata\             dane OCR                     runtime\xtts\ środowisko XTTS
```

## Uruchamianie ze źródeł

Wymagany **Python 3.12** (64-bit).

```powershell
git clone https://github.com/anonimov94/BookReader.git
cd BookReader
py -3.12 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
BookReader.bat                                   # okno programu
```

Wiersz poleceń (bez okna):

```powershell
.venv\Scripts\python -m bookreader.cli --voices                        # lista głosów
.venv\Scripts\python -m bookreader.cli ksiazka.epub --voice pl-PL-MarekNeural --speed 1.2 --bass 3
.venv\Scripts\python -m bookreader.cli ksiazka.pdf --dump              # podgląd podziału na rozdziały i zdania
```

Testy:

```powershell
.venv\Scripts\python -m pytest -q
```

## Budowanie .exe

```powershell
.venv\Scripts\pip install pyinstaller
powershell -ExecutionPolicy Bypass -File packaging\build.ps1
```

Skrypt: rysuje ikonę → buduje folder `dist\BookReader\` (PyInstaller, tryb onedir — start ~2 s) →
uruchamia **autotest spakowanego programu** (`BookReader.exe --selftest raport.txt`: audio, DSP, PDF, Piper,
ffmpeg, Qt, tłumaczenie) → pakuje przez 7-Zip (LZMA2) do `BookReader-<data>.7z` i samorozpakowującego się
`BookReader-<data>-setup.exe` (~112 MB z ~350 MB). Folder `dist\BookReader\data` jest zachowywany przy przebudowie
i nie trafia do archiwów. XTTS nie jest wbudowany — instaluje się jako dodatek.

## Architektura

```
bookreader/
  loaders/          EPUB (ebooklib + BeautifulSoup), PDF (PyMuPDF) + ocr.py (Tesseract wbudowany w MuPDF)
  text/             cleaner, segmenter (zdania + offsety), normalizer (skróty, liczby, rzymskie),
                    pronunciation (słownik wymowy), langdetect
  translate/        service (tłumaczenie w tle przed lektorem), ollama, deepl, glossary (postacie/narrator),
                    store (SQLite)
  tts/              registry → edge | piper_engine | xtts_engine (klient procesu XTTS); cache (FLAC)
  audio/            dsp (tempo/wysokość na zdanie; korektor i głośność na żywo w blokach 1024 próbek),
                    player (bufor do przodu, skoki, akapity czekające na tłumaczenie)
  addons/           xtts_runtime (przenośny Python + PyTorch), xtts_worker (proces XTTS, JSON przez stdin/stdout),
                    ollama_setup (cicha instalacja i autostart)
  gui/              main_window, dialogs, manager (Głosy i modele), setup_wizard (Instalator dodatków)
  export.py         M4B/MP3 przez ffmpeg      cli.py  wiersz poleceń      selftest.py  autotest wersji .exe
packaging/          launcher, spec PyInstallera, ikona, build.ps1
tests/              testy (pytest) z własnymi przykładowymi książkami
```

## Rozwiązywanie problemów

| Problem | Rozwiązanie |
|---|---|
| Brak głosów Edge | wymagany internet; offline użyj Piper lub XTTS |
| PDF bez tekstu („skan”) | zainstaluj OCR w Instalatorze dodatków i otwórz książkę ponownie |
| Tłumaczenie nie rusza | *Narzędzia → Głosy i modele → Ollama*: czy działa i ma pobrany model; albo wpisz klucz DeepL |
| XTTS: błąd instalacji | otwórz Instalator ponownie i kliknij *Zainstaluj* (naprawa); dziennik pokazuje szczegóły |
| XTTS bardzo wolny | brak karty NVIDIA — XTTS działa wtedy na procesorze |
| Czytanie się zacina | głosy wielojęzyczne Edge generują się wolniej; wybierz Zofię/Marka lub Piper |

## Licencje i uwagi prawne

- Kod programu: do użytku prywatnego autora repozytorium (licencja do ustalenia).
- **XTTS v2**: [Coqui Public Model License](https://coqui.ai/cpml) — tylko użytek niekomercyjny; akceptujesz ją
  w programie przed instalacją.
- **Głosy Piper**: licencje poszczególnych głosów — zob. [rhasspy/piper-voices](https://huggingface.co/rhasspy/piper-voices).
- **Edge TTS** korzysta z nieoficjalnego interfejsu usługi Microsoft (pakiet `edge-tts`) — może przestać działać
  bez zapowiedzi.
- Klonuj wyłącznie własny głos lub głos osoby, która wyraziła na to zgodę.
- Czytaj i tłumacz książki, do których masz prawo; wygenerowane audiobooki są do użytku własnego.
