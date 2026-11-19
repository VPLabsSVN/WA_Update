#!/usr/bin/env python3
"""BB X11 rollback guard V1; stage and verify without touching running GUI.
Specific to BB Test1. Requires sudo. No WAN/backend/ESP32 modifications.
"""
from pathlib import Path
import argparse, hashlib, json, os, shutil, subprocess, sys

ROOT=Path("/var/lib/bb-x11-recovery")
MAN=ROOT/"manifest.json"
ARM=ROOT/"ARMED"
SELF=Path("/usr/local/sbin/bb-x11-recovery.py")
UNIT=Path("/etc/systemd/system/bb-x11-recovery.service")
TIMER=Path("/etc/systemd/system/bb-x11-recovery.timer")
PI=Path("/home/pi")

def paths():
    lightdm=Path("/etc/lightdm/lightdm.conf")
    confd=lightdm.parent/"lightdm.conf.d"
    return [lightdm] + sorted(confd.glob("*.conf")) + [
        PI/".config/kanshi/config",
        PI/".config/labwc/autostart",
        PI/".config/labwc/rc.xml",
        PI/".config/autostart/panel-startup.desktop",
        PI/".config/autostart/ads-player.desktop",
        PI/"bin/start-panel.sh",
        PI/"bin/fix-displays.sh",
        PI/"WA/Main_program/Fis/start_ads_x11.sh",
    ]

def sha(b):
    return hashlib.sha256(b).hexdigest()

def run(*args,check=True):
    p=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                     text=True,timeout=30)
    if check and p.returncode:
        raise RuntimeError("Command failed: "+args[0]+": "+p.stderr[:300])
    return p

def validate():
    if os.geteuid()!=0:
        raise RuntimeError("Run with sudo.")
    if os.uname().nodename!="Test1":
        raise RuntimeError("Safety lock: only BB hostname Test1 is supported.")
    if not (PI/"WA/Main_program/Fis/Panel_backend_merged.py").is_file():
        raise RuntimeError("BB kiosk source not found.")
    for p in [Path("/usr/share/xsessions/LXDE-pi-x.desktop"),
              Path("/usr/share/wayland-sessions/LXDE-pi-labwc.desktop")]:
        if not p.is_file():
            raise RuntimeError("Expected desktop session is missing: "+str(p))
    if shutil.disk_usage("/").free<200*1024*1024:
        raise RuntimeError("Less than 200 MiB free disk; stop.")

def stage():
    validate()
    if MAN.exists():
        raise RuntimeError("Recovery backup already exists; use --status.")
    source=paths()
    if not source[0].is_file():
        raise RuntimeError("Missing /etc/lightdm/lightdm.conf")
    config="\n".join(x.read_text(errors="replace") for x in source
                     if x.is_file() and "/etc/lightdm/" in str(x))
    if "autologin-session=LXDE-pi-x" not in config:
        raise RuntimeError("Expected X11 autologin session not found.")
    if "user-session=LXDE-pi-x" not in config:
        raise RuntimeError("Expected X11 user session not found.")
    ROOT.mkdir(mode=0o700,parents=True,exist_ok=True)
    records=[]
    for i,p in enumerate(source):
        item={"path":str(p),"exists":p.exists(),"index":i}
        if p.exists():
            if not p.is_file() or p.is_symlink():
                raise RuntimeError("Unexpected non-regular file: "+str(p))
            backup=ROOT/("backup_%02d"%i)
            shutil.copy2(p,backup)
            item["sha256"]=sha(backup.read_bytes())
        records.append(item)
    MAN.write_text(json.dumps(records,indent=2)+"\n")
    os.chmod(MAN,0o600)
    shutil.copy2(Path(__file__),SELF)
    os.chmod(SELF,0o700)
    UNIT.write_text("""[Unit]
Description=BB X11 rescue if Wayland trial is not confirmed
ConditionPathExists=/var/lib/bb-x11-recovery/ARMED
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /usr/local/sbin/bb-x11-recovery.py --auto
""")
    TIMER.write_text("""[Unit]
Description=BB X11 automatic restore after 10 minute Wayland test
[Timer]
OnActiveSec=10min
AccuracySec=10s
Unit=bb-x11-recovery.service
[Install]
WantedBy=timers.target
""")
    os.chmod(UNIT,0o644)
    os.chmod(TIMER,0o644)
    run("systemctl","daemon-reload")
    print("STAGED: guarded X11 backup and watchdog unit installed.")
    print("Watchdog is NOT armed. Desktop and running applications unchanged.")
    print("Backups:",ROOT)

def restored_records():
    items=json.loads(MAN.read_text())
    for item in items:
        if item["exists"]:
            p=ROOT/("backup_%02d"%item["index"])
            if not p.is_file() or sha(p.read_bytes())!=item["sha256"]:
                raise RuntimeError("Backup integrity check FAILED. No files restored.")
    return items

def restore(auto=False):
    validate()
    if not MAN.exists():
        raise RuntimeError("No backup available.")
    if auto and not ARM.exists():
        print("Disarmed; no automatic recovery required.")
        return
    items=restored_records()
    for item in items:
        p=Path(item["path"])
        if item["exists"]:
            p.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(ROOT/("backup_%02d"%item["index"]),p)
        elif p.exists():
            if p.is_file():p.unlink()
            else:raise RuntimeError("Unexpected new non-file at "+str(p))
    ARM.unlink(missing_ok=True)
    run("systemctl","disable","--now","bb-x11-recovery.timer",check=False)
    print("Restored original X11 session, launchers, touch and monitor settings.",flush=True)
    if auto:
        print("Automatic rollback: reboot to original X11 now.",flush=True)
        run("systemctl","reboot","--no-wall",check=False)

def arm():
    validate()
    if not MAN.exists():raise RuntimeError("Run --stage first.")
    restored_records()
    if ARM.exists():raise RuntimeError("Already armed.")
    ARM.write_text("BB guarded migration active\n")
    os.chmod(ARM,0o600)
    try:
        run("systemctl","enable","--now","bb-x11-recovery.timer")
        p=run("systemctl","is-active","bb-x11-recovery.timer",check=False)
        if p.stdout.strip()!="active":
            raise RuntimeError("Independent rollback timer did not become active.")
    except Exception:
        ARM.unlink(missing_ok=True)
        raise
    print("ARMED: independent systemd watchdog will restore X11 and reboot after 10 minutes.")
    print("Do not arm until ready to immediately switch desktop.")

def disarm():
    validate()
    run("systemctl","disable","--now","bb-x11-recovery.timer",check=False)
    ARM.unlink(missing_ok=True)
    print("Watchdog disarmed; X11 recovery backup retained.")

def status():
    validate()
    print("Backup present:",MAN.exists())
    print("Backup integrity:", "PASS" if MAN.exists() and restored_records() is not None else "NOT STAGED")
    print("Watchdog armed:",ARM.exists())
    print("Timer enabled:",run("systemctl","is-enabled","bb-x11-recovery.timer",check=False).stdout.strip())
    print("Timer active:",run("systemctl","is-active","bb-x11-recovery.timer",check=False).stdout.strip())
    print("Current login session:")
    for line in run("loginctl","list-sessions","--no-legend",check=False).stdout.splitlines():
        if not line.strip():continue
        sid=line.split()[0]
        info=run("loginctl","show-session",sid,"-p","Name","-p","Type","-p","Desktop",check=False).stdout
        if "Name=pi" in info:print(info.strip().replace("\n","; "))

if __name__=="__main__":
    arg=argparse.ArgumentParser()
    g=arg.add_mutually_exclusive_group(required=True)
    for op in ("stage","status","arm","disarm","restore","auto"):
        g.add_argument("--"+op,action="store_true")
    x=arg.parse_args()
    try:
        if x.stage:stage()
        elif x.status:status()
        elif x.arm:arm()
        elif x.disarm:disarm()
        elif x.restore:restore()
        elif x.auto:restore(auto=True)
    except Exception as e:
        print("STOP:",type(e).__name__+":",e,file=sys.stderr)
        sys.exit(1)
