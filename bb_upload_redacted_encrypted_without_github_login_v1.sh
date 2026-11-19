#!/usr/bin/env bash
# BB ONLY. Encrypt existing best-effort-redacted review ZIP with GPG AES-256,
# upload encrypted blob anonymously to TempFile.org; no GitHub credentials,
# no SCP, no kiosk changes, no server code or hardware actions.
set -euo pipefail
set +x
umask 077

ZIP="$HOME/BB_source_review_redacted.zip"
STAMP="$(date +%Y%m%d_%H%M%S)"
OUT="$HOME/BB_source_review_redacted_$STAMP.zip.gpg"
API="https://tempfile.org/api/upload/local"

for p in curl gpg sha256sum python3; do
  command -v "$p" >/dev/null 2>&1 || { echo "Missing required program: $p" >&2; exit 1; }
done
[[ -f "$ZIP" && -r "$ZIP" ]] || { echo "Missing review ZIP: $ZIP" >&2; exit 1; }

# Check that the archive contains only the expected redacted review output.
python3 - "$ZIP" <<'PY'
import sys, zipfile, pathlib
p=pathlib.Path(sys.argv[1])
if p.stat().st_size > 2_000_000:
    raise SystemExit("Review ZIP unexpectedly large; refusing")
expected={
"BB_source_redacted/Panel.py",
"BB_source_redacted/Panel_backend_merged.py",
"BB_source_redacted/backend_client.py",
"BB_source_redacted/Ads.py",
"BB_source_redacted/report.json",
"BB_source_redacted/README.txt",
}
with zipfile.ZipFile(p) as z:
    names=set(z.namelist())
    if names != expected:
        raise SystemExit("Unexpected ZIP members; refusing to send")
    for item in z.infolist():
        is_symlink=((item.external_attr >> 16) & 0o170000) == 0o120000
        if item.is_dir() or item.file_size>1_500_000 or is_symlink:
            raise SystemExit("Unsupported ZIP entry type or size")
    redacted = b"".join(z.read(x) for x in expected if x.endswith(".py"))
    if b"REDACTED" not in redacted:
        raise SystemExit("No redaction markers found; refusing to send")
print("BB redacted source ZIP structure: VERIFIED (not a guarantee all secrets were found)")
PY

TMP="$(mktemp -d "$HOME/.bb_encrypted_upload_XXXXXXXX")"
cleanup(){ rm -rf -- "$TMP"; }
trap cleanup EXIT

echo "Source ZIP: $ZIP"
echo "Source SHA256:"
sha256sum "$ZIP"
echo
echo "Choose a NEW, UNIQUE passphrase (20+ characters, not used elsewhere)."
read -r -s -p 'Encryption passphrase: ' PASS
echo
read -r -s -p 'Repeat encryption passphrase: ' PASS2
echo
if [[ "$PASS" != "$PASS2" || ! "$PASS" =~ ^.{20,}$ ]]; then
  unset PASS PASS2
  echo "Passphrase mismatch or too short; nothing uploaded" >&2
  exit 1
fi
unset PASS2
gpg --batch --yes --pinentry-mode loopback --no-symkey-cache \
    --symmetric --cipher-algo AES256 --s2k-digest-algo SHA256 \
    --passphrase-fd 3 --output "$OUT" "$ZIP" 3<<< "$PASS"
unset PASS
chmod 600 "$OUT"
[[ -s "$OUT" ]] || { echo "Encryption failed" >&2; exit 1; }
echo
echo "Encrypted filename: $(basename "$OUT")"
echo "Encrypted SHA256:"
sha256sum "$OUT"

echo
echo "Uploading ONLY the encrypted file to TempFile.org (24-hour expiry)."
HTTP="$(curl --silent --show-error --retry 2 --retry-delay 2 \
  --max-time 120 -o "$TMP/response.json" -w '%{http_code}' \
  -X POST "$API" -F "files=@$OUT" -F "expiryHours=24")"
if [[ "$HTTP" != "200" && "$HTTP" != "201" ]]; then
  echo "Upload failed (HTTP $HTTP). Encrypted local file remains: $OUT" >&2
  exit 1
fi
python3 - "$TMP/response.json" <<'PY'
import json, sys
from urllib.parse import urlparse
with open(sys.argv[1],encoding="utf-8") as f:
    obj=json.load(f)
files=obj.get("files") or []
if obj.get("success") is not True or len(files)<1:
    raise SystemExit("Upload service did not confirm success")
url=files[0].get("url","")
u=urlparse(url)
if u.scheme!="https" or u.netloc!="tempfile.org":
    raise SystemExit("Unexpected upload destination in response")
print("\nUPLOAD SUCCESSFUL")
print("Temporary encrypted download:",url)
print("Send ChatGPT this URL, the encrypted filename, and the SHA256 above.")
print("Encrypted file expires from the transfer service after 24 hours.")
PY
echo
echo "BB application, customer records, backend, services and hardware unchanged."
echo "Do NOT put the plain ZIP on public GitHub. Keep the passphrase private."
