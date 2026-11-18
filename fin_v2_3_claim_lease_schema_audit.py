#!/usr/bin/env python3
"""Fin V2.3 claim-lease schema audit (TEST reservations only).

GET config, POST verify, optional POST claim and POST release. No payment,
confirmation, redemption, door command or file changes. Raw response values,
reservation code, claim token, server address and credentials never printed.

The claim changes server reservation state temporarily. If release fails,
the operator must reconcile the test reservation before using it again.
"""
from __future__ import annotations
import argparse
import ast
from datetime import datetime, timezone
import getpass
import json
import pathlib
import sys
import urllib.error
import urllib.request

APPDIR = pathlib.Path("/home/wa_stopshop_mb/WA/Main_program/Fis")
PACKAGE = pathlib.Path.home() / "fin_reservation_v2_3"

def settings():
    tree = ast.parse((APPDIR / "Panel_backend_merged.py").read_text(encoding="utf-8"))
    allowed = {"BACKEND_BASE_URL", "BACKEND_PANEL_KEY", "BACKEND_PANEL_ID_OVERRIDE"}
    result = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in allowed:
                    try: value = ast.literal_eval(node.value)
                    except (TypeError, ValueError, SyntaxError): continue
                    if isinstance(value, str): result[target.id] = value
    if not result.get("BACKEND_BASE_URL"):
        raise RuntimeError("Backend settings unavailable")
    return result

def request(base, path, headers, payload=None):
    encoded = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(base.rstrip("/") + path, data=encoded, headers=headers,
                                 method="POST" if encoded is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=8) as response:
            obj = json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Server returned HTTP {exc.code}; body withheld") from None
    except urllib.error.URLError:
        raise RuntimeError("Server connection failed; details withheld") from None
    if not isinstance(obj, dict):
        raise RuntimeError("Server response is not a JSON object")
    return obj

def field_names(obj):
    if not isinstance(obj, dict):
        return "(not an object)"
    # Only schema names; never dump reservation codes, tokens or payload values
    return ", ".join(sorted(str(k) for k in obj)) or "(empty)"

def lease_candidates(obj, prefix):
    if not isinstance(obj, dict):
        return
    for key, value in sorted(obj.items()):
        name = str(key)
        lower = name.casefold()
        if not any(s in lower for s in ("claim", "hold", "lease", "expir", "ttl", "until")):
            continue
        if any(s in lower for s in ("token", "secret", "key", "code")):
            continue
        kind = type(value).__name__
        description = kind
        if value is None or value == "":
            description += " (empty)"
        elif isinstance(value, str):
            try:
                date = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if date.tzinfo is not None:
                    sec = (date - datetime.now(timezone.utc)).total_seconds()
                    description += f" (timezone-aware timestamp; approx {round(sec)} seconds ahead)"
                else:
                    description += " (timestamp without timezone)"
            except ValueError:
                description += " (non-ISO text; contents withheld)"
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            description += " (numeric; value withheld)"
        print(f"  {prefix}.{name}: {description}")

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--expect", choices=("unpaid", "pla", "wolt"), default="unpaid")
    p.add_argument("--claim-test", action="store_true")
    args = p.parse_args()
    helper = PACKAGE / "fin_reservation_v2_logic.py"
    if not helper.is_file():
        raise RuntimeError("V2.3 helper is missing from ~/fin_reservation_v2_3")
    sys.path.insert(0, str(PACKAGE))
    from fin_reservation_v2_logic import reservation_route, claim_seconds_remaining

    cfg = settings()
    panel = cfg.get("BACKEND_PANEL_ID_OVERRIDE") or "Fin"
    base = cfg["BACKEND_BASE_URL"]
    headers = {"Content-Type": "application/json"}
    if cfg.get("BACKEND_PANEL_KEY"):
        headers["X-Panel-Key"] = cfg["BACKEND_PANEL_KEY"]

    print("=== FIN V2.3 SERVER CLAIM SCHEMA AUDIT ===")
    print("Dedicated TEST reservation only. No payment, redemption, or door commands.")
    code = getpass.getpass("Numeric TEST reservation code (hidden): ").strip()
    if not code.isascii() or not code.isdigit():
        raise RuntimeError("A numeric TEST reservation code is required")
    verified = request(base, "/api/reservations/verify", headers,
                       {"code": code, "panel_id": panel})
    rec = verified.get("reservation")
    if verified.get("valid") is not True or not isinstance(rec, dict):
        print("STOP: reservation not valid; nothing claimed")
        return 1
    route = reservation_route(rec)
    print("Verified marker route:", route)
    if route != args.expect:
        print("STOP: route mismatch; nothing claimed")
        return 1
    print("Verified reservation field names:", field_names(rec))

    if not args.claim_test:
        print("READ-ONLY COMPLETE. Use --claim-test to inspect one server claim.")
        return 0

    answer = input("Type CLAIM-AND-RELEASE-TEST to proceed: ").strip()
    if answer != "CLAIM-AND-RELEASE-TEST":
        print("Cancelled; nothing claimed")
        return 1

    token = None
    passed = False
    release_confirmed = False
    try:
        claimed = request(base, "/api/reservations/claim", headers, {
            "code": code, "panel_id": panel, "claimed_by": "panel",
            "hold_seconds": 600 if route == "unpaid" else 60,
        })
        token = claimed.get("claim_token")
        print("Claim response field names:", field_names(claimed))
        claim_rec = claimed.get("reservation")
        print("Claim reservation field names:", field_names(claim_rec))
        print("Lease-related field types (values withheld):")
        lease_candidates(claimed, "claim")
        lease_candidates(claim_rec, "reservation")
        if not isinstance(token, str) or not token:
            print("STOP: no claim token; release may need manual operator action")
            return 1
        if not isinstance(claim_rec, dict) or reservation_route(claim_rec) != route:
            print("FAIL: claimed reservation metadata mismatch")
            return 1
        secs = claim_seconds_remaining(claimed)
        print("V2.3 recognized claim expiry:",
              "MISSING/INVALID" if secs is None else f"approx {round(secs)} seconds remaining")
        passed = (route != "unpaid" or secs is not None and secs >= 180)
        print("V2.3 route preflight:", "PASS" if passed else "FAIL")
        return 0 if passed else 1
    finally:
        if token:
            try:
                released = request(base, "/api/reservations/release", headers, {
                    "code": code, "claim_token": token, "panel_id": panel,
                    "released_by": "panel", "release_reason": "claim_lease_schema_audit",
                })
                release_confirmed = (
                    not any(released.get(k) is False for k in ("ok", "released", "success") if k in released)
                    and any(released.get(k) is True for k in ("ok", "released", "success"))
                )
                print("Release acknowledged:", "YES" if release_confirmed else "NO/UNKNOWN")
                if release_confirmed:
                    after = request(base, "/api/reservations/verify", headers,
                                    {"code": code, "panel_id": panel})
                    print("Post-release reservation valid:", after.get("valid") is True)
                    if after.get("valid") is not True:
                        release_confirmed = False
            except Exception:
                print("WARNING: release or follow-up verification failed; check server state")
        if not release_confirmed:
            print("WARNING: claim release not proven. Inspect the TEST reservation before reusing it.")
        elif not passed:
            print("Test reservation released, but V2.3 unpaid checkout remains BLOCKED.")

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print("Stopped:", type(exc).__name__,
              str(exc) if isinstance(exc, RuntimeError) else "", file=sys.stderr)
        raise SystemExit(1)
