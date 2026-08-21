# Phase F1 — Merge & Quant Engine Consolidation Report

**Branch:** `codex/phase1-quant-consolidation`  
**Base:** `main` @ `10d90c6` (PR #4 merged by owner). F1 commits were rebased onto that merge. Codex did not merge PR #4.  
**Scope:** WORK_FRONT F1.0 and P2-1…P2-10 only. Nothing from F2+.  
**Operating mode:** paper-only, fail-closed, offline tests.

---

## 1. What was done

| # | Task | Result |
|---|---|---|
| — | PR #4 out of draft | Done. Merge left to the repo owner. |
| F1.0 | C23 compensatory quarantine tests | `tests/security/test_legacy_quarantine.py` + `tests/security/fixtures/regression_c23_unguarded_executor.py.txt`. Existence, AST-on-body (not file grep), behavioural `ExecutionForbidden`, fixture goes red. |
| P2-1 | Name collision C3 | `skills/trading_intelligence/` → `legacy/trading_intelligence_v1/`. `QUARANTINED_LEGACY_EXECUTORS` updated. Old import path tombstoned with `ImportError`. |
| P2-8 | Dependencies C11 | `numpy`, `pandas`, `pyarrow`, `duckdb`, `hypothesis` in `requirements.txt`, asserted by `tests/test_quant_dependencies.py`. |
| P2-9 | CI + branch protection S6 | `.github/workflows/ci.yml` runs `pytest tests/ -v` and `pytest tests/security -v` on every push/PR. Branch protection on `main`: required check `tests`, strict, enforce_admins, no force-push, no delete. |
| P2-2 | Mandatory `CostModel` C8 | `BacktestEngine.__init__(cost_model: CostModel, …)` — no default. `None` raises `TypeError`. Fees maker/taker, spread, slippage f(size, volatility, volume). |
| P2-3 | Next-bar entry I5 | Signal on `bars[:i+1]`; fill at `open(i+1)` + spread + slippage. Same-bar close fills are gone. |
| P2-5 | Stop-loss and time-stop | Required `stop_loss_pct > 0` and `time_stop_bars >= 1`. Engine exits on stop, time-stop, reverse signal, or adverse regime. |
| P2-4 | Leakage test I5 | Synthetic gap-up after signal close. Same-bar fill would profit; next-open fill does not. Test fails if the engine prints money on that series. |
| P2-6 | `run_id` + manifest I8 | `BacktestResult` carries `run_id`, `config_hash`, `dataset_hash`, `seed`, package versions. |
| P2-10 | Research ↔ execution boundary I1 | `tests/quant/test_research_boundary.py` forbids `ccxt`, keyring, approval, `shared.execution`, `shared.providers.exchange` inside `trading_intelligence/`. |
| P2-7 | C21 import (last) | `crypto_bot/trade_crypto_bot.py` imports `skills.crypto_swarm`. Dust tool stays unregistered. |

PR #2 (`cursor/quant-engine-b60c`) was **not** git-merged as-is. Its quant tree was brought in and then subjected to P2-1…P2-10. The owner still merges PR #2 (or this F1 PR, which supersedes it) after review.

---

## 2. Validation

```
python -m pytest tests/ -v            # 120 passed
python -m pytest tests/security -v    # 56 passed (was 45)
```

C23 proof: `test_regression_c23_unguarded_executor_is_flagged` and `test_ast_checker_does_not_accept_sibling_guard` require the AST checker to go red on an unguarded `place_market_order` even when `cancel_order` in the same file is guarded.

---

## 3. What was **not** done (and why)

| Item | Why |
|---|---|
| Merge of PR #4 | Owner-only. Codex does not merge its own PRs. |
| Git-merge of PR #2 as a PR | Conditions P2-1…P2-10 had to land first; this branch contains the consolidated result. Owner merges. |
| Paper broker / execution gateway | F4 |
| `LiveBroker` | Must not exist in this version |
| C19 (`prepare_trade` writing `D:/RazAgent_Enterprise/...`) | F4 |
| Data plane | F2 |
| Realistic walk-forward validation beyond the P2-5/P2-4 invariants | F3 |
| Unified risk engine | F5 |
| Swarm/LLM production wiring beyond the imported research cycle | F6 |
| Observability | F7 |
| Relaxing, skipping, or deleting existing tests | Forbidden |
| Architecture / phase reorder | Forbidden |

---

## 4. Blockers

1. **PR #2 should be closed or superseded** after this PR is accepted; do not merge the unfixed quant draft.
2. None else. PR #4 is merged. F2 remains blocked on F1 merge.

---

## 5. Invariants

Paper trading only. Fail-closed. One order point (platform adapters). LLM does not place orders. Secrets stay in keyring. Scanner exclusions now have compensatory tests.
