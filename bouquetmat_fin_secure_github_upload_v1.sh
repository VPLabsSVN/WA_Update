#!/usr/bin/env bash
# Bouquetmat Fin: take a snapshot of deployed Python files, encrypt, and upload
# encrypted archive only to the public WA_Update GitHub repository.
# NO changes to app files, systemd units, motor controls, network or kiosk processes.
set -euo pipefail
set +x
umask 077

REPO="VPLabsSVN/WA_Update"
APPDIR="/home/wa_stopshop_mb/WA/Main_program/Fis"
STAMP="$(date +%Y%m%d_%H%M%S)"
NAME="fin_kiosk_source_${STAMP}.tar.gz.gpg"
ENCRYPTED="$HOME/$NAME"

[[ -d "$APPDIR" ]] || { echo "ERROR: missing app directory: $APPDIR" >&2; exit 1; }
command -v gpg >/dev/null || { echo 'ERROR: gpg missing. Install with: sudo apt install gnupg' >&2; exit 1; }
command -v python3 >/dev/null || { echo 'ERROR: python3 missing.' >&2; exit 1; }

TMP="$(mktemp -d "$HOME/.fin_secure_export.XXXXXXXX")"
trap 'rm -rf -- "$TMP"' EXIT
mkdir -m 700 "$TMP/source"

{
  printf 'Collected: %s\n' "$(date -Is)"
  printf 'Machine: %s\n' "$(hostname)"
  printf 'Application dir: %s\n\n' "$APPDIR"
  printf 'Running bouquetmat user service:\n'
  systemctl --user show bouquetmat.service -p LoadState -p ActiveState -p MainPID -p FragmentPath -p ExecMainStartTimestamp 2>/dev/null || true
  printf '\nFiles copied (SHA256):\n'
} > "$TMP/manifest.txt"

COUNT=0
for NAME_PART in \
  Panel.py \
  Panel_backend_merged.py \
  Panel_backend_combined.py \
  backend_client.py \
  Ads.py \
  wa_module_health_bridge.py \
  bouquetmat_launcher.py \
  requirements.txt
do
  SOURCE="$APPDIR/$NAME_PART"
  if [[ -f "$SOURCE" && -r "$SOURCE" ]]; then
    cp -L -- "$SOURCE" "$TMP/source/$NAME_PART"
    chmod 600 "$TMP/source/$NAME_PART"
    sha256sum "$TMP/source/$NAME_PART" >> "$TMP/manifest.txt"
    COUNT=$((COUNT+1))
  fi
done

(( COUNT > 0 )) || { echo 'ERROR: No requested source files readable.' >&2; exit 1; }
tar -C "$TMP" -czf "$TMP/source.tar.gz" manifest.txt source

printf '\nThis script will encrypt %s source files before uploading.\n' "$COUNT"
printf 'The GitHub repository is PUBLIC; only a GPG-encrypted file will be uploaded.\n'
printf 'Use a NEW strong passphrase (20+ characters), not an existing account password.\n'
read -r -s -p 'Encryption passphrase: ' PASS
printf '\n'
read -r -s -p 'Repeat encryption passphrase: ' CONFIRM
printf '\n'
if [[ "$PASS" != "$CONFIRM" || "${#PASS}" -lt 20 ]]; then
  unset PASS CONFIRM
  echo 'ERROR: Passphrases do not match or are shorter than 20 characters.' >&2
  exit 1
fi
unset CONFIRM

# GPG receives passphrase on an inherited file descriptor, NOT as a command argument.
# Encrypted archive stays on Pi if upload cannot complete.
gpg --batch --yes --no-symkey-cache --pinentry-mode loopback \
  --symmetric --cipher-algo AES256 --s2k-digest-algo SHA256 \
  --passphrase-fd 3 --output "$ENCRYPTED" "$TMP/source.tar.gz" 3<<< "$PASS"
unset PASS
chmod 600 "$ENCRYPTED"
echo
printf 'Encrypted file: %s\n' "$ENCRYPTED"
sha256sum "$ENCRYPTED"
echo

if ! command -v gh >/dev/null 2>&1; then
  echo 'GitHub CLI (gh) not found. Encrypted archive is ready for a manual browser upload.'
  echo 'To enable terminal upload: sudo apt install gh'
  echo 'Then: gh auth login --hostname github.com --git-protocol https --web'
  echo 'And re-run this script (it creates a NEW encrypted archive).'
  exit 0
fi

if ! gh auth status --hostname github.com >/dev/null 2>&1; then
  echo 'GitHub CLI needs authentication; encrypted archive is ready locally.'
  echo 'Run: gh auth login --hostname github.com --git-protocol https --web'
  echo 'Then re-run this script, or upload the encrypted file through GitHub in the Pi browser.'
  exit 0
fi

python3 - "$ENCRYPTED" "$TMP/upload.json" <<'PY'
import base64
import json
import pathlib
import sys
encrypted = pathlib.Path(sys.argv[1])
payload_path = pathlib.Path(sys.argv[2])
payload = {
    "message": "Upload encrypted Fin kiosk source snapshot for performance audit",
    "content": base64.b64encode(encrypted.read_bytes()).decode("ascii"),
}
payload_path.write_text(json.dumps(payload), encoding="utf-8")
PY
chmod 600 "$TMP/upload.json"

echo 'Uploading ENCRYPTED file to GitHub ...'
URL="$(gh api --method PUT --input "$TMP/upload.json" \
  "repos/$REPO/contents/fin_source_audit/$NAME" \
  --jq '.content.html_url')"
printf '\nUPLOAD COMPLETE\n%s\n' "$URL"
echo 'Plaintext files existed only in the private temporary folder, which is deleted on exit.'
echo 'Keep the encryption passphrase private; it is NOT uploaded to GitHub.'
