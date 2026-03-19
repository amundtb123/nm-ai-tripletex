"""Workflow entrypoints: Tripletex API calls driven by the planner."""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import date, timedelta
from typing import Any

from customer_resolver import search_customer_by_name, resolve_customer_by_name
from product_resolver import (
    pick_best_product_match,
    resolve_product_by_name_or_number,
    search_products_fallback,
)
from planner import Plan
from tripletex_client import TripletexClient
from tripletex_request import tripletex_json

log = logging.getLogger(__name__)


class WorkflowInputError(ValueError):
    """Invalid or incomplete user input before calling Tripletex."""


def _unwrap_value(data: Any) -> Any:
    if isinstance(data, dict) and "value" in data:
        return data["value"]
    return data


# Writable top-level Customer fields we allow on PUT. Nested DTOs (e.g. postalAddress)
# are excluded until we explicitly build them — re-sending GET snapshots often includes
# read-only or server-owned keys and triggers validation errors.
_CUSTOMER_PUT_ALLOWED: frozenset[str] = frozenset(
    {
        "name",
        "email",
        "phoneNumber",
        "mobileNumber",
        "organizationNumber",
        "isCustomer",
        "isSupplier",
    }
)


def build_customer_update_payload(
    existing_customer: dict[str, Any],
    planned_updates: dict[str, Any],
) -> dict[str, Any]:
    """
    Build a minimal JSON body for PUT /customer/{id}.

    Sending the **entire** GET /customer response on PUT is risky: Tripletex returns
    nested objects, ``url``, read-only flags, and expanded relations. Clients often
    cannot write those fields verbatim, which produces 4xx validation errors or
    unintended changes. We therefore send only ``id``, ``version`` (optimistic lock),
    and explicitly requested scalar fields from ``planned_updates``.
    """
    rid = existing_customer.get("id")
    ver = existing_customer.get("version")
    if rid is None:
        raise WorkflowInputError("Mangler kunde-id i Tripletex-svaret.")
    if ver is None:
        raise WorkflowInputError(
            "Mangler «version» for kundeoppdatering — påkrevd av Tripletex for PUT."
        )

    payload: dict[str, Any] = {"id": rid, "version": ver}
    for key, raw in planned_updates.items():
        if key not in _CUSTOMER_PUT_ALLOWED:
            continue
        if raw is None:
            continue
        if isinstance(raw, str):
            s = raw.strip()
            if not s:
                continue
            payload[key] = s
        elif isinstance(raw, bool):
            payload[key] = raw
        else:
            payload[key] = raw

    if len(payload) <= 2:
        raise WorkflowInputError(
            "Ingen gyldige felt å oppdatere etter filtrering. Oppgi navn, e-post eller telefon."
        )
    return payload


def _invoice_search_window() -> tuple[str, str]:
    end = date.today()
    start = end - timedelta(days=730)
    return start.isoformat(), (end + timedelta(days=1)).isoformat()


def extract_invoice_number(text: str) -> str:
    m = re.search(
        r"(?:faktura|invoice)\s*(?:nr|no|nummer)?\s*[:#]?\s*(\d{3,})",
        text,
        re.IGNORECASE,
    )
    if m:
        return m.group(1)
    return ""


def parse_currency_amount(text: str) -> float | None:
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:kr|nok)\b", text, re.IGNORECASE)
    if not m:
        return None
    return float(m.group(1).replace(",", "."))


def _product_number_label(display: str) -> str:
    base = re.sub(r"[^A-Z0-9]+", "-", display.upper())[:14].strip("-") or "PROD"
    return f"{base}-{uuid.uuid4().hex[:6].upper()}"


def _post_product_return_row(
    client: TripletexClient,
    *,
    name: str,
    number: str,
    price_ex_vat: float | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"name": name, "number": number}
    if price_ex_vat is not None:
        body["priceExcludingVatCurrency"] = price_ex_vat
    data = tripletex_json(client, log, "POST", "/product", json_body=body)
    obj = _unwrap_value(data)
    if not isinstance(obj, dict):
        raise WorkflowInputError("Uventet svar ved oppretting av produkt.")
    log.info(
        json.dumps(
            {"event": "product_created", "product_number": number},
            ensure_ascii=False,
        )
    )
    return obj


def _product_row_unit_price_ex_vat(row: dict[str, Any]) -> float | None:
    v = row.get("priceExcludingVatCurrency")
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def workflow_noop(plan: Plan, client: TripletexClient) -> dict[str, str]:
    _ = client
    return {
        "workflow": "noop",
        "status": "skipped",
        "reason": "no_matching_workflow_trigger",
        "plan_intent": plan.detected_intent,
    }


def workflow_list_employees(plan: Plan, client: TripletexClient) -> dict[str, str]:
    _ = plan
    data = tripletex_json(
        client,
        log,
        "GET",
        "/employee",
        params={"from": 0, "count": 100},
    )
    values = _unwrap_value(data)
    n = len(values) if isinstance(values, list) else 0
    return {"workflow": "list_employees", "status": "ok", "employee_count": str(n)}


def workflow_search_customer(plan: Plan, client: TripletexClient) -> dict[str, str]:
    name = plan.customer_name.strip()
    if not name:
        raise WorkflowInputError(
            "Oppgi kundenavn etter utløseren, f.eks. «finn kunde Acme AS»."
        )
    matches = search_customer_by_name(client, log, name)
    return {
        "workflow": "search_customer",
        "status": "ok",
        "customer_match_count": str(len(matches)),
    }


def workflow_create_customer(plan: Plan, client: TripletexClient) -> dict[str, str]:
    name = plan.customer_name.strip()
    if not name:
        raise WorkflowInputError(
            "Oppgi navn på ny kunde etter utløseren, f.eks. «opprett kunde Ny Bedrift AS»."
        )
    data = tripletex_json(
        client,
        log,
        "POST",
        "/customer",
        json_body={"name": name},
    )
    obj = _unwrap_value(data)
    cid = ""
    if isinstance(obj, dict) and obj.get("id") is not None:
        cid = str(obj["id"])
    return {"workflow": "create_customer", "status": "ok", "customer_id": cid}


def workflow_update_customer(plan: Plan, client: TripletexClient) -> dict[str, str]:
    cname = plan.customer_name.strip()
    if not cname:
        raise WorkflowInputError(
            "Oppgi hvilken kunde som skal oppdateres, f.eks. «oppdater kunde Acme AS»."
        )
    row = resolve_customer_by_name(client, log, cname)
    if row is None:
        raise WorkflowInputError(f"Fant ingen kunde som matcher «{cname}».")
    cid = int(row["id"])
    data = tripletex_json(client, log, "GET", f"/customer/{cid}", params=None)
    full = _unwrap_value(data)
    if not isinstance(full, dict):
        raise WorkflowInputError("Uventet svar ved henting av kunde.")

    planned_updates: dict[str, Any] = {}
    if plan.name.strip():
        planned_updates["name"] = plan.name.strip()
    if plan.email.strip():
        planned_updates["email"] = plan.email.strip()
    if plan.phone.strip():
        planned_updates["phoneNumber"] = plan.phone.strip()
    if not planned_updates:
        raise WorkflowInputError(
            "Oppgi minst ett felt å oppdatere (e-post eller telefon i teksten, eller navn: … / name: …)."
        )

    payload = build_customer_update_payload(full, planned_updates)
    tripletex_json(client, log, "PUT", f"/customer/{cid}", json_body=payload)
    return {"workflow": "update_customer", "status": "ok", "customer_id": str(cid)}


def workflow_search_product(plan: Plan, client: TripletexClient) -> dict[str, str]:
    pname = plan.product_name.strip()
    pnum = plan.product_number.strip()
    if not pname and not pnum:
        raise WorkflowInputError(
            "Oppgi produktnavn eller varenummer etter utløseren, "
            "f.eks. «finn produkt Kaffe» eller «søk produkt varenummer: ABC-1»."
        )
    matches = search_products_fallback(client, log, name=pname, product_number=pnum)
    _ = pick_best_product_match(
        matches,
        query_name=pname,
        query_number=pnum,
        log=log,
    )
    return {
        "workflow": "search_product",
        "status": "ok",
        "product_match_count": str(len(matches)),
    }


def workflow_create_product(plan: Plan, client: TripletexClient) -> dict[str, str]:
    name = (plan.product_name or plan.name).strip()
    if not name:
        raise WorkflowInputError(
            "Oppgi produktnavn etter utløseren, f.eks. «opprett produkt Konsul_time»."
        )
    code = plan.product_number.strip()
    number = code if code else _product_number_label(name)
    obj = _post_product_return_row(
        client,
        name=name,
        number=number,
        price_ex_vat=plan.product_price,
    )
    pid = ""
    if obj.get("id") is not None:
        pid = str(obj["id"])
    return {"workflow": "create_product", "status": "ok", "product_id": pid, "product_number": number}


def workflow_search_invoice(plan: Plan, client: TripletexClient) -> dict[str, str]:
    date_from, date_to = _invoice_search_window()
    params: dict[str, Any] = {
        "invoiceDateFrom": date_from,
        "invoiceDateTo": date_to,
        "from": 0,
        "count": 100,
    }
    inv_no = extract_invoice_number(plan.raw_prompt)
    cname = plan.customer_name.strip()

    if inv_no:
        params["invoiceNumber"] = inv_no
    elif cname.isdigit():
        params["invoiceNumber"] = cname
    elif cname:
        cust = resolve_customer_by_name(client, log, cname)
        if cust is None:
            raise WorkflowInputError(f"Fant ingen kunde som matcher «{cname}» for fakturasøk.")
        params["customerId"] = str(int(cust["id"]))
    else:
        raise WorkflowInputError(
            "Oppgi fakturanummer (f.eks. «finn faktura 10042») eller kundenavn for å filtrere fakturaer."
        )

    data = tripletex_json(client, log, "GET", "/invoice", params=params)
    values = _unwrap_value(data)
    n = len(values) if isinstance(values, list) else 0
    return {"workflow": "search_invoice", "status": "ok", "invoice_match_count": str(n)}


def _fetch_invoices_by_number(
    client: TripletexClient,
    *,
    invoice_number: str,
    customer_id: int | None = None,
) -> list[dict[str, Any]]:
    """GET /invoice with required date window (same as search_invoice)."""
    date_from, date_to = _invoice_search_window()
    params: dict[str, Any] = {
        "invoiceDateFrom": date_from,
        "invoiceDateTo": date_to,
        "invoiceNumber": invoice_number.strip(),
        "from": 0,
        "count": 100,
    }
    if customer_id is not None:
        params["customerId"] = str(customer_id)
    data = tripletex_json(client, log, "GET", "/invoice", params=params)
    values = _unwrap_value(data)
    if not isinstance(values, list):
        return []
    return [v for v in values if isinstance(v, dict)]


def workflow_register_payment(plan: Plan, client: TripletexClient) -> dict[str, str]:
    inv_no = plan.payment_invoice_number.strip()
    if not inv_no:
        raise WorkflowInputError(
            "Oppgi fakturanummer, f.eks. «registrer betaling faktura 10042 …» eller «fakturanummer: 10042»."
        )
    raw_pt = os.environ.get("TRIPLETEX_DEFAULT_PAYMENT_TYPE_ID")
    if not raw_pt or not raw_pt.strip():
        raise WorkflowInputError(
            "Mangler innstilling for betalingstype: sett miljøvariabelen "
            "TRIPLETEX_DEFAULT_PAYMENT_TYPE_ID (Tripletex betalingstype for kundeinkasso / innbetaling)."
        )
    payment_type_id = int(raw_pt.strip())

    amount: float | None = plan.payment_amount
    if amount is None:
        amount = parse_currency_amount(plan.raw_prompt)
    if amount is None:
        raise WorkflowInputError(
            "Oppgi beløp med «… kr» / «… nok» eller «beløp: 1000» i teksten."
        )

    pay_date = (plan.payment_date or "").strip() or date.today().isoformat()

    # --- Tripletex / miljø (se også PROJECT_STATE §4 og §6):
    # - Endepunkt per OpenAPI: ``PUT /invoice/{id}/:payment`` med query ``paymentDate``,
    #   ``paymentTypeId``, ``paidAmount`` (påkrevd). Vi sender ikke ``paidAmountCurrency``;
    #   det kreves iflg. OpenAPI bare ved avvikende fakturavaluta.
    # - ``GET /invoice`` inkluderer iflg. dokumentasjon kun bestemte utgående, belastede fakturaer;
    #   om en ikke vises, kan den være utenfor filter, betalt, eller annen status — **verifiser**.
    # - **paymentTypeId** er kontospesifikk; ingen standardverdi her — må settes eksplisitt i miljø.
    # - **Bokføringsdato vs betalingsdato:** kun ``paymentDate`` som query; regnskapsmessig effekt
    #   kan avhenge av Tripletex-oppsett — **verifiser** i mål-miljø.
    # - Allerede fullt betalt eller låst faktura kan gi **502** med Tripletex-validering.

    cname = plan.customer_name.strip()
    cid: int | None = None
    if cname:
        cust = resolve_customer_by_name(client, log, cname)
        if cust is None:
            raise WorkflowInputError(f"Fant ingen kunde som matcher «{cname}».")
        cid = int(cust["id"])

    rows = _fetch_invoices_by_number(client, invoice_number=inv_no, customer_id=cid)
    if not rows:
        raise WorkflowInputError(
            f"Fant ingen faktura med nummer «{inv_no}» i søkevinduet (eller ingen som passer med kunden). "
            "Sjekk nummer og kunde, eller at fakturaen er synlig som utestående i Tripletex."
        )
    if len(rows) > 1:
        raise WorkflowInputError(
            f"Flere fakturaer matcher «{inv_no}». Bruk «kunde: …» for å avgrense treffene."
        )

    inv_row = rows[0]
    rid = inv_row.get("id")
    if rid is None:
        raise WorkflowInputError("Tripletex returnerte faktura uten id.")
    iid = int(rid)

    tripletex_json(
        client,
        log,
        "PUT",
        f"/invoice/{iid}/:payment",
        params={
            "paymentDate": pay_date,
            "paymentTypeId": payment_type_id,
            "paidAmount": amount,
        },
        json_body=None,
    )

    inv_disp = str(inv_row.get("invoiceNumber") or inv_no)
    return {
        "workflow": "register_payment",
        "status": "ok",
        "invoice_id": str(iid),
        "invoice_number": inv_disp,
        "paid_amount": str(amount),
        "payment_date": pay_date,
    }


def workflow_create_invoice_for_customer(plan: Plan, client: TripletexClient) -> dict[str, str]:
    cname = plan.customer_name.strip()
    if not cname:
        raise WorkflowInputError(
            "Oppgi kundenavn etter utløseren, f.eks. «opprett faktura Acme AS»."
        )
    cust = resolve_customer_by_name(client, log, cname)
    if cust is None:
        raise WorkflowInputError(
            f"Fant ingen kunde som matcher «{cname}». Opprett kunden først, eller skriv et mer presist navn."
        )

    cid = int(cust["id"])
    today = date.today().isoformat()
    # --- Miljø / Tripletex-usikkerhet (krever ofte verifikasjon i ekte selskap):
    # - ``TRIPLETEX_DEFAULT_VAT_TYPE_ID``: MVA-type-ID er **ikke** portabel mellom leietakere.
    # - ``sendToCustomer=false`` (query på POST /invoice): antar at vi unngår auto-utsendelse;
    #   faktisk effekt kan avhenge av Tripletex-innstilling.
    # - Ordrelinjer med ``product``: noen konti kan kreve flere felt (enhet, lager, …) enn vi sender.
    # - OpenAPI sier faktura kan opprettes med **innebygde** ``orders``/``orderLines`` (ikke eget /order-kall først).
    vat_id = int(os.environ.get("TRIPLETEX_DEFAULT_VAT_TYPE_ID", "3"))
    default_amount = float(os.environ.get("TRIPLETEX_INVOICE_LINE_AMOUNT", "100"))
    parsed_prompt_amount = parse_currency_amount(plan.raw_prompt)

    pname = plan.product_name.strip()
    pnum = plan.product_number.strip()
    product_row: dict[str, Any] | None = None
    if pname or pnum:
        product_row = resolve_product_by_name_or_number(
            client, log, name=pname, product_number=pnum
        )
        if product_row is None and plan.invoice_autocreate_product:
            if not pname:
                raise WorkflowInputError(
                    "Fant ikke produktet i Tripletex. For automatisk oppretting må du oppgi produktnavn "
                    "(f.eks. «produkt: Navn» eller «… produkt Navn») sammen med «opprett produkt hvis mangler»."
                )
            number = pnum if pnum else _product_number_label(pname)
            create_price: float | None = plan.product_price
            if create_price is None:
                create_price = parsed_prompt_amount
            product_row = _post_product_return_row(
                client,
                name=pname,
                number=number,
                price_ex_vat=create_price,
            )

    if (pname or pnum) and product_row is None:
        raise WorkflowInputError(
            "Fant ingen produkt som matcher det du oppga (navn/varenummer). "
            "Opprett eller søk opp produktet først, presiser teksten, "
            "eller legg til «opprett produkt hvis mangler» for å opprette automatisk."
        )

    product_id_str = ""
    if product_row and product_row.get("id") is not None:
        pid = int(product_row["id"])
        product_id_str = str(pid)
        catalog_price = _product_row_unit_price_ex_vat(product_row)
        if parsed_prompt_amount is not None:
            unit_ex = parsed_prompt_amount
        elif plan.product_price is not None:
            unit_ex = plan.product_price
        elif catalog_price is not None:
            unit_ex = catalog_price
        else:
            unit_ex = default_amount
        line_desc = str(product_row.get("name") or pname or "Vare")
        order_line: dict[str, Any] = {
            "count": 1,
            "description": line_desc,
            "unitPriceExcludingVatCurrency": unit_ex,
            "vatType": {"id": vat_id},
            "product": {"id": pid},
        }
    else:
        if parsed_prompt_amount is not None:
            unit_ex = parsed_prompt_amount
        elif plan.product_price is not None:
            unit_ex = plan.product_price
        else:
            unit_ex = default_amount
        line_desc = plan.notes.strip() or "Tjeneste / vare (agent)"
        order_line = {
            "count": 1,
            "description": line_desc,
            "unitPriceExcludingVatCurrency": unit_ex,
            "vatType": {"id": vat_id},
        }

    body = {
        "invoiceDate": today,
        "customer": {"id": cid},
        "orders": [
            {
                "orderDate": today,
                "isPrioritizeAmountsIncludingVat": False,
                "orderLines": [order_line],
            }
        ],
    }

    data = tripletex_json(
        client,
        log,
        "POST",
        "/invoice",
        params={"sendToCustomer": "false"},
        json_body=body,
    )
    inv = _unwrap_value(data)
    iid = invno = ""
    if isinstance(inv, dict):
        if inv.get("id") is not None:
            iid = str(inv["id"])
        if inv.get("invoiceNumber") is not None:
            invno = str(inv["invoiceNumber"])
    return {
        "workflow": "create_invoice_for_customer",
        "status": "ok",
        "customer_id": str(cid),
        "invoice_id": iid,
        "invoice_number": invno,
        "product_id": product_id_str,
    }


def run_workflow(plan: Plan, client: TripletexClient) -> dict[str, str]:
    dispatch: dict[str, Any] = {
        "list_employees": workflow_list_employees,
        "search_customer": workflow_search_customer,
        "create_customer": workflow_create_customer,
        "update_customer": workflow_update_customer,
        "search_product": workflow_search_product,
        "create_product": workflow_create_product,
        "search_invoice": workflow_search_invoice,
        "register_payment": workflow_register_payment,
        "create_invoice_for_customer": workflow_create_invoice_for_customer,
    }
    fn = dispatch.get(plan.workflow)
    if fn:
        return fn(plan, client)
    return workflow_noop(plan, client)
