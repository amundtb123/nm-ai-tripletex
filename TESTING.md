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
| `TRIPLETEX_DEFAULT_PAYMENT_TYPE_ID` | `register_payment` (**required** by workflow if unset) |
| `TRIPLETEX_INVOICE_LINE_AMOUNT` | Invoice line default if no `… kr` in prompt |
| `PORT` | Server port (default `8080`) |
| `LOG_LEVEL` | e.g. `INFO` or `DEBUG` |

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
- [ ] **`plan_built`** — `workflow`, `detected_intent`, `has_customer_name`, `has_product_*`, `has_payment_*`, …
- [ ] **`workflow_started`** — matches `plan_built.workflow`
- [ ] **`tripletex_http`** — one or more lines: `status_code`, `path`, `query_param_keys`, short `response_preview`
- [ ] **`workflow_finished`** — e.g. `customer_id`, `invoice_id`, `product_match_count`, … **or**
- [ ] **`workflow_failed`** — `failure_kind` (`credential_config` / `workflow_input` / `tripletex` / …) and safe error fields
- [ ] **`request_finished`** — `outcome`, `http_status`

**Not logged:** `session_token`, `Authorization`, full response bodies (only truncated preview on `tripletex_http`).

## Expected HTTP outcomes

- **200** + `{"status":"completed"}` — workflow completed (including **noop** if no trigger matched).
- **400** — validation / `WorkflowInputError` / **credential_config** (placeholder or invalid `base_url` / `session_token` before Tripletex).
- **502** — Tripletex API error (`TripletexAPIError`).

Sanity-check HTTP status against **`request_finished.http_status`** in logs.
