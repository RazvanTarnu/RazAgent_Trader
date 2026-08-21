# WORK FRONT — frontul de lucru curent pentru Codex

**Actualizat:** 2026-08-21 · Perplexity (Architect + Research + Schema)
**Acesta este fișierul pe care Codex îl citește primul la fiecare sesiune.**

---

# 🔴 BLOCANT — se remediază înainte de orice altceva

**F0 a fost RESPINS la review post-merge.** Analiza completă: [`docs/F0_REVIEW.md`](./F0_REVIEW.md)

**F1 rămâne BLOCAT.** Nu se atinge PR #2, nu se începe merge-ul motorului quant, nu se scrie nimic din F2+.

**Cauza:** există o cale funcțională, neblocată și înregistrată ca tool care execută o acțiune financiară reală și ireversibilă pe contul Binance live:

```
skills/crypto_swarm/dust_sweeper.py:166
    await _binance_request("POST", "/sapi/v1/asset/dust", {"asset": ",".join(asset_list)})
```

Fără paper mode. Fără kill-switch. Fără risk engine. Fără approval gate. Cu credențiale reale citite direct din `os.environ` / keyring. Singura barieră: stringul `confirmed="true"` — exact anti-pattern-ul C1 pe care F0 trebuia să îl elimine, replicat identic într-un alt fișier.

Testul de securitate livrat în F0 trece verde în prezența acestei căi.

---

## FAZA ACTIVĂ: F0-R — Containment Remediation

**Branch:** `codex/phase0r-containment-remediation`
**Scope:** exclusiv task-urile 1–6 de mai jos. Nimic din F1+.
**Regulă:** nu se șterge și nu se rescrie munca corectă din F0 (F0.1, F0.3, F0.4, F0.5 sunt validate — vezi `F0_REVIEW.md` §2).

### Task 1 — Neutralizează `dust_sweeper` (rezolvă B1 / C15) · P0

**Fișier:** `skills/crypto_swarm/dust_sweeper.py`

Cerințe:

1. `crypto_dust_sweep()` ridică `ExecutionForbidden("dust conversion is a financial action; paper-only build")` **necondiționat**, indiferent de `confirmed`. Ștergi corpul de execuție, inclusiv apelul `POST /sapi/v1/asset/dust` și parsarea răspunsului.
2. `_binance_request()` nu mai acceptă metode care mută stare. Semnătura devine read-only: dacă `method != "GET"` ⇒ `ExecutionForbidden`. Preferabil: elimini complet parametrul `method` și helperul face doar GET.
3. Elimină `_get_keys()`. Citirile read-only (`crypto_dust_check`, `crypto_portfolio`, `_get_usdt_price`) trec prin `shared/providers/exchange/factory.py` → `ReadOnlyExchange`, la fel ca `exchange_connector.py`. Zero `os.environ.get("BINANCE_*")`, zero `hmac` în fișier. (rezolvă și C22)
4. Dacă refactorizarea la factory e prea largă pentru acest task, alternativa acceptabilă: `crypto_dust_sweep` și `_binance_request` cu `method != "GET"` ridică `ExecutionForbidden`, `import hmac` și `import hashlib` dispar, iar semnarea dispare — dar atunci `crypto_dust_check` și `crypto_portfolio` trebuie dezactivate cu `ExecutionForbidden` până la migrarea lor, pentru că nu pot funcționa fără semnare. **Nu lăsa semnare HMAC în fișier.**
5. `skills/crypto_swarm/__init__.py`: `crypto_dust_sweep` **nu se mai înregistrează** în dicționarul de tool-uri. Un tool care ridică întotdeauna excepție nu are ce căuta în registru.

**Verificare:** `grep -n "hmac\|X-MBX-APIKEY\|POST" skills/crypto_swarm/dust_sweeper.py` → zero rezultate.

### Task 2 — Inventar exhaustiv de acțiuni financiare (previne următorul B1) · P0

Înainte de a scrie testul, fă căutarea pe care F0 nu a făcut-o. Documentează rezultatul în raport, inclusiv fișierele curate.

Caută în tot repo-ul:

| Pattern | Ce vânează |
|---|---|
| hosts `binance.com`, `kucoin.com`, `api.binance`, `api.kucoin` | orice client de bursă |
| `hmac`, `hashlib.sha256`, `X-MBX-APIKEY`, `KC-API-SIGN`, `KC-API-PASSPHRASE` | logică de semnare |
| `client.post`, `client.put`, `client.delete`, `session.post`, `requests.post` | mutare de stare prin HTTP |
| `create_order`, `create_market_*_order`, `create_limit_order`, `create_order_ws`, `edit_order`, `cancel_order` | alias-uri ccxt |
| `asset/dust`, `asset/transfer`, `convert/`, `margin/`, `capital/withdraw`, `sub-account/`, `universal-transfer`, `/order`, `/orders`, `/oco` | endpoint-uri financiare |
| `getattr(`, dispatch prin dict, `functools.partial` peste metode de bursă | indirecție |

Pentru fiecare hit: clasifică READ-ONLY / MUTARE DE STARE / COD MORT, și pentru fiecare mutare de stare aplică `ExecutionForbidden`.

### Task 3 — Rescrie testul de securitate ca să prindă B1 (rezolvă B2 / C16) · P0

**Fișier:** `tests/security/test_no_live_execution.py` — extinzi, păstrezi cele 7 teste existente.

Teste noi obligatorii:

1. `test_no_signed_exchange_client_outside_adapters` — niciun modul în afara `shared/providers/exchange/` nu conține simultan un host de bursă și logică de semnare. Acesta este testul care ar fi prins B1.
2. `test_no_state_changing_http_to_exchange_hosts` — niciun `post/put/delete` HTTP într-un modul care referențiază un host de bursă, în afara adaptoarelor.
3. `test_order_aliases_are_confined_to_platform_adapters` — extinde scanarea AST la toate alias-urile ccxt din tabelul Task 2, nu doar `create_order`.
4. `test_no_indirect_order_dispatch` — detectează `getattr(obj, "<alias>")` și literalii string care conțin un alias de ordin în afara adaptoarelor.
5. `test_no_financial_endpoint_literals_outside_adapters` — niciun literal cu endpoint financiar în afara adaptoarelor.
6. `test_live_broker_does_not_exist` — afirmație pozitivă: nu există niciun fișier sau clasă `LiveBroker` / `live_broker` în repo.
7. `test_forbidden_execution_tools_are_not_registered` — niciun tool care ridică `ExecutionForbidden` nu apare în `register_tools()`.

**Criteriu de acceptare pentru B2 — obligatoriu:** adaugi `tests/security/fixtures/regression_b1_sample.py.txt` (fișier text, NU `.py`, ca să nu fie importabil) care reproduce pattern-ul `dust_sweeper`, și un test care rulează scanerul peste acest fixture și **afirmă că scanerul îl semnalează**. Un test de securitate se validează prin faptul că devine roșu la reintroducerea defectului, nu prin faptul că e verde.

### Task 4 — Conectează kill-switch-ul (rezolvă B3 / C17) · P0

1. `shared/platform/lifecycle.py`: la pornire, dacă `data/kill_switch.json` lipsește sau e invalid ⇒ `persist_armed()` + log de avertizare.
2. `shared/providers/exchange/{binance,kucoin}.py` → `place_order()`: prima verificare devine `if is_armed(): return OrderResult(success=False, error="kill-switch ARMED")`. Se aplică înaintea ramurii de paper, ca să fie testabilă acum și corectă mai târziu.
3. `skills/crypto_swarm/trade_executioner.py` → `prepare_trade()`: dacă `is_armed()`, returnează refuz fără a atinge rețeaua.
4. `metrics_server.py`: expune `kill_switch: "ARMED"|"DISARMED"` în `/metrics`. Read-only, fără endpoint de mutație.
5. Test: cu switch ARMED, `place_order` și `prepare_trade` refuză.

**Nu** adaugi API de dezarmare. Rămâne inexistent până la F4.

### Task 5 — Extinde deny-list-ul de endpoint-uri (rezolvă C18) · P1

În `shared/providers/exchange/base.py` și `skills/trading_intelligence/exchanges/base_executor.py`, adaugă la lista interzisă: `asset/dust`, `asset/convert`, `convert/`, `margin/`, `futures/`, `lending/`, `staking/`, `sub-account/`, `simple-earn/`, `loan/`. Test parametrizat care verifică fiecare intrare.

### Task 6 — Audit pe refuzuri (rezolvă C20) · P1

Fiecare `raise ExecutionForbidden` din cod de producție emite un `AuditEvent` prin `shared/events/event_logger.py` înainte de a ridica excepția (best-effort, fără să mascheze excepția). O tentativă blocată de acțiune financiară este exact evenimentul care trebuie să lase urmă.

### Nu în acest task (amânate explicit)

- **C19** (`prepare_trade` scrie în `D:/RazAgent_Enterprise/...`) → F4, la unificarea ledger-ului
- **C21** (import `backend.razagent_server.*` rupt) → F1, Task 4
- Curățenia codului mort de semnare din executorii legacy → F1
- Orice paper broker, gateway, risk engine, data plane → F4/F5/F2

### Definition of Done pentru F0-R

- [ ] `grep -rn "hmac" --include=*.py skills/` → zero rezultate, sau doar în module integral cuarantinate
- [ ] Scanerul din Task 3 raportează zero violări pe repo și **semnalează** fixture-ul de regresie
- [ ] `pytest tests/ -v` verde · `pytest tests/security -v` verde, cu numărul de teste crescut
- [ ] Kill-switch consultat în cel puțin 3 puncte, armat la pornire, expus în metrici
- [ ] `docs/PHASE_F0R_REPORT.md` cu inventarul complet din Task 2, inclusiv fișierele găsite curate
- [ ] Zero cod din F1+

---

## FAZA URMĂTOARE: F1 — Merge & Consolidare Quant Engine

**🔒 BLOCATĂ până la re-review-ul F0-R.** Specificația e mai jos ca să știi ținta, nu ca s-o începi.

**Branch (când se deblochează):** `codex/phase1-quant-consolidation`

### Ce am verificat deja în PR #2 (`cursor/quant-engine-b60c`)

Am auditat granița research ↔ execuție pe branch. **Este curată:** niciun fișier din `trading_intelligence/` nu importă `ccxt`, `keyring`, `shared.providers.exchange`, `shared.execution`, approval gate sau ceva legat de ordine. Singurul acces la rețea este `httpx` către API-ul public CoinGecko. Invariantele I1, I2, I6 sunt respectate. Limita „research plane nu importă niciodată execution” din schema §3.3 este ținută.

### Cum trebuie recenzat și modificat PR #2 înainte de merge

PR #2 **nu se merge în forma actuală.** Condiții, în ordine:

| # | Condiție | Motiv |
|---|---|---|
| **P2-1** | **Rezolvă coliziunea de nume.** `skills/trading_intelligence/` (legacy) și `trading_intelligence/` (nou) coexistă și se pot umbri reciproc în funcție de `sys.path`. Redenumește legacy în `legacy/trading_intelligence_v1/` și actualizează toate importurile. Fără asta, merge-ul introduce ambiguitate de import în codul de trading. | C3, igienă de import |
| **P2-2** | **Adaugă cost model în backtest — condiție dură.** `trading_intelligence/backtest/engine.py` nu modelează comision, spread sau slippage. Fără ele, orice Sharpe raportat e ficțiune (C8, principiul #17 din research). `BacktestEngine.__init__` primește un `CostModel` **obligatoriu, fără valoare implicită**; dacă lipsește ⇒ excepție, nu rulare gratuită. | C8 |
| **P2-3** | **Mută intrarea pe bara următoare.** Acum semnalul se calculează pe `bars[:i+1]` (corect, fără look-ahead) dar intrarea se execută la `close` al aceleiași bare — optimist și nerealizabil. Intrarea devine `open(i+1)` + slippage. | I5, C8 |
| **P2-4** | **Test de leakage explicit.** Test cu serie sintetică în care viitorul e deliberat detectabil; dacă engine-ul obține profit pe ea, testul eșuează. Afirmația „no look-ahead” din docstring trebuie dovedită, nu declarată. | I5 |
| **P2-5** | **Stop-loss și time-stop.** Engine-ul iese doar pe semnal invers sau regim advers. O strategie fără stop nu e evaluabilă la risc. | risk realism |
| **P2-6** | **`run_id` + manifest.** Fiecare `BacktestResult` transportă `run_id`, hash de config, hash de dataset, seed. Fără asta, I8 nu se poate satisface retroactiv. | I8 |
| **P2-7** | **Repară `crypto_bot/trade_crypto_bot.py:186`** — importul `backend.razagent_server.skills.crypto_swarm` nu există în acest repo. **Atenție:** repararea acestui import deschide reachability-ul pachetului `crypto_swarm`. Se face DOAR după ce F0-R Task 1 e merged. | C21 |
| **P2-8** | **Dependențe:** adaugă `numpy`, `pandas`, `pyarrow`, `duckdb`, `hypothesis` în `requirements.txt`. | C11 |
| **P2-9** | **CI:** GitHub Actions care rulează `pytest tests/ -v` și `pytest tests/security -v` pe fiecare push, plus branch protection pe `main`. Testul de securitate devine obligatoriu, nu opțional. | S6 |
| **P2-10** | **Păstrează granița curată.** După modificări, re-verifică: niciun import de execuție în `trading_intelligence/`. Adaugă un test care afirmă asta permanent. | I1, §3.3 |

**Ordinea de lucru la F1:** P2-1 → P2-8 → P2-9 (infrastructură) → P2-2, P2-3, P2-5 (corectitudine backtest) → P2-4, P2-6, P2-10 (dovezi) → P2-7 (ultimul, după F0-R) → merge PR #2.

**Cine face merge:** proprietarul repo-ului. Perplexity nu face merge, Codex nu face merge la propriul PR.

---

## Stare faze

| Fază | Stare |
|---|---|
| F0 — Containment | 🔴 **RESPINS la review** (PR #3 merged, dar incomplet) |
| **F0-R — Containment Remediation** | ⏳ **ACTIVĂ — frontul curent** |
| F1 — Merge & consolidare quant engine | 🔒 Blocată de F0-R |
| F2 — Data plane | 🔒 Blocată de F1 |
| F3 — Backtest realist + validare | 🔒 Blocată de F2 |
| F4 — Paper broker + execution gateway | 🔒 Blocată de F3 |
| F5 — Risk engine unificat | 🔒 Blocată de F3 |
| F6 — Semnale / swarm / LLM | 🔒 Blocată de F3 |
| F7 — Observabilitate & feedback | 🔒 Blocată de F4 |
| F8 — Guvernanță & igienă | 🔒 Blocată de F1 (paralelă) |
| F9 — Capital real | 🔒 Blocată permanent până la audit explicit al proprietarului |

## Constatări deschise

C1–C14: `docs/SCHEMA_AND_ROADMAP.md` §1.4 — C1, C2, C6 rezolvate de F0; restul deschise.
C15–C22: `docs/F0_REVIEW.md` §6 — C15, C16, C17 sunt blocante și se rezolvă în F0-R.
