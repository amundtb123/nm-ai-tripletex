# Handoff — NM AI Accounting Agent (routing, grønt scope, guardrails)

**Sist oppdatert:** 2026-03-21 · **Teststatus (siste kjøring):** `unittest discover` over `tests/test*.py` → **133 tester OK** (kjør med prosjekt-venv om `requests` mangler på system-Python).

---

## 1. Current state

**Produksjon / drift:** `POST /solve` er **stabil**. **422-problemet** (Pydantic som avviste body før handler) er **løst** (tolerant `schemas.py`, logging av valideringsfeil). **Cloud Run deploys** fungerer. **Routing og logging** er utvidet (`planner_mode`, `planner_llm_status`, heuristikk-logg, `file_count`, osv.).

**Cloud Run — hvilken tjeneste er NM-endpointet (viktig):** Konkurranse-/NM-testing skal alltid treffe **`ai-accounting-agent`** i **`europe-west1`**, f.eks. `https://ai-accounting-agent-2wgziq3vqq-ew.a.run.app/solve`. Tjenesten **`nm-ai-tripletex`** i **`europe-north1`** er en **ekstra** Cloud Run-service som ble opprettet ved uhell / sideeffekt av annet navn — **deploy dit** eller **logger derfra** gir **misvisende** signaler (annen revisjon / ikke det NM faktisk kaller). **Deploy** og **les `gcloud logging`** for **`ai-accounting-agent`** når dere tolker NM-atferd. Etter deploy av samme image til riktig service ble f.eks. **`latestReadyRevisionName`:** `ai-accounting-agent-00019-rtg` og **HTTP 200** på `/solve`. Detaljert runbook: **`PROJECT_STATE.md` §20**.

**Grønt scope (aktivt optimalisert):** `list_employees`, `search_customer`, `create_customer`, `search_product`, `create_product`.

**Bevisst utenfor scope / skal normalt ende som noop:** `create_invoice_for_customer`, `register_payment`, prosjekt/bred billing, lønn, månedsavslutning / periodisering / reversering, og øvrige brede regnskapsoppgaver.

**Strategi (må forstås):**

1. Ikke støtte alle cases nå — **maksimere score på støttet grønt scope**, ikke åpne nye domener.
2. **Correctness først**; efficiency-bonus bare ved perfekt correctness.
3. **GET er billigere enn feil writes**; **search/reuse** ofte bedre enn aggressiv **create**.
4. Unødvendige **POST/PUT/PATCH/DELETE** og **4xx** er dyre.
5. **Search over create ved tvil** på mange grønne tasks.
6. Dårlige runs ødelegger ikke tidligere best-score per task, men endringer skal være **målrettede**, ikke brede eksperimenter.
7. **Nåværende linje:** behold **guardrails** for faktura/betaling/prosjekt/lønn/månedsavslutning; **forbedre recall** på plausible grønne prompts; la **invoice/faktura** slippe gjennom heuristikk bare når det **åpenbart er kontekst** rundt grønn customer/product/employees-oppgave.
8. **Vedlegg** skal ikke i seg selv blokkere grønn routing; **ingen dyp vedleggsforståelse** er bygget.

**Siste kjente kodepunkter (`planner_llm.py`):**

- Når `_classify_intent` gir **`invoice`** bare fordi «faktura»/«invoice» finnes, men prompten er **standalone grønn**, **ikke** `_billing_invoice_primary_task`, og har **find/list/price/staff**-signal → remappes intent til **`search` kun for heuristikk-scoring** (unngår tapt +3/+2 uten å endre planner.py sin rå intent overalt).
- **Faktura-nummer-penalty** mot customer-scores brukes **ikke** i samme **standalone + ikke billing-primary**-unntak.
- `_weak_green_recall_eligible`: **ny kunde**-gren bruker **`not _billing_invoice_primary_task`**, ikke grov blokkering på alle «invoice»-forekomster.
- Engelsk **fetch/get/retrieve/pull up + customer** = lookup; **«the register»** skal ikke trigge **create** feilaktig.
- **Attachment/boilerplate-stripping** for scoring, **uten** å svekke guardrails.
- **`file_count`** inn i heuristikk-trepasset (svak pass lavere terskel ved filer).

**Live-logger — typiske mønstre:** Grønne enkle cases (spesielt **list_employees**, customer/product) treffer ofte. **Vanskeligste** har vært **`llm_noop` / modell velger noop** på lange, svake eller faktura-nevnte prompts. **Invoice → create_customer** som feiltype er redusert via guardrails. **Fase:** Videre tuning utover siste smale justeringer er **trolig overoptimalisering** — **observer nye logger** og reager bare på **konkrete mønstre**.

---

## 2. What has been done (kort punktliste)

- **Request validation & logging** på `/solve` (valideringsfeil synlige uten stille 422).
- **Mer tolerant parsing** (`extra=ignore`, `files: null`, prompt-koercering, tester).
- **`planner_llm`:** JSON-router, **system prompt**, **structured signals** til modellen, **entity/reason**-logging.
- **Search-over-create** for kunde der det er riktig (e-post/telefon alene ≠ create uten tydelig «ny/registrer»).
- **Produkt- og ansatt-recall** (heuristikk-trepass, svak pass, synonymer, NM-tilpasninger).
- **Guardrails** mot invoice/payment/prosjekt/lønn/month-close i `_non_green_accounting_context` + `_heuristic_blocked` + LLM-avvisning av grønt når OOS.
- **Vedlegg:** recall uten dyp filforståelse; **file_count** i trepass; **strip** av ledende vedleggs-/faktura-boilerplate for scoring.
- **Fetch/retrieve/register-fikser** for engelsk customer lookup og false **create** fra «register».
- **Invoice reference vs primary task:** `_billing_invoice_primary_task`, standalone-unntak, tester (`test_planner_invoice_reference_vs_primary.py`, attachment-routing-tester).
- **Siste smale scoring:** intent-remap for scoring + faktura-nr-penalty-unntak som over (kun når standalone grønn + ikke billing-primary).

**422:** tidligere problem; **nå løst** (se også eldre notat nederst i denne filen om diagnose).

---

## 3. Recommended next steps

1. Les **`PROJECT_STATE.md`**, denne **`HANDOFF.md`**, **`docs/SESSION_WRAPUP.md`**, **`planner.py`**, **`planner_llm.py`**, **`main.py`**.
2. **Bekreft** git-status / siste commit / deploy-revisjon i ditt miljø.
3. **Les nye live-logger** før du endrer kode — se etter **konkrete** gjentakelser (prompt + `planner_llm_status` + `reason`).
4. **Endre bare** ved dokumentert mønster; **ikke** bred refaktor, **ikke** scope-utvidelse, **ikke** `workflows.py** uten svært god grunn.
5. Hvis loggene **ikke** viser noe nytt og konkret: **stopp tuning** og behold observasjon.

**Tester:** `python3 -m unittest discover -s tests -p 'test*.py'` (eller `.venv/bin/python` etter `python3 -m venv .venv && pip install requests`).

---

## Vedlegg: Diagnose 422 (historisk)

422 fra FastAPI = Pydantic avviste body **før** `solve`-handler; da mangler ofte `request_received`. Typisk: `extra=forbid`, `files: null`, ugyldig JSON. Nå håndteres dette mer tolerant + logges — se `schemas.py`, `main.py`, `tests/test_solve_validation.py`.
