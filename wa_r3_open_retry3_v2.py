#!/usr/bin/env python3
"""Add bounded, pre-unlock R3 positioning retries to installed single-flight V1.

Only when the ESP returns the FINAL typed ACK for a *single* R3 revolver
item with an exact pre-pulse 'revolver positioning failed' error, retry the
same request up to TWO more times. Each retry requires a healthy read-only
relay/motor readiness ACK and a cooldown. No retries after lock pulse,
ambiguous ACK, door-cycle or driver faults, or for multi-item batches.

Usage: --check (read only), --apply, --status
Does not change firmware, POS, FURS, invoice numbers, logs, or other files.
"""
from __future__ import annotations
import argparse
import ast
from datetime import datetime
from pathlib import Path
import os
import re
import shutil
import tempfile

HERE = Path(__file__).resolve().parent
PANEL = HERE / 'Panel.py'
START = '# WA_R3_SINGLE_FLIGHT_V1_BEGIN'
END = '# WA_R3_SINGLE_FLIGHT_V1_END'
V2_MARK = '# WA_R3_POSITION_RETRY3_V2'
FUNC_HEADER = 'def _wa_r3_send_with_single_flight(self, position, payload, timeout_s=None):'
FUNC_END = '\ndef _wa_r3_set_reopen_buttons(self, disabled):'

REPLACEMENT = '''def _wa_r3_send_with_single_flight(self, position, payload, timeout_s=None):
    # WA_R3_POSITION_RETRY3_V2
    # Three TOTAL attempts, never three resends. Exact FINAL pre-lock-pulse
    # positioning failure only. All other responses retain V1 behavior.
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
            return {"ok": False, "suppressed": True,
                    "error": "r3_open_already_in_progress_no_command_sent"}
        _wa_r3_inflight[key] = {"action": action, "uncertain": False}

    try:
        import time as _wa_r3_retry_time
        items = (payload or {}).get("items") or []
        minimum = 300.0 if len(items) <= 1 else min(1200.0, 300.0 * len(items))
        requested = float(timeout_s) if timeout_s is not None else 0.0
        deadline_seconds = max(minimum, requested)
        expected = _wa_r3_ack_names[action]
        retry_id = None
        if len(items) == 1:
            try:
                candidate = items[0]
                retry_id = int(candidate.get("id") if isinstance(candidate, dict) else candidate)
                if retry_id < 5 or retry_id > 21:
                    retry_id = None
            except (TypeError, ValueError, AttributeError):
                retry_id = None

        def _call(command, seconds):
            try:
                return _wa_r3_original_send(self, pos, command, timeout_s=seconds)
            except Exception as exc:
                return {"ok": False, "error": type(exc).__name__ + ": " + str(exc)}

        def _is_retryable_position_failure(reply):
            if retry_id is None or not isinstance(reply, dict):
                return False
            if reply.get("type") != expected or reply.get("warning") or reply.get("error"):
                return False
            results = reply.get("results")
            if not isinstance(results, list) or len(results) != 1:
                return False
            result = results[0]
            if not isinstance(result, dict) or result.get("ok") is not False:
                return False
            try:
                if int(result.get("id", -1)) != retry_id:
                    return False
            except (ValueError, TypeError):
                return False
            # These are emitted by the ESP *before* the lock pulse.
            return str(result.get("error", "")).strip() in (
                "revolver positioning failed",
                "revolver_positioning_failed",
            )

        response = None
        attempts = 0
        for attempt in range(1, 4):
            if attempt > 1:
                # A complete typed ACK of failure must precede the retry.
                # We NEVER resend after timeout or unknown completion.
                _wa_r3_retry_time.sleep(1.0)
                status = _call({"type": "remote_relay_status", "items": [retry_id]}, 8.0)
                if not (isinstance(status, dict)
                        and status.get("type") == "remote_relay_status_ack"
                        and status.get("ok") is True
                        and status.get("relay_ok") is True
                        and not status.get("error")):
                    print("[R3 RETRY3] stopped: read-only controller readiness check refused;",
                          "panel=", pos, "item=", retry_id,
                          "reason=", status.get("error") if isinstance(status, dict) else "invalid reply")
                    break
                print("[R3 RETRY3] controller readiness OK; retrying positioning",
                      "panel=", pos, "item=", retry_id, "attempt=", attempt, "/3")
            attempts = attempt
            response = _call(payload, deadline_seconds)
            if not _is_retryable_position_failure(response):
                break
            if attempt < 3:
                print("[R3 RETRY3] ESP confirmed positioning failure BEFORE lock pulse;",
                      "panel=", pos, "item=", retry_id, "attempt=", attempt, "/3")
            else:
                print("[R3 RETRY3] three attempts exhausted; no automatic recovery")

        is_matching_ack = isinstance(response, dict) and response.get("type") == expected
        has_final_results = is_matching_ack and isinstance(response.get("results"), list)
        rejected_before_execution = (
            is_matching_ack
            and isinstance(response.get("error"), str)
            and bool(response.get("error"))
            and response["error"] != "previous_order_in_progress_wait_for_final_ack"
        )
        is_terminal = has_final_results or rejected_before_execution
        with _wa_r3_mutex:
            if is_terminal:
                _wa_r3_inflight.pop(key, None)
            elif key in _wa_r3_inflight:
                _wa_r3_inflight[key]["uncertain"] = True

        print("[R3 SINGLE FLIGHT]", action, "panel", pos,
              "terminal_ack=", is_terminal, "attempts=", attempts,
              "ok=", bool(response.get("ok")) if isinstance(response, dict) else False)
        if not is_terminal:
            print("[R3 SINGLE FLIGHT] NO FINAL ACK: new R3 opens remain blocked;"
                  " inspect ESP status before restarting kiosk")
        if isinstance(response, dict):
            response["pi_open_attempts"] = attempts
        return response
    except Exception as exc:
        # Never release the guard after an unexpected exception: the UDP
        # command might have reached the controller with an unknown outcome.
        print("[R3 SINGLE FLIGHT] unexpected wrapper error:", repr(exc),
              "R3 operations remain blocked")
        with _wa_r3_mutex:
            if key in _wa_r3_inflight:
                _wa_r3_inflight[key]["uncertain"] = True
        return {"ok": False, "error": "r3_open_outcome_uncertain", "details": str(exc)}
'''


def transform(source: str) -> tuple[str, str]:
    if source.count(START) != 1 or source.count(END) != 1:
        raise ValueError('Expected exactly one complete installed V1 single-flight block')
    begin, end = source.index(START), source.index(END)
    block = source[begin:end]
    if V2_MARK in block:
        return source, 'already_installed'
    if block.count(FUNC_HEADER) != 1 or block.count(FUNC_END) != 1:
        raise ValueError('Cannot uniquely find V1 send wrapper; source may differ')
    head = source.index(FUNC_HEADER, begin, end)
    tail = source.index(FUNC_END, head, end)
    old = source[head:tail]
    if ('[R3 SINGLE FLIGHT] ignored duplicate' not in old
            or '_wa_r3_original_send' not in old
            or 'rejected_before_execution' not in old):
        raise ValueError('V1 single-flight implementation differs; no automatic edit')
    result = source[:head] + REPLACEMENT + source[tail:]
    ast.parse(result, filename=str(PANEL))
    compile(result, str(PANEL), 'exec')
    return result, 'ready'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument('--check', action='store_true')
    g.add_argument('--apply', action='store_true')
    g.add_argument('--status', action='store_true')
    args = parser.parse_args()
    if not PANEL.is_file():
        raise SystemExit(f'STOP: Cannot find {PANEL}')
    try:
        old = PANEL.read_text(encoding='utf-8')
        new, state = transform(old)
    except Exception as exc:
        raise SystemExit(f'STOP: {exc}') from exc
    if state == 'already_installed':
        print('ALREADY INSTALLED: R3 three-attempt positioning retry guard')
        return
    print('PASS: V1 single-flight guard located')
    print('PASS: Only confirmed pre-pulse positioning failures are retryable')
    print('PASS: At most 3 TOTAL attempts for single R3 item IDs 5..21')
    print('PASS: Requires read-only readiness ACK before each retry')
    print('PASS: No retry after timeout, status-declared fault, open/pulsed door, or for multi-item batches and fixed locks')
    print('PASS: Patched Panel.py parses and compiles')
    if args.check or args.status:
        print('No files modified')
        return
    backup_dir = PANEL.parent / 'wa_ui_patch_backups' / datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup = backup_dir / 'Panel.py'
    shutil.copy2(PANEL, backup)
    fd, tmp = tempfile.mkstemp(prefix='.Panel_r3_retry3_', suffix='.py', dir=str(PANEL.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(new)
            f.flush()
            os.fsync(f.fileno())
        shutil.copymode(PANEL, tmp)
        os.replace(tmp, PANEL)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    print('SUCCESS: R3 up-to-3 positioning attempts enabled')
    print('BACKUP:', backup)
    print('UNCHANGED: ESP32 firmware, POS, FURS, counters, invoice logs, normal locks')


if __name__ == '__main__':
    main()
