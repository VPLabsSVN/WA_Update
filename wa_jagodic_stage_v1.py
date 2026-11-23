#!/usr/bin/env python3
"""Offline-only configuration staging for Jagodic. No FURS or email requests."""
from __future__ import annotations
import argparse
import ast
import datetime as dt
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

parser = argparse.ArgumentParser()
parser.add_argument('--apply', action='store_true')
args = parser.parse_args()
root = Path('/home/wa_stopshop_mb/WA/Main_program/Fis')
files = {
    'Panel_backend_merged.py': {
        'RECEIPT_PRINTER_TAX_NUMBER': '75196212',
        'RECEIPT_PRINTER_BUSINESS_NAME': 'CVETLIČNO DARILNI BUTIK JANA',
        'RECEIPT_PRINTER_OPERATOR_NAME': 'JANA JAGODIČ s.p.',
        'RECEIPT_PRINTER_ADDRESS_LINE': 'Rimska cesta 1, 3240 Šmarje pri Jelšah',
        'MONTHLY_INVOICE_EMAIL_TO': 'mateja@azu-plus.si',
    },
    'Fiscalisation.py': {
        'TAX_NUMBER': '75196212',
        'OPERATOR_TAX_NUMBER': '75196212',
        'BUSINESS_PREMISE_ID': 'JAGODIC01',
        'ELECTRONIC_DEVICE_ID': 'AVT01',
    },
}

if not root.is_dir():
    raise SystemExit('ABORT: Fis directory missing')
switch = (root / 'bp_sent.txt').read_text(encoding='utf-8')
if re.search(r'(?m)^[ \t]*allowed[ \t]*=[ \t]*0[ \t]*$', switch) is None:
    raise SystemExit('ABORT: fiscalization not disabled (allowed=0 required)')
if re.search(r'(?m)^[ \t]*PAYMENT_ENABLED[ \t]*=[ \t]*False(?:[ \t]*#.*)?$', (root / 'Panel.py').read_text(encoding='utf-8')) is None:
    raise SystemExit('ABORT: POS not disabled')

prepared = {}
for filename, settings in files.items():
    path = root / filename
    source = path.read_text(encoding='utf-8')
    for key, newvalue in settings.items():
        regex = re.compile(rf'^(?P<prefix>[ \t]*{re.escape(key)}[ \t]*=[ \t]*)(?P<value>[^\r\n#]*)(?P<comment>[ \t]*(?:#.*)?)$', re.M)
        match_list = list(regex.finditer(source))
        if len(match_list) != 1:
            raise SystemExit(f'ABORT: expected one {key} assignment in {filename}, found {len(match_list)}')
        match = match_list[0]
        try:
            previous = ast.literal_eval(match.group('value').strip())
        except Exception:
            raise SystemExit(f'ABORT: {key} is not a simple literal; review it manually')
        if isinstance(previous, str):
            replacement = json.dumps(newvalue, ensure_ascii=False)
        elif type(previous) is int and key in {'TAX_NUMBER', 'OPERATOR_TAX_NUMBER'}:
            replacement = str(int(newvalue))
        else:
            raise SystemExit(f'ABORT: unexpected value type for {key}')
        source = source[:match.start('value')] + replacement + source[match.end('value'):]
    ast.parse(source, filename=str(path))
    prepared[path] = source
    print('PASS:', filename, ', '.join(settings.keys()))

record = {
    'status': 'DRAFT_NOT_SUBMITTED_TO_FURS',
    'seller': 'CVETLIČNO DARILNI BUTIK JANA, JANA JAGODIČ s.p.',
    'seller_tax_number': '75196212',
    'seller_vat_id': 'SI75196212',
    'seller_registered_address': 'Rimska cesta 1, 3240 Šmarje pri Jelšah',
    'machine_address': 'Zgornje Negonje 35E, 3250 Rogaška Slatina',
    'location_commercial_name': 'Trgovski center Rogaška Slatina - Negonje',
    'cadastral_municipality_number': 1176,
    'cadastral_municipality_name': 'Negonje',
    'parcel_number': '289/36',
    'building_number': None,
    'building_part_number': None,
    'provisional_business_premise_id': 'JAGODIC01',
    'provisional_electronic_device_id': 'AVT01',
    'pos_terminal_id': '25688418',
    'monthly_report_recipient': 'mateja@azu-plus.si',
    'technical_provider': 'VP Labs, Vid Pejovnik s.p.',
}
draft = root / 'jagodic_furs_registration_DRAFT.json'
if draft.exists():
    raise SystemExit('ABORT: draft file exists; do not overwrite it')
print('PASS: live fiscalization and POS switches remain disabled')
print('DRAFT:', draft)
if not args.apply:
    print('CHECK ONLY: no files changed')
    raise SystemExit(0)

stamp = dt.datetime.now().strftime('%Y%m%d_%H%M%S')
for path, updated in prepared.items():
    backup = path.with_name(f'{path.name}.before_jagodic_stage_{stamp}')
    shutil.copy2(path, backup)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix='.wa_jagodic_')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(updated)
            f.flush()
            os.fsync(f.fileno())
        shutil.copymode(path, tmp)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    print('UPDATED:', path.name, 'BACKUP:', backup.name)
fd = os.open(str(draft), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
with os.fdopen(fd, 'w', encoding='utf-8') as f:
    json.dump(record, f, indent=2, ensure_ascii=False)
    f.write('\n')
print('PASS: offline draft saved; no FURS registration, invoice, or email sent')
