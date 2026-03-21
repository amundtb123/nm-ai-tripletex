# Session wrap-up — AI Accounting Agent (Spor B / `planner_llm`)

> **Formål:** Rask handoff til ny agent eller utvikler. Teknisk detalj og historikk ligger i **`PROJECT_STATE.md`** (§2.1 m.fl.); denne filen er et **kompakt snapshot**.

**Sist oppdatert:** 2026-03-21

---

## Mål som er jobbet mot

- **Minimal LLM-router** foran eksisterende **`workflows.py`** (LLM genererer **ikke** rå Tripletex-kall).
- **Grønne workflows i LLM-scope:** `list_employees`, `search_customer`, `create_customer`, `search_product`, `create_product`.
- **Ikke prioritert i samme spor:** `create_invoice_for_customer`, `register_payment`, bred refaktor av execution-layer.

---

## Arkitektur (nåværende)

1. **`build_plan_rules`** (`planner.py`) — eksakte triggere → regex-fallbacks → ev. `noop`.
2. **`build_plan`** — ved `noop` fra regler: hvis `LLM_PLANNER_ENABLED` + API-nøkkel → **`planner_llm`**.
3. **`planner_llm.py`:**
   - OpenAI-kompatibel `chat/completions` → JSON (`LLMRouterJSON`).
   - **`build_llm_router_user_content`:** original prompt + deterministiske hints + score-linje per grønt workflow.
   - Ved modell-**noop**: **`heuristic_green_workflow_after_llm_noop`** — scorer de fem workflowene; ved tydelig vinner → syntetisk plan, **`planner_llm_status` = `ok_heuristic_override`**.
   - **`_heuristic_blocked`** — reduserer feilrouting fra ren faktura/betalings-formulering.
4. **`workflows.py`** — execution; endret minimalt (planner-sporet bærer hovedløftet).

---

## Viktige filer

| Fil | Rolle |
|-----|--------|
| `planner_llm.py` | LLM-kall, hints, heuristikk, override, mapping til `Plan` |
| `planner.py` | `Plan`, `PlannerMode`, `build_plan_rules` / `build_plan`, `planner_heuristic_log` |
| `main.py` | `plan_built` med `planner_*` + `planner_heuristic_log` |
| `tests/test_planner_llm.py` | Mapping, mock, heuristikk, override |
| `PROJECT_STATE.md` | §2.1 Spor B, drift, NM-observasjoner, justeringer |
| `TESTING.md` | Env, verifisering av `ok` vs `ok_heuristic_override` vs `llm_noop` |

---

## Miljø (Cloud Run / lokal)

- `LLM_PLANNER_ENABLED` = `1` / `true` / `yes` / `on`
- `OPENAI_API_KEY` eller `LLM_PLANNER_API_KEY`
- Valgfritt: `LLM_PLANNER_BASE_URL`, `LLM_PLANNER_MODEL`

**Ingen** API-nøkler i repo.

---

## Logging (`plan_built`)

- `planner_mode`, `planner_selected_workflow`, `planner_selected_entity`, `planner_confidence`, `planner_language`, `planner_llm_status` (`ok` \| `ok_heuristic_override` \| feil/NOOP-varianter), `planner_route_detail`, **`planner_heuristic_log`**.
- Livsløp ellers: `request_received` → … → `request_finished`, `tripletex_http` som før.

---

## Git (referanse)

- Siste kjente store leveranse på `main`: commit **`eb5a844`** — «heuristic override after LLM noop + stronger routing» (verifiser med `git log -1` etter pull).

---

## Tester

```bash
python3 -m unittest discover -s tests -p 'test*.py'
```

(Prosjektet bruker **`unittest`**; `pytest` er ikke påkrevd.)

---

## Kjent gjeld / neste steg

1. Ved vedvarende **`llm_noop`** på tydelige kunde-/kontakt-prompts: se **`planner_heuristic_log`**, vurder terskler i `planner_llm.py` (`_HEURISTIC_MIN_SCORE`, `_HEURISTIC_AMBIGUITY_GAP`) og scoring — ikke nødvendigvis mer regex utenfor `planner_llm`.
2. Faktura/betaling er bevisst **utenfor** LLM-router-scope i denne strategien.
3. Oppdater **`PROJECT_STATE.md`** når atferd eller prioritet endres vesentlig; oppdater **denne filen** ved store økter / før NM-submit.

---

## Hva vi faktisk prøvde (kort historikk)

- Skifte fra ren regex-først til **lagdelt** regler + **LLM** + **heuristisk sikkerhetsnett** når modellen returnerer `noop`.
- Dokumentert NM-feilmønstre (`llm_noop`, score 0/7 → senere f.eks. 2/8) — problem forstått som **routing**, ikke primært deploy/proxy.
- Push til `origin/main` gjennomført (se git over).

---

*For full arkitektur og tabeller, se **`PROJECT_STATE.md`**. For manuell verifisering, se **`TESTING.md`**. *
