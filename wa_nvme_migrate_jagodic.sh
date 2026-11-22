#!/usr/bin/env bash
# Jagodic Raspberry Pi 5: copy current microSD installation to the existing NVMe partitions.
# CAUTION: Overwrites the old NVMe OS. Never overwrites the source microSD.
set -Eeuo pipefail

MODE="${1:-}"
if [[ "$MODE" != prepare && "$MODE" != finalize ]]; then
  echo "Usage: sudo bash $0 prepare|finalize" >&2; exit 2
fi
if [[ "$EUID" -ne 0 ]]; then echo "Run with sudo." >&2; exit 2; fi

R=/mnt/wa_nvme_root_check
B=/mnt/wa_nvme_boot_check
SRC_APP=/home/wa_stopshop_mb/WA/Main_program/Fis
NVME_BOOT_ID=1c3522b0-a047-4064-8686-a7b5983cd3dd
NVME_ROOT_ID=42ce440e-87cf-4e02-b67e-cb2ef5fdbac9
SD_BOOT_ID=fbded642-01
SD_ROOT_ID=fbded642-02

[[ "$(findmnt -no SOURCE /)" == /dev/mmcblk0p2 ]] || { echo 'ABORT: / is not microSD /dev/mmcblk0p2' >&2; exit 1; }
[[ "$(findmnt -no SOURCE /boot/firmware)" == /dev/mmcblk0p1 ]] || { echo 'ABORT: active boot is not microSD' >&2; exit 1; }
[[ "$(blkid -s PARTUUID -o value /dev/nvme0n1p1)" == "$NVME_BOOT_ID" ]] || { echo 'ABORT: unexpected SSD boot partition' >&2; exit 1; }
[[ "$(blkid -s PARTUUID -o value /dev/nvme0n1p2)" == "$NVME_ROOT_ID" ]] || { echo 'ABORT: unexpected SSD root partition' >&2; exit 1; }
[[ -f "$SRC_APP/Panel_backend_merged.py" ]] || { echo 'ABORT: kiosk source missing' >&2; exit 1; }
grep -q WA_R3_PI_RESERVE_V2 "$SRC_APP/Panel_backend_merged.py" || { echo 'ABORT: reservation collection fix missing' >&2; exit 1; }
grep -q WA_R3_CANCEL_SYNC_V1 "$SRC_APP/Panel_backend_merged.py" || { echo 'ABORT: reservation cancellation fix missing' >&2; exit 1; }
if [[ "$MODE" == finalize ]]; then
  if [[ -f /run/user/1000/systemd/user/bouquetmat.service ]]; then
    echo 'INFO: runtime mask file present'
  fi
  if pgrep -u pi -f '[/]home/wa_stopshop_mb/WA/Main_program/Fis/bouquetmat_launcher.py' >/dev/null; then
    echo 'ABORT: kiosk process still running; stop and runtime-mask bouquetmat.service first.' >&2; exit 1
  fi
fi

mkdir -p "$R" "$B"
prepare_mount() {
  local dev="$1" dest="$2" type="$3"
  if mountpoint -q "$dest"; then
    if [[ "$(findmnt -nro SOURCE --target "$dest")" != "$dev" ]]; then
      echo "ABORT: unexpected mount at $dest" >&2; exit 1
    fi
    if ! findmnt -nro OPTIONS --target "$dest" | grep -Eq '(^|,)rw(,|$)'; then
      umount "$dest"
      mount -t "$type" -o rw "$dev" "$dest"
    fi
  else
    mount -t "$type" -o rw "$dev" "$dest"
  fi
  [[ "$(findmnt -nro SOURCE --target "$dest")" == "$dev" ]] || exit 1
}
prepare_mount /dev/nvme0n1p2 "$R" ext4
prepare_mount /dev/nvme0n1p1 "$B" vfat

[[ ! "$R" -ef / ]] || { echo 'ABORT: destination is source' >&2; exit 1; }
echo "=== ${MODE^^} copy: / (microSD) -> $R (NVMe) ==="
rsync -aHAXx --numeric-ids --delete --info=progress2 \
  --exclude='/dev/***' \
  --exclude='/proc/***' \
  --exclude='/sys/***' \
  --exclude='/run/***' \
  --exclude='/tmp/***' \
  --exclude='/mnt/***' \
  --exclude='/media/***' \
  --exclude='/boot/firmware/***' \
  --exclude='/lost+found/***' \
  --exclude='/var/tmp/***' \
  --exclude='/var/swap' \
  --exclude='/swapfile' \
  / "$R/"

echo '=== Copy Raspberry Pi boot files to NVMe ==='
rsync -rt --delete --modify-window=1 /boot/firmware/ "$B/"

# Adjust boot locations only in the copied SSD installation.
export WA_NVME_ROOT="$R" WA_NVME_BOOT="$B" \
       WA_SD_BOOT_ID="$SD_BOOT_ID" WA_SD_ROOT_ID="$SD_ROOT_ID" \
       WA_NVME_BOOT_ID="$NVME_BOOT_ID" WA_NVME_ROOT_ID="$NVME_ROOT_ID"
python3 - <<'PY'
import os
from pathlib import Path
root = Path(os.environ['WA_NVME_ROOT'])
boot = Path(os.environ['WA_NVME_BOOT'])
sd_root = os.environ['WA_SD_ROOT_ID']
sd_boot = os.environ['WA_SD_BOOT_ID']
nvme_root = os.environ['WA_NVME_ROOT_ID']
nvme_boot = os.environ['WA_NVME_BOOT_ID']
cmd = boot / 'cmdline.txt'
fstab = root / 'etc/fstab'
cmdtext = cmd.read_text()
assert cmdtext.count('root=PARTUUID=' + sd_root) == 1, 'Unexpected source cmdline root setting'
cmd.write_text(cmdtext.replace('root=PARTUUID=' + sd_root, 'root=PARTUUID=' + nvme_root, 1))
fstext = fstab.read_text()
assert fstext.count('PARTUUID=' + sd_boot) == 1, 'Unexpected fstab boot entry'
assert fstext.count('PARTUUID=' + sd_root) == 1, 'Unexpected fstab root entry'
fstab.write_text(fstext.replace('PARTUUID=' + sd_boot, 'PARTUUID=' + nvme_boot, 1)
                      .replace('PARTUUID=' + sd_root, 'PARTUUID=' + nvme_root, 1))
print('NVMe cmdline and fstab updated (microSD untouched).')
PY

# Verify core program and the two boot references.
cmp -s "$SRC_APP/Panel_backend_merged.py" "$R$SRC_APP/Panel_backend_merged.py" || { echo 'ABORT: kiosk copy mismatch' >&2; exit 1; }
[[ -s "$B/config.txt" ]] || { echo 'ABORT: SSD config.txt missing' >&2; exit 1; }
find "$B" -maxdepth 1 -type f -name 'kernel*.img' -size +0c -print -quit | grep -q . || { echo 'ABORT: SSD kernel image missing' >&2; exit 1; }
grep -q "root=PARTUUID=$NVME_ROOT_ID" "$B/cmdline.txt" || { echo 'ABORT: wrong SSD cmdline' >&2; exit 1; }
grep -q "PARTUUID=$NVME_BOOT_ID" "$R/etc/fstab" || { echo 'ABORT: wrong SSD fstab boot' >&2; exit 1; }
grep -q "PARTUUID=$NVME_ROOT_ID" "$R/etc/fstab" || { echo 'ABORT: wrong SSD fstab root' >&2; exit 1; }
sync
printf 'PASS: %s sync and boot checks complete.\n' "$MODE"
printf 'SSD root: %s\nSSD boot: %s\n' "$R" "$B"
