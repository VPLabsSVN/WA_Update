#!/usr/bin/env python3
"""Fin V2.2 contract check. Only --claim-test mutates reservation state, by
temporarily claiming and immediately releasing a DEDICATED TEST reservation.
Never charges, redeems, confirms or sends door commands. No codes or tokens printed.
"""
from __future__ import annotations
import argparse, ast, getpass, hashlib, json, pathlib, sys, urllib.request, urllib.error

APP = pathlib.Path("/home/wa_stopshop_mb/WA/Main_program/Fis")
PACKAGE = pathlib.Path.home() / "fin_reservation_v2_2"
HELPER_SHA = "735cdaa89660f80d22d7f9d74391b3bec56f472cdf7c3370680f842104e6165b"

def config():
    tree=ast.parse((APP/"Panel_backend_merged.py").read_text(encoding="utf-8"))
    keys={"BACKEND_BASE_URL","BACKEND_PANEL_KEY","BACKEND_PANEL_ID_OVERRIDE"}
    vals={}
    for n in tree.body:
        if isinstance(n,ast.Assign):
            for t in n.targets:
                if isinstance(t,ast.Name) and t.id in keys:
                    try: v=ast.literal_eval(n.value)
                    except (TypeError,ValueError,SyntaxError): continue
                    if isinstance(v,str): vals[t.id]=v
    if not vals.get("BACKEND_BASE_URL"): raise RuntimeError("Backend URL not found")
    return vals

def request(base,path,headers,payload=None):
    data=None if payload is None else json.dumps(payload).encode("utf-8")
    req=urllib.request.Request(base.rstrip("/")+path, data=data, headers=headers, method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req,timeout=8) as r: result=json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{path} returned HTTP {e.code}; body withheld") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"{path} network failure; details withheld") from None
    if not isinstance(result,dict): raise RuntimeError("Unexpected backend response type")
    return result

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--expect",choices=("unpaid","pla","wolt"),default="unpaid")
    p.add_argument("--claim-test",action="store_true",help="Temporarily claim and automatically release a TEST reservation")
    args=p.parse_args()
    helper=PACKAGE/"fin_reservation_v2_logic.py"
    if not helper.is_file() or hashlib.sha256(helper.read_bytes()).hexdigest()!=HELPER_SHA:
        raise RuntimeError("V2.2 helper missing or hash mismatch")
    sys.path.insert(0,str(PACKAGE))
    from fin_reservation_v2_logic import reservation_route,claim_seconds_remaining
    cfg=config()
    base=cfg["BACKEND_BASE_URL"]
    panel=cfg.get("BACKEND_PANEL_ID_OVERRIDE") or "Fin"
    headers={"Content-Type":"application/json"}
    if cfg.get("BACKEND_PANEL_KEY"): headers["X-Panel-Key"]=cfg["BACKEND_PANEL_KEY"]
    print("FIN V2.2 BACKEND CONTRACT TEST - expected route:",args.expect)
    print("Use a dedicated TEST reservation while Fin is idle.")
    print("This script NEVER charges, confirms, redeems or opens a door.")
    code=getpass.getpass("Enter TEST reservation code (hidden): ").strip()
    if not code: raise RuntimeError("No test reservation code supplied")
    verified=request(base,"/api/reservations/verify",headers,{"code":code,"panel_id":panel})
    if verified.get("valid") is not True or not isinstance(verified.get("reservation"),dict):
        print("FAIL: reservation did not verify correctly; nothing claimed")
        return 1
    route=reservation_route(verified["reservation"])
    print("Verified backend route:",route)
    if route!=args.expect:
        print("STOP: route unexpected or blocked; nothing claimed")
        return 1
    config_data=request(base,f"/api/panels/{panel}/config",headers)
    rows=config_data.get("reservation_codes") or []
    matching=[row for row in rows if isinstance(row,dict) and row.get("code")==code]
    if matching:
        consistent=all(reservation_route(row)==route for row in matching)
        print("Config and verify agree:",consistent)
        if not consistent: return 1
    else:
        print("No matching config record; independent config comparison unavailable")
    if not args.claim_test:
        print("READ-ONLY COMPLETE: no claim was made")
        return 0
    print("CAUTION: claiming a test reservation temporarily changes server inventory state.")
    phrase=input("Type CLAIM-AND-RELEASE-TEST to proceed: ").strip()
    if phrase!="CLAIM-AND-RELEASE-TEST":
        print("Cancelled; no claim made")
        return 1
    token=None
    try:
        hold=600 if route=="unpaid" else 60
        claimed=request(base,"/api/reservations/claim",headers,{
            "code":code,"panel_id":panel,"claimed_by":"panel","hold_seconds":hold})
        token=claimed.get("claim_token")
        if not isinstance(token,str) or not token:
            print("STOP: claim token missing; operator must check server state")
            return 1
        reservation=claimed.get("reservation")
        if not isinstance(reservation,dict):
            print("FAIL: claim reservation object absent")
            return 1
        claimed_route=reservation_route(reservation)
        print("Claim backend route:",claimed_route)
        if claimed_route!=route:
            print("FAIL: verify/claim markers inconsistent")
            return 1
        seconds=claim_seconds_remaining(claimed)
        if seconds is None:
            print("Claim expiry: MISSING / UNRECOGNIZED")
        else:
            print(f"Claim time remaining (approx): {seconds:.0f} seconds")
        if route=="unpaid" and (seconds is None or seconds<180):
            print("FAIL: V2.2 would reject unpaid checkout before payment")
            return 1
        print("PASS: V2.2 marker and lease requirements appear satisfied")
        return 0
    finally:
        if token:
            try:
                r=request(base,"/api/reservations/release",headers,{
                    "code":code,"claim_token":token,"panel_id":panel,
                    "released_by":"panel","release_reason":"fin_contract_test"})
                if any(r.get(k) is False for k in ("ok","released","success") if k in r):
                    print("WARNING: release endpoint signaled failure")
                elif any(r.get(k) is True for k in ("ok","released","success")):
                    print("Release endpoint acknowledged success")
                else:
                    print("Release response returned without an explicit success flag")
                again=request(base,"/api/reservations/verify",headers,{"code":code,"panel_id":panel})
                print("Post-release reservation valid:",again.get("valid") is True)
            except Exception:
                print("WARNING: release or follow-up verification failed; inspect server state.")
        else:
            print("No claim token; release could not be attempted. Verify server state manually.")

if __name__=="__main__":
    try: raise SystemExit(main())
    except Exception as exc:
        print("Contract test stopped:",type(exc).__name__,str(exc) if isinstance(exc,RuntimeError) else "",file=sys.stderr)
        raise SystemExit(1)
