#!/usr/bin/env bash
# Fin / older bouquetmat: capture the ACTUAL deployed kiosk source, locally only.
# Does not restart, stop, patch, or upload anything.
set -euo pipefail
umask 077

APPDIR="${APPDIR:-/home/wa_stopshop_mb/WA/Main_program/Fis}"
STAMP="$(date +%Y%m%d_%H%M%S)"
OUT="${1:-$HOME/fin_kiosk_source_${STAMP}.tar.gz}"

[[ -d "$APPDIR" ]] || { echo "Application directory missing: $APPDIR" >&2; exit 1; }
TEMP="$(mktemp -d "${HOME}/.fin_source_XXXXXXXX")"
trap 'rm -rf -- "$TEMP"' EXIT
mkdir -m 700 "$TEMP/source"

{
    printf 'Captured at: %s\n' "$(date -Is)"
    printf 'Hostname: %s\n' "$(hostname)"
    printf 'App directory: %s\n' "$APPDIR"
    printf '\nRunning user service:\n'
    systemctl --user show bouquetmat.service \
      -p LoadState -p ActiveState -p MainPID -p ExecMainStartTimestamp -p FragmentPath 2>/dev/null || true
    printf '\nFiles:\n'
} > "$TEMP/manifest.txt"

count=0
for name in \
    Panel.py \
    Panel_backend_merged.py \
    Panel_backend_combined.py \
    backend_client.py \
    Ads.py \
    wa_module_health_bridge.py \
    bouquetmat_launcher.py \
    requirements.txt
do
    source="$APPDIR/$name"
    if [[ -f "$source" && -r "$source" ]]; then
        cp -- "$source" "$TEMP/source/$name"
        chmod 600 "$TEMP/source/$name"
        printf '%s | %s bytes | ' "$name" "$(wc -c < "$source")" >> "$TEMP/manifest.txt"
        sha256sum "$TEMP/source/$name" >> "$TEMP/manifest.txt"
        count=$((count+1))
    fi
done
(( count > 0 )) || { echo 'No expected source files found.' >&2; exit 1; }

tar -C "$TEMP" -czf "$OUT" manifest.txt source
chmod 600 "$OUT"
printf '\nCreated local archive: %s\nFiles captured: %s\n' "$OUT" "$count"
printf 'No application files or services were changed.\n'
printf 'CAUTION: Original source may contain credentials; do NOT commit this archive to a public GitHub repository.\n'
