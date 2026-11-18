#!/usr/bin/env python3
"""Read-only Fin PLA/Wolt routing audit.

Requests only GET panel config and POST reservation verify, never claim, release,
payment, confirmation, redemption, or hardware. Never displays codes, payload
contents, credentials, URLs, customer names, or raw server responses.
"""
from __future__ import annotations

import ast
import getpass
import importlib.util
import json
import pathlib
import re
import sys
import urllib.error
import urllib.request

APP = pathlib.Path("/home/wa_stopshop_mb/WA/Main_program/Fis")
LOGIC = pathlib.Path.home() / "fin_reservation_v2_2" / "fin_reservation_v2_logic.py"
KNOWN_FIELDS = {
    "payment_marker", "payment_code", "payment_type", "marker",
    "reservation_marker", "is_paid", "paid", "payment_status",
    "reserved_for", "reservation_type", "source", "order_source",
    "provider", "platform", "channel", "type", "wolt", "is_wolt",
    "reserved_for_wolt",
}

def backend_settings():
    source = (APP / "Panel_backend_merged.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    found = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in {
                    "BACKEND_BASE_URL", "BACKEND_PANEL_KEY", "BACKEND_PANEL_ID_OVERRIDE"
                }:
                    try:
                        value = ast.literal_eval(node.value)
                    except (ValueError, SyntaxError, TypeError):
                        continue
                    if isinstance(value, str):
                        found[target.id] = value
    if not found.get("BACKEND_BASE_URL"):
        raise RuntimeError("Backend address cannot be read from the installed source")
    return found

def call(url, headers, data=None):
    payload = json.dumps(data).encode("utf-8") if data is not None else None
    request = urllib.request.Request(
        url, data=payload, headers=headers,
        method="POST" if payload is not None else "GET"
    )
    try:
        with urllib.request.urlopen(request, timeout=8) as resp:
            obj = json.load(resp)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Backend returned HTTP {exc.code}; response hidden") from None
    except urllib.error.URLError:
        raise RuntimeError("Backend unavailable; network details hidden") from None
    if not isinstance(obj, dict):
        raise RuntimeError("Unexpected backend response type")
    return obj

def scalar_label(value):
    if value is None or (isinstance(value, str) and not value.strip()):
        return "EMPTY"
    if isinstance(value, str) and value.strip().casefold() in ("pla", "wolt"):
        return value.strip().upper()
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    return "OTHER (withheld)"

def payload_shape(value):
    if value is None or value == "":
        return "empty", value
    if isinstance(value, str):
        s=value.strip()
        if s.casefold()=="pla":
            return "exact PLA string", value
        if s.casefold()=="wolt":
            return "exact WOLT string", value
        try:
            decoded=json.loads(s)
            return "JSON-encoded " + type(decoded).__name__, decoded
        except (ValueError, TypeError):
            if re.search(r"(?i)(?<![a-z0-9])pla(?![a-z0-9])", s):
                return "non-JSON text containing PLA token (not trusted)", value
            return "non-JSON text (contents withheld)", value
    return type(value).__name__, value

def nested_pla_values(obj, depth=0):
    """Look for exact PLA values, never inspect user code or labels."""
    if depth>5:
        return 0
    if isinstance(obj, dict):
        return sum(nested_pla_values(v,depth+1) for k,v in obj.items()
                   if k not in ("code","label","item_id","panel_position"))
    if isinstance(obj, list):
        return sum(nested_pla_values(v,depth+1) for v in obj[:50])
    return int(isinstance(obj, str) and obj.strip().casefold()=="pla")

def details(label, rec, routing):
    if not isinstance(rec, dict):
        print(f"{label}: response has no reservation object")
        return
    markers, paid, wolt, opaque = routing._metadata_values(rec)
    print(f"\n=== {label} (values redacted) ===")
    print("V2.2 route:", routing.reservation_route(rec))
    print("Fields present:", ", ".join(sorted(str(k) for k in rec.keys())))
    print("Recognized marker classes:",
          ", ".join(scalar_label(x) for x in markers) if markers else "NONE")
    print("Paid-state classes:",
          ", ".join(scalar_label(x) for x in paid) if paid else "NONE")
    print("Wolt recognized:", wolt)
    print("Parsing ambiguity:", opaque)
    kind, parsed = payload_shape(rec.get("payload"))
    print("Payload representation:", kind)
    print("Exact PLA values within parsed payload:", nested_pla_values(parsed))
    fields = sorted(k for k in rec if str(k) in KNOWN_FIELDS)
    print("Recognized top-level field categories:")
    for key in fields:
        print(f"  {key}: {scalar_label(rec[key])}")
    if rec.get("payload") is not None and routing.reservation_route(rec)=="unpaid":
        print("Note: this record contains no PLA marker recognized by V2.2.")
    # No values for any personal, financial, or reservation identification fields.

def main():
    if not LOGIC.is_file():
        raise RuntimeError("V2.2 logic not found; extract the V2.2 patch first")
    spec=importlib.util.spec_from_file_location("fin_v2_2_logic_audit", LOGIC)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cfg=backend_settings()
    base=cfg["BACKEND_BASE_URL"].rstrip("/")
    panel=cfg.get("BACKEND_PANEL_ID_OVERRIDE") or "Fin"
    headers={"Content-Type":"application/json"}
    if cfg.get("BACKEND_PANEL_KEY"):
        headers["X-Panel-Key"]=cfg["BACKEND_PANEL_KEY"]
    code=getpass.getpass("Numeric TEST reservation code (hidden): ").strip()
    if not code.isascii() or not code.isdigit():
        raise RuntimeError("Enter an existing numeric test reservation code")
    verify=call(base+"/api/reservations/verify", headers,
                {"code":code,"panel_id":panel})
    print("Backend verify valid:", verify.get("valid") is True)
    if verify.get("valid") is not True:
        print("No record inspected; no changes made.")
        return 1
    details("VERIFY", verify.get("reservation"), module)
    config=call(base+f"/api/panels/{panel}/config",headers)
    records=config.get("reservation_codes") or []
    matches=[r for r in records if isinstance(r,dict) and r.get("code")==code]
    print("\nMatching config records:",len(matches))
    for rec in matches[:2]:
        details("CONFIG",rec,module)
    if len(matches)>2:
        print("Additional config matches withheld")
    print("\nREAD-ONLY: no claims, payments, door commands, or file modifications.")
    return 0

if __name__=="__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print("Stopped:",type(exc).__name__,
              str(exc) if isinstance(exc, RuntimeError) else "",
              file=sys.stderr)
        raise SystemExit(1)
