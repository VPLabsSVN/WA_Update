#!/usr/bin/env python3
"""Fin V2.4 offline-test fix only. NO production file or service changes.

Fixes non-deterministic unit test that accidentally depends on the existence
of ~/.config/bouquetmat/fin_v24_hold_proof.json after a successful live hold test.
Changes test_fin_reservation_v2.py, installer *test SHA*, and local SHA256SUMS.
The production application patch and helper are intentionally unchanged.
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import sys
import tempfile

OLD_TEST = "ae8f51370a2a0d985217c6f18289d8315610acc90f6a076556958647369abd78"
NEW_TEST = "2c7654ae172af3156e821580cf1bf6be8953940425f8d8a8a5afba17813930dc"
OLD_INSTALLER = "fa0235fc22e760d0248f3433ddced1bec96aaeff58f0dcc00ec7c4026ec2c382"
NEW_INSTALLER = "3ceab3a51326c2eaedcdc953a137cb41149b993deb1873a439a1cf171657a1aa"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def replace_once(s: str, before: str, after: str) -> str:
    if s.count(before) != 1:
        raise ValueError(f"Unexpected package content: anchor occurs {s.count(before)} times")
    return s.replace(before, after, 1)


def transform_test(original: str) -> str:
    s = replace_once(original, "import ast\n", "import ast\nimport io\nimport json\n")
    s = replace_once(
        s,
        '"time":__import__(\'time\'), "datetime": datetime, "os":__import__(\'os\')}',
        '"time":__import__(\'time\'), "datetime": datetime, "os":__import__(\'os\'), "json": json}',
    )
    s = replace_once(
        s,
        "self.method('_reservation_v2_start_checkout',{'claim_seconds_remaining':lambda d: None})(app2,'UNPAID','claimtoken',{'item_id':3,'panel_position':0},{})",
        "self.method('_reservation_v2_start_checkout',{\n"
        "            'claim_seconds_remaining':lambda d: None,\n"
        "            # Keep this test independent of a *real* local hold proof on Fin.\n"
        "            'open': lambda *_a, **_kw: (_ for _ in ()).throw(FileNotFoundError()),\n"
        "        })(app2,'UNPAID','claimtoken',{'item_id':3,'panel_position':0},{})",
    )
    addition = '''
    def test_unpaid_with_verified_local_hold_proof_starts_checkout(self):
        # Exercise the expiry-free path without reading the real Pi's proof.
        app,events=self.dummy()
        app._reservation_v2_recovery_path='/tmp/fin_v2_nonexistent_unit_test_file'
        app._checkout_in_progress=False
        app._reservation_v2_pending=None
        app._get_panel_item=lambda pos,iid:{'id':iid,'price':12.5,'tax_pct':22,'enabled':True,'label':'Test item'}
        app._reservation_v2_store_recovery=lambda ctx,phase:events.append(('ledger',phase))
        app._reservation_v2_error=lambda msg:events.append(('error',msg))
        app._start_payment_for_items=lambda *args:events.append(('payment',args))
        method=self.method('_reservation_v2_start_checkout', {
            'claim_seconds_remaining':lambda _d:None,
            'open':lambda *_a,**_kw:io.StringIO('{}'),
            'validated_unreported_claim_window':lambda *_a,**_kw:120,
            'BACKEND_BASE_URL':'https://example.invalid',
            'BACKEND_PANEL_ID':'Fin',
            'base':SimpleNamespace(PANEL_POSITION=0,DEFAULT_TAX_PERCENT=22,next_invoice_number=lambda:'INVOICE-2'),
        })
        method(app,'UNPAID','claimtoken',{'item_id':3,'panel_position':0},{'claimed':True,'claim_token':'claimtoken'})
        self.assertTrue(any(x[0]=='payment' for x in events))
        self.assertTrue(any(x[0]=='ledger' for x in events))
        self.assertFalse(any(x[0]=='door' for x in events))
'''
    return replace_once(
        s,
        "        self.assertTrue(any(x[0]=='release' for x in events2))\n",
        "        self.assertTrue(any(x[0]=='release' for x in events2))\n" + addition,
    )


def atomic_write(path: Path, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".fin_v24_test_fix_")
    try:
        os.fchmod(fd, path.stat().st_mode & 0o777)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dir", type=Path, default=Path.home() / "fin_reservation_v2_4")
    args = p.parse_args()
    root = args.dir.expanduser().resolve()
    test = root / "test_fin_reservation_v2.py"
    installer = root / "fin_reservation_v2_apply.sh"
    sums = root / "SHA256SUMS"
    for f in (test, installer, sums):
        if not f.is_file():
            raise ValueError(f"Missing package file: {f.name}")
    test_hash = sha(test.read_bytes())
    inst_hash = sha(installer.read_bytes())
    if test_hash == NEW_TEST and inst_hash == NEW_INSTALLER:
        print("Test harness already corrected; nothing changed.")
        return 0
    if test_hash != OLD_TEST or inst_hash != OLD_INSTALLER:
        raise ValueError("Package is modified/unrecognized; refusing to change it")
    expected = transform_test(test.read_text(encoding="utf-8")).encode("utf-8")
    if sha(expected) != NEW_TEST:
        raise ValueError("Internal expected test hash mismatch")
    inst_new = replace_once(installer.read_text(encoding="utf-8"), OLD_TEST, NEW_TEST).encode("utf-8")
    if sha(inst_new) != NEW_INSTALLER:
        raise ValueError("Internal expected installer hash mismatch")
    manifest = sums.read_text(encoding="utf-8")
    manifest = replace_once(manifest, OLD_TEST + "  test_fin_reservation_v2.py", NEW_TEST + "  test_fin_reservation_v2.py")
    manifest = replace_once(manifest, OLD_INSTALLER + "  fin_reservation_v2_apply.sh", NEW_INSTALLER + "  fin_reservation_v2_apply.sh")

    # Only the extracted package is modified; NEVER touch the production APPDIR.
    atomic_write(test, expected)
    atomic_write(installer, inst_new)
    atomic_write(sums, manifest.encode("utf-8"))
    print("Corrected V2.4 offline test harness (now 39 tests).")
    print("Production files and running services were NOT modified.")
    print("Next: cd ~/fin_reservation_v2_4 && sha256sum -c SHA256SUMS && bash fin_reservation_v2_apply.sh --check")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        print(f"Fix refused: {type(e).__name__}: {e}", file=sys.stderr)
        raise SystemExit(1)
