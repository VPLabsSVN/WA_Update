#!/usr/bin/env python3
"""Correct stale FURS JWS certificate metadata without registering or enabling sales.

Derives subject, issuer, and serial from the *installed merchant certificate*.
Aborts unless the certificate is valid, matches the private key, and has
Jagodic's expected taxpayer number in its certificate OU attribute.
"""
from __future__ import annotations

import ast
from datetime import datetime, timezone
import os
from pathlib import Path
import pprint
import shutil
import stat
import sys
from cryptography import x509
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from cryptography.x509.oid import NameOID

ROOT = Path('/home/wa_stopshop_mb/WA/Main_program/Fis')
EXPECTED_TAX_NUMBER = '75196212'
EXPECTED_BP = 'WAF260003'


def require(condition, message):
    if not condition:
        raise RuntimeError('STOP: ' + message)


def assignment(tree, symbol):
    hits = [n for n in tree.body if isinstance(n, ast.Assign)
            and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name)
            and n.targets[0].id == symbol]
    require(len(hits) == 1, 'expected precisely one ' + symbol + ' assignment')
    return hits[0], ast.literal_eval(hits[0].value)


def main():
    panel = ROOT / 'Panel.py'
    fiscal = ROOT / 'Fiscalisation.py'
    state = ROOT / 'bp_sent.txt'
    require(panel.is_file() and fiscal.is_file() and state.is_file(),
            'production program or state not found')
    require('allowed=0' in state.read_text(encoding='utf-8').splitlines(),
            'FURS fiscal sales must remain disabled')
    panel_tree = ast.parse(panel.read_text(encoding='utf-8'))
    require(assignment(panel_tree, 'PAYMENT_ENABLED')[1] is False,
            'POS sales must remain disabled')

    src = fiscal.read_text(encoding='utf-8')
    tree = ast.parse(src)
    require(str(assignment(tree, 'TAX_NUMBER')[1]) == EXPECTED_TAX_NUMBER,
            'wrong FURS taxpayer number')
    require(assignment(tree, 'BUSINESS_PREMISE_ID')[1] == EXPECTED_BP,
            'unexpected business premises ID')
    require(assignment(tree, 'BASE_URL')[1] == 'https://blagajne.fu.gov.si:9003',
            'unexpected FURS endpoint')
    require(assignment(tree, 'ELECTRONIC_DEVICE_ID')[1] == '3',
            'expected electronic device ID 3')
    require('"NumberingStructure": "C"' in src,
            'expected Type D numbering structure C')

    cert_path = Path(assignment(tree, 'CLIENT_CERT_PATH')[1])
    key_path = Path(assignment(tree, 'CLIENT_KEY_PATH')[1])
    cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    key = load_pem_private_key(key_path.read_bytes(), password=None)
    require(cert.public_key().public_numbers() == key.public_key().public_numbers(),
            'certificate and private key are not a pair')

    ou_values = [attr.value for attr in
                 cert.subject.get_attributes_for_oid(NameOID.ORGANIZATIONAL_UNIT_NAME)]
    require(EXPECTED_TAX_NUMBER in ou_values,
            'installed certificate does not have the expected tax number in an OU')
    require(any('DavPotRac' in val for val in ou_values),
            'installed certificate lacks the DavPotRac purpose')
    now = datetime.now(timezone.utc)
    first = getattr(cert, 'not_valid_before_utc', None)
    last = getattr(cert, 'not_valid_after_utc', None)
    if first is None:
        first = cert.not_valid_before.replace(tzinfo=timezone.utc)
    if last is None:
        last = cert.not_valid_after.replace(tzinfo=timezone.utc)
    require(first <= now <= last, 'merchant certificate is outside its validity period')

    node, header = assignment(tree, 'JWS_HEADER')
    require(isinstance(header, dict) and header.get('alg') == 'RS256',
            'unexpected JWS header type/algorithm')
    require('subject_name' in header and 'issuer_name' in header and 'serial' in header,
            'JWS header does not have expected fields')

    # Certificate metadata is public; the signing key is neither modified nor printed.
    correct = dict(header)
    correct['subject_name'] = cert.subject.rfc4514_string()
    correct['issuer_name'] = cert.issuer.rfc4514_string()
    correct['serial'] = int(cert.serial_number)
    print('Certificate taxpayer OU:', EXPECTED_TAX_NUMBER)
    print('Certificate validity:', first.date(), 'to', last.date())
    print('Current JWS matches certificate:', header == correct)
    if correct == header:
        print('No changes needed.')
        return

    lines = src.splitlines(keepends=True)
    # JWS_HEADER is a top-level multiline assignment; replace only that statement.
    require(node.col_offset == 0, 'JWS_HEADER must be a top-level assignment')
    new_assignment = 'JWS_HEADER = ' + pprint.pformat(correct, sort_dicts=False, width=100) + '\n'
    changed = ''.join(lines[:node.lineno - 1]) + new_assignment + ''.join(lines[node.end_lineno:])
    compile(changed, str(fiscal), 'exec')
    new_tree = ast.parse(changed)
    require(assignment(new_tree, 'JWS_HEADER')[1] == correct, 'header verification failed')

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup = fiscal.with_name('Fiscalisation.py.before_jws_fix_' + timestamp)
    shutil.copy2(fiscal, backup)
    temp = fiscal.with_name('.Fiscalisation.py.jws_new')
    try:
        temp.write_text(changed, encoding='utf-8')
        os.chmod(temp, stat.S_IMODE(fiscal.stat().st_mode))
        os.replace(temp, fiscal)
    finally:
        if temp.exists():
            temp.unlink()
    print('Backup:', backup)
    print('Updated JWS_HEADER subject_name, issuer_name and serial from the installed certificate')
    print('No FURS request or POS transaction was made. Sales remain disabled.')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('ERROR:', exc, file=sys.stderr)
        sys.exit(1)
