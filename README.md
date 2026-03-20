# AI Accounting Agent (NM i AI 2026)

Liten FastAPI-tjeneste med Tripletex-integrasjon og regelbasert planner (ingen LLM i denne versjonen).

## Krav

- Python 3.11
- Avhengigheter: se `requirements.txt`

## Kjøre lokalt

```bash
cd "/sti/til/AI Accounting Agent"
python3.11 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Standard port er **8080** (kan overstyres med miljøvariabel `PORT`). Loggnivå: `LOG_LEVEL=INFO` (standard) eller `DEBUG`.

```bash
export PORT=8080
python main.py
```

Alternativt med uvicorn direkte:

```bash
uvicorn main:app --host 127.0.0.1 --port 8080
```

### Sjekk

- `GET http://127.0.0.1:8080/health` → `{"ok":true}`
- `POST /solve` med JSON-body (se under)

Ved suksess: `200` og `{"status":"completed"}`.

Filer skrives under **`/tmp/uploads`** (batch-mapper `batch_<id>`), med tillatelse `0700` på mapper. Overstyres med `AI_AGENT_UPLOAD_ROOT` om nødvendig.

### Lokal testing mot Tripletex **sandbox** (ikke konkurranse-URL)

Prosjektet er ment for **lokal** kjøring mot Tripletex **test/sandbox**-konto. Bruk **ekte sandbox session token** i JSON; ikke sjekk inn token i git. Konkurranse-URL/-oppsett er et eget steg.

1. Hent **session token** og **API base URL** fra Tripletex sandbox (ofte `https://api.tripletex.io/v2`).
2. Sett miljøvariabler etter behov, f.eks. `TRIPLETEX_DEFAULT_VAT_TYPE_ID`, `TRIPLETEX_DEFAULT_PAYMENT_TYPE_ID` (sistnevnte for `register_payment`).
3. Start appen og følg **JSON på stdout** for hendelsesrekkefølge og feil.
4. Ferdige payload-maler med plassholdere ligger i **`examples/`** (se **`TESTING.md`** for oversikt og sjekkliste).

```bash
curl -sS -X POST "http://127.0.0.1:8080/solve" \
  -H "Content-Type: application/json" \
  -d @examples/solve_search_customer.json
```

```bash
# Krever bl.a. TRIPLETEX_DEFAULT_PAYMENT_TYPE_ID
curl -sS -X POST "http://127.0.0.1:8080/solve" \
  -H "Content-Type: application/json" \
  -d @examples/solve_register_payment.json
```

**Plassholder-curl** (bytt kun token lokalt):

```bash
curl -sS -X POST "http://127.0.0.1:8080/solve" \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "Finn kunde Eksempel AS",
    "files": [],
    "tripletex_credentials": {
      "base_url": "https://api.tripletex.io/v2",
      "session_token": "USE_SANDBOX_TOKEN_NOT_PRODUCTION"
    }
  }'
```

### Strukturert logging (feilsøking, ingen hemmeligheter)

Én JSON-linje per hendelse på stdout; felt **`event`** + **`request_id`** (korrelerer hele `/solve`-kallet og hvert `tripletex_http`):

| `event` | Relevante felt (utdrag) |
|---------|---------------------------|
| `request_received` | `request_id`, `prompt_length`, `file_count`, `tripletex_base_url` |
| `files_decoded` | `request_id`, `count`, ev. `filenames`, `total_bytes` (ikke filinnhold) |
| `plan_built` | `request_id`, `workflow`, `detected_intent`, `has_customer_name`, `has_product_name`, `has_invoice_number_in_prompt`, `has_payment_invoice_number`, `has_payment_amount`, … |
| `workflow_started` | `request_id`, `workflow`, `target_entity` |
| `tripletex_http` | `request_id`, `method`, `path`, `status_code`, liste over query-/body-nøkler, kort `response_preview` |
| `workflow_finished` | `request_id` + resultat (f.eks. `invoice_id`, `workflow`, …) |
| `workflow_failed` | `request_id`, `failure_kind`, ev. `tripletex_http_status`, feilmelding |
| `request_finished` | `request_id`, `outcome`, `http_status` |

**Logges ikke:** `session_token`, `Authorization`, full respons (kun avkortet `response_preview`), eller filers rå innhold.

### Planner (uttrekk)

For hver forespørsel bygges en enkel plan med bl.a.:

| Felt | Beskrivelse |
|------|-------------|
| `workflow` | Valgt arbeidsflyt (se tabell under) |
| `target_entity` | `employee` / `customer` / `product` / `invoice` / tom ved noop |
| `name` | Bl.a. nytt produktnavn ; ved **update customer** navn fra `navn:` / `name:` |
| `email` | Første e-post funnet i prompt (regex) |
| `phone` | Første telefonfunnet i prompt (regex, enkel) |
| `customer_name` | Tekst etter nøkkelfrase for kunde-relaterte flyter |
| `notes` | Tekst etter `note:`, `merknad:` eller `kommentar:` |

**Tripletex-relaterte miljøvariabler (valgfritt):**

- `TRIPLETEX_DEFAULT_VAT_TYPE_ID` — mva-type id på fakturalinje (standard `3`, typisk utgående høy sats).
- `TRIPLETEX_INVOICE_LINE_AMOUNT` — beløp eks. mva per linje hvis ikke `… kr` i prompt (standard `100`).
- `TRIPLETEX_DEFAULT_PAYMENT_TYPE_ID` — **påkrevd for `register_payment`**; hentes fra Tripletex for sandbox-kontoen.

### Støttede prompt-typer (rekkefølge: første treff vinner)

| Prompt inneholder | Workflow | API |
|---------------------|----------|-----|
| `list employees`, `ansatte` | Liste ansatte | `GET /employee` |
| `search invoice`, `find invoice`, `søk faktura`, `finn faktura` | Søk fakturaer* | `GET /invoice` |
| `create invoice`, `opprett faktura`, `invoice for customer`, `faktura til kunde` | Opprett faktura til kunde** | `POST /invoice` |
| `find customer`, `search customer`, `finn kunde` | Søk kunde | `GET /customer` |
| `update customer`, `oppdater kunde` | Oppdater kunde*** | `GET /customer/{id}`, `PUT /customer/{id}` |
| `create product`, `opprett produkt`, `nytt produkt` | Opprett produkt | `POST /product` |
| `create customer`, `opprett kunde` | Opprett kunde | `POST /customer` |

\* Krever **fakturanummer** i prompt (f.eks. `finn faktura 10042`) **eller** kundenavn (filtrerer med `customerId` innenfor datointervall siste ~730 dager).

\** Finner kunde med **customer resolver** (søk på navn); ved ingen treff: **400** med tydelig beskjed. Enkel faktura: én ordrelinje; beløp kan angis som `500 kr` i prompt; linjetekst fra `merknad:` / note-felt.

\*** Krever minst ett felt å oppdatere (e-post/telefon i teksten, eller `navn:` / `name:` for nytt firmanavn).

Ved manglende felt / ingen treff der det kreves: **400**. Ved Tripletex-feil: **502** med kort `detail` fra API.

## Deploy til Google Cloud Run

Forutsetter `gcloud` innlogget og et GCP-prosjekt valgt.

1. **Bygg og push image** (erstatt `PROJECT_ID` og ønsket repo/navn):

```bash
export PROJECT_ID=your-gcp-project
export SERVICE=ai-accounting-agent
export REGION=europe-north1

gcloud builds submit --tag gcr.io/${PROJECT_ID}/${SERVICE}
```

2. **Deploy tjenesten**:

```bash
gcloud run deploy ${SERVICE} \
  --image gcr.io/${PROJECT_ID}/${SERVICE} \
  --region ${REGION} \
  --platform managed \
  --allow-unauthenticated \
  --port 8080
```

Cloud Run setter `PORT` automatisk; containeren leser `${PORT:-8080}` i `Dockerfile`.

3. Kall den utstedte URL-en med samme `/health` og `/solve` som lokalt.

**Konkurranse (NM i AI) submission-skjema:** Fyll **Endpoint URL** med **`https://<cloud-run-service-url>/solve`** (full path til **`/solve`**). **API Key** kan stå **tom** med denne appen (ingen innebygd nøkkel på `/solve`). Se **`PROJECT_STATE.md` §19** for sjekkliste og detaljer.

**Artifact Registry + bytte image på eksisterende Cloud Run-tjeneste:** Eksakte **`gcloud`**-kommandoer, image-URL og test-curl — **`PROJECT_STATE.md` §20**.

### Merk

- Legg ikke ekte `session_token` i image eller kildekode; bruk hemmeligheter (Secret Manager / env på Cloud Run) når dere går videre.
- Skaleringsgrenser og CPU minne velges med `gcloud run deploy` (`--memory`, `--cpu`, `--max-instances`, osv.) etter behov.

## Prosjektstruktur

| Fil | Formål |
|-----|--------|
| `main.py` | FastAPI-app, `/health`, `/solve` |
| `tripletex_client.py` | HTTP-klient med Basic Auth (`0` + token) |
| `planner.py` | Regelbasert ruting + enkle felt ut fra prompt |
| `workflows.py` | Tripletex-workflows |
| `customer_resolver.py` | Søk kunde på navn + «best match» |
| `tripletex_request.py` | HTTP-kall + logging (uten token) |
| `tripletex_errors.py` | ApiError-parsing (`TripletexAPIError`) |
| `file_parser.py` | Base64 → filer under `/tmp/uploads` |
| `request_context.py` | `request_id` for korrelerte logger (contextvar) |
| `schemas.py` | Pydantic-modeller for `/solve`-body |
| `examples/` | Eksempel-JSON til curl (plassholdere for token) |
| `requirements.txt` | Python-avhengigheter |
| `Dockerfile` | Produksjonsimage for Cloud Run |

## Neste utviklingstrinn (i koden markert med `TODO`)

- LLM / rikere enhetsuttrekk (orgnr, kontonummer, økonomiske felt på faktura).
- Finere oppløsning ved flere kundenavn-treff (disambiguering).
- Paginering og robust PUT-kunde (kun muterbare felt).
