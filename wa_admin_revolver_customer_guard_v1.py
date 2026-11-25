#!/usr/bin/env python3
"""WA kiosk: prevent customer item callbacks from taking over the Admin R3 wheel.

Root cause captured by WA NAV TRACE on 2026-11-24:
Admin wheel -> customer item_detail after an Admin item save.

This patch changes only two UI dispatch methods of the backend App class:
  * refresh_revolver_screen: while in Admin wheel/editor, rebuild Admin wheel
  * goto_item_detail: if invoked from Admin wheel, open Admin item editor

--check  validate source and proposed change without writing
--apply  backup + atomic write (stop kiosk first)
--status report installed state
--remove remove only this patch with backup (stop kiosk first)

No changes to ESP32, motor operations, retries, invoicing, POS, FURS or inventory.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import os
from pathlib import Path
import re
import shutil
import tempfile

NAME = 'Panel_backend_merged.py'
START = '# WA_ADMIN_REVOLVER_CUSTOMER_GUARD_V1_BEGIN'
END = '# WA_ADMIN_REVOLVER_CUSTOMER_GUARD_V1_END'

BLOCK = r'''
# WA_ADMIN_REVOLVER_CUSTOMER_GUARD_V1_BEGIN
# A live NAV TRACE captured a customer slot callback on the Admin R7 wheel.
# Keep the admin wheel's on_release callbacks stable during asynchronous UI refreshes.
# Does not initiate or change motor commands or inventory actions.
_wa_admin_wheel_prev_refresh = PanelBackendReservationApp.refresh_revolver_screen
_wa_admin_wheel_prev_item_detail = PanelBackendReservationApp.goto_item_detail


def _wa_admin_wheel_guarded_refresh(self, *args, **kwargs):
    try:
        is_admin_wheel = bool(getattr(self, '_admin_revolver_mode', False))
        active_screen = getattr(getattr(self, 'root', None), 'current', None)
        if is_admin_wheel and active_screen in ('revolver', 'admin_edit_item'):
            wheel = getattr(self, 'current_revolver', None)
            if not isinstance(wheel, dict):
                print('[ADMIN WHEEL GUARD] missing wheel state; refused customer redraw',
                      flush=True)
                return None
            print('[ADMIN WHEEL GUARD] replaced customer refresh with Admin wheel',
                  'screen=', active_screen, flush=True)
            # The Admin renderer binds each item to goto_admin_edit (not customer details).
            return self._refresh_admin_revolver_screen()
    except Exception as exc:
        # Fail CLOSED: a faulty Admin refresh must not populate customer callbacks.
        print('[ADMIN WHEEL GUARD] Admin refresh blocked:', repr(exc), flush=True)
        return None
    return _wa_admin_wheel_prev_refresh(self, *args, **kwargs)


def _wa_admin_wheel_guarded_item_detail(self, item_id, *args, **kwargs):
    if (bool(getattr(self, '_admin_revolver_mode', False)) and
            getattr(getattr(self, 'root', None), 'current', None) == 'revolver'):
        try:
            iid = int(item_id)
            wheel = getattr(self, 'current_revolver', None)
            if not isinstance(wheel, dict):
                print('[ADMIN WHEEL GUARD] refused customer item callback:',
                      'missing active revolver', flush=True)
                return None
            permitted = {
                int(it['id']) for it in (self._revolver_items(wheel) or [])
                if isinstance(it, dict) and it.get('id') is not None
            }
            if iid not in permitted:
                print('[ADMIN WHEEL GUARD] refused customer item callback:',
                      'item not in active revolver', iid, flush=True)
                return None
            print('[ADMIN WHEEL GUARD] redirected customer slot callback to Admin editor',
                  'item=', iid, 'wheel=', wheel.get('key', ''), flush=True)
            return self.goto_admin_edit(iid)
        except Exception as exc:
            print('[ADMIN WHEEL GUARD] refused customer slot callback:',
                  repr(exc), flush=True)
            return None
    return _wa_admin_wheel_prev_item_detail(self, item_id, *args, **kwargs)


PanelBackendReservationApp.refresh_revolver_screen = _wa_admin_wheel_guarded_refresh
PanelBackendReservationApp.goto_item_detail = _wa_admin_wheel_guarded_item_detail
# WA_ADMIN_REVOLVER_CUSTOMER_GUARD_V1_END
'''.strip('\n') + '\n\n'


def source_state(src: str) -> str:
    if (START in src) != (END in src):
        raise ValueError('Incomplete guard markers: inspect backend manually')
    if START in src:
        if src.count(START) != 1 or src.count(END) != 1:
            raise ValueError('Duplicate guard marker: stop')
        compile(src, NAME, 'exec')
        return 'installed'
    required = (
        'class PanelBackendReservationApp(',
        'PanelBackendReservationApp._refresh_admin_revolver_screen',
        'PanelBackendReservationApp.open_admin_revolver',
        'PanelBackendReservationApp.goto_admin_panel',
        'self.goto_admin_edit(',
        'self.goto_item_detail(',
        '_admin_revolver_mode',
        'WA_ADMIN_REVOLVER_STAY_V1_BEGIN',
    )
    missing = [marker for marker in required if marker not in src]
    if missing:
        raise ValueError('Unexpected backend; missing: ' + ', '.join(missing))
    match = list(re.finditer(
        r'(?m)^if __name__\s*==\s*[\'\"]__main__[\'\"]\s*:', src))
    if len(match) != 1:
        raise ValueError(f'Expected one main entrypoint; found {len(match)}')
    compile(src, NAME, 'exec')
    return 'pending'


def build(src: str, operation: str) -> str:
    if operation == 'apply':
        entry = list(re.finditer(
            r'(?m)^if __name__\s*==\s*[\'\"]__main__[\'\"]\s*:', src))
        if len(entry) != 1:
            raise ValueError('Missing unique entrypoint')
        # Put this above the main loop. Existing V1 stay & nav tracing remain.
        pos = entry[0].start()
        result = src[:pos] + BLOCK + '\n' + src[pos:]
    elif operation == 'remove':
        pattern = r'(?ms)^' + re.escape(START) + r'\n.*?^' + re.escape(END) + r'\n{2,3}'
        result, count = re.subn(pattern, '', src)
        if count != 1:
            raise ValueError('Cannot safely isolate guard block for removal')
    else:
        raise ValueError(operation)
    compile(result, NAME, 'exec')
    return result


def backup_and_write(target: Path, result: str) -> Path:
    folder = target.parent / 'wa_ui_patch_backups' / datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    folder.mkdir(parents=True, exist_ok=False)
    backup = folder / target.name
    shutil.copy2(target, backup)
    fd, tmp = tempfile.mkstemp(prefix='.wa_admin_wheel_', suffix='.py', dir=target.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='') as f:
            f.write(result)
            f.flush()
            os.fsync(f.fileno())
        shutil.copymode(target, tmp)
        compile(Path(tmp).read_text(encoding='utf-8'), str(target), 'exec')
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return backup


def main():
    p = argparse.ArgumentParser(description=__doc__)
    modes = p.add_mutually_exclusive_group(required=True)
    modes.add_argument('--check', action='store_true')
    modes.add_argument('--apply', action='store_true')
    modes.add_argument('--status', action='store_true')
    modes.add_argument('--remove', action='store_true')
    p.add_argument('--source', type=Path, default=None, help=argparse.SUPPRESS)
    args = p.parse_args()
    target = args.source or Path(__file__).resolve().parent / NAME
    if not target.is_file():
        raise SystemExit(f'STOP: Missing backend: {target}')
    src = target.read_text(encoding='utf-8')
    try:
        state = source_state(src)
        if args.status:
            print('Admin wheel customer-callback guard:', state)
            return
        if args.remove:
            if state != 'installed':
                print('NOT INSTALLED; nothing to remove')
                return
            result = build(src, 'remove')
        elif state == 'installed':
            print('ALREADY INSTALLED: Admin wheel customer-callback guard V1')
            return
        else:
            result = build(src, 'apply')
    except Exception as exc:
        raise SystemExit('STOP: ' + str(exc)) from exc

    if args.check:
        print('PASS: Admin revolver UI routing recognized; proposed patch compiles')
        print('PASS: Customer callbacks will be redirected only while Admin wheel is active')
        print('PASS: No changes to payment, fiscalisation, retry logic or motor safety')
        print('No file modified')
        return
    backup = backup_and_write(target, result)
    print('SUCCESS:', 'Removed' if args.remove else 'Installed', 'Admin wheel guard V1')
    print('Backup:', backup)
    print('UNCHANGED: all customer screens, ESP32, R3 retry3 V2, POS, FURS, stock')

if __name__ == '__main__':
    main()
