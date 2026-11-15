#!/usr/bin/env bash
# Bouquetmat Raspberry Pi diagnostic v1 (read-only, no restarts, no writes to application files)
# Intended to run as the SAME non-root user that runs the Kivy kiosk (usually pi).
# No elevated privileges required. Creates a local report in user's home directory.
set -u
umask 077
STAMP="$(date +%Y%m%d_%H%M%S)"
OUT="${1:-$HOME/bouquetmat_diagnostic_${STAMP}.txt}"
APPDIR="/home/wa_stopshop_mb/WA/Main_program/Fis"
JOURNAL_TMP="$(mktemp)"
JOURNAL_SYS_TMP="$(mktemp)"
trap 'rm -f "$JOURNAL_TMP" "$JOURNAL_SYS_TMP"' EXIT

command -v journalctl >/dev/null 2>&1 && journalctl --user -u bouquetmat.service --since '2 hours ago' --no-pager -o short-iso > "$JOURNAL_TMP" 2>/dev/null || true
command -v journalctl >/dev/null 2>&1 && journalctl -u bouquetmat.service --since '2 hours ago' --no-pager -o short-iso > "$JOURNAL_SYS_TMP" 2>/dev/null || true
if [[ ! -s "$JOURNAL_TMP" && -s "$JOURNAL_SYS_TMP" ]]; then
  cp "$JOURNAL_SYS_TMP" "$JOURNAL_TMP"
fi

section() { printf '\n========== %s ==========\n' "$1"; }
try() { "$@" 2>&1 || true; }
counts() {
  local label="$1" pattern="$2" file="$3" count
  count="$(grep -Eic "$pattern" "$file" 2>/dev/null || true)"
  printf '%-30s %s\n' "$label" "${count:-0}"
}
redact() {
 sed -E \
  -e 's/([Tt][Oo][Kk][Ee][Nn]|[Ss][Ee][Cc][Rr][Ee][Tt]|[Pp][Aa][Ss][Ss][Ww][Oo][Rr][Dd]|[Aa][Pp][Ii][_-]?[Kk][Ee][Yy]|[Aa][Uu][Tt][Hh][Oo][Rr][Ii][Zz][Aa][Tt][Ii][Oo][Nn])([=: ]+)[^ ,;]+/\1\2[REDACTED]/g' \
  -e 's/([Bb]earer )[A-Za-z0-9._-]+/\1[REDACTED]/g' \
  -e 's/([A-Za-z0-9_+-]+)@([A-Za-z0-9.-]+\.[A-Za-z]{2,})/[EMAIL-REDACTED]/g'
}

{
  section 'BASIC / READ-ONLY DIAGNOSTIC'
  printf 'Generated: %s\n' "$(date -Is)"
  printf 'Logged in as: %s\n' "$(id -un)"
  printf 'Hostname: %s\n' "$(hostname)"
  printf 'Kernel: '; uname -r
  if [[ -r /proc/device-tree/model ]]; then printf 'Hardware: '; tr -d '\0' </proc/device-tree/model; printf '\n'; fi
  if [[ -r /etc/os-release ]]; then grep -E '^(PRETTY_NAME|VERSION_ID)=' /etc/os-release; fi
  printf 'Uptime: '; uptime
  printf 'IPs: '; hostname -I 2>/dev/null || true

  section 'MEMORY / DISK / CPU / POWER'
  try free -h
  try df -h / "$HOME"
  try df -i /
  command -v vcgencmd >/dev/null 2>&1 && try vcgencmd measure_temp
  command -v vcgencmd >/dev/null 2>&1 && try vcgencmd get_throttled
  if [[ -r /sys/class/thermal/thermal_zone0/temp ]]; then
    printf 'SoC temperature (milli C): '; cat /sys/class/thermal/thermal_zone0/temp
  fi
  if [[ -r /proc/pressure/cpu ]]; then printf 'CPU pressure: '; tr '\n' ' ' </proc/pressure/cpu; echo; fi
  if [[ -r /proc/pressure/memory ]]; then printf 'Memory pressure: '; tr '\n' ' ' </proc/pressure/memory; echo; fi
  if [[ -r /proc/pressure/io ]]; then printf 'IO pressure: '; tr '\n' ' ' </proc/pressure/io; echo; fi

  section 'TOP CPU USERS'
  ps -eo pid,ppid,stat,etime,%cpu,%mem,rss,comm --sort=-%cpu | head -n 20 || true
  section 'TOP RAM USERS'
  ps -eo pid,ppid,stat,etime,%cpu,%mem,rss,comm --sort=-rss | head -n 20 || true

  section 'KIOSK / ADS PROCESSES'
  ps -eo pid,ppid,stat,etime,%cpu,%mem,comm,args | grep -Ei '([P]anel\.py|[P]anel_backend_merged|[K]ivy|[A]ds\.py|[m]pv|[l]abwc|[k]anshi|[b]ouquetmat)' | cut -c1-220 | head -n 55 || true

  section 'BOUQUETMAT SERVICE STATUS (USER AND SYSTEM)'
  try systemctl --user show bouquetmat.service -p LoadState -p ActiveState -p SubState -p MainPID -p NRestarts -p ExecMainStartTimestamp -p FragmentPath -p DropInPaths
  try systemctl show bouquetmat.service -p LoadState -p ActiveState -p SubState -p MainPID -p NRestarts -p ExecMainStartTimestamp -p FragmentPath -p DropInPaths
  printf '\nAds service (user):\n'
  try systemctl --user show bouquetmat-ads.service -p LoadState -p ActiveState -p SubState -p MainPID -p NRestarts
  printf '\nWayland session(s):\n'
  try loginctl list-sessions --no-legend

  section 'SYNC / RESYNC / ERROR COUNTS - LAST 2 HOURS (SERVICE JOURNAL)'
  printf 'Service journal lines inspected: %s\n' "$(wc -l < "$JOURNAL_TMP")"
  counts 'items/sync requests' 'items/sync' "$JOURNAL_TMP"
  counts 'resync/re-sync/reconcile' 'resync|re-sync|reconcile' "$JOURNAL_TMP"
  counts 'LED/full sync' '(LED.*(sync|full)|full.*LED)' "$JOURNAL_TMP"
  counts 'timeouts/network errors' 'timed? ?out|connection reset|connection refused|network.*error|HTTP.*(4[0-9][0-9]|5[0-9][0-9])' "$JOURNAL_TMP"
  counts 'traceback/exceptions' 'traceback|exception|segmentation fault' "$JOURNAL_TMP"
  counts 'all error/warning mentions' 'error|warning|failed' "$JOURNAL_TMP"
  printf '\nFirst and last matching sync log timestamps (up to 4 each):\n'
  grep -Ei 'items/sync|resync|re-sync|reconcile|LED.*(sync|full)' "$JOURNAL_TMP" | head -n 4 | cut -c1-240 | redact || true
  echo '...'
  grep -Ei 'items/sync|resync|re-sync|reconcile|LED.*(sync|full)' "$JOURNAL_TMP" | tail -n 4 | cut -c1-240 | redact || true
  printf '\nLatest relevant warnings/errors (max 30):\n'
  grep -Ei 'error|warning|failed|traceback|exception|timed? ?out|connection reset|overload|blocked' "$JOURNAL_TMP" | tail -n 30 | cut -c1-240 | redact || true

  section 'KIVY LOG SUMMARY'
  KIVY_LOG_DIR="$HOME/.kivy/logs"
  if [[ -d "$KIVY_LOG_DIR" ]]; then
    KIVY_FILE="$(find "$KIVY_LOG_DIR" -maxdepth 1 -type f -name 'kivy_*.txt' -printf '%T@ %p\n' 2>/dev/null | sort -nr | head -n 1 | cut -d' ' -f2-)"
    if [[ -n "$KIVY_FILE" && -r "$KIVY_FILE" ]]; then
      printf 'Latest Kivy log: %s\n' "$KIVY_FILE"
      printf 'Size: '; du -h "$KIVY_FILE" || true
      counts 'Kivy resync/sync mentions' 'items/sync|resync|re-sync|reconcile' "$KIVY_FILE"
      counts 'Kivy warnings/errors' 'error|warning|failed|traceback|exception|timed? ?out' "$KIVY_FILE"
      grep -Ei 'error|warning|failed|traceback|exception|timed? ?out' "$KIVY_FILE" | tail -n 25 | cut -c1-240 | redact || true
    else
      echo 'No readable Kivy log found.'
    fi
  else
    echo 'No ~/.kivy/logs directory for this login.'
  fi

  section 'INPUT DEVICES / GRAPHICS'
  if [[ -r /proc/bus/input/devices ]]; then
    grep -E 'N: Name=|H: Handlers=' /proc/bus/input/devices | head -n 35 || true
  fi
  printf '\nDRM connectors:\n'
  for d in /sys/class/drm/card*-*/status; do
    [[ -r "$d" ]] && printf '%s: %s\n' "$d" "$(cat "$d")"
  done

  section 'KERNEL POWER / DISPLAY / USB ERRORS - LAST 2 HOURS'
  if command -v journalctl >/dev/null 2>&1; then
    journalctl -k --since '2 hours ago' --no-pager -o short-iso 2>/dev/null | grep -Ei 'under.?voltage|throttl|temperature|oom|out of memory|usb.*(disconnect|reset|error)|i2c.*(fail|error)|touch|drm.*(error|fail)|mmc.*(error|timeout)' | tail -n 40 | cut -c1-240 | redact || true
  fi

  section 'NETWORK / LISTENING PORTS'
  try ip -br addr
  if command -v ss >/dev/null 2>&1; then
    ss -lun 2>/dev/null | grep -E ':(5000|48555|48556|48557)([[:space:]]|$)' || true
  fi

  section 'APP FILES / DATES / HASHES (NO CODE OR SECRETS COPIED)'
  if [[ -d "$APPDIR" ]]; then
    for f in Panel.py Panel_backend_merged.py backend_client.py Ads.py; do
      if [[ -f "$APPDIR/$f" ]]; then
        stat -c '%n | %s bytes | modified %y' "$APPDIR/$f"
        sha256sum "$APPDIR/$f"
        printf 'Relevant line numbers in %s (sample):\n' "$f"
        grep -Ein 'resync|full[_ ]sync|sync.*item|item.*sync|Clock\.schedule|requests\.(get|post)|time\.sleep|asyncio\.run' "$APPDIR/$f" | cut -d: -f1 | head -n 60 | paste -sd ',' - || true
      fi
    done
  else
    echo "App directory not found: $APPDIR"
  fi

  section 'SYSTEM CLOCK'
  try timedatectl status
  command -v chronyc >/dev/null 2>&1 && try chronyc tracking

  section 'END'
  echo 'No application files were modified. No kiosk/motor/network service was restarted.'
  echo 'Review this report for private information before sharing it.'
} > "$OUT" 2>&1

printf '\nDiagnostic finished. Report: %s\n' "$OUT"
printf 'No services were restarted, and no app code was changed.\n'
