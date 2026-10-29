#!/usr/bin/env bash
# Decide, once per boot, which kernel driver binds an NVIDIA GPU.
#
# Rowyn ships one image for everyone: Canonical-signed prebuilt NVIDIA open
# kernel modules AND the in-tree nouveau driver. Neither is in the initramfs
# and neither is blacklisted statically; this helper looks at the PCI bus in
# the real root and writes a runtime-only modprobe.d file that picks one:
#
#   a supported NVIDIA GPU is present   -> nouveau is blacklisted, nvidia binds
#   only unsupported NVIDIA GPUs        -> the nvidia modules are blacklisted,
#                                          nouveau binds
#   no NVIDIA display device            -> nothing is written (a stale file
#                                          from an earlier run is removed)
#
# "Supported" means the PCI device ID is in the list generated at image build
# time from the supported-gpus.json that NVIDIA ships with the driver
# (shared/nvidia-open-devids.py): current-branch entries with the
# `kernelopen` feature, i.e. Turing and newer (GSP firmware). The open
# modules carry PCI class aliases for every NVIDIA display device, so
# without the blacklist they would claim unsupported GPUs and fail to
# probe them; the blacklist is what makes nouveau win there.
#
# The output lives in /run and is never persisted. The helper never fails a
# boot: unreadable sysfs entries are skipped, and a missing ID list means
# "treat every NVIDIA GPU as unsupported" (nouveau drives all of them).
#
# Options (all optional; the defaults are what the systemd unit uses):
#   --sysfs-root PATH     directory that plays the role of /sys
#                         (devices are read from PATH/bus/pci/devices/*)
#   --output PATH         file to write (default /run/modprobe.d/rowyn-gpu.conf)
#   --supported-ids PATH  device-ID list (default /usr/lib/rowyn/nvidia-open-devids)
set -euo pipefail

sysfs_root=/sys
output=/run/modprobe.d/rowyn-gpu.conf
supported_ids=/usr/lib/rowyn/nvidia-open-devids

usage() {
    echo "usage: $0 [--sysfs-root PATH] [--output PATH] [--supported-ids PATH]" >&2
    exit 2
}

while (($#)); do
    ((($# >= 2))) || usage
    case "$1" in
        --sysfs-root) sysfs_root=$2 ;;
        --output) output=$2 ;;
        --supported-ids) supported_ids=$2 ;;
        *) usage ;;
    esac
    shift 2
done

# read_attr FILE: first line, lowercased, whitespace-free; empty if unreadable.
read_attr() {
    local value=
    if [[ -r "$1" ]]; then
        IFS= read -r value < "$1" || true
    fi
    value=${value,,}
    printf '%s' "${value//[[:space:]]/}"
}

# is_supported ID4: is the 4-digit hex device ID in the supported list?
# The list is read lazily, once, and only if an NVIDIA display device exists.
ids_loaded=0
declare -A supported=()
load_ids() {
    local line
    ids_loaded=1
    if [[ ! -r "$supported_ids" ]]; then
        echo "rowyn-gpu-select: $supported_ids is missing; treating NVIDIA GPUs as unsupported (nouveau)" >&2
        return 0
    fi
    while IFS= read -r line || [[ -n "$line" ]]; do
        line=${line%%#*}
        line=${line//[[:space:]]/}
        line=${line,,}
        line=${line#0x}
        [[ "$line" =~ ^[0-9a-f]{4}$ ]] && supported[$line]=1
    done < "$supported_ids"
    return 0
}
is_supported() {
    ((ids_loaded)) || load_ids
    [[ -n "${supported[$1]:-}" ]]
}

nvidia_seen=0
nvidia_supported=0
for dev in "$sysfs_root"/bus/pci/devices/*; do
    [[ -d "$dev" ]] || continue
    [[ "$(read_attr "$dev/vendor")" == 0x10de ]] || continue
    class=$(read_attr "$dev/class")
    # Display controllers only: 0x03xxxx (VGA 0x0300, 3D 0x0302, ...). The
    # HDMI audio, USB-C and other functions of the same card are not GPUs.
    [[ "$class" == 0x03???? ]] || continue
    nvidia_seen=1
    id=$(read_attr "$dev/device")
    id=${id#0x}
    if is_supported "$id"; then
        nvidia_supported=1
    fi
done

if ((!nvidia_seen)); then
    rm -f -- "$output"
    exit 0
fi

mkdir -p -- "$(dirname -- "$output")"
tmp=$(mktemp "${output}.XXXXXX")
trap 'rm -f -- "$tmp"' EXIT
if ((nvidia_supported)); then
    cat > "$tmp" <<'EOF2'
# Written by rowyn-gpu-select at boot; lives in /run, never persisted.
# A supported NVIDIA GPU is present: the open nvidia modules drive it.
# (nova is the in-kernel Rust NVIDIA driver; keep it out of nvidia's way.)
blacklist nouveau
options nouveau modeset=0
blacklist nova_core
blacklist nova_drm
EOF2
else
    cat > "$tmp" <<'EOF2'
# Written by rowyn-gpu-select at boot; lives in /run, never persisted.
# Only NVIDIA GPUs the open modules do not support are present (pre-Turing):
# keep the nvidia modules away so nouveau binds.
blacklist nvidia
blacklist nvidia_drm
blacklist nvidia_modeset
blacklist nvidia_uvm
EOF2
fi
chmod 0644 "$tmp"
mv -f -- "$tmp" "$output"
trap - EXIT
