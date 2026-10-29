#!/usr/bin/env bash

set -xeuo pipefail

mkdir -p /usr/lib/dracut/dracut.conf.d/
printf "systemdsystemconfdir=/etc/systemd/system\nsystemdsystemunitdir=/usr/lib/systemd/system\n" | tee /usr/lib/dracut/dracut.conf.d/30-bootcrew-fix-bootc-module.conf
printf 'reproducible=yes\nhostonly=no\ncompress=zstd\nadd_dracutmodules+=" bootc plymouth "' | tee "/usr/lib/dracut/dracut.conf.d/30-bootcrew-bootc-container-build.conf"
kernel_dir="$(find /usr/lib/modules -maxdepth 1 -type d | grep -v -E "*.img" | tail -n 1)"
dracut --force "${kernel_dir}/initramfs.img"

# The boot splash lives or dies here: Plymouth can only draw the Rowyn
# theme at boot if dracut's plymouth module actually folded the daemon,
# the theme, and its logo into this initramfs (it reads the theme from
# /etc/plymouth/plymouthd.conf, installed by plymouth-theme.sh earlier in
# the build). Fail the build rather than ship a silently splashless boot.
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
