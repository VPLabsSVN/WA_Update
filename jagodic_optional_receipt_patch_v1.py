#!/usr/bin/env python3
"""Jagodic: make physical receipt printing manual/optional.

This does NOT affect the fiscal worker, checkout, R3 door behavior, or
customer's Print Receipt button. Offline checks only; never contacts FURS/TIM.

Usage (from the Jagodic Fis directory):
  python3 jagodic_optional_receipt_patch_v1.py --check
  python3 jagodic_optional_receipt_patch_v1.py --apply
  python3 jagodic_optional_receipt_patch_v1.py --verify
"""
from __future__ import annotations
import argparse
import ast
from datetime import datetime
import os
from pathlib import Path
import re
import shutil
import tempfile
from types import SimpleNamespace

DEFAULT_ROOT = Path('/home/wa_stopshop_mb/WA/Main_program/Fis')
MARKER = 'JAGODIC_OPTIONAL_RECEIPT_V1'
ANCHOR = '''_PRE_AUTO_RECEIPT_WORKER_SEND = (
    PanelBackendReservationApp._worker_send
)
'''
FUNCTION_NAME = '_worker_send_with_auto_receipt'
OLD_BLOCK = '''    invoice_key = str(invoice_number)

    try:
        already_printed = getattr('''
NEW_BLOCK = '''    # JAGODIC_OPTIONAL_RECEIPT_V1
    # Fiscal processing above always runs; only the automatic *paper print*
    # below is skipped. The customer's on_receipt() button stays available.
    if not RECEIPT_AUTO_PRINT_ENABLED:
        print('[AUTO RECEIPT] Optional printing: manual button only')
        return result

    invoice_key = str(invoice_number)

    try:
        already_printed = getattr('''


def check_structure(source: str) -> str:
    tree = ast.parse(source)
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == FUNCTION_NAME]
    if len(nodes) != 1:
        raise RuntimeError('Expected one automatic-receipt wrapper')
    lines = source.splitlines(keepends=True)
    node = nodes[0]
    block = ''.join(lines[node.lineno - 1:node.end_lineno])
    if block.count(OLD_BLOCK) != 1:
        raise RuntimeError('Automatic-receipt wrapper differs from audited version')
    if source.count(ANCHOR) != 1:
        raise RuntimeError('Fiscal worker wrapper anchor not found exactly once')
    if 'def on_receipt(' not in source:
        raise RuntimeError('Manual receipt method missing')
    return block


def transform(source: str) -> str:
    if MARKER in source:
        raise RuntimeError('Optional-receipt patch is already installed')
    block = check_structure(source)
    new = source.replace(ANCHOR, 'RECEIPT_AUTO_PRINT_ENABLED = False  # Jagodic: customer-requested paper only\n\n'+ANCHOR, 1)
    # Replace specifically inside the function; anchor must be unique in that block.
    if source.count(block) != 1:
        raise RuntimeError('Wrapper function content not unique')
    new = new.replace(block, block.replace(OLD_BLOCK, NEW_BLOCK, 1), 1)
    ast.parse(new)
    return new


def offline_test(modified: str) -> None:
    tree = ast.parse(modified)
    funcs = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == FUNCTION_NAME]
    assert len(funcs) == 1
    f = funcs[0]
    isolated = ast.fix_missing_locations(ast.Module(body=[f], type_ignores=[]))
    calls = []
    marker_result = object()
    def fiscal_worker(*args, **kwargs):
        calls.append(('fiscal', args, kwargs))
        return marker_result
    scope = {
        '_PRE_AUTO_RECEIPT_WORKER_SEND': fiscal_worker,
        'RECEIPT_AUTO_PRINT_ENABLED': False,
    }
    exec(compile(isolated, '<optional-receipt-offline-test>', 'exec'), scope)
    result = scope[FUNCTION_NAME](SimpleNamespace(), '42', '2026-11-23T12:00:00+01:00')
    assert result is marker_result and len(calls) == 1, 'Fiscal worker behavior changed'
    assert calls[0][1][1:3] == ('42', '2026-11-23T12:00:00+01:00')
    print('PASS: Fiscal worker still called exactly once')
    print('PASS: Automatic print action not invoked')
    print('PASS: Manual on_receipt() method remains available')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    g=p.add_mutually_exclusive_group(required=True)
    g.add_argument('--check',action='store_true')
    g.add_argument('--apply',action='store_true')
    g.add_argument('--verify',action='store_true')
    p.add_argument('--root',type=Path,default=DEFAULT_ROOT,help=argparse.SUPPRESS)
    a=p.parse_args()
    root=a.root
    path=root/'Panel_backend_merged.py'
    source=path.read_text(encoding='utf-8')
    if a.verify:
        assert MARKER in source and 'RECEIPT_AUTO_PRINT_ENABLED = False' in source
        ast.parse(source)
        offline_test(source)
        print('PASS: Optional receipt setting installed')
        return
    modified=transform(source)
    offline_test(modified)
    if a.check:
        print('PASS: Compatible; no files modified')
        return
    if not re.search(r'(?m)^allowed\s*=\s*0\s*$', (root/'bp_sent.txt').read_text()):
        raise SystemExit('ABORT: FURS must remain disabled during update')
    if not re.search(r'(?m)^PAYMENT_ENABLED\s*=\s*False\b', (root/'Panel.py').read_text()):
        raise SystemExit('ABORT: POS must remain disabled during update')
    stamp=datetime.now().strftime('%Y%m%d_%H%M%S')
    backup=path.with_name(f'Panel_backend_merged.py.before_optional_receipt_{stamp}')
    shutil.copy2(path, backup)
    fd, tmp=tempfile.mkstemp(dir=path.parent,prefix='.jagodic_receipt_')
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as f:
            f.write(modified);f.flush();os.fsync(f.fileno())
        shutil.copymode(path,tmp)
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)
    print('PASS: Manual-only physical receipt printing installed')
    print('Backup:',backup.name)
    print('Restart kiosk only when idle')

if __name__=='__main__':
    main()
