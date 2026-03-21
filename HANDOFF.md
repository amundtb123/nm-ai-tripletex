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

Kjør tester: `python3 -m unittest discover -s tests -p 'test*.py'` (55 tester etter noop-reduksjon 2026-03-21).

## Noop-reduksjon (2026-03-21) — score

Se **`PROJECT_STATE.md` §2.1** *Justering (runde 3 — noop-reduksjon)*. Kort: flere **norske** eksakte triggere og **`list_employees`**-fallback; **`planner_llm`**: lavere heuristikkterskler, bedre **`_heuristic_blocked`**-presisjon, **`ok_low_confidence_llm`**, heuristikk **før** `low_confidence`-avvisning når modellen er noop/usikker.

## Neste steg (score / stabilitet)

1. **Verifiser i Cloud Run:** etter deploy skal **`request_validation_error`** være **sjelden**; når den finnes, bruk loggfeltet til å se **nøyaktig** hvilket felt NM sender feil.
2. **Planner:** monitorer **`planner_llm_status`** (`llm_noop`, `ok_heuristic_override`, `ok_low_confidence_llm`) og **`noop`**-rate på NM — se `PROJECT_STATE.md` §2.1.
3. **Ikke** utvid til faktura/betaling i `planner_llm` før grønt scope er stabilt.

Detaljert bakgrunn: **`PROJECT_STATE.md`** (422-handoff + §2.1 runde 3).
