#!/usr/bin/env python3
"""BB Reservation V2.4 (Pi only) — guarded adaptation of Fin's tested policy.

Requires verified Fin_Reservation_V2_4_Pi_Only_Safe_Patch.zip (used as source
of audited logic, NOT applied as its Fin-specific patch).
Actions:
  --check              OFFLINE: BB source/hash gate, build candidate and 39 tests
  --prepare-proof-tool  Create isolated home-directory BB observer + pure policy
  --apply              Require fresh BB hold proof; backup then replace disk files
  --rollback DIR        Restore version-locked backup IF no unresolved payment
No Kivy launch, remote relay calls, service restart, backend change or payment.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

APP=Path("/home/pi/WA/Main_program/Fis")
HOME=Path("/home/pi")
PKG=HOME/"Fin_Reservation_V2_4_Pi_Only_Safe_Patch.zip"
ZIP_SHA="5285f8a42fa68b1654b914590f3b9942a0b45c60bc5d3dedcbd5784f1d1f9d52"
PERF_BACKUP=HOME/"bb_performance_v1_backups/20261119_183059"
BACKUPS=HOME/"bb_reservation_v24_backups"
BACKEND=APP/"Panel_backend_merged.py"
PANEL=APP/"Panel.py"
POLICY="bb_reservation_v24_policy.py"
PROOF=HOME/".config/bouquetmat/bb_v24_hold_proof.json"
LEDGER=APP/".bb_reservation_v24_recovery.json"
EXPECTED_ORIG={
    "Panel.py":"f7bdcb3a4a2fc8337e59552baaab1a37af3d3fd238c909ad1a1775d7780fe20f",
    "Panel_backend_merged.py":"398e61cceca5e5520859c1cc1ec902b6d27ef878b7e08d61e981f5113cdca7ca",
}

def stop(msg):
    raise RuntimeError(msg)

def hash_bytes(data):
    return hashlib.sha256(data).hexdigest()

def digest(path):
    return hash_bytes(path.read_bytes())

def read_manifest():
    try:
        lines=(PERF_BACKUP/"new_sha256.txt").read_text().splitlines()
        out={line.split("  ",1)[1]:line.split("  ",1)[0] for line in lines}
    except (OSError, ValueError):
        stop("Missing BB Performance Stage 1 backup SHA manifest")
    for name,expected in EXPECTED_ORIG.items():
        b=PERF_BACKUP/name
        if not b.is_file() or digest(b)!=expected:
            stop("BB performance original backup invalid: "+name)
        if name not in out or digest(APP/name)!=out[name]:
            stop("Running BB source revision differs from installed Performance Stage 1: "+name)
    return out

def package():
    if not PKG.is_file() or digest(PKG)!=ZIP_SHA:
        stop("Missing/mismatched verified Fin V2.4 source package: "+str(PKG))
    with zipfile.ZipFile(PKG) as z:
        needed=["Fin_Reservation_V2_4.patch","fin_reservation_v2_logic.py",
                "fin_v24_observe_claim_hold.py","test_fin_reservation_v2.py"]
        if any(name not in z.namelist() for name in needed):
            stop("Required audited Fin V2.4 package entries missing")
        return {name:z.read(name).decode("utf-8") for name in needed}

def replaced(s,old,new,label):
    n=s.count(old)
    if n!=1:stop(f"{label}: source anchor expected once, found {n}")
    return s.replace(old,new,1)

def method(source,name):
    tree=ast.parse(source)
    klass=[n for n in tree.body if isinstance(n,ast.ClassDef)
           and n.name=="PanelBackendReservationApp"]
    if len(klass)!=1:stop("BB app class missing")
    funcs=[n for n in klass[0].body if isinstance(n,ast.FunctionDef) and n.name==name]
    if len(funcs)!=1:stop(f"BB method {name}: expected 1, found {len(funcs)}")
    node=funcs[0]
    lines=source.splitlines(keepends=True)
    return "".join(lines[node.lineno-1:node.end_lineno])

def replace_method(source,name,new_method):
    old=method(source,name)
    return replaced(source,old,new_method, "BB "+name)

def patch_new_side(diff):
    out=[]
    for line in diff.splitlines():
        if line.startswith("@@"):out.append("")
        elif line.startswith("---") or line.startswith("+++"):continue
        elif line.startswith(" ") or line.startswith("+"):out.append(line[1:])
    return "\n".join(out)

def get_additions(diff):
    new=patch_new_side(diff)
    a="    # ---------- Fin reservation checkout V2 ----------"
    b="    def _reservation_collect_claimed("
    if new.count(a)!=1 or new.count(b)!=1:
        stop("Fin V2.4 vetted additions not found uniquely")
    start=new.index(a)
    stop_at=new.index(b,start)
    added=new[start:stop_at].replace("Fin reservation checkout","BB reservation checkout")
    added=added.replace("[FIN V2.4]","[BB V2.4]").replace("[FIN V2]","[BB V2]")
    added=added.replace(".fin_reservation_v2_recovery.json",".bb_reservation_v24_recovery.json")
    added=added.replace("fin_v24_hold_proof.json","bb_v24_hold_proof.json")
    added=added.replace("_fin_v24_","_bb_v24_")
    return added.rstrip()+"\n\n"

def get_i18n(diff):
    new=patch_new_side(diff)
    a="_FIN_V2_I18N = {"
    b="base.I18N.update(_FIN_V2_I18N)"
    if new.count(a)!=1 or new.count(b)!=1:stop("Fin translations missing/duplicated")
    segment=new[new.index(a):new.index(b)+len(b)]
    return segment.replace("_FIN_V2_I18N","_BB_V24_I18N")+"\n\n"

def bb_collect(original):
    a=original
    a=replaced(a,
        "    def reservation_collect(self):\n",
        "    def reservation_collect(self):\n"
        "        # BB V2: prevent duplicate claim/checkout.\n"
        "        if self._reservation_v2_pending or self._checkout_in_progress:\n"
        "            return\n",
        "reservation double-claim gate")
    pre='''                verified = self.reservation_client.verify(code=code, panel_id=panel_id)
                if not bool((verified or {}).get("valid")):
                    Clock.schedule_once(lambda _dt, r=verified:
                        self._reservation_verify_then_collect_invalid(r), 0)
                    return
                pre_record = (verified or {}).get("reservation") or {}
                pre_route = reservation_route(pre_record)
                # A missing payment-metadata contract cannot justify collection/charging.
                marker_keys = (
                    "payload", "payment_marker", "payment_code", "payment_type",
                    "marker", "reservation_marker", "reserved_for", "is_wolt",
                    "wolt", "reserved_for_wolt", "source", "provider", "platform",
                    "channel", "reservation_type", "order_source", "type",
                )
                if not any(k in pre_record for k in marker_keys):
                    pre_route = "blocked"
                if pre_route == "blocked":
                    Clock.schedule_once(lambda _dt:
                        self._reservation_collect_failed("unsupported_payment_marker"), 0)
                    return
                requested_hold = (RESERVATION_HOLD_SECONDS
                    if pre_route in ("pla","wolt")
                    else RESERVATION_PAYMENT_HOLD_SECONDS)
'''
    a=replaced(a,
       "            try:\n                claim_result = self.reservation_client.claim(\n",
       "            try:\n"+pre+"                claim_result = self.reservation_client.claim(\n",
       "verify before claim")
    a=replaced(a,
       "                    hold_seconds=RESERVATION_HOLD_SECONDS,\n",
       "                    hold_seconds=requested_hold,\n",
       "claim hold")
    a=replaced(a,
       '            if not bool((claim_result or {}).get("claimed")):\n',
       '            if not bool((claim_result or {}).get("claimed")) or not bool((claim_result or {}).get("ok")):\n',
       "authoritative claim ok")
    old='''            reservation = (claim_result or {}).get("reservation") or {}
            claim_token = '''
    if a.count(old)!=1:stop("Claim response token region changed")
    # Match one entire code line; source redaction can change literal keys,
    # so this patch deliberately writes the expected backend claim_token contract.
    a,n=re.subn(r'(?m)^            claim_token = str\(\(claim_result or \{\}\)\.get\([^\n]+\)$',
                '            claim_token = str((claim_result or {}).get("claim_token") or "")',
                a,count=1)
    if n!=1:stop("Cannot safely extract claim_token from BB claim response")
    old='''            Clock.schedule_once(lambda _dt, r=reservation, c=code, t=claim_token: self._reservation_collect_claimed(c, t, r), 0)'''
    new='''            if isinstance(claim_result, dict):
                claim_result["_bb_v24_claim_received_monotonic"] = time.monotonic()
                claim_result["_bb_v24_requested_hold"] = requested_hold
            Clock.schedule_once(
                lambda _dt, r=reservation, c=code, t=claim_token,
                info=claim_result, before=pre_route:
                    self._reservation_collect_claimed(c, t, r,
                        claim_info=info, pre_route=before), 0
            )'''
    a=replaced(a,old,new,"claimed callback")
    return a

def bb_claimed(original):
    a=replaced(original,
       "    def _reservation_collect_claimed(self, code: str, claim_token: str, reservation: dict):",
       "    def _reservation_collect_claimed(self, code: str, claim_token: str, reservation: dict, claim_info=None, pre_route=None):",
       "claimed signature")
    branch='''        # Verified backend metadata only. No typed-code or inventory/LED heuristics.
        route = reservation_route(reservation)
        marker_keys = (
            "payload", "payment_marker", "payment_code", "payment_type", "marker",
            "reservation_marker", "reserved_for", "is_wolt", "wolt",
            "reserved_for_wolt", "source", "provider", "platform", "channel",
            "reservation_type", "order_source", "type",
        )
        if not any(k in reservation for k in marker_keys):
            route = "blocked"
        matches = [row for row in (self.server_reservation_codes or [])
                   if isinstance(row, dict)
                   and str(row.get("code") or "") == str(code)]
        if matches and any(reservation_route(row)!=route for row in matches):
            route = "blocked"
        if pre_route is not None and route!=pre_route:
            route = "blocked"
        if route not in ("pla","wolt"):
            if route=="unpaid":
                return self._reservation_v2_start_checkout(
                    code, claim_token, reservation, claim_info or {})
            self._reservation_release_async(
                code=code, claim_token=claim_token,
                reason="unsupported_payment_marker")
            rs.ids.reservation_status.text = self.t("reservation_backend_error")
            rs.ids.reservation_collect_btn.disabled = False
            self.backend_post_event_async("reservation_payment_route_blocked",
                severity="warning", payload={"reason":"metadata_mismatch"})
            return

'''
    a=replaced(a,
       '        self.backend_post_event_async(\n            "reservation_claimed",',
       branch+'        self.backend_post_event_async(\n            "reservation_claimed",',
       "claimed payment routing")
    a=replaced(a,
       'payload={"reservation": reservation, "claim_token": claim_token}',
       'payload={"panel_position": panel_position, "item_id": item_id, "route": route}',
       "avoid claim token in events")
    return a

def door_gate(method_text):
    anchor="            if on_complete:\n                on_complete(results)"
    inject='''            # Paid reservation state clears only after the actual relay result.
            delivery = self._reservation_v2_delivery_ctx
            if delivery:
                expected_id = int(delivery["item_id"])
                accepted = any(
                    int(r.get("id", -1)) == expected_id and bool(r.get("ok"))
                    for r in (results or [])
                )
                self._reservation_v2_delivery_ctx = None
                if accepted:
                    self._reservation_v2_clear_recovery()
                    self.backend_post_event_async(
                        "reservation_paid_door_command_ok",
                        payload={"invoice":delivery["invoice"],"item_id":expected_id})
                else:
                    try:
                        self._reservation_v2_store_recovery(
                            delivery,"paid_door_command_failed_manual_review")
                    except Exception:
                        print("[BB V2] CRITICAL: door failure recovery write failed")
                    self.backend_post_event_async(
                        "reservation_paid_door_command_failed",
                        severity="error",
                        payload={"invoice":delivery["invoice"],"item_id":expected_id})
                    self._reservation_v2_error(self.t("v2_door_failed"))
'''
    return replaced(method_text,anchor,inject+anchor,"paid door callback")

def transform_backend(source,diff):
    s=source
    s=replaced(s,"RESERVATION_HOLD_SECONDS = 60\n",
        "RESERVATION_HOLD_SECONDS = 60\n"
        "RESERVATION_PAYMENT_HOLD_SECONDS = 600  # BB V2.4 unpaid claim\n",
        "hold constant")
    s=replaced(s,"import Panel as base\n",
        "import Panel as base\n"
        "from bb_reservation_v24_policy import (reservation_route,\n"
        "    claim_seconds_remaining, validated_unreported_claim_window)\n",
        "routing policy import")
    s=replaced(s,"_RESERVATION_SCREEN_BLOCK = '''",
        get_i18n(diff)+"_RESERVATION_SCREEN_BLOCK = '''","translation insertion")
    needle="        self._reservation_claim_token: str | None = None\n"
    new=(needle+
        "        self._reservation_v2_pending = None\n"
        "        self._reservation_v2_delivery_ctx = None\n"
        "        self._reservation_v2_recovery_path = os.path.join(\n"
        "            os.path.dirname(__file__), '.bb_reservation_v24_recovery.json')\n")
    s=replaced(s,needle,new,"pending reservation state")
    s=replace_method(s,"reservation_collect",bb_collect(method(s,"reservation_collect")))
    s=replace_method(s,"_reservation_collect_claimed",bb_claimed(method(s,"_reservation_collect_claimed")))
    # Rename the original unchanged successful-purchase code to the standard
    # delegate. The audited V2 override calls it after server confirmation.
    s=replaced(s,
       "    def _complete_successful_purchase(self, items, invoice_number: str, issue_datetime_iso: str, total: float):",
       "    def _complete_successful_purchase_standard(self, items, invoice_number: str, issue_datetime_iso: str, total: float):",
       "standard retail delegate")
    s=replaced(s,
       "    def _reservation_collect_claimed(",
       get_additions(diff)+"    def _reservation_collect_claimed(",
       "Fin V2.4 guarded methods")
    s=replace_method(s,"open_doors_sequential",
        door_gate(method(s,"open_doors_sequential")))
    pay=method(s,"_handle_payment_failure")
    pay=replaced(pay,
        "        super()._handle_payment_failure(items, error_text)",
        "        return self._reservation_v2_handle_payment_failure(items, error_text)",
        "failed payment delegate")
    s=replace_method(s,"_handle_payment_failure",pay)
    pay=method(s,"_handle_payment_result")
    pay=replaced(pay,
        "        super()._handle_payment_result(items, invoice_number, issue_datetime_iso, total, result)",
        "        return self._reservation_v2_handle_payment_result(items, invoice_number, issue_datetime_iso, total, result)",
        "paid payment delegate")
    s=replace_method(s,"_handle_payment_result",pay)
    ast.parse(s,filename="BB patched backend")
    compile(s,"BB patched backend","exec")
    # Avoid unintentional duplicate method overrides.
    klass=next(n for n in ast.parse(s).body if isinstance(n,ast.ClassDef)
               and n.name=="PanelBackendReservationApp")
    names=[n.name for n in klass.body if isinstance(n,ast.FunctionDef)]
    if len(names)!=len(set(names)):stop("Duplicate backend class methods after patch")
    for key in ("reservation_collect","_reservation_collect_claimed",
                "_complete_successful_purchase","_complete_successful_purchase_standard",
                "_reservation_v2_start_checkout","_reservation_v2_finish_paid"):
        if names.count(key)!=1:stop("Missing/duplicated method "+key)
    return s

def adapt_assets(z):
    helper=z["fin_reservation_v2_logic.py"]
    helper=helper.replace("fin-v24-observed-claim-hold-v1",
                          "bb-v24-observed-claim-hold-v1")
    helper=helper.replace("_fin_v24_","_bb_v24_")
    observer=z["fin_v24_observe_claim_hold.py"]
    observer=observer.replace(
        "/home/wa_stopshop_mb/WA/Main_program/Fis",
        "/home/pi/WA/Main_program/Fis")
    observer=observer.replace("fin_reservation_v2_logic.py",POLICY)
    observer=observer.replace("fin_v24_hold_proof.json","bb_v24_hold_proof.json")
    observer=observer.replace("fin-v24-observed-claim-hold-v1",
                               "bb-v24-observed-claim-hold-v1")
    observer=observer.replace("_fin_v24_","_bb_v24_")
    observer=observer.replace("'Fin'","'BB'").replace('"Fin"','"BB"')
    observer=observer.replace("FIN V2.4","BB V2.4").replace("Fin is idle","BB is idle")
    observer=observer.replace("fin_v24_test","bb_v24_test")
    tests=z["test_fin_reservation_v2.py"]
    tests=tests.replace("fin_reservation_v2_logic.py",POLICY)
    tests=tests.replace("fin_reservation_v2_logic","bb_reservation_v24_policy")
    tests=tests.replace("_fin_v24_","_bb_v24_")
    tests=tests.replace("fin-v24-observed-claim-hold-v1",
                        "bb-v24-observed-claim-hold-v1")
    tests=tests.replace("_FIN_V2_I18N","_BB_V24_I18N")
    tests=tests.replace('"Fin"','"BB"').replace("'Fin'","'BB'")
    tests=tests.replace("'BACKEND_PANEL_ID':'Fin'","'BACKEND_PANEL_ID':'BB'")
    tests=tests.replace("/tmp/fin_v2_","/tmp/bb_v24_")
    tests=tests.replace("return {'claimed':True,'reservation':record,'claim_token':'dummy'}",
                       "return {'ok':True,'claimed':True,'reservation':record,'claim_token':'dummy'}")
    ast.parse(helper)
    ast.parse(observer)
    ast.parse(tests)
    return helper,observer,tests

def validate_candidate(candidate,helper,tests):
    with tempfile.TemporaryDirectory(prefix="bb-reservation-v24-check-") as d:
        p=Path(d)
        (p/"Panel_backend_merged.py").write_text(candidate)
        (p/POLICY).write_text(helper)
        (p/"test_bb_reservation_v24.py").write_text(tests)
        q=subprocess.run(
            [sys.executable,str(p/"test_bb_reservation_v24.py")],
            cwd=p,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
            text=True,timeout=35)
        print(q.stdout[-8000:])
        if q.returncode!=0:
            stop("BB offline reservation tests FAILED. No source file changed.")
    print("BB reservation offline tests: PASS")

def build():
    if os.getuid()!=1000 or os.uname().nodename!="Test1":
        stop("BB package requires pi user on hostname Test1; no sudo.")
    if shutil.disk_usage("/").free<200*1024*1024:stop("Low disk space")
    read_manifest()
    if LEDGER.exists():
        stop("Unresolved BB reservation payment journal exists; manual reconciliation required")
    if (APP/POLICY).exists():
        stop("Reservation V2.4 policy already present; manual version review required")
    z=package()
    helper,observer,tests=adapt_assets(z)
    base=BACKEND.read_text(encoding="utf-8")
    candidate=transform_backend(base,z["Fin_Reservation_V2_4.patch"])
    validate_candidate(candidate,helper,tests)
    return candidate,helper,observer,tests

def parse_bb_config():
    tree=ast.parse(BACKEND.read_text())
    keys={"BACKEND_BASE_URL","BACKEND_PANEL_ID_OVERRIDE"}
    found={}
    for n in tree.body:
        if isinstance(n,ast.Assign):
            for t in n.targets:
                if isinstance(t,ast.Name) and t.id in keys:
                    try:val=ast.literal_eval(n.value)
                    except (ValueError,SyntaxError,TypeError):continue
                    if isinstance(val,str):found[t.id]=val
    if not found.get("BACKEND_BASE_URL") or not found.get("BACKEND_PANEL_ID_OVERRIDE"):
        stop("Cannot safely determine BB backend URL and panel ID")
    return found

def valid_proof(helper_text):
    if not PROOF.is_file():stop("BB hold proof missing. Run BB observer on dedicated unpaid TEST code.")
    st=PROOF.stat()
    if st.st_uid!=os.getuid() or (st.st_mode & 0o077):
        stop("BB proof file owner/permissions unsafe")
    proof=json.loads(PROOF.read_text())
    ns={}
    exec(compile(helper_text, "BB pure policy", "exec"),ns)
    now=time.monotonic()
    cfg=parse_bb_config()
    remaining=ns["validated_unreported_claim_window"]({
        "ok":True,"claimed":True,
        "claim_token":"OFFLINE-CHECK-ONLY",
        "_bb_v24_requested_hold":600,
        "_bb_v24_claim_received_monotonic":now,
    },proof=proof,
       backend_url=cfg["BACKEND_BASE_URL"],
       panel_id=cfg["BACKEND_PANEL_ID_OVERRIDE"],
       now_monotonic=now)
    if remaining is None or remaining<90:
        stop("Hold proof invalid/stale/does not match BB backend and panel")
    print("Fresh observed BB claim-hold proof: PASS")

def atomic_write(path,data,mode=None):
    fd,name=tempfile.mkstemp(prefix=".bb-v24-",dir=path.parent)
    try:
        with os.fdopen(fd,"wb") as out:
            os.fchmod(out.fileno(),mode if mode is not None else
                      (path.stat().st_mode & 0o777 if path.exists() else 0o600))
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)

def prepare_observer():
    candidate,helper,observer,_tests=build()
    p=HOME/"bb_v24_observe_claim_hold.py"
    h=HOME/POLICY
    for path in (p,h):
        if path.exists():stop("Refusing to overwrite "+str(path))
    atomic_write(h,helper.encode(),0o600)
    atomic_write(p,observer.encode(),0o700)
    print("Observer prepared: "+str(p))
    print("Pure policy file: "+str(h))
    print("NO payment, claim, kiosk source or backend action performed")
    print("Next: python3 ~/bb_v24_observe_claim_hold.py --claim-test")
    print("ONLY use a dedicated unpaid TEST reservation with operator approval.")

def apply():
    candidate,helper,observer,_tests=build()
    valid_proof(helper)
    words=input("BB idle? No payments/door operations? Type INSTALL-BB-RESERVATION-V24: ")
    if words!="INSTALL-BB-RESERVATION-V24":stop("Cancelled")
    BACKUPS.mkdir(mode=0o700,parents=True,exist_ok=True)
    from datetime import datetime
    path=BACKUPS/datetime.now().strftime("%Y%m%d_%H%M%S")
    if path.exists():stop("Backup directory collision")
    path.mkdir(mode=0o700)
    shutil.copy2(BACKEND,path/"Panel_backend_merged.py")
    (path/"expected_installed_sha256.txt").write_text(
        hash_bytes(candidate.encode())+"  Panel_backend_merged.py\n"+
        hash_bytes(helper.encode())+"  "+POLICY+"\n")
    try:
        atomic_write(BACKEND,candidate.encode())
        atomic_write(APP/POLICY,helper.encode(),0o600)
        if digest(BACKEND)!=hash_bytes(candidate.encode()) or digest(APP/POLICY)!=hash_bytes(helper.encode()):
            stop("Installed hash verification failed")
    except BaseException:
        atomic_write(BACKEND,(path/"Panel_backend_merged.py").read_bytes())
        (APP/POLICY).unlink(missing_ok=True)
        raise
    print("BB RESERVATION V2.4: INSTALLED ON DISK ONLY")
    print("Backup:",path)
    print("Kiosk NOT restarted; ads, X11, WA backend, ESP32 unchanged.")
    print("Must still verify PLA/Wolt/unpaid payment and failure paths before customer use.")

def rollback(path):
    if os.getuid()!=1000 or os.uname().nodename!="Test1":stop("Run as pi on BB Test1")
    path=path.expanduser().resolve()
    if BACKUPS.resolve() not in path.parents or not path.is_dir():
        stop("Rollback path must be inside ~/bb_reservation_v24_backups")
    if LEDGER.exists():stop("Payment recovery journal present; reconcile charge before rollback")
    sha_file=path/"expected_installed_sha256.txt"
    bk=path/"Panel_backend_merged.py"
    if not sha_file.is_file() or not bk.is_file():stop("Invalid BB V2.4 backup")
    expected=dict(v.split("  ",1)[::-1] for v in sha_file.read_text().splitlines())
    if digest(BACKEND)!=expected.get("Panel_backend_merged.py"):
        stop("Current backend changed since installation; refusing rollback")
    if not (APP/POLICY).is_file() or digest(APP/POLICY)!=expected.get(POLICY):
        stop("Current routing policy changed; refusing rollback")
    manifest=(PERF_BACKUP/"new_sha256.txt").read_text()
    base_digest=next((s.split("  ",1)[0] for s in manifest.splitlines()
                      if s.endswith("  Panel_backend_merged.py")),None)
    if digest(bk)!=base_digest:stop("Performance V1 source hash mismatch in backup")
    answer=input("Confirm idle BB and no payment pending. Type ROLLBACK-BB-V24: ")
    if answer!="ROLLBACK-BB-V24":stop("Cancelled")
    atomic_write(BACKEND,bk.read_bytes())
    (APP/POLICY).unlink()
    print("BB Reservation V2.4 code rolled back ON DISK. No GUI restart.")
    print("Existing Performance Stage 1 retained.")

def main():
    p=argparse.ArgumentParser()
    g=p.add_mutually_exclusive_group(required=True)
    g.add_argument("--check",action="store_true")
    g.add_argument("--prepare-proof-tool",action="store_true")
    g.add_argument("--apply",action="store_true")
    g.add_argument("--rollback",type=Path,metavar="BACKUP_DIRECTORY")
    a=p.parse_args()
    try:
        if a.rollback:rollback(a.rollback)
        elif a.check:
            build()
            print("BB V2.4 OFFLINE CHECK: PASS. NO production changes.")
        elif a.prepare_proof_tool:prepare_observer()
        else:apply()
    except Exception as err:
        print("STOP:",type(err).__name__+":",str(err),file=sys.stderr)
        sys.exit(1)

if __name__=="__main__":
    main()
