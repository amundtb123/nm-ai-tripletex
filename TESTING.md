# Manual local testing (Tripletex sandbox)

Local-only checks against **your** Tripletex sandbox — not the competition endpoint.

## Prerequisites

- Python **3.11** recommended (see `README.md`; **3.9** may work after `Optional[]` model fixes in `PROJECT_STATE.md` §13).
- Dependencies: `pip install -r requirements.txt`
- Sandbox **session token** and API base URL (often `https://api.tripletex.io/v2`).
- Edit each file under `examples/` and replace **`PASTE_SANDBOX_SESSION_TOKEN`** (never commit real tokens).

### Sandbox API URL and session token (one file)

Before the first real `curl` against Tripletex:

1. Open **`examples/solve_list_employees.json`** (or any other `solve_*.json`).
2. Set **`tripletex_credentials.base_url`** to the **exact** API base URL from the NM / Tripletex sandbox page (must end with **`/v2`**, HTTPS).
3. Set **`tripletex_credentials.session_token`** to the **session token** shown for that sandbox (same page — not the placeholder `PASTE_*` string).
4. Save the file and run `curl … -d @examples/solve_list_employees.json` as below.

If either value is still a placeholder or the URL is malformed, the app returns **400** before calling Tripletex (see **`PROJECT_STATE.md` §14**).

### Environment (workflow-dependent)

| Variable | Needed for |
|----------|------------|
| `TRIPLETEX_DEFAULT_VAT_TYPE_ID` | `create_invoice_for_customer` (default in code often `3` — verify in sandbox) |
| `TRIPLETEX_INVOICE_DUE_DAYS` | `create_invoice_for_customer`: `invoiceDueDate` = today + this many days (default **14**) |
| `TRIPLETEX_DEFAULT_PAYMENT_TYPE_ID` | `register_payment` (**required** by workflow if unset) |
| `TRIPLETEX_INVOICE_LINE_AMOUNT` | Invoice line default if no `… kr` in prompt |
| `PORT` | Server port (default `8080`) |
| `LOG_LEVEL` | e.g. `INFO` or `DEBUG` |
| `LLM_PLANNER_ENABLED` | Sett `1` / `true` for **Spor B** LLM-router etter `noop` (krever også API-nøkkel; se `PROJECT_STATE.md` §2.1) |
| `OPENAI_API_KEY` eller `LLM_PLANNER_API_KEY` | OpenAI-kompatibel nøkkel når LLM-planner er aktivert |
| `LLM_PLANNER_BASE_URL` / `LLM_PLANNER_MODEL` | Valgfritt; default OpenAI `v1` + `gpt-4o-mini` |

### LLM-router (Spor B) — verifisering etter Cloud Run-deploy

**Når LLM faktisk ble brukt** (vellykket routing til et grønt workflow), skal **`plan_built`** inneholde:

- **`planner_mode`:** **`llm`**
- **`workflow_route`:** **`llm`**
- **`planner_llm_status`:** **`ok`**
- **`workflow`:** f.eks. `list_employees`, `search_customer`, … (innenfor første LLM-scope)
- **`planner_confidence`**, **`planner_language`**, **`planner_route_detail`** (kort oppsummering — ingen hemmeligheter)

**Eksempel — siste `plan_built` for en request** (tilpass prosjekt/region; JSON i logglinjen kan være `jsonPayload` eller rå tekst avhengig av oppsett):

```bash
gcloud logging read 'resource.type="cloud_run_revision" AND resource.labels.service_name="ai-accounting-agent" AND jsonPayload.event="plan_built"' \
  --project=YOUR_PROJECT_ID --limit=5 --format=json | \
  jq '.[] | .jsonPayload | {planner_mode, workflow, workflow_route, planner_llm_status, planner_confidence}'
```

Hvis du logger til stdout som ren JSON (som i appen), filtrer på `event=="plan_built"` og feltet **`planner_mode`**.

**Eksempelprompts å teste etter deploy** (naturlig språk — forvent **`noop`** fra regler alene, deretter **`planner_mode`:** **`llm`** når LLM er aktivert):

| Språk | Prompt (eksempel) | Forventet workflow (typisk) |
|--------|---------------------|-----------------------------|
| Norsk | «Kan du vise meg hvem som jobber hos oss?» | `list_employees` |
| Engelsk | «I need to look up an existing customer named Acme Ltd» | `search_customer` |
| Norsk | «Registrer ny kunde Krokstrand AS» | `create_customer` |
| Engelsk | «Find our product by code 1001» | `search_product` |

Juster navn/produkt til det som finnes i **din** sandbox. Prompts som **allerede** treffer eksakte fraser (f.eks. «list employees») gir **`planner_mode`:** **`exact_rule`** — da kalles **ikke** LLM.

## Run the app locally

From the project root:

```bash
source .venv/bin/activate   # if you use a venv
export PORT=8080
python main.py
```

Or:

```bash
uvicorn main:app --host 127.0.0.1 --port 8080
```

Leave this terminal open — **all structured logs print to stdout here**.

## Health check

```bash
curl -sS http://127.0.0.1:8080/health
```

Expected: `{"ok":true}`

## Call `/solve` with an example payload

Replace port if needed. Example:

```bash
curl -sS -X POST "http://127.0.0.1:8080/solve" \
  -H "Content-Type: application/json" \
  -d @examples/solve_list_employees.json
```

### Example files → workflow

| File | Typical `workflow` in logs |
|------|------------------------------|
| `examples/solve_list_employees.json` | `list_employees` |
| `examples/solve_create_customer.json` | `create_customer` |
| `examples/solve_search_customer.json` | `search_customer` |
| `examples/solve_update_customer.json` | `update_customer` |
| `examples/solve_create_product.json` | `create_product` |
| `examples/solve_search_product.json` | `search_product` |
| `examples/solve_create_invoice_for_customer.json` | `create_invoice_for_customer` |
| `examples/solve_register_payment.json` | `register_payment` |

**Note:** Prompts use placeholder names, invoice numbers, and products. Change them to entities that **exist in your sandbox** (or create them first with create-customer / create-product / create-invoice examples).

## Log checklist (per request)

Use the same **`request_id`** on all lines for one `/solve` call.

- [ ] **`request_received`** — `file_count`, `tripletex_base_url` (no token), `tripletex_base_url_source`, `tripletex_base_url_placeholder_like`, `tripletex_session_token_placeholder_like` (booleans only — never the token value)
- [ ] **`files_decoded`** — `count` (and `filenames` if uploads)
- [ ] **`plan_built`** — `workflow`, `detected_intent`, **`planner_mode`** (`exact_rule` / `regex_fallback` / `llm` / `noop`), **`planner_selected_workflow`**, **`planner_selected_entity`**, **`planner_confidence`**, **`planner_language`**, **`planner_llm_status`**, **`planner_route_detail`**, `workflow_route`, `has_customer_name`, `has_product_*`, `has_payment_*`, …
- [ ] **`workflow_started`** — matches `plan_built.workflow`
- [ ] **`tripletex_http`** — one or more lines: `status_code`, `path`, `query_param_keys`, short `response_preview`
- [ ] **`workflow_finished`** — e.g. `customer_id`, `invoice_id`, `product_match_count`, … **or**
- [ ] **`customer_resolver_invoice_pick`** / **`customer_resolver_invoice_fallback_search`** — when testing `create_invoice_for_customer` (fallback only if primary `GET /customer` is empty)
- [ ] **`register_payment_attempt`** — before `PUT /invoice/{id}/:payment`: `invoice_id`, `payment_type_id`, `paid_amount`, `payment_date`, `customer_id` (no secrets)
- [ ] **`workflow_failed`** — `failure_kind` (`credential_config` / `workflow_input` / `tripletex` / **`tripletex_configuration`** — manglende selskapsoppsett i Tripletex, f.eks. bankkonto / …) and safe error fields
- [ ] **`request_finished`** — `outcome`, `http_status`

**Not logged:** `session_token`, `Authorization`, full response bodies (only truncated preview on `tripletex_http`).

## Expected HTTP outcomes

- **200** + `{"status":"completed"}` — workflow completed (including **noop** if no trigger matched).
- **400** — validation / `WorkflowInputError` / **credential_config** (placeholder or invalid `base_url` / `session_token` before Tripletex).
- **502** — Tripletex API error (`TripletexAPIError`). Meldinger om f.eks. manglende **bankkontonummer** prefikses som **Tripletex / selskapsoppsett** (ikke agent-feil); logger bruker **`failure_kind`:** **`tripletex_configuration`**.

### `register_payment` (end-to-end)

1. **`export TRIPLETEX_DEFAULT_PAYMENT_TYPE_ID=<id>`** — ID fra Tripletex (selskap / betalingstyper), ikke gjettet.
2. Bruk **`fakturanummer` og beløp** som matcher en **eksisterende** faktura synlig i **`GET /invoice`** (ca. siste 730 dager). Hvis sandbox har **0** fakturaer, opprett/registrer en i Tripletex først eller bruk annet miljø.
3. Bekreft **`register_payment_attempt`** deretter **`tripletex_http`** **`PUT`** **`/invoice/{id}/:payment`** med **HTTP 200**.

Sanity-check HTTP status against **`request_finished.http_status`** in logs.

### Optional: score-mode batch (green workflows)

From project root, with a real token in **`examples/local.solve_list_employees.json`**:

```bash
SCORE_PORT=9966 .venv/bin/python scripts/score_green_workflows.py
```

Writes **`.score_mode_results.json`** (and server log **`.score_mode_verify.log`**). See **`PROJECT_STATE.md` §18** for how results were interpreted (gitignored artifacts).
