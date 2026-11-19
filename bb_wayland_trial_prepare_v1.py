#!/usr/bin/env python3
"""BB Wayland dual-display preparation V1 (INERT STAGING ONLY).
Run using sudo. --check is read-only. --prepare stages configs but neither
changes LightDM nor restarts any running application. An independent root-level
X11 guard must already have been staged and verified.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import subprocess
import sys

H=Path("/home/pi")
G=Path("/var/lib/bb-x11-recovery")
M=G/"manifest.json"
ST=H/"bb_wayland_trial"
K=H/".config/kanshi/config"
A=H/".config/labwc/autostart"
PD=H/".config/autostart/panel-startup.desktop"
AD=H/".config/autostart/ads-player.desktop"
LD=Path("/etc/lightdm/lightdm.conf")
PW=ST/"panel-wayland-aware.sh"
AW=ST/"ads-wayland-aware.sh"
READY=ST/"prepared.json"

KANS="""# BB Test1: DSI touchscreen left; HDMI advertising right
profile bb-dual {
    output DSI-1 enable mode 1280x800 position 0,0 transform normal
    output HDMI-A-2 enable mode 1280x720 position 1280,0 transform normal
}
"""

PANEL=r"""#!/bin/bash
set -eo pipefail
if [ "$XDG_SESSION_TYPE" != "wayland" ] && [ -z "$WAYLAND_DISPLAY" ]; then
  exec /home/pi/bin/start-panel.sh
fi
exec 9>/run/user/1000/bb_wayland_panel.lock
flock -n 9 || exit 0
if [ -z "$DISPLAY" ]; then
  echo "[BB] No Xwayland DISPLAY, not starting duplicate kiosk" >> /home/pi/panel_start.log
  exit 1
fi
unset WAYLAND_DISPLAY
export SDL_VIDEODRIVER=x11
export SDL_VIDEO_FULLSCREEN_HEAD=0
sleep 5
cd /home/pi/WA/Main_program/Fis
exec /usr/bin/python3 Panel_backend_merged.py >> /home/pi/panel_start.log 2>&1
"""

ADS=r"""#!/bin/bash
set -eo pipefail
if [ "$XDG_SESSION_TYPE" != "wayland" ] && [ -z "$WAYLAND_DISPLAY" ]; then
  exec /home/pi/WA/Main_program/Fis/start_ads_x11.sh
fi
exec 9>/run/user/1000/bb_wayland_ads.lock
flock -n 9 || exit 0
if [ -z "$DISPLAY" ]; then
  echo "[BB] No Xwayland DISPLAY, ads not started" >> /tmp/ads_python.log
  exit 1
fi
unset WAYLAND_DISPLAY
sleep 7
cd /home/pi/WA/Main_program/Fis
exec /usr/bin/python3 -u - >> /tmp/ads_python.log 2>&1 <<'PY'
import time
import Ads
Ads._install_signal_handlers()
Ads.start_ads_player(
    ads_dir="/home/pi/WA/Main_program/Ads",
    image_seconds="8",
    rescan_seconds=2.0,
    recursive=False,
    shuffle_initial=False,
    add_mode="append-play",
    mpv_exe="mpv",
    pipe_name="mpv_ads",
    verbose=True,
    mpv_log_path="/tmp/ads_mpv.log",
    window_x=1280,
    window_y=0,
    window_w=1280,
    window_h=720,
)
while True:
    time.sleep(1)
PY
"""

LABWC_ADD="""# BEGIN BB WAYLAND TRIAL
# The original ~/.config/autostart also runs; flock prevents duplicate kiosks.
(sleep 2; if ! pgrep -u pi -x kanshi >/dev/null; then /usr/bin/kanshi; fi) &
(sleep 4; /home/pi/bb_wayland_trial/panel-wayland-aware.sh) &
(sleep 6; /home/pi/bb_wayland_trial/ads-wayland-aware.sh) &
# END BB WAYLAND TRIAL
"""

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def fail(msg):
    raise RuntimeError(msg)

def verify_before():
    if os.geteuid()!=0: fail("Use sudo.")
    if os.uname().nodename!="Test1": fail("Only BB hostname Test1 supported.")
    if shutil.disk_usage("/").free<200*1024*1024: fail("Disk space under 200 MiB.")
    if not M.is_file() or not Path("/usr/local/sbin/bb-x11-recovery.py").is_file():
        fail("Independent X11 rollback guard missing.")
    if (G/"ARMED").exists(): fail("Rollback already armed; do not stage now.")
    if READY.exists(): fail("Trial already prepared; use --status.")
    records={x["path"]:x for x in json.loads(M.read_text())}
    for path in (K,A,PD,AD,LD):
        r=records.get(str(path))
        if r is None: fail("Missing X11 backup entry for "+str(path))
        if not r["exists"] or not path.is_file():
            fail("Missing expected existing configuration "+str(path))
        backup=G/("backup_%02d"%r["index"])
        if not backup.is_file() or sha(backup)!=r["sha256"] or sha(path)!=r["sha256"]:
            fail("Original config changed since guard was staged: "+str(path))
    if "autologin-session=LXDE-pi-x" not in LD.read_text():
        fail("X11 LightDM autologin missing.")
    if "user-session=LXDE-pi-x" not in LD.read_text():
        fail("X11 LightDM user session missing.")
    if "Exec=/home/pi/bin/start-panel.sh" not in PD.read_text():
        fail("Unexpected main kiosk desktop launcher.")
    if "Exec=/home/pi/WA/Main_program/Fis/start_ads_x11.sh" not in AD.read_text():
        fail("Unexpected advertising desktop launcher.")
    if "Goodix" not in (H/".config/labwc/rc.xml").read_text():
        fail("Expected Goodix touch mapping missing.")
    sys_drm=Path("/sys/class/drm")
    outputs=set()
    for s in sys_drm.glob("card*-*/status"):
        if s.read_text().strip()=="connected":
            outputs.add(s.parent.name.split("-",1)[1])
    if not {"DSI-1","HDMI-A-2"}.issubset(outputs):
        fail("Expected DSI-1 + HDMI-A-2 not both connected.")
    for exe in ("labwc","kanshi","Xwayland","flock"):
        if shutil.which(exe) is None: fail("Missing required program "+exe)
    if re.search(r"^\s*(?:exec|exit)\b",A.read_text(),re.MULTILINE):
        fail("Unexpected exit/exec in labwc autostart; manual review needed.")
    if "# BEGIN BB WAYLAND TRIAL" in A.read_text():
        fail("labwc trial content is already present.")
    if not (H/"WA/Main_program/Fis/Ads.py").is_file():
        fail("Advertising source missing.")

def put(path,content,mode=0o644):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(content)
    os.chmod(path,mode)
    usr=pwd.getpwnam("pi")
    os.chown(path,usr.pw_uid,usr.pw_gid)

def check_shell(source):
    p=subprocess.run(["bash","-n"],input=source,text=True,capture_output=True)
    if p.returncode!=0: fail("Wrapper syntax problem: "+p.stderr[:250])

def prepare():
    verify_before()
    check_shell(PANEL)
    check_shell(ADS)
    ST.mkdir(mode=0o755,parents=True,exist_ok=True)
    usr=pwd.getpwnam("pi")
    os.chown(ST,usr.pw_uid,usr.pw_gid)
    put(PW,PANEL,0o755)
    put(AW,ADS,0o755)
    put(K,KANS)
    put(A,A.read_text().rstrip()+"\n\n"+LABWC_ADD)
    put(PD,PD.read_text().replace("Exec=/home/pi/bin/start-panel.sh",
                                 "Exec="+str(PW),1))
    put(AD,AD.read_text().replace("Exec=/home/pi/WA/Main_program/Fis/start_ads_x11.sh",
                                 "Exec="+str(AW),1))
    data={str(x):sha(x) for x in (K,A,PD,AD,PW,AW)}
    put(READY,json.dumps(data,indent=2)+"\n")
    print("STAGED: BB Wayland dual-output layout, touch-aware launchers and ads geometry.")
    print("Original X11 applications are still running; desktop is NOT switched.")
    print("LightDM still X11; root rollback timer still DISABLED.")
    print("Stage path:",ST)

def status():
    if os.geteuid()!=0:fail("Use sudo.")
    print("Prepared:",READY.is_file())
    if READY.is_file():
        data=json.loads(READY.read_text())
        for item,digest in data.items():
            p=Path(item)
            if not p.is_file() or sha(p)!=digest:fail("Prepared file mismatch: "+item)
        check_shell(PW.read_text())
        check_shell(AW.read_text())
        print("Prepared hashes and shell syntax: PASS")
    print("LightDM X11:", "autologin-session=LXDE-pi-x" in LD.read_text())
    print("Rollback armed:",(G/"ARMED").exists())
    p=subprocess.run(["systemctl","is-active","bb-x11-recovery.timer"],
       capture_output=True,text=True)
    print("Rollback timer:",p.stdout.strip() or "inactive")
    print("No restart/reboot performed by this inspector.")

def main():
    a=argparse.ArgumentParser()
    g=a.add_mutually_exclusive_group(required=True)
    for name in ("check","prepare","status"):g.add_argument("--"+name,action="store_true")
    opts=a.parse_args()
    try:
        if opts.check:
            verify_before()
            check_shell(PANEL)
            check_shell(ADS)
            print("SAFE TO PREPARE: PASS. All changes remain inert until Wayland is activated.")
        elif opts.prepare:prepare()
        else:status()
    except Exception as e:
        print("STOP:",e,file=sys.stderr)
        sys.exit(1)

if __name__=="__main__":
    main()
