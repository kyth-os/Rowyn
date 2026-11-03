#!/usr/bin/env bash

set -xeuo pipefail

mkdir -p /usr/lib/dracut/dracut.conf.d/
printf "systemdsystemconfdir=/etc/systemd/system\nsystemdsystemunitdir=/usr/lib/systemd/system\n" | tee /usr/lib/dracut/dracut.conf.d/30-bootcrew-fix-bootc-module.conf
printf 'reproducible=yes\nhostonly=no\ncompress=zstd\nadd_dracutmodules+=" bootc plymouth "' | tee "/usr/lib/dracut/dracut.conf.d/30-bootcrew-bootc-container-build.conf"
# GPU drivers are chosen in the real root by rowyn-gpu-select (nouveau for
# older NVIDIA GPUs, the nvidia open modules for supported ones). Neither may
# be in the initramfs: it cannot run that decision, and the boot splash
# renders on the firmware framebuffer (simpledrm/efifb) until the real root
# loads the right driver. dracut's drm module would otherwise fold nouveau in.
printf 'omit_drivers+=" nouveau nvidia nvidia_drm nvidia_modeset nvidia_uvm nvidia_peermem nvidia-drm nvidia-modeset nvidia-uvm nvidia-peermem "\n' | tee /usr/lib/dracut/dracut.conf.d/40-rowyn-gpu-omit.conf
kernel_dir="$(find /usr/lib/modules -mindepth 1 -maxdepth 1 -type d | sort -V | tail -n 1)"
dracut --force "${kernel_dir}/initramfs.img"

# The boot splash lives or dies here: Plymouth can only draw the Rowyn
# theme at boot if dracut's plymouth module actually folded the daemon,
# the theme, its logo, and the Pango label renderer/font into this initramfs
# (the script plugin's redraw path crashes if text rendering is unavailable).
# Fail the build rather than ship a silently splashless boot.
initrd_listing="$(lsinitrd "${kernel_dir}/initramfs.img")"
for required in \
    "usr/sbin/plymouthd" \
    "usr/share/plymouth/themes/rowyn/rowyn.plymouth" \
    "usr/share/plymouth/themes/rowyn/rowyn.script" \
    "usr/share/plymouth/themes/rowyn/rowyn-logo.png"; do
    if ! grep -Fq "$required" <<<"${initrd_listing}"; then
        echo "initramfs is missing boot-splash content: ${required}" >&2
        exit 1
    fi
done

# Ubuntu's Plymouth initramfs hook includes the label renderer and Ubuntu
# fonts only when plymouth-label is installed. Require both: without them
# ply_console_viewer_preferred() disables the script plugin's console viewer,
# whose redraw path dereferences a null viewer while drawing the splash.
if ! grep -Fq '/plymouth/label-pango.so' <<<"${initrd_listing}"; then
    echo "initramfs is missing Plymouth's Pango label renderer (plymouth-label):" >&2
    exit 1
fi
if ! grep -Eq 'usr/share/fonts/truetype/ubuntu/[^[:space:]]+\.ttf' <<<"${initrd_listing}"; then
    echo "initramfs is missing Ubuntu fonts required by Plymouth's Pango label renderer:" >&2
    exit 1
fi

# Same contract for the GPU drivers: the build fails if nouveau or any
# nvidia module made it into the initramfs despite omit_drivers.
if grep -Eq '(^|[ /])(nouveau|nvidia[A-Za-z0-9_-]*)\.ko' <<<"${initrd_listing}"; then
    echo "initramfs contains a GPU driver module that must load from the real root:" >&2
    grep -E '(^|[ /])(nouveau|nvidia[A-Za-z0-9_-]*)\.ko' <<<"${initrd_listing}" >&2
    exit 1
fi
