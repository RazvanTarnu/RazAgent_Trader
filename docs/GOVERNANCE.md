# RazAgent_Trader — GOVERNANCE

**Versiune:** 1.0
**Data:** 2026-08-21
**Aplicabilitate:** toți agenții și toți colaboratorii care scriu în `RazvanTarnu/RazAgent_Trader`
**Document părinte:** [`docs/SCHEMA_AND_ROADMAP.md`](./SCHEMA_AND_ROADMAP.md)

Acest fișier definește **cine decide ce**, **cine execută ce** și **ce este interzis**.
Dacă acest document intră în conflict cu orice alt fișier din repo, acest document câștigă.

---

## 1. Cele patru reguli fundamentale

| # | Regulă |
|---|---|
| **R1** | **Perplexity = sursa de adevăr pe SCHEMA + ROADMAP.** Arhitectura, limitele între module, contractele, gate-urile și ordinea fazelor sunt definite exclusiv în `docs/SCHEMA_AND_ROADMAP.md`. |
| **R2** | **Codex = executorul.** Codex implementează, scrie testele, rulează testele și raportează. Codul de producție este scris de Codex, nu de Perplexity. |
| **R3** | **Niciunul nu sare peste celălalt.** Perplexity nu comite cod de producție. Codex nu schimbă arhitectura, nu reordonează fazele și nu extinde scope-ul unei faze din proprie inițiativă. |
| **R4** | **PAPER TRADING ONLY** până la un audit ulterior explicit. Nicio execuție reală de ordine. Toate acțiunile financiare sunt **fail-closed**. |

---

## 2. Împărțirea rolurilor

### 2.1 Perplexity — Architect / Research / Schema

**Deține:**
- `docs/SCHEMA_AND_ROADMAP.md` — schema țintă, invariantele I1–I8, contractele între module
- `docs/GOVERNANCE.md` — acest fișier
- Ordinea fazelor F0 → F9 și dependențele dintre ele
- Gate-urile de promovare a strategiilor (G1–G5)
- Definition of Done pentru fiecare fază
- Research: ce este realist de replicat, ce nu, ce dataset-uri și metode se folosesc

**Nu are voie:**
- să scrie cod de producție în `shared/`, `trading_intelligence/`, `crypto_bot/`, `skills/`
- să comită direct în `main` altceva decât documente
- să declare o fază „gata” — asta se stabilește prin teste verzi, raportate de Codex

### 2.2 Codex — Implementation / Testing

**Deține:**
- Implementarea fiecărei faze, în ordinea din roadmap
- Testele (unit, contract, securitate) și rularea lor
- Rapoartele de fază (`docs/*_REPORT.md`)
- Deciziile de nivel implementare: structuri de date interne, nume de funcții, stil, optimizări locale

**Nu are voie:**
- să schimbe schema, limitele între module sau invariantele
- să sară o fază sau să lucreze în două faze simultan
- să extindă scope-ul unei faze (dacă apare ceva necesar și neplanificat → escaladare, vezi §4)
- să scrie, să reactiveze sau să pregătească vreo cale de execuție reală de ordine
- să slăbească, să comenteze sau să ocolească un guard, un test de securitate sau un gate

---

## 3. Bucla de lucru (obligatorie pentru fiecare fază)

```
1. Perplexity  → specifică faza N în SCHEMA_AND_ROADMAP.md
                 (livrabile concrete + criterii de acceptare)
2. Codex       → branch `codex/phaseN-<nume>`
                 implementează STRICT scope-ul fazei N
                 scrie testele cerute
                 rulează: pytest tests/ -v  ȘI  pytest tests/security -v
3. Codex       → PR + docs/PHASE_N_REPORT.md
                 (ce s-a făcut, ce teste dovedesc, ce nu s-a făcut și de ce)
4. Perplexity  → review contra schemei: invariante respectate? scope respectat?
                 limitele între module intacte? niciun canal de execuție reală?
5. Merge       → doar cu CI verde + testul de securitate verde
6. Perplexity  → actualizează roadmap-ul (status, ce s-a învățat)
7. → faza N+1
```

**Nicio fază nu începe înainte ca predecesoarea ei din graful de dependențe să fie merged.**
Graful este în `docs/SCHEMA_AND_ROADMAP.md` §4.1.

---

## 4. Escaladare (când realitatea contrazice schema)

Se întâmplă. Procedura este:

1. Codex **se oprește** pe punctul respectiv. Nu improvizează arhitectură.
2. Codex deschide o secțiune `## Blockers` în raportul de fază: ce cere schema, de ce nu funcționează, ce alternative există.
3. Perplexity decide și **actualizează schema**. Schema se schimbă în document, nu în cod.
4. Codex continuă pe baza schemei actualizate.

Un blocker nu justifică niciodată: relaxarea unui guard, dezactivarea unui test, sau deschiderea unei căi de execuție reală.

---

## 5. Reguli de siguranță financiară (non-negociabile)

| # | Regulă | Consecință la încălcare |
|---|---|---|
| **S1** | Nicio execuție reală de ordine. `LiveBroker` **nu se scrie** în această versiune. | PR respins |
| **S2** | **Fail-closed**: guard lipsă, excepție, config ambiguu, date stale ⇒ `REJECTED`. Niciodată `ALLOWED` prin default. | PR respins |
| **S3** | **Un singur punct de ordin**: `execution/gateway.py`. Orice altă cale este un bug de securitate. | PR respins |
| **S4** | O singură sursă de adevăr pentru mod: `PlatformConfig.safety.paper_mode`. | PR respins |
| **S5** | LLM-ul nu emite ordine, nu setează limite de risc, nu poate crește mărimea poziției sau confidence-ul. | PR respins |
| **S6** | `tests/security/test_no_live_execution.py` este **obligatoriu verde** pe fiecare PR. Nu se marchează `skip`, nu se relaxează. | PR respins |
| **S7** | Kill-switch: dacă starea persistată lipsește sau e coruptă, pornește **ARMED** (totul blocat). | PR respins |
| **S8** | Secretele stau exclusiv în keyring. Zero secrete în YAML, în cod, în loguri, în teste. | PR respins |
| **S9** | Trecerea la capital real (Faza 9) este blocată. Se deblochează doar prin audit explicit al proprietarului repo-ului, nu prin decizia unui agent. | — |

---

## 6. Definition of Done — universal

O faza este „gata” **numai** dacă toate sunt adevărate:

- [ ] Toate livrabilele fazei din `SCHEMA_AND_ROADMAP.md` există
- [ ] `pytest tests/ -v` verde (fără teste noi marcate `skip`/`xfail` ca să treacă)
- [ ] `pytest tests/security -v` verde
- [ ] Zero cod în afara scope-ului fazei
- [ ] `docs/PHASE_N_REPORT.md` scris, inclusiv ce **nu** s-a făcut
- [ ] Review Perplexity: invariante I1–I8 și regulile S1–S9 intacte
- [ ] Pentru faze cu componență de securitate: **fixture de regresie** care face scanerul roșu la reintroducerea defectului
- [ ] Roadmap actualizat

---

## 7. Starea curentă

**Actualizat:** 2026-08-21, după review-ul post-merge al F0.

| Element | Stare |
|---|---|
| Schema + Roadmap | ✅ `docs/SCHEMA_AND_ROADMAP.md` |
| Guvernanță | ✅ acest document |
| Monitorizare orară a repo-ului | ✅ activă (vezi §8) |
| Front de lucru curent | ✅ `docs/WORK_FRONT.md` |
| **Faza 0 — Containment** | 🔴 **RESPINSĂ la review** — PR #3 merged, dar containment incomplet (`docs/F0_REVIEW.md`) |
| **Faza 0-R — Containment Remediation** | ⏳ **FRONT ACTIV — următorul pas al lui Codex** |
| Faza 1 — Merge quant engine (PR #2) | ⛔ blocată de F0-R · 10 condiții de merge documentate (P2-1…P2-10) |
| Fazele 2–8 | ⛔ blocate |
| Faza 9 — capital real | 🔒 blocată permanent până la audit |

### 7.1 Rezultatul review-ului F0 (2026-08-21)

**Verdict: RESPINS.** F0.1, F0.3, F0.4, F0.5 sunt corecte și au fost verificate independent (44 + 7 teste confirmate rulate local). F0.2 parțial, F0.6 insuficient, F0.7 incomplet.

**Defect blocant:** `skills/crypto_swarm/dust_sweeper.py` execută `POST /sapi/v1/asset/dust` — o conversie de active reală și ireversibilă pe contul Binance live, semnată HMAC cu credențiale reale, fără paper mode, fără kill-switch, fără risk engine, fără approval gate. Nu folosește `ccxt` și nu apelează `create_order`, deci testul de securitate livrat în F0 trece verde în prezența ei.

Încălcări: I1, I2, I3 · S1, S2, S3, S6, S7.

**Regulă nouă, adoptată din acest eșec — se adaugă la §6 Definition of Done:**

> Un test de securitate se validează prin faptul că **devine roșu când reintroduci defectul**, nu prin faptul că e verde. Orice fază cu componență de securitate livrează un **fixture de regresie** care reproduce defectul și pe care scanerul trebuie să îl semnaleze.

## 8. Monitorizare automată

Perplexity verifică repo-ul **orar** (task recurent activ). La fiecare verificare:

1. Determină ancora = ultimul commit pe `docs/SCHEMA_AND_ROADMAP.md`.
2. Caută avans după ancoră: commit-uri de cod pe orice branch, branch-uri `codex/*` noi, PR-uri noi sau actualizate, rapoarte `docs/PHASE_*_REPORT.md`.
3. Fără avans ⇒ nicio acțiune, nicio notificare.
4. Cu avans ⇒ citește codul nou, îl verifică contra invariantelor I1–I8 și regulilor S1–S9, rulează `pytest tests/` și scanarea `create_order`.
5. Extinde `docs/SCHEMA_AND_ROADMAP.md` cu detalierea fină a fazei următoare, actualizează §7 din acest fișier, și scrie **`docs/WORK_FRONT.md`** — frontul de lucru curent pentru Codex.
6. Notifică proprietarul repo-ului cu rezumatul și eventualele blocante.

**`docs/WORK_FRONT.md` este fișierul pe care Codex îl citește primul.** Conține: ce s-a livrat, constatări noi (numerotate în continuarea C1–C14), încălcări de invariant marcate `🔴 BLOCANT`, și lista numerotată de task-uri în ordine cu branch-ul recomandat.

Monitorizarea nu comite niciodată cod de producție și nu face merge la PR-uri. Scrie exclusiv în `docs/`.

---

Constatările critice care motivează Faza 0 (C1–C14) sunt în `docs/SCHEMA_AND_ROADMAP.md` §1.4.
Cea mai gravă: `skills/crypto_swarm/trade_executioner.py` apelează direct `create_order` pe un client de bursă real, fără verificare de paper mode, fără drawdown guard, fără approval gate.
