#!/usr/bin/env bash
# Assemble a Casper filesystem and UEFI GRUB ISO inside the Ubuntu live image.
set -euo pipefail
# shellcheck source=/dev/null
. /etc/os-release
[[ "$ID" == ubuntu && "$VERSION_ID" == 26.10 ]] || exit 1
: "${SOURCE_TAG:?}"
[[ "$SOURCE_TAG" =~ ^[0-9a-f]{40}$ ]] || exit 1
test -d /output
work="$(mktemp -d /work/iso.XXXXXX)"
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/casper" "$work/boot/grub" "$work/.disk"
kernel="$(find /lib/modules -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort -V | tail -n1)"
cp "/boot/vmlinuz-${kernel}" "$work/casper/vmlinuz"
cp "/boot/initrd.img-${kernel}" "$work/casper/initrd"
printf 'Rowyn Ubuntu custom live installer %s\n' "$SOURCE_TAG" > "$work/.disk/info"
cat > "$work/boot/grub/grub.cfg" <<'EOF'
set timeout=10
set default=0
search --no-floppy --file --set=root /casper/vmlinuz
menuentry 'Install Rowyn (Ubuntu live; UEFI; Secure Boot must be disabled)' {
    linux /casper/vmlinuz boot=casper live-media-path=/casper username=liveuser hostname=rowyn-live quiet ---
    initrd /casper/initrd
}
menuentry 'Install Rowyn (basic graphics)' {
    linux /casper/vmlinuz boot=casper live-media-path=/casper username=liveuser hostname=rowyn-live nomodeset ---
    initrd /casper/initrd
}
EOF
# No payload layers are embedded. Installation downloads the verified digest.
mksquashfs / "$work/casper/filesystem.squashfs" -noappend -comp zstd -processors 4 \
    -wildcards -e 'proc/*' 'sys/*' 'dev/*' 'run/*' 'tmp/*' 'work' 'output' 'rowyn'
dpkg-query -W --showformat='${Package} ${Version}\n' > "$work/casper/filesystem.manifest"
/usr/bin/python3 /rowyn/ubuntu-live-packages.py verify-package-parity --manifest /usr/share/rowyn/kubuntu-desktop-live-26.10-amd64.manifest --installed-manifest "$work/casper/filesystem.manifest" --root /
du -sx --block-size=1 /usr /etc /var /home | awk '{n += $1} END {print n}' > "$work/casper/filesystem.size"
(cd "$work"; find . -type f ! -name md5sum.txt -print0 | sort -z | xargs -0 md5sum > md5sum.txt)
iso="/output/rowyn-custom-installer-${SOURCE_TAG}.iso"
# Ubuntu's grub-mkrescue builds the El Torito EFI image and hybrid disk layout.
# No signed shim is supplied: never claim Secure Boot qualification.
# -iso-level 3 is a mkisofs-emulation option: it must sit on the
# grub-mkrescue side of the -- separator (after --, xorriso runs in
# native command mode and rejects mkisofs options as unknown commands;
# both spellings failed there in CI runs 40423436389 / 40426938781).
# It allows files over 4 GiB in the image:
# the full Kubuntu live squashfs exceeds the ISO 9660 level-2 single-file
# limit and xorriso otherwise aborts (seen in CI run 40419853080).
grub-mkrescue -d /usr/lib/grub/x86_64-efi -o "$iso" "$work" -iso-level 3 -- -volid ROWYN_LIVE
xorriso -indev "$iso" -report_el_torito plain -report_system_area plain \
    > "${iso}.boot-report.txt" 2>&1
unsquashfs -s "$work/casper/filesystem.squashfs" > "${iso}.squashfs-report.txt"
test -s "$iso"
chmod 644 /output/*
