# PROJECT_STATE — AI Accounting Agent (NM i AI 2026)

> **Purpose:** Single handoff document if chat or team context is lost. Update this file when behavior or priorities change meaningfully.

---

## 1. Project goal

Deliver a **small, deployable FastAPI service** for the competition that:

- Accepts natural-language `prompt` plus optional file uploads and **Tripletex** credentials.
- Routes to **rule-based workflows** (no LLM in scope unless explicitly added later).
- Calls the **Tripletex API v2** with **HTTP Basic Auth** (`username` = `"0"`, `password` = session token).
- Returns **`{"status": "completed"}`** on successful workflow completion; uses **400** for bad client input and **502** for upstream Tripletex errors (sanitized `detail`).
- Runs in **Docker** on **port 8080**, suitable for **Google Cloud Run**.
- **Anbefalt Python:** **3.11** (se README). **3.9** kan brukes etter eksplisitte type-justeringer på modeller (se **§13**).

---

## 2. Current architecture

| Layer | Responsibility |
|--------|----------------|
| **`main.py`** | FastAPI app: `GET /health`, `POST /solve`; **/solve-livsløp** logger `request_received` → `files_decoded` → `plan_built` → **`credential_config`-sjekk** (kun når `workflow` ≠ `noop`) → `workflow_started` → `workflow_finished` \| `workflow_failed` → `request_finished` (+ `tripletex_http`); kobles med `request_id`. Mapper `WorkflowInputError` / feilkonfigurerte Tripletex-credentials → **400**, `TripletexAPIError` → 502. |
| **`request_context.py`** | ContextVar **`solve_request_id`** (`Optional[str]`) slik `tripletex_http` får samme `request_id` som gjeldende `/solve`; union-syntaks unngås for **Python 3.9**-kompatibilitet. |
| **`schemas.py`** | Pydantic models for `/solve` body (`SolveRequestBody`, credentials, file items). |
| **`file_parser.py`** | Decode base64 uploads under `/tmp/uploads` (or `AI_AGENT_UPLOAD_ROOT`), batch dirs, basic path safety. |
| **`planner.py`** | Rule-based **workflow choice** (first matching trigger wins) og **light slot extraction** (`customer_name`, `name`, produkt-felt, **`invoice_autocreate_product`**, **`payment_invoice_number`** / **`payment_amount`** / **`payment_date`**, `email`, `phone`, `notes`, `target_entity`, `hints`). `Plan` bruker **`Optional[float]`** (ikke `float \| None`) for Pydantic på **Python 3.9**. |
| **`workflows.py`** | One function per workflow; Tripletex calls via `tripletex_json`; raises `WorkflowInputError` when input is incomplete. Includes `build_customer_update_payload` for safe customer PUT. |
| **`customer_resolver.py`** | `search_customer_by_name`, `pick_best_customer_match`, `resolve_customer_by_name` (exact name → substring heuristic → first row + log `customer_resolver_ambiguous`). |
| **`product_resolver.py`** | `search_products`, `search_products_fallback`, `resolve_product_by_name_or_number`, `pick_best_product_match` for `GET /product`; `product_resolver_ambiguous` when multiple rows and heuristics fall back to first. |
| **`tripletex_client.py`** | Thin `requests` session with Basic auth; `get` / `post` (optional query `params`) / **`put` (optional `params`)** / `delete`. |
| **`tripletex_request.py`** | `tripletex_json`: executes request, logs **`tripletex_http`** (inkl. `request_id` når satt, method, path, status, param keys, body keys, avkortet response preview) — **never logs session token**. |
| **`tripletex_errors.py`** | Parse Tripletex `ApiError` JSON → `TripletexAPIError` with `public_detail()` for API responses. |
| **`tripletex_credential_checks.py`** | Plassholder-heuristikker for `base_url` / `session_token` (til logging) og **`tripletex_credentials_valid_for_api`** før første Tripletex-kall — **400** med norsk `detail`, **ingen** token i logger. |

**Data flow:** `POST /solve` → validate body → optional `decode_files_to_tmp` → `build_plan(prompt)` → `run_workflow(plan, client)` → JSON logs + `{"status":"completed"}`.

---

## 3. Implemented features

### HTTP

- **`GET /health`** → `{"ok": true}`
- **`POST /solve`**  
  - Body: `prompt`, optional `files[]` (`filename`, `content_base64`), `tripletex_credentials` (`base_url`, `session_token`)

### Cross-cutting

- Structured logging (`log_structured` + `tripletex_http` JSON lines on stdout)
- Pydantic request validation (`extra="forbid"` on request models)
- File uploads to **`/tmp/uploads/batch_*`** (umask-style dirs `0700`; override with **`AI_AGENT_UPLOAD_ROOT`**)
- Planner **`Plan`**: `workflow`, `target_entity`, `name`, `email`, `phone`, `customer_name`, `product_name`, `product_number`, `product_price`, `invoice_autocreate_product`, `payment_invoice_number`, `payment_amount`, `payment_date`, `notes`, `hints`, `detected_intent`, `raw_prompt`

### Workflows (see `planner.py` trigger order; first match wins)

| Workflow | Triggers (examples) | Tripletex (summary) |
|----------|---------------------|----------------------|
| `list_employees` | `list employees`, `ansatte` | `GET /employee` |
| `search_invoice` | `search invoice`, `søk faktura`, `finn faktura`, … | `GET /invoice` with date window + `invoiceNumber` or `customerId` |
| `register_payment` **(implemented)** | `register payment`, `pay invoice`, `registrer betaling`, `betal faktura` | `GET /invoice` (fakturanummer, ev. kunde) → nøyaktig **ett** treff → **`PUT /invoice/{id}/:payment`** med `paymentDate`, `paymentTypeId`, `paidAmount`; **400** hvis flere treff uten `kunde:`, manglende beløp/fakturanummer, eller manglende `TRIPLETEX_DEFAULT_PAYMENT_TYPE_ID` |
| `create_invoice_for_customer` | `create invoice`, `opprett faktura`, … | Resolve customer → ev. **produkt** (`resolve_product_by_name_or_number` / fallback) eller **POST /product** når «opprett produkt hvis mangler» + navn → `POST /invoice` med ordrelinje som har **`product: { id }`** når produkt finnes/opprettes; ellers tidligere fri linje (kun hvis ingen produktnavn/-nummer i plan) |
| `search_customer` | `find customer`, `finn kunde`, … | `GET /customer` (count matches) |
| `update_customer` | `update customer`, `oppdater kunde`, … | `GET /customer/{id}` → `build_customer_update_payload` → `PUT /customer/{id}` (kun `id`, `version`, og felt som faktisk endres) |
| `search_product` **(implemented)** | `search product`, `finn produkt`, `søk produkt`, `liste produkter`, … | `GET /product` med `name` og/eller `productNumber`; **fallback** ved 0 treff (begge → nummer → navn); match-count; **`pick_best_product_match`** for tvetydig-logg (`product_resolver_ambiguous`) |
| `create_product` | `create product`, `opprett produkt`, `nytt produkt` | `POST /product`: `name`; **`number`** fra planner (parsed varenummer) **eller** generert suffiks; valgfri **`priceExcludingVatCurrency`** når pris er parsert (`… kr` / `… nok`) |
| `create_customer` | `create customer`, `opprett kunde`, … | `POST /customer` |
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
- **Customer resolution:** Name-based search + heuristic “best” row; **no** kundenummer/orgnr parsing yet in resolver.
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
- **Automated test suite** / CI (recommended future addition)
- **Comprehensive Tripletex field coverage** (only minimal happy paths)

---

## 8. Current priority strategy

1. **Etter `register_payment` (implementert):** **live-test og herde betalingsflyt** — bekreft **paymentTypeId**, valuta/`paidAmountCurrency`, og at målfaktura finnes i `GET /invoice`.
2. **Herde faktura** — flere linjer, paginering, **MVA** / **`sendToCustomer`** / ordrelinje- og **POST /product**-felt.
3. **Planner** (regex uten LLM): bedre betalings-/faktura-uttrekk (delbetaling, KID, flere valutaer).
4. **Operational clarity** — hold `README.md` og denne filen oppdatert.

---

## 9. Immediate next steps

1. **Prioritet etter `register_payment`:** live-test av `PUT /invoice/{id}/:payment`; valider **GET /invoice**-synlighet; korrekt **TRIPLETEX_DEFAULT_PAYMENT_TYPE_ID**; utvid ved behov med **`paidAmountCurrency`**, delbetaling og klar feilhåndtering for lukket faktura.
2. **Fortsett faktura-/produkt-verifikasjon:** `vatType`, **`sendToCustomer`**, **POST /product**, **GET /product** `productNumber`.
3. **Planner:** forbedre uttrekk for betaling (KID, delbeløp) når konkurransen krever det.

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

- Lim inn **eksakt** `base_url` (HTTPS, sti **`/v2`**) og **session token** fra sandbox-siden i én `examples/solve_*.json` (se **`TESTING.md`**), kjør f.eks. **`solve_list_employees.json`**, bekreft **`tripletex_http`** med JSON-svar.

*Last updated: 2026-03-19 — **§14** sandbox AccessDenied + credential-validering; **§13** uendret innhold; **3.11** anbefalt.*
