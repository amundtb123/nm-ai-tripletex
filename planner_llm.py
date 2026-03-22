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
# When the prompt is a clear standalone CRM/product/employee task, allow a slightly lower bar for override.
_HEURISTIC_MIN_SCORE_STANDALONE = 2.48
# Second pass when standard+standalone thresholds still fail but relax_eligible (not OOS).
_HEURISTIC_MIN_SCORE_RELAXED = 2.10
_HEURISTIC_AMBIGUITY_GAP = 0.48
# Only treat as ambiguous when the runner-up is also quite strong (both workflows plausible).
_HEURISTIC_SECOND_STRONG_MIN = 3.15
# Relaxed ambiguity: smaller gap → fewer "tie" rejections; lower bar for runner-up being "strong".
_HEURISTIC_AMBIGUITY_GAP_RELAXED = 0.30
_HEURISTIC_SECOND_STRONG_MIN_RELAXED = 2.58
# Third pass: only when _weak_green_recall_eligible (OOS false, blocked false, weak consistent signals).
_HEURISTIC_MIN_SCORE_WEAK = 1.72
# Slightly lower when HTTP files are present: NM often prepends «vedlegg» lines; scores still guarded.
_HEURISTIC_MIN_SCORE_WEAK_ATTACHED = 1.62
_HEURISTIC_AMBIGUITY_GAP_WEAK = 0.22
_HEURISTIC_SECOND_STRONG_MIN_WEAK = 2.35


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
    entity: Literal["customer", "product", "employees", "unknown"] = Field(
        default="unknown",
        description="Primary entity for logging; align with workflow",
    )
    reason: str = Field(
        default="",
        max_length=120,
        description="Very short routing reason (5–12 words) for logs",
    )
    customer_name: str = ""
    product_name: str = ""
    product_number: str = ""
    extraction_summary: str = Field(
        default="",
        max_length=400,
        description="Optional extra slot notes; no emails or tokens",
    )

    @field_validator("extraction_summary", "customer_name", "product_name", "reason", mode="before")
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


def llm_green_first_enabled() -> bool:
    """
    When True (default) and :func:`llm_router_enabled`, :func:`planner.build_plan`
    calls the LLM before rules for green-scope routing. Set ``LLM_PLANNER_GREEN_FIRST=0``
    to restore rules-first ordering.
    """
    if not llm_router_enabled():
        return False
    v = os.environ.get("LLM_PLANNER_GREEN_FIRST", "1").strip().lower()
    if v in ("0", "false", "no", "off"):
        return False
    return True


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


_SYSTEM_PROMPT = """You are a JSON router for a Tripletex accounting assistant. Output ONE workflow and extract slots. Never output URLs, HTTP methods, or API paths.

Allowed workflows (green scope only — each maps to a single CRM/catalog/employee action):
- list_employees — list who works at the company (ansatte, staff, employees).
- search_customer — find or verify an EXISTING customer (finn, søk, look up, including when email/phone identify who to find).
- create_customer — register a clearly NEW customer (opprett/registrer/ny kunde when not a lookup).
- search_product — find or check an EXISTING product: price, stock, article/SKU, «på lager», «hva koster».
- create_product — add a NEW product to the catalog (opprett/nytt produkt with article data).
- noop — use when the user request is NOT fully satisfied by exactly one of the workflows above. This includes: general ledger / journal / account analysis, reporting, trends, variance, budgets, KPIs, tax or period commentary, or any ask that is mainly «analyze / explain / compare / report» rather than «find this customer or product» or «list employees». Also: invoice issuance, payment registration, disputes, projects, payroll detail, period close — unless the ONLY ask is a standalone CRM line (find/register customer or product). Choosing noop for a clear standalone find/list/register of customer, product, or employees is wrong; choosing green for pure analysis/reporting is wrong.

The HTTP request may include attached files; file bytes are NOT shown to you. Route ONLY from the text. The JSON field attachments_count is metadata only — it does not add or remove task scope. Do NOT choose noop solely because attachments_count > 0 or the text mentions an attachment, «vedlegg», or «invoice attached» when the actual task is still a CRM/catalog/employee lookup.

Out-of-scope (always noop — do NOT map to green even if customer/product/email/price appear):
- Reporting & books: general ledger, hovedbok, journal entries, account balances, cost/revenue trends, variance, «identify the top N accounts», month-over-month analysis.
- Invoice/payment workflows: disputes, reminders, overdue, fees, KID, «faktura til kunde» as a billing task, registering payment.
- Projects, payroll, monthly/period close, accruals/reversals — unless the ONLY ask is explicitly to find/register a customer or product as a CRM task (see standalone rule below).

Standalone green tasks (these stay in green scope):
- Explicitly: find/search/list/show customer or product or employees; register new customer or new product; price/stock questions for a catalog article; «who are the employees».

Decision procedure (follow in order):
1) If the message is mainly reporting/GL/analysis/budget/trends OR invoice/payment/project/payroll/period-close (and not a standalone CRM line below) → noop.
2) Entity: employees, customer, product, or none.
3) If employees → list_employees.
4) If customer or product: decide lookup vs create.
5) Prefer search_* when the user asks to find/check/verify/price/stock/existing, or gives identifying details without clear «new/register/create» for that entity.
6) Prefer create_* only when the user clearly asks to add/register/create a new customer or product.
7) If unsure between search and create → choose search_*.
8) noop when step 1 applies or no supported entity is plausible.

Search-over-create (hard rules):
- Email, phone, or address alone do NOT imply create_customer; with «finn/søk/look up» they support search_customer.
- Price, stock, SKU, or product details alone do NOT imply create_product; price/stock questions → search_product.
- Ambiguous → search_*.

Slot extraction (when routing to search_customer or create_customer):
- Fill customer_name with the company/person name from the prompt. If the text uses Spanish «el cliente / la cliente …» or French «le client …» before the name, put that name in customer_name (stop before «(» or verbs like tiene/hay). Same for «Finn kunden X», «customer: X».
- If there is still no name but a non-generic business email is present (e.g. kontakt@firma-as.no), set customer_name from the domain segment (firma-as.no → «Firma As» or similar) — never leave customer_name empty for create_customer when any identifiable email domain exists.

Contrast (mapping hints):
- «Finn kunde Ola Bygg AS med e-post post@ola.no» → search_customer
- «Registrer ny kunde Ola Bygg AS med e-post post@ola.no» → create_customer
- «Hva er prisen på SuperWidget 3000?» → search_product
- «Har vi SuperWidget 3000 på lager?» → search_product
- «Opprett et nytt produkt SuperWidget 3000 til 199 kr» → create_product
- «Hvem er de ansatte i bedriften?» → list_employees
- «Registrer betaling på faktura 12345» → noop
- «Invoice is wrong for customer Acme AS» → noop (complaint, not CRM)
- «Create project linked to customer X» → noop
- «Payroll email for employee» → noop
- «Analyze the general ledger and identify the three largest expense accounts» → noop
- «Total costs increased from January to February — explain» → noop

The user message includes deterministic signals (hints). They are auxiliary — apply the decision procedure above; do not blindly copy the top heuristic.

Slots: customer_name, product_name, product_number (empty if unknown). language: short code. confidence: 0.0–1.0.
entity: customer | product | employees | unknown (primary entity you used).
reason: 5–12 words, why this workflow (for logs; mask emails as [email]).
extraction_summary: optional short extra note.

Output JSON keys: workflow, confidence, language, entity, reason, customer_name, product_name, product_number, extraction_summary.
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
            r"\b(customer|customers|client|clients|cliente|clientes|kunde|kunden|kunder|kundene|"
            r"firma|company|bedrift|bedrifter|organisasjon|kontakt|kontakter|mottaker|avsender)\b",
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
            r"\b(find|search|lookup|look\s+up|fetch|retrieve|pull\s+up|"
            r"søk|finn|finne|oppslag|seek|locate|sjekk)\b",
            low,
        )
    )
    mentions_list = bool(
        re.search(
            r"\b(list|show|display|vis|liste|get|hent|print|give\s+me|oversikt|alle|export)\b",
            low,
        )
    )
    # «the register» / «a register» is usually the noun (cash/customer book), not «register a customer».
    mentions_create = bool(
        re.search(
            r"\b(create|add|new|opprett|registrer|legg\s+til|ny\s+kunde|nytt\s+produkt|set\s+up)\b",
            low,
        )
        or (
            re.search(r"\bregister\b", low)
            and not re.search(r"\b(?:the|a|an)\s+register\b", low)
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
    # Pris/lager uten ordet «produkt» (typisk NM: «Hva er prisen på 'X'?»)
    mentions_price_or_stock_lookup = bool(
        re.search(
            r"\b(hva\s+er\s+prisen|pris\s+på|prisen\s+på|pris|priser|koster|kostnad|"
            r"på\s+lager|lagerbeholdning|sjekk\s+om|"
            r"what\s+is\s+the\s+price|what\s+is\s+the\s+cost|price\s+of|cost\s+of|"
            r"stock|inventory|price|cost)\b",
            low,
        )
    )
    mentions_staff_in_company = bool(
        re.search(
            r"\b(hvem\s+er\s+de\s+ansatte|ansatte\s+i\s+bedriften|de\s+ansatte)\b",
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
        "mentions_price_or_stock_lookup": mentions_price_or_stock_lookup,
        "mentions_staff_in_company": mentions_staff_in_company,
    }


def _standalone_green_request(raw_prompt: str) -> bool:
    """
    True when the user is clearly asking for a CRM/catalog/staff task, not invoice/payment/payroll/close.
    Used to unblock heuristics and to avoid suppressing green scores for these prompts.
    """
    low = raw_prompt.lower()
    if re.search(
        r"\b(hvem|who)\s+.{0,40}?\b(jobber|ansatt|ansatte|employee|staff|kollegaer|medarbeider|medarbeidere)\b",
        low,
    ):
        return True
    if re.search(
        r"\b(finn|søk|finne|look\s+up|search|locate|vis\s+alle|liste|oversikt|hent)\s+.{0,48}?"
        r"\b(kunde|kunden|kunder|kundene|customer|customers|client|clients|"
        r"vare|varen|varer|produkt|produkter|produktet|artikkel|"
        r"ansatte?|medarbeider|medarbeidere|employee|staff|kollegaer)\b",
        low,
    ):
        return True
    # English CRM phrasing without «find …» contiguous to entity.
    # Do not treat incidental «invoice» (reference/boilerplate) as blocking unless billing is primary.
    if re.search(
        r"\b(get|fetch|retrieve|pull\s+up|show)\s+.{0,48}?"
        r"\b(customer|customers|client|clients|kunde|kunden|kunder|kundene)\b",
        low,
    ):
        if re.search(r"\b(faktura|invoice)\b", low) and _billing_invoice_primary_task(raw_prompt):
            return False
        return True
    if re.search(
        r"\b(hvem\s+er\s+de\s+ansatte|ansatte\s+i\s+bedriften|de\s+ansatte|list\s+employees|show\s+(all\s+)?staff)\b",
        low,
    ):
        return True
    if re.search(r"\b(opprett\s+et\s+nytt\s+produkt|opprett\s+nytt\s+produkt)\b", low):
        return True
    if re.search(
        r"\b(registrer\s+ny\s+kunde|register\s+new\s+customer|add\s+new\s+customer|new\s+client|ny\s+kunde)\b",
        low,
    ):
        return True
    # Catalog price/stock. Incidental «invoice»/«faktura» (reference only) must not block unless
    # the ask is billing-primary (invoice line, dispute, find-invoice, etc.).
    if re.search(
        r"\b(hva\s+er\s+prisen|pris\s+på|prisen\s+på|har\s+vi|på\s+lager|stock|inventory|"
        r"what\s+is\s+the\s+price|what\s+is\s+the\s+cost)\b",
        low,
    ):
        if re.search(r"\b(faktura|invoice)\b", low) and _billing_invoice_primary_task(raw_prompt):
            return False
        return True
    return False


def _billing_invoice_primary_task(raw_prompt: str) -> bool:
    """
    True when the user is primarily asking for invoice/payment/billing-document work.
    Incidental words like «invoice» in «invoice attached» must NOT set this when the ask is CRM/catalog.
    """
    low = raw_prompt.lower()
    if re.search(
        r"\b(opprett\s+faktura|create\s+invoice|ny\s+faktura|new\s+invoice|registrer\s+betaling|register\s+payment|"
        r"betal\s+faktura|invoice\s+payment|payment\s+on\s+invoice|betaling\s+på\s+faktura)\b",
        low,
    ):
        return True
    if re.search(
        r"\b(feil|wrong|dispute|purring|forfalt|overdue|reminder|inkasso|collection)\b",
        low,
    ) and re.search(r"\b(faktura|invoice)\b", low):
        return True
    if re.search(r"\b(ubetalt|unpaid|utestående|past\s+due|overdue)\b", low) and re.search(
        r"\b(faktura|invoice)\b", low
    ):
        return True
    # Find/search the invoice document (tight window — not «look up customer …, invoice attached»).
    if re.search(
        r"\b(find|search|søk|finn|look\s+up|locate)\s+.{0,16}?\b(?:the\s+)?(invoice|faktura)\b",
        low,
    ):
        return True
    if re.search(
        r"\b(invoice|faktura)\s+.{0,16}?\b(find|search|lookup|søk|finn)\b",
        low,
    ):
        return True
    # Price/cost on invoice line or explicit «on invoice» — billing, not catalog product lookup.
    if re.search(
        r"\b(price|cost|pris|prisen|koster|amount|beløp|what\s+is\s+the\s+price|what\s+is\s+the\s+cost|"
        r"hva\s+er\s+prisen|pris\s+på|prisen\s+på)\b",
        low,
    ) and re.search(
        r"\b(invoice\s+line|fakturalinje|on\s+(?:the\s+)?invoice|på\s+faktura\s+linje|"
        r"pris\s+(?:på|for)\s+faktura|price\s+on\s+invoice)\b",
        low,
    ):
        return True
    return False


def _strip_leading_attachment_boilerplate(raw_prompt: str) -> str:
    """
    Drop short leading lines that only reference attachments (NM / Cloud Run prompts).
    Does not read file bytes. Used for scoring / router signals only — guardrails use full text.
    """
    lines = raw_prompt.strip().splitlines()
    if not lines:
        return raw_prompt.strip()
    i = 0
    billing_re = re.compile(
        r"\b(opprett|create|registrer|register|betaling|payment|faktura|invoice|payroll|lønn|prosjekt|project|"
        r"måneds|monthly\s+close|accrual|period\s+close|reversal|reversering|avslutning|ubetalt|dispute)\b",
        re.I,
    )
    attach_start = re.compile(r"^\s*(?:vedlegg|attachment|attached)\b", re.I)
    attach_phrase = re.compile(
        r"^\s*(?:see\s+attached|please\s+refer|see\s+below|per\s+vedlegg|refer\s+to\s+the\s+attachment)\b",
        re.I,
    )
    crm_hint = re.compile(
        r"\b(customer|kunde|produkt|product|ansatt|employee|staff|finn|søk|find|price|stock|lager|pris|who|hvem|"
        r"liste|list|fetch|retrieve|bolt|sku|vare)\b",
        re.I,
    )
    while i < len(lines) and i < 4:
        line = lines[i].strip()
        if not line:
            i += 1
            continue
        if billing_re.search(line):
            break
        if attach_start.match(line) or attach_phrase.match(line):
            i += 1
            continue
        if len(line) < 130 and re.search(r"\b(vedlegg|attachment|attached)\b", line, re.I) and not crm_hint.search(
            line
        ):
            i += 1
            continue
        break
    return "\n".join(lines[i:]).strip() or raw_prompt.strip()


def _fixed_price_or_project_booking_prompt(raw_prompt: str) -> bool:
    """
    Fixed-price / project-pricing asks (DE Festpreis, NO fastpris, EN fixed price) are not in
    green CRM scope — blocks heuristic + LLM-green so we do not mis-route to create_customer.
    """
    low = raw_prompt.lower()
    if re.search(
        r"\b(festpreis|fest\s+preis|fastpris|fast\s+pris|festpris|fixed\s+price)\b",
        low,
    ):
        return True
    # German «Legen Sie … fest» + price / Festpreis context (NM-style project pricing).
    if re.search(r"\blegen\s+sie\b", low) and re.search(
        r"\b(festpreis|fest\s+preis|preis|price|nok|kr\b)\b",
        low,
    ):
        return True
    # Project + explicit pricing / amount (DE Projekt uses «projekt»; NO «prosjekt»).
    if re.search(r"\b(prosjekt|projekt|project)\b", low) and re.search(
        r"\b(festpreis|fest\s+preis|fastpris|fast\s+pris|fixed\s+price|von\s+\d)\b",
        low,
    ):
        return True
    return False


def _travel_or_expense_report_prompt(raw_prompt: str) -> bool:
    """
    Travel expense / mileage / reiseregning (NO/EN) — not green create_customer.
    Blocks heuristic email+contact boosts that mis-fire on «registrer reiseregning …».
    """
    low = raw_prompt.lower()
    if re.search(
        r"\b(reiseregning|reisekost|reisekostnad|reisekostnader|kjørebok|travel\s+expense|"
        r"kilometersats|km-?\s*sats|utlegg|utleggs|mileage)\b",
        low,
    ):
        return True
    return False


def _spanish_portuguese_project_runbook_prompt(raw_prompt: str) -> bool:
    """
    ES/PT project lifecycle / execution checklists (NM-style) — not Tripletex CRM «new customer».
    Heuristic email+phone boosts must not override LLM noop on these.
    """
    low = raw_prompt.lower()
    if re.search(r"\bciclo\s+de\s+vida\b", low) and re.search(r"\bproyecto\b", low):
        return True
    if re.search(r"\b(ejecute|ejecutar)\b", low) and re.search(r"\bproyecto\b", low):
        return True
    if re.search(r"\bprojeto\b", low) and re.search(
        r"\b(executar|ciclo\s+de\s+vida)\b", low
    ):
        return True
    return False


def _non_green_accounting_context(raw_prompt: str) -> bool:
    """Invoice/payment/project/payroll/period-close — green workflows are not in scope (unless standalone)."""
    from planner import _classify_intent

    low = raw_prompt.lower()
    if _fixed_price_or_project_booking_prompt(raw_prompt) or _travel_or_expense_report_prompt(
        raw_prompt
    ):
        return True
    if _spanish_portuguese_project_runbook_prompt(raw_prompt):
        return True
    # Structural OOS (before standalone CRM exemption)
    # DE/EN: supplier / purchase invoice (incoming bill) — not CRM «find customer».
    if re.search(
        r"\b(rechnung|lieferant|lieferanten|eingangsrechnung|einkaufsrechnung|"
        r"supplier\s+invoice|vendor\s+invoice|purchase\s+invoice|incoming\s+invoice)\b",
        low,
    ):
        return True
    if re.search(r"\b(prosjekt|projekt|project)\b", low) and re.search(
        r"\b(kunde|customer|client|koble|link|for\s+customer|til\s+kunde|linked)\b",
        low,
    ):
        return True
    if re.search(
        r"\b(lønn|payroll|payslip|payslips|lønns|timelønn|feriepenger|salary|pay\s+run)\b",
        low,
    ):
        return True
    if re.search(
        r"\b(månedsavslutning|månedlig\s+avslutning|månedlig\s+lukning|monthly\s+close|"
        r"period\s+close|year[- ]end|årsavslutning|accrual|periodisering|reversal|reversering|"
        r"avsetning|bokslut|periodisere)\b",
        low,
    ):
        return True
    if re.search(
        r"\b(overdue|forfalt|purring|påminnelse|reminder|inkasso|collection|"
        r"late\s+fee|pålagt\s+gebyr|reminder\s+fee|service\s+fee)\b",
        low,
    ) and re.search(r"\b(faktura|invoice|betaling|payment)\b", low):
        return True
    # Clear CRM/catalog/employee ask: do not treat bare «invoice»/«payment» substrings as OOS
    # (e.g. «invoice attached» in boilerplate) unless billing is the primary task.
    if _standalone_green_request(raw_prompt) and not _billing_invoice_primary_task(raw_prompt):
        return False

    intent = _classify_intent(raw_prompt)
    if intent in ("invoice", "payment"):
        return True
    return False


def _heuristic_relax_eligible(raw_prompt: str) -> bool:
    """
    Safe to run a second, looser heuristic pass: standalone green task and not dominated by
    invoice/payment/project/payroll/close signals (guardrails stay in effect via _heuristic_blocked).
    """
    if _non_green_accounting_context(raw_prompt):
        return False
    if _heuristic_blocked(raw_prompt):
        return False
    return _standalone_green_request(raw_prompt)


def _weak_green_recall_eligible(raw_prompt: str) -> bool:
    """
    Third heuristic pass: relax_eligible prompts that still tie-break fail, plus weak non-standalone cues.
    Never when OOS accounting or blocked.
    """
    if _non_green_accounting_context(raw_prompt):
        return False
    if _heuristic_blocked(raw_prompt):
        return False
    if _heuristic_relax_eligible(raw_prompt):
        return True
    effective = _strip_leading_attachment_boilerplate(raw_prompt)
    low = effective.lower()
    s = collect_router_signals(effective)
    if s.get("mentions_staff_in_company"):
        return True
    if s["mentions_employee_terms"] and (
        s["mentions_find_verbs"] or s["mentions_list_or_show_verbs"] or s["mentions_who_or_all"]
    ):
        return True
    if s["mentions_customer_terms"] and (
        s["mentions_find_verbs"] or s.get("mentions_existing_customer_cue")
    ):
        return True
    if (s["mentions_product_terms"] or s.get("mentions_price_or_stock_lookup")) and (
        s["mentions_find_verbs"] or s.get("mentions_price_or_stock_lookup")
    ):
        return True
    if re.search(r"\b(opprett\s+et\s+nytt\s+produkt|opprett\s+nytt\s+produkt)\b", low):
        return True
    if re.search(
        r"\b(registrer\s+ny\s+kunde|register\s+new\s+customer|add\s+new\s+customer|new\s+client|ny\s+kunde)\b",
        low,
    ) and not _billing_invoice_primary_task(raw_prompt):
        return True
    return False


def _heuristic_blocked(raw_prompt: str) -> bool:
    """Do not use green heuristics or accept LLM green when prompt is out of green scope."""
    # Invoice/payment/project/payroll/close must win over broad «find customer» English matches.
    if _non_green_accounting_context(raw_prompt):
        return True
    if _standalone_green_request(raw_prompt):
        return False
    low = raw_prompt.lower()
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

    effective = _strip_leading_attachment_boilerplate(raw_prompt)
    if not effective.strip():
        effective = raw_prompt.strip()

    s = collect_router_signals(effective)
    low = effective.lower()
    intent = _classify_intent(effective)
    # «faktura»/«invoice» anywhere sets coarse intent to invoice before «create»/«search» in
    # planner._classify_intent. For standalone CRM/catalog asks, remap for scoring so we do not
    # drop search boosts when the word is only reference (guardrails unchanged: OOS uses full logic).
    if (
        intent == "invoice"
        and _standalone_green_request(raw_prompt)
        and not _billing_invoice_primary_task(raw_prompt)
    ):
        if (
            s["mentions_find_verbs"]
            or s["mentions_list_or_show_verbs"]
            or s.get("mentions_price_or_stock_lookup")
            or s.get("mentions_staff_in_company")
        ):
            intent = "search"
    has_em = bool(_extract_email(raw_prompt))
    has_ph = bool(_extract_phone(raw_prompt))
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
    if s.get("mentions_staff_in_company"):
        scores["list_employees"] += 6.0

    if s["mentions_customer_terms"] and s["mentions_create_or_add_verbs"]:
        scores["create_customer"] += 6.0
    # Contact blocks strongly suggest create only when not explicitly searching/looking up.
    if (has_em or has_ph) and s["mentions_customer_terms"] and not s["mentions_find_verbs"]:
        scores["create_customer"] += 5.0
    # Strong boost only for short contact cards or explicit customer wording — long prompts with
    # email/phone in footers (e.g. ES project runbooks) must not win create_customer on this alone.
    if (
        has_em
        and has_ph
        and not s["mentions_product_terms"]
        and not s["mentions_employee_terms"]
        and not s["mentions_find_verbs"]
        and (s["mentions_customer_terms"] or len(effective.strip()) < 280)
    ):
        scores["create_customer"] += 5.5
    if (
        intent == "create"
        and (has_em or has_ph)
        and not s["mentions_product_terms"]
        and not s["mentions_find_verbs"]
        and s["mentions_customer_terms"]
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

    # Phone/email without explicit customer/product/staff words (NM: contact-only lines).
    # Prefer search_* when unsure; create only when clear create/register verbs (matches router system prompt).
    if (
        (has_em or has_ph)
        and not s["mentions_customer_terms"]
        and not s["mentions_product_terms"]
        and not s["mentions_employee_terms"]
    ):
        if s["mentions_find_verbs"] or s["mentions_list_or_show_verbs"]:
            scores["search_customer"] += 6.5
        elif s["mentions_create_or_add_verbs"]:
            scores["create_customer"] += 6.0
        else:
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
    if re.search(r"\b(opprett\s+et\s+nytt\s+produkt|opprett\s+nytt\s+produkt)\b", low):
        scores["create_product"] += 7.0

    if s["mentions_product_terms"] and s["mentions_find_verbs"]:
        scores["search_product"] += 6.0
    if pcode:
        scores["search_product"] += 3.5
    if s["mentions_product_terms"] and intent == "search":
        scores["search_product"] += 2.0
    if s.get("mentions_price_or_stock_lookup"):
        scores["search_product"] += 8.0
        if s["mentions_product_terms"] or re.search(r"[«\"'][^«\"'\n]{2,}[»\"']", raw_prompt):
            scores["search_product"] += 3.0

    # Invoice numbers often appear as reference on otherwise green CRM prompts; do not penalize
    # when standalone + not billing-primary (same boundary as non_green exemption).
    if re.search(r"\b(faktura|invoice)\s*(nr|no|number|#)?\s*[:#]?\s*\d{3,}", low):
        if not (_standalone_green_request(raw_prompt) and not _billing_invoice_primary_task(raw_prompt)):
            scores["create_customer"] -= 2.5
            scores["search_customer"] -= 1.0

    for w in GREEN_WORKFLOWS:
        scores[w] = max(0.0, scores[w])

    return scores


def heuristic_green_workflow_after_llm_noop(
    raw_prompt: str,
    *,
    relaxed: bool = False,
    weak: bool = False,
    file_count: int = 0,
) -> tuple[str, str, float, str] | None:
    """
    When the LLM returned noop (or low confidence), pick a green workflow from deterministic scores.
    ``relaxed`` / ``weak`` use progressively lower min scores and looser tie-breaks (passes 2 and 3).
    """
    if relaxed and weak:
        raise ValueError("relaxed and weak cannot both be True")

    scores = _score_green_workflows(raw_prompt)
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    if not ranked:
        return None
    best_wf, best_s = ranked[0]
    second_s = ranked[1][1] if len(ranked) > 1 else 0.0

    compact = "|".join(f"{k.split('_')[0][:2]}={v:.1f}" for k, v in sorted(scores.items(), key=lambda x: -x[1]))

    if weak:
        min_score = (
            _HEURISTIC_MIN_SCORE_WEAK_ATTACHED if file_count > 0 else _HEURISTIC_MIN_SCORE_WEAK
        )
        amb_gap = _HEURISTIC_AMBIGUITY_GAP_WEAK
        second_min = _HEURISTIC_SECOND_STRONG_MIN_WEAK
        tag = "weak"
    elif relaxed:
        min_score = _HEURISTIC_MIN_SCORE_RELAXED
        amb_gap = _HEURISTIC_AMBIGUITY_GAP_RELAXED
        second_min = _HEURISTIC_SECOND_STRONG_MIN_RELAXED
        tag = "relaxed"
    else:
        stripped = _strip_leading_attachment_boilerplate(raw_prompt)
        standalone_like = _standalone_green_request(raw_prompt) or _standalone_green_request(stripped)
        min_score = _HEURISTIC_MIN_SCORE_STANDALONE if standalone_like else _HEURISTIC_MIN_SCORE
        amb_gap = _HEURISTIC_AMBIGUITY_GAP
        second_min = _HEURISTIC_SECOND_STRONG_MIN
        tag = "standard"

    if best_s < min_score:
        return None
    if best_s - second_s < amb_gap and second_s >= second_min:
        return None

    conf = min(0.74, 0.5 + best_s * 0.035)
    reason = f"heuristic_scores winner={best_wf} best={best_s:.1f} second={second_s:.1f}|{tag}"
    return (best_wf, reason, conf, compact[:400])


def heuristic_green_workflow_after_llm_noop_two_pass(
    raw_prompt: str, file_count: int = 0
) -> tuple[str, str, float, str] | None:
    """Standard → relaxed (standalone) → weak (consistent weak-green cues, still guarded)."""
    h = heuristic_green_workflow_after_llm_noop(
        raw_prompt, relaxed=False, weak=False, file_count=file_count
    )
    if h is not None:
        return h
    if _heuristic_relax_eligible(raw_prompt):
        h = heuristic_green_workflow_after_llm_noop(
            raw_prompt, relaxed=True, weak=False, file_count=file_count
        )
        if h is not None:
            return h
    if _weak_green_recall_eligible(raw_prompt):
        return heuristic_green_workflow_after_llm_noop(
            raw_prompt, relaxed=False, weak=True, file_count=file_count
        )
    return None


def _router_identifying_price_stock(raw_prompt: str) -> tuple[bool, bool]:
    """Split price vs stock cues for structured router input (Norwegian + English)."""
    low = raw_prompt.lower()
    price = bool(
        re.search(
            r"\b(hva\s+er\s+prisen|pris\s+på|prisen\s+på|pris|priser|koster|kostnad|"
            r"what\s+is\s+the\s+price|what\s+is\s+the\s+cost|price\s+of|cost\s+of|price|cost)\b",
            low,
        )
    )
    stock = bool(
        re.search(
            r"\b(på\s+lager|lagerbeholdning|lager|stock|inventory|"
            r"sjekk\s+om|har\s+vi|do\s+we\s+have|in\s+stock)\b",
            low,
        )
    )
    return price, stock


def build_llm_router_user_content(raw_prompt: str, file_count: int = 0) -> str:
    """Structured original prompt + machine-readable signals + heuristic ranking (not a command to the model)."""
    from planner import _extract_email, _extract_phone, _extract_product_code

    effective = _strip_leading_attachment_boilerplate(raw_prompt)
    s = collect_router_signals(effective)
    scores = _score_green_workflows(raw_prompt)
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    top_wf, top_score = ranked[0] if ranked else ("noop", 0.0)
    second_score = ranked[1][1] if len(ranked) > 1 else 0.0
    id_price, id_stock = _router_identifying_price_stock(effective)

    has_em_f = bool(_extract_email(raw_prompt))
    has_ph_f = bool(_extract_phone(raw_prompt))
    entity_customer = bool(
        s["mentions_customer_terms"]
        or (
            (has_em_f or has_ph_f)
            and not s["mentions_product_terms"]
            and not s["mentions_employee_terms"]
        )
    )
    entity_product = bool(s["mentions_product_terms"] or s["mentions_price_or_stock_lookup"])
    entity_employees = bool(s["mentions_employee_terms"] or s["mentions_staff_in_company"])

    action_find = bool(s["mentions_find_verbs"] or s["mentions_list_or_show_verbs"])
    action_create = bool(s["mentions_create_or_add_verbs"])
    action_exists = bool(
        s["mentions_existing_customer_cue"] or s["mentions_price_or_stock_lookup"] or id_price or id_stock
    )

    # Compact coarse intent for context (same string as planner intent classifier).
    coarse = s["coarse_intent"]
    sg = _standalone_green_request(raw_prompt)
    ng = _non_green_accounting_context(raw_prompt)

    fc = max(0, int(file_count))
    return (
        "[router_input]\n"
        f"original_prompt: {raw_prompt.strip()}\n"
        f"attachments_count: {fc}\n"
        "\n"
        "routing_note:\n"
        "  attachments_count is metadata only (file bytes are not visible here). It does not mean\n"
        "  'no task' or 'out of scope'. Route from the TEXT; do not choose noop solely because\n"
        "  attachments_count > 0 or the text mentions vedlegg / attachment when the task is still\n"
        "  CRM / catalog / employees.\n"
        "\n"
        "router_guardrails:\n"
        f"  standalone_green_likely: {sg}\n"
        f"  non_green_accounting_context: {ng}\n"
        "  (If non_green_accounting_context and not standalone_green_likely → prefer noop.)\n"
        "\n"
        "entity_signals:\n"
        f"  customer: {entity_customer}\n"
        f"  product: {entity_product}\n"
        f"  employees: {entity_employees}\n"
        "\n"
        "action_signals:\n"
        f"  find_or_list: {action_find}\n"
        f"  create_or_register: {action_create}\n"
        f"  lookup_existence_or_details: {action_exists}\n"
        "\n"
        "identifying_details:\n"
        f"  email_in_text: {bool(_extract_email(raw_prompt))}\n"
        f"  phone_in_text: {bool(_extract_phone(raw_prompt))}\n"
        f"  price_question: {id_price}\n"
        f"  stock_question: {id_stock}\n"
        f"  product_code_in_text: {bool(_extract_product_code(raw_prompt))}\n"
        "\n"
        f"coarse_intent_classifier: {coarse}\n"
        "\n"
        "heuristic_ranking (reference only — apply system decision rules, do not mirror blindly):\n"
        f"  heuristic_top_workflow: {top_wf}\n"
        f"  heuristic_top_score: {top_score:.2f}\n"
        f"  heuristic_second_score: {second_score:.2f}\n"
        "  per_workflow_scores:\n"
        + "".join(f"    {k}: {v:.2f}\n" for k, v in ranked)
        + "\n"
        "raw_signal_flags (debug):\n"
        + "".join(f"  {k}: {v}\n" for k, v in sorted(s.items()))
    )


def call_llm_router(prompt: str, file_count: int = 0) -> LLMRouterJSON | None:
    """Call remote LLM; return parsed model or None on failure."""
    user_content = build_llm_router_user_content(prompt, file_count=file_count)
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
    from planner import (
        _extract_customer_name_after_client_cue,
        _extract_customer_name_fallback_from_email,
        _extract_email,
        _extract_label_value,
        _extract_product_code,
        _strip_product_metadata,
    )

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
        if not customer_name:
            customer_name = _extract_customer_name_after_client_cue(raw_prompt)
        if not customer_name:
            customer_name = _extract_customer_name_fallback_from_email(_extract_email(raw_prompt))

    if workflow in ("create_product", "search_product"):
        product_name = _extract_label_value(raw_prompt, "produkt", "vare", "product", "article", "linje")
        product_name = _strip_product_metadata(product_name) if product_name else ""
        if not product_name and workflow == "search_product":
            product_name = _strip_product_metadata(raw_prompt)[:120]

    summary = f"noop_override|{reason}|sc={scores_compact[:120]}"
    ent_map = {
        "list_employees": "employees",
        "search_customer": "customer",
        "create_customer": "customer",
        "search_product": "product",
        "create_product": "product",
        "noop": "unknown",
    }
    synth_entity = ent_map.get(workflow, "unknown")
    synth_reason = f"heuristic override winner {workflow}"
    return LLMRouterJSON(
        workflow=workflow,  # type: ignore[arg-type]
        confidence=confidence,
        language="unknown",
        entity=synth_entity,  # type: ignore[arg-type]
        reason=synth_reason[:120],
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
        _extract_customer_name_after_client_cue,
        _extract_customer_name_fallback_from_email,
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
    if wf in ("search_customer", "create_customer") and not customer_name:
        customer_name = _extract_customer_name_after_client_cue(raw_prompt).strip()
    if wf in ("search_customer", "create_customer") and not customer_name:
        customer_name = _extract_customer_name_fallback_from_email(email).strip()
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
    if wf == "noop":
        intent = "unknown"
    snippet = re.sub(r"\s+", " ", raw_prompt.strip())[:120]
    hint_em = int(bool(_extract_email(raw_prompt)))
    hint_ph = int(bool(_extract_phone(raw_prompt)))
    ov = "1" if heuristic_override else "0"
    ent = llm.entity
    rsn = (llm.reason or "").strip()
    detail = (
        f"llm:workflow={wf}|entity={ent}|ov={ov}|lang={llm.language}|conf={llm.confidence:.2f}|"
        f"i={intent}|em={hint_em}|ph={hint_ph}|reason={rsn[:120]}|"
        f"{(llm.extraction_summary or '')[:120]}"
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


def try_heuristic_override_plan(raw_prompt: str, file_count: int = 0) -> tuple["Plan", str] | None:
    """Deterministic green heuristic → Plan when scores show a clear winner."""
    hw = heuristic_green_workflow_after_llm_noop_two_pass(raw_prompt, file_count=file_count)
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


def try_heuristic_green_override_only_with_detail(
    raw_prompt: str, file_count: int = 0
) -> tuple[Plan | None, str]:
    """
    Heuristic-only pass (used after rules still return noop in LLM-first mode).
    """
    if not llm_router_enabled():
        return None, "llm_disabled"
    got = try_heuristic_override_plan(raw_prompt, file_count=file_count)
    if got is None:
        return None, "heuristic_no_override"
    return got[0], got[1]


def try_llm_green_first_with_detail(raw_prompt: str, file_count: int = 0) -> tuple[Plan | None, str]:
    """
    LLM-first router for green scope only. Returns a Plan when the model picks a
    green workflow with confidence >= :data:`_LLM_CONFIDENCE_MIN` and guardrails
    pass. Otherwise ``(None, reason)`` so caller can fall back to rules.
    """
    if not llm_router_enabled():
        return None, "llm_disabled"
    llm = call_llm_router(raw_prompt, file_count=file_count)
    if llm is None:
        return None, "llm_invalid_response"

    guardrail_rejected = False
    if llm.workflow in GREEN_WORKFLOWS and _heuristic_blocked(raw_prompt):
        guardrail_rejected = True
        llm = LLMRouterJSON(
            workflow="noop",
            confidence=0.95,
            language=llm.language or "unknown",
            entity="unknown",
            reason="guardrail_rejected_green_for_accounting_oos",
            extraction_summary="green_blocked_oos",
        )

    if llm.workflow == "noop" or llm.confidence < _LLM_CONFIDENCE_MIN:
        if guardrail_rejected:
            return None, "guardrail_rejected_llm_green"
        if llm.workflow == "noop":
            return None, "llm_chose_noop"
        return None, f"low_confidence:{llm.confidence:.2f}"

    if llm.workflow not in GREEN_WORKFLOWS:
        return None, "llm_chose_noop"

    return llm_router_json_to_plan(raw_prompt, llm), "ok"


def try_llm_plan_after_noop_with_detail(raw_prompt: str, file_count: int = 0) -> tuple[Plan | None, str]:
    """
    If LLM enabled and rules returned noop, try LLM (legacy rules-first pipeline).
    Returns (Plan, \"ok\" | \"ok_heuristic_override\") on success, or (None, reason) for logging / safe fallback.
    """
    if not llm_router_enabled():
        return None, "llm_disabled"
    llm = call_llm_router(raw_prompt, file_count=file_count)
    if llm is None:
        return None, "llm_invalid_response"

    guardrail_rejected_llm_green = False
    if llm.workflow in GREEN_WORKFLOWS and _heuristic_blocked(raw_prompt):
        guardrail_rejected_llm_green = True
        llm = LLMRouterJSON(
            workflow="noop",
            confidence=0.95,
            language=llm.language or "unknown",
            entity="unknown",
            reason="guardrail_rejected_green_for_accounting_oos",
            extraction_summary="green_blocked_oos",
        )

    # Prefer deterministic scores when the model is noop or under-confident.
    if llm.workflow == "noop" or llm.confidence < _LLM_CONFIDENCE_MIN:
        got = try_heuristic_override_plan(raw_prompt, file_count=file_count)
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

    # noop + under threshold: heuristics already tried; do not label as low_confidence (misleading vs model noop).
    if llm.confidence < _LLM_CONFIDENCE_MIN:
        if llm.workflow == "noop":
            return None, "guardrail_rejected_llm_green" if guardrail_rejected_llm_green else "llm_chose_noop"
        return None, f"low_confidence:{llm.confidence:.2f}"

    if llm.workflow == "noop":
        return None, "guardrail_rejected_llm_green" if guardrail_rejected_llm_green else "llm_chose_noop"
    return llm_router_json_to_plan(raw_prompt, llm), "ok"


def try_llm_plan_after_noop(raw_prompt: str, file_count: int = 0) -> Plan | None:
    """Thin wrapper; use :func:`try_llm_plan_after_noop_with_detail` when logging the reason."""
    plan, _ = try_llm_plan_after_noop_with_detail(raw_prompt, file_count=file_count)
    return plan
