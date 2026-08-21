# RazAgent_Trader — SCHEMA & ROADMAP

**Autor:** Architect + Research + Schema (Perplexity Computer)
**Data:** 2026-08-21
**Repo:** `RazvanTarnu/RazAgent_Trader` (privat)
**Colaborator implementare:** Codex
**Status repo la momentul auditului:** `main` @ `9bb5c81`, 117 fișiere versionate, ~18.6k LOC Python, 37 teste platformă verzi.

> **REGULI NENEGOCIABILE ALE ACESTUI DOCUMENT**
> 1. **PAPER TRADING ONLY** până la un audit ulterior explicit. Nicio execuție reală de ordine. Nici măcar accidental, nici măcar „doar de test”.
> 2. **FAIL-CLOSED** pe orice acțiune financiară: dacă un guard, un config, un provider sau o verificare lipsește / aruncă excepție / e ambiguu → acțiunea se **blochează**, nu se execută.
> 3. **Perplexity = sursa de adevăr pe schema + roadmap. Codex = executorul care implementează și testează. Niciunul nu sare peste celălalt.** Acest document este specificație, nu cod de producție.
>
> Regulile de colaborare, bucla de lucru per fază, procedura de escaladare și criteriile de acceptare sunt normate în [`docs/GOVERNANCE.md`](./GOVERNANCE.md).

---

## PARTEA 1 — INVENTAR (ce există acum, verificat în cod)

### 1.1 Structura repository-ului

```
RazAgent_Trader/
├── README.md                        # laptop-only deployment, Kimi K2.6 via OpenRouter
├── PLATFORM_HANDOFF.md              # handoff Agent 1 (Platform Architect)
├── pyproject.toml / pytest.ini / requirements.txt
├── .env.example                     # doar documentație; secretele stau în keyring
├── config/
│   ├── default.yaml                 # config versionat, ZERO secrete
│   ├── laptop.yaml.example          # template host-specific (laptop.yaml e gitignored)
│   └── MIGRATION.md
├── metrics_server.py                # FastAPI :9100, READ-ONLY (healthz/readyz/metrics)
├── crypto_bot/
│   ├── trade_crypto_bot.py          # 686 LOC — entry Telegram (@TradeCrypto13_bot), polling
│   ├── start_trade_crypto_bot.bat
│   └── skills/                      # gem_radar, news_broadcaster, trading_auditor
├── shared/                          # STRAT PLATFORMĂ (Agent 1) + moștenire GodClaw
│   ├── platform/                    # interfaces, config, secrets, lifecycle, metrics_state
│   ├── providers/
│   │   ├── llm/                     # openrouter (prod), moonshot (dormant), factory
│   │   └── exchange/                # base, binance, kucoin, factory
│   ├── market_data/provider.py      # MarketDataProvider read-only + DataQuality
│   ├── persistence/trade_repository.py   # SQLiteTradeRepository (are coloană paper_mode)
│   ├── events/event_logger.py       # SQLiteEventLogger (audit trail)
│   ├── approval_base.py / approval_gate.py / trading_approval_gate.py / approval_snapshot.py
│   ├── drawdown_guard.py            # kill-switch pierdere zilnică
│   ├── binance_live_config.py       # PAPER_MODE = True (hardcodat, „forced True”)
│   ├── patches/trading_activate.py  # comandă PIN pentru flip PAPER → LIVE
│   ├── audit_log.py / replay_engine.py / trade_journal.py / memory_manager.py
│   ├── keyring_loader.py / log_filter.py / single_instance.py / ip_watchdog.py
│   └── smart_alerts.py / webhooks.py / push_notifications.py / telegram_*.py / rag_sync.py
├── skills/                          # COD LEGACY (extras din GodClaw / RazAgent-Enterprise)
│   ├── trading_intelligence/        # 20 module: prediction_engine (503), orchestrator (463),
│   │                                # technical_analyzer, sentiment_analyzer, news_aggregator,
│   │                                # arbitrage_detector, smart_exit_manager, trade_suggester,
│   │                                # historical_ohlcv, exchanges/{binance,kucoin}_executor…
│   └── crypto_swarm/                # 8 module: trading_swarm, market_analyst, risk_manager,
│                                    # sentiment_analyzer (637), strategy_learner (678),
│                                    # trade_executioner, dust_sweeper, exchange_connector
├── scripts/                         # PowerShell ops: start/stop/status/watchdog/autostart
│   └── validate_platform.py         # validare pornire (fail clean)
└── tests/platform/                  # 37 teste: config, secrets, llm, exchange, metrics,
                                     # market_data, persistence/events
```

### 1.2 Branch-uri și PR-uri

| Ref | Stare | Conținut |
|---|---|---|
| `main` @ `9bb5c81` | activ | Fundația de platformă (PR #1, merged) |
| `cursor/platform-foundation-b60c` | merged (PR #1) | `shared/platform`, `shared/providers`, teste |
| `cursor/quant-engine-b60c` | **PR #2 — DRAFT, NEMERGED** | Motor quant nou: 27 fișiere, +2154 linii |

**PR #2 (draft) conține deja — important, nu duplica:**

```
trading_intelligence/           # pachet NOU, separat de skills/trading_intelligence
├── backtest/engine.py          # backtest bar-by-bar, folosește doar bars[:i+1] (fără look-ahead)
├── backtest/metrics.py         # CAGR, Sharpe, Sortino, maxDD, win rate, profit factor, tail loss
├── backtest/walk_forward.py    # validare walk-forward
├── data/providers/coingecko.py + composite.py
├── features/technical.py (212) + pipeline.py
├── signals/aggregator.py (205) + models.py
├── regime/classifier.py        # bull_trend / bear_trend / high_vol_chop …
├── swarm/{protocol,agents,coordinator}.py
├── pipeline/cycle.py           # ciclu research → semnal
└── tests/quant/test_quant_engine.py (256)
+ QUANT_ENGINE_HANDOFF.md
```

### 1.3 Ce funcționează bine (de păstrat)

1. **Separarea de straturi este corectă** (`PLATFORM_HANDOFF.md`): LLM → Quant → Risk → Approval → Exchange. LLM-ul nu are autoritate de execuție. Aceasta este exact regula #1 din research („separarea research de live trading”).
2. **Interfețe abstracte curate** în `shared/platform/interfaces.py`: `LLMProvider`, `ExchangeProvider`, `MarketDataProvider`, `TradeRepository`, `EventLogger`, `MetricsProvider`.
3. **Config cu precedență explicită** și invariante de siguranță: `safety.paper_mode: true`, `safety.auto_live: false`, secrete interzise în YAML, validare la pornire.
4. **Metrics server strict read-only** — zero endpoint-uri POST/PUT/PATCH/DELETE.
5. **Zero-withdrawal guard** (`validate_url_safety()`) + redactare secrete din loguri (`shared/log_filter.py`).
6. **Adaptoarele noi respectă paper mode**: `binance.py` / `kucoin.py` → `place_order()` returnează ID sintetic `paper-*` fără să atingă bursa.
7. **Data quality gating**: `MarketDataProvider` întoarce `DataQuality`, consumatorul poate respinge date stale.
8. **PR #2 aduce deja backtest fără look-ahead + walk-forward + metrici** — fundația de validare există în draft.

### 1.4 CONSTATĂRI CRITICE (blocante)

| # | Severitate | Constatare | Locație |
|---|---|---|---|
| **C1** | 🔴 **CRITIC** | `execute_trade()` apelează direct `ex.create_order(symbol, "market", side, amount)` pe un client **ccxt real**. **Nu există verificare `paper_mode`, nici drawdown guard, nici approval gate.** Singura barieră e un string `confirmed=true` dintr-un mesaj Telegram. Aceasta este o cale de execuție **LIVE fail-open**. | `skills/crypto_swarm/trade_executioner.py:141` |
| **C2** | 🔴 **CRITIC** | `skills/crypto_swarm/exchange_connector.py` instanțiază `ccxt.async_support` cu chei reale din env/keyring, complet în afara `shared/providers/exchange/factory.py`. Ocolește toate garanțiile platformei. | `skills/crypto_swarm/exchange_connector.py` |
| **C3** | 🔴 **CRITIC** | **Două stive de execuție coexistă**: `skills/trading_intelligence/exchanges/{binance,kucoin}_executor.py` (legacy) și `shared/providers/exchange/*` (nou). Legacy nu e dezactivat. Suprafață dublă de risc. | ambele arbori |
| **C4** | 🟠 **MAJOR** | „Paper mode” actual = **ordinul nu se trimite**. Nu există **broker de hârtie**: fără fill simulat, fără slippage, fără comisioane, fără poziții, fără P&L, fără echity curve live. Deci nu se poate valida nimic pe paper. | `shared/providers/exchange/binance.py:112` |
| **C5** | 🟠 **MAJOR** | `PAPER_MODE` există în **cel puțin 4 surse de adevăr** paralele: `config/default.yaml → safety.paper_mode`, `shared/binance_live_config.py:12` (hardcodat), `crypto_bot.config.PAPER_MODE`, env `PAPER_MODE`. Fiecare modul citește alta. Ambiguitate = risc. | multiple |
| **C6** | 🟠 **MAJOR** | `shared/patches/trading_activate.py` **rescrie fișierul de config** pentru a flip-ui `PAPER_MODE = True → False` cu un PIN, apoi hot-reload. Un singur secret slab → LIVE. Contravine mandatului „paper only”. | `shared/patches/trading_activate.py:120` |
| **C7** | 🟠 **MAJOR** | `drawdown_guard.check_drawdown()` e apelat **doar** din `skills/trading_intelligence/exchanges/base_executor.py:179`. Calea `crypto_swarm` nu îl atinge niciodată. Guard neuniversal = guard inexistent. | grep pe repo |
| **C8** | 🟡 | Backtest-ul din PR #2 nu modelează **comisioane, slippage, spread**, e long-only, fără stop-loss, iar intrarea se face la **close-ul barei care a generat semnalul** (optimist). Fără costuri realiste, orice Sharpe e ficțiune (principiul #17 din research). | `trading_intelligence/backtest/engine.py` |
| **C9** | 🟡 | Fără **purged K-fold / embargo**, fără test out-of-sample sigilat, fără Monte Carlo / permutation test. Walk-forward singur nu previne overfitting prin iterare repetată. | `backtest/walk_forward.py` |
| **C10** | 🟡 | `shared/replay_engine.py` este **decision replay** (post-mortem de decizii), NU replay de piață. Ușor de confundat cu backtesting. | `shared/replay_engine.py` |
| **C11** | 🟡 | Fără `numpy` / `pandas` / `pyarrow` în `requirements.txt`. Fără strat de stocare time-series (doar SQLite ad-hoc). Nu scalează la research serios. | `requirements.txt` |
| **C12** | 🟡 | Fără feature store, fără model registry, fără versionare dataset/model. Rezultatele nu sunt reproductibile. | absent |
| **C13** | 🟡 | Fără suită `@pytest.mark.live`, fără test care să **demonstreze** că nicio cale nu poate emite un ordin real (test negativ de securitate). | `tests/` |
| **C14** | ⚪ | README menționează host `DESKTOP-BH3MFQ9` / IP `192.168.1.137`; device-ul activ este `DESKTOP-R7BP6VC`. Documentație de deployment desincronizată. | `README.md` |

---

## PARTEA 2 — COMPARAȚIE CU RESEARCH-UL ANTERIOR

Referință: **GLOBAL AI TRADING INTELLIGENCE REPORT 2026** (sesiunea anterioară) — cele 20 de principii comune ale firmelor de top, matricea de priorități de build, și planul de 90 de zile.

| Principiu / componentă din research | Status în RazAgent_Trader | Comentariu |
|---|---|---|
| 1. Separarea research ↔ live trading | 🟡 Parțial | Arhitectura o cere; `crypto_swarm` o încalcă (C1) |
| 2. Walk-forward validation obligatorie | 🟡 În draft | Există în PR #2, nemerged, fără gate obligatoriu |
| 3. Diversificare de semnale (nu un model) | 🟢 Bun | `signals/aggregator` + swarm cu 3 roluri |
| 4. Infrastructură proprie | 🟢 OK pentru scară | Laptop + workstation local; adecvat Tier 1 |
| 6. Peer review intern al strategiilor | 🔴 Absent | Fără registru de strategii, fără proces de aprobare research |
| 7. Control data leakage (purged CV, embargo) | 🔴 Absent | C9 |
| 8. Execution alpha / modelare costuri | 🔴 Absent | C8 — fără fee/slippage/spread |
| 9. Risk limits automate | 🟡 Parțial | `drawdown_guard` există dar neuniversal (C7) |
| 10. Feedback loop trading → research | 🟡 Parțial | `trading_improvement_loop`, `strategy_learner` — nemăsurat |
| 11. Alternative data (nu doar OHLCV) | 🟡 Parțial | News + sentiment; fără validare de valoare predictivă |
| 13. Model ensembles | 🟡 Parțial | Agregare de semnale, fără ensemble ML antrenat |
| 14. Human oversight (AI nu setează risk limits) | 🟢 Bun | Approval gates + PIN; LLM fără autoritate |
| 17. Cost awareness / transaction costs realiste | 🔴 Absent | C8 |
| 18. Regime adaptability | 🟢 În draft | `regime/classifier.py` |
| 20. Continuous learning / retraining | 🔴 Absent | Fără model registry, fără retrain schedule |
| **Pipeline research: RAW → VALIDATED → NORMALIZED → FEATURES → FEATURE STORE → MODEL → SIGNAL → BACKTEST → PAPER** | 🟡 ~40% | Lipsesc: validated/normalized layer, feature store, model layer, paper broker |
| **Gate-uri de promovare** (Hypothesis → Backtest → Robustness → OOS → Paper → Capital) | 🔴 Absent | Nu există niciun gate formal; oricine poate declara o strategie „gata” |
| **P0 din matricea de build: data ingestion+validation, backtesting, feature engineering, risk engine** | 🟡 2/4 | Backtest + features în draft; data validation și risk engine unificat lipsesc |

**Diagnostic sintetic:** repo-ul are o **fundație de platformă foarte bună** (interfețe, config, secrete, metrici, audit) și un **motor quant promițător în draft**. Îi lipsește exact ce transformă un bot într-un agent de trading serios: **un broker de hârtie real, un risk engine unic obligatoriu, costuri de tranzacționare realiste, gate-uri de promovare și eliminarea căilor de execuție fail-open.**

---

## PARTEA 3 — SCHEMA ȚINTĂ

### 3.1 Principii arhitecturale (invariante)

| # | Invariant | Cum se aplică |
|---|---|---|
| **I1** | **Un singur punct de intrare pentru orice ordin.** `ExecutionGateway` este singura componentă care poate emite un ordin. | Test de securitate care eșuează dacă orice modul în afara gateway-ului importă `ccxt` sau apelează `create_order` |
| **I2** | **Paper only.** În v1, `ExecutionGateway` rutează 100% către `PaperBroker`. Ruta live **nu este implementată**, nu doar dezactivată. | `LiveBroker` nu există ca clasă |
| **I3** | **Fail-closed.** Orice guard indisponibil / excepție / config lipsă / date stale → `REJECTED`, niciodată `ALLOWED`. | Default `deny`; guard-urile returnează `Decision`, nu `bool` |
| **I4** | **O singură sursă de adevăr pentru mod.** `PlatformConfig.safety` este canonic. Toate celelalte definiții de `PAPER_MODE` se șterg. | Test care interzice re-definirea |
| **I5** | **Fără look-ahead, fără leakage.** Orice calcul de features/semnal la momentul `t` folosește doar date `≤ t`; intrarea se simulează la bara `t+1`. | Test de leakage cu date sintetice |
| **I6** | **LLM = research assistant, nu decident.** Nu generează ordine, nu setează limite de risc, nu poate escalada privilegii. | `LLMProvider` întoarce `LLMRecommendation`, tip distinct de `OrderRequest` |
| **I7** | **Totul e auditat.** Fiecare decizie (semnal, verdict de risc, aprobare, fill simulat) → `EventLogger`, imuabil, cu `run_id`. | Coverage de audit verificat în teste |
| **I8** | **Reproductibilitate.** Fiecare backtest/paper run are `run_id`, hash de config, hash de dataset, seed. | Manifest `runs/<run_id>/manifest.json` |

### 3.2 Diagrama de module

```
┌───────────────────────────────────────────────────────────────────┐
│ L0 · PLATFORM (există, se păstrează)                              │
│  shared/platform/{interfaces,config,secrets,lifecycle,metrics}    │
│  shared/providers/{llm,exchange}   shared/{events,persistence}    │
└───────────────────────────────────┬───────────────────────────────┘
                                    │
┌───────────────────────────────────▼───────────────────────────────┐
│ L1 · DATA PLANE                          [de construit: parțial]  │
│  data/ingest      → OHLCV, news, sentiment (async, rate-limited)  │
│  data/validate    → schemă, range, monotonie ts, gap detect       │
│  data/normalize   → simbol canonic, TZ UTC, timeframe canonic     │
│  data/store       → Parquet partiționat + DuckDB (read); SQLite   │
│                     doar pentru state operațional                 │
│  CONTRACT: fiecare bară are `as_of` + `DataQuality`.              │
│  Date stale sau cu gap → FAIL-CLOSED (nu se generează semnal).    │
└───────────────────────────────────┬───────────────────────────────┘
                                    │
┌───────────────────────────────────▼───────────────────────────────┐
│ L2 · FEATURE PLANE                       [draft PR #2 + de extins]│
│  features/technical  · price, volume, volatility, momentum        │
│  features/regime     · clasificare regim                          │
│  features/altdata    · news/sentiment (opțional, validat separat) │
│  features/store      · feature store versionat (nume+versiune+hash)│
│  CONTRACT: features pure, deterministe, fără I/O, fără stare.     │
└───────────────────────────────────┬───────────────────────────────┘
                                    │
┌───────────────────────────────────▼───────────────────────────────┐
│ L3 · RESEARCH PLANE (offline, ZERO acces la execuție)             │
│  research/hypotheses  · registru de ipoteze (fișier, versionat)   │
│  research/backtest    · engine bar-by-bar + COST MODEL            │
│  research/validation  · walk-forward, purged K-fold + embargo,    │
│                          Monte Carlo, permutation, OOS sigilat    │
│  research/registry    · strategy registry cu stări + gate-uri     │
│  LIMITĂ DURĂ: acest strat nu importă niciodată execution.         │
└───────────────────────────────────┬───────────────────────────────┘
                                    │ doar strategii PROMOVATE
┌───────────────────────────────────▼───────────────────────────────┐
│ L4 · SIGNAL / DECISION PLANE                                      │
│  signals/models · signals/aggregator · swarm/{protocol,agents}    │
│  llm/advisor    · LLM = comentariu + raționament, NU ordin        │
│  OUTPUT: TradeIntent {symbol, side, size_pct, confidence,         │
│                       rationale, features_hash, run_id}           │
└───────────────────────────────────┬───────────────────────────────┘
                                    │
┌───────────────────────────────────▼───────────────────────────────┐
│ L5 · RISK ENGINE  ← UNIC, OBLIGATORIU, FAIL-CLOSED                │
│  risk/limits      · max_trade_usd, max_position_pct, max_symbols  │
│  risk/drawdown    · daily loss kill-switch (drawdown_guard)       │
│  risk/exposure    · concentrare, corelație, exposure brut/net     │
│  risk/data_health · respinge dacă date stale/gap/quality != OK    │
│  risk/kill_switch · stare persistată; ARMED → totul REJECTED      │
│  OUTPUT: RiskDecision {ALLOW | REJECT, reasons[], adjusted_size}  │
│  REGULĂ: excepție în orice guard ⇒ REJECT.                        │
└───────────────────────────────────┬───────────────────────────────┘
                                    │ doar ALLOW
┌───────────────────────────────────▼───────────────────────────────┐
│ L6 · APPROVAL GATE (human-in-the-loop)                            │
│  shared/trading_approval_gate.py · timeout 30min ⇒ REJECT         │
│  Snapshot imuabil al contextului la momentul cererii              │
└───────────────────────────────────┬───────────────────────────────┘
                                    │
┌───────────────────────────────────▼───────────────────────────────┐
│ L7 · EXECUTION GATEWAY  ← SINGURA CALE DE ORDIN (I1)              │
│  execution/gateway.py                                             │
│    · reverifică: paper_mode, risk verdict, approval token         │
│    · rutează EXCLUSIV către PaperBroker                           │
│  execution/paper_broker.py   ← COMPONENTA CEA MAI IMPORTANTĂ NOUĂ │
│    · fill simulat pe bara următoare (open + slippage)             │
│    · model de cost: fee taker/maker, spread, slippage f(size,vol) │
│    · poziții, cash, mark-to-market, echity curve, P&L realizat    │
│    · order lifecycle: NEW→FILLED/PARTIAL/REJECTED/CANCELLED       │
│  execution/ledger.py · sursă de adevăr pentru poziții și P&L      │
│  ❌ execution/live_broker.py — NU EXISTĂ ÎN v1                    │
└───────────────────────────────────┬───────────────────────────────┘
                                    │
┌───────────────────────────────────▼───────────────────────────────┐
│ L8 · OBSERVABILITY & FEEDBACK                                     │
│  metrics_server.py (read-only) + paper equity/DD/Sharpe live      │
│  events/event_logger · audit imuabil                              │
│  monitoring/drift · paper vs backtest divergence, model/data drift│
│  reporting · raport zilnic paper (P&L, slippage, hit rate, DD)    │
└───────────────────────────────────────────────────────────────────┘
```

### 3.3 Responsabilități și limite (contracte între module)

| Modul | Poate | NU poate |
|---|---|---|
| `data/*` | citi API-uri publice, valida, scrie în store | genera semnale, atinge execuția |
| `features/*` | transforma bare în features deterministe | face I/O, citi „viitor” |
| `research/*` | rula backtest, valida, promova strategii | importa `execution/*`, folosi chei de bursă |
| `signals/*` | produce `TradeIntent` | produce `OrderRequest`, dimensiona final poziția |
| `risk/*` | aproba/respinge/ajusta mărimea | plasa ordine |
| `approval/*` | cere confirmare umană, expira | ocoli risk engine |
| `execution/gateway` | valida final + ruta către PaperBroker | rula live, sări peste risk/approval |
| `paper_broker` | simula fill-uri, ține ledger | atinge rețeaua |
| `llm/*` | explica, rezuma, propune ipoteze | emite ordine, seta limite |

### 3.4 Mașina de stări a unei strategii (gate-uri obligatorii)

```
DRAFT
  └─ gate G1: ipoteză documentată (economic rationale, nu doar „merge pe date”)
IN_BACKTEST
  └─ gate G2: backtest CU costuri; min N tranzacții; Sharpe IS > prag
IN_VALIDATION
  └─ gate G3: walk-forward Sharpe ≥ 60% din IS  ·  purged CV OK
             ·  Monte Carlo/permutation: p < 0.05  ·  maxDD ≤ limită
OOS_SEALED
  └─ gate G4: UN SINGUR test pe holdout sigilat. Eșec ⇒ ARCHIVED, fără reîncercare.
PAPER
  └─ gate G5: minim 60 zile paper continuu; |paper − backtest| în toleranță;
             fără breach de risc; slippage realizat ≈ modelat
PAPER_APPROVED   ← STAREA FINALĂ ÎN v1 · nu există trecere la capital real
```

**Regulă anti-overfitting:** fiecare rulare pe holdout se contorizează. Bugetul este 1. Fără excepții.

---

## PARTEA 4 — ROADMAP (ordonat, cu dependențe)

Notație: `[dep: …]`. Fiecare fază are **Definition of Done** verificabilă.

### FAZA 0 — CONTAINMENT (blocant pentru tot restul) · P0

Scop: să nu mai existe nicio cale prin care sistemul poate emite un ordin real.

| ID | Task | Dep |
|---|---|---|
| 0.1 | **Neutralizează `skills/crypto_swarm/trade_executioner.py`**: `execute_trade()` ridică `ExecutionForbidden` necondiționat. Șterge apelul `ex.create_order`. | — |
| 0.2 | **Neutralizează `skills/crypto_swarm/exchange_connector.py`**: elimină construcția de clienți ccxt cu chei; redirecționează către `shared/providers/exchange/factory.py` în mod read-only. | — |
| 0.3 | **Cuarantinează executorii legacy** `skills/trading_intelligence/exchanges/*`: mută în `legacy/` sau adaugă guard de import care aruncă la `place_order`. | — |
| 0.4 | **Sursă unică de mod**: șterge `PAPER_MODE` din `shared/binance_live_config.py` și `crypto_bot.config`; totul citește `PlatformConfig.safety.paper_mode`. | — |
| 0.5 | **Retrage `shared/patches/trading_activate.py`**: comanda PIN → răspuns „LIVE nu este implementat în această versiune”. Fără rescriere de config. | 0.4 |
| 0.6 | **Test negativ de securitate** `tests/security/test_no_live_execution.py`: scanare AST a repo-ului — niciun modul în afara `shared/providers/exchange/` nu importă `ccxt`; niciun apel `create_order` în afara adaptoarelor; `place_order` inaccesibil fără gateway. | 0.1–0.3 |
| 0.7 | **Kill-switch global persistat** (`data/kill_switch.json` + env override). ARMED ⇒ gateway respinge tot. Default la pornire dacă fișierul e corupt/lipsă: **ARMED** (fail-closed). | 0.4 |

**DoD F0:** `pytest tests/security -v` verde; grep manual confirmă zero căi de `create_order` în afara adaptoarelor; `trading_activate` nu mai poate flip-ui modul.

### FAZA 1 — MERGE & CONSOLIDARE QUANT ENGINE · P0 · [dep: F0]

| ID | Task |
|---|---|
| 1.1 | Review PR #2 (`cursor/quant-engine-b60c`) contra acestei scheme; verifică absența importurilor de execuție din `trading_intelligence/*`. |
| 1.2 | Rezolvă ambiguitatea de nume: `skills/trading_intelligence/` (legacy) vs `trading_intelligence/` (nou) → redenumește legacy în `legacy/trading_intelligence_v1/`. |
| 1.3 | Merge PR #2 în `main` după 1.1–1.2. |
| 1.4 | Adaugă `numpy`, `pandas`, `pyarrow`, `duckdb`, `hypothesis` în `requirements.txt`. |
| 1.5 | CI (GitHub Actions): `pytest tests/ -v` + testul de securitate pe fiecare push. |

**DoD F1:** un singur pachet quant, teste verzi în CI, zero import de execuție din research.

### FAZA 2 — DATA PLANE · P0 · [dep: F1]

| ID | Task |
|---|---|
| 2.1 | `data/ingest`: OHLCV multi-sursă (CoinGecko existent + Binance public REST read-only), async, rate-limited, retry cu backoff. |
| 2.2 | `data/validate`: schemă, monotonie timestamp, detectare gap-uri, outlieri, duplicate → `DataQuality{OK, DEGRADED, STALE, INVALID}`. |
| 2.3 | `data/normalize`: simbol canonic, UTC, timeframe canonic, `as_of` obligatoriu. |
| 2.4 | `data/store`: Parquet partiționat pe `symbol/timeframe/an`, citire via DuckDB. SQLite rămâne doar pentru state operațional. |
| 2.5 | Backfill istoric: minim 3 ani daily + 1 an intraday pentru universul de simboluri definit. |
| 2.6 | Hook în risk engine: `DataQuality != OK` ⇒ REJECT (fail-closed). |

**DoD F2:** dataset reproductibil cu manifest (hash, interval, sursă); test care demonstrează respingerea pe date cu gap.

### FAZA 3 — BACKTEST REALIST + VALIDARE · P0 · [dep: F2]

| ID | Task |
|---|---|
| 3.1 | **Cost model** în `backtest/engine.py`: fee maker/taker, spread, slippage f(size, volatilitate, volum). Fără cost model ⇒ rularea eșuează, nu rulează „gratis”. |
| 3.2 | **Fill pe bara următoare**: semnal la `t` ⇒ intrare la `open(t+1)` + slippage. Elimină intrarea la close-ul barei de semnal. |
| 3.3 | Stop-loss, take-profit, time-stop; suport short (opțional, flag). |
| 3.4 | `validation/purged_cv.py`: purged K-fold + embargo. |
| 3.5 | `validation/monte_carlo.py`: bootstrap pe tranzacții + permutation test pe semnal (p-value). |
| 3.6 | `validation/oos.py`: holdout sigilat, contor de utilizări, buget = 1. |
| 3.7 | `research/registry.py`: mașina de stări din §3.4, cu gate-uri G1–G5 aplicate programatic. |
| 3.8 | `runs/<run_id>/manifest.json`: hash config, hash dataset, seed, versiuni pachete, metrici. |

**DoD F3:** o strategie de referință trece G1→G4 cu artefacte reproductibile; test de leakage verde.

### FAZA 4 — PAPER BROKER + EXECUTION GATEWAY · P0 · [dep: F3]

Cea mai importantă componentă lipsă.

| ID | Task |
|---|---|
| 4.1 | `execution/paper_broker.py`: order lifecycle complet, fill simulat pe bara următoare, același cost model ca backtest-ul (cod partajat, nu duplicat). |
| 4.2 | `execution/ledger.py`: poziții, cash, mark-to-market, P&L realizat/nerealizat, echity curve — sursă unică de adevăr. |
| 4.3 | `execution/gateway.py`: singura cale de ordin. Reverifică `paper_mode`, verdict de risc, token de aprobare, kill-switch. Rutează exclusiv la `PaperBroker`. |
| 4.4 | Persistență paper în `TradeRepository` cu `paper_mode=1` forțat la nivel de schemă. |
| 4.5 | Reconciliere zilnică: ledger vs. journal vs. event log; divergență ⇒ ARM kill-switch. |

**DoD F4:** un ciclu complet `TradeIntent → Risk → Approval → Gateway → PaperBroker → Ledger → Audit`, cu P&L simulat; test care demonstrează că gateway-ul respinge când oricare verificare lipsește.

### FAZA 5 — RISK ENGINE UNIFICAT · P0 · [dep: F4, poate merge în paralel cu F4]

| ID | Task |
|---|---|
| 5.1 | `risk/engine.py`: punct unic, returnează `RiskDecision{ALLOW/REJECT, reasons[], adjusted_size}`. |
| 5.2 | Integrează `drawdown_guard` ca guard obligatoriu (nu opțional, nu try/except silent). |
| 5.3 | Limite: `max_trade_usd`, `max_position_pct`, `max_open_positions`, `max_daily_trades`, `max_gross_exposure`, concentrare/corelație. |
| 5.4 | Guard de sănătate a datelor (F2.6) și guard de vechime a semnalului. |
| 5.5 | Politică fail-closed: orice excepție ⇒ REJECT + audit + alertă. Test dedicat cu guard care aruncă. |
| 5.6 | Limitele se citesc din `PlatformConfig.safety`; modificarea lor e loguită în audit. |

**DoD F5:** grep confirmă că nicio cale nu atinge gateway-ul fără `RiskDecision`; testul cu guard defect returnează REJECT.

### FAZA 6 — SEMNALE, SWARM, LLM CU ROL CORECT · P1 · [dep: F3]

| ID | Task |
|---|---|
| 6.1 | Formalizează `TradeIntent` ca tip distinct de `OrderRequest`; conversia se face doar în gateway. |
| 6.2 | Consolidează cei doi swarm (`skills/crypto_swarm` vs `trading_intelligence/swarm`) într-unul singur. |
| 6.3 | `llm/advisor.py`: LLM produce rationale + ipoteze de research. Output-ul nu poate crește confidence-ul sau mărimea poziției. Test dedicat. |
| 6.4 | Modele ML (XGBoost/sklearn) ca generatoare de semnal, antrenate doar în research plane, cu model registry + versiune. |
| 6.5 | Validează separat valoarea predictivă a news/sentiment; dacă nu trece G3, features altdata rămân dezactivate. |

### FAZA 7 — OBSERVABILITATE & FEEDBACK · P1 · [dep: F4]

| ID | Task |
|---|---|
| 7.1 | Extinde `metrics_server.py` (read-only) cu: paper equity, drawdown curent, Sharpe rolling, nr. respingeri de risc, stare kill-switch. |
| 7.2 | Raport zilnic paper (Telegram + fișier): P&L, hit rate, slippage realizat vs modelat, breach-uri de risc. |
| 7.3 | `monitoring/drift.py`: divergență paper vs backtest, data drift, degradare Sharpe → alertă și, la depășirea pragului, ARM kill-switch. |
| 7.4 | Coverage de audit: test care verifică că fiecare decizie majoră produce un `AuditEvent`. |

### FAZA 8 — GUVERNANȚĂ & IGIENĂ · P2 · [dep: F1]

| ID | Task |
|---|---|
| 8.1 | Sincronizează `README.md` cu realitatea (host, IP, model LLM) — vezi C14. |
| 8.2 | `docs/RISK_POLICY.md`: limite, escaladare, procedură kill-switch, cine poate schimba ce. |
| 8.3 | `docs/RESEARCH_PROTOCOL.md`: cum se documentează o ipoteză, cum se cere peer review, cum se arhivează un eșec. |
| 8.4 | Rotație secrete + audit că nu apar în loguri (extinde `log_filter`). |
| 8.5 | `CODEOWNERS` + branch protection pe `main`: CI + testul de securitate obligatorii. |

### FAZA 9 — DINCOLO DE PAPER (NU SE IMPLEMENTEAZĂ ACUM)

Trecerea la capital real este **out of scope**. Preconditii minime, documentate pentru viitor: 6+ luni paper cu metrici stabile, `docs/RISK_POLICY.md` semnat, audit extern al gateway-ului, dual-control pe activare, capital limitat la 25% din dimensiunea țintă. Până atunci, `LiveBroker` **nu se scrie**.

### 4.1 Graful de dependențe

```
F0 (containment)
 └─> F1 (merge quant engine)
      ├─> F2 (data plane) ──> F3 (backtest realist + validare)
      │                        ├─> F4 (paper broker + gateway) ──> F7 (observabilitate)
      │                        │                                └─> F5 (risk engine)
      │                        └─> F6 (semnale/LLM)
      └─> F8 (guvernanță, paralel)
F9 — blocat, nu se începe
```

---

## PARTEA 5 — CE LIPSEȘTE FAȚĂ DE UN AGENT DE TRADING AUTONOM SERIOS

Sinteză, în ordinea impactului:

1. **Broker de hârtie real.** Acum „paper” = ordinul nu pleacă. Fără fill simulat, cost model, ledger și P&L, nu există feedback → nu există învățare. **Cel mai mare gol.**
2. **Un singur punct de execuție.** Există minimum trei căi de ordin, una fail-open (C1). Un agent autonom cu mai multe uși de ieșire nu e auditabil.
3. **Risk engine obligatoriu și fail-closed.** Guard-urile există dar sunt opționale și inegal aplicate.
4. **Costuri de tranzacționare realiste.** Fără fee/spread/slippage, orice rezultat de backtest e nefolosibil (principiul #17).
5. **Anti-overfitting disciplinat.** Purged CV + embargo, Monte Carlo/permutation, holdout sigilat cu buget 1.
6. **Gate-uri de promovare formale.** Fără mașina de stări din §3.4, „strategia e gata” devine o opinie.
7. **Reproductibilitate.** `run_id`, hash de config și dataset, seed, versiuni. Fără ele nu se poate depana un regres.
8. **Feature store + model registry.** Necesare pentru retraining controlat și pentru detectarea drift-ului.
9. **Strat de date serios.** Parquet + DuckDB în loc de SQLite ad-hoc; validare de calitate ca guard, nu ca log.
10. **Monitorizare de divergență paper↔backtest.** Semnalul cel mai devreme că modelul s-a rupt.
11. **Protocol de research scris.** Ipoteză → test → peer review → arhivare. Fără el, sistemul rescrie aceleași greșeli.
12. **Test negativ de securitate în CI.** Singura garanție durabilă că „paper only” rămâne adevărat după 50 de commit-uri.

---

## PARTEA 6 — CE TREBUIE SĂ FACĂ CODEX ACUM

### PASUL 1 (și singurul, până la review): FAZA 0 — CONTAINMENT

**Branch:** `codex/phase0-containment`
**Scope:** exclusiv F0.1 → F0.7. Nu atinge `trading_intelligence/`, nu începe paper broker, nu refactoriza semnale.

**Livrabile concrete:**

1. `skills/crypto_swarm/trade_executioner.py` — `execute_trade()` ridică `ExecutionForbidden("live execution not implemented; paper-only build")`. Apelul `ex.create_order(...)` de la linia ~141 **șters**.
2. `skills/crypto_swarm/exchange_connector.py` — fără construcție de clienți ccxt cu credențiale de trading; doar acces read-only prin `shared/providers/exchange/factory.py`.
3. `skills/trading_intelligence/exchanges/*` — mutat sub `legacy/` sau cu guard care aruncă la orice metodă de plasare de ordin.
4. `shared/binance_live_config.py` — `PAPER_MODE` eliminat; toate consumatoarele citesc `PlatformConfig.safety.paper_mode`.
5. `shared/patches/trading_activate.py` — nu mai scrie fișiere de config; returnează mesaj că LIVE nu e implementat.
6. `shared/execution/kill_switch.py` (nou, mic) — stare persistată, default **ARMED** dacă fișierul lipsește sau e corupt.
7. `tests/security/test_no_live_execution.py` (nou) — scanare AST:
   - niciun `import ccxt` în afara `shared/providers/exchange/`;
   - niciun apel `.create_order(` în afara adaptoarelor;
   - `place_order` cu `paper_mode=False` nu e atins de nicio cale de cod din `skills/` sau `crypto_bot/`;
   - `trading_activate` nu modifică nicio variabilă de mod.
8. `docs/CONTAINMENT_REPORT.md` — ce s-a neutralizat, unde, ce teste dovedesc.

**Criterii de acceptare:**
- `pytest tests/ -v` → toate cele 37 de teste existente rămân verzi.
- `pytest tests/security -v` → verde.
- `grep -rn "create_order" --include=*.py` → rezultate **doar** în `shared/providers/exchange/{binance,kucoin}.py`.
- Niciun fișier nou de producție în `trading_intelligence/` (acela e scope-ul F1+).

**Ce NU face Codex la pasul 1:** paper broker, execution gateway, risk engine, data plane, merge PR #2. Fiecare are faza sa.

---

## Referințe

- `PLATFORM_HANDOFF.md` — contractele stratului de platformă (Agent 1)
- `QUANT_ENGINE_HANDOFF.md` (pe branch-ul `cursor/quant-engine-b60c`) — motorul quant în draft
- `docs/OPERATOR.md` — ghid operator
- `config/default.yaml` — invariantele de siguranță
- GLOBAL AI TRADING INTELLIGENCE REPORT 2026 (sesiune de research anterioară) — cele 20 de principii, matricea de priorități de build, planul de 90 de zile
