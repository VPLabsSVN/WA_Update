#!/usr/bin/env python3
"""BB machine-source and storage pre-upgrade audit.

READ ONLY. Does not import/execute kiosk source or contact the WA server.
Does not print source, credentials, URLs, reservation/customer records,
full process arguments, or logs. No backups, edits, restarts, payments or doors.
"""
from __future__ import annotations
import ast
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys

APP = Path("/home/pi/WA/Main_program/Fis")
FILES = ("Panel.py","Panel_backend_merged.py","backend_client.py","Ads.py")
FIN_ORIGINAL = {
    "Panel.py": "8fcb2f4f0858583205d24935a56e5197bfe92a7d46bb4c0d86b09c5804d8a4f5",
    "Panel_backend_merged.py": "2b408cea669f68d91f19b79129e0ae9ecac16cd3f37ba6081d9db64c03e3889e",
}
FUNCS = (
  "reservation_collect", "_reservation_collect_claimed",
  "_reservation_confirm_async", "_reservation_release_async",
  "_reservation_v2_start_checkout", "_reservation_v2_finish_paid",
  "_reservation_v2_handle_payment_result",
  "_handle_payment_result", "_handle_payment_failure",
  "_backend_apply_config", "backend_sync_items_async",
  "_apply_remote_panel_message", "_backend_init_hr",
  "_start_payment_for_items", "_open_door",
)
def cmd(args,timeout=12):
    try:
        p=subprocess.run(args,capture_output=True,text=True,timeout=timeout)
        return p.stdout.strip() if p.returncode==0 else ""
    except (OSError,subprocess.TimeoutExpired):
        return ""
def sha(path):
    digest=hashlib.sha256()
    with path.open("rb") as f:
        for part in iter(lambda:f.read(1024*1024), b""):
            digest.update(part)
    return digest.hexdigest()
def size(v):
    units=("B","KiB","MiB","GiB")
    v=float(v)
    for u in units:
        if v<1024 or u==units[-1]:
            return f"{v:.1f} {u}"
        v/=1024
def tree(path):
    try:
        s=path.read_text(encoding="utf-8")
        parsed=ast.parse(s)
        compile(parsed,str(path),"exec")
        return parsed,len(s.splitlines()),"PASS"
    except (OSError,UnicodeError,SyntaxError,ValueError) as exc:
        return None,None,type(exc).__name__
def disk_usage(path):
    total,used,free=shutil.disk_usage(path)
    print(f"{path}: total={size(total)} used={size(used)} free={size(free)} usage={used*100/total:.1f}%")
def directory_sizes(root):
    if not root.is_dir():
        return
    # Only count within same filesystem; no file data is printed.
    raw=cmd(["du","-x","-k","-d","1",str(root)],timeout=35)
    pairs=[]
    for line in raw.splitlines():
        n,sep,entry=line.partition("\t")
        if sep and n.isdigit():
            pairs.append((int(n),entry))
    for n,path in sorted(pairs,reverse=True)[:9]:
        print(f"  {path}: {size(n*1024)}")
def process_info():
    print("\n=== KIOSK PROCESSES ===")
    current_user=os.getuid()
    matches=[]
    for item in Path("/proc").iterdir():
        if not item.name.isdigit():
            continue
        try:
            if item.stat().st_uid!=current_user:
                continue
            argv=(item/"cmdline").read_bytes().split(b"\x00")
            for target in FILES:
                if any(a.endswith(("/"+target).encode()) for a in argv if a):
                    stat=(item/"status").read_text(errors="replace")
                    rss=next((line.split(":",1)[1].strip() for line in stat.splitlines()
                              if line.startswith("VmRSS:")), "?")
                    matches.append((int(item.name),target,rss))
                    break
        except (OSError,PermissionError):
            pass
    for pid,target,rss in sorted(matches):
        print(f"PID={pid} main_script={target} RSS={rss}")
    if not matches:
        print("No matching processes detected under this user")
def startup():
    print("\n=== STARTUP MECHANISM (filenames and references only) ===")
    locations=[
        Path.home()/".config/autostart",
        Path.home()/".config/labwc/autostart",
        Path.home()/".config/wayfire.ini",
        Path.home()/".config/systemd/user",
    ]
    for root in locations:
        if not root.exists(): continue
        candidates=sorted(root.iterdir()) if root.is_dir() else [root]
        for file in candidates:
            if not file.is_file():continue
            try:
                text=file.read_text(encoding="utf-8",errors="replace")
                if any(s in text for s in ("Panel_backend_merged.py","Ads.py","Main_program/Fis")):
                    print(f"{file}: references BB kiosk source")
            except OSError:
                pass
    print("Desktop autostart (not a bouquetmat systemd service) may be used.")
def main():
    print("=== BB READ-ONLY SOURCE / DISK AUDIT V2 ===")
    print("Hostname:",os.uname().nodename)
    print("Correct BB application directory exists:",APP.is_dir())
    disk_usage("/")
    print("\n=== SOURCE INVENTORY ===")
    all_functions=set()
    trees={}
    for name in FILES:
        file=APP/name
        if not file.is_file():
            print(f"{name}: MISSING")
            continue
        digest=sha(file)
        parsed,lines,syntax=tree(file)
        trees[name]=parsed
        fn={node.name for node in ast.walk(parsed) if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef))} if parsed else set()
        all_functions.update(fn)
        original="YES" if FIN_ORIGINAL.get(name)==digest else "NO"
        print(f"{name}: {size(file.stat().st_size)}, {lines if lines else '?'} lines, syntax={syntax}, SHA256={digest}, identical_to_Fin_original={original}, functions={len(fn)}")
    print("\n=== RESERVATION / PAYMENT / SYNC FUNCTIONS ===")
    for name in FUNCS:
        print(f"{name}: {'present' if name in all_functions else 'absent'}")
    print("\n=== V2.4 HELPER / PROOF ===")
    print("V2.4 helper file already installed:",(APP/"fin_reservation_v2_logic.py").is_file())
    print("Hold-observation proof already present:",(Path.home()/".config/bouquetmat/fin_v24_hold_proof.json").is_file())
    process_info()
    startup()
    print("\n=== LARGEST DIRECTORIES (no deletion) ===")
    directory_sizes(Path.home())
    print("\n=== FINISHED ===")
    print("READ ONLY: no application, service, disk, backend, payment or hardware changes.")
    print("Do NOT use Fin's version-locked installers on BB.")
if __name__=="__main__":
    main()
