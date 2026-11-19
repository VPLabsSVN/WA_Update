#!/usr/bin/env python3
"""BB Performance Stage 1: reversible, BB-version-locked source optimization.

Changes only:
- Panel.py: reduce unnecessary global hover refreshes and input styling walks
- Panel_backend_merged.py: avoid unchanged config redraws, coalesce item-sync workers
Preserves BB remote temperature handling, ads, payments, reservations, hardware.
--check (default): NO production modification, offline syntax and structural tests.
--apply: guarded backup + atomic writes; no restart.
--rollback BACKUP_DIR: only with original installed target hashes; no restart.

DO NOT use this patch on Fin or any BB version with different hashes.
"""
from __future__ import annotations
import argparse
import ast
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime

APP = Path("/home/pi/WA/Main_program/Fis")
BACKUPS = Path.home() / "bb_performance_v1_backups"
EXPECTED = {
    "Panel.py": "f7bdcb3a4a2fc8337e59552baaab1a37af3d3fd238c909ad1a1775d7780fe20f",
    "Panel_backend_merged.py": "398e61cceca5e5520859c1cc1ec902b6d27ef878b7e08d61e981f5113cdca7ca",
}
FILES = tuple(EXPECTED)

def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def replace_once(s: str, a: str, b: str, name: str) -> str:
    count=s.count(a)
    if count != 1:
        raise ValueError(f"{name}: expected exactly one source anchor, found {count}")
    return s.replace(a,b,1)

def transform_panel(s: str) -> str:
    s=replace_once(s,
       "        Clock.schedule_once(self._refresh_all_hover_buttons, 0.40)\n"
       "        Clock.schedule_once(self._refresh_all_hover_buttons, 0.90)\n",
       "        # BB performance: two hover refreshes suffice (immediate + 150 ms).\n",
       "Panel.py hover repaints")
    s=replace_once(s,
       "        Clock.schedule_interval(lambda _dt: (self._force_light_input_text() or True), 0.20)\n",
       "        # Re-style inputs only when an input screen is visible; no 5Hz global scan.\n"
       "        Clock.schedule_interval(\n"
       "            lambda _dt: self._force_light_input_text()\n"
       "            if self.root and getattr(self.root, 'current', '') in\n"
       "            ('admin_login', 'reservation', 'admin_edit_item') else None,\n"
       "            5.0,\n"
       "        )\n",
       "Panel.py input styling")
    return s

def transform_backend(s: str) -> str:
    s=replace_once(s,
       '        self._admin_sync_last_hash: str = ""\n',
       '        self._admin_sync_last_hash: str = ""\n'
       '        self._bb_sync_lock = threading.Lock()\n'
       '        self._bb_sync_pending = None\n'
       '        self._bb_sync_running = False\n',
       "BB backend initialization")

    # Always refresh reservation data. Update the grids if reservations OR items changed.
    a=(
       "        self.backend_config_version = int(cfg.get(\"config_version\", self.backend_config_version) or self.backend_config_version)\n"
       "        self.server_reservation_codes = list(cfg.get(\"reservation_codes\") or [])\n"
       "\n"
       "        server_items = cfg.get(\"items\") or []\n"
    )
    b=(
       "        self.backend_config_version = int(cfg.get(\"config_version\", self.backend_config_version) or self.backend_config_version)\n"
       "        new_codes = list(cfg.get(\"reservation_codes\") or [])\n"
       "        reservations_changed = new_codes != self.server_reservation_codes\n"
       "        self.server_reservation_codes = new_codes\n"
       "\n"
       "        server_items = cfg.get(\"items\") or []\n"
       "        items_changed = False\n"
    )
    s=replace_once(s,a,b,"BB backend config header")
    a=(
       "                self.items = merged_items if merged_items else normalized\n"
       "                base.save_items_state(self.items)\n"
       "            finally:\n"
       "                self._backend_applying_config = False\n"
       "\n"
       "        self.refresh_selection_grid()\n"
    )
    b=(
       "                next_items = merged_items if merged_items else normalized\n"
       "                items_changed = next_items != getattr(self, 'items', [])\n"
       "                if items_changed:\n"
       "                    self.items = next_items\n"
       "                    base.save_items_state(self.items)\n"
       "            finally:\n"
       "                self._backend_applying_config = False\n"
       "\n"
       "        # Reservations can change without item prices or enabled states changing.\n"
       "        if not (items_changed or reservations_changed):\n"
       "            return\n"
       "        self.refresh_selection_grid()\n"
    )
    s=replace_once(s,a,b,"BB backend config refresh")

    a=(
       "    def backend_sync_items_async(self):\n"
       "        if not self.backend_client:\n"
       "            return\n"
       "        items_snapshot = self._backend_visible_items_snapshot()\n"
       "\n"
       "        def worker():\n"
       "            try:\n"
       "                self.backend_client.sync_items(items_snapshot, replace_missing=True)\n"
       "            except Exception as e:\n"
       "                print(f\"[BACKEND] item sync failed: {e}\")\n"
       "\n"
       "        threading.Thread(target=worker, daemon=True).start()\n"
    )
    b=(
       "    def backend_sync_items_async(self):\n"
       "        if not self.backend_client:\n"
       "            return\n"
       "        snapshot = self._backend_visible_items_snapshot()\n"
       "        with self._bb_sync_lock:\n"
       "            self._bb_sync_pending = snapshot\n"
       "            if self._bb_sync_running:\n"
       "                return\n"
       "            self._bb_sync_running = True\n"
       "\n"
       "        def worker():\n"
       "            while True:\n"
       "                with self._bb_sync_lock:\n"
       "                    if self._backend_stop or self._bb_sync_pending is None:\n"
       "                        self._bb_sync_running = False\n"
       "                        return\n"
       "                    pending = self._bb_sync_pending\n"
       "                    self._bb_sync_pending = None\n"
       "                try:\n"
       "                    self.backend_client.sync_items(pending, replace_missing=True)\n"
       "                except Exception as e:\n"
       "                    print(f\"[BACKEND] item sync failed: {e}\")\n"
       "\n"
       "        try:\n"
       "            threading.Thread(target=worker, daemon=True).start()\n"
       "        except Exception:\n"
       "            with self._bb_sync_lock:\n"
       "                self._bb_sync_running = False\n"
       "            raise\n"
    )
    s=replace_once(s,a,b,"BB backend sync worker")
    return s

def structural_tests(orig: dict, transformed: dict) -> None:
    for filename, data in transformed.items():
        ast.parse(data,filename=filename)
        compile(data,filename,"exec")
        print(f"Syntax OK: {filename}")
    old_backend = orig["Panel_backend_merged.py"]
    new_backend = transformed["Panel_backend_merged.py"]
    # No change to the temperature-logging remote message handler.
    def method(source: str, start: str, end: str) -> str:
        return source.split(start,1)[1].split(end,1)[0]
    anchor='    def _apply_remote_panel_message(self, msg: dict, addr):'
    next_anchor='    def _prune_stale_remote_panels(self):'
    if method(old_backend,anchor,next_anchor) != method(new_backend,anchor,next_anchor):
        raise ValueError("BB remote temperature handler was modified unexpectedly")
    # The reservation and payment methods must be untouched.
    for start,end in [
        ('    def reservation_collect(self):','    def _reservation_collect_failed('),
        ('    def _handle_payment_result(','    def _complete_successful_purchase('),
    ]:
        if method(old_backend,start,end) != method(new_backend,start,end):
            raise ValueError(f"BB payment/reservation method modified: {start}")
    # Reserved-status changes must trigger redraw even if items are identical.
    if "items_changed or reservations_changed" not in new_backend:
        raise ValueError("Reservation UI refresh safeguard absent")
    print("Structure OK: temperature logging, reservations, payments left unchanged")
    print("Structure OK: reservation metadata changes still refresh Kivy grids")

def atomically_write(path: Path, data: bytes) -> None:
    fd,temp=tempfile.mkstemp(prefix=".bb_perf_staged_",dir=path.parent)
    try:
        os.fchmod(fd,path.stat().st_mode & 0o777)
        with os.fdopen(fd,"wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp,path)
    finally:
        if os.path.exists(temp): os.unlink(temp)

def main():
    p=argparse.ArgumentParser()
    g=p.add_mutually_exclusive_group()
    g.add_argument("--check",action="store_true")
    g.add_argument("--apply",action="store_true")
    g.add_argument("--rollback",type=Path,metavar="BACKUP_DIRECTORY")
    args=p.parse_args()

    if args.rollback:
        backup=args.rollback.expanduser().resolve()
        if BACKUPS.resolve() not in backup.parents or not backup.is_dir():
            raise ValueError("Rollback requires a backup under ~/bb_performance_v1_backups")
        if any(not (backup/x).is_file() for x in FILES):
            raise ValueError("Incomplete rollback backup")
        if not (backup/"new_sha256.txt").is_file():
            raise ValueError("Missing installed-file hash manifest")
        for name in FILES:
            line=next((line.split("  ",1)[0] for line in (backup/"new_sha256.txt").read_text().splitlines()
                if line.endswith("  "+name)),None)
            if not line or sha((APP/name).read_bytes()) != line:
                raise ValueError("Installed application changed; refusing automatic rollback")
            if sha((backup/name).read_bytes()) != EXPECTED[name]:
                raise ValueError("Backup hash mismatch")
        if (APP/".fin_reservation_v2_recovery.json").exists():
            raise ValueError("Pending reservation payment ledger; reconcile before rollback")
        answer=input("BB idle, no payment in progress. Type ROLLBACK-BB-PERFORMANCE: ")
        if answer!="ROLLBACK-BB-PERFORMANCE":
            raise ValueError("Cancelled")
        for name in FILES:
            atomically_write(APP/name,(backup/name).read_bytes())
        print("Rollback restored source on disk. Desktop app NOT restarted.")
        return

    disk=shutil.disk_usage(APP)
    if disk.free < 100*1024*1024:
        raise ValueError("Less than 100 MiB free; stop and investigate disk usage first")
    originals={}
    output={}
    for name in FILES:
        raw=(APP/name).read_bytes()
        if sha(raw) != EXPECTED[name]:
            raise ValueError(f"{name} is not the expected BB source revision; no changes made")
        originals[name]=raw.decode("utf-8")
    output["Panel.py"]=transform_panel(originals["Panel.py"])
    output["Panel_backend_merged.py"]=transform_backend(originals["Panel_backend_merged.py"])
    structural_tests(originals,output)
    print("BB source hashes, transformation anchors, syntax and safeguards: PASS")
    print("NO backend, ESP32, ads, payment or reservation behavior modified")
    if not args.apply:
        print("DRY RUN: no live application or service files changed")
        return
    answer=input("BB must be idle. Type APPLY-BB-PERFORMANCE-STAGE1: ")
    if answer!="APPLY-BB-PERFORMANCE-STAGE1":
        raise ValueError("Cancelled")
    BACKUPS.mkdir(mode=0o700,exist_ok=True)
    backup=BACKUPS/datetime.now().strftime("%Y%m%d_%H%M%S")
    backup.mkdir(mode=0o700)
    for name in FILES:
        shutil.copy2(APP/name,backup/name)
    with (backup/"new_sha256.txt").open("w") as f:
        for name in FILES:
            f.write(sha(output[name].encode("utf-8"))+"  "+name+"\n")
    try:
        for name in FILES:
            atomically_write(APP/name,output[name].encode("utf-8"))
        for name in FILES:
            if sha((APP/name).read_bytes())!=sha(output[name].encode("utf-8")):
                raise ValueError(f"Unexpected installed hash for {name}")
    except Exception:
        for name in FILES:
            atomically_write(APP/name,(backup/name).read_bytes())
        raise
    print("BB PERFORMANCE STAGE 1 INSTALLED ON DISK ONLY")
    print("Backup:",backup)
    print("Do NOT restart if a customer is using BB or if a payment is underway")
    print("Desktop autostart remains unchanged; no reboot performed")

if __name__=="__main__":
    try:
        main()
    except Exception as exc:
        print(f"STOP: {type(exc).__name__}: {exc}",file=sys.stderr)
        raise SystemExit(1)
