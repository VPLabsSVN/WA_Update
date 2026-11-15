#!/usr/bin/env bash
# FIN ONLY: capture ACTUAL deployed kiosk sources and upload an ENCRYPTED archive
# to a temporary file service without GitHub login on the Pi.
# No application modification, restarts, service changes, or GitHub authentication.
set -euo pipefail
set +x
umask 077

APPDIR="/home/wa_stopshop_mb/WA/Main_program/Fis"
STAMP="$(date +%Y%m%d_%H%M%S)"
ENC="$HOME/fin_kiosk_source_${STAMP}.tar.gz.gpg"
UPLOAD_API="https://tempfile.org/api/upload/local"

for bin in curl gpg tar sha256sum python3; do
  command -v "$bin" >/dev/null 2>&1 || { echo "Required command missing: $bin" >&2; exit 1; }
done
[[ -d "$APPDIR" ]] || { echo "App directory missing: $APPDIR" >&2; exit 1; }

TEMP="$(mktemp -d "$HOME/.fin_export_XXXXXXXX")"
cleanup() { rm -rf -- "$TEMP"; }
trap cleanup EXIT
mkdir -m 700 "$TEMP/source"

{
  echo "Capture date: $(date -Is)"
  echo "Hostname: $(hostname)"
  echo "App dir: $APPDIR"
  echo
  systemctl --user show bouquetmat.service -p LoadState -p ActiveState -p MainPID -p FragmentPath -p ExecMainStartTimestamp 2>/dev/null || true
  echo
  echo "Source SHA256:"
} > "$TEMP/manifest.txt"

COUNT=0
for f in Panel.py Panel_backend_merged.py Panel_backend_combined.py backend_client.py Ads.py wa_module_health_bridge.py bouquetmat_launcher.py requirements.txt; do
  if [[ -f "$APPDIR/$f" && -r "$APPDIR/$f" ]]; then
    cp -L -- "$APPDIR/$f" "$TEMP/source/$f"
    chmod 600 "$TEMP/source/$f"
    sha256sum "$TEMP/source/$f" >> "$TEMP/manifest.txt"
    COUNT=$((COUNT+1))
  fi
done
((COUNT>=2)) || { echo "Too few files found; refusing to upload." >&2; exit 1; }

tar -C "$TEMP" -czf "$TEMP/source.tar.gz" manifest.txt source
echo "Files collected: $COUNT"
echo "Use a NEW, unique passphrase, at least 20 characters long."
read -r -s -p 'Encryption passphrase: ' PASS
echo
read -r -s -p 'Repeat encryption passphrase: ' PASS2
echo
if [[ "$PASS" != "$PASS2" || ${#PASS} -lt 20 ]]; then
  unset PASS PASS2
  echo 'Passphrase mismatch or too short; upload cancelled.' >&2
  exit 1
fi
unset PASS2

gpg --batch --yes --no-symkey-cache --pinentry-mode loopback \
  --symmetric --cipher-algo AES256 --s2k-digest-algo SHA256 \
  --passphrase-fd 3 --output "$ENC" "$TEMP/source.tar.gz" 3<<< "$PASS"
unset PASS
chmod 600 "$ENC"
[[ -s "$ENC" ]] || { echo "Encryption failed" >&2; exit 1; }
echo "Encrypted source: $ENC"
sha256sum "$ENC"
echo
echo "Uploading ENCRYPTED archive to TempFile.org (24-hour expiry; no account required) ..."
HTTP="$(curl -sS --retry 2 --retry-delay 2 --max-time 120 \
    -o "$TEMP/result.json" -w '%{http_code}' \
    -X POST "$UPLOAD_API" \
    -F "files=@$ENC" -F "expiryHours=24")"
if [[ "$HTTP" != "200" && "$HTTP" != "201" ]]; then
  echo "Upload failed (HTTP $HTTP). Encrypted file remains at: $ENC"
  exit 1
fi

python3 - "$TEMP/result.json" <<'PY'
import json
import sys
from urllib.parse import urlparse
with open(sys.argv[1], encoding='utf-8') as f:
    data=json.load(f)
files=data.get('files') or []
if data.get('success') is not True or not files:
    raise SystemExit('Upload response does not report success')
url=files[0].get('url','')
p=urlparse(url)
if p.scheme!='https' or p.netloc!='tempfile.org':
    raise SystemExit('Unexpected upload URL')
print('\nUPLOAD SUCCESSFUL')
print('Temporary encrypted-download link:',url)
print('Send this link in your ChatGPT conversation.')
print('The file expires after 24 hours.')
PY
echo "The kiosk was not modified or restarted."
echo "Only the ENCRYPTED archive was uploaded; the temporary plaintext copy has been removed."
