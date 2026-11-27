#!/usr/bin/env python3
"""Configure the STAGED Jagodic clone for Videm pri Ptuju; no services or networking.

Fail-closed edits for known literal settings only. Does not constitute production
commissioning: fiscal certificates, receipt data, invoice ledger, network and ESP
pairing must be reviewed separately before starting any service.
"""
from __future__ import annotations

import ast
from datetime import datetime
import os
from pathlib import Path
import re
import shutil
import sys

STAGE = Path('/var/lib/wa-jagodic-clone/stage-001')
APP = STAGE / 'home/wa_stopshop_mb/WA/Main_program/Fis'
PANEL = APP / 'Panel.py'
BACKEND = APP / 'Panel_backend_merged.py'
STATE = APP / 'bp_sent.txt'
FISCAL = APP / 'Fiscalisation.py'
INVERTER = STAGE / 'home/pi/.config/bouquetmat-inverter.env'


def stop(msg):
    raise SystemExit('STOP: ' + msg)


def literal_assignment(s, key, new, required=True):
    """Replace exactly one top-level literal assignment while keeping its comment."""
    tree = ast.parse(s)
    nodes = []
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(t, ast.Name) and t.id == key for t in targets):
                nodes.append(node)
    if not nodes:
        if required:
            stop(f'{key} not found')
        return s, False
    if len(nodes) != 1:
        stop(f'{key}: expected one assignment, found {len(nodes)}')
    node = nodes[0]
    try:
        old = ast.literal_eval(node.value)
    except (ValueError, TypeError, SyntaxError):
        stop(f'{key}: assignment is dynamic; refusing to modify it')
    if not (isinstance(old, type(new))):
        stop(f'{key}: unexpected value type {type(old).__name__}')
    lines = s.splitlines(keepends=True)
    if node.lineno != node.end_lineno:
        stop(f'{key}: multiline assignment; refusing to modify')
    line = lines[node.lineno - 1]
    pattern = re.compile(r'^([ \t]*' + re.escape(key) + r'[ \t]*=[ \t]*)(.+?)([ \t]*(?:\#[^\r\n]*)?)(\r?\n)?$')
    m = pattern.match(line)
    if not m:
        stop(f'{key}: cannot safely replace line')
    # Existing value must be a simple literal, with at most a trailing comment.
    if isinstance(old, str):
        if not re.fullmatch(r'''(?s)(?:"[^"\n]*"|'[^'\n]*')''', m.group(2)):
            stop(f'{key}: not a simple quoted string')
        replacement = repr(new)
    elif isinstance(old, bool):
        if m.group(2) not in ('True', 'False'):
            stop(f'{key}: not a simple Boolean literal')
        replacement = str(new)
    else:
        stop(f'{key}: unsupported value type')
    lines[node.lineno - 1] = m.group(1) + replacement + m.group(3) + (m.group(4) or '')
    return ''.join(lines), True


def patch_file(path, fn):
    if not path.is_file() or path.is_symlink():
        stop(f'missing/unsafe staged file: {path}')
    if not path.resolve().is_relative_to(STAGE.resolve()):
        stop(f'file escapes stage: {path}')
    old = path.read_text(encoding='utf-8')
    new = fn(old)
    return old, new


def patch_backend(s):
    s, _ = literal_assignment(s, 'BACKEND_PANEL_ID_OVERRIDE', 'Orhideja')
    s, _ = literal_assignment(s, 'BACKEND_PANEL_NAME_OVERRIDE', 'Videm pri Ptuju')
    compile(s, str(BACKEND), 'exec')
    return s


def patch_panel(s):
    s, _ = literal_assignment(s, 'PANEL_TYPE', 'A')
    s, _ = literal_assignment(s, 'PAYMENT_ENABLED', False)
    # An older version may define its own backend identity here too.
    s, _ = literal_assignment(s, 'BACKEND_PANEL_ID_OVERRIDE', 'Orhideja', required=False)
    s, _ = literal_assignment(s, 'BACKEND_PANEL_NAME_OVERRIDE', 'Videm pri Ptuju', required=False)
    compile(s, str(PANEL), 'exec')
    return s


def patch_state(s):
    for key in ('bp_sent', 'allowed'):
        rx = re.compile(r'(?m)^([ \t]*' + key + r'[ \t]*=[ \t]*)[01]([ \t]*)$')
        if len(rx.findall(s)) != 1:
            stop(f'{STATE}: expected one Boolean {key} entry')
        s = rx.sub(lambda m: m.group(1) + '0' + m.group(2), s)
    return s


def patch_inverter(s):
    rx = re.compile(r'(?m)^(INVERTER_PANEL_ID[ \t]*=[ \t]*)([^\r\n#]+)([ \t]*(?:\#[^\r\n]*)?)$')
    if len(rx.findall(s)) != 1:
        stop('Expected one INVERTER_PANEL_ID setting in staged inverter env')
    return rx.sub(lambda m: m.group(1) + 'Orhideja' + m.group(3), s)


def main():
    if os.geteuid() != 0:
        stop('Run with sudo')
    if not (STAGE / 'clone_metadata/source.json').is_file():
        stop('Original staged backup metadata missing')
    if Path('/etc/wa-third-production-ready').exists():
        stop('Commissioning guard file exists; do not edit a commissioned unit')
    for p in (BACKEND, PANEL, STATE, FISCAL, INVERTER):
        if not p.is_file() or p.is_symlink():
            stop(f'Required staged file is missing or symlinked: {p}')
    fiscal_text = FISCAL.read_text(encoding='utf-8')
    if 'def process_transaction' not in fiscal_text or 'read_state()' not in fiscal_text or 'if allowed == 0:' not in fiscal_text:
        stop('FURS gate differs from verified Jagodic version; manual inspection needed')
    edits = {
        BACKEND: patch_file(BACKEND, patch_backend),
        PANEL: patch_file(PANEL, patch_panel),
        STATE: patch_file(STATE, patch_state),
        INVERTER: patch_file(INVERTER, patch_inverter),
    }
    backup = STAGE / 'clone_metadata' / ('orhideja_prepatch_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
    if backup.exists():
        stop('Backup directory already exists')
    backup.mkdir(mode=0o700)
    for path, (old, new) in edits.items():
        if old != new:
            b = backup / path.name
            shutil.copy2(path, b)
    try:
        for path, (old, new) in edits.items():
            if old == new:
                continue
            tmp = path.with_name(path.name + '.orhideja-tmp')
            tmp.write_text(new, encoding='utf-8')
            os.chmod(tmp, path.stat().st_mode & 0o777)
            os.replace(tmp, path)
    except BaseException:
        for path, (old, new) in edits.items():
            if old != new:
                b = backup / path.name
                if b.exists():
                    shutil.copy2(b, path)
        raise
    print('SUCCESS: STAGED copy configured:')
    print('  BACKEND_PANEL_ID_OVERRIDE = Orhideja')
    print('  BACKEND_PANEL_NAME_OVERRIDE = Videm pri Ptuju')
    print('  PANEL_TYPE = A')
    print('  PAYMENT_ENABLED = False')
    print('  bp_sent=0 and allowed=0 (Slovenian FURS normal transaction gate)')
    print('  INVERTER_PANEL_ID = Orhideja')
    print('Backups:', backup)
    print('NO service started or installed. Fiscal certificates/receipts and IDs still need commissioning.')


if __name__ == '__main__':
    main()
