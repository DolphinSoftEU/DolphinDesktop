# Do zaktualizowania w Jirze — KAN-467 … KAN-477

Gotowe, zweryfikowane, zmergowane z pracą Kamila (testpypi-preview) i
przetestowane (`tests/framework`: **2577 passed, 0 failed**, commit
`f4d267c` na branchu `security-bugfix`). Pełny techniczny opis każdego
zgłoszenia jest w `AUDYT_SECURITY.md` w repo — poniżej wersja skrócona pod
wklejenie do Jiry (status + komentarz).

Legenda proponowanego statusu: **Gotowe do code review** (bezpieczniej niż
od razu "Done", bo PR jeszcze nie poszedł) — zmień na "Done" sam, jeśli u
Was ten etap już wystarcza.

---

## KAN-467 — Wstrzyknięcie komend do s3270 (P1)

**Proponowany status:** Gotowe do code review

**Komentarz do wklejenia:**
> Zaimplementowane w `_mainframe.py`: jedno wywołanie API = dokładnie jedna
> akcja protokołu s3270. `CR`/`LF`/`NUL` w hoście lub tekście są odrzucane
> (`_reject_frame_delimiters`), host waliduje się do nazwy/IPv4/IPv6
> (`_validate_host`), cudzysłowy/backslashe są escapowane jedną wspólną
> funkcją (`_s3270_quote`). Zmergowane z niezależną poprawką Kamila
> (KAN-631/632, ten sam wektor) — dodatkowo dołożony printable-only check
> pod frame-delimiter guard. Testy: `test_security_mainframe.py`,
> `test_mainframe.py`. Nieprzetestowane: efekt na realnym binarnym
> `ws3270.exe` (poprawka działa na granicy protokołu, przed wysłaniem).

## KAN-468 — TLS dla TN3270 / TN5250 (P1)

**Proponowany status:** Gotowe do code review

**Komentarz:**
> `Desktop.mainframe(tls=, tls_cafile=, tls_context=, allow_plaintext=)`.
> Natywny TN5250 przez `ssl.create_default_context()` + weryfikacja
> certyfikatu i hosta, bez fallbacku na plaintext przy nieudanym handshake.
> Plaintext do hosta zewnętrznego blokowany domyślnie
> (`_require_transport_policy`), port 992 nie włącza TLS automatycznie.
> Testy z realnym handshake TLS na 127.0.0.1 (`test_security_mainframe.py`,
> fixture `tests/fixtures/tls/`). Nieprzetestowane: TLS wobec prawdziwego
> hosta z/OS z firmowym CA.

## KAN-469 — Sekrety w logach / trace / crash dump / Allure (P1)

**Proponowany status:** Gotowe do code review

**Komentarz:**
> `Secret` wrapper (`_logging.py`) — wartość maskowana wszędzie niezależnie
> od nazwy zmiennej; redakcja strukturalna (`redact_value`/`is_sensitive_key`)
> wpięta w trace, crash dump, Allure, pytest plugin. Zmergowane z
> niezależną pracą Kamila nad redakcją connection-string/NaN
> (potwierdzone w historii testpypi-preview, mimo że jego komentarz w
> Jirze sugerował brak commitu — commit jednak jest). Ograniczenie:
> filtr tekstowy nie chroni screenshotów/wideo (opisane w `SECURITY.md`).

## KAN-470 — Uwierzytelnienie i limity Qt Agent IPC (P1)

**Proponowany status:** Częściowo zrobione — do decyzji, czy zamykać

**Komentarz:**
> Zrobione po stronie klienta Pythona: losowa nazwa pipe per-attach,
> `reattach()` z ochroną przed reużyciem PID, weryfikacja SHA-256
> bundlowanych DLL przed iniekcją, limit rozmiaru requestu. Dołożone z
> mergowania: limit głębokości/liczby węzłów JSON requestu
> (`MAX_REQUEST_DEPTH`/`MAX_REQUEST_NODES`) i guard na NaN/Infinity.
> **Nie da się dokończyć z tego repo:** ACL pipe po stronie serwera,
> `PIPE_REJECT_REMOTE_CLIENTS`, sekret sesji — to wymaga źródeł C++ DLL,
> których nie ma w repozytorium. Zostaje jako known limitation
> (`SECURITY.md`), nie jako otwarty bug.

## KAN-471 — Podatne zależności + Pillow (P2)

**Proponowany status:** Gotowe do code review

**Komentarz:**
> `uv.lock`: Pillow 12.2.0→12.3.0, cryptography 48→50.1, mkdocs-material,
> pymdown-extensions. `CDPSession.screenshot()` akceptuje tylko PNG/JPEG
> po magic bytes, z limitem rozmiaru. `pip-audit` na pełnym locku: czysto.

## KAN-472 — Sprzątanie procesów a ponowne użycie PID (P2)

**Proponowany status:** Gotowe do code review

**Komentarz:**
> `(PID, creation-time)` pin w `_application.py`, reaper w
> `pytest_plugin.py` weryfikuje tożsamość przed `TerminateProcess`.
> Zmergowane z niezależnym mechanizmem Kamila opartym o zakotwiczony
> uchwyt OS (`_owned_process_handles`) — **oba działają razem**: uchwyt
> próbowany jako pierwszy (silniejszy — PID fizycznie nie może zostać
> ponownie użyty, dopóki uchwyt jest otwarty), weryfikacja czasu
> utworzenia jako fallback dla starszych/niezakotwiczonych wrapperów.
> Przy mergowaniu znalezione i naprawione 2 błędy: reaper usuwał PID ze
> śledzenia nawet gdy `TerminateProcess` się nie udało (psuło retry), oraz
> brakowało honorowania "fail closed" dla PID-ów z nieudanym zakotwiczeniem.

## KAN-473 — DLL search-order hijacking (P2)

**Proponowany status:** Gotowe do code review

**Komentarz:**
> Nowy `_native.py`: `load_trusted_dll()` — tylko ścieżka absolutna,
> `LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_SYSTEM32`. Wpięte
> w `_java._JABSession` i `_mainframe._resolve_hllapi_dll`. CWD/PATH nigdy
> nie przeszukiwane. Testy przepisane pod ten mechanizm w `test_java.py` i
> `test_mainframe.py` (usunięte duplikaty testujące alternatywny,
> nieużywany już mechanizm ładowania DLL z gałęzi Kamila).

## KAN-474 — Uprawnienia i przypięcie GitHub Actions (P2)

**Proponowany status:** Gotowe do code review

**Komentarz:**
> Wszystkie `uses:` przypięte do commit SHA, `permissions: contents: read`
> domyślnie, nowy job `security` (pip-audit + bandit + detect-secrets),
> Dependabot. Zweryfikowane lokalnie (bandit/detect-secrets/pip-audit
> czyste); sam przebieg workflow na GitHub nie był uruchamiany stąd.

## KAN-475 — Tożsamość endpointu CDP (P2)

**Proponowany status:** Gotowe do code review

**Komentarz:**
> Nowy `_netinfo.py` (odczyt właściciela portu loopback) +
> `Desktop._verify_cdp_port_owner()`: port zajęty przed startem →
> odmowa; po `HTTP 200` właściciel musi być uruchomionym PID lub jego
> potomkiem, inaczej `app.kill()` + błąd. Zmergowane z niezależnym,
> równoległym mechanizmem Kamila pod tym samym numerem ticketu
> (KAN-475) — zachowany mój mechanizm (szerszy, używany też do
> collision-check przed startem), dołożone jego przydatne zabezpieczenie
> "kill app przy nieudanym connect".

## KAN-476 — Globalny `os.environ` w `launch_qt` (P3)

**Proponowany status:** Gotowe do code review

**Komentarz:**
> Kod już nie modyfikował `os.environ` (zmienne szły przez prywatny blok
> środowiska dziecka). Dodany test współbieżności: 12 równoległych
> `launch_qt()`, `os.environ` rodzica nietknięte.

## KAN-477 — Schematy URL w `http_ok` (P3)

**Proponowany status:** Gotowe do code review

**Komentarz:**
> `_is_http_url()`: tylko `http`/`https` z hostem, reszta → `False` bez
> otwierania zasobu. Zmergowane z dodatkiem Kamila: każdy redirect target
> jest teraz też walidowany (nie tylko URL wejściowy), plus guard na
> NaN/Infinity w requeście.

---

## Uwagi ogólne do wpisania (opcjonalnie, jako komentarz na epiku KAN-513)

- Branch `security-bugfix` zawiera merge `testpypi-preview` → wszystkie
  11 zgłoszeń zweryfikowane pod kątem kolizji z równoległą pracą Kamila
  (Delfin7) na KAN-6xx. Kolizje były realne na 3 ticketach (KAN-467/631,
  632, KAN-469/630, KAN-475 pod tym samym numerem) — rozwiązane
  merytorycznie, nie automatycznym "bierzemy jedną stronę".
- Pełny techniczny audyt (co zrobione, co niesprawdzalne w tym środowisku,
  wyniki e2e) w `AUDYT_SECURITY.md`.
