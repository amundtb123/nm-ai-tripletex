"""Build an execution plan from the user prompt: exact rules → regex fallback → optional LLM router (Spor B)."""

from __future__ import annotations

import re
from typing import Literal, Optional

from pydantic import BaseModel, Field

IntentKind = Literal["create", "update", "delete", "search", "invoice", "payment", "unknown"]
PlannerMode = Literal["exact_rule", "regex_fallback", "llm", "noop"]
WorkflowKind = Literal[
    "list_employees",
    "search_invoice",
    "register_payment",
    "create_invoice_for_customer",
    "search_customer",
    "update_customer",
    "search_product",
    "create_product",
    "create_customer",
    "noop",
]

_WORKFLOW_TARGET: dict[WorkflowKind, str] = {
    "list_employees": "employee",
    "search_invoice": "invoice",
    "register_payment": "payment",
    "create_invoice_for_customer": "invoice",
    "search_customer": "customer",
    "update_customer": "customer",
    "search_product": "product",
    "create_product": "product",
    "create_customer": "customer",
    "noop": "",
}

# First matching rule wins (ordered by specificity / product needs).
_WORKFLOW_RULES: tuple[tuple[WorkflowKind, tuple[str, ...]], ...] = (
    (
        "list_employees",
        (
            "list employees",
            "find employees",
            "show employees",
            "get employees",
            "ansatte",
            "liste over ansatte",
            "oversikt over ansatte",
            "alle ansatte",
            "hvem jobber",
            "ansattliste",
            "medarbeiderliste",
            "vis ansatte",
            "personalet",
            "hvem er de ansatte",
            "ansatte i bedriften",
        ),
    ),
    ("search_invoice", ("search invoice", "find invoice", "søk faktura", "finn faktura")),
    (
        "register_payment",
        (
            "register payment",
            "pay invoice",
            "registrer betaling",
            "betal faktura",
        ),
    ),
    (
        "create_invoice_for_customer",
        ("create invoice", "opprett faktura", "invoice for customer", "faktura til kunde"),
    ),
    (
        "search_customer",
        (
            "find customer",
            "search customer",
            "finn kunde",
            "søk kunde",
            "kundeoppslag",
            "finn kunden",
            "søk etter kunde",
        ),
    ),
    ("update_customer", ("update customer", "oppdater kunde")),
    (
        "search_product",
        (
            "search product",
            "find product",
            "søk produkt",
            "finn produkt",
            "list products",
            "liste produkter",
            "søk vare",
            "vareoppslag",
            "finn varen",
            "søk etter vare",
            "søk etter produkt",
            "hva er prisen",
            "pris på",
            "på lager",
            "sjekk om vi har",
        ),
    ),
    (
        "create_product",
        (
            "create product",
            "opprett produkt",
            "opprett et nytt produkt",
            "opprett nytt produkt",
            "nytt produkt",
            "ny vare",
            "legg til produkt",
            "registrer produkt",
        ),
    ),
    (
        "create_customer",
        (
            "create customer",
            "opprett kunde",
            "ny kunde",
            "registrer kunde",
            "legg til kunde",
        ),
    ),
)

_INVOICE_AUTOPRODUCT_PHRASES: tuple[str, ...] = (
    "opprett produkt hvis mangler",
    "opprett vare hvis mangler",
    "create product if missing",
)


def _invoice_wants_autocreate_product(prompt: str) -> bool:
    low = prompt.lower()
    return any(p in low for p in _INVOICE_AUTOPRODUCT_PHRASES)


def _split_invoice_tail_customer_product(tail: str) -> tuple[str, str]:
    """Split ``tail`` into (customer-ish text, product-ish text) using weak delimiters."""
    t = tail.strip()
    for sep in (r"\bprodukt\s*:\s*", r"\bvare\s*:\s*", r"\bproduct\s*:\s*"):
        m = re.search(sep, t, re.IGNORECASE)
        if m:
            return _clean_tail(t[: m.start()]), _clean_tail(t[m.end() :])
    m = re.search(r"\s+\bprodukt\b\s+", t, re.IGNORECASE)
    if m:
        return _clean_tail(t[: m.start()]), _clean_tail(t[m.end() :])
    m = re.search(r"\s+\bvare\b\s+", t, re.IGNORECASE)
    if m:
        return _clean_tail(t[: m.start()]), _clean_tail(t[m.end() :])
    return _clean_tail(t), ""


def _trim_customer_name_at_product_boundary(name: str) -> str:
    """
    ``kunde:`` bruker ``[^\\n,]+``; uten komma kan hele halen (inkl. «produkt …») bli med.
    Klipp før første tydelige «produkt» / «vare» / «product»-ledd.
    """
    s = name.strip()
    if not s:
        return s
    m = re.search(
        r"\s+\b(?:produkt|vare|product)\b(?:\s*[;\s]|:\s*|\s+)",
        s,
        re.IGNORECASE,
    )
    if m:
        return _clean_tail(s[: m.start()])
    return s


def _extract_invoice_number_for_payment(text: str) -> str:
    m = re.search(
        r"(?:faktura|invoice)\s*(?:nr|no|nummer)?\s*[:#]?\s*(\d{3,})",
        text,
        re.IGNORECASE,
    )
    if m:
        return m.group(1)
    m = re.search(
        r"(?:fakturanummer|invoice\s*number)\s*[:=]\s*(\d{3,})",
        text,
        re.IGNORECASE,
    )
    return m.group(1) if m else ""


def _extract_payment_date_iso(text: str) -> str:
    m = re.search(
        r"(?:betalingsdato|payment\s*date|betalt\s*dato|dato)\s*[:=]\s*(\d{4}-\d{2}-\d{2})\b",
        text,
        re.IGNORECASE,
    )
    if m:
        return m.group(1)
    m = re.search(
        r"(?:betalingsdato|payment\s*date|betalt\s*dato|dato)\s*[:=]\s*(\d{1,2})\.(\d{1,2})\.(\d{4})\b",
        text,
        re.IGNORECASE,
    )
    if m:
        d, mo, y = m.groups()
        return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
    return ""


def _payment_amount_from_text(text: str) -> float | None:
    v = _parse_product_price_nok(text)
    if v is not None:
        return v
    m = re.search(
        r"(?:beløp|amount|sum)\s*[:=]\s*(\d+(?:[.,]\d+)?)\s*(?:kr|nok)?",
        text,
        re.IGNORECASE,
    )
    if m:
        return float(m.group(1).replace(",", "."))
    return None


def _strip_tail_for_payment_customer(tail: str, invoice_number: str) -> str:
    s = tail
    if invoice_number:
        s = re.sub(
            rf"faktura\s*(?:nr|no|nummer)?\s*[:#]?\s*{re.escape(invoice_number)}",
            " ",
            s,
            flags=re.IGNORECASE,
        )
    s = re.sub(r"\d+(?:[.,]\d+)?\s*(?:kr|nok)\b", " ", s, flags=re.IGNORECASE)
    return _clean_tail(s)


_INTENT_RULES: tuple[tuple[IntentKind, tuple[str, ...]], ...] = (
    ("payment", ("betaling", "payment", "innbetaling", "utbetaling", "betale")),
    ("invoice", ("faktura", "invoice")),
    ("delete", ("slett", "delete", "fjern")),
    ("create", ("opprett", "create", "legg til", "ny kunde", "nytt")),
    ("update", ("oppdater", "update", "endre")),
    ("search", ("søk", "search", "finn", "liste", "hent", "oppslag")),
)

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(
    r"(?:(?:\+|00)\d{1,3}[-.\s]?)?(?:\d{2,4}[-.\s]?){2,5}\d{2,4}",
    re.IGNORECASE,
)


def _classify_intent(prompt: str) -> IntentKind:
    text = prompt.strip().lower()
    if not text:
        return "unknown"
    for intent, keywords in _INTENT_RULES:
        for kw in keywords:
            if kw in text:
                return intent
    return "unknown"


def _clean_tail(tail: str) -> str:
    s = tail.strip().strip(":,").strip("«»").strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "'\"":
        s = s[1:-1].strip()
    return s


def _extract_email(text: str) -> str:
    m = _EMAIL_RE.search(text)
    return m.group(0) if m else ""


def _extract_phone(text: str) -> str:
    m = _PHONE_RE.search(text)
    if not m:
        return ""
    return re.sub(r"\s+", " ", m.group(0).strip())[:40]


def _extract_notes(text: str) -> str:
    m = re.search(r"(?:note|merknad|kommentar)\s*:\s*([^\n]+)", text, re.IGNORECASE)
    return m.group(1).strip() if m else ""


def _extract_label_value(text: str, *labels: str) -> str:
    for lab in labels:
        m = re.search(rf"(?:{re.escape(lab)})\s*[:=]\s*([^\n,]+)", text, re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return ""


def _strip_email_phone_chunks(text: str) -> str:
    s = _EMAIL_RE.sub(" ", text)
    s = _PHONE_RE.sub(" ", s)
    return _clean_tail(s)


def _parse_product_price_nok(text: str) -> float | None:
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:kr|nok)\b", text, re.IGNORECASE)
    if not m:
        return None
    return float(m.group(1).replace(",", "."))


def _extract_product_code(text: str) -> str:
    for lab in ("varenummer", "produktnummer", "product number", "produktnr", "kode"):
        m = re.search(rf"(?:{re.escape(lab)})\s*[:#=]\s*([^\s,]+)", text, re.IGNORECASE)
        if m:
            return m.group(1).strip()
    m = re.search(r"(?:^|[\s,])nr\s*[:#=]\s*([^\s,]+)", text, re.IGNORECASE)
    return m.group(1).strip() if m else ""


# After strict substring triggers: natural-language prompts may separate verb and entity
# (e.g. "list all employees …") so "list employees" never matches as one contiguous substring.
_LIST_EMPLOYEES_ENTITY_RE = re.compile(
    r"\b(employees?|ansatte?|medarbeider(?:e)?|personell|kollegaer|kolleger)\b",
    re.IGNORECASE,
)
_LIST_EMPLOYEES_VERB_RE = re.compile(
    r"\b(list|find|show|get|display|retrieve|fetch|vis|finn|hent|oversikt|alle|who|whom|hvem|tell|gi\s+meg|ta\s+ut)\b",
    re.IGNORECASE,
)


def _list_employees_fallback_tokens(prompt: str) -> tuple[str, str] | None:
    """
    Word-boundary verb + employee-entity match (non-adjacent OK).
    Returns (verb_token, entity_token) lowercased for logging, or None.
    """
    em = _LIST_EMPLOYEES_ENTITY_RE.search(prompt)
    vm = _LIST_EMPLOYEES_VERB_RE.search(prompt)
    if not em or not vm:
        return None
    return (vm.group(1).lower(), em.group(1).lower())


# create_customer: natural prompts split "create" / "new" and "customer" / "kunde"
_CREATE_CUSTOMER_ENTITY_RE = re.compile(
    r"\b(customers?|client|clients|kunde|kunder)\b",
    re.IGNORECASE,
)
_CREATE_CUSTOMER_VERB_RE = re.compile(
    r"\b(create|add|register|new|opprett)\b",
    re.IGNORECASE,
)


def _tail_after_last_customer_entity(prompt: str) -> str:
    last = None
    for m in _CREATE_CUSTOMER_ENTITY_RE.finditer(prompt):
        last = m
    if not last:
        return ""
    return _clean_tail(prompt[last.end() :])


def _substantive_tail_for_customer_fallback(tail: str) -> bool:
    """True if tail has at least two word characters (not just «.» or whitespace)."""
    t = _clean_tail(tail)
    if len(t) < 2:
        return False
    return bool(re.search(r"\w", t, re.UNICODE))


def _customer_name_fallback_tail(prompt: str) -> str:
    """Label or text after last customer-entity token (shared by create/search fallbacks)."""
    labeled = _extract_label_value(prompt, "kunde", "customer", "name", "navn")
    if labeled.strip():
        return labeled.strip()
    return _tail_after_last_customer_entity(prompt)


def _create_customer_fallback_tokens(prompt: str) -> tuple[str, str] | None:
    """
    Verb + customer-entity; requires a usable name (label or non-empty tail after entity).
    Avoids matching bare «customer» mentions without create-intent or without a name.
    """
    em = _CREATE_CUSTOMER_ENTITY_RE.search(prompt)
    vm = _CREATE_CUSTOMER_VERB_RE.search(prompt)
    if not em or not vm:
        return None
    labeled = _extract_label_value(prompt, "kunde", "customer", "name", "navn")
    tail_after = _tail_after_last_customer_entity(prompt)
    if not labeled.strip() and not _substantive_tail_for_customer_fallback(tail_after):
        return None
    return (vm.group(1).lower(), em.group(1).lower())


def _create_customer_fallback_tail(prompt: str) -> str:
    return _customer_name_fallback_tail(prompt)


# search_* fallbacks: search/find/list/… — create_* runs after search_* in fallback order
_SEARCH_VERB_RE = re.compile(
    r"\b(search|find|finn|finne|søk|lookup|locate|list|fetch|retrieve)\b",
    re.IGNORECASE,
)

_PRODUCT_ENTITY_RE = re.compile(
    r"\b(products?|produkt|produkter|vare|varer)\b",
    re.IGNORECASE,
)


def _tail_after_last_product_entity(prompt: str) -> str:
    last = None
    for m in _PRODUCT_ENTITY_RE.finditer(prompt):
        last = m
    if not last:
        return ""
    return _clean_tail(prompt[last.end() :])


def _product_name_fallback_tail(prompt: str) -> str:
    labeled = _extract_label_value(prompt, "produkt", "vare", "product", "linje")
    if labeled.strip():
        return labeled.strip()
    return _tail_after_last_product_entity(prompt)


def _search_customer_fallback_tokens(prompt: str) -> tuple[str, str] | None:
    """Search-intent verb + customer entity + query tail (same guards as create_customer)."""
    em = _CREATE_CUSTOMER_ENTITY_RE.search(prompt)
    vm = _SEARCH_VERB_RE.search(prompt)
    if not em or not vm:
        return None
    labeled = _extract_label_value(prompt, "kunde", "customer", "name", "navn")
    tail_after = _tail_after_last_customer_entity(prompt)
    if not labeled.strip() and not _substantive_tail_for_customer_fallback(tail_after):
        return None
    return (vm.group(1).lower(), em.group(1).lower())


def _search_product_fallback_tokens(prompt: str) -> tuple[str, str] | None:
    """Search-intent verb + product entity + name/code signal."""
    em = _PRODUCT_ENTITY_RE.search(prompt)
    vm = _SEARCH_VERB_RE.search(prompt)
    if not em or not vm:
        return None
    labeled = _extract_label_value(prompt, "produkt", "vare", "product", "linje")
    tail_after = _tail_after_last_product_entity(prompt)
    if (
        not labeled.strip()
        and not _substantive_tail_for_customer_fallback(tail_after)
        and not _extract_product_code(prompt)
    ):
        return None
    return (vm.group(1).lower(), em.group(1).lower())


def _create_product_fallback_tokens(prompt: str) -> tuple[str, str] | None:
    """Create-intent verb + product entity + name tail (same verb set as create_customer)."""
    em = _PRODUCT_ENTITY_RE.search(prompt)
    vm = _CREATE_CUSTOMER_VERB_RE.search(prompt)
    if not em or not vm:
        return None
    labeled = _extract_label_value(prompt, "produkt", "vare", "product", "linje")
    tail_after = _tail_after_last_product_entity(prompt)
    if not labeled.strip() and not _substantive_tail_for_customer_fallback(tail_after):
        return None
    return (vm.group(1).lower(), em.group(1).lower())


def _strip_product_metadata(tail: str) -> str:
    """Remove common inline price / code fragments to keep a cleaner display name."""
    s = tail
    s = re.sub(
        r"\b(?:pris|price|ex\s*mva)\s*[:=]?\s*\d+(?:[.,]\d+)?\s*(?:kr|nok)?\b",
        " ",
        s,
        flags=re.IGNORECASE,
    )
    s = re.sub(
        r"(?:varenummer|produktnummer|product number|produktnr|kode|nr)\s*[:#=]\s*[^\s,]+",
        " ",
        s,
        flags=re.IGNORECASE,
    )
    return _clean_tail(s)


def _select_workflow(
    prompt: str,
) -> tuple[WorkflowKind, str, str | None, str]:
    """
    Returns (workflow, tail, route_kind, route_detail).

    route_kind: \"exact\" (substring trigger), \"fallback\" (word-based), or None (noop).
    route_detail: matched trigger phrase (exact) or \"verb=…|entity=…\" (fallback); empty for noop.
    Fallback order after strict rules: list_employees → search_customer → create_customer →
    search_product → create_product (each requires verb+entity regex matches; see helpers above).
    **search_customer before create_customer** so «finn/søk kunde …» wins over «ny/opprett»-ord
    when both patterns could match.
    """
    lower = prompt.lower()
    for kind, triggers in _WORKFLOW_RULES:
        for trig in triggers:
            pos = lower.find(trig)
            if pos < 0:
                continue
            tail = prompt[pos + len(trig) :].strip()
            return kind, tail, "exact", trig[:120]
    toks = _list_employees_fallback_tokens(prompt)
    if toks:
        verb, ent = toks
        return "list_employees", "", "fallback", f"verb={verb}|entity={ent}"
    toks_sc = _search_customer_fallback_tokens(prompt)
    if toks_sc:
        v, e = toks_sc
        return (
            "search_customer",
            _customer_name_fallback_tail(prompt),
            "fallback",
            f"verb={v}|entity={e}",
        )
    toks_cc = _create_customer_fallback_tokens(prompt)
    if toks_cc:
        verb_c, ent_c = toks_cc
        tail_cc = _create_customer_fallback_tail(prompt)
        return (
            "create_customer",
            tail_cc,
            "fallback",
            f"verb={verb_c}|entity={ent_c}",
        )
    toks_sp = _search_product_fallback_tokens(prompt)
    if toks_sp:
        v, e = toks_sp
        return (
            "search_product",
            _product_name_fallback_tail(prompt),
            "fallback",
            f"verb={v}|entity={e}",
        )
    toks_cpr = _create_product_fallback_tokens(prompt)
    if toks_cpr:
        v, e = toks_cpr
        return (
            "create_product",
            _product_name_fallback_tail(prompt),
            "fallback",
            f"verb={v}|entity={e}",
        )
    return "noop", "", None, ""


class Plan(BaseModel):
    raw_prompt: str
    detected_intent: IntentKind
    workflow: WorkflowKind
    target_entity: str = ""
    name: str = ""
    email: str = ""
    phone: str = ""
    customer_name: str = ""
    product_name: str = ""
    product_number: str = ""
    product_price: Optional[float] = None
    invoice_autocreate_product: bool = False
    payment_invoice_number: str = ""
    payment_amount: Optional[float] = None
    payment_date: str = ""
    notes: str = ""
    hints: list[str] = Field(default_factory=list)
    workflow_route: Optional[Literal["exact", "fallback", "llm"]] = None
    workflow_route_detail: str = ""
    planner_mode: PlannerMode = "noop"
    planner_selected_workflow: str = ""
    planner_selected_entity: str = ""
    planner_confidence: Optional[float] = None
    planner_language: Optional[str] = None
    planner_llm_status: Optional[str] = None
    planner_route_detail: str = ""
    planner_heuristic_log: str = ""


def build_plan_rules(prompt: str) -> Plan:
    """
    Keyword routing + light field extraction (regex only).

    When this returns ``noop``, :func:`build_plan` may invoke the LLM router (Spor B).
    """
    intent = _classify_intent(prompt)
    wf, tail, route_kind, route_detail = _select_workflow(prompt)
    target = _WORKFLOW_TARGET[wf]

    email = _extract_email(prompt)
    phone = _extract_phone(prompt)
    notes = _extract_notes(prompt)

    name = ""
    customer_name = ""
    product_name = ""
    product_number = ""
    product_price: float | None = None
    invoice_autocreate_product = False
    payment_invoice_number = ""
    payment_amount: float | None = None
    payment_date = ""

    if wf == "search_product":
        product_name = _strip_product_metadata(tail) or _clean_tail(tail)
        product_number = _extract_product_code(prompt)
        product_price = _parse_product_price_nok(prompt)
    elif wf == "create_product":
        product_name = _strip_product_metadata(tail) or _clean_tail(tail)
        product_number = _extract_product_code(prompt)
        product_price = _parse_product_price_nok(prompt)
        name = product_name
    elif wf == "register_payment":
        labeled_cust = _extract_label_value(prompt, "kunde", "customer")
        inv_no = _extract_invoice_number_for_payment(prompt)
        if not inv_no:
            m = re.search(
                r"\bfaktura\s*(?:nr|no|nummer)?\s*[:#]?\s*(\d{3,})",
                tail,
                re.IGNORECASE,
            )
            if m:
                inv_no = m.group(1)
        payment_invoice_number = inv_no
        payment_amount = _payment_amount_from_text(prompt)
        payment_date = _extract_payment_date_iso(prompt)
        if labeled_cust:
            customer_name = labeled_cust.strip()
        else:
            cust_guess = _strip_tail_for_payment_customer(tail, inv_no)
            m2 = re.search(r"^(.+?)\s+\bfaktura\b", cust_guess, re.IGNORECASE)
            if m2:
                chunk = _clean_tail(m2.group(1))
                customer_name = _strip_email_phone_chunks(chunk) or chunk
            else:
                chunk = _clean_tail(cust_guess)
                customer_name = _strip_email_phone_chunks(chunk) or chunk
    elif wf == "create_invoice_for_customer":
        labeled_cust = _extract_label_value(prompt, "kunde", "customer")
        cust_part, prod_from_split = _split_invoice_tail_customer_product(tail)
        if labeled_cust:
            customer_name = labeled_cust.strip()
        else:
            customer_name = _strip_email_phone_chunks(cust_part) or _clean_tail(cust_part)
        customer_name = _trim_customer_name_at_product_boundary(customer_name)
        labeled_prod = _extract_label_value(prompt, "produkt", "vare", "product", "linje")
        if labeled_prod:
            raw_pname = labeled_prod
        else:
            raw_pname = prod_from_split
        product_name = _strip_product_metadata(raw_pname) or _clean_tail(raw_pname)
        product_number = _extract_product_code(prompt)
        product_price = _parse_product_price_nok(prompt)
        invoice_autocreate_product = _invoice_wants_autocreate_product(prompt)
    elif wf in ("search_customer", "create_customer", "search_invoice"):
        customer_name = _strip_email_phone_chunks(tail) or _clean_tail(tail)
    elif wf == "update_customer":
        raw_tail = _strip_email_phone_chunks(tail) or _clean_tail(tail)
        raw_tail = re.sub(
            r"(?:navn|name)\s*[:=]\s*[^\n,]+",
            " ",
            raw_tail,
            flags=re.IGNORECASE,
        )
        customer_name = _clean_tail(raw_tail)
        name = _extract_label_value(prompt, "navn", "name")

    snippet = re.sub(r"\s+", " ", prompt.strip())[:120]
    hints = [
        "Planner v6: register_payment slots (invoice #, amount, date).",
        f"workflow={wf!r}",
        f"normalized_snippet={snippet!r}",
    ]
    if route_kind:
        hints.append(f"workflow_route={route_kind}")
        if route_detail:
            hints.append(f"workflow_route_detail={route_detail}")

    planner_mode: PlannerMode = "noop"
    if wf != "noop":
        if route_kind == "exact":
            planner_mode = "exact_rule"
        elif route_kind == "fallback":
            planner_mode = "regex_fallback"

    return Plan(
        raw_prompt=prompt,
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
        invoice_autocreate_product=invoice_autocreate_product,
        payment_invoice_number=payment_invoice_number,
        payment_amount=payment_amount,
        payment_date=payment_date,
        notes=notes,
        hints=hints,
        workflow_route=route_kind if route_kind in ("exact", "fallback") else None,
        workflow_route_detail=route_detail,
        planner_mode=planner_mode,
        planner_selected_workflow=wf,
        planner_selected_entity=target,
        planner_confidence=None,
        planner_language=None,
        planner_llm_status=None,
        planner_route_detail=route_detail[:500] if route_detail else "",
        planner_heuristic_log="",
    )


def _noop_plan_with_llm_detail(plan: Plan, detail: str) -> Plan:
    status = "noop"
    if detail == "llm_disabled":
        status = "disabled"
    elif detail == "llm_invalid_response":
        status = "invalid_response"
    elif detail.startswith("low_confidence:"):
        status = "low_confidence"
    elif detail == "llm_chose_noop":
        status = "llm_noop"
    elif detail == "guardrail_rejected_llm_green":
        status = "guardrail_rejected_green"
    return plan.model_copy(
        update={
            "planner_llm_status": status,
            "planner_route_detail": detail[:500],
        }
    )


def build_plan(prompt: str, file_count: int = 0) -> Plan:
    """Rules + regex first; if ``noop``, optionally call LLM router (env-gated)."""
    plan = build_plan_rules(prompt)
    if plan.workflow != "noop":
        return plan
    from planner_llm import try_llm_plan_after_noop_with_detail

    alt, detail = try_llm_plan_after_noop_with_detail(prompt, file_count=file_count)
    if alt is not None:
        return alt
    return _noop_plan_with_llm_detail(plan, detail)
