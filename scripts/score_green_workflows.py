#!/usr/bin/env python3
"""
Kontrollert /solve-runde mot lokal Uvicorn (score mode) — parser stdout-logg for hendelser.
Bruker credentials fra examples/local.solve_list_employees.json
"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples" / "local.solve_list_employees.json"
LOG = ROOT / ".score_mode_verify.log"
PORT = int(os.environ.get("SCORE_PORT", "9966"))


def load_creds() -> dict[str, Any]:
    data = json.loads(EXAMPLES.read_text(encoding="utf-8"))
    return data["tripletex_credentials"]


def parse_log_events(log_text: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for line in log_text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            o = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(o, dict) and "event" in o:
            out.append(o)
    return out


def events_for_last_request(events: list[dict[str, Any]], request_id: str) -> list[dict[str, Any]]:
    return [e for e in events if e.get("request_id") == request_id]


def main() -> int:
    if not EXAMPLES.exists():
        print("Missing", EXAMPLES, file=sys.stderr)
        return 1
    creds = load_creds()
    if "PASTE" in creds.get("session_token", ""):
        print("Set real session token in", EXAMPLES, file=sys.stderr)
        return 1

    LOG.unlink(missing_ok=True)
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", str(PORT)],
        cwd=str(ROOT),
        stdout=open(LOG, "w", encoding="utf-8"),
        stderr=subprocess.STDOUT,
    )
    time.sleep(2)
    if proc.poll() is not None:
        print("Uvicorn failed to start; see", LOG, file=sys.stderr)
        return 1

    tests: list[tuple[str, str]] = [
        ("list_employees", "list employees"),
        ("list_employees", "ansatte"),
        ("list_employees", "Kan du vise ansatte?"),
        ("search_customer", "finn kunde Acme"),
        ("search_customer", "search customer Acme"),
        ("search_customer", "finn kunde"),
        ("search_product", "finn produkt Kaffe"),
        ("search_product", "søk produkt Kaffe"),
        ("search_product", "liste produkter"),
        ("create_customer", "opprett kunde Score Mode Unik 20260320"),
        ("create_customer", "opprett kunde Agent Verify NM 20260320"),
        ("create_customer", "opprett kunde Acme AS"),
        ("create_product", "opprett produkt Score Mode Vare Alfa pris 49 kr"),
        ("create_product", "nytt produkt Score Mode Vare Beta 10 kr"),
        ("create_product", "create product Score Mode Vare Gamma"),
    ]

    results: list[dict[str, Any]] = []
    base = f"http://127.0.0.1:{PORT}/solve"

    try:
        for wf, prompt in tests:
            body = {"prompt": prompt, "files": [], "tripletex_credentials": creds}
            r = requests.post(base, json=body, timeout=120)
            log_tail = LOG.read_text(encoding="utf-8") if LOG.exists() else ""
            events = parse_log_events(log_tail)
            # siste request_received
            req_ids = [e["request_id"] for e in events if e.get("event") == "request_received"]
            rid = req_ids[-1] if req_ids else ""
            mine = events_for_last_request(events, rid) if rid else []
            wf_ev = next((e for e in reversed(mine) if e.get("event") == "plan_built"), None)
            built = wf_ev.get("workflow") if wf_ev else None
            fin = next((e for e in reversed(mine) if e.get("event") == "workflow_finished"), None)
            fail = next((e for e in reversed(mine) if e.get("event") == "workflow_failed"), None)
            http_counts = len(
                [e for e in mine if e.get("event") == "tripletex_http"]
            )
            hint = [e for e in mine if e.get("event") == "tripletex_list_count_hint"]
            row: dict[str, Any] = {
                "expected_workflow": wf,
                "prompt": prompt[:80],
                "http_status": r.status_code,
                "body_status": (r.json() or {}).get("status") or (r.json() or {}).get("detail", "")[:120],
                "plan_workflow": built,
                "plan_match": built == wf,
                "workflow_finished": fin,
                "workflow_failed": fail,
                "tripletex_http_calls": http_counts,
                "tripletex_list_count_hint": len(hint),
            }
            if fin:
                row["finished_keys"] = {k: v for k, v in fin.items() if k not in ("level", "event", "request_id")}
            results.append(row)
            time.sleep(0.15)
    finally:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    out_path = ROOT / ".score_mode_results.json"
    out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(results, indent=2, ensure_ascii=False))
    print("Wrote", out_path, file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
