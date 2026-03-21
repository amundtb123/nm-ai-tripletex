# PROJECT_STATE — AI Accounting Agent (NM i AI 2026)

> **Purpose:** Single handoff document if chat or team context is lost. Update this file when behavior or priorities change meaningfully.

> **Siste økt / ny agent:** Se også **`docs/SESSION_WRAPUP.md`** for kort wrap-up (arkitektur, filer, git, tester, neste steg). **Kort NM-handoff (422 /solve):** **`HANDOFF.md`**.

---

## 1. Project goal

Deliver a **small, deployable FastAPI service** for the competition that:

- Accepts natural-language `prompt` plus optional file uploads and **Tripletex** credentials.
- Routes to **rule-based workflows** first; **optional LLM router (Spor B)** may map **noop** / naturlige prompts til samme `Plan` → `workflows.py` (LLM genererer **ikke** rå Tripletex-kall).
- Calls the **Tripletex API v2** with **HTTP Basic Auth** (`username` = `"0"`, `password` = session token).
- Returns **`{"status": "completed"}`** on successful workflow completion; uses **400** for bad client input and **502** for upstream Tripletex errors (sanitized `detail`).
- Runs in **Docker** on **port 8080**, suitable for **Google Cloud Run**.
- **Anbefalt Python:** **3.11** (se README). **3.9** kan brukes etter eksplisitte type-justeringer på modeller (se **§13**).

---

## 2. Current architecture

| Layer | Responsibility |
|--------|----------------|
| **`main.py`** | FastAPI app: `GET /health`, `POST /solve`; middleware **`capture_solve_request_body`** + **`request_validation_error`**-logger ved **422** (før handler); **/solve-livsløp** logger `request_received` → `files_decoded` → `plan_built` (inkl. **`workflow_route`**, **`workflow_route_detail`**, **`planner_mode`**, **`planner_selected_workflow`**, **`planner_selected_entity`**, **`planner_confidence`**, **`planner_language`**, **`planner_llm_status`**, **`planner_route_detail`**, **`planner_heuristic_log`**) → **`credential_config`-sjekk** (kun når `workflow` ≠ `noop`) → `workflow_started` → `workflow_finished` \| `workflow_failed` → `request_finished` (+ `tripletex_http`); kobles med `request_id`. Mapper `WorkflowInputError` / feilkonfigurerte Tripletex-credentials → **400**, `TripletexAPIError` → 502. **Ingen** hemmeligheter i logger. |
| **`request_context.py`** | ContextVar **`solve_request_id`** (`Optional[str]`) slik `tripletex_http` får samme `request_id` som gjeldende `/solve`; union-syntaks unngås for **Python 3.9**-kompatibilitet. |
| **`schemas.py`** | Pydantic models for `/solve` body (`SolveRequestBody`, credentials, file items): **`extra="ignore"`**, **`files: null` → `[]`**, enkel **`prompt`**-coercion. |
| **`file_parser.py`** | Decode base64 uploads under `/tmp/uploads` (or `AI_AGENT_UPLOAD_ROOT`), batch dirs, basic path safety. |
| **`planner.py`** | **`build_plan_rules`**: samme som før — **eksakte delstreng-triggere** → **ord-basert fallback** (**`list_employees`** → **`create_customer`** → **`search_customer`** → **`search_product`** → **`create_product`**; se **§5** punkt 20–22) + **slot extraction**. **`build_plan`**: kjører regler først; ved **`workflow` = `noop`** kalles **`planner_llm.try_llm_plan_after_noop_with_detail`** når **LLM er aktivert** (se **§2.1**). `Plan` utvidet med **`planner_*`**-felt (se **§2.1**). **`Optional[float]`** for Pydantic på **Python 3.9**. |
| **`planner_llm.py`** | **Spor B:** OpenAI-kompatibel **`chat/completions`** med **`response_format: json_object`** → **`LLMRouterJSON`** (workflow + confidence + språk + **`entity`** + **`reason`** + slots + valgfri `extraction_summary`). Mapper til eksisterende **`Plan`** / **`workflows.py`**-input — **ingen** generering av Tripletex-paths eller HTTP. Kun workflows i **første LLM-scope** (se **§2.1**). |
| **`workflows.py`** | One function per workflow; Tripletex calls via `tripletex_json`; raises `WorkflowInputError` when input is incomplete. Includes `build_customer_update_payload` for safe customer PUT. **`workflow_create_customer`:** logger **`create_customer_reuse_rejected`** (tvetydig), **`create_customer_create_chosen`** (før **POST**). |
| **`customer_resolver.py`** | `search_customer_by_name`, **`search_customer_by_name_with_meta`** (**GET /customer**): kandidatrader utledes via **`tripletex_list`** ( **`tripletex_list_rows_from_response`** ). **`filter_exact_planned_name_matches`** / **`find_exact_customer_matches_for_create`** (**`create_customer`** + precheck‑logging inkl. **`list_payload_extract`**). **Faktura:** `resolve_customer_for_invoice` + `pick_best_customer_match_for_invoice` (fallback **`customerName`**, rangering, **`customer_resolver_invoice_*`**, **400** ved tvetydig). |
| **`product_resolver.py`** | `search_products`, `search_products_fallback`, `resolve_product_by_name_or_number`, `pick_best_product_match` for `GET /product`; `product_resolver_ambiguous` when multiple rows and heuristics fall back to first. |
| **`tripletex_list.py`** | **`tripletex_list_rows_from_response`**: felles utpakking når **`value`** er liste eller **`{ fullResultSize, values }`** — brukes av **kunde**-, **produkt**- og **`list_employees`**-flyter (**§17**). |
| **`tripletex_client.py`** | Thin `requests` session with Basic auth; `get` / `post` (optional query `params`) / **`put` (optional `params`)** / `delete`. |
| **`tripletex_request.py`** | `tripletex_json`: executes request, logs **`tripletex_http`** (inkl. `request_id` når satt, method, path, status, param keys, body keys, avkortet response preview) — **never logs session token**. |
| **`tripletex_errors.py`** | Parse Tripletex `ApiError` JSON → `TripletexAPIError` with `public_detail()` for API responses. |
| **`tripletex_credential_checks.py`** | Plassholder-heuristikker for `base_url` / `session_token` (til logging) og **`tripletex_credentials_valid_for_api`** før første Tripletex-kall — **400** med norsk `detail`, **ingen** token i logger. |

**Data flow:** `POST /solve` → validate body → optional `decode_files_to_tmp` → `build_plan(prompt)` → `run_workflow(plan, client)` → JSON logs + `{"status":"completed"}`.

### 2.1 Spor B — LLM-router (minimal, 2026-03-20)

**Strategisk skifte:** Vi går bevisst fra **regex-/frase-først** som eneste intelligens til en **lagdelt planner**: **eksakte regler** → **eksisterende regex-fallbacks** → **valgfri LLM-router** når reglene gir **`noop`** (eller når man senere utvider til eksplisitt «alltid LLM» — ikke implementert i runde 1). **Prioritet:** Hovedproblemet antas å være **prompttolkning** (naturlig/flerspråklig tekst), ikke Tripletex-kallene — derfor **minimal, robust LLM-routing** foran mer regex-lapping.

**Hvorfor:** NM-promptene er **naturlige og ofte flerspråklige**; Cloud Run-loggene viste **`workflow_finished`** med **`no_matching_workflow_trigger`** / **`noop`** til tross for **HTTP 200** — funksjonelt **ingen** Tripletex-flyt. Regex og delstrenger når ikke langt nok uten å eksplodere i antall special cases.

**Mål denne runden:** Minste **trygge** LLM-lag som **klassifiserer intent**, **velger workflow** og **trekker ut slots** som strukturert JSON — **ikke** rå Tripletex-kall. **Execution-layer** (`workflows.py`) er **uendret** i forretningslogikk.

**Historikk:** Tidligere observasjoner om **`noop`**, substring-grenser, faktura-parsing og sandbox (f.eks. **§5** punkt 20–22, **§17**–**§18**) er **fortsatt gyldig diagnose** og er **ikke** fjernet — Spor B **bygger på** den historikken i stedet for å erstatte den.

**Første LLM-scope (workflows):** `list_employees`, `search_customer`, `create_customer`, `search_product`, `create_product`. **Ikke** i scope nå: `create_invoice_for_customer`, `register_payment` (kan legges til senere når det er naturlig).

**Fallback (trygt):**

1. Regler + regex matcher → `planner_mode` **`exact_rule`** eller **`regex_fallback`** (avhengig av `workflow_route`).
2. Ingen treff fra regler → **`workflow` = `noop`** og `planner_mode` **`noop`** (før LLM).
3. Ved **`noop`** fra regler: hvis **`LLM_PLANNER_ENABLED`** og API-nøkkel er satt → **LLM-kall**; gyldig JSON + **confidence** ≥ **~0,45** og workflow ≠ **`noop`** → `planner_mode` **`llm`**, `workflow_route` **`llm`**.
4. LLM **feiler**, **lav confidence**, eller velger **`noop`** → **`plan.workflow` = `noop`**, `planner_mode` forblir **`noop`**; `planner_llm_status` settes (f.eks. `invalid_response`, `low_confidence`, `llm_noop`, `disabled`) og **`planner_route_detail`** forklarer kort (uten hemmeligheter).

**Miljø (LLM):**

| Variabel | Betydning |
|----------|-----------|
| `LLM_PLANNER_ENABLED` | `1` / `true` / `yes` / `on` for å aktivere LLM-steg etter `noop` |
| `OPENAI_API_KEY` eller `LLM_PLANNER_API_KEY` | Bearer-token til OpenAI-kompatibel endpoint |
| `LLM_PLANNER_BASE_URL` | Default `https://api.openai.com/v1` |
| `LLM_PLANNER_MODEL` | Default `gpt-4o-mini` |

**`planner_mode` (logg / `Plan`):** `exact_rule` \| `regex_fallback` \| `llm` \| **`noop`**. Sistnevnte brukes når **`workflow` = `noop`** etter regler (og etter trygt LLM-fallback som ikke endret workflow).

**Nye / utvidede `Plan`-felt:** `planner_mode`, `planner_selected_workflow`, `planner_selected_entity`, `planner_confidence`, `planner_language`, `planner_llm_status`, `planner_route_detail`. **`workflow_route`** kan være **`llm`** når planen kom fra LLM-router.

**Nye `plan_built`-felt (strukturert JSON-logg):** `planner_mode`, `planner_selected_workflow`, `planner_selected_entity`, `planner_confidence`, `planner_language`, `planner_llm_status`, `planner_route_detail` — **aldri** API-nøkler eller session tokens.

**Tester:** `tests/test_planner_llm.py` — mapping NO/EN, `noop`→LLM med mock, **heuristikk**-override når modell svarer noop, blokkert betalingsprompt, score-baserte cases, **eksakt** regel uten LLM-kall, ugyldig/lav confidence LLM → `noop` + status, `planner_mode` **`noop`** fra regler. Øvrige planner-tester (`test_planner_*`) bekrefter at **eksakte/regex**-triggere fortsatt fungerer. Kjør: `python -m unittest discover -s tests -p 'test*.py'`.

**Cloud Run — env for å aktivere LLM:** `LLM_PLANNER_ENABLED` = `1`/`true`/`yes`/`on` **og** `OPENAI_API_KEY` **eller** `LLM_PLANNER_API_KEY` satt. Valgfritt: `LLM_PLANNER_BASE_URL`, `LLM_PLANNER_MODEL`. Se også **`TESTING.md`** (LLM-seksjon).

**Verifisering i logger:** Når LLM faktisk velger et grønt workflow, skal **`plan_built`** vise **`planner_mode`:** **`llm`**, **`workflow_route`:** **`llm`**, **`planner_llm_status`:** **`ok`**, samt **`planner_confidence`** / **`planner_language`** / **`planner_route_detail`**. Se **`TESTING.md`** for `gcloud logging`-/`jq`-eksempler.

**Drift — LLM aktiv i Cloud Run; `llm_noop` som hovedproblem (2026-03-21):** LLM er **bekreftet aktiv** (API-nøkkel + `LLM_PLANNER_ENABLED`). En NM-runde ga **0/7** med **`planner_llm_status`:** **`llm_noop`** / **`detected_intent`:** **`create`**, **`has_phone`:** **true** — modellen valgte **`noop`** selv om oppgaven sannsynligvis skulle til et **grønt** workflow. **Senere** NM-forsøk **2/8 (25 %)** — **framgang**, men logger viste fortsatt **`llm_noop`** med **`detected_intent`:** **`unknown`**, **`has_email`/`has_phone`:** **true** — dvs. **ikke** infrastruktur/deploy/proxy, men **routing-konservering** i modellen.

**Justering (runde 2 — robusthet, samme grønne scope):** I **`planner_llm.py`**: (1) **Sterkere systemprompt** + lavere temperatur (`0.05`); eksplisitt at **`noop`** nesten aldri skal brukes for naturlig kunde/produkt/ansatt-tekst. (2) **`collect_router_signals`** / **`build_llm_router_user_content`**: utvidet med flere ord (f.eks. **`finne`** som søk), **score-linje** per grønt workflow sendes til modellen. (3) **Heuristisk post-processing** når modellen fortsatt svarer **`workflow`:** **`noop`**: **`heuristic_green_workflow_after_llm_noop`** beregner **score** for de fem grønne workflowene (kontekst: e-post/telefon uten produkt/ansatt → **create_customer**, `finn`+`kunde` → **search_customer**, **blokkering** av ren faktura/betalings-intent uten grønn krok). Ved **tydelig vinner** (min score, margin til nr. 2) bygges syntetisk **`LLMRouterJSON`** → **`llm_router_json_to_plan`** med **`planner_llm_status`:** **`ok_heuristic_override`** og **`planner_heuristic_log`** (kompakt årsak, **ingen** hemmeligheter). (4) **`planner_route_detail`:** inneholder **`ov=1`** ved override. **Tester:** `tests/test_planner_llm.py` — mange **heuristikk**-cases (kontakt-kort, betaling, søk kunde, produkt, ansatte, negativ).

**Kjent gjeld etter runde 2:** Ekstremt tvetydige prompts kan fortsatt ende **noop** (begge scorer lave / for lik margin). **Faktura/betaling** uten grønn krok er **med vilje** ikke heuristisk mappet til kunde/produkt (unngår feil route). **Invoice/payment**-workflows er **ikke** utvidet i `planner_llm.py`.

**Justering (runde 3 — noop-reduksjon / score, 2026-03-21):** Målet er **færre** `noop` når NM-prompten **tydelig** er innen de fem grønne workflowene.

| Område | Endring |
|--------|---------|
| **`planner.py`** | Flere **norske eksakte delstreng-triggere** (bl.a. `søk kunde`, `liste over ansatte`, `ny kunde`, `søk vare`, `legg til produkt`, …). **`list_employees`**-fallback: utvidet **entitet** (`ansatte?`, `kollegaer`, …) og **verb** (`oversikt`, `alle`, `hvem`, `who`, `gi meg`, …). |
| **`planner_llm.py`** | **Heuristikk:** `_HEURISTIC_MIN_SCORE` **2,85**, `_HEURISTIC_AMBIGUITY_GAP` **0,48**, `_HEURISTIC_SECOND_STRONG_MIN` **3,15** (færre «tvetydig»-avslag når nr. 2 ikke er like sterk). **`collect_router_signals`:** flere NO/EN-ord (bedrift, artikkel, personell, …). **`_heuristic_blocked`:** *unblock* når teksten ligner **grønt søk** (`finn/søk/…` + kunde/produkt/ansatt, eller `hvem` + jobb/ansatt) **før** harde betalings-/faktura-regexer — reduserer at «faktura» i setningen dreper scoring. **Scoring:** ekstra løft for **ansattliste** (who+employee, oversikt/alle+employee). **`try_llm_plan_after_noop_with_detail`:** kjører **heuristikk først** når modellen sier **`noop`** *eller* confidence **< 0,45**; godtar dessuten **`ok_low_confidence_llm`** når modellen velger et **grønt** workflow under terskel (unngår noop når modellen «nesten» traff). |
| **Tester** | `tests/test_planner_green_recall.py` (NO-triggere, fallback, unblock vs betaling). Oppdatert `tests/test_planner_llm.py` (lav confidence). |

**Oppdatert fallback-tolkning (LLM-steg):** Rekkefølgen er nå: heuristikk ved **noop eller lav confidence** → ev. **behold grønt LLM-valg** med `planner_llm_status` **`ok_low_confidence_llm`** → ellers **`low_confidence`** / **`llm_chose_noop`**. Punkt 4 i listen over (eldre tekst om «lav confidence») er dermed **myknet**: lav confidence alene leder **ikke** alltid til noop om heuristikk eller grønt workflow kan brukes.

**Gjenstående høyrisiko-mønstre (bevisst korte):** (1) Eksakt trigger **`ansatte`** treffer **enhver** setning som inneholder ordet «ansatte» — høy recall, kan gi **feil** `list_employees` på sjeldne setninger. (2) Blandet **opprett + søk** kunde i én prompt kan fortsatt gi **tvetydig** score. (3) Uten LLM (miljø av) og uten regex-treff → fortsatt **`noop`**.

**Kunde-path — søk vs opprett (2026-03-21, treffsikkerhet):** **`search_customer`** bruker `plan.customer_name` → `GET /customer` (teller treff). **`create_customer`** bruker samme navnefelt; **før `POST /customer`** kjører **`find_exact_customer_matches_for_create`** — **én** eksakt treff → **gjenbruk** id (**ingen POST**), **0** treff → **`POST /customer`** med **`{ "name": name }`** (e-post/tlf i plan brukes **ikke** i POST-body i dag). **Routing:** I **`planner.py`** prøves **`search_customer`-fallback nå før `create_customer`-fallback** (unngår at «finn …»-setninger med både søke- og opprettelsesord feilaktig prioriterer opprett). I **`planner_llm._score_green_workflows`** gir **e-post/telefon-boost til `create_customer` ikke** når **`mentions_find_verbs`** er sann (slik at «finn kunde … med e-post/tlf» ikke domineres av opprett). Signal **`mentions_existing_customer_cue`** (f.eks. *eksisterende*, *i systemet*) **øker `search_customer`**. Tester: **`tests/test_planner_customer_disambiguation.py`**.

**Produkt/ansatte — NM‑noop (2026-03-21):** Logger viste **noop** på tydelige oppgaver (pris på vare, lager/sjekk, **«opprett et nytt produkt»**, **«hvem er de ansatte»**). **Eksakte triggere** i **`planner.py`:** bl.a. `hva er prisen`, `pris på`, `på lager`, `sjekk om vi har`, `opprett et nytt produkt`, `hvem er de ansatte`, `ansatte i bedriften`. **`planner_llm`:** signal **`mentions_price_or_stock_lookup`** (pris/kost/lager/inventory/sjekk …), **`mentions_staff_in_company`**, scoring‑løft for **`search_product`** / **`create_product`** / **`list_employees`**; **`sjekk`** inkludert i `mentions_find_verbs`; systemprompt presiserer at pris/lager/ansatt/produkt **ikke** er «utenfor scope». Tester: **`tests/test_planner_nm_green_recall.py`**.

**LLM-router — beslutningsprompt + strukturert input (2026-03-21, runde 4):** **`_SYSTEM_PROMPT`** er strammet til en **beslutningsrekkefølge** (entitet → lookup vs create → **search ved tvil**), **noop** som siste utvei utenfor kunde/produkt/ansatt, **kontrasteksempler** (finn vs registrer, pris/lager → `search_product`, betaling → `noop`), og eksplisitte regler om at **kontaktinfo/pris/lager alene ikke** betyr `create_*`. **`build_llm_router_user_content`** sender nå **maskinlesbar** blokk (`entity_signals`, `action_signals`, `identifying_details`, `heuristic_ranking` som *reference only*) i stedet for flat liste + «MUST»-tekst. **`LLMRouterJSON`** utvidet med **`entity`** (`customer` \| `product` \| `employees` \| `unknown`) og kort **`reason`** (5–12 ord); **`planner_route_detail`** logger `entity=` og `reason=` for **Cloud Logging**. Heuristikk og prompt overlapper mindre: modellen skal følge systemreglene, ikke blindt speile topp-score. Tester: utvidet **`tests/test_planner_llm.py`** (struktur, pris/lager-flagg, entity i `planner_route_detail`).

**Kjent gjeld / ikke støttet ennå:** LLM scope dekker **ikke** faktura/betaling; **invoice/payment**-workflows er **ikke** utvidet i `planner_llm.py`. **Naturlige** faktura-/betalingsprompts som **ikke** treffer regex kan fortsatt bli **`noop`** når LLM ikke er aktivert eller feiler. **Ingen** bred ny forretningslogikk i `workflows.py` i denne runden.

---

## 3. Implemented features

### HTTP

- **`GET /health`** → `{"ok": true}`
- **`POST /solve`**  
  - Body: `prompt`, optional `files[]` (`filename`, `content_base64`), `tripletex_credentials` (`base_url`, `session_token`)

### Cross-cutting

- Structured logging (`log_structured` + `tripletex_http` JSON lines on stdout)
- Pydantic request validation: **`extra="ignore"`** on `/solve` models (unknown JSON keys tolerated); **`files: null`** coerced to **`[]`**; **`prompt`** kan være JSON-tall/bool og coerces til streng. **422 før handler:** FastAPI **`RequestValidationError`** → strukturert logglinje **`request_validation_error`** med **`path`**, **`validation_errors`**, **`raw_body`** (lengde + avkortet preview), **`request_headers`** (sensitive headers redacted). Middleware **`capture_solve_request_body`** leser **`POST /solve`**-body én gang slik at preview finnes ved valideringsfeil.
- File uploads to **`/tmp/uploads/batch_*`** (umask-style dirs `0700`; override with **`AI_AGENT_UPLOAD_ROOT`**)
- Planner **`Plan`**: `workflow`, `target_entity`, `name`, `email`, `phone`, `customer_name`, `product_name`, `product_number`, `product_price`, `invoice_autocreate_product`, `payment_invoice_number`, `payment_amount`, `payment_date`, `notes`, `hints`, `detected_intent`, `raw_prompt`, **`workflow_route`** / **`workflow_route_detail`**, **`planner_mode`** (`exact_rule` \| `regex_fallback` \| `llm` \| `noop`), **`planner_selected_workflow`**, **`planner_selected_entity`**, **`planner_confidence`**, **`planner_language`**, **`planner_llm_status`**, **`planner_route_detail`**, **`planner_heuristic_log`**

### Workflows (see `planner.py` trigger order; first match wins)

| Workflow | Triggers (examples) | Tripletex (summary) |
|----------|---------------------|----------------------|
| `list_employees` | Strenge delstrenger: `list employees`, `find employees`, `show employees`, `get employees`, `ansatte`; **etter** det: ord-basert fallback (verb + entitet, se **§5** punkt 20) | `GET /employee` |
| `search_invoice` | `search invoice`, `søk faktura`, `finn faktura`, … | `GET /invoice` with date window + `invoiceNumber` or `customerId` |
| `register_payment` **(implemented)** | `register payment`, `pay invoice`, `registrer betaling`, `betal faktura` | `GET /invoice` (fakturanummer, ev. kunde) → nøyaktig **ett** treff → **`PUT /invoice/{id}/:payment`** med `paymentDate`, `paymentTypeId`, `paidAmount`; **400** hvis flere treff uten `kunde:`, manglende beløp/fakturanummer, eller manglende `TRIPLETEX_DEFAULT_PAYMENT_TYPE_ID` |
| `create_invoice_for_customer` | `create invoice`, `opprett faktura`, … | Resolve customer → ev. **produkt** (`resolve_product_by_name_or_number` / fallback) eller **POST /product** når «opprett produkt hvis mangler» + navn → `POST /invoice` med ordrelinje som har **`product: { id }`** når produkt finnes/opprettes; ellers tidligere fri linje (kun hvis ingen produktnavn/-nummer i plan) |
| `search_customer` | Strenge delstrenger: `find customer`, `search customer`, …; **etter** fallbacks: ord-basert (**§5** punkt 22) | `GET /customer` (count matches) |
| `update_customer` | `update customer`, `oppdater kunde`, … | `GET /customer/{id}` → `build_customer_update_payload` → `PUT /customer/{id}` (kun `id`, `version`, og felt som faktisk endres) |
| `search_product` **(implemented)** | Strenge delstrenger + ord-basert fallback (**§5** punkt 22) | `GET /product` med `name` og/eller `productNumber`; **fallback** ved 0 treff (begge → nummer → navn); match-count; **`pick_best_product_match`** for tvetydig-logg (`product_resolver_ambiguous`) |
| `create_product` | Strenge delstrenger + ord-basert fallback (**§5** punkt 22) | `POST /product`: `name`; **`number`** fra planner (parsed varenummer) **eller** generert suffiks; valgfri **`priceExcludingVatCurrency`** når pris er parsert (`… kr` / `… nok`) |
| `create_customer` | Strenge delstrenger: `create customer`, `opprett kunde`, …; **etter** `list_employees`-fallback: ord-basert fallback (verb + kunde-entitet + meningsfull navnetekst eller etikett, se **§5** punkt 21) | **Search-before-create:** `GET /customer` (planlagt navn) → ved **0** eksakt normaliserte treff: **`POST /customer`**; ved **1** treff: **gjenbruk** eksisterende id (logger **`create_customer_existing_match_*`**); ved **>1** eksakt treff: **400** tvetydig |
| `noop` | (no trigger matched) | No API call; still returns `completed` if no error |

### `register_payment` — oppsummering (vedlikehold)

1. **Implementert workflow:** `register_payment` står i tabellen over som **(implemented)**; detaljer om utløsere, `Plan`-felt og Tripletex-kall i **§4** og **§12**.
2. **Nøkkelantakelser:** Beløp kommer **kun** fra bruker-prompt (ingen auto-restbeløp fra faktura-GET); **én** registrering per kall (ingen delbetaling/KID/bankavstemming i flyten); **`paymentDate`** = **i dag** når ikke oppgitt i tekst; **`paymentTypeId`** settes **utelukkende** via **`TRIPLETEX_DEFAULT_PAYMENT_TYPE_ID`** (ingen gjetting fra prompt).
3. **Hovedusikkerhet (live API):** Faktura **synlig** i `GET /invoice` (filter/status); om **`TRIPLETEX_DEFAULT_PAYMENT_TYPE_ID`** er riktig **PaymentType** for kontoen; behov for **`paidAmountCurrency`** ved annen valuta; oppførsel når faktura er **allerede betalt / låst** (typisk **502**); **betalingsdato** vs regnskapsføring i Tripletex.
4. **Anbefalt neste steg (etter `register_payment`):** **Live-test og herde betaling** (`paidAmountCurrency`, delbetaling, tydeligere feil ved lukket faktura), deretter **herd faktura/produkt** som i **§8–9**.

### Environment knobs (invoice)

- **`TRIPLETEX_DEFAULT_VAT_TYPE_ID`** — default `3` (must be validated per company)
- **`TRIPLETEX_INVOICE_LINE_AMOUNT`** — default line amount if no `… kr` / `… nok` in prompt

### Environment knobs (payment)

- **`TRIPLETEX_DEFAULT_PAYMENT_TYPE_ID`** — **påkrevd** for `register_payment` (Tripletex **PaymentType**-id for innbetaling; **ikke** portabel mellom leietakere — må settes eksplisitt)

---

## 4. Tripletex integration details

- **Base URL:** From request (e.g. `https://api.tripletex.io/v2`); paths are relative (`/customer`, `/invoice`, …).
- **Auth:** `requests` Session with `auth=("0", session_token)`.
- **Errors:** Non-2xx responses parsed when body matches `ApiError`; surfaced as `TripletexAPIError` → HTTP 502 with `public_detail()`.
- **Logging:** Each call logs one JSON line with `event: tripletex_http`; query **keys** logged, not sensitive values; **never** log `session_token`.
- **`POST /invoice`:** Query `sendToCustomer=false` (string `"false"` in query params as implemented) to avoid auto-send; verify in target environment if behavior differs.
- **Customer resolution:** Navnebasert **`GET /customer`** + heuristikk. **`create_invoice_for_customer`** bruker **`resolve_customer_for_invoice`** (ikke `resolve_customer_by_name`): ved **0 treff** på fullt navn forsøkes **tokens** (lengste ord først) som **`customerName`**; blant treff velges **beste** etter normalisert **eksakt** / **prefiks** / **delstreng**; **flere like scorer** med **forskjellige** visningsnavn → **400** med tydelig norsk tekst. Øvrige workflows uendret (**`pick_best_customer_match`**). Ingen orgnr/kundenummer ennå.
- **`search_product` (implemented):** `GET /product` via **`product_resolver`**. **Produktoppløsning / fallback** (samme som i faktura): når både navn og varenummer er satt → søk med **begge**; ved **0 treff** → **kun `productNumber`**; deretter **kun `name`** (`search_products_fallback` / `workflow_search_product`).
- **`create_invoice_for_customer` — produktstøttet linje (implemented):**
  - Ordrelinje kan inkludere **`product: { id }`** når planner har `product_name` og/eller `product_number` (etiketter eller svak «produkt»/«vare»-split i halen).
  - **Valgfri produktoppretting** når prompt tydelig ber om det (`invoice_autocreate_product`): fraser *«opprett produkt hvis mangler»*, *«opprett vare hvis mangler»*, *«create product if missing»* — krever **produktnavn**; ellers `POST /product` via felles `_post_product_return_row`.
  - **Streng 400:** Er produkt **ment** (navn/nummer i plan) men **finnes ikke** og **ingen** autocreate-frase → **`WorkflowInputError`** (ikke stille fallback til fri linje).
  - Uten produktfelt i plan: **én fri ordrelinje** som før (`TRIPLETEX_INVOICE_LINE_AMOUNT` / prompt-beløp).
- **Konservativ prisrekkefølge på produktlinje:** beløp i prompt (`… kr`/`… nok`) → `plan.product_price` → produktets **`priceExcludingVatCurrency`** (katalog) → **`TRIPLETEX_INVOICE_LINE_AMOUNT`**.
- **Gjenstående usikkerhet (faktura/produkt):** korrekt **`TRIPLETEX_DEFAULT_VAT_TYPE_ID`** per selskap; faktisk effekt av **`sendToCustomer=false`**; om Tripletex krever **ekstra felt** på ordrelinje med produkt (enhet, lager, valuta, …); og **POST /product**-påkrevde felt på enkelte konti — se **§6** og kodekommentarer i `workflow_create_invoice_for_customer`.
- **`register_payment` (implemented):** Fra prompt hentes **fakturanummer** (mønster / `fakturanummer:`), **beløp** (`… kr` / `beløp:`), **betalingsdato** (`dato:` / `betalingsdato:` som `YYYY-MM-DD` eller `DD.MM.YYYY`, ellers **i dag**), og valgfri **kunde** (`kunde:` eller tekst før «faktura …»). `GET /invoice` med ~730-dagers vindu og `invoiceNumber` (+ valgfri `customerId`). Ved **>1** treff uten entydig kunde → **400**. **`PUT /invoice/{id}/:payment`** (OpenAPI) med query `paymentDate`, `paymentTypeId`, `paidAmount`; **`paidAmountCurrency`** sendes **ikke** (kun relevant ved avvikende valuta iflg. OpenAPI). **`TripletexClient.put`** støtter nå **`params`** slik query-parametre faktisk sendes.
- **Antakelser ved betaling:** Beløpet er eksplisitt i prompt (ingen automatisk «restbeløp» fra faktura-GET); én full betaling per kall; ingen bank/avstemming.

---

## 5. Important practical findings

1. **`GET /invoice` requires `invoiceDateFrom` and `invoiceDateTo`** (required in OpenAPI). Implementation uses a **~730-day** window ending “tomorrow” (exclusive end semantics per API docs).
2. **Invoice search** filters by **`invoiceNumber`** and/or **`customerId`** (after resolving customer by name when needed). Pure digit tail after trigger can be interpreted as invoice number.
3. **Invoice create** uses **`orders: [{ orderLines: [...] }]`**, `isPrioritizeAmountsIncludingVat: false`, **`unitPriceExcludingVatCurrency`**, and **`vatType: { id }`**. Company/account settings (VAT-inclusive vs exclusive) must stay consistent or Tripletex returns validation errors (see developer FAQ on invoice/order).
4. **`TRIPLETEX_DEFAULT_VAT_TYPE_ID`** must be verified against the **company’s VAT types** (IDs are not portable across tenants).
5. **`update_customer` sender ikke lenger hele GET-objektet på PUT.** Etter `GET /customer/{id}` brukes **`build_customer_update_payload`**, som kun legger inn **`id`**, **`version`**, og **eksplisitt planlagte skrivebare felt** (i dag fra planner: `name`, `email`, `phoneNumber`). Dette **reduserer risiko** for Tripletex-valideringsfeil utløst av skrivebeskyttede eller irrelevante felter fra full respons. **`postalAddress`** (og andre nestede adresseobjekter) er **med vilje ikke** med før vi bygger dem eksplisitt. **Åpent:** om Tripletex **bevarer** felt som utelates i PUT eller om de kan **nullstilles** — se **§11**.
6. **Missing customer** for flows that require resolution → **`WorkflowInputError`** → **400** with a **Norwegian** message (by design).
7. **`create_product`** bruker **eksplisitt varenummer** fra teksten når `varenummer:` / `kode:` / `nr:` (m.fl.) finnes; ellers genereres et **unikt `number`** fra navnet + kort suffiks. **Pris** hentes med enkel `… kr` / `… nok`-regex og sendes som `priceExcludingVatCurrency` når den finnes.
8. **`search_product`** er **implementert**; teller treff via `GET /product` med **fallback** (se §4). Ved flere rader logger **`product_resolver_ambiguous`** (samme mønster som kunde).
9. **`create_invoice_for_customer`** kan sette **`product: { id }`** på ordrelinjen når planner har `product_name` / `product_number` (fra etiketter `kunde:`/`produkt:` eller svak splitting på «produkt»/«vare» i halen). Mangler produktet og brukeren **har** ikke bedt om «opprett produkt hvis mangler» → **400**. Beløp: **prompt `… kr` > `product_price` > katalog `priceExcludingVatCurrency` >** `TRIPLETEX_INVOICE_LINE_AMOUNT`.
10. **`register_payment`** krever **TRIPLETEX_DEFAULT_PAYMENT_TYPE_ID** og **beløp** i klienten; **ingen gjetting** av konto, KID eller delbetalinger. **Flere** fakturaer med samme synlige nummer → **400** med oppfordring til `kunde:`. Faktura må finnes i **`GET /invoice`**-resultatet (OpenAPI: «charged outgoing» — **usikkerhet** for kreditert/eldre linjer).
11. **Lokal sandbox-test:** Ved feil **`base_url`** (feil vert, mangler **`/v2`**, `http` i stedet for `https`) og/eller ugyldig / plassholder-**`session_token`** kan Tripletex eller et mellomled returnere **XML `AccessDenied`** (eller annet ikke-JSON) i stedet for normal **`ApiError`**-JSON. Dette er ofte **konfigurasjon**, ikke workflow-logikk. Appen avviser nå åpenbare plassholdere og URL-feil med **400** (`workflow_failed` med **`failure_kind`**: **`credential_config`**) før HTTP-kall — se **§14**.
12. **`list_employees` mot NM-sandbox (bekreftet):** Lokal **ende-til-ende**-kjøring er **verifisert** for denne flyten (**HTTP Basic** + korrekt **`base_url`**, planner-routing, og **Tripletex**-tilkobling). Logger viste **`workflow` = `list_employees`**, **`tripletex_http`** for **GET `/employee`** med **HTTP 200**, og **`request_finished`** med **HTTP 200**. **Oppfølging (liten observabilitetssak, ikke blokker):** **`workflow_finished`** logget **`employee_count`** som **`"0"`** selv om **`response_preview`** på **`tripletex_http`** indikerte minst én ansatt — tolkes som mulig avvik i **utpakking/telling** av responsstruktur, ikke som tegn på feil nettverkskall.
13. **`create_customer` mot NM-sandbox (historisk bekreftet + duplikater):** Gjentatte lokale sandbox-testkjøringer med samme visningsnavn (**f.eks. «Acme AS»**) har skapt **flere** kunderader — **kjent test-artefakt**. **`create_customer`** gjør nå **forhåndssøk** og **gjenbruk** ved **eksakt** normalisert treff før **`POST /customer`** (**§5** punkt 18). Tidligere ble flyten verifisert med **HTTP 201** på **POST** når kunden var ny.
14. **`create_product` mot NM-sandbox (bekreftet):** **`create_product`** er **ende-til-ende verifisert** mot sandbox. Logger bekreftet **`workflow` = `create_product`**, **`tripletex_http`** for **`POST /product`** med **HTTP 201**, **`request_finished`** **`http_status` 200**, og **`POST /solve`** **HTTP 200** + **`{"status":"completed"}`**. **Praktisk observasjon (ikke blokker):** Prompten *«opprett produkt Kaffe varenummer 1001 pris 59 kr»* ga et opprettet produkt der **navnet tilsynelatende fortsatt inneholdt teksten «varenummer 1001»** (ikke et rent *«Kaffe»*-navn) — tyder på at **uttrekk av `product_name` vs. `product_number`** i **`planner.py`** kan **forfinnes senere**; **ikke** kritisk nå — se **§13**.
15. **`search_product` mot NM-sandbox (bekreftet):** **`search_product`** er **ende-til-ende verifisert**. Logger bekreftet **`workflow` = `search_product`**, **`tripletex_http`** for **GET `/product`** med **HTTP 200**, **`request_finished`** **`http_status` 200**, og **`POST /solve`** **HTTP 200** + **`{"status":"completed"}`**. **Oppfølging (liten observabilitet/telling, ikke blokker):** **`workflow_finished`** logget **`product_match_count`** som **`"0"`** mens **`tripletex_http`** **`response_preview`** viste **`fullResultSize=1`** — tolkes som avvik i **telling/serialisering** i workflow-logg, ikke som feilet API-kall — se **§13**.
16. **`create_invoice_for_customer` — lokal sandbox-øvelse (delvis):** Flyten er **kjørt** mot sandbox med tre prompt-varianter. **(a)** *«opprett faktura for kunde Acme AS produkt Kaffe»* ga **feil kundenavn** i planner: **«for kunde Acme AS»** (for naiv stripping av «kunde»-ledd). **(b)** *«opprett faktura kunde: Acme AS produkt Kaffe»* ga **«Acme AS produkt Kaffe»** som kunde — **`kunde:`**-verdien tas som **`[^\\n,]+`**, så **mangler komma** / **`produkt:`** før **produkt**-ledd skviser inn produkttekst i **`customer_name`**. **(c)** *«opprett faktura kunde: Acme AS, produkt: Kaffe 500 kr»* ga **korrekt parsing** av kunde **«Acme AS»**, men **`POST /solve`** endte i **`WorkflowInputError`** / **400** med *«Fant ingen kunde som matcher «Acme AS»»* — ekte **kundeoppslagsfeil** fra resolver/Tripletex-søk, ikke auth. **Konklusjon:** **Ruting** til **`create_invoice_for_customer`**, **sandbox auth** og **HTTP**-oppkobling fungerer; **fakturaflyt** trenger **sterkere planner** (kunde/produkt-separasjon) og/eller **bedre kundeoppløsning** mot **kjente** sandbox-navn. Lokal fil **`examples/local.solve_create_invoice_for_customer.json`** er oppdatert til **variant (c)** — se **§13**. **Oppdatert (punkt 17):** Delvis søk *«finn kunde Acme»* viser **data i sandbox** — **manglende treff** på eksakt **«Acme AS»** i faktura er da sannsynlig **resolver/adferd**, ikke **tom** leietaker.
17. **`search_customer` — del-søk «Acme» (sandbox, oppdatert observert):** Lokal kjøring med *«finn kunde Acme»* ga **`GET /customer`** **200** med **`fullResultSize=4`** i preview, men **`customer_match_count`** **`"0"`**. **Årsak (rettet 2026-03-19):** Utdatamodellen var **`value.values`** (liste), ikke en flat liste under **`value`** — klientkoden tok ut feil nivå og fikk **tom** kandidatliste. **Etter retting** skal **`search_customer_by_name`** speile **`fullResultSize`** i antall rader (innen **`count`**).
18. **`create_customer` — duplikatvern / search-before-create (2026-03-19):** Flyten bruker **search-before-create**: før **`POST /customer`** kjøres **`GET /customer`** med planlagt navn; rader der **`name`** / **`displayName`** matcher **eksakt** (normalisert små bokstaver + felles whitespace) telles. **0** treff → **opprett** som før; **1** treff → **returner eksisterende `customer_id`**, logger **`create_customer_existing_match_found`** og **`create_customer_existing_match_reused`**, **`workflow_finished`** inkl. **`customer_reused`**: **`true`** (ingen ny **POST**). **>1** treff med samme eksakte navn → **400** + **`create_customer_existing_match_ambiguous`**. **Begrensning:** Eksisterende kunde som **ikke** returneres av Tripletex for dette søket kan fortsatt gi nye rader; dette er **inkrementelt** vidd, ikke full deduplisering.
    - **Mockede tester:** `tests/test_create_customer_reuse.py` (`unittest` + `unittest.mock`) dekker gjenbruksatferd: **eksakt eksisterende treff** → **gjenbruk** og **ingen** `tripletex_json`-kall for **`POST /customer`**; **ingen treff** → **opprett ny** kunde via **`POST /customer`** med **`customer_reused`**: **`false`**. **Begge** scenarier **grønn** i testkjøring.
    - **Live-verifikasjon (Cursor/agent-miljø):** `POST /solve` med `examples/solve_create_customer.json` mot lokal Uvicorn ga **HTTP 502** med Tripletex/mellomledd **403** (HTML-feilside) — **ikke konklusiv** for om gjenbruksstien fungerte i akkurat den økta. **Lokal sandbox-verifikasjon** av reuse med gyldig token og kundenavn som allerede finnes **gjenstår**.
    - **Lokal to-terminal test (Uvicorn + `curl`, 2026-03-19, innfanget stdout):** Samme mislykkede mønster — se **§13** *«Lokal to-terminal `create_customer` (2026-03-19)»*. Kort: **502** / upstream **403** på **`POST /customer`**, **ingen** reuse-logger, **POST** ble **ikke** unngått.
    - **Vellykket live reuse** (forventet i stdout): **`tripletex_http`** med **GET** for kundesøk (ikke **`POST /customer`**), **`create_customer_existing_match_found`**, **`create_customer_existing_match_reused`**, **`workflow_finished`** med **`customer_reused`** = **`true`**.
    - **Lokal sandbox (2026-03-19, observerte før fiks):** **`GET /customer`** deretter **`POST /customer`**, **`customer_reused`**: **`false`**. **Delvis forklart** av **JSON‑utpakingsfeil** ( **`api_candidate_count`** = **0** til tross for **`fullResultSize`**) — se siste understrek nedenfor. **Når utpakking er riktig:** **`false`** betyr fortsatt **`combined_exact_match_row_count`** ≠ **1** (navn/displayName matcher ikke planlagt streng etter normalisering).
    - **Diagnostikk-logging (2026-03-19):** Ved **`create_customer`** precheck logges **`create_customer_precheck_search_result`** (`api_candidate_count`, **`list_payload_extract`** (må være **`unwrapped_object_values`** eller **`unwrapped_list`** ved normale Tripletex-svar), **`any_exact_*`**, **`combined_exact_match_row_count`**, m.m. — **ingen** hemmeligheter). Øvrige hendelser som før (**`create_customer_exact_match_found`**, **`create_customer_displayname_match_found`**, **`create_customer_reuse_rejected`**, **`create_customer_create_chosen`**).
    - **Live lokal motsigelse funnet (2026-03-19):** **`tripletex_http`** for **`GET /customer`** viste **200** + **`fullResultSize=5`**, mens **`create_customer_precheck_search_result`** hadde **`api_candidate_count`** = **0** og **`reason`** **`no_api_rows`** — **feil**: kandidater ble ikke tatt ut av JSON (**`value.values`**). **Rettet** i **`customer_resolver._customer_rows_from_list_response`**. **`create_customer` gjenbruk** må **bekreftes på nytt** i sandbox etter fiks (forvent **`api_candidate_count`** ≈ antall rader som returnedes, og **`no_api_rows`** kun når **`fullResultSize`**/`values` faktisk er tomme).
19. **NM / Cloud Run — `noop` på kort prompt (2026-03-20, observert i logger):** Et kall fra **NM** til **`POST /solve`** traff tjenesten, men **`plan_built`** viste **`detected_intent`**: **`unknown`**, **`workflow`**: **`noop`**, og **`workflow_finished`** med **`reason`**: **`no_matching_workflow_trigger`** (jf. **`workflows.workflow_noop`**), **`request_finished`** **`outcome`**: **`completed`**, **`http_status`**: **200**. **`prompt_length`** var **14**. **Årsak:** **`noop`** oppstår når **ingen** trigger i **`_WORKFLOW_RULES`** matcher — da kjøres **ingen** Tripletex-workflow; svaret blir fortsatt **HTTP 200** + **`{"status":"completed"}`**, som **ikke** tilfredsstiller NM sine **oppgave-sjekker** → forklarer observert **0/7** score og **5/5 checks failed** når sannsynlig prompt var f.eks. **`find employees`** eller **`show employees`** (**14** tegn hver), som **ikke** inneholdt **`list employees`** eller **`ansatte`**. **`detected_intent`**: **`unknown`** er **ekstra** (engelsk **`find`** står ikke i **`_INTENT_RULES`** for intent) og er **ikke** selve årsaken til **`noop`**. **Smal lavrisiko-fiks (kun `list_employees`):** utvid triggerlisten med **`find employees`**, **`show employees`**, **`get employees`** (merk: **`get employees`** er **13** tegn; **`find`** / **`show employees`** er **14**). **Ikke** endret: invoice/payment-regler, rekkefølge på øvrige workflows. **Valgfri oppfølging:** legg **`find`** inn under **`search`** i **`_INTENT_RULES`** for renere **`detected_intent`** ved engelske «find …»-prompts (kun observabilitet).
20. **NM / Cloud Run — `noop` på lang prompt + smal `list_employees`-utvidelse (2026-03-20):** **Observert:** **`POST /solve`** **200**, **`prompt_length`** **178**, **`workflow`**: **`noop`**, **`workflow_finished`** **`reason`**: **`no_matching_workflow_trigger`**, **`request_finished`** **`completed`** → **0/8** på siste task — funksjonelt **ingen** Tripletex-workflow. **Årsak:** eksakt **frase-matching** krever sammenhengende delstreng; naturlige setninger (**«list all employees …»**, **«show me all employees»**) gir ofte **ikke** treff. **Forbedring (smal, lav risiko, invoice/payment urørt):** (1) **Behold** alle eksisterende **`_WORKFLOW_RULES`**-triggere **først**. (2) **Deretter** ord-basert fallback **kun for `list_employees`**: **`_list_employees_fallback_tokens`** krever **både** ett **entitetsord** (`employees?`, `ansatte`, `medarbeider`/`medarbeidere`) **og** ett **verb** (`list`, `find`, `show`, `get`, `display`, `retrieve`, `fetch`, `vis`, `finn`, `hent`) med **regex-ordgrenser** — ikke løs substring-støy. **Logging (utvidet, ingen hemmeligheter):** **`Plan`** har **`workflow_route`** (`exact` \| `fallback` \| **`None`**) og **`workflow_route_detail`** (eksakt trigger-streng **eller** `verb=…|entity=…`). **`plan_built`** i **`main.py`** logger **`workflow_route`** og **`workflow_route_detail`** sammen med eksisterende felt; **`hints`** inkluderer samme metadata. **`request_received` / `workflow_started` / `workflow_finished` / `request_finished` / `tripletex_http`** er **uendret** i hensikt. **Tester lagt til** i **`tests/test_planner_list_employees_synonyms.py`:** eksakt vs. fallback vs. **noop**-negativ; lang engelsk setning; norsk **medarbeidere**+**vis** uten eksakt delstreng. **Kjøring:** `python -m unittest discover -s tests -v`. **Miljø fortsatt blokkert (uendret):** **§16** — faktura uten bankkonto, betaling uten faktura/type. **Kjent planner-gjeld (punkt 20, tidspunkt):** *«liste produkter»* uten filter; *«finn kunde»* uten navn; faktura uten **`kunde:`**; **ingen** ord-basert fallback ennå for **`search_customer`** / **`search_product`** / **`create_product`** (egen runde). **`create_customer`**-fallback kom i **§5** punkt 21. **Eksempler som skal dekkes av `list_employees` nå:** *Please list all employees…*, *Can you show me all employees?*, *Find all employees…*, korte *Show/Get employees*, *ansatte* / strenger med **vis**+**medarbeidere** når ikke annet eksakt treffer.

21. **NM / Cloud Run — `noop` med `detected_intent` «create» og `has_email` (2026-03-20, observert i logger):** **`request_finished`** **200** **`completed`**, men **`workflow_finished`** **`reason`**: **`no_matching_workflow_trigger`** (funksjonelt **`noop`**). **`detected_intent`**: **`create`** (treffer f.eks. «opprett», «create», «ny» i tekst) og **`has_email`**: **true** — **tolkes** som at NM-prompten ligner **naturlig opprett kunde** (lang setning, e-post i tekst) uten sammenhengende **`create customer`** / **`opprett kunde`**. **Smal lavrisiko-fiks (invoice/payment urørt):** etter strenge triggere og **`list_employees`**-fallback prøver **`_create_customer_fallback_tokens`**: **verb** (`create`, `add`, `register`, `new`, `opprett`) **og** **entitet** (`customer`/`customers`/`client`/`clients`/`kunde`/`kunder`) med **ordgrenser**; **pluss** enten etikett **`kunde:`**/**`customer:`**/**`name:`**/**`navn:`** **eller** **meningsfull hale** etter siste entitetsord (minst **to** tegn med `\w`, ikke bare punktum). **`workflow_route`** / **`workflow_route_detail`** uendret i format (`fallback` + `verb=…|entity=…`). **Tester:** **`tests/test_planner_create_customer_fallback.py`** (eksakt uendret, naturlig engelsk, `client`, etikett, **noop** uten verb / uten navn). **Oppdatert i punkt 22:** tilsvarende fallback for **`search_*`** / **`create_product`**.

22. **NM / Cloud Run — `noop` med `prompt_length` 132, proxy `base_url` (2026-03-20, observert i logger):** **`request_id`** (f.eks. **`7d4e4fcb3436`**) — **`POST /solve`** **200** **`completed`**, **`tripletex_base_url`** er **proxy-URL** (ikke plassholder; **ikke** avvist av **`credential_config`**), men **`detected_intent`**: **`unknown`**, **`workflow`**: **`noop`**, **`workflow_finished`** **`reason`**: **`no_matching_workflow_trigger`**. **Konklusjon:** feilen er **routing** før Tripletex, ikke deploy eller autentisering. **Smal utvidelse (lav risiko, invoice/payment urørt):** ord-basert fallback for **`search_customer`**, **`search_product`**, **`create_product`** i tillegg til eksisterende **`list_employees`** / **`create_customer`**: **søkeverber** `search|find|finn|søk|lookup|locate|list` + kunde-/produkt-entitet; **opprettelsesverber** `create|add|register|new|opprett` + produkt-entitet for **`create_product`**; **krever** navn-/søke-signal (etikett, meningsfull hale etter entitet, eller **varenummer** for **`search_product`**). **Rekkefølge:** `list_employees` → `create_customer` → `search_customer` → `search_product` → `create_product` → **`noop`**. **Logging:** **`workflow_route`** / **`workflow_route_detail`** som før. **Tester:** **`tests/test_planner_search_product_fallback.py`**. **Kjent gjeld:** **`update_customer`** uten fallback; tvetydige setninger med **både** kunde og produkt; prompts med kun e-post uten navn i tekst → fortsatt **ikke** `create_customer`-fallback (punkt 21).

---

## 6. Known risks / technical debt

| Risk | Notes |
|------|--------|
| **Customer PUT semantics** | Minimal body (`id`, `version`, planned fields only) avoids echoing read-only/nested GET data. **Unknown** whether omitted attributes stay unchanged or are cleared on PUT — verify in tenant / API behavior (se **§11**). **`postalAddress`** not sent by design until explicit support. |
| **Resolver ambiguity** | Multiple matches fall back to first after heuristics; wrong customer selection is possible without disambiguation or orgnr. |
| **Invoice robustness** | Én linje; **produktbak** når plan har produkt (eller autocreate). Fortsatt begrenset: ingen multi-linje, ingen avansert MVA, betalingsbetingelser eller valuta — se **§4** for prisrekkefølge og **§6** for miljø-usikkerhet. |
| **Planner brittleness** | Keyword order and substring triggers; easy to mis-route on compound prompts. |
| **PII in logs** | Response previews may contain names/emails in Tripletex payloads; tokens are not logged, but tighten previews if compliance requires. |
| **Pagination** | List/search endpoints use fixed `count` (e.g. 100); large tenants may need paging. |
| **Product POST (`/product`) — required fields** | **Uavklart / miljøavhengig:** OpenAPI viser ofte få obligatoriske felter, men enkelte konti kan kreve **enhet**, **MVA-type**, **valuta** e.l. utover `name` + `number` (+ valgfri **`priceExcludingVatCurrency`**) → mulig **502** fra Tripletex. Verifiser mot mål-miljø. |
| **Product GET — `productNumber` query** | OpenAPI beskriver `productNumber` som **liste**. Klienten sender en **enkeltverdi som liste** i `params`; **presis serialisering** (gjentatte nøkler vs annet format) bør bekreftes mot ekte API. |
| **Invoice line + VAT + utsendelse** | **`vatType`-ID** fra miljø kan være feil for leietaker. **`sendToCustomer=false`** antar redusert auto-utsendelse — **bekreft** i mål-miljø. **Ordrelinje med `product`** kan på noen konti kreve tilleggsfelt utover det som sendes. |
| **`register_payment` / `PUT …/:payment`** | **`paymentTypeId`** må matche konto og bruksområde (bank, kontant, …). **Fakturastatus** (allerede betalt, kreditert, utenfor liste) kan gi **502** eller tomt søk. **`paymentDate`** vs bokføringsdato **ikke** modellert separat. **`paidAmountCurrency`** ikke implementert (valutakryss). |

---

## 7. Out of scope so far

- **Database** or durable job queue
- **LLM** for intent or entity extraction (unless explicitly requested later)
- **OAuth / token refresh** (consumer supplies session token per request)
- **Full automated test suite** / CI (recommended future addition); **delvis:** mockede `unittest`-tester for **`create_customer`** gjenbruk — `tests/test_create_customer_reuse.py` (se **§5** punkt 18)
- **Comprehensive Tripletex field coverage** (only minimal happy paths)

---

## 8. Current priority strategy

1. **Pause `create_invoice_for_customer` i nåværende sandbox** til leietaker har **bankkonto** i Tripletex — ellers **502** / **`tripletex_configuration`** (se **§16**). **Ikke fjern** **`customer_resolver_invoice_*`**, faktura-**`workflow_finished`**-felt eller diagnose i logger.
2. **Pause `register_payment`** til sandbox har minst én **synlig faktura** (`GET /invoice`) **og** **`TRIPLETEX_DEFAULT_PAYMENT_TYPE_ID`** fra Tripletex-UI (**§16**, **`TESTING.md`**). **Behold** **`register_payment_attempt`** og **PUT** `/:payment`-stien.
3. **Herde grønne workflows:** **`list_employees`**, **`search_customer`**, **`create_customer`**, **`create_product`**, **`search_product`** — konsistent list-utpakking, tellinger vs. **`fullResultSize`**, tydelige **`workflow_finished`**-felt (**§17**).
4. **Planner-herding** for faktura-prompts **etter** at (3) er tilfredsstillende — ikke før.
5. **Operational clarity** — hold `README.md` og denne filen oppdatert.

---

## 9. Immediate next steps

1. **Grønne workflows:** Verifiser at **`employee_count`**, **`customer_match_count`**, **`product_match_count`** matcher **`tripletex_http`**-preview / **`api_full_result_size`** der det er relevant (**§17**).
2. **Når sandbox har bankkonto:** gjenoppta **`create_invoice_for_customer`** live (se eksisterende diagnose **§5** punkt 16, **§15–16** — **ikke slettet**).
3. **Når sandbox har faktura + betalingstype-ID:** **`register_payment`** end-to-end (**§16**).
4. Deretter: **planner** for faktura-prompts (**§8** punkt 4).

---

## 10. Working principles for future Cursor sessions

- **Do not** add a **database** unless product direction changes.
- **Do not** add an **LLM** unless the user explicitly requests it.
- **Do not** redesign architecture for its own sake; **extend** `planner.py` + `workflows.py` + small helpers.
- **Preserve** existing **logging shape** (`event`, structured fields) and **error mapping** (400 / 502).
- **Never log secrets** (`session_token`, raw Basic headers).
- Prefer **small functions**, **type hints**, and **incremental PR-sized** changes.
- After meaningful changes, update **`README.md`** (user-facing) and **`PROJECT_STATE.md`** (handoff).

---

## 11. Latest change — minimal `update_customer` PUT (2026-03-19)

### What changed

- **`update_customer` PUT-er ikke lenger hele objektet fra `GET /customer/{id}`.** Tidligere kunne hele JSON-responsen (inkl. nestede DTO-er og metadata) sendes tilbake og utløse valideringsfeil.
- **`build_customer_update_payload(existing_customer, planned_updates)`** (i **`workflows.py`**) bygger nå kun:
  - **`id`** og **`version`** (påkrevd for optimistic locking), og
  - **eksplisitt planlagte skrivebare felt** som matchetes mot en tillatt whitelist (`name`, `email`, `phoneNumber` m.m.; planner fyller i dag de tre førstnevnte når brukeren ber om oppdatering).
- **Effekt:** Langt mindre risiko for Tripletex-feil forårsaket av **skrivebeskyttede** eller **irrelevante** felter som lå i full GET-respons.

### Practical findings

- **`version`** må finnes på GET-svaret; mangler den → **`WorkflowInputError`** (norsk melding).
- **`_CUSTOMER_PUT_ALLOWED`** hindrer at ukjente nøkler i `planned_updates` slippes inn uten bevisst utvidelse.
- **Logging:** `tripletex_http` logger fortsatt bare **nøkkelnavn** på request body, aldri `session_token`.

### Remaining risks / open questions

- **PUT-semantikk:** Det er **fortsatt uavklart** om Tripletex **bevarer** felt som **ikke** sendes i PUT-body, eller om noen felt kan **tømmes/nullstilles** når de utelates. Observasjon i **ekte selskapsmiljø** eller API-dokumentasjon er avgjørende ved tvil.
- **Ekstra obligatoriske felt:** Enkelte konti kan kreve mer enn `id`+`version`+endrede skalarer → mulig **502** med Tripletex-melding.
- **`postalAddress`:** **Ikke inkludert med vilje** i denne runden; krever egen, gyldig struktur (og ofte under-id’er) før den kan PUT-es trygt.

### Next recommended step

- **Verifiser `register_payment` og `paymentTypeId` i ekte miljø** (**§8–9**).

---

## 12. Latest change — `register_payment` (2026-03-19)

### Implementert

- **Planner:** `register_payment` med utløsere *register payment*, *pay invoice*, *registrer betaling*, *betal faktura*; felt **`payment_invoice_number`**, **`payment_amount`**, **`payment_date`** (+ eksisterende **`customer_name`**); uttrekk av fakturanummer, beløp (`kr`/`beløp:`), dato (`dato:` / `DD.MM.YYYY`), kunde (`kunde:` eller tekst før «faktura»).
- **`workflow_register_payment`:** `GET /invoice` via **`_fetch_invoices_by_number`** → **nøyaktig ett** treff → **`PUT /invoice/{id}/:payment`** med `paymentDate`, `paymentTypeId` (**`TRIPLETEX_DEFAULT_PAYMENT_TYPE_ID`**), `paidAmount`. **400** hvis mangler nummer/beløp/betalingstype, **0** treff, eller **>1** treff uten entydig kunde (`kunde:`).
- **`TripletexClient.put`** utvidet med **`params`** (query må sendes for dette endepunktet).
- **`main.py`:** `plan_built` har `has_payment_*` (ingen hemmeligheter).

### Antakelser

- Beløp er **alltid** bruker-oppgitt (ingen automatisk henting av restbeløp fra fakturaobjektet).
- Én **full** registrering per kall; ingen delbetalinger/KID/bankavstemming.
- Standard **betalingsdato** = **i dag** når ikke oppgitt.

### Må verifiseres mot ekte API

- At **`GET /invoice`** returnerer målfakturaen (synlighetsregler / status).
- At **`TRIPLETEX_DEFAULT_PAYMENT_TYPE_ID`** er riktig **PaymentType** for kundedomenet.
- **Valuta:** behov for **`paidAmountCurrency`** på fakturaer i annen valuta.
- **Faktura allerede betalt / låst:** forventet **502**-tekst fra Tripletex.
- **Semantikk** for **betalingsdato** vs regnskapsføring i nettbutikken.

### Tidligere leveranser (kontekst)

- Faktura med **produktlinje**, produktfallback og **invoice_autocreate_product** — se **§4–5** og **§6**.

### Neste anbefalte steg (etter `register_payment`)

- **Live-test og herde betaling** — se **§3** (oppsummering) og **§8–9** (konkrete oppgaver).

---

## 13. Lokal verifikasjon og Python-kompatibilitet (2026-03-19)

### Observert ved lokal kjøring

- Tjenesten startet med **`uvicorn main:app`** (lokal vert/port).
- **`GET /health`** returnerte **`{"ok": true}`**.
- **`POST /solve`** returnerte **`{"status": "completed"}`** i et lokalt testkall (prompt som rutet til **noop**; **ingen** Tripletex API-kall i det scenariet — dermed ingen sandbox-oppkobling der).
- **Strukturert JSON-livsløpslogging** ble observert i praksis på stdout: `request_received` → `files_decoded` → `plan_built` → `workflow_started` → `workflow_finished` → `request_finished`, med konsistent **`request_id`** (og **`tripletex_http`** når workflows kaller API).

### Python 3.9 / Pydantic

- **`planner.py`:** På **`Plan`** ble union-syntaks som **`float | None`** erstattet med **`Optional[float]`** (og tilgjengeliggjort via `typing.Optional`) slik Pydantic v2 tolker felt på **Python 3.9**.
- **`request_context.py`:** Tilsvarende **`ContextVar[Optional[str]]`** i stedet for `str | None` i typeannotation for contextvar.

### Anbefaling

- **Python 3.11** forblir **anbefalt runtime** (konkurranse, dokumentasjon); **3.9** er verifisert som fungerende for import og enkel lokal test etter justeringene over.

### Bekreftet NM-sandbox — `list_employees` (2026-03-19)

- **Lokal sandbox-test vellykket** for workflow **`list_employees`** (prompt som trigger, ekte session token og **tenant-spesifikk** API-**`base_url`** fra sandbox).
- **Ende-til-ende verifisert for denne flyten:** **autentisering** (Tripletex Basic med `"0"` + token), **ruting** (`plan_built` / `workflow_started` → `list_employees`), og **Tripletex-konnektivitet** (vellykket JSON-kall mot **`GET /employee`**).
- **Logger (observert):**
  - **`plan_built`** / **`workflow_finished`**: **`workflow`** = **`list_employees`**
  - **`tripletex_http`**: **GET** **`/employee`**, **`status_code` 200**
  - **`request_finished`**: **`http_status` 200** (HTTP-respons **`{"status":"completed"}`**)
- **Oppfølgingspunkt (minor, ikke blokker):** **`workflow_finished`** felt **`employee_count`** ble logget som **`"0"`** mens **`tripletex_http`** **`response_preview`** tydet på **minst én** ansatt — en **observabilitet / respons-parsing**-detalj å undersøke ved behov, **ikke** kritisk for å anse sandbox-integrasjonen som «grønn» for dette scenariet.

### Bekreftet NM-sandbox — `create_customer` (2026-03-19)

- **`create_customer`** er **ende-til-ende verifisert** mot NM-sandbox for **ny** kunde (**`tripletex_http`**: **`POST`** **`/customer`**, **`status_code` 201**) og for routing (typisk prompt *«opprett kunde …»*, ekte **`base_url`** + session token).
- **Search-before-create / gjenbruk:** Logikk og **mockede tester** (`tests/test_create_customer_reuse.py`) bekrefter reuse uten **`POST /customer`** når ett eksakt navnetreff finnes (**§5** punkt 18). **Live reuse** mot sandbox er **ikke** bekreftet fra agent-miljø (éns **`POST /solve`** ga **502** / Tripletex **403**); **lokal sandbox** med eksisterende kundenavn **må** fortsatt kjøres for full tillit.
- **Logger — ny kunde (observert historisk):**
  - **`plan_built`** / **`workflow_finished`**: **`workflow`** = **`create_customer`**
  - **`tripletex_http`**: **`POST`** **`/customer`**, **`status_code` 201**
  - **`request_finished`**: **`http_status` 200**; **`POST /solve`**-body **`{"status":"completed"}`**
- **Logger — forventet ved vellykket live reuse:** **`tripletex_http`** med **GET** (kundesøk), **`create_customer_precheck_search_result`** med **`combined_exact_match_row_count`** **1**, **`create_customer_existing_match_found`**, **`create_customer_existing_match_reused`**, **`workflow_finished`** med **`customer_reused`** = **`true`** (ingen **`POST /customer`**).
- **Logger — precheck uten gjenbruk (typisk `customer_reused` false):** **`tripletex_http` GET** `/customer`; **`create_customer_precheck_search_result`** (sjekk **`api_candidate_count`**, **`list_payload_extract`** (`unwrapped_object_values` / `unwrapped_list` ved OK), **`any_exact_*`**, **`combined_exact_match_row_count`**); **`create_customer_reuse_rejected`** når kombinert treff er **0**; **`create_customer_create_chosen`**; deretter **`tripletex_http` POST** `/customer`.
- **Testdata:** Gjentatte lokale kjøringer med **Acme AS** har skapt **flere** kunder med samme visningsnavn i sandbox — forventet i test, men **øker behovet** for å verifisere **`search_customer`** (entydig vs. **flere treff** / heuristikk).
- **Neste anbefalte lokale test:** **`search_customer`**; **og** **live** **`create_customer`** mot eksisterende kundenavn for å bekrefte reuse-logger (se **§5** punkt 18).

### Lokal to-terminal `create_customer` (2026-03-19)

- **Oppsett:** Én terminal **Uvicorn** (her: `127.0.0.1:8765`), én terminal **`curl`** `POST /solve` med prompt *«opprett kunde Nordisk Demo AS kontakt@nordiskdemo.example»* (samme type payload som `examples/solve_create_customer.json`).
- **`curl`-respons:** **HTTP 502** `Bad Gateway`; responskropp **`detail`** = forkortet **HTML** (CloudFront **403** — *«The request could not be satisfied»* / melding om at **HTTP-metoden** for forespørselen **ikke er tillatt** for distribusjonen). **Ikke** `{"status":"completed"}`.
- **Uvicorn / server-logg (samme forespørsel; ett `request_id` per kjøring, f.eks. `dba57fc0efcf`):**
  - **`plan_built`** / **`workflow_started`:** **`workflow`** = **`create_customer`**, **`has_customer_name`**: **true**.
  - **`tripletex_http`:** **kun én linje** i fanget logg for denne flyten: **`method`:** **`POST`**, **`path`:** **`/customer`**, **`status_code`:** **403**, **`request_body_keys`:** **`name`**, **`response_preview`:** HTML-feil (samme tema som **curl** `detail`).
  - **`workflow_failed`:** **`failure_kind`:** **`tripletex`**, **`tripletex_http_status`:** **403**.
  - **`request_finished`:** **`outcome`:** **`upstream_error`**, **`http_status`:** **502**.
  - **Ikke observert:** **`workflow_finished`** med suksess, **`create_customer_existing_match_found`**, **`create_customer_existing_match_reused`**, eller **`customer_reused`**.
- **Konklusjon for denne økta:**
  - **Gjenbruk av eksisterende kunde:** **Nei** — flyten feilet før noe vellykket **`workflow_finished`**; reuse-hendelser **manglet**.
  - **`POST /customer` unngått:** **Nei** — **`POST /customer`** ble **forsøkt** (logget som **`tripletex_http`**) og returnerte **403**.
  - **`create_customer_existing_match_found` / `…_reused`:** **Nei** — **opptrådte ikke** i loggen.
- **Diagnostikk / neste anbefalte steg:**
  1. **Miljø:** Verifiser at **`base_url`** og **`session_token`** er **nøyaktig** det NM-/sandbox-siden viser (feil vert, utløpt token eller trafikk via feil CDN kan gi **403** HTML i stedet for Tripletex JSON).
  2. **Prosess:** **Restart Uvicorn** etter kodeendringer — med **search-before-create** i kilden forventes normalt **`tripletex_http` GET** `/customer` **før** eventuell **`POST /customer`** når gjenbruk **ikke** skjer på første treff; i det **innfangede** loggutdraget vises **bare** **`POST`** (kompatibelt med **gammel prosess** som ikke hadde pre-**GET**, eller utelatt linje — ved tvil: restart + ny kjøring).
  3. **Når API er grønt:** Kjør **`create_customer`** mot et kundenavn som **allerede** finnes og bekreft **GET** + **`create_customer_existing_match_*`** + **`customer_reused`:** **`true`** uten **`POST /customer`**.

### NM-sandbox — `search_customer` del-treff «Acme» (oppdatert)

- **`search_customer`** med prompt *«finn kunde Acme»* (bl.a. `examples/local.solve_search_customer_existing.json`): **`tripletex_http`** **GET `/customer`** returnerte **HTTP 200** med **`fullResultSize=4`** i **preview** — **kundedata finnes** og **søk/tilkobling** er **OK**.
- **`POST /solve`:** **HTTP 200**, **`{"status":"completed"}`**.
- **Observabilitet (minor, ikke blokker):** **`workflow_finished`** viste **`customer_match_count`** = **`"0"`** til tross for **`fullResultSize=4`** — **telle-/logg-gap** (jevnfør **`list_employees`**, **`search_product`**).
- **Konsekvens for faktura (jf. punkt 16):** *«Fant ingen kunde … «Acme AS»»* ved **`create_invoice_for_customer`** tyder på **resolver-/oppfølgingsadferd** eller **navne-match**, ikke at sandbox **mangler** «Acme»-kunder.

### Bekreftet NM-sandbox — `create_product` (2026-03-19)

- **`create_product`** er **ende-til-ende verifisert** mot NM-sandbox (bl.a. prompt *«opprett produkt Kaffe varenummer 1001 pris 59 kr»*, `examples/local.solve_create_product.json`).
- **Logger (observert):**
  - **`plan_built`** / **`workflow_finished`**: **`workflow`** = **`create_product`**
  - **`tripletex_http`**: **`POST`** **`/product`**, **`status_code` 201**
  - **`request_finished`**: **`http_status` 200**; **`POST /solve`** **`{"status":"completed"}`**
- **Praktisk observasjon (ikke blokker):** Det opprettede produktets **navn** så ut til å **inneholde «varenummer 1001»** (kombinasjon av fritekst og varenummer-ledd) fremfor et rent *«Kaffe»* — **antyder** at **navn vs. varenummer**-split i planner bør **ses på senere**; **ikke** blokkerende for videre testing.

### Bekreftet NM-sandbox — `search_product` (2026-03-19)

- **`search_product`** er **ende-til-ende verifisert** mot NM-sandbox (bl.a. prompt *«finn produkt Kaffe»*, `examples/local.solve_search_product.json`).
- **Logger (observert):**
  - **`plan_built`** / **`workflow_finished`**: **`workflow`** = **`search_product`**
  - **`tripletex_http`**: **GET** **`/product`**, **`status_code` 200**
  - **`request_finished`**: **`http_status` 200**; **`POST /solve`** **`{"status":"completed"}`**
- **Oppfølgingspunkt (minor, ikke blokker):** **`workflow_finished`** hadde **`product_match_count`** = **`"0"`** mens **`response_preview`** inneholdt **`fullResultSize=1`** — **kun** et **logg-/telle**-mønster å rette opp i senere (jevnfør **`employee_count`** for **`list_employees`**, **§5** punkt 12).

### Lokal sandbox — `create_invoice_for_customer` (øvelse, ikke fullført OK)

- Flyten er **øvet** mot NM-sandbox (`examples/local.solve_create_invoice_for_customer.json`).
- **Prompt variant 1:** *«opprett faktura for kunde Acme AS produkt Kaffe»* → planner satte kunde til **«for kunde Acme AS»** (for enkelt uttrekk).
- **Prompt variant 2:** *«opprett faktura kunde: Acme AS produkt Kaffe»* → kunde ble **«Acme AS produkt Kaffe»** (**produkt**-ledd fanget i **`kunde:`**-feltet uten **komma** / **`produkt:`**-label).
- **Prompt variant 3:** *«opprett faktura kunde: Acme AS, produkt: Kaffe 500 kr»* (**nåværende innhold i lokal payload**) → **korrekt** kunde **«Acme AS»** i plan, men **`POST /solve`** → **400** *«Fant ingen kunde som matcher «Acme AS»»* (reelt oppslag, ikke credential-feil).
- **Lesning (historisk observert):** **Tjenesteruting** + **sandbox auth** OK; *«Fant ingen kunde … «Acme AS»»* med primærsøk pekte på **resolver** — **§15** har **etterfølgende** kodeendring (**`resolve_customer_for_invoice`**). **Innsikt:** Del-søk *«finn kunde Acme»* viste **`fullResultSize=4`** — data finnes.
- **Re-test etter §15 (2026-03-19, samme sandbox URL/token):** `POST /solve` med *«opprett faktura kunde: Acme AS, produkt: Kaffe 500 kr»* (`local.solve_create_invoice_for_customer.json`) ga fortsatt **HTTP 400** med *«Fant ingen kunde som matcher «Acme AS»»* (dvs. **`not_found`**-gren i **`resolve_customer_for_invoice`**, ikke **tvetydig**-meldingen). **Mulige forklaringer:** (1) lokal **uvicorn**-/prosess **ikke restartet** etter kodeendring (kjører fortsatt gammel **`resolve_customer_by_name`**), og/eller (2) **`GET /customer`** i dette kallet returnerte **0 rader** både for primær og fallback-token i akkurat den økta. **Neste steg:** restart app, kjør på nytt, og i stdout se etter **`customer_resolver_invoice_fallback_search`** / **`customer_resolver_invoice_pick`** (bevis på ny sti).

---

## 14. Lokal sandbox — XML AccessDenied og credential-validering (2026-03-19)

### Funn ved ekte lokal `/solve` mot sandbox

- **`GET /health`** og **`POST /solve`** kan fungere lokalt, men første Tripletex-kall feiler med **XML-respons** (f.eks. **`AccessDenied`**) i stedet for forventet JSON — typisk når **`base_url`** ikke matcher miljøet (inkl. at stien må være **`…/v2`**), eller **`session_token`** er feil / utløpt / ikke fra samme miljø som URL viser.
- **Konkurranse vs. sandbox:** Bruk alltid **API-URL + session token** som sandbox-/NM-siden viser for **testing**, ikke antatt standardverdi alene.

### Ny atferd (lettvekts, ingen hemmeligheter i logg)

- Kildekode **fallback-er ikke** til `https://api.tripletex.io/v2` eller annen URL i runtime — **`base_url`** og **`session_token`** kommer **kun** fra request-body (`schemas.TripletexCredentialsIn`). JSON-eksempler i `schemas` er dokumentasjon, ikke standard.
- **`request_received`** utvider seg med: **`tripletex_base_url_source`** (`"request_body"`), **`tripletex_base_url_placeholder_like`**, **`tripletex_session_token_placeholder_like`** (kun boolske flagg — **aldri** token-verdi).
- Etter **`plan_built`**, hvis **`workflow` ≠ `noop`**: **`tripletex_credential_checks.tripletex_credentials_valid_for_api`**; ved feil → logg **`workflow_failed`** med **`failure_kind`: `credential_config`**, **`request_finished`** **400**, HTTP **`detail`** på norsk — **ingen** Tripletex-kall.
- **`noop`:** validering hoppes over (samme som før: ingen API-kall), slik lokale planner-tester med plassholder-credentials fortsatt kan returnere **`completed`**.

### Neste steg for utvikler

- **Fullført mot NM-sandbox (lokalt):** **`list_employees`**, **`create_customer`**, **`search_customer`**, **`create_product`**, **`search_product`** — **§13**.
- **`create_invoice_for_customer`:** **Kode §15** på plass; **sandbox POST /invoice** fortsatt **ikke** bekreftet grønn — siste **curl**-re-test ga fortsatt **400** kunde ikke funnet (**§13** faktura-avsnitt); **restart** tjeneste og verifiser logger.
- **Prioritet nå:** **(1)** **Restart** app etter deploy/lokal endring, **re-run** faktura-payload + sjekk **`customer_resolver_invoice_*`** i logg, **(2)** herde **faktura-prompts** (**§9**), **(3)** **`register_payment`** (**§8**).
- **Valgfritt:** Produktnavn vs. varenummer (**§13** `create_product`); **`employee_count`** / **`product_match_count`** / **`customer_match_count`** vs **`fullResultSize`** i preview (**§5** punkt 12, **15**, **17**).

---

## 15. Latest change — `create_invoice_for_customer` kundeoppløsning (2026-03-19)

### Hva som ble endret

- **`customer_resolver.py`:** Nye hjelpefunksjoner + **`resolve_customer_for_invoice`** (tuple `row`, status **`ok`** / **`not_found`** / **`ambiguous`**) og **`pick_best_customer_match_for_invoice`** med **skåring** og **tie-break** (lengde). **Strukturert logging** (JSON, **ingen** hemmeligheter): bl.a. **`customer_resolver_invoice_fallback_search`**, **`customer_resolver_invoice_pick`** (`match_kind`: `exact` \| `prefix` \| `substring` \| `query_contains_name`, `resolution`, `api_candidate_count`), **`customer_resolver_invoice_ambiguous`**.
- **`workflows.py` —** kun **`workflow_create_invoice_for_customer`:** Byttet fra **`resolve_customer_by_name`** til **`resolve_customer_for_invoice`**; ny **400**-tekst når **tvetydig**.

### Atferd (kort)

- Primært **`GET /customer`** med **`customerName`** = planlagt kundenavn; ved **tom liste** forsøkes **nedbrutte tokens** (lengst først, min. 2 tegn) — typisk fanger *«Acme»* når *«Acme AS»* gir **0** rader i API-et men **Acme**-treff finnes.
- **Valg:** Eksakt normalisert treff (navn/`displayName`) slår **prefiks** → **delstreng** → svak **navn inneholdt i query**; **flere uavklarte toppkandidater** → **ambiguous** / brukerfeilmelding (ikke «første rad» stille).
- **Live:** Skal bekreftes mot NM-sandbox med **`local.solve_create_invoice_for_customer.json`** (kunde + **`TRIPLETEX_DEFAULT_VAT_TYPE_ID`**, produkt, ol.). **Obs. re-test:** Én kjøring etter merge ga fortsatt **400** «Fant ingen kunde» — se **§13** (faktura-avsnitt) om **server restart** og logghendelser.

---

## 16. Dag 2 — faktura POST-felter + `register_payment`-logging (2026-03-20)

### Verifisert med fersk Uvicorn + `examples/local.solve_create_invoice_for_customer.json`

- **`resolve_customer_for_invoice`** kjører: logger **`customer_resolver_invoice_pick`** med `search_path` **`primary_customerName`**, `match_kind` **`exact`**, `picked_customer_id` satt. **`customer_resolver_invoice_fallback_search`** utløses **kun** når primærsøket gir **0** kandidater — for *«Acme AS»* i testmiljøet var **ikke** fallback nødvendig (primær **`GET /customer`** hadde treff).
- **Tidligere 400 «ingen kunde»** på samme payload var **ikke** reprodusert etter fiks av **`value.values`**-utpakking og fersk prosess; feil kan i stedet være **manglende produkt** (400 «Fant ingen produkt …») hvis «Kaffe» ikke finnes og **ikke** «opprett produkt hvis mangler» er i prompten.

### `POST /invoice` — obligatoriske felter (Tripletex validering)

- **`invoiceDueDate`** — tidligere **422** *«invoiceDueDate … Kan ikke være null»*. Settes nå til **i dag +** **`TRIPLETEX_INVOICE_DUE_DAYS`** (standard **14**).
- **`orders[]`:** **`customer`** og **`deliveryDate`** — **422** hvis null. Ordren får nå **`customer: { id }`** og **`deliveryDate`** = fakturadato (samme dag som `orderDate`).

### Sandbox-blokkering etter gyldig payload

- **502 / 422** med melding om at **faktura ikke kan opprettes før selskapet har bankkontonummer** — **miljø-/selskapsoppsett** i Tripletex, ikke applikasjonslogikk. Full grønn **POST /invoice** i denne leietakeren krever **registrert bankkonto** (evt. annen NM-sandbox med oppsett).

### `register_payment`

- Strukturert logglinje **`register_payment_attempt`** før **`PUT /invoice/{id}/:payment`**: `invoice_id`, `invoice_number`, `payment_type_id`, `payment_date`, `paid_amount`, `customer_id`, `invoice_search_match_count`. **`tripletex_http`** viser fortsatt **HTTP-status** og **response_preview** for selve **PUT**.
- **`TRIPLETEX_DEFAULT_PAYMENT_TYPE_ID`:** hent **ID** fra Tripletex (selskapsinnstillinger / betalingstyper). **`GET …/v2/invoice/paymentType`** kan returnere **tom liste** avhengig av rettigheter/oppsett — i så fall **ikke** trial-and-error; bruk UI-dokumentert ID.
- **Automatisert sjekk (2026-03-20):** `GET /invoice` med datovindu mot **NM-sandbox** (`kkpqfuj-amager…`) returnerte **0** fakturaer — **`register_payment`** kan da ikke fullføres før det finnes minst én synlig faktura (eller annet tenant med data).

### Tripletex-feil som selskapsoppsett (ikke agent-feil)

- Ved **422** med validering om **bankkontonummer** (og liknende): HTTP **502**-`detail` prefikses med **«Tripletex / selskapsoppsett …»**; **`workflow_failed`** bruker **`failure_kind`:** **`tripletex_configuration`** (ellers **`tripletex`**).

---

## 17. Tripletex list-unwrapping + `workflow_finished`-tellinger (2026-03-20)

- **`tripletex_list.py`:** **`tripletex_list_rows_from_response`** — felles utpakking når **`value`** er liste **eller** **`{ fullResultSize, values }`** (samme hull som tidligere rammet **kunde**).
- **`workflow_list_employees`:** **`employee_count`** reflekterer nå antall utpakkede rader (tidligere ofte **`"0"`** ved **`values`‑wrapper**).
- **`product_resolver.search_products`:** samme utpakking; **`search_products`** / **`search_products_fallback`** returnerer korrekte rader (og metadata for siste forsøk).
- **`workflow_search_customer`** / **`workflow_search_product`:** **`customer_match_count`** / **`product_match_count`** er konsistente med utpakkede lister; **`workflow_finished`** kan inkludere **`list_payload_extract`**, **`api_full_result_size`** (Tripletex `fullResultSize` når satt).
- Når **`fullResultSize`** ≠ antall rader returnert i siden (paginering / `count`): logg **`tripletex_list_count_hint`** med `resource`, `api_full_result_size`, `rows_in_page`.
- **`create_customer`** / **`create_product`:** ingen ekstra API-kall lagt til; **gjenbruk**-sti for kunde uendret (**én** pre-**GET** før ev. **POST**).

### Verifikasjonsrunde (NM-sandbox, 2026-03-20)

Kjørt sekvensielt: **`list_employees`** → **`search_customer`** («finn kunde Acme») → **`search_product`** («finn produkt Kaffe») → **`create_customer`** (unikt navn) → **`create_product`**.

| Workflow | **Før** (typisk) | **Etter herding** (observert) |
|----------|------------------|-------------------------------|
| **`list_employees`** | `employee_count` **«0»** mens `response_preview` viste **`fullResultSize` ≥ 1** | **`employee_count`** = **`api_full_result_size`** (f.eks. **1**), **`list_payload_extract`:** **`unwrapped_object_values`** |
| **`search_customer`** | **`customer_match_count`** **«0»** vs **`fullResultSize`** **6** | **`customer_match_count`** = **6**, **`api_full_result_size`:** **6** |
| **`search_product`** | **`product_match_count`** **«0»** vs **`fullResultSize`** **4** | **`product_match_count`** = **4**, **`api_full_result_size`:** **4** |
| **`tripletex_list_count_hint`** | — | **Ingen** linjer når **sideraden** = **`fullResultSize`** (forventet ved full treffside ≤ **100**) |
| **`create_customer_precheck_search_result`** | **`api_candidate_count`** **0** med **`fullResultSize` > 0** (feil utpakking) | Ved **tomt** søk på unikt navn: **`api_candidate_count`** **0** og **`GET /customer`**-preview **`fullResultSize`:** **0** — **konsistent** |

### Planner — smal faktura-justering (2026-03-20)

- **`_trim_customer_name_at_product_boundary`:** Når **`kunde:`** fanges med **`[^\n,]+`** uten komma før **`produkt:`**, klippes kundenavn før **`produkt` / `vare` / `product`**. Dekker f.eks. *«opprett faktura kunde: Acme AS produkt: Kaffe 500 kr»* uten at hele halen havner i **`customer_name`**. **Invoice/payment-workflows** og Tripletex-kall er **urørt**; tester: **`tests/test_planner_invoice_customer_trim.py`**.

**Eksplisitt (scope):** Faktura-planneren er **bare** forbedret for mønsteret **`kunde:` … `produkt:`** (ev. med komma). **Fri tekst** i stil med *«opprett faktura **for** kunde Acme AS …»* uten **`kunde:`**-etikett er **ikke** dekket av denne endringen og **forventes uendret** svakt.

### Invoice plan-parsing — re-test etter trim (2026-03-20)

**`build_plan` (ingen Tripletex-kall):**

| Prompt (utdrag) | **`customer_name`** | **`product_name`** (utdrag) |
|-----------------|---------------------|-----------------------------|
| *«… kunde: Acme AS produkt: Kaffe 500 kr»* (uten komma) | **`Acme AS`** | **`Kaffe 500 kr`** |
| *«… kunde: Acme AS, produkt: Kaffe 500 kr»* | **`Acme AS`** | **`Kaffe 500 kr`** |
| *«opprett faktura for kunde Acme AS produkt Kaffe»* | fortsatt **`for kunde Acme AS`** (kjent språklig hull; **ikke** løst av trim) | **`Kaffe`** |

**Merk:** Full **`plan_built`** / **`workflow_started`** for faktura mot sandbox er **ikke** nødvendig for å validere uttrekk; **`create_invoice_for_customer`** stopper fortsatt ofte på **miljø** (bankkonto) ved **POST /invoice**.

### `create_customer` gjenbruk — live NM-sandbox (2026-03-20)

| Scenario | HTTP | **`tripletex_http`** | **`POST /customer`** | Logger (kort) |
|----------|------|----------------------|----------------------|----------------|
| **«opprett kunde Acme AS»** (flere duplikater med samme eksakte navn i Tripletex) | **400** | **GET** `/customer` **200** | **Nei** | **`combined_exact_match_row_count`:** **6** → **`create_customer_reuse_rejected`** / **`create_customer_existing_match_ambiguous`** (tvetydig, ikke gjenbruk) |
| **«opprett kunde Agent Verify NM 20260320»** (nøyaktig **én** eksakt treff i precheck) | **200** | **GET** `/customer` **200** | **Nei** | **`combined_exact_match_row_count`:** **1** → **`create_customer_existing_match_found`** / **`create_customer_existing_match_reused`** → **`workflow_finished`** **`customer_reused`:** **`true`** |

**Bekreftet for gjenbruks-stien:** **`api_candidate_count`** og **`combined_exact_match_row_count`** stemmer med utpakkede rader; **ingen** **`tripletex_http`** **POST** `/customer` i logg for gjenbruk.

**Eksplisitt (live-verifisert, NM-sandbox 2026-03-20):** **`create_customer`** **gjenbruk** er **live-verifisert** for **begge** utfallene nedenfor — i **begge** tilfeller **ingen** **`POST /customer`**:
1. **`ambiguous_multiple_exact_matches`** — flere rader med samme eksakte navn → **400** + **`create_customer_existing_match_ambiguous`** (tvetydig, **ikke** gjenbruk som enkelt kunde).
2. **Eksakt én treff** — **`combined_exact_match_row_count`:** **1** → **`customer_reused`:** **`true`** i **`workflow_finished`**, med **`create_customer_existing_match_*`**-logger, **uten** **POST**.

### Hva som er grønt / stabilt vs. blokkert (2026-03-20)

- **Reelt grønne og stabile i sandbox (ende-til-ende observert):** **`list_employees`**, **`search_customer`**, **`search_product`**, **`create_customer`** (både **ny** kunde og **gjenbruk** når nøyaktig **én** eksakt treff), **`create_product`**, **`noop`**. **`search_invoice`** / **`update_customer`** antas fortsatt OK med gyldig input (mindre nylig live-prøvd i samme økt).
- **Miljø-blokkert (ikke kodefeil i agenten):** **`create_invoice_for_customer`** — **POST /invoice** krever bl.a. **bankkonto** i Tripletex-selskapet (**§16**). **`register_payment`** — trenger **synlig faktura** + **`TRIPLETEX_DEFAULT_PAYMENT_TYPE_ID`** (**§16**).
- **Kode-/planner-gjeld (ikke miljø):** Naturlig norsk *«opprett faktura **for** kunde …»* uten **`kunde:`**-etikett gir fortsatt **svakt kundenavn** (se tabell over). Flere **identiske** «Acme AS»-rader → **400** tvetydig (forventet).

---

## 18. Score mode — grønne workflows (konkurranse-lignende prompts, 2026-03-20)

### Metode (reproduserbar)

- **Skript:** **`scripts/score_green_workflows.py`** — starter lokal **Uvicorn** (port **`SCORE_PORT`**, standard **9966**), **`POST /solve`** sekvensielt mot **`http://127.0.0.1:{port}/solve`**, credentials fra **`examples/local.solve_list_employees.json`** (NM-sandbox, samme mønster som tidligere live-tester).
- **Artefakter (gitignored):** **`.score_mode_verify.log`** (server stdout), **`.score_mode_results.json`** (strukturert uttrekk per kall).
- **Ikke kjørt i denne runden:** Endringer i **`create_invoice_for_customer`**, **`register_payment`**, eller fjerning av logger / **`failure_kind`**.

### Prompts som ble testet (alle **plan_workflow** matchet forventet workflow)

| # | Workflow | Prompt (naturlig variant) | HTTP | Merknad |
|---|----------|----------------------------|------|---------|
| 1 | **`list_employees`** | *«list employees»* | 200 | Stabil |
| 2 | **`list_employees`** | *«ansatte»* | 200 | Stabil |
| 3 | **`list_employees`** | *«Kan du vise ansatte?»* | 200 | Stabil (substring **«ansatte»**) |
| 4 | **`search_customer`** | *«finn kunde Acme»* | 200 | Stabil |
| 5 | **`search_customer`** | *«search customer Acme»* | 200 | Stabil |
| 6 | **`search_customer`** | *«finn kunde»* (uten navn) | 400 | **Skjør / forventet:** **`workflow_input`**, **0** Tripletex-kall |
| 7 | **`search_product`** | *«finn produkt Kaffe»* | 200 | Stabil |
| 8 | **`search_product`** | *«søk produkt Kaffe»* | 200 | Stabil |
| 9 | **`search_product`** | *«liste produkter»* (uten søkeord) | 400 | **Kjent hull:** tom tail etter trigger → **`workflow_input`**, **0** Tripletex-kall |
| 10 | **`create_customer`** | *«opprett kunde Score Mode Unik 20260320»* (nytt navn) | 200 | **GET** + **POST** `/customer` (**2** `tripletex_http`), **`customer_reused`:** **`false`** |
| 11 | **`create_customer`** | *«opprett kunde Agent Verify NM 20260320»* | 200 | **1** **GET** `/customer`, **ingen** **POST**; **`customer_reused`:** **`true`** (**eksakt én** treff) |
| 12 | **`create_customer`** | *«opprett kunde Acme AS»* | 400 | **1** **GET**, **ingen** **POST**; **`workflow_input`** (flere eksakte duplikater) |
| 13 | **`create_product`** | *«opprett produkt Score Mode Vare Alfa pris 49 kr»* | 200 | Stabil (**1** **POST** `/product`) |
| 14 | **`create_product`** | *«nytt produkt Score Mode Vare Beta 10 kr»* | 200 | Stabil |
| 15 | **`create_product`** | *«create product Score Mode Vare Gamma»* | 200 | Stabil (engelsk trigger) |

### Observasjoner (API, tellere, logging)

- **List/search:** **`workflow_finished`**-tall (**`employee_count`**, **`customer_match_count`**, **`product_match_count`**) stemte med **`api_full_result_size`** i logg; **`list_payload_extract`:** **`unwrapped_object_values`** der vist.
- **`tripletex_list_count_hint`:** **Ingen** linjer i denne sandbox-runden (forventet når **side** = **total** for små datasett).
- **Routing:** Ingen uventet **noop** eller feil workflow i tabellen over.
- **Tvetydighet:** Flere treff på kunde-søk logges ikke som feil (design); **create_customer** med flere **eksakt** like navne-treff → tydelig **400** + **`failure_kind`:** **`workflow_input`**.
- **Ny kunde vs. reuse:** Som i **§17** — **reuse** og **tvetydig** uten **POST**; **ny** kunde med **GET** + **POST**.

### Live vs. miljø vs. kjent gjeld (etter runden)

| Kategori | Innhold |
|----------|---------|
| **Live-verifisert (denne runden)** | Tabellen over + observasjoner; **§17** (reuse + list-telling) forblir gyldig historikk. |
| **Miljø-blokkert** | Uendret: **§16** (**faktura** uten bankkonto, **betaling** uten faktura/type). **Ikke** retestet her. |
| **Kjent planner-/kodegjeld (ikke fikset nå)** | *«liste produkter»* uten filter; *«finn kunde»* uten navn; faktura *«for kunde …»* uten **`kunde:`** (**§17**). **Merk:** fraser som *«liste medarbeidere»* / *«vis medarbeidere»* er **ikke** egne triggere — kan bli **noop**; **ingen** kodeendring i denne runden (unngår substring-konflikter med f.eks. *«opprett … medarbeidere»* uten egen designrunde). |

### Kodeendringer knyttet til score mode

- **Kun** tillegg av **`scripts/score_green_workflows.py`** (verifikasjon). **Ingen** endring i **invoice** / **payment**-**workflows** eller **planner**-routing i denne omgangen.

### Anbefalt neste steg

- Når **Tripletex**-miljø tillater: gjenoppta **§16** (**POST /invoice**, **register_payment**).
- Valgfritt: egen **design**-sak for **ansatt**-synonymer (**medarbeidere**) med **ordgrenser** / lengre fraser — **ikke** hastverksendring.

---

## 19. NM i AI — konkurranse-submission og offentlig endpoint (2026-03-20)

### Hva du skal lime inn i submission-skjemaet (NM i AI)

| Felt | Anbefaling |
|------|------------|
| **Endpoint URL** | Full **HTTPS**-URL som peker direkte på **`POST /solve`**. **Nøyaktig mønster:** **`https://<SERVICE_URL_UTEN_PATH>/solve`** der **`<SERVICE_URL_UTEN_PATH>`** er **rot-URL** fra **Google Cloud Run** (f.eks. `https://ai-accounting-agent-xxxxx-ew.a.run.app`). **Må** ende med **`/solve`** — ikke bare rot-domenet. **Ikke** bruk Tripletex API-URL her. |
| **API Key** | **Tom / blank** — FastAPI-appen i dette repoet har **ingen** innebygd Bearer- eller API-key-autentisering på **`/solve`**. Fyll **kun** inn nøkkel hvis dere **senere** setter foran en proxy med auth. |
| **Tripletex `base_url` + `session_token`** | Kommer **ikke** i submission-endepunktfeltet. De sendes i **JSON-body** til **`/solve`** (`tripletex_credentials`), fra **konkurransens / NM sandbox**-konto, slik **`schemas.SolveRequestBody`** beskriver. |

### Deploy- og runtime-forutsetninger (fra kode / Dockerfile — ikke endret i denne runden)

- **Offentlig tjeneste:** Cloud Run deploy med **`--allow-unauthenticated`** (se **README.md**) eksponerer **`GET /health`** og **`POST /solve`** uten egen auth i appen.
- **Port:** **`Dockerfile`** kjører **`uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}`** — **8080** som default; **Cloud Run** setter **`PORT`**. Ingen binding til **localhost** i container.
- **Runtime uten lokale prosjektfiler:** Uploads skrives til **`/tmp`** (evt. **`AI_AGENT_UPLOAD_ROOT`**); Tripletex-kall bruker **`base_url`** fra request body — **ingen** hardkodet avhengighet til utviklermaskin eller repo-sti i **request path** (kun Python-moduler lastes fra image).

### Request/response-kontrakt (uendret)

- **Body:** `prompt`, `files` (valgfri), `tripletex_credentials` (`base_url`, `session_token`).
- **Suksess:** **HTTP 200**, `{"status":"completed"}`.
- **400:** `WorkflowInputError`, Pydantic-validering, eller **`credential_config`** (placeholder-URL/token) — se **`main.py`** / **`tripletex_credential_checks`**.
- **502:** **`TripletexAPIError`** (upstream Tripletex), inkl. **`tripletex_configuration`**-prefiks ved selskapsoppsett (**§16**).
- **Logger:** **`session_token`** og rå secrets logges **ikke** (**tripletex_request**).

### Offentlig verifikasjon vs. lokal baseline

| Nivå | Status |
|------|--------|
| **Lokal known-good** | **§17–§18** (grønne workflows, sandbox-token i `examples/local.*.json`). |
| **Offentlig deploy** | **Må** verifiseres av team **etter** `gcloud run deploy` når **faktisk service-URL** foreligger. **Ingen** ekte Cloud Run-URL ble tilgjengeliggjort i denne workspace-økta; **automatisk curl mot produksjons-URL ble derfor ikke kjørt her.** |
| **Miljø-blokkert (Tripletex)** | **§16** — faktura/betaling testes når sandbox/selskap tillater (**bankkonto**, synlig faktura, **payment type**). **Ingen** kodeendring i **invoice**/**payment** i denne oppgaven. |

### Sjekkliste: kjør mot **deres** offentlige `BASE` etter deploy

Erstatt **`BASE`** med **rot-URL uten path** (samme som i Cloud Run-konsollen, **uten** `/solve` på rot — **curl** bruker `/health` og `/solve` eksplisitt):

```bash
curl -sS "${BASE}/health"
# Forventet: {"ok":true}

curl -sS -w "\nHTTP:%{http_code}\n" -X POST "${BASE}/solve" \
  -H "Content-Type: application/json" \
  -d @examples/solve_list_employees.json
# Forventet: HTTP 200 og {"status":"completed"} når tripletex_credentials i filen er gyldige sandbox-verdier
```

**Merk:** Bruk **HTTPS** som Cloud Run gir. Juster **`examples/solve_list_employees.json`** til **NM-sandbox** `base_url` + **session_token** (ikke commit token).

### Deploy-risikoer / kjente begrensninger

- **Kald start / timeout:** Cloud Run **request timeout** må være tilstrekkelig for Tripletex-kall (juster ved deploy om nødvendig).
- **Minne:** Standard image er liten; ved store filopplastinger kan minne økes.
- **Tripletex**-feil (502) er **ikke** «deploy-feil» — sjekk token, **base_url** (**/v2**), og selskapsoppsett (**§16**).

### Eksakt tekst til «Endpoint URL»-feltet (copy-paste-mal)

**Etter** dere har deployet, erstatt plassholderen med **deres** rot-URL fra Cloud Run:

```
https://ERSTATT_MED_CLOUD_RUN_SERVICE_URL/solve
```

Eksempel (fiktiv): `https://ai-accounting-agent-abc123-no.a.run.app/solve`

---

## 20. Container build / Artifact Registry / Cloud Run (vei B, 2026-03-20)

### Bekreftet fra repo (ingen kodeendring i denne runden)

| Sjekk | Status |
|--------|--------|
| **`Dockerfile`** | **`python:3.11-slim`**, `pip install -r requirements.txt`, `COPY . .`, **`CMD`** `uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}` |
| **Avhengigheter** | **`requirements.txt`** (FastAPI, Uvicorn, requests, Pydantic) |
| **Entrypoint** | **`main:app`** (FastAPI) |
| **Port** | **8080** default; Cloud Run injiserer **`PORT`** — matcher **Cloud Run container port** **8080** |

**Merk:** `docker build` bør kjøres fra **ren** arbeidskopi (unngå å bake inn `.venv` / store artefakter — bruk **`.dockerignore`** eller bygg via **Cloud Build** som respekterer `.gcloudignore` hvis dere legger det til senere).

### GCP-kontekst (dette prosjektet)

| Variabel | Verdi |
|----------|--------|
| **Project ID** | **`ai-nm26osl-1733`** |
| **Region** | **`europe-west1`** |
| **Cloud Run service** | **`ai-accounting-agent`** |
| **Artifact Registry repo (foreslått navn)** | **`docker-repo`** |
| **Image-navn** | **`ai-accounting-agent`** |
| **Tag** | **`latest`** (bytt til digest/semver i produksjon etter behov) |

### Runbook — kronologisk copy/paste (samme verdier som tabellen)

**Forutsetning:** Terminal eller **Cloud Shell**; **`cd`** til **rotmappen** til dette repoet (der **`Dockerfile`** og **`main.py`** ligger). I Cloud Shell: **klon repo** eller **last opp** prosjektmappen — **ikke** kjør `gcloud builds submit` fra `~` uten kildekode.

**Før du bygger — verifiser at du står riktig:**

```bash
pwd
ls -la Dockerfile main.py
```

Hvis **`Dockerfile: No such file`** → du er ikke i prosjektroten; **`cd`** dit først.

**Eksakt image-URL (brukes i deploy og i Artifact Registry):**

```text
europe-west1-docker.pkg.dev/ai-nm26osl-1733/docker-repo/ai-accounting-agent:latest
```

---

#### Variant A — Cloud Build (anbefalt i Cloud Shell; ingen lokal Docker nødvendig)

Kjør **blokken under i én økt** (rekkefølge: API-er → repo → bygg/push → deploy → URL → tester):

```bash
export PROJECT_ID=ai-nm26osl-1733
export REGION=europe-west1
export AR_REPO=docker-repo
export IMAGE=ai-accounting-agent
export TAG=latest
export IMAGE_URI="${REGION}-docker.pkg.dev/${PROJECT_ID}/${AR_REPO}/${IMAGE}:${TAG}"

gcloud config set project "${PROJECT_ID}"

gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  --project="${PROJECT_ID}"

if ! gcloud artifacts repositories describe "${AR_REPO}" \
  --location="${REGION}" --project="${PROJECT_ID}" &>/dev/null; then
  gcloud artifacts repositories create "${AR_REPO}" \
    --repository-format=docker \
    --location="${REGION}" \
    --description="Docker images for Cloud Run" \
    --project="${PROJECT_ID}"
fi

gcloud builds submit --tag "${IMAGE_URI}" --project="${PROJECT_ID}" .

gcloud run deploy ai-accounting-agent \
  --image="${IMAGE_URI}" \
  --region="${REGION}" \
  --project="${PROJECT_ID}" \
  --platform=managed \
  --allow-unauthenticated \
  --port=8080

export BASE="$(gcloud run services describe ai-accounting-agent \
  --region="${REGION}" --project="${PROJECT_ID}" --format='value(status.url)')"
echo "BASE=${BASE}"

curl -sS "${BASE}/health"
echo

curl -sS -w "\nHTTP:%{http_code}\n" -X POST "${BASE}/solve" \
  -H "Content-Type: application/json" \
  -d @examples/solve_list_employees.json
```

**Før siste `curl`:** Sett **gyldige** `tripletex_credentials` i **`examples/solve_list_employees.json`** (sandbox — ikke commit token).

---

#### Cloud Shell — feilsøking (hva du så)

| Symptom | Årsak | Hva du gjør |
|---------|--------|-------------|
| **`ERROR: (gcloud.builds.submit) Invalid value for [source]: Dockerfile required when specifying --tag`** | **`gcloud builds submit … .`** ble kjørt fra **feil mappe** (f.eks. `~` uten kildekode). Cloud Build fant **ingen** `Dockerfile` i katalogen som ble sendt inn. | **`cd`** til repo-rot der **`Dockerfile`** ligger. Kjør **`ls Dockerfile`** før **`gcloud builds submit`**. |
| **`Image '…/ai-accounting-agent:latest' not found`** | Bygget feilet (se over), så imaget ble **aldri** pushet til Artifact Registry. **`gcloud run deploy`** finner da ikke imaget. | Fiks **build** først; kjør deploy på nytt når **`gcloud builds submit`** er **OK**. |
| **`GET /`** eller **`/health`** returnerer **HTML** («Congratulations» / «It's running!») | Tjenesten kjører fortsatt **demo-/hello-container** fra tidligere deploy, eller **ingen** vellykket deploy av app-imaget. | Etter vellykket **build + deploy** med riktig image skal **`curl "${BASE}/health"`** gi **`{"ok":true}`** (JSON), ikke HTML. |
| **`curl: option -d: error encountered when reading a file`** | **`examples/solve_list_employees.json`** finnes ikke i **cwd** (du er ikke i repo-rot). | **`cd`** til prosjektrot, eller bruk **full sti** til JSON-filen. |
| **Rotete terminal** (fragmenter som `…OST "${BASE}/solve"` …) | **Sammenlimt** copy/paste — flere kommandoer i én linje. | Lim inn **én** kommando om gangen, eller bruk hele runbook-blokken **fra et rent shell** etter **`cd`** til repo. |

---

#### Variant B — Lokal `docker build` + `docker push`

Krever **Docker** installert og innlogging mot Artifact Registry:

```bash
export PROJECT_ID=ai-nm26osl-1733
export REGION=europe-west1
export AR_REPO=docker-repo
export IMAGE=ai-accounting-agent
export TAG=latest
export IMAGE_URI="${REGION}-docker.pkg.dev/${PROJECT_ID}/${AR_REPO}/${IMAGE}:${TAG}"

gcloud config set project "${PROJECT_ID}"

gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  --project="${PROJECT_ID}"

if ! gcloud artifacts repositories describe "${AR_REPO}" \
  --location="${REGION}" --project="${PROJECT_ID}" &>/dev/null; then
  gcloud artifacts repositories create "${AR_REPO}" \
    --repository-format=docker \
    --location="${REGION}" \
    --description="Docker images for Cloud Run" \
    --project="${PROJECT_ID}"
fi

gcloud auth configure-docker europe-west1-docker.pkg.dev

docker build -t "${IMAGE_URI}" .
docker push "${IMAGE_URI}"

gcloud run deploy ai-accounting-agent \
  --image="${IMAGE_URI}" \
  --region="${REGION}" \
  --project="${PROJECT_ID}" \
  --platform=managed \
  --allow-unauthenticated \
  --port=8080

export BASE="$(gcloud run services describe ai-accounting-agent \
  --region="${REGION}" --project="${PROJECT_ID}" --format='value(status.url)')"
echo "BASE=${BASE}"

curl -sS "${BASE}/health"
echo

curl -sS -w "\nHTTP:%{http_code}\n" -X POST "${BASE}/solve" \
  -H "Content-Type: application/json" \
  -d @examples/solve_list_employees.json
```

---

**NM Endpoint URL etter vellykket deploy:** **`${BASE}/solve`** (lim inn den **faktiske** `https://…`-strengen fra `echo BASE=…` + **`/solve`**).

### Eksakt image-URL (lim inn i Cloud Run + dokumentasjon)

Etter vellykket push er **full image-referanse**:

```text
europe-west1-docker.pkg.dev/ai-nm26osl-1733/docker-repo/ai-accounting-agent:latest
```

*(Hvis dere velger annet **repository ID** enn `docker-repo`, erstatt den delen av stien tilsvarende.)*

---

### 1) Enable API-er (én gang per prosjekt)

```bash
gcloud config set project ai-nm26osl-1733

gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  --project=ai-nm26osl-1733
```

*(**`cloudbuild.googleapis.com`** trengs hvis dere bruker **`gcloud builds submit`**; for ren lokal **`docker push`** holder ofte **Artifact Registry** + **Run**.)*

---

### 2) Opprett Artifact Registry (Docker) i `europe-west1` (hvis det mangler)

```bash
gcloud artifacts repositories describe docker-repo \
  --location=europe-west1 \
  --project=ai-nm26osl-1733
```

Hvis den **ikke** finnes:

```bash
gcloud artifacts repositories create docker-repo \
  --repository-format=docker \
  --location=europe-west1 \
  --description="Docker images for Cloud Run" \
  --project=ai-nm26osl-1733
```

---

### 3) Autentiser Docker mot Artifact Registry

```bash
gcloud auth configure-docker europe-west1-docker.pkg.dev
```

---

### 4) Bygg og push image (fra **rot** av dette repoet)

**Alternativ A — lokal Docker:**

```bash
cd "/sti/til/AI Accounting Agent"

export PROJECT_ID=ai-nm26osl-1733
export REGION=europe-west1
export AR_REPO=docker-repo
export IMAGE=ai-accounting-agent
export TAG=latest

docker build -t "${REGION}-docker.pkg.dev/${PROJECT_ID}/${AR_REPO}/${IMAGE}:${TAG}" .

docker push "${REGION}-docker.pkg.dev/${PROJECT_ID}/${AR_REPO}/${IMAGE}:${TAG}"
```

**Alternativ B — Cloud Build (uten lokal Docker):**

```bash
cd "/sti/til/AI Accounting Agent"

export PROJECT_ID=ai-nm26osl-1733
export REGION=europe-west1
export AR_REPO=docker-repo
export IMAGE=ai-accounting-agent
export TAG=latest

gcloud builds submit --tag "${REGION}-docker.pkg.dev/${PROJECT_ID}/${AR_REPO}/${IMAGE}:${TAG}" \
  --project="${PROJECT_ID}"
```

**Eksakt image-URL etter push** (samme som over):

```text
europe-west1-docker.pkg.dev/ai-nm26osl-1733/docker-repo/ai-accounting-agent:latest
```

---

### 5) Cloud Run — bytt fra demo-image til app-image (konsoll)

1. Gå til **Google Cloud Console** → **Cloud Run** → velg region **`europe-west1`** → tjeneste **`ai-accounting-agent`**.
2. Klikk **Edit & deploy new revision** (eller **Rediger og distribuer ny revisjon**).
3. Under **Container**:
   - **Container image URL:** lim inn **nøyaktig**  
     `europe-west1-docker.pkg.dev/ai-nm26osl-1733/docker-repo/ai-accounting-agent:latest`
   - **Container port:** **8080** (skal samsvare med **`PORT`** / **Dockerfile**).
4. **Ingress:** **All** (eller tilsvarende offentlig tilgang for konkurranse) — som før, med mindre dere med vilje begrenser.
5. **Authentication:** **Allow unauthenticated invocations** (offentlig **`/health`** og **`/solve`** uten IAM på kallet), med mindre NM krever noe annet.
6. **Deploy** / **Distribuer**.

**Alternativ — `gcloud` (samme image, samme service):**

```bash
gcloud run deploy ai-accounting-agent \
  --image=europe-west1-docker.pkg.dev/ai-nm26osl-1733/docker-repo/ai-accounting-agent:latest \
  --region=europe-west1 \
  --project=ai-nm26osl-1733 \
  --platform=managed \
  --allow-unauthenticated \
  --port=8080
```

---

### 6) Hent offentlig service-URL (rot, uten path)

```bash
gcloud run services describe ai-accounting-agent \
  --region=europe-west1 \
  --project=ai-nm26osl-1733 \
  --format='value(status.url)'
```

Utdata er **`BASE`** (f.eks. `https://ai-accounting-agent-xxxxx-ew.a.run.app`).

---

### 7) Test etter deploy (offentlig)

Erstatt **`BASE`** med URL fra forrige kommando.

```bash
curl -sS "${BASE}/health"
# Forventet: {"ok":true}
```

**Grønn `/solve`** (Tripletex må være gyldig i JSON — bruk **NM sandbox** `base_url` + `session_token` i filen, ikke commit token):

```bash
curl -sS -w "\nHTTP:%{http_code}\n" -X POST "${BASE}/solve" \
  -H "Content-Type: application/json" \
  -d @examples/solve_list_employees.json
# Forventet: HTTP 200 og {"status":"completed"} ved gyldige credentials og vellykket Tripletex-kall
```

*(Tilpass **`examples/solve_list_employees.json`** med deres sandbox-verdier før test.)*

---

### 8) NM i AI — **Endpoint URL** (nøyaktig)

**Lim inn:**

```text
${BASE}/solve
```

**Konkret:** **`BASE`** fra **`gcloud run services describe … --format='value(status.url)'` + **`/solve`** på slutten.

**Eksempel (kun form — erstatt med deres faktiske URL):**

```text
https://ai-accounting-agent-xxxxx-ew.a.run.app/solve
```

**API Key-felt:** **tomt** (**§19**).

---

## Handoff — NM `POST /solve` **422** (2026-03-21)

**Observasjon:** Manuelle **`curl`**-kall ga **200** + **`request_received`**, mens Cloud Run viste **`POST /solve … 422`**. **Konklusjon:** **422** kommer fra **FastAPI/Pydantic body-validering** *før* **`solve()`** kjører — da finnes **ikke** `request_id` / `request_received` i logg (typisk mønster).

**Sannsynlige årsaker (avstemt mot kode før fiks):**

1. **`extra="forbid"`** — klient med **ekstra toppnivå-felt** (metadata) → **422**.
2. **`"files": null`** — forventet liste → **422** (Pydantic godtok ikke `null`).
3. **Feil innholdstype / ugyldig JSON** — gir **422** uten app-handler (logg viser nå rå preview der mulig).

**Implementert i repo:** Se **`main.py`** (`request_validation_error_handler`, **`capture_solve_request_body`**), **`schemas.py`** ( **`ignore`**, **`files`**-coercion, **`prompt`**-coercion), **`requirements.txt`** (**`httpx`** for `TestClient`), **`tests/test_solve_validation.py`**.

**Neste steg (score etter 422):** Bekreft i Cloud Logging at **`request_validation_error`** **ikke** lenger dominerer; deretter fortsett **planner_llm**-heuristikk / terskler for **`llm_noop`** på grønne workflows (se **§§2.1** og **§5** punkt 83–86).

---

*Last updated: 2026-03-21 — **Handoff** NM 422 + valideringslogging + tolerant `/solve`-parsing; **§5** punkt 22 search/create_product-fallbacks + NM 132/proxy; punkt 21 `create_customer`; punkt 20 `list_employees`; **§20** deploy-runbook + Cloud Shell-feilsøking; **§19** NM endpoint; **§18** score mode; **§17** grønne workflows; **§16** faktura/payment miljø; **3.11** anbefalt.*
