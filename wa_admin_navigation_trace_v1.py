#!/usr/bin/env python3
"""Add/remove low-overhead, read-only navigation diagnostics to the active WA Kivy backend.

No controller commands, payment, inventory, or fiscalization edits.

--check: validate patch without changing files
--apply: make timestamped backup, insert tracing code, syntax-check atomically
--status: check installation state
--remove: remove only this diagnostic block, backed up, without reverting other changes

Requires installation while the kiosk is idle (systemctl --user stop bouquetmat.service).
"""
from __future__ import annotations
import argparse
from datetime import datetime
import os
from pathlib import Path
import re
import shutil
import tempfile

TARGET = Path(__file__).resolve().parent / "Panel_backend_merged.py"
START = "# WA_ADMIN_NAV_TRACE_V1_BEGIN"
END = "# WA_ADMIN_NAV_TRACE_V1_END"
BLOCK = r'''
# WA_ADMIN_NAV_TRACE_V1_BEGIN
# Purely observational diagnostics. Never mutates screen, event, payment or motor state.
import time as _wa_nav_time
import traceback as _wa_nav_traceback
from pathlib import Path as _wa_nav_Path
from kivy.core.window import Window as _wa_nav_Window

_WA_NAV_SCREENS = frozenset((
    "admin_login", "admin_panel", "admin_edit_item", "admin_esp_status", "revolver"
))
_WA_NAV_BUTTONS = {
    "admin_panel": (
        "admin_back_btn", "admin_home_btn", "admin_right_btn", "admin_left_btn",
        "reenable_all_btn",
    ),
    "admin_edit_item": (
        "edit_save_btn", "edit_back_btn", "edit_home_btn", "edit_reenable_btn",
    ),
    "revolver": ("revolver_back_btn", "revolver_home_btn", "revolver_cart_btn"),
    "admin_esp_status": (
        "admin_stats_back_btn", "admin_stats_home_btn", "admin_stats_left_btn",
        "esp_send_pwm_btn",
    ),
}

def _wa_navtrace_screen_changed(self, _screen_manager, value):
    try:
        before = str(getattr(self, "_wa_navtrace_previous", "?"))
        after = str(value)
        elapsed = _wa_nav_time.monotonic() - float(
            getattr(self, "_wa_navtrace_touch_time", _wa_nav_time.monotonic())
        )
        # Capture the actual synchronous Python call chain that changed root.current.
        frames = _wa_nav_traceback.extract_stack(limit=22)[:-1]
        chain = " > ".join(
            f"{_wa_nav_Path(f.filename).name}:{f.lineno}:{f.name}"
            for f in frames[-12:]
        )
        print(
            f"[WA NAV TRACE] SCREEN {before} -> {after}; "
            f"seconds_since_last_touch={elapsed:.1f}; "
            f"admin_mode={bool(getattr(self, '_admin_revolver_mode', False))}; "
            f"caller={chain}", flush=True,
        )
        self._wa_navtrace_previous = after
        if after in _WA_NAV_SCREENS:
            self._wa_navtrace_recent_admin_time = _wa_nav_time.monotonic()
    except Exception as err:
        print("[WA NAV TRACE] screen callback warning:", type(err).__name__, flush=True)

def _wa_navtrace_touch_down(self, _window, touch):
    try:
        self._wa_navtrace_touch_time = _wa_nav_time.monotonic()
        screen = str(getattr(getattr(self, "root", None), "current", ""))
        recent_admin = (
            _wa_nav_time.monotonic() - float(
                getattr(self, "_wa_navtrace_recent_admin_time", -999999.0)
            ) < 20.0
        )
        # Admin login touches (password entry) are excluded for privacy.
        if screen != "admin_login" and (screen in _WA_NAV_SCREENS or recent_admin):
            print(
                f"[WA NAV TRACE] TOUCH_DOWN screen={screen} "
                f"x={int(touch.x)} y={int(touch.y)}", flush=True,
            )
    except Exception:
        pass
    return False

def _wa_navtrace_button(self, screen_name, widget_id):
    try:
        print(
            f"[WA NAV TRACE] BUTTON_PRESS screen={screen_name} "
            f"widget={widget_id} root_current={self.root.current}", flush=True,
        )
    except Exception:
        pass

_wa_navtrace_previous_on_start = PanelBackendReservationApp.on_start

def _wa_navtrace_on_start(self):
    result = _wa_navtrace_previous_on_start(self)
    if getattr(self, "_wa_navtrace_attached", False):
        return result
    self._wa_navtrace_attached = True
    now = _wa_nav_time.monotonic()
    self._wa_navtrace_touch_time = now
    self._wa_navtrace_recent_admin_time = -999999.0
    self._wa_navtrace_previous = str(getattr(self.root, "current", "?"))
    self.root.bind(current=lambda instance, value, app=self:
                   _wa_navtrace_screen_changed(app, instance, value))
    _wa_nav_Window.bind(on_touch_down=lambda window, touch, app=self:
                        _wa_navtrace_touch_down(app, window, touch))
    bound = []
    for screen_name, names in _WA_NAV_BUTTONS.items():
        try:
            scr = self.root.get_screen(screen_name)
        except Exception:
            continue
        for widget_id in names:
            try:
                widget = scr.ids.get(widget_id)
                if widget is None:
                    continue
                widget.bind(on_press=lambda _button, n=screen_name, wid=widget_id, app=self:
                            _wa_navtrace_button(app, n, wid))
                bound.append(f"{screen_name}/{widget_id}")
            except Exception:
                continue
    print("[WA NAV TRACE] ACTIVE screen=", self._wa_navtrace_previous,
          "bound_buttons=", ",".join(bound), flush=True)
    return result

# Kivy's normal handlers and all original button callbacks remain unchanged.
PanelBackendReservationApp.on_start = _wa_navtrace_on_start
# WA_ADMIN_NAV_TRACE_V1_END
'''.strip('\n') + '\n\n'


def validate(src: str):
    if (START in src) != (END in src):
        raise ValueError("Incomplete navigation trace patch marker")
    if START in src:
        if src.count(START) != 1 or src.count(END) != 1:
            raise ValueError("Duplicate navigation trace patch markers")
        return "installed"
    if "class PanelBackendReservationApp(" not in src:
        raise ValueError("PanelBackendReservationApp class missing")
    if "PanelBackendReservationApp.on_start" not in src:
        raise ValueError("Backend on_start method binding missing")
    if "admin_save_edit" not in src and "goto_admin_edit" not in src:
        raise ValueError("Admin editor references missing")
    entries = list(re.finditer(
        r'(?m)^if __name__\s*==\s*[\'\"]__main__[\'\"]\s*:', src))
    if len(entries) != 1:
        raise ValueError(f"Expected exactly one main entrypoint, found {len(entries)}")
    return entries[0].start()


def build(src, mode, pos):
    if mode == "apply":
        assert isinstance(pos, int)
        output = src[:pos] + BLOCK + "\n" + src[pos:]
    elif mode == "remove":
        pattern = r'(?ms)^' + re.escape(START) + r'\n.*?^' + re.escape(END) + r'\n{2,3}'
        output, count = re.subn(pattern, "", src)
        if count != 1:
            raise ValueError("Could not identify exact navigation trace block for removal")
    else:
        raise ValueError(mode)
    compile(output, str(TARGET), "exec")
    return output


def atomic_write(src):
    backup_dir = TARGET.parent / "wa_ui_patch_backups" / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup = backup_dir / TARGET.name
    shutil.copy2(TARGET, backup)
    fd, temp_name = tempfile.mkstemp(prefix=".wa_admin_navtrace_", suffix=".py", dir=str(TARGET.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(src)
            f.flush()
            os.fsync(f.fileno())
        shutil.copymode(TARGET, temp_name)
        os.replace(temp_name, TARGET)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    return backup


def main():
    p = argparse.ArgumentParser(description=__doc__)
    options = p.add_mutually_exclusive_group(required=True)
    options.add_argument("--check", action="store_true")
    options.add_argument("--status", action="store_true")
    options.add_argument("--apply", action="store_true")
    options.add_argument("--remove", action="store_true")
    args = p.parse_args()
    if not TARGET.is_file():
        raise SystemExit(f"STOP: No such backend: {TARGET}")
    src = TARGET.read_text(encoding="utf-8")
    try:
        found = validate(src)
        state = "installed" if found == "installed" else "not installed"
        if args.status:
            print("WA admin navigation trace:", state)
            return
        if args.check:
            if found == "installed":
                print("PASS: Trace already installed; no file modified")
            else:
                build(src, "apply", found)
                print("PASS: Admin navigation trace patch compiles; no file modified")
            print("PASS: No changes to POS/FURS, motors, orders, or button behavior")
            return
        if args.apply and found == "installed":
            print("ALREADY INSTALLED: no changes")
            return
        if args.remove and found != "installed":
            print("NOT INSTALLED: nothing to remove")
            return
        updated = build(src, "remove" if args.remove else "apply", found)
        backup = atomic_write(updated)
        print("SUCCESS:", "Removed" if args.remove else "Enabled", "Admin navigation trace")
        print("Backup:", backup)
        print("UNCHANGED: all existing navigation handlers, POS/FURS, ESP32")
    except Exception as exc:
        raise SystemExit(f"STOP: {exc}") from exc

if __name__ == "__main__":
    main()
