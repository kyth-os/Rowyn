#!/usr/bin/env bash
# Install Rowyn's small desktop tuning defaults: per-device I/O schedulers,
# journald size caps, Baloo indexing off, and low-latency PipeWire that is
# applied only while a game runs under GameMode.
#
# Everything lives in vendor locations (/usr/lib, /etc/xdg) so users and
# administrators can override it. /etc/gamemode.ini is the one exception: it
# is GameMode's only system-wide override location (the package's own
# defaults in /usr/share/gamemode/gamemode.ini are loaded first and merged,
# so this file carries only the [custom] hooks). Idempotent: each file is
# rewritten in full.
set -euo pipefail

root=/
while (($#)); do
    case "$1" in
        --root)
            root=${2:?--root requires a path}
            shift 2
            ;;
        *)
            echo "usage: $0 [--root PATH]" >&2
            exit 2
            ;;
    esac
done

if [[ "$root" != / ]]; then
    root="${root%/}"
fi
root_path() {
    if [[ "$1" == /* ]]; then
        printf '%s%s' "$root" "$1"
    else
        printf '%s/%s' "$root" "$1"
    fi
}

# write_file PATH MODE: stdin becomes the file.
write_file() {
    local path
    path=$(root_path "$1")
    install -d "$(dirname "$path")"
    cat > "$path"
    chmod "$2" "$path"
}

# Ubuntu's udev/systemd ship no I/O scheduler rules. Only sd* whole disks
# (SATA/SAS/USB) are matched, so nvme (kernel default "none"), virtio (vd*),
# loop, ram, zram, dm and mmc devices are never touched.
write_file /usr/lib/udev/rules.d/60-rowyn-ioschedulers.rules 0644 <<'EOF2'
# Rowyn I/O scheduler defaults by device type. Override with an
# /etc/udev/rules.d/60-rowyn-ioschedulers.rules of the same name.
ACTION=="remove", GOTO="rowyn_ioscheduler_end"
SUBSYSTEM!="block", GOTO="rowyn_ioscheduler_end"
ENV{DEVTYPE}!="disk", GOTO="rowyn_ioscheduler_end"
# Only SCSI-layer disks (SATA/SAS/USB); never nvme, vd, loop, ram, zram, dm.
KERNEL!="sd[a-z]|sd[a-z][a-z]", GOTO="rowyn_ioscheduler_end"

# Rotational disks: bfq keeps the desktop responsive under heavy seeking.
ATTR{queue/rotational}=="1", ATTR{queue/scheduler}="bfq"
# SATA/SAS SSDs: mq-deadline has low overhead and no seek logic to waste.
ATTR{queue/rotational}=="0", ATTR{queue/scheduler}="mq-deadline"

LABEL="rowyn_ioscheduler_end"
EOF2

write_file /usr/lib/systemd/journald.conf.d/90-rowyn.conf 0644 <<'EOF2'
# Rowyn journal size caps. Override in /etc/systemd/journald.conf.d/.
[Journal]
SystemMaxUse=1G
SystemMaxFileSize=64M
RuntimeMaxUse=128M
MaxRetentionSec=1month
EOF2

# KDE reads /etc/xdg through XDG_CONFIG_DIRS; a user's ~/.config/baloofilerc
# overrides this, so indexing can be re-enabled in System Settings.
write_file /etc/xdg/baloofilerc 0644 <<'EOF2'
# Rowyn: no background file indexing by default. Re-enable in
# System Settings > Search > File Search.
[Basic Settings]
Indexing-Enabled=false
EOF2

write_file /usr/libexec/rowyn-gamemode-audio 0755 <<'EOF2'
#!/bin/sh
# Called by GameMode (start/end hooks). GameMode runs as a systemd user
# service, so this executes inside the game user's session and pw-metadata
# reaches that user's PipeWire. The default quantum is kept for normal use
# (Bluetooth needs it); 256 samples at 48 kHz is about 5.3 ms while gaming.
# Always exits 0: audio tuning must never disturb a game launch.
case "${1:-}" in
    start) quantum=256 ;;
    end) quantum=0 ;;
    *) exit 0 ;;
esac
command -v pw-metadata >/dev/null 2>&1 || exit 0
if command -v timeout >/dev/null 2>&1; then
    timeout 5 pw-metadata -n settings 0 clock.force-quantum "$quantum" >/dev/null 2>&1 || true
else
    pw-metadata -n settings 0 clock.force-quantum "$quantum" >/dev/null 2>&1 || true
fi
exit 0
EOF2

# GameMode loads /usr/share/gamemode/gamemode.ini, then /etc/gamemode.ini,
# merging per key, so only the [custom] hooks are needed here.
write_file /etc/gamemode.ini 0644 <<'EOF2'
# Rowyn: low-latency PipeWire only while a game runs under GameMode.
# The package defaults in /usr/share/gamemode/gamemode.ini still apply.
[custom]
start=/usr/libexec/rowyn-gamemode-audio start
end=/usr/libexec/rowyn-gamemode-audio end
EOF2
