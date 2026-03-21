"""LLM-based router: maps natural-language prompts to structured Plan fields (no Tripletex calls)."""

from __future__ import annotations

import json
import os
import re
from typing import Any, Literal, Optional

import requests
from pydantic import BaseModel, Field, field_validator

LLM_ROUTER_WORKFLOWS = frozenset(
    {
        "list_employees",
        "search_customer",
        "create_customer",
        "search_product",
        "create_product",
        "noop",
    }
)

GREEN_WORKFLOWS = (
    "list_employees",
    "search_customer",
    "create_customer",
    "search_product",
    "create_product",
)

_LLM_CONFIDENCE_MIN = 0.45
# Lower bar + tighter gap → fewer false "ambiguous" noops when a green workflow is clearly ahead.
_HEURISTIC_MIN_SCORE = 2.85
_HEURISTIC_AMBIGUITY_GAP = 0.48
# Only treat as ambiguous when the runner-up is also quite strong (both workflows plausible).
_HEURISTIC_SECOND_STRONG_MIN = 3.15


class LLMRouterJSON(BaseModel):
    """Schema for OpenAI JSON response — must not include API paths or Tripletex details."""

    workflow: Literal[
        "list_employees",
        "search_customer",
        "create_customer",
        "search_product",
        "create_product",
        "noop",
    ]
    confidence: float = Field(ge=0.0, le=1.0, default=0.8)
    language: str = Field(
        default="unknown",
        description="Primary language: no, en, da, sv, de, fr, unknown",
    )
    customer_name: str = ""
    product_name: str = ""
    product_number: str = ""
    extraction_summary: str = Field(
        default="",
        max_length=400,
        description="Short routing rationale; no emails or tokens",
    )

    @field_validator("extraction_summary", "customer_name", "product_name", mode="before")
    @classmethod
    def _strip_email_like(cls, v: Any) -> Any:
        if not isinstance(v, str):
            return v
        return re.sub(
            r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}",
            "[email]",
            v,
        )


def llm_router_enabled() -> bool:
    v = os.environ.get("LLM_PLANNER_ENABLED", "").strip().lower()
    if v not in ("1", "true", "yes", "on"):
        return False
    key = os.environ.get("OPENAI_API_KEY") or os.environ.get("LLM_PLANNER_API_KEY")
    return bool(key and key.strip())


def _openai_chat_json(system: str, user: str) -> dict[str, Any] | None:
    api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("LLM_PLANNER_API_KEY")
    if not api_key:
        return None
    base = os.environ.get("LLM_PLANNER_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.environ.get("LLM_PLANNER_MODEL", "gpt-4o-mini")
    url = f"{base}/chat/completions"
    payload = {
        "model": model,
        "temperature": 0.05,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    try:
        r = requests.post(
            url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=60,
        )
        r.raise_for_status()
        data = r.json()
        content = data["choices"][0]["message"]["content"]
        return json.loads(content)
    except Exception:
        return None


_SYSTEM_PROMPT = """You are a JSON router for an accounting assistant (Tripletex). Pick exactly ONE workflow and extract slots. Never output URLs, HTTP methods, or API paths.

Allowed workflows:
- list_employees — show/list who works here: employees, staff, team, ansatte, medarbeidere, kolleger, "who works", "everyone at the company".
- search_customer — find an EXISTING customer/client (kunde, kunden, client) by name or identifier; "look up", "find", "søk", "finn".
- create_customer — register/add a NEW customer; contact blocks (email + phone), "new client", "registrer kunde", company names with contact info.
- search_product — find an EXISTING product/article (vare, produkt) or by article number.
- create_product — add a NEW product/article to the catalog.
- noop — ONLY when the message cannot reasonably be any of the five above (e.g. pure invoice/payment/bank tasks with no customer/product/employee action, or unrelated chit-chat).

Critical rules:
- **Almost never choose noop** if the user is asking about employees, customers, or products in natural language (any language). Map to the closest workflow.
- If the user wants to **find / search / look up** an **existing** customer (including when email/phone appear as identifying details) → **search_customer**. Email/phone alone do **not** mean create.
- **create_customer** when clearly registering a **new** customer (opprett/ny kunde/registrer/add …) without find/search intent.
- If **coarse_intent** is create and the text is about **new** customer registration (not lookup) → **create_customer**.
- If **product** + **find/search** → **search_product**; **product** + **create/add** → **create_product**.
- **noop** is wrong for: contact details + company/person name, "add client", "new customer", "liste ansatte", "find product".

Slots: customer_name, product_name, product_number (strings; empty if unknown). language: short code. extraction_summary: one short reason (mask emails as [email]). confidence: 0.0–1.0.

Output JSON keys: workflow, confidence, language, customer_name, product_name, product_number, extraction_summary.
"""


def collect_router_signals(raw_prompt: str) -> dict[str, Any]:
    """Deterministic signals for prompts + heuristics (no secrets)."""
    from planner import _classify_intent, _extract_email, _extract_phone, _extract_product_code

    low = raw_prompt.lower()
    intent = _classify_intent(raw_prompt)
    has_email = bool(_extract_email(raw_prompt))
    has_phone = bool(_extract_phone(raw_prompt))
    pcode = _extract_product_code(raw_prompt)

    mentions_customer = bool(
        re.search(
            r"\b(customer|customers|client|clients|kunde|kunden|kunder|kundene|firma|company|"
            r"bedrift|bedrifter|organisasjon|kontakt|kontakter|mottaker|avsender)\b",
            low,
        )
    )
    mentions_product = bool(
        re.search(
            r"\b(product|products|produkt|produkter|vare|varer|article|articles|sku|"
            r"artikkel|artikkelnr|varenummer|lager)\b",
            low,
        )
    )
    mentions_employee = bool(
        re.search(
            r"\b(employee|employees|staff|team|ansatt|ansatte|medarbeider|medarbeidere|kollegaer|colleagues|"
            r"personell|personalet|lønns|timeliste|personal)\b",
            low,
        )
    )
    mentions_find = bool(
        re.search(
            r"\b(find|search|lookup|look\s+up|søk|finn|finne|oppslag|seek|locate)\b",
            low,
        )
    )
    mentions_list = bool(
        re.search(
            r"\b(list|show|display|vis|liste|get|hent|print|give\s+me|oversikt|alle|export)\b",
            low,
        )
    )
    mentions_create = bool(
        re.search(
            r"\b(create|add|register|new|opprett|registrer|legg\s+til|ny\s+kunde|nytt\s+produkt|set\s+up)\b",
            low,
        )
    )
    mentions_who = bool(re.search(r"\b(who|whom|hvem|alle|all|everyone|everybody)\b", low))
    mentions_existing_customer_cue = bool(
        re.search(
            r"\b(eksisterende|allerede|finnes|i\s+systemet|i\s+databasen|existing|fra\s+før|"
            r"registrert|kundekort|kundenummer)\b",
            low,
        )
    )

    return {
        "coarse_intent": intent,
        "has_email_in_text": has_email,
        "has_phone_in_text": has_phone,
        "has_product_code_in_text": bool(pcode),
        "mentions_customer_terms": mentions_customer,
        "mentions_product_terms": mentions_product,
        "mentions_employee_terms": mentions_employee,
        "mentions_find_verbs": mentions_find,
        "mentions_list_or_show_verbs": mentions_list,
        "mentions_create_or_add_verbs": mentions_create,
        "mentions_who_or_all": mentions_who,
        "mentions_existing_customer_cue": mentions_existing_customer_cue,
    }


def _heuristic_blocked(raw_prompt: str) -> bool:
    """Do not override LLM noop for invoice/payment-first prompts (out of green scope)."""
    low = raw_prompt.lower()
    # Clear green-scope routing — never zero-out scores for these (even if "faktura"/"betaling" appear).
    if re.search(
        r"\b(hvem|who)\s+.{0,40}?\b(jobber|ansatt|ansatte|employee|staff|kollegaer|medarbeider|medarbeidere)\b",
        low,
    ):
        return False
    if re.search(
        r"\b(finn|søk|finne|look\s+up|search|vis\s+alle|liste|oversikt|hent)\s+.{0,48}?"
        r"\b(kunde|kunden|kunder|kundene|vare|varen|varer|produkt|produkter|produktet|artikkel|"
        r"ansatte?|medarbeider|medarbeidere|employee|staff|kollegaer)\b",
        low,
    ):
        return False
    if re.search(
        r"\b(registrer\s+betaling|register\s+payment|pay\s+invoice|betal\s+faktura|invoice\s+payment|payment\s+for\s+invoice)\b",
        low,
    ):
        return True
    if re.search(
        r"\b(opprett\s+faktura|create\s+invoice|invoice\s+for\s+customer|new\s+invoice|ny\s+faktura)\b",
        low,
    ):
        return True
    if re.search(r"\b(bank|swift|iban|kid\b|remittance)\b", low) and not re.search(
        r"\b(kunde|customer|produkt|product|ansatt|employee)\b",
        low,
    ):
        return True
    return False


def _score_green_workflows(raw_prompt: str) -> dict[str, float]:
    """Higher = better fit for that workflow."""
    if _heuristic_blocked(raw_prompt):
        return {w: 0.0 for w in GREEN_WORKFLOWS}

    from planner import _classify_intent, _extract_email, _extract_phone, _extract_product_code

    s = collect_router_signals(raw_prompt)
    low = raw_prompt.lower()
    intent = _classify_intent(raw_prompt)
    has_em = s["has_email_in_text"]
    has_ph = s["has_phone_in_text"]
    pcode = _extract_product_code(raw_prompt)

    scores = {w: 0.0 for w in GREEN_WORKFLOWS}

    if s["mentions_employee_terms"]:
        scores["list_employees"] += 4.5
        if s["mentions_list_or_show_verbs"] or s["mentions_find_verbs"] or s["mentions_who_or_all"]:
            scores["list_employees"] += 2.5
        if re.search(r"\b(work|works|jobber|ansett|hire)\b", low):
            scores["list_employees"] += 1.0
        if s["mentions_who_or_all"] and re.search(
            r"\b(employee|staff|ansatt|ansatte|medarbeider|kollegaer|personell)\b",
            low,
        ):
            scores["list_employees"] += 2.2
        if re.search(r"\b(oversikt|liste|alle|everyone|everybody|samtlige)\b", low) and s[
            "mentions_employee_terms"
        ]:
            scores["list_employees"] += 1.8

    if s["mentions_customer_terms"] and s["mentions_create_or_add_verbs"]:
        scores["create_customer"] += 6.0
    # Contact blocks strongly suggest create only when not explicitly searching/looking up.
    if (has_em or has_ph) and s["mentions_customer_terms"] and not s["mentions_find_verbs"]:
        scores["create_customer"] += 5.0
    if (
        has_em
        and has_ph
        and not s["mentions_product_terms"]
        and not s["mentions_employee_terms"]
        and not s["mentions_find_verbs"]
    ):
        scores["create_customer"] += 5.5
    if (
        intent == "create"
        and (has_em or has_ph)
        and not s["mentions_product_terms"]
        and not s["mentions_find_verbs"]
    ):
        scores["create_customer"] += 4.0
    if intent == "create" and s["mentions_customer_terms"]:
        scores["create_customer"] += 3.0
    if s["mentions_customer_terms"] and not s["mentions_find_verbs"] and (has_em or has_ph):
        scores["create_customer"] += 2.5

    if s["mentions_customer_terms"] and s["mentions_find_verbs"]:
        scores["search_customer"] += 6.0
        if not s["mentions_create_or_add_verbs"]:
            scores["search_customer"] += 2.0
    if s["mentions_customer_terms"] and intent == "search":
        scores["search_customer"] += 3.0
    if s.get("mentions_existing_customer_cue") and s["mentions_customer_terms"]:
        scores["search_customer"] += 5.0

    if scores["search_customer"] > 0 and scores["create_customer"] > 0:
        if s["mentions_create_or_add_verbs"] and not s["mentions_find_verbs"]:
            scores["search_customer"] -= 3.0
        elif s["mentions_find_verbs"] and not s["mentions_create_or_add_verbs"]:
            scores["create_customer"] -= 3.0

    if s["mentions_product_terms"] and s["mentions_create_or_add_verbs"]:
        scores["create_product"] += 6.0
    if intent == "create" and s["mentions_product_terms"]:
        scores["create_product"] += 3.0

    if s["mentions_product_terms"] and s["mentions_find_verbs"]:
        scores["search_product"] += 6.0
    if pcode:
        scores["search_product"] += 3.5
    if s["mentions_product_terms"] and intent == "search":
        scores["search_product"] += 2.0

    if re.search(r"\b(faktura|invoice)\s*(nr|no|number|#)?\s*[:#]?\s*\d{3,}", low):
        scores["create_customer"] -= 2.5
        scores["search_customer"] -= 1.0

    for w in GREEN_WORKFLOWS:
        scores[w] = max(0.0, scores[w])

    return scores


def heuristic_green_workflow_after_llm_noop(raw_prompt: str) -> tuple[str, str, float, str] | None:
    """
    When the LLM returned noop, pick a green workflow from deterministic scores if confident enough.
    Returns (workflow, reason, confidence, scores_compact) or None.
    """
    scores = _score_green_workflows(raw_prompt)
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    if not ranked:
        return None
    best_wf, best_s = ranked[0]
    second_s = ranked[1][1] if len(ranked) > 1 else 0.0

    compact = "|".join(f"{k.split('_')[0][:2]}={v:.1f}" for k, v in sorted(scores.items(), key=lambda x: -x[1]))

    if best_s < _HEURISTIC_MIN_SCORE:
        return None
    if best_s - second_s < _HEURISTIC_AMBIGUITY_GAP and second_s >= _HEURISTIC_SECOND_STRONG_MIN:
        return None

    conf = min(0.74, 0.5 + best_s * 0.035)
    reason = f"heuristic_scores winner={best_wf} best={best_s:.1f} second={second_s:.1f}"
    return (best_wf, reason, conf, compact[:400])


def build_llm_router_user_content(raw_prompt: str) -> str:
    """Original prompt + deterministic hints + top heuristic suggestion."""
    s = collect_router_signals(raw_prompt)
    hint_lines = [f"- {k}: {v}" for k, v in s.items()]
    scores = _score_green_workflows(raw_prompt)
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    top_suggestion = (
        f"heuristic_top_workflow={ranked[0][0]} (score {ranked[0][1]:.1f})"
        if ranked
        else "heuristic_top_workflow=none"
    )
    score_line = "heuristic_scores: " + ", ".join(f"{k}={v:.1f}" for k, v in ranked)

    return (
        f"{raw_prompt.strip()}\n\n"
        "---\n"
        "Routing hints (deterministic, no secrets):\n"
        + "\n".join(hint_lines)
        + f"\n- {top_suggestion}\n- {score_line}\n"
        + "You MUST output one of the five green workflows if any hint or the user text plausibly matches "
        "employees, customers, or products. Use noop only for clear out-of-scope requests.\n"
    )


def call_llm_router(prompt: str) -> LLMRouterJSON | None:
    """Call remote LLM; return parsed model or None on failure."""
    user_content = build_llm_router_user_content(prompt)
    raw = _openai_chat_json(_SYSTEM_PROMPT, user_content)
    if raw is None:
        return None
    try:
        obj = LLMRouterJSON.model_validate(raw)
    except Exception:
        return None
    if obj.workflow not in LLM_ROUTER_WORKFLOWS:
        return None
    return obj


def _synthetic_llm_from_heuristic(
    raw_prompt: str,
    workflow: str,
    reason: str,
    confidence: float,
    scores_compact: str,
) -> LLMRouterJSON:
    """Build router JSON after heuristic override of model noop."""
    from planner import _extract_label_value, _extract_product_code, _strip_product_metadata

    customer_name = ""
    product_name = ""
    product_number = _extract_product_code(raw_prompt)

    if workflow in ("create_customer", "search_customer"):
        customer_name = _extract_label_value(raw_prompt, "kunde", "customer", "name", "navn", "firma", "company")
        customer_name = (customer_name or "").strip()
        if not customer_name:
            m = re.search(
                r"\b(?:client|kunde|customer)\s+([A-ZÆØÅa-zæøå][A-Za-zÆØÅæøå0-9\s&\.\-]{1,64}?)(?:\s*[,.]|\s+email|\s+phone|\s+tlf|\s+@|\s*$)",
                raw_prompt,
                re.IGNORECASE,
            )
            if m:
                customer_name = m.group(1).strip()

    if workflow in ("create_product", "search_product"):
        product_name = _extract_label_value(raw_prompt, "produkt", "vare", "product", "article", "linje")
        product_name = _strip_product_metadata(product_name) if product_name else ""
        if not product_name and workflow == "search_product":
            product_name = _strip_product_metadata(raw_prompt)[:120]

    summary = f"noop_override|{reason}|sc={scores_compact[:120]}"
    return LLMRouterJSON(
        workflow=workflow,  # type: ignore[arg-type]
        confidence=confidence,
        language="unknown",
        customer_name=customer_name[:500],
        product_name=product_name[:500],
        product_number=product_number[:80],
        extraction_summary=summary[:400],
    )


def llm_router_json_to_plan(
    raw_prompt: str,
    llm: LLMRouterJSON,
    *,
    heuristic_override: bool = False,
    heuristic_log: str = "",
) -> "Plan":
    """Build Plan from LLM output; re-uses regex helpers for price/code from raw prompt."""
    from planner import (
        Plan,
        WorkflowKind,
        _WORKFLOW_TARGET,
        _classify_intent,
        _extract_email,
        _extract_notes,
        _extract_phone,
        _extract_product_code,
        _parse_product_price_nok,
        _strip_product_metadata,
    )

    wf: WorkflowKind = llm.workflow  # type: ignore[assignment]
    target = _WORKFLOW_TARGET[wf]

    email = _extract_email(raw_prompt)
    phone = _extract_phone(raw_prompt)
    notes = _extract_notes(raw_prompt)

    customer_name = (llm.customer_name or "").strip()
    product_name = (llm.product_name or "").strip()
    product_number = (llm.product_number or "").strip()
    if not product_number:
        product_number = _extract_product_code(raw_prompt)

    product_price = _parse_product_price_nok(raw_prompt)
    name = ""
    if wf == "create_product":
        product_name = _strip_product_metadata(product_name) or product_name
        name = product_name
    elif wf == "search_product":
        product_name = _strip_product_metadata(product_name) or product_name

    intent = _classify_intent(raw_prompt)
    snippet = re.sub(r"\s+", " ", raw_prompt.strip())[:120]
    hint_em = int(bool(_extract_email(raw_prompt)))
    hint_ph = int(bool(_extract_phone(raw_prompt)))
    ov = "1" if heuristic_override else "0"
    detail = (
        f"llm:workflow={wf}|ov={ov}|lang={llm.language}|conf={llm.confidence:.2f}|"
        f"i={intent}|em={hint_em}|ph={hint_ph}|"
        f"{(llm.extraction_summary or '')[:160]}"
    )
    status = "ok_heuristic_override" if heuristic_override else "ok"
    hints = [
        "Planner LLM router (Spor B).",
        f"workflow={wf!r}",
        f"normalized_snippet={snippet!r}",
        f"workflow_route=llm",
        f"workflow_route_detail={detail[:300]}",
    ]
    return Plan(
        raw_prompt=raw_prompt,
        detected_intent=intent,
        workflow=wf,
        target_entity=target,
        name=name,
        email=email,
        phone=phone,
        customer_name=customer_name,
        product_name=product_name,
        product_number=product_number,
        product_price=product_price,
        invoice_autocreate_product=False,
        payment_invoice_number="",
        payment_amount=None,
        payment_date="",
        notes=notes,
        hints=hints,
        workflow_route="llm",
        workflow_route_detail=detail[:500],
        planner_mode="llm",
        planner_selected_workflow=wf,
        planner_selected_entity=target,
        planner_confidence=llm.confidence,
        planner_language=llm.language or None,
        planner_llm_status=status,
        planner_route_detail=detail[:500],
        planner_heuristic_log=heuristic_log[:500],
    )


def try_llm_plan_after_noop_with_detail(raw_prompt: str) -> tuple[Plan | None, str]:
    """
    If LLM enabled and rules returned noop, try LLM.
    Returns (Plan, \"ok\" | \"ok_heuristic_override\") on success, or (None, reason) for logging / safe fallback.
    """
    if not llm_router_enabled():
        return None, "llm_disabled"
    llm = call_llm_router(raw_prompt)
    if llm is None:
        return None, "llm_invalid_response"

    def _plan_from_heuristic_override() -> tuple[Plan, str] | None:
        hw = heuristic_green_workflow_after_llm_noop(raw_prompt)
        if hw is None:
            return None
        wf, reason, conf, compact = hw
        synth = _synthetic_llm_from_heuristic(raw_prompt, wf, reason, conf, compact)
        log_line = f"override_noop->{wf}|{reason}|{compact}"
        plan = llm_router_json_to_plan(
            raw_prompt,
            synth,
            heuristic_override=True,
            heuristic_log=log_line,
        )
        return plan, "ok_heuristic_override"

    # Prefer deterministic scores when the model is noop or under-confident.
    if llm.workflow == "noop" or llm.confidence < _LLM_CONFIDENCE_MIN:
        got = _plan_from_heuristic_override()
        if got is not None:
            return got

    # Keep a green workflow from the LLM even when confidence is below the usual bar.
    if (
        llm.workflow != "noop"
        and llm.workflow in GREEN_WORKFLOWS
        and llm.confidence < _LLM_CONFIDENCE_MIN
    ):
        plan = llm_router_json_to_plan(raw_prompt, llm)
        plan = plan.model_copy(update={"planner_llm_status": "ok_low_confidence_llm"})
        return plan, "ok_low_confidence_llm"

    if llm.confidence < _LLM_CONFIDENCE_MIN:
        return None, f"low_confidence:{llm.confidence:.2f}"
    if llm.workflow == "noop":
        return None, "llm_chose_noop"
    return llm_router_json_to_plan(raw_prompt, llm), "ok"


def try_llm_plan_after_noop(raw_prompt: str) -> Plan | None:
    """Thin wrapper; use :func:`try_llm_plan_after_noop_with_detail` when logging the reason."""
    plan, _ = try_llm_plan_after_noop_with_detail(raw_prompt)
    return plan
