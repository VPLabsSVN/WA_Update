#!/usr/bin/env python3
"""Guarded local setup for Jagodic Slovenian FURS Type-D vending machine.

--prepare: back up and change POS identifier and FURS Type-D fields (no network).
--register: validate merchant cert, send one production BP registration, mark bp_sent=1 only on acceptance.
--status:  read-only checks. Never enables POS or fiscalized sales.
"""
from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
from pathlib import Path
import re
import shutil
import sys

ROOT_DEFAULT = Path('/home/wa_stopshop_mb/WA/Main_program/Fis')
MERCHANT_TAX = 75196212
POS_ID = '25688418'
BP = 'WAF260003'
DEVICE = '3'
PROD = 'https://blagajne.fu.gov.si:9003'


def must(cond, message):
    if not cond:
        raise RuntimeError('STOP: ' + message)


def assignment(src, name):
    tree = ast.parse(src)
    hits = []
    for n in tree.body:
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in n.targets):
            hits.append(ast.literal_eval(n.value))
    must(len(hits) == 1, f'exactly one {name} assignment required')
    return hits[0]


def safe_state(root):
    state = (root / 'bp_sent.txt').read_text(encoding='utf-8').splitlines()
    must('allowed=0' in state, 'FURS allowed must remain 0 during preparation/registration')
    return 'bp_sent=1' in state


def preflight(root):
    must(root.is_dir(), f'program folder not found: {root}')
    p = (root / 'Panel.py').read_text(encoding='utf-8')
    f = (root / 'Fiscalisation.py').read_text(encoding='utf-8')
    must(assignment(p, 'PAYMENT_ENABLED') is False, 'PAYMENT_ENABLED must be False')
    must(int(assignment(f, 'TAX_NUMBER')) == MERCHANT_TAX,
         f'FURS TAX_NUMBER is not {MERCHANT_TAX}; check merchant configuration and certificate before edits')
    must(assignment(f, 'BUSINESS_PREMISE_ID') == BP,
         f'FURS BUSINESS_PREMISE_ID is not {BP}')
    must(assignment(f, 'BASE_URL') == PROD, 'FURS must use exact production endpoint')
    sent = safe_state(root)
    return p, f, sent


def status(root):
    p, f, sent = preflight(root)
    print('Merchant tax number:', assignment(f, 'TAX_NUMBER'))
    print('Business premise:', assignment(f, 'BUSINESS_PREMISE_ID'))
    print('FURS device ID:', assignment(f, 'ELECTRONIC_DEVICE_ID'))
    print('POS ID:', assignment(p, 'PAYMENT_POS_ID'))
    print('BP registered in local state:', sent)
    print('POS enabled:', assignment(p, 'PAYMENT_ENABLED'))
    print('FURS enabled: False')
    print('NumberingStructure=C:', '"NumberingStructure": "C"' in f)
    counter = Path('/home/wa_stopshop_mb/WA/Main_program/Panel/invoice_counter.txt')
    if counter.exists():
        print('Next transaction counter:', counter.read_text(encoding='utf-8').strip())
    if re.search(r'(?m)^\s*invoice_number\s*=\s*next_invoice_number\(\)', p):
        print('CHECKOUT AUDIT: number is allocated in checkout code; needs POS-flow correction before live sales')
    print('IMPORTANT: this tool does not enable sales')


def prepare(root):
    p, f, sent = preflight(root)
    must(not sent, 'BP already registered locally; do not modify registered identifiers')
    device = assignment(f, 'ELECTRONIC_DEVICE_ID')
    must(device in {'FLORA0003', DEVICE}, f'unexpected device ID: {device!r}')
    pos = assignment(p, 'PAYMENT_POS_ID')
    must(pos in {'__SET_JAGODIC_POS_ID__', POS_ID}, f'unexpected POS ID: {pos!r}')
    n = f.count('"NumberingStructure": "B"')
    c = f.count('"NumberingStructure": "C"')
    must((n, c) in {(1, 0), (0, 1)}, 'unexpected invoice numbering-structure assignment')

    f_new = f
    if device != DEVICE:
        f_new, count = re.subn(r'(?m)^(ELECTRONIC_DEVICE_ID\s*=\s*)[\'\"]FLORA0003[\'\"]',
                               rf'\g<1>"{DEVICE}"', f_new)
        must(count == 1, 'cannot patch electronic device ID exactly once')
    f_new = f_new.replace('"NumberingStructure": "B"', '"NumberingStructure": "C"')
    p_new = p
    if pos != POS_ID:
        p_new, count = re.subn(r'(?m)^(PAYMENT_POS_ID\s*=\s*)[\'\"]__SET_JAGODIC_POS_ID__[\'\"]',
                               rf'\g<1>"{POS_ID}"', p_new)
        must(count == 1, 'cannot patch POS ID exactly once')

    must(assignment(f_new, 'ELECTRONIC_DEVICE_ID') == DEVICE, 'device patch check failed')
    must(assignment(p_new, 'PAYMENT_POS_ID') == POS_ID, 'POS patch check failed')
    must(assignment(p_new, 'PAYMENT_ENABLED') is False, 'POS enablement modified unexpectedly')
    must(f_new.count('"NumberingStructure": "C"') == 1, 'numbering patch check failed')
    compile(f_new, 'Fiscalisation.py', 'exec')
    compile(p_new, 'Panel.py', 'exec')

    if (p, f) == (p_new, f_new):
        print('Already prepared; nothing modified')
        return

    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup = root / f'jagodic_setup_backup_{stamp}'
    backup.mkdir(mode=0o700)
    for filename in ('Panel.py', 'Fiscalisation.py'):
        shutil.copy2(root / filename, backup / filename)
    print('Backups:', backup)

    # Files are edited only after every validation and compile has passed.
    for filename, source in (('Panel.py', p_new), ('Fiscalisation.py', f_new)):
        dest = root / filename
        temp = root / (filename + '.jagodic-new')
        temp.write_text(source, encoding='utf-8')
        temp.chmod(dest.stat().st_mode & 0o777)
        temp.replace(dest)
    print('Prepared: POS ID 25688418; FURS device ID 3; numbering structure C')
    print('No network call, no BP registration, no POS activation and no fiscal sale performed')


def register(root):
    from cryptography import x509
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    import requests

    p, src, sent = preflight(root)
    must(not sent, 'bp_sent=1 already; refusing duplicate registration')
    must(assignment(p, 'PAYMENT_POS_ID') == POS_ID, 'POS ID is not provisioned in config')
    must(assignment(src, 'ELECTRONIC_DEVICE_ID') == DEVICE, 'FURS device ID must be numeric 3')
    must('"NumberingStructure": "C"' in src, 'Type D numbering structure must be C')

    sys.path.insert(0, str(root))
    import Fiscalisation as f
    bp_payload = f.build_bp_payload()['BusinessPremiseRequest']['BusinessPremise']
    vm = bp_payload.get('BPIdentifier', {}).get('VendingMachine', {})
    address = vm.get('Address', {})
    must(bp_payload.get('TaxNumber') == MERCHANT_TAX, 'BP payload has incorrect taxpayer')
    must(bp_payload.get('BusinessPremiseID') == BP, 'BP payload has incorrect BP ID')
    must(vm.get('VPremiseType') == 'D', 'BP payload must be vending machine Type D')
    must(address.get('Street') == 'Zgornje Negonje', 'wrong BP street')
    must(str(address.get('HouseNumber')) == '35', 'wrong BP house number')
    must(address.get('HouseNumberAdditional') == 'E', 'wrong BP house suffix')
    must(str(address.get('PostalCode')) == '3250', 'wrong BP postal code')
    must(address.get('City') == 'Rogaška Slatina', 'wrong BP post office/city')
    must(str(getattr(f, 'BP_REGISTER_PATH', '')) == '/v1/cash_registers/invoices/register',
         'unexpected registration endpoint')

    cert_bytes = Path(f.CLIENT_CERT_PATH).read_bytes()
    key_bytes = Path(f.CLIENT_KEY_PATH).read_bytes()
    cert = x509.load_pem_x509_certificate(cert_bytes)
    key = load_pem_private_key(key_bytes, password=None)
    must(cert.public_key().public_numbers() == key.public_key().public_numbers(),
         'certificate and signing private key do not match')
    now = datetime.now(timezone.utc)
    start = getattr(cert, 'not_valid_before_utc', cert.not_valid_before.replace(tzinfo=timezone.utc))
    end = getattr(cert, 'not_valid_after_utc', cert.not_valid_after.replace(tzinfo=timezone.utc))
    must(start <= now <= end, 'merchant certificate is expired or not yet valid')
    subject = cert.subject.rfc4514_string()
    must(str(MERCHANT_TAX) in subject, 'certificate belongs to a different tax number')
    must(str(MERCHANT_TAX) in str(f.JWS_HEADER.get('subject_name', '')),
         'JWS header subject_name belongs to another merchant')
    must(int(f.JWS_HEADER.get('serial', -1)) == int(cert.serial_number),
         'JWS header serial does not match installed certificate')
    print('Certificate valid:', start.date(), 'through', end.date())
    print('Merchant certificate tax number:', MERCHANT_TAX)
    print('Submitting production registration:', BP, '/ Type D / Zgornje Negonje 35E')

    with requests.Session() as session:
        result = f.send_bp_once(session)
    body = result.get('decoded_payload') if isinstance(result, dict) else None
    response = body.get('BusinessPremiseResponse') if isinstance(body, dict) else None
    accepted = (result.get('http') == 200 and isinstance(response, dict)
                and isinstance(response.get('Header'), dict) and not response.get('Error'))
    print('HTTP response:', result.get('http'))
    if not accepted:
        print('Registration not confirmed. Error:',
              (response or {}).get('Error') if isinstance(response, dict) else result.get('text'))
        raise RuntimeError('FURS did not confirm BP registration; bp_sent remains 0')
    f.write_state(bp_sent=True, allowed=0)
    print('BP registration ACCEPTED; stored bp_sent=1; fiscalization remains disabled')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['status', 'prepare', 'register'])
    parser.add_argument('--root', type=Path, default=ROOT_DEFAULT)
    opts = parser.parse_args()
    root = opts.root.resolve()
    try:
        {'status': status, 'prepare': prepare, 'register': register}[opts.mode](root)
    except Exception as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
