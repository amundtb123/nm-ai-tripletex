"""FastAPI service: health check and /solve orchestration for AI Accounting Agent."""

from __future__ import annotations

import json
import logging
import os
import sys
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException

from file_parser import FileDecodeError, decode_files_to_tmp
from planner import build_plan
from request_context import solve_request_id
from schemas import SolveRequestBody
from tripletex_credential_checks import (
    tripletex_base_url_placeholder_like,
    tripletex_credentials_valid_for_api,
    tripletex_session_token_placeholder_like,
)
from tripletex_client import TripletexClient
from tripletex_errors import TripletexAPIError
from workflows import WorkflowInputError, extract_invoice_number, run_workflow


def _configure_logging() -> None:
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)
    if not root.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(level)
        handler.setFormatter(logging.Formatter("%(message)s"))
        root.addHandler(handler)


def log_structured(logger: logging.Logger, level: int, event: str, **fields: Any) -> None:
    """Emit one JSON log line for ingestion by Cloud Logging / jq."""
    record = {"level": logging.getLevelName(level), "event": event, **fields}
    logger.log(level, json.dumps(record, default=str, ensure_ascii=False))


_configure_logging()
logger = logging.getLogger(__name__)

app = FastAPI(title="AI Accounting Agent", version="0.1.0")

DEFAULT_PORT = 8080


@app.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}


@app.post("/solve")
def solve(body: SolveRequestBody) -> dict[str, str]:
    """
    Accept prompt + optional files + Tripletex credentials; return completed when done.

    Structured lifecycle logs (stdout JSON, no secrets): ``request_received`` →
    ``files_decoded`` → ``plan_built`` → ``workflow_started`` → (``tripletex_http`` per call) →
    ``workflow_finished`` or ``workflow_failed`` (``failure_kind`` may be ``tripletex`` or
    ``tripletex_configuration`` for Tripletex tenant/setup errors) → ``request_finished``.
    Session tokens and Authorization headers are never logged (see ``tripletex_request``).
    """
    rid = uuid.uuid4().hex[:12]
    token = solve_request_id.set(rid)

    try:
        creds = body.tripletex_credentials
        bu_raw = creds.base_url
        tok_raw = creds.session_token
        log_structured(
            logger,
            logging.INFO,
            "request_received",
            request_id=rid,
            prompt_length=len(body.prompt),
            file_count=len(body.files),
            tripletex_base_url=bu_raw,
            tripletex_base_url_source="request_body",
            tripletex_base_url_placeholder_like=tripletex_base_url_placeholder_like(bu_raw),
            tripletex_session_token_placeholder_like=tripletex_session_token_placeholder_like(
                tok_raw
            ),
        )

        file_dicts: list[dict[str, str]] = [
            {"filename": f.filename, "content_base64": f.content_base64}
            for f in body.files
        ]

        if file_dicts:
            try:
                saved = decode_files_to_tmp(file_dicts)
            except FileDecodeError as exc:
                log_structured(
                    logger,
                    logging.WARNING,
                    "workflow_failed",
                    request_id=rid,
                    failure_kind="file_decode",
                    error=str(exc),
                )
                log_structured(
                    logger,
                    logging.INFO,
                    "request_finished",
                    request_id=rid,
                    outcome="client_error",
                    http_status=400,
                )
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            log_structured(
                logger,
                logging.INFO,
                "files_decoded",
                request_id=rid,
                count=len(saved),
                filenames=[s["original_filename"] for s in saved],
                total_bytes=sum(s["size_bytes"] for s in saved),
            )
        else:
            log_structured(
                logger,
                logging.INFO,
                "files_decoded",
                request_id=rid,
                count=0,
            )

        plan = build_plan(body.prompt)
        has_invoice_number_hint = bool(extract_invoice_number(body.prompt))

        log_structured(
            logger,
            logging.INFO,
            "plan_built",
            request_id=rid,
            detected_intent=plan.detected_intent,
            workflow=plan.workflow,
            workflow_route=plan.workflow_route,
            workflow_route_detail=plan.workflow_route_detail,
            target_entity=plan.target_entity,
            planner_mode=plan.planner_mode,
            planner_selected_workflow=plan.planner_selected_workflow or plan.workflow,
            planner_selected_entity=plan.planner_selected_entity or plan.target_entity,
            planner_confidence=plan.planner_confidence,
            planner_language=plan.planner_language,
            planner_llm_status=plan.planner_llm_status,
            planner_route_detail=plan.planner_route_detail,
            planner_heuristic_log=plan.planner_heuristic_log,
            has_customer_name=bool(plan.customer_name),
            has_entity_name=bool(plan.name),
            has_product_name=bool(plan.product_name),
            has_product_number=bool(plan.product_number),
            has_product_price=plan.product_price is not None,
            invoice_autocreate_product=plan.invoice_autocreate_product,
            has_payment_invoice_number=bool(plan.payment_invoice_number),
            has_payment_amount=plan.payment_amount is not None,
            has_payment_date=bool(plan.payment_date),
            has_invoice_number_in_prompt=has_invoice_number_hint,
            has_email=bool(plan.email),
            has_phone=bool(plan.phone),
            has_notes=bool(plan.notes),
            hints=plan.hints,
        )

        if plan.workflow != "noop":
            ok_creds, cred_err = tripletex_credentials_valid_for_api(bu_raw, tok_raw)
            if not ok_creds:
                log_structured(
                    logger,
                    logging.WARNING,
                    "workflow_failed",
                    request_id=rid,
                    failure_kind="credential_config",
                    error=cred_err,
                )
                log_structured(
                    logger,
                    logging.INFO,
                    "request_finished",
                    request_id=rid,
                    outcome="client_error",
                    http_status=400,
                )
                raise HTTPException(status_code=400, detail=cred_err) from None

        client = TripletexClient(
            base_url=bu_raw,
            session_token=tok_raw,
        )

        log_structured(
            logger,
            logging.INFO,
            "workflow_started",
            request_id=rid,
            workflow=plan.workflow,
            target_entity=plan.target_entity,
            detected_intent=plan.detected_intent,
        )

        try:
            result = run_workflow(plan, client)
        except WorkflowInputError as exc:
            log_structured(
                logger,
                logging.WARNING,
                "workflow_failed",
                request_id=rid,
                failure_kind="workflow_input",
                error=str(exc),
            )
            log_structured(
                logger,
                logging.INFO,
                "request_finished",
                request_id=rid,
                outcome="client_error",
                http_status=400,
            )
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except TripletexAPIError as exc:
            tripletex_failure_kind = (
                "tripletex_configuration"
                if exc.is_tenant_configuration_error()
                else "tripletex"
            )
            log_structured(
                logger,
                logging.ERROR,
                "workflow_failed",
                request_id=rid,
                failure_kind=tripletex_failure_kind,
                tripletex_http_status=exc.http_status,
                api_code=exc.code,
                message=exc.api_message,
                tripletex_request_id=exc.request_id,
            )
            log_structured(
                logger,
                logging.INFO,
                "request_finished",
                request_id=rid,
                outcome="upstream_error",
                http_status=502,
            )
            raise HTTPException(status_code=502, detail=exc.public_detail()) from exc
        except Exception as exc:
            log_structured(
                logger,
                logging.ERROR,
                "workflow_failed",
                request_id=rid,
                failure_kind="internal",
                error_type=type(exc).__name__,
            )
            logger.exception("Internal workflow error (request_id=%s)", rid)
            log_structured(
                logger,
                logging.INFO,
                "request_finished",
                request_id=rid,
                outcome="internal_error",
                http_status=500,
            )
            raise HTTPException(status_code=500, detail="Workflow execution failed") from exc

        log_structured(logger, logging.INFO, "workflow_finished", request_id=rid, **result)
        log_structured(
            logger,
            logging.INFO,
            "request_finished",
            request_id=rid,
            outcome="completed",
            http_status=200,
        )
        return {"status": "completed"}
    finally:
        solve_request_id.reset(token)


def main() -> None:
    """Run uvicorn when executing `python main.py` locally."""
    import uvicorn

    port = int(os.environ.get("PORT", DEFAULT_PORT))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)


if __name__ == "__main__":
    main()
