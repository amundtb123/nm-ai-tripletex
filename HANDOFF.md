# Handoff — NM `POST /solve` 422 og videre score

## Diagnose (kort)

- **422 Unprocessable Entity** fra FastAPI betyr nesten alltid at **Pydantic avviste request-body** før route-handleren (`solve`) kjørte.
- Da ser du **ikke** `request_received` i logg — bare HTTP 422 på linjen `POST /solve`.
- **Typiske årsaker** som tidligere traff den strenge modellen: `extra="forbid"` (ukjente JSON-nøkler), `"files": null`, eller ugyldig JSON / feil `Content-Type`.

## Endringer i repo (denne leveransen)

| Område | Fil | Innhold |
|--------|-----|---------|
| Logging | `main.py` | Middleware som leser `POST /solve`-body; `RequestValidationError`-handler som logger `request_validation_error` med `path`, `validation_errors`, `raw_body` (preview), `request_headers` (redacted). |
| Parsing | `schemas.py` | `extra="ignore"`; `files: null` → `[]`; `prompt` fra tall/bool → streng; `strip_whitespace` der det hjelper. |
| Tester | `tests/test_solve_validation.py` | `TestClient`-tester for tolerant parsing + 422-logg. |
| Avhengighet | `requirements.txt` | `httpx` (kreves av Starlette `TestClient`). |

Kjør tester: `python3 -m unittest discover -s tests -p 'test*.py'` (46 tester etter denne runden).

## Neste steg (score / stabilitet)

1. **Verifiser i Cloud Run:** etter deploy skal **`request_validation_error`** være **sjelden**; når den finnes, bruk loggfeltet til å se **nøyaktig** hvilket felt NM sender feil.
2. **Planner:** fortsett arbeid mot **`llm_noop`** på grønne workflows (heuristikk, terskler, systemprompt) — se `PROJECT_STATE.md` §2.1 og §5.
3. **Ikke** utvid til faktura/betaling i `planner_llm` før grønt scope er stabilt.

Detaljert bakgrunn og tabelloppdateringer: **`PROJECT_STATE.md`** (seksjon *Handoff — NM POST /solve 422*).
