# WORK FRONT — frontul de lucru curent pentru Codex

**Actualizat:** 2026-08-21 09:15 EEST · Perplexity (Architect + Research + Schema)
**Acesta este fișierul pe care Codex îl citește primul la fiecare sesiune.**

---

## Stare: 🟢 F0-R APROBAT · F1 DEBLOCAT

**F0-R (PR #4) a trecut review-ul.** Verdict complet: [`docs/F0R_REVIEW.md`](./F0R_REVIEW.md)

Cele trei defecte blocante din F0 sunt rezolvate și verificate comportamental, nu doar prin citirea codului:

```
kill-switch ARMED → place_order()       → success=False, "kill-switch ARMED"   ✅
crypto_dust_sweep(confirmed="true")     → ExecutionForbidden                   ✅
"crypto_dust_sweep" in register_tools() → False                                ✅
82 teste + 45 teste de securitate       → verzi, reconfirmate independent      ✅
scaner cu defectul B1 reintrodus        → 3 teste roșii, în 2 scenarii         ✅
```

C15, C16, C17, C18, C20, C22 — închise.

**PR #4 poate fi scos din draft și merged de proprietarul repo-ului.** Perplexity nu face merge, Codex nu face merge la propriul PR.

---

# FAZA ACTIVĂ: F1 — Merge & Consolidare Quant Engine

**Branch:** `codex/phase1-quant-consolidation`
**Precondiție:** PR #4 merged în `main`. Nu începe înainte.

## ⚠️ F1.0 — PRIMUL COMMIT, ÎNAINTE DE ORICE ALTĂ MUNCĂ F1

### Închide C23 — excluderea din scaner nu are asertiune compensatorie

Scanerul exclude două fișiere din verificări:

```python
# tests/security/live_execution_scan.py:55
QUARANTINED_LEGACY_EXECUTORS = frozenset({
    Path("skills/trading_intelligence/exchanges/binance_executor.py"),
    Path("skills/trading_intelligence/exchanges/kucoin_executor.py"),
})
```

Excluderea e legitimă (cod mort de semnare, amânat aici la F1). **Dar apartenența la frozenset este singura dovadă că modulele sunt cuarantinate.** Am verificat empiric: am înlăturat guard-urile `raise_execution_forbidden` din `binance_executor.py`, restaurând o cale live semnată HMAC către `POST /api/v3/order` — **toate 45 de teste de securitate au trecut verzi.**

Aceeași clasă de defect ca B1: o zonă în care scanerul nu se uită, fără nimic compensatoriu.

**Livrabil:** `tests/security/test_legacy_quarantine.py`

1. Pentru fiecare intrare din `QUARANTINED_LEGACY_EXECUTORS`: fișierul **există**. O excludere care indică un fișier șters e o excludere putredă și trebuie să facă testul roșu.
2. Pentru fiecare metodă publică care poate emite un ordin (`place_market_order`, `place_order`, `cancel_order`, `execute`, `route`, plus orice metodă care ajunge la un `client.post`): verificare **AST pe ordinea instrucțiunilor din corpul funcției** — `raise_execution_forbidden(...)` sau `raise ExecutionForbidden(...)` apare **înainte** de orice apel de rețea. Nu `grep` pe fișier: `grep` trece și dacă guard-ul e pe altă funcție.
3. Test comportamental: apelul fiecărei astfel de metode ridică `ExecutionForbidden`.
4. **Fixture de regresie** `tests/security/fixtures/regression_c23_unguarded_executor.py.txt` — executor legacy fără guard. Testul afirmă că verificarea îl semnalează. Fără acest fixture, F1.0 nu e acceptat.

**Regulă generală adoptată, valabilă de acum în toate fazele:** orice excludere dintr-un scaner de securitate cere o asertiune care dovedește de ce excluderea e sigură. O listă de excluderi fără test compensatoriu este o zonă oarbă cu documentație.

**DoD F1.0:** cele patru puncte livrate; `pytest tests/security -v` verde cu număr crescut; înlăturarea unui guard dintr-un executor cuarantinat produce test roșu — dovedit, nu presupus.

---

## Restul F1 — cele zece condiții de merge pentru PR #2

PR #2 (`cursor/quant-engine-b60c`, draft) aduce motorul quant: backtest bar-by-bar, walk-forward, metrici, features, regime classifier, swarm — 2154 linii.

**Am auditat granița research ↔ execuție pe branch și este curată:** niciun import de `ccxt`, `keyring`, `shared.providers.exchange`, `shared.execution`, approval gate sau ordine în `trading_intelligence/*`. Singurul acces la rețea este `httpx` către API-ul public CoinGecko. I1, I2, I6 respectate.

**PR #2 nu se merge în forma actuală.** Condiții:

| # | Condiție | Motiv |
|---|---|---|
| **P2-1** | **Rezolvă coliziunea de nume.** `skills/trading_intelligence/` (legacy) și `trading_intelligence/` (nou) coexistă și se pot umbri reciproc în funcție de `sys.path`. Redenumește legacy în `legacy/trading_intelligence_v1/`, actualizează toate importurile, **și actualizează căile din `QUARANTINED_LEGACY_EXECUTORS`** — altfel F1.0 devine test putred. | C3 |
| **P2-2** | **`CostModel` obligatoriu în backtest.** `BacktestEngine.__init__` primește un `CostModel` **fără valoare implicită**: fee maker/taker, spread, slippage f(size, volatilitate, volum). Lipsa lui ⇒ excepție, nu rulare gratuită. Fără costuri, orice Sharpe raportat e ficțiune. | C8, principiul #17 |
| **P2-3** | **Intrare pe bara următoare.** Semnalul se calculează corect pe `bars[:i+1]`, dar intrarea se execută la `close` al aceleiași bare. Devine `open(i+1)` + slippage. | I5, C8 |
| **P2-4** | **Test de leakage explicit.** Serie sintetică în care viitorul e deliberat detectabil; dacă engine-ul scoate profit pe ea, testul eșuează. Afirmația „no look-ahead” din docstring trebuie dovedită, nu declarată. | I5 |
| **P2-5** | **Stop-loss și time-stop.** Engine-ul iese doar pe semnal invers sau regim advers. O strategie fără stop nu e evaluabilă la risc. | realism de risc |
| **P2-6** | **`run_id` + manifest.** `BacktestResult` transportă `run_id`, hash de config, hash de dataset, seed, versiuni de pachete. | I8 |
| **P2-7** | **Repară `crypto_bot/trade_crypto_bot.py:186`** — importul `backend.razagent_server.skills.crypto_swarm` nu există în acest repo. **Se face ultimul**, după ce F0-R e merged: repararea deschide reachability-ul pachetului `crypto_swarm`, care e sigur abia acum. | C21 |
| **P2-8** | **Dependențe:** `numpy`, `pandas`, `pyarrow`, `duckdb`, `hypothesis` în `requirements.txt`. | C11 |
| **P2-9** | **CI + branch protection.** GitHub Actions: `pytest tests/ -v` și `pytest tests/security -v` obligatorii pe fiecare push; branch protection pe `main`. Testul de securitate nu mai depinde de disciplina umană. | S6 |
| **P2-10** | **Test permanent de graniță.** Afirmă că `trading_intelligence/` nu importă niciodată execuție, exchange, keyring sau approval. Granița e curată azi; trebuie să rămână. | I1, schema §3.3 |

### Ordinea de lucru la F1

```
F1.0 (C23)  →  P2-1  →  P2-8  →  P2-9        [fundație: import curat, deps, CI]
            →  P2-2  →  P2-3  →  P2-5        [corectitudine backtest]
            →  P2-4  →  P2-6  →  P2-10       [dovezi]
            →  P2-7                          [ultimul]
            →  merge PR #2
```

### DoD F1

- [ ] F1.0 livrat, cu fixture de regresie care face verificarea roșie
- [ ] Un singur pachet quant; zero ambiguitate de import
- [ ] `CostModel` obligatoriu; backtest fără costuri imposibil de rulat
- [ ] Test de leakage verde; test de graniță research ↔ execuție verde
- [ ] `pytest tests/ -v` și `pytest tests/security -v` verzi în CI
- [ ] `docs/PHASE_F1_REPORT.md`, inclusiv ce **nu** s-a făcut
- [ ] Zero cod din F2+

---

## Stare faze

| Fază | Stare |
|---|---|
| F0 — Containment | 🔴 Respins la review (PR #3) |
| F0-R — Containment Remediation | 🟢 **APROBAT** (PR #4) — de merged de proprietar |
| **F1 — Merge & consolidare quant engine** | ⏳ **FRONT ACTIV** — F1.0 primul |
| F2 — Data plane | 🔒 Blocată de F1 |
| F3 — Backtest realist + validare | 🔒 Blocată de F2 |
| F4 — Paper broker + execution gateway | 🔒 Blocată de F3 |
| F5 — Risk engine unificat | 🔒 Blocată de F3 |
| F6 — Semnale / swarm / LLM | 🔒 Blocată de F3 |
| F7 — Observabilitate & feedback | 🔒 Blocată de F4 |
| F8 — Guvernanță & igienă | 🔒 Blocată de F1 (paralelă) |
| F9 — Capital real | 🔒 Blocată permanent până la audit explicit al proprietarului |

## Constatări

**Închise:** C1, C2, C6 (F0) · C15, C16, C17, C18, C20, C22 (F0-R)
**Deschise, amânate:** C19 → F4 · C21 → F1 (P2-7) · C3, C8, C11 → F1 · restul C4–C14 pe fazele lor
**Deschise, obligatorii acum:** **C23** → F1.0

Detalii: `docs/SCHEMA_AND_ROADMAP.md` §1.4 și §1.5 · `docs/F0_REVIEW.md` §6 · `docs/F0R_REVIEW.md` §3
