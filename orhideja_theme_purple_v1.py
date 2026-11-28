#!/usr/bin/env python3
"""Orhideja: audited purple UI palette change in *staged* Panel.py only.

No networking; no service start; no POS, fiscal, or controller changes.
Usage: sudo python3 orhideja_theme_purple_v1.py --inspect | --apply | --verify
       sudo python3 orhideja_theme_purple_v1.py --rollback /path/to/backup/Panel.py
"""
from __future__ import annotations
import argparse
import ast
from collections import Counter
from datetime import datetime
import hashlib
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile

DEFAULT_STAGE = Path('/var/lib/wa-jagodic-clone/stage-001')
RELPANEL = Path('home/wa_stopshop_mb/WA/Main_program/Fis/Panel.py')
RELBACKEND = Path('home/wa_stopshop_mb/WA/Main_program/Fis/Panel_backend_merged.py')

# These are only the historical *neutral UI accent* values seen in the kiosk
# source. Each mapping changes RGB, not the alpha channel. Semantic colours
# for payment-success, reservations, errors, availability and warning states
# have deliberately NOT been included.
RGB_PALETTE = (
    ('main surface / button', (0.20, 0.33, 0.23), (0.36, 0.20, 0.51)),
    ('hover state',           (0.24, 0.39, 0.27), (0.45, 0.25, 0.63)),
    ('pressed state',         (0.17, 0.29, 0.20), (0.28, 0.14, 0.40)),
    ('disabled UI surface',   (0.38, 0.45, 0.39), (0.43, 0.36, 0.48)),
    ('main highlight',       (0.63, 0.82, 0.66), (0.78, 0.62, 0.91)),
    ('light highlight',      (0.76, 0.96, 0.78), (0.88, 0.76, 0.98)),
    ('background veil',      (0.04, 0.08, 0.05), (0.09, 0.04, 0.13)),
    ('top band',             (0.18, 0.30, 0.20), (0.31, 0.16, 0.45)),
)
# Restrict replacements to styling/colour assignment lines; never broad text.
STYLE_LINE = re.compile(
    r'\b(?:base_normal|base_hover|base_down|bg_rgba|fill_rgba|'
    r'disabled_rgba|border_rgba|number_color_rgba|color|rgba)\s*[:=]'
)

class Stop(Exception):
    pass

def need(ok: bool, message: str) -> None:
    if not ok:
        raise Stop(message)

def literal_rgb_pattern(v: tuple[float,float,float]) -> re.Pattern:
    nums = [f'{n:.2f}' for n in v]
    # Avoid partial replacement within other numeric literals.
    return re.compile(r'(?<![\d.])' + r'\s*,\s*'.join(re.escape(t) for t in nums) + r'(?![\d.])')

OLD_PATTERNS = [(name, literal_rgb_pattern(old), ', '.join(f'{x:.2f}' for x in new))
                for name, old, new in RGB_PALETTE]
NEW_PATTERNS = [(name, literal_rgb_pattern(new)) for name, old, new in RGB_PALETTE]

def transform(text: str):
    counts = Counter()
    lines = text.splitlines(keepends=True)
    edited = []
    locations = []
    for lineno, line in enumerate(lines, 1):
        if not STYLE_LINE.search(line):
            edited.append(line)
            continue
        output = line
        for name, pattern, replacement in OLD_PATTERNS:
            output, n = pattern.subn(replacement, output)
            if n:
                counts[name] += n
                locations.append((lineno, name, n))
        edited.append(output)
    result = ''.join(edited)
    ast.parse(result)
    return result, counts, locations

def purple_counts(text: str) -> Counter:
    counts = Counter()
    for line in text.splitlines():
        if not STYLE_LINE.search(line):
            continue
        for name, pattern in NEW_PATTERNS:
            counts[name] += len(pattern.findall(line))
    return counts

def source_static(source: str, name: str):
    matches = []
    for n in ast.parse(source).body:
        if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
            if n.targets[0].id == name:
                if isinstance(n.value, ast.Constant):
                    matches.append(n.value.value)
                else:
                    matches.append('<dynamic>')
    return matches

def validate(stage: Path, writable: bool):
    need(stage.is_dir(), f'Staging directory missing: {stage}')
    root = stage.resolve()
    if writable:
        need(os.geteuid() == 0, 'Use sudo for --apply / --rollback')
        need(str(root).startswith('/var/lib/wa-jagodic-clone/stage-'),
             'Writes allowed ONLY inside /var/lib/wa-jagodic-clone/stage-*')
        need(not Path('/etc/wa-third-production-ready').exists(),
             'STOP: commissioning authorization file exists')
        need((stage / 'clone_metadata/source.json').is_file(),
             'Missing clone metadata; not a valid staged clone')
    panel = stage / RELPANEL
    backend = stage / RELBACKEND
    need(panel.is_file() and backend.is_file(), 'Staged Panel.py / Panel_backend_merged.py missing')
    need(not panel.is_symlink() and panel.resolve().is_relative_to(root),
         'Panel.py is a symlink or outside the staged clone')
    need(not backend.is_symlink() and backend.resolve().is_relative_to(root),
         'Backend is a symlink or outside the staged clone')
    ps = panel.read_text(encoding='utf-8')
    bs = backend.read_text(encoding='utf-8')
    for label, found, required in (
        ('PANEL_TYPE', source_static(ps,'PANEL_TYPE'), 'A'),
        ('PAYMENT_ENABLED', source_static(ps,'PAYMENT_ENABLED'), False),
        ('BACKEND_PANEL_ID_OVERRIDE', source_static(bs,'BACKEND_PANEL_ID_OVERRIDE'),'Orhideja'),
        ('BACKEND_PANEL_NAME_OVERRIDE', source_static(bs,'BACKEND_PANEL_NAME_OVERRIDE'),'Videm pri Ptuju'),
    ):
        need(found == [required], f'{label} should be {required!r}, got {found!r}')
    bp = stage / RELPANEL.parent / 'bp_sent.txt'
    need(bp.is_file() and not bp.is_symlink(), 'Staged bp_sent.txt missing or symlink')
    bps = bp.read_text(encoding='utf-8')
    need(re.search(r'(?m)^\s*bp_sent\s*=\s*0\s*$',bps) is not None and
         re.search(r'(?m)^\s*allowed\s*=\s*0\s*$',bps) is not None,
         'Expected staged Slovenian FURS gate bp_sent=0 and allowed=0')
    return panel, ps

def digest(data: str) -> str:
    return hashlib.sha256(data.encode('utf-8')).hexdigest()

def write_atomic(path: Path, source: str):
    fd, filename = tempfile.mkstemp(prefix='.orhideja-theme-',dir=str(path.parent))
    tmp = Path(filename)
    try:
        with os.fdopen(fd,'w',encoding='utf-8',newline='') as f:
            f.write(source)
            f.flush()
            os.fsync(f.fileno())
        shutil.copystat(path,tmp)
        os.chown(tmp, path.stat().st_uid, path.stat().st_gid)
        os.replace(tmp,path)
    finally:
        if tmp.exists():
            tmp.unlink()

def main():
    p = argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    action = p.add_mutually_exclusive_group(required=True)
    action.add_argument('--inspect',action='store_true')
    action.add_argument('--apply',action='store_true')
    action.add_argument('--verify',action='store_true')
    action.add_argument('--rollback',metavar='BACKUP_PANEL_PY')
    p.add_argument('--stage',default=str(DEFAULT_STAGE),help='staged root; writes must remain under /var/lib/wa-jagodic-clone/stage-*')
    args = p.parse_args()
    stage = Path(args.stage)
    panel, original = validate(stage, args.apply or bool(args.rollback))

    if args.rollback:
        backup = Path(args.rollback)
        root = (stage / 'clone_metadata').resolve()
        need(backup.is_file() and not backup.is_symlink() and backup.name == 'Panel.py' and
             backup.resolve().is_relative_to(root), 'Backup must be staged clone_metadata/.../Panel.py')
        previous = backup.read_text(encoding='utf-8')
        ast.parse(previous)
        need(source_static(previous,'PAYMENT_ENABLED') == [False] and
             source_static(previous,'PANEL_TYPE') == ['A'],
             'Backup has unsafe panel/payment flags')
        need(source_static(original,'PAYMENT_ENABLED') == [False], 'Current payment gate unexpectedly changed')
        write_atomic(panel,previous)
        print('ROLLED BACK STAGED Panel.py palette from:',backup)
        print('No services changed or started')
        return

    result,counts,locations = transform(original)
    existing = purple_counts(original)
    print('TARGET: staged Orhideja Panel.py only (not installed/live)')
    print('Current file SHA256:',digest(original))
    print('Recognized remaining neutral green/teal UI colours:',sum(counts.values()))
    for name,_,_ in RGB_PALETTE:
        if counts[name] or existing[name]:
            print(f'  {name:24s}: {counts[name]:3d} old / {existing[name]:3d} already purple')
    print('Preserved: success / shopping cart / warnings / errors / door statuses')
    if args.inspect:
        if locations:
            print('Matched lines (no code or secrets printed):')
            print(' ', ', '.join(str(x[0]) for x in locations[:70]))
        if not counts and not sum(existing.values()):
            print('STOP: No known palette patterns detected. Need a read-only colour audit of the real Panel.py')
        print('NO FILES CHANGED')
        return

    if args.verify:
        if counts:
            print('INCOMPLETE: %d old UI accent occurrences remain' % sum(counts.values()))
            raise Stop('Run --apply after reviewing --inspect')
        need(sum(existing.values()) >= 8, 'Unexpected number of purple UI accent values: review Panel.py')
        print('PASS: purple UI accents present; recognized green/teal UI accents absent')
        print('NO SERVICES STARTED')
        return

    if not counts:
        if sum(existing.values()) >= 8:
            print('ALREADY APPLIED: recognized palette is purple; no changes made')
            return
        raise Stop('No recognized green/teal colours found. Cannot safely apply blindly.')
    need(sum(counts.values()) >= 8, 'Too few matched UI colors; inspect actual staged palette before changing')
    need(digest(result) != digest(original), 'Unexpected identical output')
    # Keep all terms, logic, and transaction code unchanged by changing only palette RGB.
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    backup = stage / 'clone_metadata' / ('orhideja_purple_prepatch_' + stamp) / 'Panel.py'
    need(backup.resolve().is_relative_to(stage.resolve()), 'Backup path escaped staging')
    backup.parent.mkdir(parents=True,mode=0o700)
    shutil.copy2(panel,backup)
    try:
        write_atomic(panel,result)
        reread=panel.read_text(encoding='utf-8')
        need(reread==result,'Post-write comparison failed')
        _,remaining,_=transform(reread)
        need(sum(remaining.values())==0,'Old palette values still present after patch')
    except BaseException:
        write_atomic(panel,backup.read_text(encoding='utf-8'))
        raise
    print('PASS: purple palette written to STAGED Panel.py')
    print('Replacements:',sum(counts.values()))
    print('Backup:',backup)
    print('New file SHA256:',digest(result))
    print('No production files, services, POS, fiscalization or networking touched')

if __name__ == '__main__':
    try:
        main()
    except (Stop,OSError,SyntaxError) as exc:
        print('STOP:',exc,file=sys.stderr)
        sys.exit(2)
