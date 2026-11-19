#!/usr/bin/env python3
"""BB Test1: Stage Wayland trial V2 to correct Kivy/Ads monitor assignment.

--check: READ ONLY: check original X11 state and rollback backup.
--prepare: stage temporary trial launchers/config; no service restart.
--status: READ ONLY: verify the staged files.
Original Panel.py, Panel_backend_merged.py, Ads.py, WA backend, ESP32 unaffected.
Uses existing bb_wayland_trial_activate_v1.py to arm root X11 rollback + switch.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import subprocess
import sys

PI=Path("/home/pi")
ROOT=Path("/var/lib/bb-x11-recovery")
MAN=ROOT/"manifest.json"
DIR=PI/"bb_wayland_trial"
LD=Path("/etc/lightdm/lightdm.conf")
LAB=PI/".config/labwc/autostart"
KAN=PI/".config/kanshi/config"
PDE=PI/".config/autostart/panel-startup.desktop"
ADE=PI/".config/autostart/ads-player.desktop"
PANEL=PI/"WA/Main_program/Fis/Panel.py"
BE=PI/"WA/Main_program/Fis/Panel_backend_merged.py"
ADS=PI/"WA/Main_program/Fis/Ads.py"
PW=DIR/"panel-wayland-aware.sh"
AW=DIR/"ads-wayland-aware.sh"
PR=DIR/"panel-run-wayland.py"
AR=DIR/"Ads_wayland_runtime.py"
READY=DIR/"prepared.json"

KANS="""# BB Trial V2: DSI kiosk left, HDMI advertising right
profile bb-dual {
    output DSI-1 enable mode 1280x800 position 0,0 transform normal
    output HDMI-A-2 enable mode 1280x720 position 1280,0 transform normal
}
"""

LABWC_ADD="""# BEGIN BB WAYLAND TRIAL V2
(sleep 2; if ! pgrep -u pi -x kanshi >/dev/null; then /usr/bin/kanshi; fi) &
(sleep 4; /home/pi/bb_wayland_trial/panel-wayland-aware.sh) &
(sleep 6; /home/pi/bb_wayland_trial/ads-wayland-aware.sh) &
# END BB WAYLAND TRIAL V2
"""

PANEL_RUN='''#!/usr/bin/env python3
"""Trial-only boot: load original Panel.py with DSI positioning in memory.
Do not change real Panel.py or production backend code.
"""
import runpy
import sys
import types
import importlib.util
from pathlib import Path

app=Path("/home/pi/WA/Main_program/Fis")
original=app/"Panel.py"
backend=app/"Panel_backend_merged.py"
src=original.read_text(encoding="utf-8")
a="Config.set('graphics', 'fullscreen', 'auto')"
b="Config.set('graphics', 'position', 'auto')"
if src.count(a)!=1 or src.count(b)!=1:
    raise SystemExit("BB Wayland: Panel.py anchors changed; refusing kiosk boot")
src=src.replace(a,"Config.set('graphics', 'fullscreen', '0')",1)
src=src.replace(b,
    "Config.set('graphics', 'position', 'custom')\\n"
    "Config.set('graphics', 'left', '0')\\n"
    "Config.set('graphics', 'top', '0')\\n"
    "Config.set('graphics', 'width', '1280')\\n"
    "Config.set('graphics', 'height', '800')",1)
# The preloaded module retains the ORIGINAL __file__ for local data paths.
sys.path.insert(0,str(app))
m=types.ModuleType("Panel")
m.__file__=str(original)
m.__package__=""
m.__spec__=importlib.util.spec_from_loader("Panel",loader=None)
sys.modules["Panel"]=m
exec(compile(src,str(original),"exec"),m.__dict__)
runpy.run_path(str(backend),run_name="__main__")
'''

PANEL_SH='''#!/bin/bash
set -eo pipefail
if [ "$XDG_SESSION_TYPE" != "wayland" ] && [ -z "$WAYLAND_DISPLAY" ]; then
    exec /home/pi/bin/start-panel.sh
fi
exec 9>/run/user/1000/bb_wayland_panel.lock
flock -n 9 || exit 0
if [ -z "$DISPLAY" ]; then
    echo "[BB-V2] Xwayland DISPLAY missing; kiosk did not start" >> /home/pi/panel_start.log
    exit 1
fi
# Kivy uses the original application code, with window geometry overridden in memory.
unset WAYLAND_DISPLAY
export SDL_VIDEODRIVER=x11
export SDL_VIDEO_WINDOW_POS=0,0
export SDL_VIDEO_FULLSCREEN_HEAD=0
sleep 5
cd /home/pi/WA/Main_program/Fis
exec /usr/bin/python3 -u /home/pi/bb_wayland_trial/panel-run-wayland.py >> /home/pi/panel_start.log 2>&1
'''

ADS_SH='''#!/bin/bash
set -eo pipefail
if [ "$XDG_SESSION_TYPE" != "wayland" ] && [ -z "$WAYLAND_DISPLAY" ]; then
    exec /home/pi/WA/Main_program/Fis/start_ads_x11.sh
fi
exec 9>/run/user/1000/bb_wayland_ads.lock
flock -n 9 || exit 0
if [ -z "$DISPLAY" ]; then
    echo "[BB-V2] Xwayland DISPLAY missing; ads did not start" >> /tmp/ads_python.log
    exit 1
fi
unset WAYLAND_DISPLAY
sleep 8
cd /home/pi/WA/Main_program/Fis
exec /usr/bin/python3 -u /home/pi/bb_wayland_trial/Ads_wayland_runtime.py >> /tmp/ads_python.log 2>&1
'''

def fail(s):raise RuntimeError(s)

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def system(*args):
    p=subprocess.run(args,text=True,capture_output=True,timeout=12)
    if p.returncode:fail("Command failed: "+args[0]+" "+p.stderr[:140])
    return p.stdout.strip()

def backup_records():
    if not MAN.is_file() or not Path("/usr/local/sbin/bb-x11-recovery.py").is_file():
        fail("X11 recovery guard not staged.")
    items=json.loads(MAN.read_text())
    result={}
    for it in items:
        p=Path(it["path"])
        result[str(p)]=it
        if it["exists"]:
            b=ROOT/("backup_%02d"%it["index"])
            if not b.is_file() or sha(b)!=it["sha256"]:
                fail("Rollback backup damaged: "+str(p))
    return result

def precheck():
    if os.geteuid()!=0:fail("Use sudo.")
    if os.uname().nodename!="Test1":fail("Only BB hostname Test1 supported.")
    if shutil.disk_usage("/").free<200*1024*1024:fail("Disk below 200 MiB free.")
    if (ROOT/"ARMED").exists():fail("X11 rollback still armed; do not stage.")
    if system("systemctl","is-active","bb-x11-recovery.timer")=="active":
        fail("Watchdog timer still active.")
    records=backup_records()
    for p in (KAN,LAB,PDE,ADE,LD):
        it=records.get(str(p))
        if not it:fail("Backup lacks "+str(p))
        if not it["exists"] or not p.is_file() or sha(p)!=it["sha256"]:
            fail("Expected original X11 file missing or changed: "+str(p))
    if "autologin-session=LXDE-pi-x" not in LD.read_text():
        fail("Expected original X11 LightDM login not found.")
    if "user-session=LXDE-pi-x" not in LD.read_text():
        fail("Expected original X11 LightDM user session not found.")
    if "Exec=/home/pi/bin/start-panel.sh" not in PDE.read_text():
        fail("X11 Panel desktop launcher not as expected.")
    if "Exec=/home/pi/WA/Main_program/Fis/start_ads_x11.sh" not in ADE.read_text():
        fail("X11 advertising desktop launcher not as expected.")
    if not all(x.is_file() for x in (PANEL,BE,ADS)):
        fail("An original kiosk file is missing.")
    pan=PANEL.read_text(encoding="utf-8")
    for x in ("Config.set('graphics', 'fullscreen', 'auto')",
              "Config.set('graphics', 'position', 'auto')"):
        if pan.count(x)!=1:fail("Panel.py source changed: "+x)
    if BE.read_text(encoding="utf-8").count("import Panel as base")!=1:
        fail("Backend entrypoint imports Panel differently.")
    if 'Goodix' not in (PI/".config/labwc/rc.xml").read_text():
        fail("Goodix touch mapping not present.")
    drms=Path("/sys/class/drm")
    names=set()
    for p in drms.glob("card*-*/status"):
        if p.read_text().strip()=="connected":names.add(p.parent.name.split("-",1)[1])
    if not {"DSI-1","HDMI-A-2"}.issubset(names):
        fail("Expected both DSI-1 and HDMI-A-2 connected.")
    for exe in ("labwc","kanshi","Xwayland","flock"):
        if not shutil.which(exe):fail("Missing executable "+exe)
    ast.parse(PANEL_RUN,filename="trial-panel-runner")
    for s in (PANEL_SH,ADS_SH):
        q=subprocess.run(["bash","-n"],input=s,text=True,capture_output=True)
        if q.returncode:fail("Wrapper syntax invalid: "+q.stderr)
    return pan

def ads_transformed():
    src=ADS.read_text(encoding="utf-8")
    # Only change the two final window geometry settings for BB's 1280x720 HDMI.
    for a,b in (("        window_w=1920,","        window_w=1280,"),
                ("        window_h=1080,","        window_h=720,")):
        if src.count(a)!=1:fail("Unexpected advertising source geometry: "+a)
        src=src.replace(a,b,1)
    # The original has a stray trailing PY token; direct execution never normally
    # reaches it because of its infinite loop. Remove it in the TRIAL COPY only.
    if src.rstrip().endswith("\\nPY"):
        src=src.rstrip()[:-2].rstrip()+"\n"
    ast.parse(src,filename="BB Ads runtime trial")
    return src

def put(path,contents,executable=False):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(contents,encoding="utf-8")
    os.chmod(path,0o755 if executable else 0o644)
    info=pwd.getpwnam("pi")
    os.chown(path,info.pw_uid,info.pw_gid)

def prepare():
    precheck()
    ads=ads_transformed()
    if DIR.joinpath("prepared-v2.json").exists():fail("V2 already staged. Use --status.")
    # Original X11 files are already preserved by root rollback guard.
    DIR.mkdir(mode=0o755,parents=True,exist_ok=True)
    info=pwd.getpwnam("pi")
    os.chown(DIR,info.pw_uid,info.pw_gid)
    put(PR,PANEL_RUN,True)
    put(AR,ads)
    put(PW,PANEL_SH,True)
    put(AW,ADS_SH,True)
    put(KAN,KANS)
    original_lab=LAB.read_text(encoding="utf-8")
    if "# BEGIN BB WAYLAND TRIAL" in original_lab:
        fail("Unexpected prior trial markers in labwc/autostart")
    put(LAB,original_lab.rstrip()+"\n\n"+LABWC_ADD)
    put(PDE,PDE.read_text().replace("Exec=/home/pi/bin/start-panel.sh",
                                   "Exec="+str(PW),1))
    put(ADE,ADE.read_text().replace(
        "Exec=/home/pi/WA/Main_program/Fis/start_ads_x11.sh",
        "Exec="+str(AW),1))
    paths=(KAN,LAB,PDE,ADE,PW,AW)
    data={str(p):sha(p) for p in paths}
    put(READY,json.dumps(data,indent=2)+"\n")
    data2={str(p):sha(p) for p in (*paths,PR,AR)}
    put(DIR/"prepared-v2.json",json.dumps(data2,indent=2)+"\n")
    print("V2 STAGED: Kivy DSI geometry and HDMI ads runtime copy.")
    print("Python runner syntax and trial advertising copy: PASS")
    print("Original production Panel.py, backend, Ads.py: UNMODIFIED")
    print("Original X11 desktop: STILL ACTIVE; recovery timer: DISABLED")
    print("Use existing bb_wayland_trial_activate_v1.py --check before any switch.")

def status():
    if os.geteuid()!=0:fail("Use sudo.")
    f=DIR/"prepared-v2.json"
    if not f.is_file():fail("Wayland V2 has not been staged.")
    data=json.loads(f.read_text())
    for name,digest in data.items():
        p=Path(name)
        if not p.is_file() or sha(p)!=digest:
            fail("Prepared V2 file missing or changed: "+name)
    ast.parse(PR.read_text(),filename=str(PR))
    ast.parse(AR.read_text(),filename=str(AR))
    backup_records()
    print("V2 configured files and source syntax: PASS")
    print("LightDM X11:", "autologin-session=LXDE-pi-x" in LD.read_text())
    print("Rollback armed:",(ROOT/"ARMED").exists())
    print("Rollback timer:",system("systemctl","is-active","bb-x11-recovery.timer"))
    print("Original production applications untouched: yes")

def main():
    p=argparse.ArgumentParser()
    g=p.add_mutually_exclusive_group(required=True)
    for t in ("check","prepare","status"):g.add_argument("--"+t,action="store_true")
    v=p.parse_args()
    try:
        if v.check:
            precheck()
            ads_transformed()
            print("V2 PRECHECK PASS: X11 restored, backups healthy, source anchors match.")
            print("READ ONLY: no source file, kiosk process or desktop changes.")
        elif v.prepare:prepare()
        else:status()
    except Exception as e:
        print("STOP:",type(e).__name__+":",e,file=sys.stderr)
        sys.exit(1)

if __name__=="__main__":
    main()
