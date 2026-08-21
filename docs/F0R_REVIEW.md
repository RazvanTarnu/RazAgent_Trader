# F0-R — Review (Perplexity)

**Reviewer:** Architect + Research + Schema (Perplexity)
**Data:** 2026-08-21
**Obiect:** PR #4 (draft) `codex/phase0r-containment-remediation` @ `6449556`
**Raport recenzat:** `docs/PHASE_F0R_REPORT.md`
**Bază:** `main` @ `1366408`
**Review anterior:** [`docs/F0_REVIEW.md`](./F0_REVIEW.md) (F0 respins)

---

## VERDICT

# 🟢 F0-R APROBAT — cu un report obligatoriu (F1.0)

**F1 se DEBLOCHEAZĂ.** PR #4 poate fi scos din draft și merged de proprietarul repo-ului.

Cele trei defecte blocante din F0 (B1, B2, B3) sunt **rezolvate și verificate independent, comportamental, nu doar prin citirea codului**.

Un singur report obligatoriu: **C23** — excluderea din scaner a celor doi executori legacy nu are o asertiune compensatorie, deci cuarantina lor poate fi înlăturată silențios. Am demonstrat-o empiric (§3). Se remediază ca **F1.0, primul commit al F1, înainte de orice altă muncă F1**. Nu blochează merge-ul PR #4, pentru că starea actuală este sigură — dar gaura permite o regresie viitoare, iar clasa de defect este identică cu B1.

---

## 1. Verificare independentă a dovezilor

Toate rulate de mine, pe branch, Python 3.14, deps din `requirements.txt`.

| Dovadă revendicată | Verificare independentă | Rezultat |
|---|---|---|
| `pytest tests/ -v` → 82 passed | Rulat | ✅ **82 passed** |
| `pytest tests/security -v` → 45 passed (era 7) | Rulat | ✅ **45 passed** |
| `dust_sweeper.py` fără `hmac` / `X-MBX-APIKEY` / `POST` / host de bursă | `grep -c` pe cele patru pattern-uri | ✅ **0 potriviri** |
| Scanerul semnalează fixture-ul de regresie | Inclus în suită, trece | ✅ Confirmat |
| Kill-switch consultat înainte de `place_order` | Citit codul: `reject_if_kill_switch_armed()` este **prima** instrucțiune, înaintea ramurii de paper | ✅ Corect poziționat |
| Kill-switch armat la pornire | `lifecycle.py:12` → `ensure_persisted_armed_if_missing_or_invalid` | ✅ Confirmat |
| Kill-switch expus în metrici | `metrics_server.py:103` → `"kill_switch": read_kill_switch().value`, read-only | ✅ Confirmat |
| `AuditEvent` la refuz | `shared/execution/__init__.py` → `raise_execution_forbidden()` emite audit best-effort, apoi ridică; eșecul de audit nu maschează refuzul | ✅ Corect implementat |

### 1.1 Verificări comportamentale (nu doar statice)

Am executat efectiv codul, pentru că un review pe cod citit este exact ce a ratat B1:

```
kill-switch ARMED → place_order()            → success=False, error="kill-switch ARMED"   ✅
crypto_dust_sweep(confirmed="true")          → ExecutionForbidden("dust conversion is a
                                                financial action; paper-only build")      ✅
"crypto_dust_sweep" in register_tools()      → False                                      ✅
```

B1 este mort pe toate cele trei axe: capabilitate eliminată, apel refuzat, tool dezînregistrat.

---

## 2. Testul care contează: scanerul devine roșu la reintroducerea defectului?

Regula din `GOVERNANCE.md` §7.1 spune că un test de securitate se validează prin faptul că devine roșu, nu prin faptul că e verde. Am verificat-o adversarial, în două variante.

**Test A — pattern-ul B1 într-un fișier de producție complet nou** (`skills/crypto_swarm/evil_sweeper.py`, semnare HMAC + `client.post` către `api.binance.com/sapi/v1/asset/dust`):

```
FAILED test_no_signed_exchange_client_outside_adapters
FAILED test_no_state_changing_http_to_exchange_hosts
FAILED test_no_financial_endpoint_literals_outside_adapters
3 failed, 42 passed
```

**Test B — același pattern reinjectat în `dust_sweeper.py`, fișierul original al defectului:**

```
FAILED test_no_signed_exchange_client_outside_adapters
FAILED test_no_state_changing_http_to_exchange_hosts
FAILED test_no_financial_endpoint_literals_outside_adapters
3 failed, 42 passed
```

**Trei detecții independente în ambele cazuri.** Scanerul nu se sprijină pe o singură semnătură. Acesta este criteriul dur din `WORK_FRONT.md` Task 3 și este îndeplinit.

Apreciere pe calitatea implementării: literalii din `live_execution_scan.py` sunt sparți prin concatenare (`"binance" + ".com"`, `"create" + "_order"`) ca fișierul scanerului să nu se auto-semnaleze. Fixture-ul de regresie e salvat ca `.py.txt`, deci nu e importabil. Ambele sunt detalii de meserie corecte.

---

## 3. 🟠 C23 — excluderea din scaner nu are asertiune compensatorie

Scanerul exclude explicit două fișiere:

```python
# tests/security/live_execution_scan.py:55
QUARANTINED_LEGACY_EXECUTORS = frozenset({
    Path("skills/trading_intelligence/exchanges/binance_executor.py"),
    Path("skills/trading_intelligence/exchanges/kucoin_executor.py"),
})
```

Excluderea este documentată onest în raport (§5.1) și urmează literal formularea DoD pe care am scris-o eu („zero `hmac` în `skills/`, **sau doar în module integral cuarantinate**”). Problema nu e că excluderea există — codul mort de semnare de sub metodele cuarantinate e legitim amânat la F1. Problema este că **apartenența la acest frozenset este singura dovadă că modulele sunt cuarantinate. Nimic nu verifică faptul.**

**Test C — am înlăturat guard-urile `raise_execution_forbidden` din `binance_executor.py`**, restaurând o cale live, semnată HMAC, către `POST /api/v3/order`:

```
45 passed
```

**Zero teste roșii.** Un fișier cu client de bursă semnat, endpoint de ordine și zero guard-uri trece toată suita de securitate, pentru că e pe lista de excluderi.

Aceasta este exact clasa de defect B1: o zonă în care scanerul nu se uită, fără nimic care să compenseze. Diferența de severitate e reală și o marchez ca atare — B1 era o cale **activă** de execuție, C23 este o **gaură de regresie** peste o stare care în prezent este sigură (guard-urile sunt la locul lor, verificate). De aceea C23 nu blochează merge-ul, dar trebuie închisă înainte de orice muncă F1, cât timp lecția e proaspătă.

**Remediere cerută (F1.0):** `tests/security/test_legacy_quarantine.py` — pentru fiecare intrare din `QUARANTINED_LEGACY_EXECUTORS`:

1. fișierul există (o excludere care indică un fișier șters este o excludere putredă);
2. fiecare metodă publică care poate emite un ordin (`place_market_order`, `place_order`, `cancel_order`, `execute`, `route`) apelează `raise_execution_forbidden` sau `raise ExecutionForbidden` **înainte de orice apel de rețea** — verificat prin AST pe ordinea instrucțiunilor din corpul funcției, nu prin `grep` pe fișier;
3. test comportamental: apelul metodei ridică `ExecutionForbidden`;
4. test de regresie: dacă guard-ul e înlăturat, testul devine roșu. Se dovedește cu un fixture `.py.txt` care reproduce executorul fără guard.

**Invariant afectat:** I3 (fail-closed, ca durabilitate). **Regulă afectată:** S6 (în spirit — testul nu acoperă o zonă în care există capabilitate).

---

## 4. Evaluare pe invariante I1–I8

| Inv. | Stare | Justificare |
|---|---|---|
| **I1** Punct unic de ordin | 🟢 **RESPECTAT** | B1 eliminat. Singurele apeluri de ordin rămân în `shared/providers/exchange/{binance,kucoin}.py`, verificat de scaner cu 10 alias-uri, nu doar `create_order` |
| **I2** Paper only, `LiveBroker` inexistent | 🟢 **RESPECTAT** | `test_live_broker_does_not_exist` — afirmație pozitivă. Factory forțează `paper=True` |
| **I3** Fail-closed | 🟡 **RESPECTAT cu rezervă** | Kill-switch acum efectiv (verificat comportamental), refuzuri auditate, deny-list extins. Rezerva: C23 |
| **I4** O singură sursă de adevăr pentru mod | 🟢 **RESPECTAT** | Neschimbat față de F0, care era corect |
| **I5** Fără look-ahead | ⚪ N/A | F3 |
| **I6** LLM fără autoritate | 🟢 **RESPECTAT** | Nicio schimbare de autoritate |
| **I7** Totul auditat | 🟢 **RESPECTAT** | `raise_execution_forbidden()` emite `AuditEvent` cu `status="BLOCKED"`; audit best-effort care nu maschează excepția — semantica corectă |
| **I8** Reproductibilitate | ⚪ N/A | F3 |

## 5. Evaluare pe regulile S1–S9

| Reg. | Stare | Notă |
|---|---|---|
| **S1** Nicio execuție reală | 🟢 OK | Verificat comportamental |
| **S2** Fail-closed | 🟢 OK | Kill-switch prima verificare în `place_order`, înaintea ramurii de paper |
| **S3** Punct unic de ordin | 🟢 OK | — |
| **S4** Sursă unică de mod | 🟢 OK | — |
| **S5** LLM fără autoritate | 🟢 OK | — |
| **S6** Test de securitate verde și valid | 🟡 **OK cu rezervă** | Verde, și **demonstrat roșu la reintroducere** (§2). Rezerva: zona exclusă din C23 |
| **S7** Kill-switch default ARMED | 🟢 OK | Logica corectă din F0, acum **conectată**: lifecycle + 2 adaptoare + `prepare_trade` + metrici. Fără API de dezarmare |
| **S8** Secrete doar în keyring | 🟢 OK | `_get_keys()` eliminat; `dust_sweeper` nu mai atinge `os.environ` |
| **S9** Faza 9 blocată | 🟢 OK | — |

---

## 6. Rezolvarea constatărilor deschise

| # | Stare | Dovadă |
|---|---|---|
| **C15** dust sweep live | ✅ **REZOLVAT** | Zero `hmac`/`POST`/host; `ExecutionForbidden` la apel; dezînregistrat din `register_tools()` |
| **C16** scaner incomplet | ✅ **REZOLVAT** | 7 verificări noi + fixture; roșu confirmat de mine în 2 scenarii |
| **C17** kill-switch inert | ✅ **REZOLVAT** | 5 puncte de integrare, verificat comportamental |
| **C18** deny-list incomplet | ✅ **REZOLVAT** | 10 intrări noi, teste parametrizate |
| **C20** refuzuri neauditate | ✅ **REZOLVAT** | `raise_execution_forbidden()` centralizat |
| **C22** secrete în afara stratului | ✅ **REZOLVAT** | `_get_keys()` eliminat |
| **C19** ledger paralel pe `D:/` | 🔒 Amânat la F4 | Corect amânat |
| **C21** import `backend.razagent_server.*` | 🔒 Amânat la F1 (P2-7) | Corect amânat — repararea deschide reachability, deci vine după acest merge |
| **C23** excludere neasertată | 🟠 **NOU** | F1.0, obligatoriu înainte de restul F1 |

---

## 7. Observații minore (nu blochează, nu cer acțiune acum)

1. **`cancel_order` nu e gated pe kill-switch.** Raportul o declară explicit (§5.2) și e corect: Task 4 a numit `place_order`. În paper mode nu are efect real. Se rezolvă natural la F4, când gateway-ul devine punctul unic — anularea unui ordin este, în principiu, o acțiune de reducere a riscului, deci gating-ul ei pe kill-switch ar fi chiar contraproductiv. Decizia finală se ia la F4.
2. **`tests/platform/test_exchange_adapters.py` modificat** ca `place_order` în paper să stub-uiască `is_armed=False`. Legitim: fără asta, noua primă verificare ar face testul verde din motivul greșit. Modificarea e declarată în raport, nu ascunsă.
3. **`.gitignore` += `data/kill_switch.json`.** Corect — starea de runtime nu se versionează.
4. **Detaliu de mediu:** raportul menționează Windows / CPython 3.12.8. Eu am rulat pe Linux / 3.14 și am obținut aceleași cifre. Suita e portabilă.

---

## 8. Notă către Codex

F0-R este muncă bună. Raportul este de o onestitate care contează: `crypto_bot/trade_crypto_bot.py` este listat explicit cu importul rupt nereparat, iar excluderea celor doi executori e declarată în §5 în loc să fie strecurată în cod. Exact așa se citește un raport pe care pot să mă bazez.

Trei decizii care depășesc ce ceream și sunt corecte: literalii sparți în scaner ca să nu se auto-semnaleze, fixture-ul salvat ca `.py.txt` ca să nu fie importabil, și `raise_execution_forbidden()` centralizat cu audit best-effort care nu poate masca refuzul.

C23 nu este vina ta — DoD-ul meu spunea „sau doar în module integral cuarantinate”, ai urmat litera, iar litera avea o gaură. Am scris-o eu greșit a doua oară la rând. Reflexul de reținut, pentru amândoi: **orice excludere dintr-un scaner de securitate cere o asertiune care dovedește de ce excluderea e sigură.** O listă de excluderi fără test compensatoriu este o zonă oarbă cu documentație.

Frontul următor: [`docs/WORK_FRONT.md`](./WORK_FRONT.md) — F1.0 (C23) primul, apoi P2-1 … P2-10.
