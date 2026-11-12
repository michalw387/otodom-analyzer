# 🏠 Otodom Analyzer

[![CI](https://github.com/michalw387/otodom-analyzer/actions/workflows/ci.yml/badge.svg)](https://github.com/michalw387/otodom-analyzer/actions/workflows/ci.yml)
![Python 3.14](https://img.shields.io/badge/python-3.14-blue)
![mypy](https://img.shields.io/badge/mypy-checked-2a6db2)
![ruff](https://img.shields.io/badge/lint-ruff-d7ff64)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

**Stack:** Python 3.14 · Streamlit · SQLAlchemy 2.0 (SQLite) · APScheduler · Pydantic · curl_cffi + BeautifulSoup ·
Playwright · httpx · pandas · Claude Code CLI · pytest · mypy · ruff · pre-commit · GitHub Actions

Osobisty asystent wyszukiwania mieszkań. Aplikacja cyklicznie sprawdza nowe ogłoszenia sprzedaży
mieszkań na Otodom.pl, pobiera ich pełne dane i ocenia każde z nich przez **Claude Code** (Anthropic)
według Twoich kryteriów – w skali 0–100, z listą zalet, wad/red flags, podsumowaniem i kontrolą
spójności opisu z filtrami. O najlepszych ofertach i obniżkach cen powiadamia od razu na telefon.

## Funkcje

- **Filtry Otodom**: lokalizacja (+ promień), cena, powierzchnia, liczba pokoi, maks. cena za m²,
  rynek – albo dowolny własny URL wyszukiwania skopiowany z Otodom.
- **Kryteria oceny AI** z wagami *Niska / Średnia / Wysoka / Krytyczna* (edytowalna tabela).
- **Automatyczne skanowanie w tle** (APScheduler) co 15 min – 12 godz. oraz przycisk
  „Skanuj i przeanalizuj teraz”.
- **Brak dublowania**: ogłoszenia już zapisane w bazie nie są ponownie pobierane ani wysyłane do AI.
- **Powiadomienia** (ntfy.sh, Discord, Telegram) o nowych ofertach z oceną powyżej progu (domyślnie 80)
  i o **obniżkach cen** zapisanych ofert.
- **Wykrywanie duplikatów** – to samo mieszkanie wystawione przez kilka agencji jest pokazywane jako
  jedna karta z listą powiązanych ogłoszeń.
- **Odległość i czas dojazdu** do wybranego adresu (praca, szkoła…) – na kartach, w eksporcie
  i w prompcie AI.
- **Bez klucza API** – oceny wykonuje zainstalowany i zalogowany Claude Code w ramach Twojej subskrypcji.
- **Dzienny limit tokenów AI** – skanowanie nie zużyje całego limitu subskrypcji.
- **Dashboard**: sortowanie (ocena, cena, cena/m², data), filtr minimalnej oceny, ulubione,
  odrzucone, „skontaktowano”, archiwum odrzuconych, **eksport wybranych ofert do Excela**.
- **Skrypty CLI**: ponowna ocena ofert i eksport raportów do `.xlsx` / `.csv`.

## Wymagania

- Python 3.14+
- [Claude Code](https://claude.com/claude-code) zainstalowany i zalogowany na konto z subskrypcją Claude
  (`claude auth login`). Klucz API nie jest potrzebny – bez działającego Claude Code oferty są pobierane,
  ale nie oceniane.

## Instalacja

```bash
cd otodom-analyzer
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Opcjonalnie: przeglądarka używana awaryjnie, gdy Otodom zablokuje zapytania (HTTP 403)
playwright install chromium

claude auth login                  # jeśli Claude Code nie jest jeszcze zalogowany
cp .env.example .env               # opcjonalnie: powiadomienia, ścieżka do claude
```

Wartości z `.env`:

| Zmienna | Znaczenie |
|---|---|
| `CLAUDE_CLI_PATH` | opcjonalnie: pełna ścieżka do `claude`, gdy nie ma go w `PATH` procesu aplikacji |
| `NOTIFY_WEBHOOK_URL` | URL tematu ntfy (`https://ntfy.sh/twoj-temat`) lub webhooka Discord |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | bot Telegram (token od @BotFather) i ID czatu |
| `OTODOM_DB_PATH` | opcjonalnie: inna ścieżka bazy (domyślnie `data/otodom.db`) |

Ustawienia powiadomień możesz też podać w aplikacji (zakładka **Ustawienia i Kryteria**) – wartość
zapisana w aplikacji ma pierwszeństwo przed `.env`. `ANTHROPIC_API_KEY` z `.env` jest ignorowany i nigdy
nie trafia do Claude Code, więc oceny nie są rozliczane jako płatne API.

## Uruchomienie

```bash
./start.sh
```

Skrypt `start.sh` przechodzi do katalogu projektu, aktywuje `.venv` i uruchamia `streamlit run app.py`,
więc działa z dowolnego katalogu (np. `~/projekty/otodom-analyzer/start.sh`). Dodatkowe
argumenty trafiają do Streamlit, np. `./start.sh --server.port 8502`.

Aplikacja otworzy się pod <http://localhost:8501>. Domyślnie nasłuchuje tylko na `localhost`
(`.streamlit/config.toml`) – uruchamia Claude Code na Twoim koncie i przechowuje dane powiadomień, więc nie wystawiaj jej
do sieci bez dodatkowego zabezpieczenia.

### Pierwsze kroki

1. Otwórz **⚙️ Ustawienia i Kryteria**.
2. Ustaw lokalizację – to fragment adresu wyników Otodom po `/mieszkanie/`, np.
   `mazowieckie/warszawa/warszawa/warszawa/mokotow` albo `malopolskie/krakow/krakow/krakow`.
   Możesz też wkleić cały URL wyszukiwania w polu „Własny URL” – wtedy pozostałe filtry są pomijane.
3. Dopasuj kryteria i wagi. Opcjonalnie podaj **adres docelowy** i sposób dojazdu oraz skonfiguruj
   **powiadomienia** (przycisk „Przetestuj powiadomienie” wysyła wiadomość próbną bez zapisywania).
4. Wybierz model, częstotliwość i dzienny limit tokenów, kliknij **Zapisz ustawienia**. W sekcji
   **🩺 Diagnostyka** przycisk „Sprawdź Claude Code” weryfikuje `claude --version` i zalogowanie.
5. Kliknij **🔍 Skanuj i przeanalizuj teraz**. Wyniki pojawią się w zakładce **🏆 Najlepsze Oferty**.

## Powiadomienia

| Kanał | Konfiguracja |
|---|---|
| **ntfy.sh** | Zainstaluj aplikację ntfy, zasubskrybuj temat o trudnej do zgadnięcia nazwie i wpisz `https://ntfy.sh/<temat>` (działa też własny serwer ntfy). Temat na ntfy.sh jest publiczny dla każdego, kto zna nazwę. |
| **Discord** | Ustawienia kanału → Integracje → Webhooki → skopiuj URL webhooka. |
| **Telegram** | Utwórz bota u @BotFather (token), napisz do niego wiadomość i odczytaj swój chat ID (np. przez @userinfobot). |

- **Nowa oferta**: wysyłana, gdy świeżo oceniona oferta osiągnie próg. Zawiera tytuł, cenę, metraż, ocenę,
  najważniejsze zalety i wady oraz link. Każde mieszkanie zgłaszane jest raz – ponowna ocena ani ta sama
  oferta od innej agencji nie wywołują kolejnego powiadomienia.
- **Obniżka ceny**: „📉 Obniżka ceny! Z 700 000 zł na 650 000 zł”. Ceny znanych ofert są porównywane przy
  każdym skanowaniu na podstawie listy wyników. Dodatkowo **obserwowane oferty** (ulubione,
  skontaktowane i z oceną ≥ progu powiadomień), które zniknęły już z pierwszych stron wyników, są
  sprawdzane bezpośrednio – maks. 10 na skanowanie, każda najwyżej raz na dobę.

## Duplikaty

Dwie oferty uznawane są za to samo mieszkanie, gdy mają to samo piętro, powierzchnię różniącą się
o maks. 1 m², cenę różniącą się o mniej niż 5% i leżą w tym samym miejscu: na tej samej ulicy (a gdy
któraś jej nie podaje – w tej samej dzielnicy), w tym samym mieście i nie dalej niż 1,5 km od siebie
(według współrzędnych z Otodom). Grupa jest pokazywana jako karta najwcześniej znalezionej oferty
z rozwijaną listą pozostałych ogłoszeń (cena, ocena, link). Oferty zapisane przed tą wersją nie mają
ulicy ani dzielnicy – porównywana jest dla nich pełna lokalizacja.

## Dojazd

Współrzędne mieszkań pochodzą z Otodom (część ogłoszeń podaje lokalizację przybliżoną). Adres docelowy
jest geokodowany przez OpenStreetMap (Nominatim) tylko przy zapisie ustawień. Odległość liczona jest
w linii prostej, a czas dojazdu to **szacunek** (trasa ≈ 1,3 × odległość, średnia prędkość zależna od
środka transportu) – nie korzysta z rozkładów jazdy ani nawigacji. Wartość trafia też do promptu AI,
więc po zmianie adresu warto użyć „Oceń ponownie wszystkie oferty”.

## Limit tokenów

Każde wywołanie Claude Code jest zapisywane w bazie wraz z liczbą tokenów (wejście, wyjście, cache).
Gdy dzisiejsze zużycie osiągnie **dzienny limit** (domyślnie 100 000, `0` = bez limitu), oceny są
wstrzymywane do północy, a oferty czekają w kolejce. Limit sprawdzany jest przed każdą oceną, więc może
zostać przekroczony najwyżej o jedno zapytanie. Zużycie widać w pasku statusu.

## Skrypty CLI

```bash
# Ponowna ocena (np. po zmianie kryteriów lub promptu) – domyślnie wszystkie nieodrzucone oferty
python scripts/reevaluate.py
python scripts/reevaluate.py --unscored --limit 20 --yes
python scripts/reevaluate.py --ids 68562677 68559126 --model claude-haiku-5-5
python scripts/reevaluate.py --min-score 70 --notify --ignore-token-limit

# Eksport najlepszych ofert (domyślnie ocena ≥ 75 → reports/oferty-RRRR-MM-DD.xlsx)
python scripts/export_reports.py
python scripts/export_reports.py --min-score 60 -o oferty.csv
python scripts/export_reports.py --favorites --include-rejected
```

`reevaluate.py` respektuje dzienny limit tokenów (chyba że podasz `--ignore-token-limit`) i domyślnie nie
wysyła powiadomień (`--notify`, aby je włączyć). Nie uruchamiaj go w trakcie skanowania w aplikacji –
oba procesy mogłyby ocenić te same oferty. CSV używa średnika i przecinka dziesiętnego, więc otwiera się
poprawnie w polskim Excelu. Teksty z ogłoszeń są w eksportach zabezpieczone przed wstrzyknięciem formuł.

## Jak to działa

1. **Wyszukiwanie** – `scraper/otodom.py` buduje URL wyników posortowanych od najnowszych i czyta dane
   z osadzonego w stronie JSON-a `__NEXT_DATA__` (Next.js). Zapytania wysyła `curl_cffi`, który odtwarza
   odcisk TLS i nagłówki prawdziwej przeglądarki (Chrome / Edge / Safari / Firefox, losowo na skanowanie),
   z przerwami 1,5–4 s, ponawianiem przy 429/5xx i awaryjnie Playwright przy 403. Sprawdzane są maks.
   2 strony wyników; skan kończy się wcześniej, gdy strona nie zawiera nic nowego.
2. **Zmiany cen i nowe oferty** – ceny znanych ofert są porównywane z listą wyników (i ponownie sprawdzane
   dla obserwowanych ofert); nowe ogłoszenia (maks. 30 na skan) są pobierane w całości.
3. **Duplikaty** – po każdym skanie grupy duplikatów są przeliczane dla całej bazy.
4. **Ocena AI** – `ai/evaluator.py` uruchamia `claude -p` (Claude Code w trybie nieinteraktywnym) z promptem
   systemowym zawierającym kryteria i ogłoszeniem przekazanym przez stdin. Odpowiedź musi być czystym obiektem
   JSON – pilnuje tego schemat `--json-schema` i walidacja Pydantic. Claude Code działa **bez żadnych narzędzi**
   (`--tools ""`), hooków, pluginów i serwerów MCP, bo opis ogłoszenia pisze obca osoba: nie może on nakłonić
   Claude do wykonania czegokolwiek na Twoim komputerze (dlatego aplikacja nigdy nie używa
   `--dangerously-skip-permissions`). Błąd jednej oferty jest ponawiany przy kolejnych skanach (maks. 3 próby);
   brak Claude Code, brak zalogowania czy wyczerpany limit subskrypcji przerywają ocenianie do następnego skanu.
5. **Powiadomienia** – patrz wyżej.

Logika skanu jest w `services/scanner.py` i jest wspólna dla harmonogramu i skryptów. Harmonogram
działa wewnątrz procesu Streamlit – skanowanie trwa, dopóki działa `streamlit run`. Uruchamiaj jedną
instancję aplikacji na jedną bazę. Baza z poprzedniej wersji jest aktualizowana automatycznie przy starcie
(nowe kolumny i tabele), bez utraty danych.

## Modele i limity subskrypcji

| Model | ID (`claude --model`) |
|---|---|
| Claude Sonnet 5.5 (domyślny) | `claude-sonnet-5-5` |
| Claude Haiku 5.5 (oszczędniejszy) | `claude-haiku-5-5` |

Oceny nie są płatne osobno – zużywają limit Twojej subskrypcji Claude (ten sam, z którego korzystasz
w Claude Code). Jedna ocena to ok. 10–15 tys. tokenów (Claude Code dodaje własny kontekst) i trwa ok. 10 s
na Sonnecie. Na planie Pro skanowanie wielu ofert może szybko zużyć limit okna użycia – wtedy aplikacja
wstrzymuje oceny do następnego skanowania. Haiku zużywa limit wolniej. Dzienny limit tokenów
(domyślnie 100 000, czyli ok. 8–10 ocen) dodatkowo ogranicza zużycie przez skanowanie.

## Testy, typowanie i hook pre-commit

```bash
python -m pytest     # testy nie łączą się z Otodom, Nominatim ani nie uruchamiają prawdziwego claude
mypy                 # sprawdzenie typów (konfiguracja w mypy.ini)

.venv/bin/pre-commit install            # raz po sklonowaniu repozytorium
.venv/bin/pre-commit run --all-files    # ręczne uruchomienie wszystkich hooków
```

Przed każdym `git commit` uruchamiane są automatycznie ([pre-commit](https://pre-commit.com/),
konfiguracja w `.pre-commit-config.yaml`):

- porządki w plikach: białe znaki na końcu linii, znak nowej linii na końcu pliku, końce linii LF,
  poprawność YAML/TOML, konflikty scalania, duże pliki, klucze prywatne;
- **ruff** – lint kodu (reguły w `ruff.toml`), z automatyczną poprawką tam, gdzie to bezpieczne;
- **blokada sekretów** – commit z plikiem `.env`, bazą SQLite (`*.db`) lub kluczem API Anthropic
  (`sk-ant-…`) zostanie odrzucony;
- **mypy** i **pytest** – uruchamiane z `.venv`, tylko gdy commit zawiera pliki Pythona.

Hooki z zewnętrznych repozytoriów mają przypięte wersje; aktualizacja: `.venv/bin/pre-commit autoupdate`.

Te same hooki uruchamia **GitHub Actions** (`.github/workflows/ci.yml`) przy każdym pushu do `main`
i każdym pull requeście.

## Struktura projektu

```text
otodom-analyzer/
├── CLAUDE.md             # Zasady pracy nad projektem dla Claude Code
├── config.py             # Konfiguracja, zmienne środowiskowe, parametry domyślne
├── database.py           # Baza SQLite, modele SQLAlchemy, migracje, zapytania
├── formatting.py         # Formatowanie kwot, liczb i odmiany po polsku
├── scraper/
│   ├── otodom.py         # Budowa URL-i, parsing wyników i ogłoszeń (__NEXT_DATA__)
│   └── utils.py          # curl_cffi, ponawianie, fallback Playwright
├── ai/
│   ├── evaluator.py      # Claude Code CLI (claude -p), odpowiedź JSON, tokeny, błędy, diagnostyka
│   └── prompts.py        # Prompt systemowy i prompt ogłoszenia
├── services/
│   ├── scanner.py        # Jeden skan: wyszukiwanie, ceny, duplikaty, AI, powiadomienia
│   ├── duplicates.py     # Wykrywanie duplikatów ofert
│   ├── geo.py            # Geokodowanie celu, odległość i szacowany dojazd
│   └── export.py         # Eksport do Excela / CSV
├── notifications/
│   └── webhook.py        # ntfy.sh, Discord, Telegram
├── scripts/
│   ├── reevaluate.py     # Ponowna ocena AI zapisanych ofert
│   └── export_reports.py # Eksport najlepszych ofert do .xlsx / .csv
├── scheduler.py          # APScheduler: cykliczne i ręczne skanowanie
├── app.py                # Interfejs Streamlit
├── start.sh              # Uruchomienie aplikacji (venv + streamlit run)
├── tests/                # Testy pytest
├── .github/workflows/ci.yml  # CI: pre-commit (ruff, mypy, pytest) na GitHub Actions
├── .streamlit/config.toml
├── .env.example
├── .pre-commit-config.yaml
├── mypy.ini
├── ruff.toml
├── requirements.txt
├── README.md
└── LICENSE
```

## Uwagi

- **Korzystaj z umiarem.** Regulamin Otodom może ograniczać automatyczne pobieranie treści.
  Aplikacja jest przeznaczona do osobistego użytku – nie skracaj interwału poniżej potrzeb
  i nie zwiększaj limitów stron.
- **Zmiany po stronie Otodom.** Jeśli Otodom zmieni strukturę strony lub zacznie blokować
  zapytania, w pasku statusu pojawi się błąd skanowania (np. „brak danych `__NEXT_DATA__`” lub
  „HTTP 403”). Przy 403 zainstaluj Chromium dla Playwright (`playwright install chromium`).
- **Nominatim** (OpenStreetMap) to darmowa usługa z limitem 1 zapytania na sekundę – aplikacja
  geokoduje wyłącznie adres docelowy i tylko przy jego zmianie.
- Ocena AI jest pomocą, a nie wyrocznią – zawsze sprawdź ogłoszenie, księgę wieczystą
  i mieszkanie osobiście.

## Licencja

[MIT](LICENSE)
