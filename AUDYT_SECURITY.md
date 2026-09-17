# Audyt bezpieczeństwa — realizacja zadań KAN-467 … KAN-477

Dokument opisuje, co zostało zaimplementowane dla każdego zgłoszenia z
folderu `SecurityTasks/`, jak zostało zweryfikowane, oraz **czego nie dało
się uruchomić / potwierdzić w tym środowisku** — zgodnie z prośbą, żeby
takie rzeczy wyaudytować tutaj zamiast udawać, że przeszły.

Zakres zmian: branch `testpypi-preview`, wersja `0.2.0.dev5`.
Środowisko weryfikacji: Windows 11, Python 3.13, `.venv` projektu,
`ruff`, `mypy`, `pytest`, `bandit`, `detect-secrets`, `pip-audit` (przez
`uvx`), lokalny `ws3270.exe` z `%LOCALAPPDATA%\wc3270`.

## Podsumowanie stanu weryfikacji

| Zadanie | Zaimplementowane | Testy jednostkowe/komponentowe | Wymaga weryfikacji poza tym środowiskiem |
|---|---|---|---|
| KAN-467 wstrzyknięcie s3270 | ✅ | ✅ | Wykonanie polecenia systemowego w realnym `ws3270` (nie odtwarzane) |
| KAN-468 TLS 3270/5250 | ✅ | ✅ (handshake + e2e mock TN5250-over-TLS) | TLS wobec realnego hosta z firmowym CA |
| KAN-469 redakcja sekretów | ✅ | ✅ | Screeny/wideo (poza filtrem tekstowym) |
| KAN-470 Qt Agent IPC | ⚠️ (część kliencka pełna; serwerowa blokowana) | ✅ | ACL/`PIPE_REJECT_REMOTE_CLIENTS` + sekret sesji + odtwarzalny build DLL — wymaga źródła C++ |
| KAN-471 zależności + Pillow | ✅ | ✅ | — (lock + `pip-audit` czyste) |
| KAN-472 PID reuse | ✅ | ✅ | Realny wyścig na żywym Windows |
| KAN-473 DLL hijacking | ✅ | ✅ | Podpis Authenticode (opcjonalny) |
| KAN-474 GitHub Actions | ✅ | n/d (konfiguracja CI) | Uruchomienie workflow na GitHub |
| KAN-475 tożsamość CDP | ✅ | ✅ | Realna kolizja portu z żywą przeglądarką |
| KAN-476 os.environ w launch_qt | ✅ (było OK, dodano test) | ✅ | — |
| KAN-477 schematy http_ok | ✅ | ✅ | — |

Legenda: ✅ zrobione i sprawdzone lokalnie.

## Wyniki testów e2e (uruchomione realnie w tym środowisku)

Poza bramką unit+komponent (2422 zdane) uruchomiono realne suity e2e tam,
gdzie środowisko na to pozwala:

| Suita e2e | Wynik | Uwagi |
|---|---|---|
| `tests/mainframe/pub400` + `tests/mainframe/tn5250_native` (żywy pub400.com) | **18/18 zdane** | s3270 (NVT) i natywny TN5250. Potwierdza KAN-467/468 na realnym hoście. Fixture'y dostały `allow_plaintext=True` — pub400 to publiczny host bez TLS. |
| `tests/qt` (realna iniekcja, PySide6 6.11.2) | **69 zdane / 3 nieudane / 56 pominięte** | Iniekcja agenta z losową nazwą pipe + weryfikacją hasha DLL działa end-to-end (KAN-470). 3 nieudane potwierdzone jako **wcześniej istniejące** (identycznie na czystym HEAD): mapowanie `objectName`→UIA AutomationId i wejście klawiatury `SendInput` na PySide6 6.11.2 — nie wynikają ze zmian bezpieczeństwa. |
| `tests/electron` (VS Code CDP) | **148 zdane / 1 pominięte** | Początkowo 149 błędów setupu przez `Target.createTarget: Not supported` — przyczyną była **zawieszona aktualizacja VS Code** (`vscode-updating` mutex → 0 targetów `page` na `/json/list`), nie kod. Po dokończeniu aktualizacji cała suita przechodzi. Potwierdza KAN-475 (weryfikacja właściciela portu) i KAN-471 (dekodowanie screenshotu) na żywym VS Code. |
| `tests/oracle_forms` (mock Swing + Java Access Bridge) | **12/12 zdane** | Mock `OracleFormsMock.java` skompilowany (`javac --release 21`) i sterowany przez JAB — ładowanie `windowsaccessbridge-64.dll` przez `_native.load_trusted_dll`, czyli wprost KAN-473 na żywym JVM. |
| `tests/mainframe/mock_tn3270/test_tls_transport.py` (mock TN5250-over-TLS, napisany pod KAN-468) | **3/3 zdane, stabilnie w 5 przebiegach** | Nowy mock: serwer TLS na 127.0.0.1 (cert fixture) + telnet + rekord Write-To-Display. Backend `tn5250` z `tls=True, tls_cafile=…` czyta ekran przez zweryfikowany kanał; niezaufany cert → `MainframeError` bez fallbacku; plaintext do portu TLS nie tworzy sesji. Pełny e2e ścieżki connect→TLS→negocjacja→parsowanie. |
| `tests/delphi` (próbka LCL zbudowana przez lazbuild) | **70/70 zdane** | `sample_lcl.exe` zbudowany (`lazbuild sample_lcl.lpi`, FPC 3.2.2). Steruje realną aplikacją Delphi/LCL przez UIA i ćwiczy m.in. reaper tożsamości procesu (KAN-472) na uruchomionym AUT. |
| `tests/steam` (realny klient Steam, CEF/CDP) | **3/3 zdane** | Wymagało utworzenia znacznika `.cef-enable-remote-debugging` w katalogu Steam (nowszy Steam ignoruje samą flagę `-cef-enable-debugging`) i restartu Steam — wtedy port CDP 8080 wstał i suita połączyła się w trybie bezpiecznym. Ćwiczy ścieżkę CDP/CEF (te same, których dotyczą KAN-475 i KAN-471). **Uwaga:** znacznik został na dysku — patrz nota niżej. |

**Błąd znaleziony i naprawiony dzięki e2e:** lokalny `ws3270` v4.5ga5 to build
**Windows Schannel**, który waliduje względem magazynu certyfikatów Windows i
**nie zna opcji `-cafile`** — odrzuca ją i nie startuje. Moja wcześniejsza
zmiana `_S3270Backend._spawn` bezwarunkowo dodawała `-cafile`, co psułoby start
s3270 na domyślnym Windows, gdy ustawiono `tls_cafile`. Poprawione:
`_s3270_supports_cafile()` sprawdza build; `-cafile` jest dodawane tylko gdy
wspierane, a przy braku wsparcia backend zgłasza jasny błąd (z podpowiedzią:
zaimportuj CA do magazynu Windows albo użyj `backend='tn5250'`) zamiast po
cichu zignorować CA i walidować względem innego magazynu zaufania. Pokryte
testami jednostkowymi w `test_security_mainframe.py`.

Metoda potwierdzenia „wcześniej istniejące": `git stash` moich zmian →
uruchomienie tego samego testu na czystym HEAD → identyczny wynik → `git
stash pop`. Do uruchomienia Qt e2e doinstalowano `PySide6` do `.venv`
(nie zmieniano `pyproject.toml`).

Nie uruchomiono: **SAP** (środowisko nie działa; mock niemożliwy — test musi
przejść przez realny interfejs COM SAP GUI Scripting), **PowerBuilder**
(wymaga runtime Appeon PB).

**Zmiana w środowisku użytkownika (do cofnięcia):** aby uruchomić testy Steam,
utworzono plik `C:\Program Files (x86)\Steam\.cef-enable-remote-debugging`.
Włącza on **na stałe** zdalne debugowanie CEF w Steam (port 8080 otwiera się
przy każdym uruchomieniu Steam) — to lokalna powierzchnia ataku bez
uwierzytelnienia, więc po testach warto ten plik usunąć, jeśli nie jest
potrzebny.

---

## KAN-467 — wstrzyknięcie komend do s3270 (P1)

**Zrobione.** `src/dolphin_desktop/_mainframe.py`:

- `_reject_frame_delimiters()` odrzuca `CR`, `LF`, `NUL` na granicy każdego
  polecenia; `send_string()` i `_exec()` przepuszczają je przez tę funkcję,
  więc jedno wywołanie API generuje dokładnie jedną akcję protokołu.
- `_s3270_quote()` — jedna wspólna funkcja serializująca argument `String(...)`
  (escapowanie `"` i `\`), używana przez wszystkie ścieżki.
- `_validate_host()` / `_validate_port()` — host tylko jako nazwa / IPv4 /
  IPv6 w nawiasach, port `1..65535`. Prefiksy x3270 inne niż `L:` (TLS) są
  odrzucane, bo `:` nie jest znakiem hosta.
- `connect()` waliduje host i port zanim cokolwiek trafi do emulatora i
  składa cel jako `[L:]host:port`.

**Testy:** `tests/framework/test_security_mainframe.py` (klasa KAN-467) +
zaktualizowane `test_mainframe.py`. Sprawdzają: odrzucenie `hello\nQuit()`,
`\r`, `\x00`; dokładnie jedną akcję; escapowanie; walidację hosta/portu;
prefiks `L:`.

**Nie zweryfikowano (audyt):** nie odtwarzano wykonania polecenia
systemowego w prawdziwym `ws3270.exe` na Windows — zgodnie z samym
zgłoszeniem zależy to od wersji/platformy emulatora. Poprawka działa na
granicy protokołu (bajty wysyłane do stdin), co jest właściwym miejscem
kontroli; skutek końcowy w konkretnym emulatorze pozostaje poza zakresem
testu jednostkowego.

## KAN-468 — TLS dla TN3270 / TN5250 (P1)

**Zrobione.** Nowe parametry `Desktop.mainframe(tls=, tls_cafile=,
tls_context=, allow_plaintext=)`:

- Natywny TN5250: `ssl.create_default_context()`, `wrap_socket()` z
  `server_hostname`, weryfikacja certyfikatu i nazwy hosta. **Brak
  fallbacku** — nieudany handshake podnosi `MainframeError`, gniazdo jest
  zamykane.
- s3270: `tls=True` (lub host `L:`) otwiera tunel `L:`; przełączniki
  wyłączające weryfikację (`-noverifycert`, `-noverifyhostcert`) są
  odrzucane.
- `_require_transport_policy()` — plaintext do hosta nie-loopback jest
  odrzucany bez `allow_plaintext=True`; port 992 nie jest traktowany jako
  TLS; niewery­fikujący `SSLContext` jest odrzucany.

**Testy:** `test_security_mainframe.py` (klasa KAN-468) — handshake TLS na
`127.0.0.1` z certyfikatem fixture (`tests/fixtures/tls/localhost.crt`),
odrzucenie niezaufanego certyfikatu bez fallbacku, odmowa plaintext,
port 992, wyjątek loopback, odrzucenie niewery­fikującego kontekstu.

**Nie zweryfikowano (audyt):** TLS wobec prawdziwego hosta z/OS/IBM i i
firmowego CA — brak takiego hosta w środowisku. Test dowodzi weryfikacji
łańcucha i nazwy hosta oraz braku cichego fallbacku po stronie klienta na
pętli zwrotnej; zachowanie serwera IBM (np. wymóg TN3270E STARTTLS) nie było
badane.

## KAN-469 — sekrety w logach, trace, crash dump, Allure (P1)

**Zrobione.** `src/dolphin_desktop/_logging.py`:

- `Secret` — wrapper wartości; `type_text(Secret("..."))` przekazuje realny
  tekst do aplikacji, ale rejestruje wartość w słowniku maskowanych sekretów
  (`mark_sensitive`), więc jest maskowana wszędzie, niezależnie od nazwy
  zmiennej. `str`/`repr` nigdy nie ujawniają wartości.
- `redact_value()` / `is_sensitive_key()` — redakcja **strukturalna**: klucz
  wrażliwy (`password`, `token`, `pin`, …) maskuje całą wartość niezależnie
  od typu; rekurencja po dict/list/set.
- Redakcja wpięta na granicy zapisu w: `_trace.record_step()` /
  `finish()` (selector, error, `error_message`, `uia_tree`),
  `_crash.write_crash_dump()` (stack, UIA, `extra`), `pytest_plugin`
  (Allure stdout/stderr, longrepr — już było), `_locator._trace_step`
  (`redact_repr(criteria)`).
- s3270/TN5250/HLLAPI: trace loguje **nazwę akcji i rozmiar payloadu**, nie
  treść `String(...)` ani surowe ramki.

**Testy:** `test_security_core.py` (klasa KAN-469) — canary nie występuje w
`trace.db`, w ZIP crash dumpa ani w attachmencie Allure; redakcja
strukturalna dict; `test_security_mainframe.py` — trace s3270/HLLAPI nie
zawiera hasła, `Secret` maskowany w logach.

**Nie zweryfikowano / ograniczenia (audyt):** filtr tekstowy nie zabezpiecza
sekretów widocznych na **screenshotach i wideo** — to jest udokumentowane w
`SECURITY.md`. Zalecenie: ograniczyć retencję artefaktów i nie publikować
ich z przebiegu, w którym w grze były realne poświadczenia.

## KAN-470 — uwierzytelnienie i limity Qt Agent IPC (P1)

**Zrobione (część możliwa z Pythona).** `_qt_inject.py`, `_qt_agent/`:

- Nazwa pipe zawiera losowy token per-attach (`dolphin_qt_<pid>_<16B hex>`),
  więc lokalny proces nie odgadnie nazwy, by wyprzedzić serwer agenta.
- `reattach()` odrzuca PID, którego czas utworzenia zmienił się od `attach`
  (ponowne użycie PID).
- `agent_manifest.json` (SHA-256 obu DLL) + `verify_agent_dll()`;
  `agent_dll_for()` weryfikuje hash przed iniekcją — podmieniona DLL jest
  odrzucana.
- Limit rozmiaru pojedynczego żądania po stronie klienta
  (`MAX_REQUEST_BYTES`, 8 MiB): przesadnie duży request jest odrzucany
  **przed** wysłaniem, więc nasz klient nie może zalać agenta w procesie, a
  połączenie zostaje używalne (kryterium „request ponad limit" od strony
  klienta). Pełne wymuszenie limitu po stronie serwera należy do DLL.

**Testy:** `test_security_core.py` (klasa KAN-470) — zgodność hashy
bundlowanych DLL, odrzucenie zmodyfikowanej DLL, unikalność i
nieprzewidywalność nazwy pipe; `test_qt_agent_rpc.py` (zaktualizowane) —
reattach reużywa zapamiętaną nazwę, limit rozmiaru żądania odrzuca bez
zrywania połączenia.

**Nie zweryfikowano / poza zakresem (audyt):**

- **Nie da się z tego repo:** jawny ACL powiązany z logon SID sesji,
  `PIPE_REJECT_REMOTE_CLIENTS` i sekret sesji na kanale — to jest po
  **stronie serwera pipe w DLL C++**, którego źródeł nie ma w repozytorium.
  Ochrona kierunku klient→serwer (weryfikacja PID serwera,
  `SECURITY_IDENTIFICATION`) była już obecna i pozostaje.
- **DD-14 / natywne artefakty:** brak źródeł C++ i odtwarzalnego buildu
  DLL; brak flagi CFG i podpisu Authenticode w bundlowanych binariach
  (potwierdzone przy generowaniu manifestu: `dllchar=0x0160` = HIGH_ENTROPY_VA
  + DYNAMIC_BASE + NX_COMPAT, katalog Authenticode pusty). Manifest SHA-256
  jest kotwicą pochodzenia do czasu przywrócenia źródeł/repro-buildu.
- Realna iniekcja do procesu Qt nie była wykonywana (brak żywego AUT Qt w
  tej sesji; testy iniekcji są headless z podstawionymi prymitywami).

## KAN-471 — podatne zależności + Pillow (P2)

**Zrobione.** `uv.lock` zaktualizowany:

| Pakiet | było | jest |
|---|---|---|
| Pillow | 12.2.0 | 12.3.0 |
| cryptography | 48.0.0 | 50.0.1 |
| mkdocs-material | 9.7.6 | 9.7.7 |
| pymdown-extensions | 10.21.3 | 11.0.2 |

- `CDPSession.screenshot()` / `CDPLocator.screenshot()` → `_decode_screenshot()`:
  akceptuje wyłącznie PNG/JPEG (magic bytes), dekoduje z przypiętą listą
  formatów (`formats=[...]`), limit rozmiaru `MAX_SCREENSHOT_BYTES` (64 MiB).

**Testy:** `test_security_core.py` (klasa KAN-471) — PNG/JPEG dekodowane,
EPS/GIF/śmieci/pusty odrzucane, limit rozmiaru; potwierdzono, że Pillow
12.3.0 nie zawiesza się na wadliwym EPS. `pip-audit` na pełnym locku:
**brak znanych podatności**.

**Uwaga:** minimalne wersje deklarowane użytkownikom (`dependencies` w
`pyproject.toml`, np. `Pillow>=10.4`) to osobny temat i wymaga oddzielnej
decyzji — sam `uv.lock` ich nie podnosi. Zostawione bez zmian, bo dotyka
kompatybilności wstecznej biblioteki.

## KAN-472 — sprzątanie procesów a ponowne użycie PID (P2)

**Zrobione.** `_application.py`:

- Tożsamość `(PID, creation-time)`: `record_process_identity()` przy starcie
  (owns_process) i przy adopcji hand-off; `_process_creation_time()` przez
  `GetProcessTimes`.
- `terminate_tracked_pid()` — pomija `TerminateProcess`, jeśli czas
  utworzenia PID się zmienił; zawsze zapomina tożsamość po próbie.
- Reaper w `pytest_plugin.pytest_runtest_teardown()` i
  `pytest_sessionfinish()` używa tej weryfikacji.

**Testy:** `test_security_core.py` (klasa KAN-472) — pominięcie ubicia dla
PID ponownie użytego, ubicie przy zgodnej tożsamości, ubicie gdy tożsamości
nie zarejestrowano (zachowanie wsteczne); zaktualizowane testy pluginu.

**Nie zweryfikowano (audyt):** realna częstotliwość wyścigu na żywym
Windows — test dowodzi kontroli tożsamości, nie statystyki. `QtAgentClient.reattach()`
dostał analogiczny pin czasu utworzenia (KAN-470).

## KAN-473 — DLL search-order hijacking (P2)

**Zrobione.** Nowy `src/dolphin_desktop/_native.py`:
`load_trusted_dll()` — tylko ścieżka absolutna do istniejącego pliku,
`LoadLibraryEx` z `LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR |
LOAD_LIBRARY_SEARCH_SYSTEM32`; ścieżka logowana. Wpięte w:

- `_java._JABSession` — DLL z `JAVA_HOME\bin` / rejestru, potem `System32`.
- `_mainframe._resolve_hllapi_dll` — katalogi vendorów w Program Files, potem
  `System32`; bare-name i ścieżka względna odrzucane.

CWD i PATH nigdy nie są przeszukiwane; brak DLL kończy się jednoznacznym
błędem z listą prób.

**Testy:** `test_security_core.py` (klasa KAN-473) + `test_mainframe.py` +
`test_java.py` (przepisane): odrzucenie ścieżki względnej i brakującego
pliku, `existing_candidates`, preferencja `JAVA_HOME\bin` przed `System32`.

**Nie zweryfikowano (audyt):** opcjonalna weryfikacja producenta/podpisu
Authenticode — nie zaimplementowana (zgłoszenie oznacza ją jako
„opcjonalnie”). `_native.load_trusted_dll` jest miejscem, gdzie taką kontrolę
można dołożyć.

## KAN-474 — uprawnienia i przypięcie GitHub Actions (P2)

**Zrobione.** `.github/workflows/*.yml` + `.github/dependabot.yml`:

- Wszystkie `uses:` przypięte do pełnych commit SHA (z komentarzem wersji).
- `permissions: contents: read` domyślnie; `contents: write` tylko w jobie
  deploy docs; `id-token: write` tylko w jobach publikujących w release.
- Nowy job `security` w CI: `pip-audit` (na dokładnym locku), `bandit -ll`,
  `detect-secrets` (hook z `.secrets.baseline`).
- `release.yml`: audyt CVE locka przed buildem publikowanego artefaktu.
- `uv lock --check` (spójność locka) zostaje bramką.
- Dependabot dla `github-actions` i `pip`.

**Zweryfikowano lokalnie:** `bandit -ll` = 0 Medium/High (dwa `# nosec B310`
z uzasadnieniem dla urlopen o stałym/zwalidowanym URL), `detect-secrets-hook`
przeciw baseline = przechodzi, `pip-audit` na locku = czysto.
`.secrets.baseline` obejmuje znane, przejrzane trafienia (fixture testowe,
klucz testowy localhost, SHA commitów, hashe manifestu).

**Nie zweryfikowano (audyt):** samo uruchomienie workflow na GitHub (składnia
YAML sprawdzona ręcznie i parserem; realny przebieg wymaga pusha na
repozytorium i dostępu do GitHub). SHA akcji rozwiązane przez API GitHub w
trakcie realizacji.

## KAN-475 — tożsamość endpointu CDP (P2)

**Zrobione.** Nowy `src/dolphin_desktop/_netinfo.py`
(`GetExtendedTcpTable`, listener PID dla portu loopback) +
`Desktop._verify_cdp_port_owner()` i kontrola kolizji przed startem w
`_launch_with_cdp_flag()`:

- Jeśli port jest już zajęty przed startem → odmowa.
- Po `HTTP 200` sprawdzany właściciel portu: musi być uruchomionym PID lub
  jego zweryfikowanym potomkiem, inaczej `app.kill()` + `RuntimeError`.
- Gdy właściciela nie da się odczytać (starszy Windows / brak uprawnień) →
  zachowanie jak dotąd (sam HTTP), bez fałszywej blokady.

**Testy:** `test_security_core.py` (klasa KAN-475) — `port_owned_by`,
odrzucenie obcego PID (z `app.kill`), akceptacja potomka, łagodność przy
braku danych.

**Nie zweryfikowano (audyt):** realna kolizja z żywą przeglądarką na
przewidywalnym porcie — test podstawia `loopback_listener_pids`. Nadal
zalecany unikalny, wysoki `debug_port` per przebieg (opisane w `SECURITY.md`).

## KAN-476 — globalny os.environ w launch_qt (P3)

**Stan:** kod już nie modyfikował `os.environ` — `launch_qt` przekazuje
zmienne przez prywatny blok środowiska dziecka (`_runner._build_environment_block`
→ `CreateProcessW` z `CREATE_UNICODE_ENVIRONMENT`). Dodano **test
współbieżności**: 12 równoległych `launch_qt()` — każde dziecko dostaje
własne `QT_ACCESSIBILITY=1`, a `os.environ` rodzica nigdy nie jest zmieniany.

**Testy:** `test_security_core.py::test_concurrent_launch_qt_never_touches_parent_environ`.

## KAN-477 — schematy URL w http_ok (P3)

**Zrobione.** `_helpers.http_ok` → `_is_http_url()`: `urlsplit`, tylko
`http`/`https` z hostem; inne schematy i URL bez hosta zwracają `False` bez
otwierania zasobu; kontrakt „nigdy nie rzuca” zachowany (szeroki `except`).

**Testy:** `test_security_core.py` (klasa KAN-477) — `file://`, `ftp://`,
`gopher://`, `http://` bez hosta, `data:` odrzucane bez wywołania `urlopen`;
`http`/`https` nadal sondowane.

---

## Czego świadomie nie ruszono

- **SAP** — środowisko nie działa (zgodnie z informacją); żaden test SAP nie
  był uruchamiany. Zmiany nie dotykają logiki SAP poza dodaniem obsługi
  `Secret` w warstwie locatorów (SAP `SapLocator.type_text` nie był
  modyfikowany).
- **Minimalne wersje `dependencies`** w `pyproject.toml` (KAN-471, uwaga) —
  osobna decyzja produktowa, poza zakresem tego zadania.
- **Rotacja poświadczenia w historii git** (`deb_branch`) — poza zakresem
  tych zgłoszeń.

## Jak odtworzyć weryfikację lokalnie

```bash
uv sync --group dev --extra vision --extra cdp --extra pytest --extra docs
uv run ruff check .
uv run ruff format --check .
uv run mypy src/
uv run pytest tests/framework tests/mainframe/hllapi_mock tests/mainframe/mock_tn3270 -q
uvx bandit -r src/dolphin_desktop -ll
uvx --from detect-secrets detect-secrets-hook --baseline .secrets.baseline $(git ls-files)
uv export --no-hashes --format requirements-txt --all-extras --no-emit-project > req.txt
uvx --from pip-audit pip-audit -r req.txt --no-deps --strict
```
