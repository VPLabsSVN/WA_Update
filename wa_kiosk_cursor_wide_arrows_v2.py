#!/usr/bin/env python3
"""Guarded V2 UI-only change for the active WA Kivy kiosk.

Usage:
  python wa_kiosk_cursor_wide_arrows_v2.py --check
  python wa_kiosk_cursor_wide_arrows_v2.py --apply

Changes only Panel.py: hide Kivy cursor and give six navigation arrow
buttons equal (1/3) horizontal layout shares. POS/FURS state untouched.
"""
import argparse
import ast
from datetime import datetime
from pathlib import Path
import os
import re
import shutil

ROOT = Path('/home/wa_stopshop_mb/WA/Main_program/Fis')
PANEL = ROOT / 'Panel.py'
STATE = ROOT / 'bp_sent.txt'
IDS = (
    'selection_left_btn', 'selection_right_btn',
    'check_price_left_btn', 'check_price_right_btn',
    'admin_left_btn', 'admin_right_btn',
)


def patch(source):
    lines = source.splitlines(keepends=True)
    deletions = set()
    edits = 0
    for button_id in IDS:
        indexes = [i for i, line in enumerate(lines)
                   if line.strip() == f'id: {button_id}']
        if len(indexes) != 1:
            raise ValueError(f'Expected exactly one KV button {button_id}; found {len(indexes)}')
        idx = indexes[0]
        if idx == 0 or lines[idx - 1].strip() not in ('NavArrowButton:', 'HoverButton:'):
            raise ValueError(f'Unexpected KV widget for {button_id}; no changes applied')
        # Only examine fields for this named arrow, stopping at its on_release.
        end = next((j for j in range(idx + 1, min(idx + 10, len(lines)))
                    if lines[j].lstrip().startswith('on_release:')), None)
        if end is None:
            raise ValueError(f'Missing navigation action for {button_id}')
        hint = [j for j in range(idx + 1, end)
                if lines[j].strip().startswith('size_hint_x:')]
        if len(hint) != 1:
            raise ValueError(f'Unexpected size_hint_x for {button_id}')
        h = hint[0]
        val = lines[h].strip().split(':', 1)[1].strip()
        if val == 'None':
            lines[h] = re.sub(r'(?<=size_hint_x:)\s*None', ' 1', lines[h], count=1)
            edits += 1
        elif val != '1':
            raise ValueError(f'Unexpected width hint for {button_id}: {val}')
        for j in range(idx + 1, end):
            if lines[j].strip().startswith('width:'):
                if not re.fullmatch(r'width:\s*dp\(\d+(?:\.\d+)?\)', lines[j].strip()):
                    raise ValueError(f'Unexpected fixed width for {button_id}: {lines[j].strip()}')
                deletions.add(j)
                edits += 1
    lines = [line for i, line in enumerate(lines) if i not in deletions]
    result = ''.join(lines)

    # Kivy graphics option must be set before the Window provider initializes.
    config_line = "Config.set('graphics', 'show_cursor', '0')"
    if config_line not in result:
        anchor = 'from kivy.config import Config\n'
        if result.count(anchor) != 1:
            raise ValueError('Could not find unique Kivy Config import')
        result = result.replace(anchor, anchor + config_line + '\n', 1)
        edits += 1

    # Runtime property guards against desktop / compositor cursor defaults.
    runtime_line = 'Window.show_cursor = False'
    if runtime_line not in result:
        anchor = 'from kivy.core.window import Window\n'
        if result.count(anchor) != 1:
            raise ValueError('Could not find unique Window import')
        result = result.replace(anchor, anchor + runtime_line + '\n', 1)
        edits += 1

    # The refresh helper also forces widths to 110dp: remove that override.
    tree = ast.parse(result)
    methods = [n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
               and n.name == '_refresh_panel_nav_buttons']
    if len(methods) != 1:
        raise ValueError('Expected one _refresh_panel_nav_buttons method')
    method = methods[0]
    rows = result.splitlines(keepends=True)
    scope = range(method.lineno - 1, method.end_lineno)
    for side in ('left_btn', 'right_btn'):
        current = [i for i in scope if rows[i].strip() == f'{side}.width = nav_w']
        already = [i for i in scope if rows[i].strip() == f'{side}.size_hint_x = 1']
        if len(current) == 1:
            i = current[0]
            rows[i] = rows[i].replace(f'{side}.width = nav_w', f'{side}.size_hint_x = 1')
            edits += 1
        elif len(already) != 1:
            raise ValueError(f'Unexpected refresh layout for {side}')
    result = ''.join(rows)

    if not re.search(r'(?m)^PAYMENT_ENABLED\s*=\s*True\b', result):
        raise ValueError('Unexpected POS configuration; wrong Panel.py?')
    compile(result, str(PANEL), 'exec')
    return result, edits


def main():
    ap = argparse.ArgumentParser()
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument('--check', action='store_true')
    group.add_argument('--apply', action='store_true')
    args = ap.parse_args()
    if not PANEL.is_file() or not STATE.is_file():
        raise SystemExit('STOP: expected active Fis files are missing')
    state = STATE.read_text(encoding='utf-8')
    if not re.search(r'(?m)^bp_sent=1$', state) or not re.search(r'(?m)^allowed=1$', state):
        raise SystemExit('STOP: not the expected enabled Jagodic installation')
    original = PANEL.read_text(encoding='utf-8')
    try:
        updated, count = patch(original)
    except (ValueError, SyntaxError) as exc:
        raise SystemExit(f'STOP: {exc}')
    if args.check:
        print('PASS: Six arrow buttons found; existing navigation actions preserved')
        print('PASS: Cursor settings and 1/3 row layout validated (dp(100), dp(110), or other numeric dp widths accepted)')
        print('PASS: Panel.py Python syntax valid')
        print(f'Changes needed: {count}')
        print('No file modified, no POS/FURS action')
        return
    if updated == original:
        print('ALREADY APPLIED: No changes needed')
        return
    backup_dir = ROOT / 'wa_ui_patch_backups' / datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    backup_dir.mkdir(parents=True)
    backup = backup_dir / 'Panel.py'
    shutil.copy2(PANEL, backup)
    tmp = PANEL.with_name('Panel.py.ui_patch_tmp')
    try:
        shutil.copy2(PANEL, tmp)
        tmp.write_text(updated, encoding='utf-8')
        compile(tmp.read_text(encoding='utf-8'), str(tmp), 'exec')
        os.replace(tmp, PANEL)
    finally:
        tmp.unlink(missing_ok=True)
    print('SUCCESS: Cursor hidden inside the fullscreen Kivy kiosk')
    print('SUCCESS: Six arrow buttons now each occupy 1/3 of their navigation row')
    print('UNCHANGED: Button actions, POS, FURS, BP registration, invoice logs and counter')
    print(f'Backup: {backup}')


if __name__ == '__main__':
    main()
