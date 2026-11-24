#!/usr/bin/env python3
"""Guard R3 open commands against double taps.  No ESP firmware/FURS/POS edits.

--check: read-only validation
--apply: backup + insert code into Panel.py (no service management)
--status: report whether the patch is installed
"""
import argparse
import ast
from datetime import datetime
import os
from pathlib import Path
import py_compile
import re
import shutil
import tempfile

MARK_START = "# WA_R3_SINGLE_FLIGHT_V1_BEGIN"
MARK_END = "# WA_R3_SINGLE_FLIGHT_V1_END"
TARGET = Path(__file__).resolve().parent / "Panel.py"
BLOCK = r"""
# WA_R3_SINGLE_FLIGHT_V1_BEGIN
# Pi-side guard for the R3 shared-door revolver controller.
# A final ESP ACK releases the guard; no ACK leaves it in uncertain state.
# The guard does NOT repeat movements, clear ESP faults, or change purchases.
import threading as _wa_r3_threading
from kivy.clock import Clock as _wa_r3_Clock

_wa_r3_mutex = _wa_r3_threading.RLock()
_wa_r3_inflight = {}
_wa_r3_types = frozenset(("remote_purchase", "remote_reopen", "remote_admin_open"))
_wa_r3_ack_names = {
    "remote_purchase": "remote_purchase_ack",
    "remote_reopen": "remote_reopen_ack",
    "remote_admin_open": "remote_admin_open_ack",
}

def _wa_r3_is_panel(self, position):
    try:
        panel = getattr(self, "remote_panels", {}).get(int(position)) or {}
        return str(panel.get("panel_type", "")).upper() == "R3"
    except Exception:
        return False

def _wa_r3_key(self, position):
    return (id(self), int(position))

def _wa_r3_pending(self, position):
    with _wa_r3_mutex:
        return _wa_r3_key(self, position) in _wa_r3_inflight

def _wa_r3_send_with_single_flight(self, position, payload, timeout_s=None):
    action = str((payload or {}).get("type", ""))
    pos = int(position)
    if action not in _wa_r3_types or not _wa_r3_is_panel(self, pos):
        if timeout_s is None:
            return _wa_r3_original_send(self, position, payload)
        return _wa_r3_original_send(self, position, payload, timeout_s=timeout_s)

    key = _wa_r3_key(self, pos)
    with _wa_r3_mutex:
        if key in _wa_r3_inflight:
            print("[R3 SINGLE FLIGHT] ignored duplicate", pos, action,
                  "existing=", _wa_r3_inflight[key]["action"])
            return {
                "ok": False, "suppressed": True,
                "error": "r3_open_already_in_progress_no_command_sent",
            }
        _wa_r3_inflight[key] = {"action": action, "uncertain": False}

    try:
        # R3 may spend time positioning and waiting for door feedback.
        # A too-short Pi socket timeout is NOT an ESP completion signal.
        items = (payload or {}).get("items") or []
        minimum = 300.0 if len(items) <= 1 else min(1200.0, 300.0 * len(items))
        requested = float(timeout_s) if timeout_s is not None else 0.0
        response = _wa_r3_original_send(
            self, pos, payload, timeout_s=max(minimum, requested))
    except Exception as exc:
        response = {"ok": False, "error": type(exc).__name__ + ": " + str(exc)}

    expected = _wa_r3_ack_names[action]
    is_matching_ack = (
        isinstance(response, dict)
        and response.get("type") == expected
    )
    has_final_results = is_matching_ack and isinstance(
        response.get("results"), list
    )
    rejected_before_execution = (
        is_matching_ack
        and isinstance(response.get("error"), str)
        and bool(response.get("error"))
        and response["error"] != "previous_order_in_progress_wait_for_final_ack"
    )
    # ESP only produces per-item results once its order worker completes.
    # An immediate rejection is also safe to release; no movement was started.
    is_terminal = has_final_results or rejected_before_execution
    with _wa_r3_mutex:
        if is_terminal:
            _wa_r3_inflight.pop(key, None)
        else:
            if key in _wa_r3_inflight:
                _wa_r3_inflight[key]["uncertain"] = True

    print("[R3 SINGLE FLIGHT]", action, "panel", pos,
          "terminal_ack=", is_terminal,
          "ok=", bool(response.get("ok")) if isinstance(response, dict) else False)
    if not is_terminal:
        print("[R3 SINGLE FLIGHT] NO FINAL ACK: new R3 opens remain blocked;"
              " check ESP order/door state before restarting kiosk")
    return response

def _wa_r3_set_reopen_buttons(self, disabled):
    try:
        for screen_name, button_id in (
            ("thanks", "reopen_door_btn"),
            ("receipt_printing", "receipt_reopen_btn"),
        ):
            screen = self.root.get_screen(screen_name)
            widget = screen.ids.get(button_id)
            if widget is not None:
                widget.disabled = bool(disabled)
    except Exception as exc:
        print("[R3 SINGLE FLIGHT] reopen visual update:", type(exc).__name__)

def _wa_r3_reopen_once_async(self):
    items = getattr(self, "_last_purchased_items", None) or []
    positions = set()
    for item in items:
        try:
            pos = int(item.get("panel_position", -1))
        except (ValueError, TypeError, AttributeError):
            continue
        if _wa_r3_is_panel(self, pos):
            positions.add(pos)

    # Non-R3 reopening is unchanged.
    if not positions:
        return _wa_r3_original_reopen(self)

    if getattr(self, "_wa_r3_reopen_busy", False) or any(
        _wa_r3_pending(self, pos) for pos in positions
    ):
        print("[R3 SINGLE FLIGHT] ignored repeated customer reopen")
        return
    self._wa_r3_reopen_busy = True
    _wa_r3_set_reopen_buttons(self, True)

    def worker():
        try:
            # Preserve the existing local+remote reopening logic, but no longer
            # block the Kivy UI thread while waiting for the R3 final ACK.
            _wa_r3_original_reopen(self)
        except Exception as exc:
            print("[R3 SINGLE FLIGHT] customer reopen:", repr(exc))
        finally:
            def done(_dt):
                if any(_wa_r3_pending(self, pos) for pos in positions):
                    print("[R3 SINGLE FLIGHT] customer controls held:"
                          " controller completion remains uncertain")
                    return
                self._wa_r3_reopen_busy = False
                _wa_r3_set_reopen_buttons(self, False)
            _wa_r3_Clock.schedule_once(done, 0)
    _wa_r3_threading.Thread(
        target=worker, name="r3-customer-reopen", daemon=True
    ).start()

def _wa_r3_admin_worker_reopen_once_async(self, item_id):
    try:
        pos = int(self.current_panel_position)
    except Exception:
        return _wa_r3_original_admin_worker(self, item_id)

    if not _wa_r3_is_panel(self, pos):
        return _wa_r3_original_admin_worker(self, item_id)
    if _wa_r3_pending(self, pos) or getattr(
        self, "_wa_r3_admin_worker_busy", False
    ):
        print("[R3 SINGLE FLIGHT] ignored repeated admin worker open")
        return
    if not self.admin_can_reopen_doors():
        self._refresh_admin_panel_title(self.t("access_denied"))
        return
    it = self._get_panel_item(pos, item_id)
    if it is None:
        return
    self._wa_r3_admin_worker_busy = True
    iid = int(it["id"])
    self._admin_set_pending_ids([iid])
    self._refresh_admin_panel_title(
        f"{self.t('door_opening_now')} {self._format_bouquet_name(it)}"
    )

    def worker():
        try:
            ack = self._send_remote_message_with_ack(
                pos, {"type": "remote_reopen", "items": [iid]})
            result = (ack or {}).get("results") or [
                {"id": iid, "ok": bool((ack or {}).get("ok")),
                 "error": (ack or {}).get("error")}
            ]
        except Exception as exc:
            result = [{"id": iid, "ok": False, "error": str(exc)}]
        def done(_dt):
            try:
                self._admin_worker_reopen_done(pos, result)
            finally:
                self._wa_r3_admin_worker_busy = _wa_r3_pending(self, pos)
        _wa_r3_Clock.schedule_once(done, 0)
    _wa_r3_threading.Thread(
        target=worker, name="r3-admin-worker-open", daemon=True
    ).start()

_wa_r3_original_send = __WA_R3_TARGET_CLASS__._send_remote_message_with_ack
_wa_r3_original_reopen = __WA_R3_TARGET_CLASS__.on_reopen_door
_wa_r3_original_admin_worker = __WA_R3_TARGET_CLASS__.admin_worker_reopen_door

__WA_R3_TARGET_CLASS__._send_remote_message_with_ack = _wa_r3_send_with_single_flight
__WA_R3_TARGET_CLASS__.on_reopen_door = _wa_r3_reopen_once_async
__WA_R3_TARGET_CLASS__.admin_worker_reopen_door = _wa_r3_admin_worker_reopen_once_async
# WA_R3_SINGLE_FLIGHT_V1_END
"""

def validate(src):
    if MARK_START in src and MARK_END in src:
        return "installed"
    if MARK_START in src or MARK_END in src:
        raise ValueError("Incomplete previous patch marker")
    tree = ast.parse(src)
    target = None
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            defs = {m.name for m in node.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))}
            if {"_send_remote_message_with_ack", "on_reopen_door", "admin_worker_reopen_door"} <= defs:
                target = node.name
                break
    if not target:
        raise ValueError("Panel.py has no matching single class defining required 3 methods")
    if not re.search(r"(?m)^if __name__\s*==\s*[\"']__main__[\"']\s*:", src):
        raise ValueError("Cannot find application entry point; inspect source manually")
    # We guard against accidentally patching an old, wrong kiosk file.
    for token in ('remote_reopen', 'remote_purchase', 'REMOTE_PANEL_COMMAND_PORT'):
        if token not in src:
            raise ValueError(f"Missing required live-controller marker: {token}")
    return target

def build(src, target):
    code = BLOCK.replace("__WA_R3_TARGET_CLASS__", target).strip("\n") + "\n\n"
    points = list(re.finditer(r"(?m)^if __name__\s*==\s*[\"']__main__[\"']\s*:", src))
    # Insert before last app entry, leaving all earlier definitions intact.
    i = points[-1].start()
    output = src[:i] + code + src[i:]
    compile(output, str(TARGET), "exec")
    return output

def main():
    parser = argparse.ArgumentParser()
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true")
    g.add_argument("--apply", action="store_true")
    g.add_argument("--status", action="store_true")
    args = parser.parse_args()
    if not TARGET.is_file():
        raise SystemExit(f"STOP: Missing {TARGET}")
    raw = TARGET.read_text(encoding="utf-8")
    try:
        target = validate(raw)
    except Exception as ex:
        raise SystemExit(f"STOP: {ex}") from ex
    if target == "installed":
        print("INSTALLED: R3 open single-flight guard already present")
        return
    print("PASS: Target app class:", target)
    print("PASS: Both customer and admin open hooks found")
    print("PASS: FURS, POS and ESP code are not touched")
    output = build(raw, target)
    print("PASS: Generated Panel.py compiles")
    if args.check or args.status:
        print("Changes needed: yes; no files modified")
        return
    backup = TARGET.parent / "wa_ui_patch_backups" / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup.mkdir(parents=True, exist_ok=False)
    shutil.copy2(TARGET, backup / "Panel.py")
    fd, name = tempfile.mkstemp(prefix=".Panel_r3_guard_", dir=str(TARGET.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(output)
            f.flush()
            os.fsync(f.fileno())
        shutil.copymode(TARGET, name)
        py_compile.compile(name, doraise=True)
        os.replace(name, TARGET)
    except Exception:
        if os.path.exists(name):
            os.unlink(name)
        print("STOP: Apply failed; original Panel.py remains unchanged")
        raise
    print("SUCCESS: R3 single-flight guard installed")
    print("SUCCESS: Customer/admin worker reopen no longer blocks Kivy UI")
    print("BACKUP:", backup / "Panel.py")
    print("UNCHANGED: POS, FURS, fiscal logs, invoice counter, ESP firmware")

if __name__ == "__main__":
    main()
