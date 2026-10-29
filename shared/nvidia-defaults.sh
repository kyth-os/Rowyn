#!/usr/bin/env bash
# Install Rowyn's static NVIDIA configuration and neutralize the parts of the
# Ubuntu driver packages that assume NVIDIA is the only GPU driver.
#
# - /usr/lib/modprobe.d/rowyn-nvidia.conf: module options. They only take
#   effect when an nvidia module loads, so they are harmless on AMD, Intel
#   and pre-Turing NVIDIA machines.
# - Removes the packages' static nouveau blacklist and KMS conffile. The
#   blacklist (`blacklist nouveau`, `alias nouveau off`, ...) would keep
#   nouveau from ever binding the older GPUs it must drive, and `alias ... off`
#   cannot be overridden from /run. The decision is made per boot by
#   rowyn-gpu-select instead. The /etc KMS conffile duplicates (and
#   conflicts with: TemporaryFilePath=/var) the options above.
# - Drop-ins that make the NVIDIA sleep/power units no-ops unless the nvidia
#   driver is actually loaded (/proc/driver/nvidia exists).
#
# Idempotent. Takes --root PATH like the other shared/*.sh helpers.
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

write_file /usr/lib/modprobe.d/rowyn-nvidia.conf 0644 <<'EOF2'
# Rowyn NVIDIA module options. Inert unless an nvidia module loads; which
# driver (nvidia or nouveau) binds is decided per boot by rowyn-gpu-select.

# Kernel modesetting and the framebuffer console on nvidia-drm: required for
# Wayland (KWin) and for a flicker-free hand-off from the boot splash.
options nvidia-drm modeset=1 fbdev=1

# Keep VRAM contents across suspend/resume (nvidia-suspend/resume.service
# drive it), spilling to /var/tmp, and let supported laptops power the GPU
# down between uses (fine-grained runtime PM).
options nvidia NVreg_PreserveVideoMemoryAllocations=1 NVreg_TemporaryFilePath=/var/tmp NVreg_DynamicPowerManagement=0x02
EOF2

# Packaged files that would pin the driver choice (see the header).
for stale in \
    /usr/lib/modprobe.d/nvidia-graphics-drivers.conf \
    /etc/modprobe.d/nvidia-graphics-drivers.conf \
    /etc/modprobe.d/nvidia-graphics-drivers-kms.conf; do
    rm -f -- "$(root_path "$stale")"
done

for unit in nvidia-suspend nvidia-resume nvidia-hibernate \
    nvidia-suspend-then-hibernate nvidia-powerd; do
    write_file "/usr/lib/systemd/system/$unit.service.d/10-rowyn-nvidia-only.conf" 0644 <<'EOF2'
# Do nothing on machines where the nvidia driver is not loaded (AMD, Intel,
# nouveau): the units ship in every image.
[Unit]
ConditionPathExists=/proc/driver/nvidia
EOF2
done
