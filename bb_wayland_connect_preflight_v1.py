#!/usr/bin/env python3
"""BB Wayland migration preflight. READ ONLY. No display changes or restarts.

Reports launch, autologin, Xwayland and display capabilities without dumping
private files, authentication material or process arguments.
"""
from __future__ import annotations
from pathlib import Path
import os, shutil, subprocess, sys

def cmd(*argv, timeout=8, env=None):
    try:
        p = subprocess.run(argv, text=True, capture_output=True, timeout=timeout, env=env, check=False)
        return (p.returncode, p.stdout.strip(), p.stderr.strip())
    except (OSError, subprocess.TimeoutExpired):
        return (-1, "", "command unavailable")

def yesno(v):
    return "YES" if v else "NO"

def service(name, user=False):
    args=["systemctl"]
    if user: args.append("--user")
    args+=["show", name, "-p", "LoadState", "-p", "ActiveState", "-p", "SubState"]
    rc,out,_=cmd(*args)
    props=dict(x.split("=",1) for x in out.splitlines() if "=" in x)
    return f"{props.get('LoadState','?')}/{props.get('ActiveState','?')}/{props.get('SubState','?')}"

def literal_config(path):
    if not path.is_file():return None
    try:return path.read_text(encoding="utf-8",errors="replace")
    except OSError:return None

def detect_autologin():
    confs=[Path("/etc/lightdm/lightdm.conf")]
    root=Path("/etc/lightdm/lightdm.conf.d")
    if root.is_dir(): confs+=sorted(root.glob("*.conf"))
    result=[]
    for path in confs:
        content=literal_config(path)
        if content is None:continue
        for line in content.splitlines():
            clean=line.strip()
            if clean.startswith("#") or "=" not in clean:continue
            k,v=clean.split("=",1)
            if k.strip() in ("autologin-user","autologin-session","user-session"):
                value=v.strip()
                # Only print whether pi autologin is set and desktop type; no accounts beyond known pi.
                label=("pi" if value=="pi" else "<different user>" if k.strip()=="autologin-user"
                       else value[:80])
                result.append(f"{k.strip()}={label}")
    return result

def main():
    print("=== BB WAYLAND + RASPBERRY PI CONNECT PRE-MIGRATION AUDIT ===")
    print("Read-only: no reboot, no service changes, no GUI changes, no credential output.")
    print("Hostname:",os.uname().nodename)
    print("Main kiosk source present:",yesno(Path("/home/pi/WA/Main_program/Fis/Panel_backend_merged.py").is_file()))
    print("Ads source present:",yesno(Path("/home/pi/WA/Main_program/Fis/Ads.py").is_file()))
    print("\n--- Existing desktop and user sessions ---")
    rc,out,_=cmd("loginctl","list-sessions","--no-legend")
    sessions=[x.split()[0] for x in out.splitlines() if x.split()]
    for sid in sessions:
        _,props,_=cmd("loginctl","show-session",sid,"-p","Name","-p","Type","-p","Desktop","-p","Active","-p","State")
        items=dict(x.split("=",1) for x in props.splitlines() if "=" in x)
        if items.get("Name")=="pi":
            print(f"Session {sid}: {items.get('Type')}/{items.get('Desktop')}, active={items.get('Active')}")
    rc,linger,_=cmd("loginctl","show-user","pi","-p","Linger","-p","State")
    print("User session:",linger.replace("\n","; "))
    print("Display manager:",service("lightdm.service"))
    print("Connect main:",service("rpi-connect.service",user=True))
    print("Connect screen:",service("rpi-connect-wayvnc.service",user=True))
    print("Autologin settings:", "; ".join(detect_autologin()) or "(not found)")
    print("Graphical boot:",cmd("systemctl","get-default")[1])
    print("\n--- Installed programs ---")
    for name in ("labwc","wayvnc","Xwayland","wlr-randr","kanshi","raspi-config","loginctl","systemd-run","xrandr","xinput","mpv"):
        print(f"{name}: {yesno(shutil.which(name) is not None)}")
    print("Available Wayland sessions:")
    way=Path("/usr/share/wayland-sessions")
    print(", ".join(sorted(x.name for x in way.glob("*.desktop"))) if way.is_dir() else "(directory missing)")
    print("\n--- Present physical display connectors ---")
    drm=Path("/sys/class/drm")
    for f in sorted(drm.glob("card*-*/status")):
        try:
            s=f.read_text().strip()
            if s=="connected":print(f"{f.parent.name}: connected")
        except OSError:pass
    print("X11 display socket:",yesno(Path("/tmp/.X11-unix/X0").exists()))
    print("Wayland display socket:",yesno(any(Path("/run/user/1000").glob("wayland-[0-9]*"))))
    print("\n--- X11 output names and positions (best effort) ---")
    env=dict(os.environ,DISPLAY=":0",XAUTHORITY="/home/pi/.Xauthority")
    rc,out,err=cmd("xrandr","--query", env=env)
    if rc==0:
        for line in out.splitlines():
            if " connected" in line:
                print(line.split(" (")[0][:180])
    else: print("Unavailable from remote shell; current X11 output config was already provided.")
    print("\n--- Startup constraints ---")
    fix=literal_config(Path("/home/pi/bin/fix-displays.sh")) or ""
    main=literal_config(Path("/home/pi/bin/start-panel.sh")) or ""
    ads=literal_config(Path("/home/pi/WA/Main_program/Fis/start_ads_x11.sh")) or ""
    for name,contents in [("Main launcher",main),("Display fix",fix),("Ads launcher",ads)]:
        print(name,":","X11-specific" if any(x in contents for x in ("XAUTHORITY", "DISPLAY=:0", "xrandr", "xinput")) else "No obvious X11 anchor")
    print("\n--- Labwc user configuration presence ---")
    for filename in ("rc.xml","autostart","environment"):
        print(filename,":",yesno((Path.home()/".config/labwc"/filename).is_file()))
    print("\n--- Disk ---")
    disk=shutil.disk_usage("/")
    print(f"Free: {disk.free // (1024*1024)} MiB")
    print("\nCONCLUSION: NO MODIFICATIONS. Do not switch desktops until an automatic X11 rollback is armed.")
if __name__=="__main__":
    main()
