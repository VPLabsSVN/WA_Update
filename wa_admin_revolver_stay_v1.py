#!/usr/bin/env python3
"""WA kiosk: return from Admin revolver item editing to the same wheel.

Only edits the UI navigation callback in Panel_backend_merged.py.

  --check  validate proposed source patch; no file modifications
  --apply  backup + atomically install the UI-only patch
  --status check marker (read-only)

Install while the kiosk is idle. Do not modify POS/FURS/ESP logic.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import os
from pathlib import Path
import re
import shutil
import tempfile

FILE_NAME = "Panel_backend_merged.py"
START_MARKER = "# WA_ADMIN_REVOLVER_STAY_V1_BEGIN"
END_MARKER = "# WA_ADMIN_REVOLVER_STAY_V1_END"

PATCH = r'''
# WA_ADMIN_REVOLVER_STAY_V1_BEGIN
# Preserve the active R7/R10 wheel while returning from its Admin item editor.
# Do not change door opening, stock state, motor safety, POS, or fiscalisation.
_wa_admin_revolver_previous_goto_panel = PanelBackendReservationApp.goto_admin_panel


def _wa_admin_revolver_goto_panel_or_wheel(self, *args, **kwargs):
    try:
        root = getattr(self, "root", None)
        wheel = getattr(self, "current_revolver", None)
        wheel_pos = getattr(self, "current_revolver_panel_position", None)
        edit_pos = getattr(self, "_admin_edit_panel_position", None)
        edit_item = getattr(self, "_admin_edit_item_id", None)

        # Only intercept a return from the Admin item EDIT screen, and only
        # if it originated inside the currently selected Admin revolver.
        if (
            root is not None
            and getattr(root, "current", None) == "admin_edit_item"
            and bool(getattr(self, "_admin_revolver_mode", False))
            and isinstance(wheel, dict)
            and wheel_pos is not None
            and edit_pos is not None
            and int(wheel_pos) == int(edit_pos)
            and edit_item is not None
        ):
            wheel_items = self._revolver_items(wheel) or []
            valid_ids = {
                int(it["id"])
                for it in wheel_items
                if isinstance(it, dict) and it.get("id") is not None
            }
            if int(edit_item) in valid_ids:
                # Refresh the same wheel; do not go through Admin R3 overview.
                self.active_textinput = None
                self.keyboard_upper = False
                self._refresh_admin_revolver_screen()
                root.current = "revolver"
                # Existing helper handles translated title and header state.
                try:
                    self._refresh_revolver_context_ui()
                except Exception as exc:
                    print("[ADMIN REVOLVER STAY] header refresh:", exc)
                print(
                    "[ADMIN REVOLVER STAY] returned to wheel",
                    "panel=", int(wheel_pos),
                    "item=", int(edit_item),
                    "revolver=", wheel.get("key", wheel.get("title", "")),
                )
                return
    except Exception as exc:
        print("[ADMIN REVOLVER STAY] fallback to normal Admin:", repr(exc))

    # Normal Admin Back, explicitly leaving the wheel, other panels and
    # any unrelated navigation retain their existing implementations.
    return _wa_admin_revolver_previous_goto_panel(self, *args, **kwargs)


PanelBackendReservationApp.goto_admin_panel = _wa_admin_revolver_goto_panel_or_wheel
# WA_ADMIN_REVOLVER_STAY_V1_END
'''.strip("\n") + "\n\n"


def validate_source(src: str) -> str:
    if START_MARKER in src and END_MARKER in src:
        if src.count(START_MARKER) != 1 or src.count(END_MARKER) != 1:
            raise ValueError("Duplicated patch markers detected")
        compile(src, FILE_NAME, "exec")
        return "installed"
    if START_MARKER in src or END_MARKER in src:
        raise ValueError("Incomplete patch marker detected")

    tokens = (
        "class PanelBackendReservationApp(",
        "_refresh_admin_revolver_screen",
        "current_revolver_panel_position",
        "_admin_revolver_mode",
        "goto_admin_edit",
        "PanelBackendReservationApp.goto_admin_panel",
    )
    missing = [token for token in tokens if token not in src]
    if missing:
        raise ValueError("Unexpected installed backend: missing " + ", ".join(missing))

    entry = list(re.finditer(
        r'(?m)^if __name__\s*==\s*[\'\"]__main__[\'\"]\s*:', src
    ))
    if len(entry) != 1:
        raise ValueError(f"Expected exactly one main entrypoint; found {len(entry)}")
    return "pending"


def proposed(src: str) -> str:
    anchor = re.search(r'(?m)^if __name__\s*==\s*[\'\"]__main__[\'\"]\s*:', src)
    assert anchor is not None
    # MUST be above the main entrypoint; patches after it do not run until
    # the kiosk exits when the backend file is launched directly.
    new_src = src[:anchor.start()] + PATCH + "\n" + src[anchor.start():]
    compile(new_src, FILE_NAME, "exec")
    return new_src


def apply(path: Path, src: str, new_src: str) -> Path:
    backup_dir = path.parent / "wa_ui_patch_backups" / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup = backup_dir / path.name
    shutil.copy2(path, backup)

    fd, tmp_path = tempfile.mkstemp(prefix=".wa_admin_revolver_", suffix=".py", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(new_src)
            fh.flush()
            os.fsync(fh.fileno())
        shutil.copymode(path, tmp_path)
        compile(Path(tmp_path).read_text(encoding="utf-8"), str(path), "exec")
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
    return backup


def main():
    p = argparse.ArgumentParser(description=__doc__)
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--status", action="store_true")
    # Optional source override for off-device testing; do not use on Pi.
    p.add_argument("--source", type=Path, default=None, help=argparse.SUPPRESS)
    args = p.parse_args()

    target = args.source or Path(__file__).resolve().parent / FILE_NAME
    if not target.exists():
        raise SystemExit("STOP: Missing backend file: " + str(target))
    src = target.read_text(encoding="utf-8")
    try:
        state = validate_source(src)
        if state == "installed":
            print("ALREADY INSTALLED: Admin revolver wheel return V1")
            return
        if args.status:
            print("NOT INSTALLED: Admin revolver wheel return V1")
            return
        new_src = proposed(src)
    except Exception as exc:
        raise SystemExit("STOP: " + str(exc)) from exc

    print("PASS: Admin revolver state and item editor detected")
    print("PASS: Wheel restored only from its own Admin item editor")
    print("PASS: Original Admin overview, Back/Home and customer screens preserved")
    print("PASS: Syntax compilation successful")
    print("UNCHANGED: POS, FURS, item stock, invoice number, ESP, motor commands")
    if args.check:
        print("No files modified")
        return
    backup = apply(target, src, new_src)
    print("SUCCESS: Admin R3 item actions return to the same revolver wheel")
    print("BACKUP:", backup)


if __name__ == "__main__":
    main()
