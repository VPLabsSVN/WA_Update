#!/usr/bin/env python3
"""BB (Test1) guarded X11 kiosk restart for the installed Reservation V2.4 (V2).

Commands:
  sudo python3 bb_reservation_v24_restart_guard_v1.py --check
  sudo python3 bb_reservation_v24_restart_guard_v1.py --prepare
  sudo python3 bb_reservation_v24_restart_guard_v1.py --status
  sudo python3 bb_reservation_v24_restart_guard_v1.py --restart
  sudo python3 bb_reservation_v24_restart_guard_v1.py --confirm
  sudo python3 bb_reservation_v24_restart_guard_v1.py --restore
No screens, ads, WA backend, ESP32, reservation or payment API calls.
The watchdog is SYSTEM-scoped and remains active if the remote terminal disconnects.
"""
from pathlib import Path
import argparse
import hashlib
import json
import os
import pwd
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time

HOME=Path("/home/pi")
APP=HOME/"WA/Main_program/Fis"
BACKEND=APP/"Panel_backend_merged.py"
POLICY=APP/"bb_reservation_v24_policy.py"
ADS=APP/"Ads.py"
LOG=HOME/"panel_start.log"
LEDGER=APP/".bb_reservation_v24_recovery.json"
BACKUP=HOME/"bb_reservation_v24_backups/20261119_212127"
MANIFEST=BACKUP/"expected_installed_sha256.txt"
STAGE=Path("/var/lib/bb-v24-kiosk-restart")
ARM=STAGE/"ARMED"
ENV=STAGE/"original-process-env.bin"
RUN=Path("/usr/local/sbin/bb_reservation_v24_restart_guard.py")
UNIT=Path("/etc/systemd/system/bb-v24-restart-rescue.service")
TIMER=Path("/etc/systemd/system/bb-v24-restart-rescue.timer")
TIMER_NAME="bb-v24-restart-rescue.timer"
PREV=BACKUP/"Panel_backend_merged.py"

def fail(message):
    raise RuntimeError(message)

def run(*args,check=True,timeout=20):
    p=subprocess.run(args,capture_output=True,text=True,timeout=timeout)
    if check and p.returncode!=0:
        fail("Command failed: "+str(args[0])+" ("+str(p.returncode)+"): "+p.stderr[:200])
    return p

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def expected_hashes():
    if not MANIFEST.is_file():
        fail("BB V2.4 installation manifest missing")
    try:
        values={x.split("  ",1)[1]:x.split("  ",1)[0]
                for x in MANIFEST.read_text().splitlines()}
    except Exception:
        fail("Invalid V2.4 SHA manifest")
    if sorted(values)!=["Panel_backend_merged.py","bb_reservation_v24_policy.py"]:
        fail("Unexpected V2.4 manifest entries")
    return values

def processes(target):
    # Match exact Python script arguments, never pgrep's own command line.
    found=[]
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():continue
        try:
            a=(proc/"cmdline").read_bytes().split(b"\0")
            a=[x.decode("utf-8",errors="replace") for x in a if x]
            if len(a)<2:continue
            if not (a[0].endswith("/python3") or a[0].endswith("/python")
                    or "/python3." in a[0]):continue
            if a[-1]!=target and a[-1]!=str(APP/target):
                continue
            if (proc/"cwd").resolve()!=APP:continue
            found.append(int(proc.name))
        except (OSError,ValueError,FileNotFoundError):
            continue
    return sorted(found)

def foundation(installed=True):
    if os.geteuid()!=0:fail("Run with sudo, not as an unprivileged user")
    if os.uname().nodename!="Test1":fail("Safety lock: BB Test1 only")
    if shutil.disk_usage("/").free<160*1024*1024:fail("Storage below 160 MiB")
    for p in (BACKEND,ADS,POLICY,PREV,MANIFEST):
        if not p.is_file():fail("Required BB file missing: "+str(p))
    if LEDGER.exists():
        fail("Reservation recovery ledger exists; reconcile any payment before restarting")
    shas=expected_hashes()
    if installed:
        for p in (BACKEND,POLICY):
            if sha(p)!=shas[p.name]:
                fail("Installed V2.4 source changed since installation: "+p.name)
    if not APP.joinpath("Panel.py").is_file():
        fail("Base Kivy app missing")
    if not Path("/etc/lightdm/lightdm.conf").is_file():
        fail("LightDM configuration missing")
    light=Path("/etc/lightdm/lightdm.conf").read_text()
    if "autologin-session=LXDE-pi-x" not in light:
        fail("BB is not configured for original X11 desktop")
    if Path("/var/lib/bb-x11-recovery/ARMED").exists():
        fail("Wayland rollback guard still armed")
    if not Path("/tmp/.X11-unix/X0").exists():
        fail("X11 socket not present; do not restart")
    if not ENV.is_file() and ARM.exists():
        fail("Restart armed but process environment snapshot absent")
    if run("systemctl","is-active","lightdm.service",check=False).stdout.strip()!="active":
        fail("LightDM is not active")
    return shas

def preflight():
    shas=foundation()
    if len(processes("Panel_backend_merged.py"))!=1:
        fail("Expected exactly one original BB kiosk process")
    if len(processes("Ads.py"))!=1:
        fail("Expected exactly one independent BB ads process")
    # Syntax-check by AST only; do not import Kivy, hardware or payment services.
    import ast
    for p in (BACKEND,POLICY):
        try:ast.parse(p.read_text(),filename=str(p))
        except SyntaxError as err:fail("Invalid Python syntax: "+str(err))
    print("BB Reservation V2.4 restart preflight PASS")
    print("Current installed source and policy SHA: verified")
    print("Exactly one kiosk and Ads.py process: verified")
    print("X11, LightDM and required source: verified")
    print("Recovery ledger: absent")
    print("No processes restarted.")

def stage():
    preflight()
    if ARM.exists():fail("Already armed; do not stage again")
    if not STAGE.exists():STAGE.mkdir(mode=0o700,parents=True)
    if not RUN.exists():
        shutil.copy2(Path(__file__).resolve(),RUN)
        os.chmod(RUN,0o700)
    elif sha(RUN)!=sha(Path(__file__).resolve()):
        fail("Installed recovery program differs; inspect before replacing")
    unit_text="""[Unit]
Description=BB V2.4 kiosk rescue, restores previous backend if not confirmed
ConditionPathExists=/var/lib/bb-v24-kiosk-restart/ARMED
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /usr/local/sbin/bb_reservation_v24_restart_guard.py --auto-restore
"""
    timer_text="""[Unit]
Description=BB V2.4 kiosk rollback after unconfirmed restart
[Timer]
OnActiveSec=8min
AccuracySec=10s
Unit=bb-v24-restart-rescue.service
[Install]
WantedBy=timers.target
"""
    UNIT.write_text(unit_text)
    TIMER.write_text(timer_text)
    os.chmod(UNIT,0o644)
    os.chmod(TIMER,0o644)
    run("systemctl","daemon-reload")
    print("GUARD PREPARED: root rescue timer installed but DISABLED.")
    print("No running applications or source files changed.")

def get_original_env(pid):
    data=Path("/proc")/str(pid)/"environ"
    raw=data.read_bytes()
    env={}
    for entry in raw.split(b"\0"):
        if not entry or b"=" not in entry:continue
        name,value=entry.split(b"=",1)
        env[name.decode(errors="ignore")]=value.decode(errors="surrogateescape")
    env.update(HOME=str(HOME),USER="pi",LOGNAME="pi",
               DISPLAY=":0",XAUTHORITY="/home/pi/.Xauthority")
    return env

def launch(env):
    if len(processes("Panel_backend_merged.py")):
        fail("Refusing to create a duplicate BB kiosk process")
    u=pwd.getpwnam("pi")
    def lower_permissions():
        os.initgroups("pi",u.pw_gid)
        os.setgid(u.pw_gid)
        os.setuid(u.pw_uid)
    with open(LOG,"ab",buffering=0) as f:
        p=subprocess.Popen(
            ["/usr/bin/python3",str(BACKEND)],
            cwd=str(APP),env=env,
            stdin=subprocess.DEVNULL,stdout=f,stderr=subprocess.STDOUT,
            start_new_session=True,preexec_fn=lower_permissions,close_fds=True)
    return p.pid

def stop_old(pid):
    os.kill(pid,signal.SIGTERM)
    for i in range(50):
        if not (Path("/proc")/str(pid)).exists():return
        try:
            if (Path("/proc")/str(pid)/"stat").read_text().split()[2]=="Z":return
        except (OSError,IndexError):return
        time.sleep(0.2)
    fail("Old kiosk did not terminate within 10 seconds; no force kill performed")

def arm():
    if not RUN.is_file() or not TIMER.is_file():
        fail("Root rollback watchdog not staged; run --prepare")
    if sha(RUN)!=sha(Path(__file__).resolve()):
        fail("Root watchdog program changed; refuse activation")
    if ARM.exists():fail("Restart already armed")
    ARM.write_text("unconfirmed kiosk restart\n")
    os.chmod(ARM,0o600)
    try:
        run("systemctl","enable","--now",TIMER_NAME)
        if run("systemctl","is-active",TIMER_NAME).stdout.strip()!="active":
            fail("Independent restart rescue timer is not active")
    except BaseException:
        ARM.unlink(missing_ok=True)
        raise

def disarm():
    run("systemctl","disable","--now",TIMER_NAME,check=False)
    ARM.unlink(missing_ok=True)
    ENV.unlink(missing_ok=True)

def do_restart():
    preflight()
    if ARM.exists():fail("Trial already armed; inspect --status")
    if not TIMER.is_file() or not RUN.is_file():fail("Run --prepare first")
    pid=processes("Panel_backend_merged.py")[0]
    env=get_original_env(pid)
    answer=input("BB IDLE and no payment/door action? Type RESTART-BB-V24: ")
    if answer!="RESTART-BB-V24":fail("Cancelled")
    # Environment not printed; recover it only for restarting old version.
    ENV.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    ENV.write_bytes(b"\0".join((k+"="+v).encode(errors="surrogateescape")
                              for k,v in env.items()) + b"\0")
    os.chmod(ENV,0o600)
    arm()
    print("Independent 8-minute V2.4 rollback timer active.",flush=True)
    print("Stopping ONLY the main BB kiosk. Advertising left running.",flush=True)
    stop_old(pid)
    new_pid=launch(env)
    time.sleep(14)
    after=processes("Panel_backend_merged.py")
    if after!=[new_pid]:
        print("New kiosk process failed early; restoring original immediately",flush=True)
        restore()
        fail("V2.4 kiosk startup failed; prior backend restored. Check log before retry.")
    print("New BB reservation V2.4 kiosk process running: PID",new_pid,flush=True)
    print("UNCONFIRMED. Do not process real reservations yet.")
    print("Run --status; if healthy and inspected, use --confirm before the 8-minute rescue timer fires.")

def existing_env():
    if not ENV.is_file():fail("Original kiosk environment snapshot is missing")
    raw=ENV.read_bytes()
    env={}
    for entry in raw.split(b"\0"):
        if not entry or b"=" not in entry:continue
        k,v=entry.split(b"=",1)
        env[k.decode(errors="ignore")]=v.decode(errors="surrogateescape")
    env.update(HOME=str(HOME),USER="pi",LOGNAME="pi",DISPLAY=":0",
               XAUTHORITY="/home/pi/.Xauthority")
    return env

def restore(auto=False):
    if os.geteuid()!=0:fail("Use sudo")
    if auto and not ARM.exists():return
    if LEDGER.exists():
        print("CRITICAL: BB payment recovery ledger exists; no automatic version rollback.",flush=True)
        print("Manual payment reconciliation is required.",flush=True)
        return
    if not PREV.is_file():fail("Previous performance backend missing")
    perf=HOME/"bb_performance_v1_backups/20261119_183059/new_sha256.txt"
    expected={a.split("  ",1)[1]:a.split("  ",1)[0] for a in perf.read_text().splitlines()}
    if sha(PREV)!=expected.get("Panel_backend_merged.py"):
        fail("Previous working BB backend hash mismatch")
    before=expected_hashes()
    # Do not overwrite unexpected code.
    if BACKEND.is_file() and sha(BACKEND)!=before["Panel_backend_merged.py"]:
        fail("Live backend differs from installed hash; manual reconciliation required")
    if POLICY.is_file() and sha(POLICY)!=before["bb_reservation_v24_policy.py"]:
        fail("Policy file differs from installed hash; manual reconciliation required")
    pids=processes("Panel_backend_merged.py")
    if len(pids)>1:fail("Multiple kiosk processes; refusing rescue")
    env=existing_env()
    for pid in pids:stop_old(pid)
    fd,temp=tempfile.mkstemp(prefix=".bb-v24-backup-",dir=APP)
    try:
        with os.fdopen(fd,"wb") as f:
            os.fchmod(f.fileno(),stat.S_IMODE(BACKEND.stat().st_mode))
            f.write(PREV.read_bytes())
            f.flush();os.fsync(f.fileno())
        os.replace(temp,BACKEND)
    finally:
        if os.path.exists(temp):os.unlink(temp)
    POLICY.unlink(missing_ok=True)
    pid=launch(env)
    print("BB prior Performance Stage 1 backend restored; kiosk restarted PID",pid,flush=True)
    disarm()
    print("Advertising, X11, WA backend and ESP32 untouched.",flush=True)

def status():
    if os.geteuid()!=0:fail("Use sudo")
    shas=expected_hashes()
    print("BB X11 Reservation V2.4")
    print("Current source:", "V2.4 installed" if BACKEND.is_file() and sha(BACKEND)==shas["Panel_backend_merged.py"] else "other version")
    print("Policy present:",POLICY.is_file())
    print("Main kiosk PIDs:",processes("Panel_backend_merged.py"))
    print("Ads.py PIDs:",processes("Ads.py"))
    print("Payment recovery ledger present:",LEDGER.exists())
    print("Rollback armed:",ARM.exists())
    print("Recovery timer:",run("systemctl","is-active",TIMER_NAME,check=False).stdout.strip() or "inactive")
    p=run("systemctl","list-timers",TIMER_NAME,"--all","--no-pager",check=False)
    for line in p.stdout.splitlines():
        if "bb-v24-restart-rescue.timer" in line:print("Timer:",line.strip())
    print("No payment/door operations performed by this status check")

def confirm():
    foundation()
    if not ARM.exists():fail("No unconfirmed restart to approve")
    if run("systemctl","is-active",TIMER_NAME).stdout.strip()!="active":
        fail("Rescue timer not active; cannot confirm")
    if len(processes("Panel_backend_merged.py"))!=1:
        fail("Main kiosk not running uniquely")
    if len(processes("Ads.py"))!=1:
        fail("Advertising application not running uniquely")
    if not Path("/tmp/mpv_ads.sock").is_socket():
        fail("Ads video socket missing")
    print("Source hashes and kiosk/ads process health: PASS")
    print("Remote shell cannot establish touchscreen presentation or transaction correctness.")
    answer=input("Reviewed logs and confirmed intended activation? Type CONFIRM-BB-V24: ")
    if answer!="CONFIRM-BB-V24":fail("Not confirmed; rescue timer stays armed")
    disarm()
    print("BB V2.4 restart confirmed; automatic code rollback DISARMED.")
    print("PLA/Wolt/unpaid end-to-end scenarios still require controlled verification.")

def main():
    p=argparse.ArgumentParser()
    g=p.add_mutually_exclusive_group(required=True)
    for name in ("check","prepare","status","restart","confirm","restore","auto-restore"):
        g.add_argument("--"+name,action="store_true")
    a=p.parse_args()
    try:
        if a.check:preflight()
        elif a.prepare:stage()
        elif a.status:status()
        elif a.restart:do_restart()
        elif a.confirm:confirm()
        elif a.restore:restore()
        elif a.auto_restore:restore(auto=True)
    except Exception as e:
        print("STOP:",type(e).__name__+":",e,file=sys.stderr)
        sys.exit(1)

if __name__=="__main__":
    main()
