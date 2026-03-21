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

_LLM_CONFIDENCE_MIN = 0.45


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
        "temperature": 0.1,
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


_SYSTEM_PROMPT = """You are a JSON router for an accounting assistant (Tripletex backend). Your job is to pick ONE workflow and extract slots. Do NOT output API URLs, HTTP methods, or Tripletex paths.

Allowed workflows (only these):
- list_employees: list/show/find who works at the company (employees, staff, ansatte, medarbeidere).
- search_customer: find or look up an EXISTING customer/client (kunde) by name or identifier.
- create_customer: register or add a NEW customer/client; often includes name, phone, email, address.
- search_product: find or look up an EXISTING product/article (vare/produkt).
- create_product: add a NEW product/article to the catalog.
- noop: ONLY use when the message is clearly NOT about any of the above (e.g. unrelated topic, payment/invoice-only request, or empty noise). Do NOT choose noop just because wording is informal or multilingual.

Routing priorities (avoid unnecessary noop):
- If the user wants to add/register a new customer (or gives contact details for a new client) → create_customer. Mention of phone, email, or "new customer" supports this.
- If they want to find/search an existing customer → search_customer.
- If they want to add a new product or article → create_product; if they want to find a product → search_product.
- If they want a list of employees/staff → list_employees.
- When in doubt between two of these five, prefer the workflow that matches the strongest cue (create vs search, customer vs product); use noop only when none of the five fit.

Slots:
- Fill customer_name / product_name / product_number when you can infer them from the text; otherwise empty strings.
- language: short hint (no, en, da, sv, de, unknown, …).
- extraction_summary: one short sentence (no secrets; mask emails as [email] if needed).
- confidence: 0.0–1.0 for your workflow choice.

Output a single JSON object with keys: workflow, confidence, language, customer_name, product_name, product_number, extraction_summary.
"""


def build_llm_router_user_content(raw_prompt: str) -> str:
    """
    Original prompt plus non-secret heuristic hints so the model routes create/search cases
    that regex missed (e.g. NM natural language + phone/email).
    """
    from planner import _classify_intent, _extract_email, _extract_phone

    intent = _classify_intent(raw_prompt)
    has_email = bool(_extract_email(raw_prompt))
    has_phone = bool(_extract_phone(raw_prompt))
    low = raw_prompt.lower()
    mentions_customer = bool(
        re.search(
            r"\b(customer|customers|client|clients|kunde|kunden|kunder|kundene|firma|company)\b",
            low,
            re.IGNORECASE,
        )
    )
    mentions_product = bool(
        re.search(r"\b(product|products|produkt|produkter|vare|varer|article|articles)\b", low)
    )
    mentions_employee = bool(
        re.search(
            r"\b(employee|employees|staff|team|ansatt|ansatte|medarbeider|medarbeidere|kollegaer)\b",
            low,
        )
    )
    mentions_find = bool(
        re.search(
            r"\b(find|search|lookup|look\s+up|søk|finn|oppslag|liste|list|show|display|vis|hent)\b",
            low,
        )
    )
    mentions_create = bool(
        re.search(
            r"\b(create|add|register|new|opprett|registrer|legg\s+til|ny\s+kunde|ny\s+kunder|nytt\s+produkt)\b",
            low,
        )
    )
    return (
        f"{raw_prompt.strip()}\n\n"
        "---\n"
        "Routing hints (deterministic, no secrets):\n"
        f"- coarse_intent: {intent}\n"
        f"- has_email_in_text: {has_email}\n"
        f"- has_phone_in_text: {has_phone}\n"
        f"- mentions_customer_terms: {mentions_customer}\n"
        f"- mentions_product_terms: {mentions_product}\n"
        f"- mentions_employee_terms: {mentions_employee}\n"
        f"- mentions_find_or_list_verbs: {mentions_find}\n"
        f"- mentions_create_or_add_verbs: {mentions_create}\n"
        "Use these hints together with the user text. Prefer a green workflow when cues align; "
        "use noop only when the request is outside employee/customer/product tasks."
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


def llm_router_json_to_plan(raw_prompt: str, llm: LLMRouterJSON) -> "Plan":
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
    detail = (
        f"llm:workflow={wf}|lang={llm.language}|conf={llm.confidence:.2f}|"
        f"i={intent}|em={hint_em}|ph={hint_ph}|"
        f"{(llm.extraction_summary or '')[:180]}"
    )
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
        planner_llm_status="ok",
        planner_route_detail=detail[:500],
    )


def try_llm_plan_after_noop_with_detail(raw_prompt: str) -> tuple[Plan | None, str]:
    """
    If LLM enabled and rules returned noop, try LLM.
    Returns (Plan, \"ok\") on success, or (None, reason) for logging / safe fallback.
    """
    if not llm_router_enabled():
        return None, "llm_disabled"
    llm = call_llm_router(raw_prompt)
    if llm is None:
        return None, "llm_invalid_response"
    if llm.confidence < _LLM_CONFIDENCE_MIN:
        return None, f"low_confidence:{llm.confidence:.2f}"
    if llm.workflow == "noop":
        return None, "llm_chose_noop"
    return llm_router_json_to_plan(raw_prompt, llm), "ok"


def try_llm_plan_after_noop(raw_prompt: str) -> Plan | None:
    """Thin wrapper; use :func:`try_llm_plan_after_noop_with_detail` when logging the reason."""
    plan, _ = try_llm_plan_after_noop_with_detail(raw_prompt)
    return plan
