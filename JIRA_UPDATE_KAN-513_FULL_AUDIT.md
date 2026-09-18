# Pełny audyt epika KAN-513 — status do wpisania w Jirze

Zakres: wszystkie 92 zadania epika KAN-513 (13 moich, 66 Kamila, 13
nieprzypisanych), sprawdzone systemowo przez 20 równoległych agentów
audytujących kod źródłowy (nie tylko status w Jirze), plus 10 realnych
poprawek zaimplementowanych i przetestowanych w tej sesji. Wszystko na
branchu `security-bugfix`.

Osobny plik `JIRA_UPDATE_KAN-467-477.md` pokrywa moje 11 ticketów
bezpieczeństwa (KAN-467..477) ze szczegółowym opisem — nie duplikuję ich tu,
tylko odsyłam.

---

## 1. Zaimplementowane i przetestowane W TEJ SESJI (10 realnych poprawek)

Wszystkie potwierdzone jako obecne w kodzie PRZED sesją nie były — audyt
wykazał realny, wciąż istniejący bug; poniżej co zrobiłem i gdzie.

**Proponowany status dla wszystkich: Gotowe do code review**

### KAN-574 — [Security][P1] Domyślnie blokować makra przy otwieraniu dokumentów Office
`src/dolphin_desktop/_office.py`: `ExcelApp.open()`/`WordApp.open()` ustawiają
teraz `AutomationSecurity = 3` (msoAutomationSecurityForceDisable) zaraz po
`DispatchEx`, przed otwarciem dokumentu — makra w otwieranym pliku nigdy się
nie uruchomią, niezależnie od ustawień Trust Center użytkownika. Dla Excela
dodatkowo `AskToUpdateLinks = False` i `Workbooks.Open(path, 0)` (UpdateLinks=0)
— zewnętrzne łącza nie są odświeżane przy otwarciu. Testy:
`test_excel_open_force_disables_macros_and_link_updates`,
`test_word_open_force_disables_macros` w `test_office_com.py`.

### KAN-575 — [Security][P2] Ograniczyć bufor i wymusić twardy deadline parsera TN5250
`src/dolphin_desktop/_mainframe.py`, `_Tn5250Backend._read_records()`: dodano
`_MAX_RX_BACKLOG` (1 MiB) — strumień bez `IAC EOR` przekraczający limit
zamyka połączenie z jasnym `MainframeError` zamiast rosnąć bez końca.
Poprawiono też deadline: wcześniej `if ... and not records` oznaczało, że
gdy host stale wysyła kompletne rekordy, pętla nigdy nie kończyła się mimo
minięcia zadanego timeoutu — teraz sprawdzenie deadline jest
bezwarunkowe. Testy:
`test_tn5250_read_records_enforces_deadline_even_with_records_already_parsed`,
`test_tn5250_read_records_rejects_unbounded_backlog_without_eor`.

### KAN-576 — [Security][P2] Powiązać zdarzenia recordera z właściwym oknem i polem hasła
`src/dolphin_desktop/_recorder.py`: okno pierwszoplanowe (`GetForegroundWindow`)
jest teraz przechwytywane **w wątku hooka**, w momencie faktycznego
naciśnięcia klawisza (`_kbd_proc`), a nie później, gdy wątek przetwarzający
dogoni kolejkę zdarzeń — usuwa to wyścig, w którym zmiana fokusu między
naciśnięciem klawisza a jego przetworzeniem mogła sklasyfikować wpis
(filtr `app=`, wykrywanie pola hasła) względem złego okna. Test:
`test_handle_key_uses_the_hwnd_captured_at_hook_time_not_a_live_requery`.
**Uwaga — nie zrobione w pełni:** filtr `--app`/`Recorder(app=...)` nadal
działa przez dopasowanie fragmentu **tytułu** okna, nie PID procesu — pełna
zmiana na PID wymagałaby zmiany publicznego kontraktu `Recorder(app=...)`
(dziś udokumentowanego jako "title fragment"), więc świadomie zostawiłem to
do osobnej decyzji projektowej, nie zmieniałem cichcem.

### KAN-577 — [Security][P2] Wpisywać hasło SAP jako tekst literalny, nie składnię SendKeys
`src/dolphin_desktop/_sap.py`, `SapGui.keyboard_login()`: `user`/`password`
przechodzą teraz przez `_escape_keys()` (ten sam helper używany wszędzie
indziej w bibliotece) przed `send_keys()` — hasło zawierające
`+^%~(){}` (np. `{TAB}{ENTER}pw%d`) jest teraz wpisywane dosłownie zamiast
być interpretowane jako klawisze sterujące. Istniejący test dokumentujący
stare (podatne) zachowanie nadal przechodzi bez zmian (bo "user"/"pass" nie
mają znaków specjalnych) + dodano nowy przypadek z hasłem zawierającym
metaznaki w `test_sap.py`.

### KAN-578 — [Security][P3] Walidować trace.db przed generowaniem HTML raportu
`src/dolphin_desktop/_trace.py`, `_render_steps()`: pole `seq` z bazy
trafiało nieescape'owane wprost do HTML (atrybut `alt` i tekst `#{seq}`) —
spreparowany/uszkodzony wiersz `trace.db` mógł wstrzyknąć HTML/JS (CWE-79).
Teraz `seq` jest rzutowane na `int` z bezpiecznym fallbackiem na `"?"` przy
błędzie konwersji — oba miejsca użycia stają się bezpieczne niezależnie od
zawartości bazy. Test:
`test_render_steps_escapes_a_malicious_or_non_numeric_seq_value`.

### KAN-580 — [Security][P2] Rozdzielić TestPyPI od PyPI i nie używać skip-existing dla preview
`.github/workflows/release.yml`: usunięto `skip-existing: true` z joba
`publish-testpypi` (kolizja wersji teraz jawnie failuje zamiast po cichu
zostawić stare bajty pod tym samym numerem). `smoke-testpypi` przebudowany:
zamiast jednego `pip install` mieszającego `--index-url test.pypi.org` z
`--extra-index-url pypi.org` (co pozwalało pipowi rozwiązać dowolny pakiet z
dowolnego indeksu), teraz najpierw `pip download --no-deps` **tylko** z
TestPyPI, potem porównanie SHA-256 pobranego wheela z artefaktem
zbudowanym w tym samym przebiegu workflow, dopiero potem `pip install` z
lokalnego pliku (zależności lecą normalnie z PyPI). **Nie zweryfikowane
stąd:** sam przebieg na GitHub Actions — YAML sprawdzony składniowo
(`yaml.safe_load`), realne uruchomienie wymaga pusha/release.

### KAN-581 — [Security][P2] Podnieść minimalne wersje zależności do bezpiecznych wydań
`pyproject.toml`: `Pillow>=10.4`→`>=12.3.0`, `mkdocs-material>=9.5`→`>=9.7.7`,
`sentry-sdk>=2.0`→`>=2.8.0` (CVE-2024-40647), `pytest>=8.3`→`>=9.0.3`
(CVE-2025-71176, w obu miejscach: extra `pytest` i grupie `dev`). `uv.lock`
przeregenerowany (`uv lock`), `uv lock --check` przechodzi — obecnie
zablokowane wersje (Pillow 12.3.0, mkdocs-material 9.7.7, sentry-sdk 2.60.0,
pytest 9.0.3) już spełniały nowe minimalne wersje, więc to tylko podniesienie
deklarowanego kontraktu dla użytkowników biblioteki.

### KAN-569 — config(video_fps=10.5) akceptuje ułamek zamiast zgłosić TypeError
`src/dolphin_desktop/_config.py`: `config()` teraz sprawdza
`isinstance(video_fps, bool) or not isinstance(video_fps, int)` przed
walidacją zakresu — wartość ułamkowa (wcześniej cicho ucinana przez `int()`)
zgłasza `TypeError`. Test:
`test_config_rejects_non_integer_video_fps_instead_of_truncating`.

### KAN-570 — config(retry_count=1.5) akceptuje ułamek zamiast zgłosić TypeError
Analogicznie do KAN-569, dla `retry_count`. Test:
`test_config_rejects_non_integer_retry_count_instead_of_truncating`.

### KAN-636 — dolphin init pozwala utworzyć projekt poza katalogiem roboczym
`src/dolphin_desktop/_cli.py`, `_init_cmd()`: dodano sprawdzenie, że
`Path(name).resolve()` pozostaje pod bieżącym katalogiem roboczym —
`Path.resolve()` podąża za symlinkami, więc jedno sprawdzenie łapie
jednocześnie ścieżkę absolutną, `..`-traversal i symlinkowany katalog
pośredni. Testy: `test_init_rejects_absolute_path_outside_cwd`,
`test_init_rejects_path_traversal`,
`test_init_rejects_symlinked_target_escaping_cwd` (ten ostatni pomija się
łagodnie, jeśli środowisko nie pozwala tworzyć symlinków bez uprawnień
administratora/Developer Mode).

---

## 2. Już zaimplementowane i przetestowane PRZED tą sesją (potwierdzone audytem)

Poniższe zostały sprawdzone przez agentów audytujących bezpośrednio kod
źródłowy (nie tylko status w Jirze) — zachowanie opisane jako oczekiwane w
tickecie JUŻ istnieje w kodzie, z pokrywającym je testem regresyjnym w
`tests/framework/`. **Proponowany status: Gotowe / do zamknięcia.**

KAN-464, KAN-526 (moje), KAN-465, KAN-478, KAN-509, KAN-510, KAN-511,
KAN-527, KAN-528, KAN-529, KAN-530, KAN-566, KAN-567, KAN-568, KAN-571,
KAN-572, KAN-585, KAN-586, KAN-587, KAN-588, KAN-589, KAN-590, KAN-591,
KAN-592, KAN-594, KAN-596, KAN-598, KAN-599, KAN-600, KAN-601, KAN-602,
KAN-603, KAN-604, KAN-606, KAN-607, KAN-608, KAN-609, KAN-610, KAN-611,
KAN-612, KAN-613, KAN-615, KAN-616, KAN-617, KAN-618, KAN-619, KAN-620,
KAN-621, KAN-622, KAN-623, KAN-625, KAN-626, KAN-627, KAN-628, KAN-629,
KAN-630, KAN-631, KAN-632, KAN-633, KAN-634, KAN-635.

Szczegółowe uzasadnienie (plik/linia/test) dla każdego z powyższych jest w
wynikach workflow audytowego — mogę je wypisać osobno na żądanie, tu
pominięte dla zwięzłości (61 pozycji z identycznym wzorem "already_fixed").

## 3. Zachowanie poprawne, ale brakuje testu regresyjnego (niski priorytet)

Kod już działa zgodnie z oczekiwaniem ticketu, ale nikt nie napisał testu
pilnującego, żeby to nie wróciło. Nie zmieniałem zachowania — tylko
warto dopisać testy przy okazji:

- **KAN-531** — `Locator.click(timeout_ms=...)` poprawnie nadpisuje dłuższy
  timeout lokatora, ale brak testu end-to-end (element pojawiający się po
  timeout_ms, ale przed dłuższym timeoutem lokatora).
- **KAN-573** — mechanizm ładowania pluginu pytest po starcie coverage
  (`-p no:dolphin-desktop` + lazy-load w `tests/conftest.py`) jest obecny,
  ale brak testu subprocessowego potwierdzającego rzeczywiste pokrycie
  import-time linii.
- **KAN-595** — `OracleFormsApp.item()` poprawnie propaguje błąd z nazwą
  itemu przy braku okna, ale brak testu przechodzącego przez publiczne
  `item()` (tylko przez wewnętrzny `_primary_hwnd`).
- **KAN-597** — `wait_for_text()` poprawnie czyta `poll_interval` z
  `config()`, ale żaden test nie asercjonuje realnej wartości przekazanej do
  `time.sleep()`.
- **KAN-605** — dokumentacja `--template` jest zsynchronizowana z CLI, ale
  nic nie pilnuje, żeby nie rozjechała się przy dodaniu nowego szablonu.

## 4. Wymaga decyzji człowieka, nie kodu

- **KAN-579** — ochrona pipeline'u publikacji (branch protection, reviewerzy
  środowiska GitHub, Trusted Publisher PyPI) to ustawienia repo/organizacji
  i samego PyPI, nie plik w tym repozytorium — ktoś z uprawnieniami admina
  musi to skonfigurować ręcznie. `release.yml` sam w sobie nie ma jak tego
  wymusić z kodu.
- **KAN-564** — cel "pokrycie testami ≥80%" to metryka, nie zachowanie kodu;
  silne poszlaki (commit `b24e9f3` dodający ~15000 linii testów) sugerują że
  praca została wykonana, ale wymaga realnego `pytest --cov` żeby
  potwierdzić liczbę.
- **KAN-565** — audyt/triage testów integracyjnych to ocena, nie bug;
  poszlaki (branch `cleanup/source-test-classification`, 9 PR-ów
  zmergowanych) sugerują że praca została wykonana, wymaga przeglądu przez
  człowieka czy podział testów jest poprawny.

## 5. Odrzucone w Jirze, potwierdzone zgodne z obecnym zachowaniem — nic do zrobienia

- **KAN-593** — status "Odrzucony"; `Locator.click()` celowo nie ma wbudowanej
  weryfikacji stanu po kliknięciu (to kontrakt, nie bug).
- **KAN-624** — status "Odrzucony"; `docs/migration-0.2.md` nie istnieje
  celowo — zmiany kontraktowe 0.2.0 są udokumentowane w `CHANGELOG.md`.

---

## Testy

Wszystkie zmiany z sekcji 1 przetestowane indywidualnie (pliki dotknięte:
`test_config_env.py`, `test_sap.py`, `test_trace.py`, `test_cli_init.py`,
`test_office_com.py`, `test_mainframe.py`, `test_recorder.py` — wszystkie
zielone). Pełny przebieg `tests/framework` (2577+ testów) w toku / patrz
najnowszy wynik w rozmowie.
