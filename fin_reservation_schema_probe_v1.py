#!/usr/bin/env python3
"""Fin reservation schema audit: no writes, claims, redemptions, or secrets printed.

Uses the same public config/verify endpoints that Panel_backend_merged.py uses.
By default only GET /api/panels/Fin/config is called.
--verify prompts for a real reservation code without echo; POST /verify only.
NEVER prints reservation codes, tokens, addresses, or raw server responses.
"""
from __future__ import annotations

import argparse
import ast
import getpass
import json
import pathlib
import sys
import urllib.error
import urllib.request
from collections import Counter

DEFAULT_APPDIR = pathlib.Path("/home/wa_stopshop_mb/WA/Main_program/Fis")
PROBE_FIELDS = (
    "payment_code", "payment_marker", "payment_type", "payment_method",
    "payment_status", "payment_mode", "payment_reference", "payment_ref",
    "paid", "is_paid", "paid_at", "status", "reserved_for",
    "source", "type", "reservation_type", "code_type", "code",
)

def constants_from_backend(path: pathlib.Path) -> dict:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    wanted = {"BACKEND_BASE_URL", "BACKEND_PANEL_KEY", "BACKEND_PANEL_ID_OVERRIDE"}
    found = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in wanted:
                    try:
                        value = ast.literal_eval(node.value)
                    except (ValueError, TypeError, SyntaxError):
                        continue
                    if isinstance(value, str):
                        found[target.id] = value
    if not found.get("BACKEND_BASE_URL"):
        raise RuntimeError("Could not safely read BACKEND_BASE_URL from deployed source")
    return found

def request_json(url: str, headers: dict, data: dict | None = None) -> dict:
    body = None if data is None else json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url=url, headers=headers, data=body, method="GET" if body is None else "POST")
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            obj = json.load(resp)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}; no server response body printed") from None
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Cannot reach backend ({exc.reason.__class__.__name__})") from None
    if not isinstance(obj, dict):
        raise RuntimeError("Unexpected non-object server response")
    return obj

def safe_category(value) -> str:
    if value is None or (isinstance(value, str) and not value.strip()):
        return "EMPTY"
    if isinstance(value, str) and value.strip().upper() == "PLA":
        return "PLA"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    return "OTHER"

def inspect_records(title: str, records: list) -> None:
    rows = [r for r in records if isinstance(r, dict)]
    print(f"\n=== {title} ===")
    print(f"Object records: {len(rows)}")
    if not rows:
        print("No records available for marker classification.")
        return
    names = sorted(set().union(*(r.keys() for r in rows)))
    print("Record field names:", ", ".join(str(x) for x in names))
    for field in names:
        if field in PROBE_FIELDS or "pay" in field.lower() or "marker" in field.lower():
            counts = Counter(
                safe_category(r.get(field)) if field in r else "MISSING"
                for r in rows
            )
            print(f"  {field}: " + ", ".join(f"{k}={counts[k]}" for k in sorted(counts)))

def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--verify", action="store_true",
                   help="Optionally inspect /verify schema for a code entered privately on Pi")
    args = p.parse_args()
    try:
        cfg = constants_from_backend(DEFAULT_APPDIR / "Panel_backend_merged.py")
        base_url = cfg["BACKEND_BASE_URL"].rstrip("/")
        panel_id = (cfg.get("BACKEND_PANEL_ID_OVERRIDE") or "Fin").strip()
        key = cfg.get("BACKEND_PANEL_KEY", "")
        headers = {"Content-Type": "application/json"}
        if key:
            headers["X-Panel-Key"] = key

        result = request_json(f"{base_url}/api/panels/{panel_id}/config", headers)
        print("=== FIN RESERVATION DATA-CONTRACT CHECK ===")
        print("Config fetched: yes (HTTP 2xx)")
        print("Panel identifier:", panel_id)
        print("Configuration contains reservation_codes:", "reservation_codes" in result)
        inspect_records("CONFIG reservation_codes", result.get("reservation_codes") or [])

        if args.verify:
            print("\nOptional read-only POST /api/reservations/verify")
            code = getpass.getpass("Enter a VALID test reservation code (not displayed): ").strip()
            if not code:
                print("Verification skipped.")
                return 0
            verified = request_json(
                f"{base_url}/api/reservations/verify", headers,
                {"code": code, "panel_id": panel_id}
            )
            print("Verification response field names:", ", ".join(sorted(verified)))
            # Return only field names and marker classification. Never print code, claim_token, item ID, etc.
            reservation = verified.get("reservation")
            if isinstance(reservation, dict):
                inspect_records("VERIFY reservation", [reservation])
            else:
                print("No reservation object in verify response.")
            print("Verify accepted (if provided):", safe_category(verified.get("valid")))

        print("\nREAD-ONLY COMPLETE: no claims, confirmations, door commands, payments, or local file modifications.")
        print("No raw reservation codes, tokens or credentials printed.")
        return 0
    except Exception as exc:
        print("Probe failed:", exc, file=sys.stderr)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
