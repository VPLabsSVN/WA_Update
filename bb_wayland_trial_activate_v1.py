#!/usr/bin/env python3
"""BB Wayland / Raspberry Pi Connect activation V1 (Test1 only).

--check: READ ONLY: verify prepared files, backups, independent rollback service.
--switch: ARMS 10-minute X11 automatic rollback BEFORE changing LightDM; reboots.
--status: READ ONLY: inspect post-reboot desktop, Connect, kiosk, advertising.
--confirm: confirm only after visual user check, with all health gates passing;
           disarms automatic rollback, retaining the original X11 backup.
--revert: restore staged X11 files, then reboot (explicit confirmation).
The critical --switch and --confirm steps must be supervised remotely.
This script makes no changes to WA backend, firmware, ads media, or payment code.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile

HOST="Test1"
PI=Path("/home/pi")
TRIAL=PI/"bb_wayland_trial"
PREP=TRIAL/"prepared.json"
ROOT=Path("/var/lib/bb-x11-recovery")
MAN=ROOT/"manifest.json"
ARM=ROOT/"ARMED"
GUARD=Path("/usr/local/sbin/bb-x11-recovery.py")
LIGHTDM=Path("/etc/lightdm/lightdm.conf")
TIMER=Path("/etc/systemd/system/bb-x11-recovery.timer")
REQUIRED=[
    PI/".config/kanshi/config",
    PI/".config/labwc/autostart",
    PI/".config/autostart/panel-startup.desktop",
    PI/".config/autostart/ads-player.desktop",
    TRIAL/"panel-wayland-aware.sh",
    TRIAL/"ads-wayland-aware.sh",
]

def fail(msg):
    raise RuntimeError(msg)

def run(*args, timeout=20, check=False, env=None):
    p=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                     text=True,timeout=timeout,check=False,env=env)
    if check and p.returncode:
        fail("Command failed: "+args[0]+": "+p.stderr[:270])
    return p

def file_sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def foundation():
    if os.geteuid()!=0:fail("Run with sudo python3.")
    if os.uname().nodename != HOST:fail("Safety lock: script works only on BB Test1.")
    if not MAN.is_file() or not GUARD.is_file():
        fail("Root X11 recovery guard is not installed.")
    if not TIMER.is_file() or "OnActiveSec=10min" not in TIMER.read_text():
        fail("Expected independent 10-minute system rollback timer is missing.")
    if shutil.disk_usage("/").free<200*1024*1024:
        fail("Less than 200 MiB free disk.")
    if not Path("/usr/share/wayland-sessions/LXDE-pi-labwc.desktop").is_file():
        fail("Labwc desktop session is missing.")

def backup_valid():
    records=json.loads(MAN.read_text())
    bypath={r["path"]:r for r in records}
    for entry in records:
        p=ROOT/("backup_%02d" % entry["index"])
        if entry["exists"]:
            if not p.is_file() or file_sha(p)!=entry["sha256"]:
                fail("X11 recovery backup damaged for "+entry["path"])
    for important in (LIGHTDM, PI/".config/kanshi/config",
                      PI/".config/labwc/autostart",
                      PI/".config/autostart/panel-startup.desktop",
                      PI/".config/autostart/ads-player.desktop"):
        if str(important) not in bypath:
            fail("X11 backup omitted "+str(important))
    return bypath

def prepared():
    if not PREP.is_file():fail("Wayland stage not prepared.")
    data=json.loads(PREP.read_text())
    for path in REQUIRED:
        if not path.is_file():fail("Missing prepared config "+str(path))
        digest=data.get(str(path))
        if not digest or file_sha(path)!=digest:
            fail("Prepared file hash mismatch: "+str(path))
    for script in REQUIRED[-2:]:
        run("bash","-n",str(script),check=True)
    for program in ("labwc","kanshi","Xwayland","flock","wayvnc"):
        if not shutil.which(program):fail("Required program missing: "+program)
    layout=(PI/".config/kanshi/config").read_text()
    for token in ("output DSI-1 enable mode 1280x800 position 0,0",
                  "output HDMI-A-2 enable mode 1280x720 position 1280,0"):
        if token not in layout:fail("Expected two-monitor layout missing.")
    touch=(PI/".config/labwc/rc.xml").read_text()
    if "Goodix" not in touch or "DSI-1" not in touch:
        fail("Goodix touchscreen binding not present.")

def lightdm_value(source,key):
    matches=[]
    for line in source.splitlines():
        if line.lstrip().startswith("#"):continue
        m=re.fullmatch(r"\s*"+re.escape(key)+r"\s*=\s*(\S+)\s*",line)
        if m:matches.append(m.group(1))
    if len(matches)!=1:fail("Expected one active LightDM "+key+" entry, found "+str(len(matches)))
    return matches[0]

def x11_login():
    content=LIGHTDM.read_text()
    for name in ("autologin-session","user-session"):
        if lightdm_value(content,name)!="LXDE-pi-x":
            fail("LightDM does not currently point to original X11 session.")
    return content

def guarded_wayland_lightdm(text):
    new=text
    for field in ("autologin-session","user-session"):
        expression=re.compile(r"^(\s*"+re.escape(field)+r"\s*=\s*)LXDE-pi-x(\s*)$",re.M)
        new,n=expression.subn(r"\g<1>LXDE-pi-labwc\g<2>",new)
        if n!=1:fail("Cannot safely replace one LightDM "+field+" line.")
    return new

def atomic_write(path,data):
    s=path.stat()
    fd,temp=tempfile.mkstemp(prefix=".bb-wayland-",dir=path.parent)
    try:
        os.fchmod(fd,stat.S_IMODE(s.st_mode))
        os.fchown(fd,s.st_uid,s.st_gid)
        with os.fdopen(fd,"wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp,path)
    finally:
        if os.path.exists(temp):os.unlink(temp)

def read_only_preflight():
    foundation()
    backup_valid()
    prepared()
    x11_login()
    if ARM.exists():fail("Recovery timer is already armed.")
    p=run("systemctl","is-active","bb-x11-recovery.timer")
    if p.stdout.strip()=="active":
        fail("Rollback timer unexpectedly active before migration.")
    if run("systemctl","is-active","lightdm.service").stdout.strip()!="active":
        fail("LightDM not active.")
    # Prevent accidentally running on a session already transitioned to Wayland.
    if wayland_session():
        fail("A Wayland graphical session is already running.")
    if len(kiosk_pids())!=1:
        fail("Expected exactly one running main kiosk before migration.")
    if not run("pgrep","-f","/home/pi/WA/Main_program/Fis/Ads.py").stdout.strip():
        fail("Original separate Ads.py application not running.")
    for p in (PI/"bin/start-panel.sh",PI/"WA/Main_program/Fis/start_ads_x11.sh"):
        if not p.is_file():fail("Original launcher absent: "+str(p))
    print("ACTIVATION PREFLIGHT: PASS")
    print("Prepared hashes: PASS; X11 backup: PASS; two outputs and touch settings: PREPARED")
    print("Kiosk + original advertising processes running; root recovery watchdog ready.")
    print("No modifications made. X11 still active.")

def sessions():
    result=[]
    p=run("loginctl","list-sessions","--no-legend")
    for line in p.stdout.splitlines():
        fields=line.split()
        if not fields:continue
        q=run("loginctl","show-session",fields[0],"-p","Name","-p","Type","-p","Desktop","-p","State")
        d=dict(x.split("=",1) for x in q.stdout.splitlines() if "=" in x)
        if d.get("Name")=="pi":
            result.append(d)
    return result

def wayland_session():
    return any(x.get("Type")=="wayland" and x.get("State")=="active" for x in sessions())

def kiosk_pids():
    p=run("pgrep","-af","Panel_backend_merged.py")
    return [line for line in p.stdout.splitlines()
            if re.match(r"^\d+\s+",line) and "python" in line and
            "Panel_backend_merged.py" in line and "pgrep" not in line]

def socket_names():
    d=Path("/run/user/1000")
    found=[]
    for p in d.glob("wayland-*"):
        try:
            if stat.S_ISSOCK(p.stat().st_mode):found.append(p.name)
        except OSError:pass
    return found

def connect_vnc_active():
    env=dict(os.environ, XDG_RUNTIME_DIR="/run/user/1000",
             DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/1000/bus")
    p=run("runuser","-u","pi","--","systemctl","--user","is-active",
          "rpi-connect-wayvnc.service",env=env)
    return p.stdout.strip()=="active"

def mpv_ads_alive():
    p=run("pgrep","-af","mpv")
    return any("mpv_ads.sock" in line and "pgrep" not in line for line in p.stdout.splitlines())

def output_status():
    socknames=socket_names()
    if not socknames:return (False,"no wayland socket")
    env=dict(os.environ,XDG_RUNTIME_DIR="/run/user/1000",WAYLAND_DISPLAY=socknames[0])
    q=run("runuser","-u","pi","--","wlr-randr",env=env)
    if q.returncode:
        return (False,"wlr-randr unavailable from current user session")
    text=q.stdout
    if "DSI-1" not in text or "HDMI-A-2" not in text:
        return (False,"both DSI-1 and HDMI-A-2 not present")
    return (True,"both expected outputs reported by compositor")

def checks():
    data={}
    data["wayland graphical session"]=wayland_session()
    data["Wayland socket present"]=bool(socket_names())
    data["labwc compositor"]=run("pgrep","-x","labwc").returncode==0
    data["Raspberry Pi Connect screen service"]=connect_vnc_active()
    data["single main kiosk process"]=len(kiosk_pids())==1
    data["advertising mpv process"]=mpv_ads_alive()
    data["advertising IPC socket"]=Path("/tmp/mpv_ads.sock").is_socket()
    passed,message=output_status()
    data["both Wayland outputs detected"]=passed
    return data,message

def report():
    foundation()
    backup_valid()
    prepared()
    print("=== BB WAYLAND REMOTE HEALTH ===")
    print("Root X11 rollback timer:",run("systemctl","is-active",
          "bb-x11-recovery.timer").stdout.strip() or "inactive")
    print("X11 rollback armed:",ARM.exists())
    print("LightDM autologin session:",lightdm_value(LIGHTDM.read_text(),"autologin-session"))
    print("Pi graphical session:",", ".join(x.get("Type","?")+"/"+x.get("Desktop","?")
          for x in sessions()) or "unavailable")
    data,detail=checks()
    for key,passed in data.items():
        print(("PASS" if passed else "FAIL"),key)
    print("Wayland outputs:",detail)
    print("Remote visual confirmation still required. This command never disarms rollback.")

def do_switch():
    read_only_preflight()
    typed=input("BB IDLE and no active payment or motor operation? Type SWITCH-BB-TO-WAYLAND: ")
    if typed!="SWITCH-BB-TO-WAYLAND":fail("Cancelled.")
    old=x11_login()
    new=guarded_wayland_lightdm(old)
    if new==old:fail("No LightDM change produced.")
    # Arm persistent independent SYSTEM timer before changing the desktop.
    run("/usr/bin/python3",str(GUARD),"--arm",check=True)
    if not ARM.exists() or run("systemctl","is-active",
                              "bb-x11-recovery.timer").stdout.strip()!="active":
        fail("Recovery timer not active; LightDM not changed.")
    try:
        atomic_write(LIGHTDM,new.encode())
        if lightdm_value(LIGHTDM.read_text(),"autologin-session")!="LXDE-pi-labwc":
            fail("LightDM session change did not persist.")
    except Exception:
        atomic_write(LIGHTDM,old.encode())
        run("/usr/bin/python3",str(GUARD),"--disarm")
        raise
    print("INDEPENDENT X11 RECOVERY TIMER ACTIVE.",flush=True)
    print("Switching LightDM to Wayland labwc. Reboot requested.",flush=True)
    print("If no explicit --confirm within rollback window, X11 will be restored.",flush=True)
    p=run("systemctl","reboot","--no-wall")
    if p.returncode!=0:
        atomic_write(LIGHTDM,old.encode())
        run("/usr/bin/python3",str(GUARD),"--disarm")
        fail("Reboot request failed; original X11 login restored.")
    print("Reboot initiated. Reconnect using Raspberry Pi Connect remote shell.",flush=True)

def do_confirm():
    foundation()
    backup_valid()
    prepared()
    if not ARM.exists():fail("No armed Wayland trial to confirm.")
    if run("systemctl","is-active","bb-x11-recovery.timer").stdout.strip()!="active":
        fail("Rollback timer not active; cannot safely confirm a trial.")
    if lightdm_value(LIGHTDM.read_text(),"autologin-session")!="LXDE-pi-labwc":
        fail("LightDM is not configured for Wayland.")
    data,detail=checks()
    for key,passed in data.items():
        print(("PASS" if passed else "FAIL"),key)
    if not all(data.values()):
        fail("Trial health incomplete. Do not disarm X11 recovery; inspect --status.")
    print("DISPLAY CHECK:",detail)
    print("The Connect screen shows ONLY ONE display; the HDMI display is NOT visually verified.")
    typed=input("After confirming the Kivy UI via Connect, type KEEP-BB-WAYLAND: ")
    if typed!="KEEP-BB-WAYLAND":
        fail("Not confirmed; original X11 rollback remains armed.")
    run("/usr/bin/python3",str(GUARD),"--disarm",check=True)
    if ARM.exists():fail("Rollback remains armed; investigate.")
    print("Wayland trial retained. X11 automatic rollback DISARMED by explicit approval.")
    print("Original X11 backup preserved at",ROOT)
    print("Check actual HDMI advertising placement separately before accepting production rollout.")

def do_revert():
    foundation()
    if not MAN.is_file():fail("No X11 recovery backup.")
    backup_valid()
    typed=input("Restore BB X11 and reboot? Type RESTORE-BB-X11: ")
    if typed!="RESTORE-BB-X11":fail("Cancelled.")
    run("/usr/bin/python3",str(GUARD),"--restore",check=True)
    print("X11 restored. Rebooting the desktop.",flush=True)
    run("systemctl","reboot","--no-wall",check=True)

def main():
    p=argparse.ArgumentParser()
    g=p.add_mutually_exclusive_group(required=True)
    for a in ("check","switch","status","confirm","revert"):
        g.add_argument("--"+a,action="store_true")
    a=p.parse_args()
    try:
        if a.check:read_only_preflight()
        elif a.switch:do_switch()
        elif a.status:report()
        elif a.confirm:do_confirm()
        elif a.revert:do_revert()
    except Exception as e:
        print("STOP:",type(e).__name__+":",e,file=sys.stderr)
        sys.exit(1)

if __name__=="__main__":
    main()
