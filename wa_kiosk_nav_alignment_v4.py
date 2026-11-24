#!/usr/bin/env python3
"""Jagodic WA kiosk UI V4: PWM/ESP back arrow + buyer arrow clearance.

Upload this script to VPLabsSVN/WA_Update/main. On the Pi:
    ./venv/bin/python wa_kiosk_nav_alignment_v4.py --check
    # Stop the kiosk only while idle, then:
    ./venv/bin/python wa_kiosk_nav_alignment_v4.py --apply

Edits ONLY /home/wa_stopshop_mb/WA/Main_program/Fis/Panel.py.
- Admin ESP/PWM status: 1/3-width left/back arrow, preserving its on_release.
- Buyer selection + check-price: raise side arrows 14dp, retaining their original 72dp or 76dp height.
- Preserve the middle spacer and bottom navigation geometry; no POS/FURS edits.

Guarded and idempotent. Refuses unexpected layout and creates a timestamped backup.
"""
import argparse
import ast
from datetime import datetime
import os
from pathlib import Path
import re
import shutil

ROOT = Path('/home/wa_stopshop_mb/WA/Main_program/Fis')
PANEL = ROOT / 'Panel.py'
STATE = ROOT / 'bp_sent.txt'
DELTA = 14


def leading_space(line):
    return len(line) - len(line.lstrip(' '))


def find_screen_bounds(lines, screen):
    marker = f'<{screen}>:'
    indices = [i for i, s in enumerate(lines) if s.strip() == marker and s.startswith('<')]
    if len(indices) != 1:
        raise ValueError(f'Expected one {marker} in Panel.py; found {len(indices)}')
    first = indices[0]
    last = next((i for i in range(first + 1, len(lines))
                 if re.match(r'^<[^>]+>:\s*$', lines[i].rstrip('\r\n'))), len(lines))
    return first, last


def find_id(lines, screen, button_id):
    lo, hi = find_screen_bounds(lines, screen)
    matches = [i for i in range(lo + 1, hi) if lines[i].strip() == f'id: {button_id}']
    if len(matches) != 1:
        raise ValueError(f'Expected one {button_id} in <{screen}>, found {len(matches)}')
    return matches[0], lo, hi


def get_button_start(lines, id_idx):
    start = id_idx - 1
    if start < 0 or lines[start].strip() != 'NavArrowButton:':
        raise ValueError(f'Unexpected button type above {lines[id_idx].strip()}')
    return start


def update_stats_button(lines):
    """ESP status has one arrow and one blank widget; add a second blank widget."""
    i, lo, hi = find_id(lines, 'AdminEspStatusScreen', 'admin_stats_left_btn')
    start = get_button_start(lines, i)
    indent = leading_space(lines[start])
    end = next((k for k in range(i + 1, min(i + 12, hi))
                if lines[k].strip().startswith('on_release:')), None)
    if end is None or lines[end].strip() != 'on_release: app.goto_admin_panel()':
        raise ValueError('PWM admin back action differs from expected; no changes applied')
    hints = [k for k in range(i + 1, end) if lines[k].strip().startswith('size_hint_x:')]
    if len(hints) != 1:
        raise ValueError('Unexpected PWM admin arrow size_hint_x')
    edits = 0
    k = hints[0]
    val = lines[k].strip().split(':', 1)[1].strip()
    if val == 'None':
        lines[k] = ' ' * leading_space(lines[k]) + 'size_hint_x: 1\n'
        edits += 1
    elif val != '1':
        raise ValueError(f'Unexpected PWM admin arrow width hint: {val}')
    widths = [k for k in range(i + 1, end) if lines[k].strip().startswith('width:')]
    if len(widths) > 1:
        raise ValueError('Multiple PWM admin arrow width settings')
    if widths:
        k = widths[0]
        if not re.fullmatch(r'width:\s*dp\(\d+(?:\.\d+)?\)', lines[k].strip()):
            raise ValueError('Unexpected PWM admin arrow fixed width')
        lines.pop(k)
        edits += 1
        # Recompute indices after deletion.
        i, lo, hi = find_id(lines, 'AdminEspStatusScreen', 'admin_stats_left_btn')
        start = get_button_start(lines, i)
        end = next(k for k in range(i + 1, min(i + 12, hi))
                   if lines[k].strip().startswith('on_release:'))

    # Inspect the sibling widgets in this exact horizontal row.
    # A row with [left-arrow, Widget, Widget] gives each 1/3 of the row width.
    row_start = next((k for k in range(start - 1, lo, -1)
                      if lines[k].strip() == 'BoxLayout:' and
                      leading_space(lines[k]) == indent - 4), None)
    if row_start is None:
        raise ValueError('PWM arrow navigation row not found')
    if not any(re.fullmatch(r'height:\s*dp\((?:72|76)\)', lines[k].strip())
               for k in range(row_start + 1, start)):
        raise ValueError('PWM arrow navigation row height not recognized; no change made')
    row_end = next((k for k in range(end + 1, hi)
                    if lines[k].strip() and leading_space(lines[k]) <= indent - 4), hi)
    siblings = [(k, lines[k].strip()) for k in range(start, row_end)
                if leading_space(lines[k]) == indent and lines[k].strip()]
    names = [name for _, name in siblings]
    if names == ['NavArrowButton:', 'Widget:']:
        # Insert an equal-size third element; existing Widget remains the middle third.
        lines.insert(row_end, ' ' * indent + 'Widget:\n\n')
        edits += 1
    elif names != ['NavArrowButton:', 'Widget:', 'Widget:']:
        raise ValueError(f'Unexpected PWM navigation row children: {names!r}')
    return edits


def raise_buyer_buttons(lines, screen, button_id):
    """Raise this navigation ROW's content 14dp without resizing the arrows.

    Installed builds have rows of 72dp and 76dp. The row grows by DELTA
    while DELTA of bottom padding moves its contents upward and preserves
    the original arrow height. The lower button row is not moved.
    """
    i, lo, hi = find_id(lines, screen, button_id)
    start = get_button_start(lines, i)
    indent = leading_space(lines[start])
    row_start = next((k for k in range(start - 1, lo, -1)
                      if lines[k].strip() == 'BoxLayout:' and
                      leading_space(lines[k]) == indent - 4), None)
    if row_start is None:
        raise ValueError(f'{screen}: buyer arrow row not found')
    segment = lines[row_start + 1:start]
    if not any(s.strip() == 'size_hint_y: None' for s in segment):
        raise ValueError(f'{screen}: expected fixed-height navigation row')
    heights = [k for k in range(row_start + 1, start)
               if lines[k].strip().startswith('height:')]
    if len(heights) != 1:
        raise ValueError(f'{screen}: unexpected navigation height declarations')
    height_idx = heights[0]
    height_text = lines[height_idx].strip()
    m = re.fullmatch(r'height:\s*dp\((\d+)\)', height_text)
    if not m:
        raise ValueError(f'{screen}: unexpected row height {height_text}')
    actual_height = int(m.group(1))
    padding = f'padding: [0, 0, 0, dp({DELTA})]'
    existing = [k for k in range(row_start + 1, start)
                if lines[k].strip().startswith('padding:')]
    if len(existing) > 1:
        raise ValueError(f'{screen}: multiple row padding settings')
    if existing:
        if lines[existing[0]].strip() != padding or actual_height not in (72+DELTA, 76+DELTA):
            raise ValueError(f'{screen}: preexisting padding/height mismatch')
        return 0  # Already patched; do not increase height twice.
    if actual_height not in (72, 76):
        raise ValueError(f'{screen}: unsupported unpatched row height {height_text}')
    lines[height_idx] = (' ' * leading_space(lines[height_idx]) +
                         f'height: dp({actual_height + DELTA})\n')
    # Align padding to the BoxLayout's other properties; insert after height.
    lines.insert(height_idx + 1,
                 ' ' * leading_space(lines[height_idx]) + padding + '\n')
    return 2


def patch(source):
    # All edits are in a temporary in-memory copy, so validation failures change nothing.
    if "Config.set('graphics', 'show_cursor', '0')" not in source:
        raise ValueError('V2 cursor patch is not present')
    if 'Window.show_cursor = False' not in source:
        raise ValueError('V2 runtime cursor patch is not present')
    if not re.search(r'(?m)^PAYMENT_ENABLED\s*=\s*True\b', source):
        raise ValueError('Unexpected POS config (not the enabled Jagodic panel)')
    lines = source.splitlines(keepends=True)
    edits = update_stats_button(lines)
    edits += raise_buyer_buttons(lines, 'SelectionScreen', 'selection_left_btn')
    edits += raise_buyer_buttons(lines, 'PriceCheckScreen', 'check_price_left_btn')
    result = ''.join(lines)
    ast.parse(result)
    compile(result, str(PANEL), 'exec')
    return result, edits


def main():
    parser = argparse.ArgumentParser(description='Guarded V4 WA touchscreen navigation patch')
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check', action='store_true', help='validate without editing')
    mode.add_argument('--apply', action='store_true', help='back up then modify Panel.py')
    args = parser.parse_args()
    if not PANEL.is_file() or not STATE.is_file():
        raise SystemExit('STOP: Active kiosk files not found')
    state = STATE.read_text(encoding='utf-8')
    if not re.search(r'(?m)^bp_sent=1$', state) or not re.search(r'(?m)^allowed=1$', state):
        raise SystemExit('STOP: FURS production state differs; no changes made')
    original = PANEL.read_text(encoding='utf-8')
    try:
        updated, changes = patch(original)
    except (ValueError, SyntaxError) as exc:
        raise SystemExit(f'STOP: {exc}')
    if args.check:
        print('PASS: PWM/ESP admin left arrow is correctly identified')
        print('PASS: Buyer selection and price-check arrows will be raised by 14dp')
        print('PASS: POS/FURS untouched; Panel.py Python syntax checked')
        print(f'Changes needed: {changes}')
        print('No files changed')
        return
    if updated == original:
        print('ALREADY APPLIED: Navigation adjustment present; no files changed')
        return
    backup_dir = ROOT / 'wa_ui_patch_backups' / datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup = backup_dir / 'Panel.py'
    shutil.copy2(PANEL, backup)
    temp = PANEL.with_name('Panel.py.ui_v4_tmp')
    try:
        shutil.copy2(PANEL, temp)
        temp.write_text(updated, encoding='utf-8')
        compile(temp.read_text(encoding='utf-8'), str(temp), 'exec')
        os.replace(temp, PANEL)
    finally:
        temp.unlink(missing_ok=True)
    print('SUCCESS: PWM/ESP admin back arrow now occupies 1/3 of its row')
    print('SUCCESS: Buyer selection and price-check arrows raised 14dp at original height')
    print('UNCHANGED: Cursor, POS, fiscalization, BP, counters, logs and navigation actions')
    print(f'Backup: {backup}')


if __name__ == '__main__':
    main()
