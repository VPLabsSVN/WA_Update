#!/usr/bin/env python3
"""BB Raspberry Pi preflight for adapting Fin V1 performance + V2.4 reservations.

READ ONLY: no network calls, no imports of application modules, no file writes,
no service restart, no customer/payment/door actions. Prints no source code,
tokens, credentials, backend URLs, IP addresses, customer records, or log lines.
"""
from __future__ import annotations

import ast
from datetime import datetime
import hashlib
from pathlib import Path
import platform
import subprocess
import sys

APP = Path("/home/wa_stopshop_mb/WA/Main_program/Fis")
HOME = Path.home()
FILES = (
    "Panel.py",
    "Panel_backend_merged.py",
    "backend_client.py",
    "Ads.py",
    "fin_reservation_v2_logic.py",
)
FIN_SHA = {
    "Panel.py": {
        "Fin original": "8fcb2f4f0858583205d24935a56e5197bfe92a7d46bb4c0d86b09c5804d8a4f5",
        "Fin Performance V1": "a8c52a48602596d198c78025654275a368cd03c412259c368a9a51d2b0ed8da7",
    },
    "Panel_backend_merged.py": {
        "Fin original": "2b408cea669f68d91f19b79129e0ae9ecac16cd3f37ba6081d9db64c03e3889e",
        "Fin Performance V1": "b46796c0aacf44b91f40f571b9eda7e85687777e92fb8790cb36e0f2de52c450",
        "Fin Reservation V2.4": "1c2581115695a6c8353d730217d3400293be7b74a3cae4ef77dd21151e91b886",
    },
    "backend_client.py": {
        "Fin unchanged": "133080a1dd064c41c9e916dc68cd2539baa65fc2c733f5a7ff3f448e7795fdf0",
    },
}
SIGNATURES = [
    "reservation_collect",
    "_reservation_collect_claimed",
    "_reservation_confirm_async",
    "_reservation_release_async",
    "_handle_payment_result",
    "_handle_payment_failure",
    "_backend_apply_config",
    "backend_sync_items_async",
    "_apply_remote_panel_message",
    "_reservation_v2_start_checkout",
    "_reservation_v2_finish_paid",
    "_reservation_v2_handle_payment_result",
    "_backend_init_hr",
]
def run(*cmd):
    try:
        x = subprocess.run(cmd, capture_output=True, text=True, timeout=5, check=False)
        return x.stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""

def svc(name):
    raw = run("systemctl", "--user", "show", name,
              "-p", "LoadState", "-p", "ActiveState", "-p", "SubState",
              "-p", "MainPID", "-p", "ExecMainStartTimestamp")
    state = {}
    for line in raw.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            state[k] = v
    print(f"{name}: loaded={state.get('LoadState','unknown')}, "
          f"active={state.get('ActiveState','unknown')}, "
          f"pid={state.get('MainPID','unknown')}, "
          f"since={state.get('ExecMainStartTimestamp','unknown')}")
    return state.get("MainPID")

def digest(path):
    h=hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024*1024),b""):
            h.update(block)
    return h.hexdigest()

def parse_source(path):
    try:
        data=path.read_text(encoding="utf-8")
        tree=ast.parse(data,filename=path.name)
        compile(tree,path.name,"exec")
        return tree, "PASS"
    except (OSError, UnicodeError, SyntaxError, ValueError) as exc:
        return None,f"FAIL ({type(exc).__name__})"

def funcs(tree):
    if tree is None:
        return set()
    return {n.name for n in ast.walk(tree) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))}

def bool_setting(tree,name):
    if tree is None:
        return "not found"
    for node in ast.walk(tree):
        if isinstance(node,(ast.Assign,ast.AnnAssign)):
            names=([t.id for t in node.targets if isinstance(t,ast.Name)]
                   if isinstance(node,ast.Assign)
                   else [node.target.id] if isinstance(node.target,ast.Name) else [])
            if name in names:
                v=node.value
                if isinstance(v,ast.Constant) and type(v.value) is bool:
                    return str(v.value)
    return "not a literal bool/not found"

def runtime_stats():
    try:
        mem={}
        for ln in Path("/proc/meminfo").read_text().splitlines():
            if ln.startswith(("MemTotal:","MemAvailable:")):
                k, val=ln.split(":",1)
                mem[k]=int(val.split()[0]) // 1024
        print("Memory MiB total/available:", mem.get("MemTotal","?"), "/",mem.get("MemAvailable","?"))
    except Exception:
        print("Memory: unavailable")
    throttle=run("vcgencmd","get_throttled").strip()
    if throttle:
        print("Power throttling:",throttle)
    space=run("df","-h","/").splitlines()
    if len(space)>1:
        cols=space[1].split()
        if len(cols)>4:
            print("Root filesystem usage:", cols[4])

def main():
    print("=== BB READ-ONLY PRE-UPGRADE AUDIT V1 ===")
    print("Generated:",datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z"))
    print("Python:",platform.python_version(),"Machine:",platform.machine())
    print("Expected app directory exists:", APP.is_dir())
    print("No source code, customer data, auth values or network configuration printed.")
    print("\n--- MACHINE / SERVICE ---")
    model=Path("/proc/device-tree/model")
    if model.is_file():
        print("Hardware:",model.read_bytes().replace(b"\x00",b"").decode("utf-8","replace"))
    runtime_stats()
    pid=svc("bouquetmat.service")
    svc("bouquetmat-ads.service")
    if pid and pid.isdigit() and int(pid)>0:
        try:
            args=Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\x00")
            print("Kiosk process uses expected Panel_backend_merged.py:",
                  any(x.endswith(b"/Panel_backend_merged.py") for x in args))
        except OSError:
            print("Kiosk process command unreadable")
    adwatch=HOME/"bin/bouquetmat-ads-watchdog.sh"
    print("Separate ads watchdog script exists:",adwatch.is_file())
    print("Ads directory exists:",Path("/home/wa_stopshop_mb/WA/Main_program/Ads").is_dir())
    drm=Path("/sys/class/drm")
    for status_file in sorted(drm.glob("card*-*/status")):
        if any(s in status_file.parent.name.lower() for s in ("dsi","hdmi")):
            try:
                print("Display:",status_file.parent.name,status_file.read_text().strip())
            except OSError:
                pass
    print("\n--- APPLICATION FILES (hashes; no file contents) ---")
    trees={}
    for name in FILES:
        path=APP/name
        if not path.is_file():
            print(name,": MISSING")
            continue
        sha=digest(path)
        stat=path.stat()
        tree,syntax=parse_source(path)
        trees[name]=tree
        matches=[label for label,h in FIN_SHA.get(name,{}).items() if h==sha]
        print(f"{name}: {stat.st_size} bytes; SHA256={sha}; syntax={syntax}; "
              f"Fin baseline match={','.join(matches) if matches else 'NO'}")
    print("\n--- COMPATIBILITY / FEATURES (function names only) ---")
    fn=funcs(trees.get("Panel_backend_merged.py"))
    for name in SIGNATURES:
        print(name+":", "present" if name in fn else "absent")
    panel=trees.get("Panel.py")
    print("Panel.py ADS_ENABLED:",bool_setting(panel,"ADS_ENABLED"))
    print("Local reservation hold proof exists:",(HOME/".config/bouquetmat/fin_v24_hold_proof.json").is_file())
    print("Local reservation payment recovery file exists:",
          (APP/".fin_reservation_v2_recovery.json").is_file())
    print("\n--- CONCLUSION ---")
    print("READ ONLY: no application files or services were changed.")
    print("Do not run Fin's original version-locked installers on BB.")
    print("BB-specific software adaptation and testing are required before deployment.")

if __name__=="__main__":
    sys.exit(main())
