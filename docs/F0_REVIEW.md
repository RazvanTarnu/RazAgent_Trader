# F0 — Post-Merge Review (Perplexity)

**Reviewer:** Architect + Research + Schema (Perplexity)
**Data:** 2026-08-21
**Obiect:** PR #3 `codex/verify-existence-of-documentation-files`, merged în `main` ca `f3da9ab`
**Raport de implementare recenzat:** `docs/CONTAINMENT_REPORT.md`
**Commit-uri auditate:** `edc0cb6` (implementare F0) → `f3da9ab` (merge)

---

## VERDICT

# 🔴 F0 RESPINS — NU ÎNDEPLINEȘTE OBIECTIVUL DE CONTAINMENT

**F1 rămâne BLOCAT.**

Motivul, în o propoziție: **există în continuare o cale complet funcțională, neblocată și înregistrată ca tool, care execută o acțiune financiară reală și ireversibilă pe contul Binance live** — `skills/crypto_swarm/dust_sweeper.py`, care semnează un `POST /sapi/v1/asset/dust` cu credențiale reale, fără verificare de paper mode, fără kill-switch, fără risk engine și fără approval gate.

Testul de securitate livrat în F0 nu a detectat această cale și trece verde în prezența ei. Prin urmare, atât containment-ul cât și dovada lui sunt incomplete.

**Ce este corect implementat:** F0.1, F0.2 (parțial), F0.3, F0.4, F0.5 sunt bine făcute și au fost verificate independent. Munca nu este pierdută — necesită completare, nu rescriere.

---

## 1. Verificare independentă a dovezilor raportate

| Dovadă revendicată în `CONTAINMENT_REPORT.md` | Verificare independentă | Rezultat |
|---|---|---|
| `pytest tests/ -v` → 44 passed | Rulat local, Python 3.14, deps din `requirements.txt` | ✅ **44 passed** — confirmat |
| `pytest tests/security -v` → 7 passed | Rulat local | ✅ **7 passed** — confirmat |
| „no ccxt imports outside `shared/providers/exchange/`” | `grep -rn "ccxt" --include=*.py` + scanare AST proprie | ✅ Confirmat |
| „no exchange `create_order` calls outside those adapters” | `grep -rn "create_order\|create_market\|create_limit\|createOrder"` | ✅ Confirmat — doar `shared/providers/exchange/{binance,kucoin}.py` |
| „Manual source search: direct exchange `create_order` calls exist only in … adapters” | Confirmat | ✅ Adevărat, dar **insuficient** — vezi §3 |

**Concluzie pe dovezi:** toate afirmațiile din raport sunt literal adevărate. Problema nu este onestitatea raportului, ci **domeniul de căutare**: raportul demonstrează absența unei singure semnături de apel (`ccxt` + `create_order`), nu absența capabilității de execuție. Calea reală de execuție rămasă nu folosește nici `ccxt`, nici `create_order`.

---

## 2. Ce a fost implementat corect (verificat pe cod)

| Task | Stare | Dovadă |
|---|---|---|
| **F0.1** Neutralizare `execute_trade()` | ✅ **CORECT** | `skills/crypto_swarm/trade_executioner.py:96-98` — `raise ExecutionForbidden("live execution not implemented; paper-only build")` necondiționat; apelul `ex.create_order` eliminat |
| **F0.2** Neutralizare `exchange_connector.py` | 🟡 **PARȚIAL** | Fișierul rescris corect ca `ReadOnlyExchange` peste factory; `place_order` ridică `ExecutionForbidden`; zero `ccxt`, zero credențiale. **DAR** containment-ul pachetului `crypto_swarm` nu este complet — vezi B1 |
| **F0.3** Cuarantină executori legacy | ✅ **CORECT** | `binance_executor.py:145`, `kucoin_executor.py:355`, `exchange_router.py:83` — toate ridică `ExecutionForbidden` înainte de orice implementare de ordin |
| **F0.4** Sursă unică de mod | ✅ **CORECT** | `PAPER_MODE` eliminat din `shared/binance_live_config.py`; `create_exchange_adapters()` forțează `paper = True` și ridică `ExecutionForbidden` dacă `config.safety.paper_mode is not True`. Această hardcodare a lui `paper = True` în factory este o decizie bună, mai puternică decât ce cerea schema |
| **F0.5** Retragere flip PIN | ✅ **CORECT** | `shared/patches/trading_activate.py` redus la 17 linii, zero mutație de fișier, mesaj de indisponibilitate |
| **F0.6** Test negativ de securitate | 🔴 **INSUFICIENT** | Există și trece, dar acoperirea e prea îngustă — vezi B2 |
| **F0.7** Kill-switch persistat, default ARMED | 🟡 **INCOMPLET** | Logica e corectă și fail-closed. Dar switch-ul **nu este consultat nicăieri** — vezi B3 |

---

## 3. DEFECTE BLOCANTE

### 🔴 B1 — Cale de execuție financiară reală, neblocată: `dust_sweeper.py`

**Fișier:** `skills/crypto_swarm/dust_sweeper.py`
**Funcție:** `crypto_dust_sweep(confirmed="true")`
**Linia acțiunii:** `166` — `await _binance_request("POST", "/sapi/v1/asset/dust", {"asset": ",".join(asset_list)})`

Ce face concret: convertește toate balanțele mici din contul Binance în BNB. Este o **conversie de active reală, ireversibilă, cu comision** (`totalServiceCharge` este citit din răspuns la linia ~192, deci codul știe că plătește un comision real).

Cum evită toate barierele F0:

| Barieră F0 | De ce nu se aplică |
|---|---|
| Scanare AST pentru `ccxt` | Nu importă `ccxt`. Folosește `httpx` direct (linia 6) |
| Scanare AST pentru `.create_order` | Nu apelează `create_order`. Apelează `client.post(url, …)` |
| `ExecutionForbidden` în `trade_executioner` | Modul complet diferit |
| Factory `paper = True` | Nu folosește factory-ul. `_get_keys()` (linia 16) citește `BINANCE_API_KEY` / `BINANCE_API_SECRET` direct din `os.environ` și din keyring |
| `paper_mode` din `PlatformConfig` | Nu îl citește niciodată. Nu există nicio referință la mod în fișier |
| Zero-withdrawal guard | `validate_endpoint_safety()` nu este apelat. Și chiar dacă ar fi, lista de endpoint-uri interzise din `shared/providers/exchange/base.py` și `base_executor.py` conține `asset/transfer` dar **nu** `asset/dust` |
| Kill-switch | Nu îl consultă |
| Approval gate | Nu îl consultă. Singura barieră este stringul `confirmed="true"` din kwargs — exact anti-pattern-ul identificat ca C1 în `SCHEMA_AND_ROADMAP.md` §1.4, replicat identic |

**Înregistrare ca tool:** `skills/crypto_swarm/__init__.py` liniile 6 și 13 — `from .dust_sweeper import register_tools as _ds` / `tools.update(_ds())`. Funcția este expusă sub numele `crypto_dust_sweep` în dicționarul de tool-uri al pachetului.

**Reachability — precizare onestă:** comanda din bot (`crypto_bot/trade_crypto_bot.py:186`) importă `backend.razagent_server.skills.crypto_swarm`, un prefix de modul care **nu există în acest repo** (rămășiță din extracția GodClaw). Deci în starea actuală apelul din bot eșuează la import. **Aceasta NU este o măsură de siguranță** — este un bug de extracție care va fi reparat de primul care atinge comanda `/portfolio`. Momentul în care prefixul se corectează, calea live se deschide, fără niciun avertisment. Containment-ul nu se poate sprijini pe un import rupt.

**Invariante încălcate:** I1 (punct unic de ordin), I2 (paper only), I3 (fail-closed).
**Reguli de guvernanță încălcate:** S1, S2, S3.

---

### 🔴 B2 — Testul de securitate este demonstrabil incomplet (S6 nu e satisfăcut în spirit)

`tests/security/test_no_live_execution.py` verifică două semnături:
- import de `ccxt` în afara `shared/providers/exchange/`
- apel `.create_order` în afara aceluiași director

Ambele trec verde **în prezența lui B1**. Un test de securitate care trece verde peste o cale de execuție reală nu este o dovadă; este o falsă asigurare, care e mai periculoasă decât absența testului, pentru că închide investigația.

Cale de detecție care ar fi prins B1 (verificată de mine, întoarce exact fișierele problematice):

```python
# scanare: modul care conține un host de bursă ȘI logică de semnare
EX_HOSTS = ("binance.com", "kucoin.com")
SIGNING  = ("hmac", "X-MBX-APIKEY", "KC-API-SIGN")
# → skills/crypto_swarm/dust_sweeper.py
# → skills/trading_intelligence/exchanges/kucoin_executor.py
# → skills/trading_intelligence/exchanges/binance_executor.py
```

Semnături suplimentare neacoperite de testul actual:
- alias-uri ccxt: `create_market_buy_order`, `create_market_sell_order`, `create_limit_order`, `create_order_ws`, `edit_order`
- indirecție: `getattr(ex, "create_order")(…)`, dispatch prin dicționar, `functools.partial`
- HTTP brut către orice endpoint de mutare de stare pe un host de bursă (calea folosită de B1)
- endpoint-uri financiare care nu sunt „ordine”: `asset/dust`, `asset/transfer`, `convert/*`, `margin/*`, `capital/withdraw`, `sub-account/transfer`
- absența unui test care să afirme pozitiv că `LiveBroker` nu există

**Regulă încălcată:** S6.

---

### 🟠 B3 — Kill-switch-ul este write-only: armat, dar niciodată consultat

`shared/execution/kill_switch.py` este scris corect: `read_kill_switch()` întoarce `ARMED` pentru fișier lipsă, JSON invalid, stare necunoscută, dict invalid și override de mediu ambiguu; `persist_armed()` scrie atomic; nu există API de dezarmare. Logica trece verde pe cele două teste dedicate.

Problema: **nu există niciun consumator.** Singurele referințe în cod de producție sunt:

```
crypto_bot/trade_crypto_bot.py:511  from shared.execution.kill_switch import persist_armed
crypto_bot/trade_crypto_bot.py:512  persist_armed()
```

…adică **doar scriere**, declanșată exclusiv la rotația IP-ului public. `is_armed()` și `read_kill_switch()` nu sunt apelate de nicio cale de producție. Consecință: starea `ARMED` nu blochează absolut nimic. Cerința din roadmap era „ARMED ⇒ gateway respinge tot”, iar gateway-ul apare abia în F4 — dar atunci switch-ul trebuia fie amânat la F4, fie legat provizoriu la punctele existente de acțiune (adaptoare `place_order`, `prepare_trade`).

În plus, nimic nu armează switch-ul la pornire. `shared/platform/lifecycle.py` nu îl atinge. Fișierul lipsă e tratat ca `ARMED` la citire, ceea ce e corect — dar nefiind citit, e irelevant.

**Invariant încălcat:** I3 (fail-closed, în efect practic).
**Regulă încălcată:** S7 (în efect practic).

---

## 4. Evaluare pe invariante I1–I8

| Inv. | Cerință | Stare | Justificare |
|---|---|---|---|
| **I1** | Un singur punct de intrare pentru orice ordin | 🔴 **ÎNCĂLCAT** | `dust_sweeper` este un al doilea punct de acțiune financiară, independent de orice gateway (B1) |
| **I2** | Paper only; `LiveBroker` nu există | 🟡 **PARȚIAL** | Corect: nu s-a scris niciun `LiveBroker`, factory-ul forțează `paper=True`. Încălcat de facto: B1 execută real fără să aibă nevoie de un broker |
| **I3** | Fail-closed pe orice acțiune financiară | 🔴 **ÎNCĂLCAT** | B1 e fail-open (default execută dacă `confirmed=true`); B3 face kill-switch-ul inert |
| **I4** | O singură sursă de adevăr pentru mod | 🟢 **RESPECTAT** | `PAPER_MODE` duplicat eliminat; `PlatformConfig.safety.paper_mode` e canonic; factory validează. Excepție: B1 nu citește niciun mod — nu contrazice I4, îl ignoră |
| **I5** | Fără look-ahead / leakage | ⚪ **N/A în F0** | Se evaluează la F3 |
| **I6** | LLM fără autoritate | 🟢 **RESPECTAT** | Nicio schimbare care să dea autoritate LLM-ului |
| **I7** | Totul auditat | 🟡 **PARȚIAL** | `trading_activate` loghează refuzul. Refuzurile `ExecutionForbidden` din executorii cuarantinați nu produc `AuditEvent` — o încercare de execuție blocată este exact evenimentul care trebuie auditat |
| **I8** | Reproductibilitate | ⚪ **N/A în F0** | Se evaluează de la F3 |

## 5. Evaluare pe regulile S1–S9

| Reg. | Stare | Notă |
|---|---|---|
| **S1** Nicio execuție reală; `LiveBroker` nescris | 🔴 **ÎNCĂLCAT** | B1 |
| **S2** Fail-closed | 🔴 **ÎNCĂLCAT** | B1, B3 |
| **S3** Un singur punct de ordin | 🔴 **ÎNCĂLCAT** | B1 |
| **S4** O singură sursă de adevăr pentru mod | 🟢 OK | F0.4 corect |
| **S5** LLM fără autoritate | 🟢 OK | — |
| **S6** Test de securitate obligatoriu verde | 🔴 **ÎNCĂLCAT** | Verde, dar demonstrabil incomplet (B2) |
| **S7** Kill-switch default ARMED | 🟡 **PARȚIAL** | Logica da, efectul nu (B3) |
| **S8** Secrete doar în keyring | 🟡 **PARȚIAL** | `dust_sweeper._get_keys()` citește `os.environ` direct, în afara `shared/platform/secrets.py`. Nu scurge secrete, dar ocolește stratul de secrete |
| **S9** Faza 9 blocată | 🟢 OK | — |

---

## 6. Constatări noi (în continuarea C1–C14)

| # | Sev. | Constatare | Locație |
|---|---|---|---|
| **C15** | 🔴 | `crypto_dust_sweep()` execută `POST /sapi/v1/asset/dust` real, semnat HMAC, fără nicio barieră de mod, risc, aprobare sau kill-switch. Înregistrat ca tool. | `skills/crypto_swarm/dust_sweeper.py:137-196` |
| **C16** | 🔴 | Testul de securitate acoperă doar `ccxt` + `create_order`; nu acoperă HTTP semnat brut, alias-urile ccxt, indirecția prin `getattr`, sau endpoint-urile financiare non-order. Trece verde peste C15. | `tests/security/test_no_live_execution.py` |
| **C17** | 🟠 | Kill-switch scris dar niciodată citit; `is_armed()` fără consumator; nimic nu armează la pornire. | `shared/execution/kill_switch.py` + `lifecycle.py` |
| **C18** | 🟠 | Zero-withdrawal deny-list nu conține `asset/dust`, `convert/*`, `margin/*`. Acoperă transferuri, nu conversii de active. | `shared/providers/exchange/base.py:30`, `skills/trading_intelligence/exchanges/base_executor.py:37-41` |
| **C19** | 🟠 | `prepare_trade()` scrie în `D:/RazAgent_Enterprise/Shared_Memory/claude_memory.db` — cale Windows absolută, în afara `data_dir`, registru paralel față de `TradeRepository`, neauditat. | `skills/crypto_swarm/trade_executioner.py:7` |
| **C20** | 🟡 | Refuzurile `ExecutionForbidden` nu emit `AuditEvent`. O tentativă de execuție blocată nu lasă urmă în audit trail. | toate punctele de cuarantină |
| **C21** | 🟡 | `crypto_bot/trade_crypto_bot.py:186` importă `backend.razagent_server.skills.crypto_swarm`, prefix inexistent în acest repo. Rupe comanda `/portfolio` și, mai grav, mascheaza reachability-ul real al pachetului `crypto_swarm`. | `crypto_bot/trade_crypto_bot.py:186` |
| **C22** | 🟡 | `_get_keys()` din `dust_sweeper` și pattern-ul `os.environ.get("BINANCE_SECRET")` ocolesc `shared/platform/secrets.py`. | `skills/crypto_swarm/dust_sweeper.py:16-21` |

---

## 7. Ce NU este o problemă (evitare de fals pozitiv)

- **`place_order` pe adaptoarele de platformă** rămâne prezent, dar `create_exchange_adapters()` forțează `paper_mode=True` necondiționat, deci ramura live este inaccesibilă prin factory. Acceptabil pentru F0.
- **`ReadOnlyExchange.fetch_balance()`** apelează un endpoint privat autentificat (`get_balances()`). Este citire, nu mutare de stare. Acceptabil, dar de re-evaluat la F4 dacă se dorește principiul de privilegiu minim (chei read-only pe bursă).
- **Cei doi executori legacy conțin încă logică de semnare HMAC** sub metodele cuarantinate. Metodele de ordin ridică `ExecutionForbidden` înainte de orice apel de rețea; codul mort de semnare rămâne o datorie de curățenie, nu o cale activă.
- **`persist_armed()` la rotația IP** este o decizie bună, dincolo de scope-ul cerut. Devine utilă imediat ce B3 e remediat.

---

## 8. Condiție de acceptare pentru re-review

F0 devine COMPLET, și F1 se deblochează, doar când toate sunt adevărate:

- [ ] B1 remediat: `dust_sweeper` nu mai poate emite nicio cerere care mută stare pe un cont de bursă
- [ ] B2 remediat: testul de securitate detectează HTTP semnat brut, alias-urile de ordin, indirecția, și endpoint-urile financiare non-order — și **eșuează dacă B1 e reintrodus** (dovadă prin test roșu pe un fixture care reproduce pattern-ul)
- [ ] B3 remediat: kill-switch consultat înainte de orice acțiune financiară existentă; armat la pornire în `lifecycle`
- [ ] C18 remediat: deny-list extins
- [ ] C20 remediat: refuzurile emit `AuditEvent`
- [ ] `pytest tests/ -v` și `pytest tests/security -v` verzi
- [ ] `docs/PHASE_F0R_REPORT.md` livrat

Frontul de lucru detaliat: [`docs/WORK_FRONT.md`](./WORK_FRONT.md)

---

## 9. Notă către Codex

Munca din F0 este de bună calitate pe cele cinci task-uri pe care le acoperă. `paper = True` hardcodat în factory și absența unui API de dezarmare pe kill-switch sunt decizii mai stricte decât cerea schema — exact reflexul corect.

Defectul nu este de execuție, ci de **domeniu de căutare**: containment-ul a urmărit lista de fișiere din specificația F0 în loc să întrebe „ce alte moduri există în acest repo de a mișca bani?”. Specificația mea F0 a numit `trade_executioner`, `exchange_connector` și executorii legacy pentru că acelea le găsisem eu în auditul inițial — eu am ratat `dust_sweeper` primul. Responsabilitatea specificației incomplete este a mea și e corectată în `WORK_FRONT.md`.

Lecția de reținut, valabilă pentru toate fazele următoare: **un test de securitate se validează prin faptul că devine roșu când reintroduci defectul**, nu prin faptul că e verde.
