#!/usr/bin/env python3
"""BB labwc readiness inspector (read-only).

Examines current X11->Wayland compatibility: labwc autostart, output profile,
touch bindings, session desktop files, and fallback service availability.
No file changes, services, passwords, IP addresses or raw configuration dumps.
"""
from pathlib import Path
import os
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET

HOME = Path.home()
LAB = HOME / ".config/labwc"
KANS = HOME / ".config/kanshi/config"

def shell(*args, env=None):
    try:
        r=subprocess.run(args, capture_output=True, text=True, timeout=8, env=env)
        return r.stdout.strip() if r.returncode==0 else ""
    except (OSError,subprocess.TimeoutExpired):
        return ""

def lines(path):
    try:
        return path.read_text(encoding="utf-8",errors="replace").splitlines()
    except OSError:
        return []

def yes(x):
    return "YES" if x else "NO"

def inspect_autostart():
    print("\n=== LABWC AUTOSTART ===")
    p=LAB/"autostart"
    src=lines(p)
    print("Present:", yes(p.is_file()),"line count:",len(src))
    active=[x.strip() for x in src if x.strip() and not x.lstrip().startswith("#")]
    # Only show recognised command categories, not raw commands with sensitive arguments.
    signatures=[
        ("XDG desktop autostart",r"lxsession-xdg-autostart|dex\s+.*autostart"),
        ("kanshi output manager",r"\bkanshi\b"),
        ("wlr-randr display configuration",r"wlr-randr"),
        ("Main kiosk / panel launcher",r"Panel_backend_merged|start-panel|panel-startup|Main_program/Fis"),
        ("Advertising launcher",r"start_ads|Ads\.py|ads-player"),
        ("Keyboard or input setup",r"onboard|matchbox|wvkbd|squeekboard|xinput"),
        ("Remote access / VNC",r"wayvnc|rpi-connect|vnc"),
    ]
    for label,pattern in signatures:
        print(label+":",yes(any(re.search(pattern,x,re.I) for x in active)))
    print("Active command lines (count only):",len(active))
    print("Potential duplicate kiosk-start entries:",sum(bool(re.search(r"Panel_backend_merged|start-panel|panel-startup",x,re.I)) for x in active))

def inspect_environment():
    print("\n=== LABWC ENVIRONMENT ===")
    p=LAB/"environment"
    print("Present:",yes(p.is_file()))
    config=lines(p)
    keys=[]
    for x in config:
        if not x.strip().startswith("#") and "=" in x:
            key=x.split("=",1)[0].strip()
            if re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*",key):
                keys.append(key)
    print("Configured variable NAMES (values hidden):",", ".join(keys) or "(none)")

def inspect_touch():
    print("\n=== LABWC TOUCH MAPPING ===")
    p=LAB/"rc.xml"
    print("rc.xml present:",yes(p.is_file()))
    if not p.is_file():return
    try:
        root=ET.parse(p).getroot()
        found=0
        for element in root.iter():
            if element.tag.rsplit("}",1)[-1].lower() in ("touch","tablet"):
                # Do not print arbitrary attributes, just device and mapped output.
                device=element.get("deviceName") or element.get("name") or "(unspecified)"
                mapping=element.get("mapToOutput") or "(unspecified)"
                print("Touch binding:",device[:85],"->",mapping[:85])
                found+=1
        print("Touch bindings found:",found)
        if found==0:
            print("No explicit DSI touchscreen mapping in labwc rc.xml")
    except ET.ParseError as e:
        print("rc.xml XML parsing error:",type(e).__name__)

def inspect_kanshi():
    print("\n=== KANSHI DISPLAY PROFILE ===")
    print("Config present:",yes(KANS.is_file()))
    src=lines(KANS)
    profiles=0
    modes=[]
    for x in src:
        y=x.strip()
        if not y or y.startswith("#"):continue
        if re.match(r"profile\b",y):
            profiles+=1
        if re.match(r"output\b",y) or (" mode " in " "+y) or ("position" in y):
            # Only output profile keyword/mode/position tokens, no display EDID data.
            clean=re.sub(r"[^A-Za-z0-9 _/@,=.:+-]","",y)
            modes.append(clean[:140])
    print("Profiles found:",profiles)
    for item in modes[:12]:print("Output directive:",item)
    if len(modes)>12:print("Additional output directives:",len(modes)-12)

def inspect_sessions():
    print("\n=== AVAILABLE SESSION CHOICES ===")
    for p in [Path("/usr/share/xsessions/LXDE-pi-x.desktop"),Path("/usr/share/wayland-sessions/LXDE-pi-labwc.desktop")]:
        print(str(p)+":",yes(p.is_file()))
        if p.is_file():
            # Exec field is normally a trusted OS command; only show basename.
            for line in lines(p):
                if line.startswith("Exec="):
                    print(" Session launcher command:",Path(line.split("=",1)[1].split()[0]).name)
                    break
    print("raspi-config:",yes(shutil.which("raspi-config")))
    print("LightDM:",shell("systemctl","is-active","lightdm.service") or "unknown")
    print("Graphical boot:",shell("systemctl","get-default") or "unknown")
    print("User linger:",shell("loginctl","show-user","pi","-p","Linger") or "unknown")
    print("Remote shell:",shell("rpi-connect","status").splitlines()[-1] if shutil.which("rpi-connect") else "unknown")
    print("systemd timers supported:",yes(shutil.which("systemctl") and shutil.which("systemd-run")))
    print("Disk free MiB:",shutil.disk_usage("/").free//1048576)

def inspect_input():
    print("\n=== CURRENT X11 TOUCH DEVICE (best effort) ===")
    env=dict(os.environ,DISPLAY=":0",XAUTHORITY="/home/pi/.Xauthority")
    output=shell("xinput","--list","--short",env=env)
    hits=[re.sub(r"[^A-Za-z0-9 ._+/-]"," ",l.strip())[:120] for l in output.splitlines()
          if re.search("touch|goodix|edt-ft|ilitek|ft5|display",l,re.I)]
    for line in hits[:15]:print("X11 input:",line)
    if not hits:print("No touchscreen device name detected; use physical input sysfs later")
    out=shell("xrandr","--query",env=env)
    for line in out.splitlines():
        if " connected" in line:
            print("X11 output:",line.split(" (")[0][:120])

def main():
    print("=== BB LABWC DUAL-DISPLAY READINESS V2 ===")
    print("READ ONLY: no machine changes, credentials, full command lines or backend connections.")
    inspect_autostart()
    inspect_environment()
    inspect_touch()
    inspect_kanshi()
    inspect_sessions()
    inspect_input()
    print("\nFINISHED. Do not switch to Wayland until a persistent X11 rollback timer is installed and verified.")

if __name__=="__main__":
    main()
