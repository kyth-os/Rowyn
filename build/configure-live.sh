#!/usr/bin/env bash
# Runs only inside the Ubuntu live image build, not on the host.
set -euo pipefail
# shellcheck source=/dev/null
. /etc/os-release
[[ "$ID" == ubuntu && "$VERSION_ID" == 26.10 ]] || exit 1
: "${INSTALL_SOURCE_IMAGE:?}"
[[ "$INSTALL_SOURCE_IMAGE" =~ ^ghcr\.io/kyth-os/rowyn@sha256:[0-9a-f]{64}$ ]] || exit 1
digest="${INSTALL_SOURCE_IMAGE##*@}"
target_image="ghcr.io/kyth-os/rowyn:stable"
export INSTALL_TARGET_IMAGE="$target_image"
install -Dm755 /rowyn/ubuntu-live-preflight.py /usr/libexec/rowyn-live-preflight
# Retain the exact v2 verifier used by the publisher; verify download integrity.
curl --fail --location --retry 3 \
    https://github.com/sigstore/cosign/releases/download/v2.6.1/cosign-linux-amd64 \
    -o /usr/bin/kyth-cosign
printf '%s  %s\n' 064954c5d8c7e3b28188eee5b1727b31c411550bc5fefd41aa672d3c761d103a /usr/bin/kyth-cosign | sha256sum -c -
chmod 755 /usr/bin/kyth-cosign
export TUF_ROOT=/var/tmp/rowyn-build-tuf
mkdir -p "$TUF_ROOT" /usr/share/kyth
/usr/bin/kyth-cosign verify \
    --certificate-identity https://github.com/kyth-os/Rowyn/.github/workflows/build.yml@refs/heads/main \
    --certificate-oidc-issuer https://token.actions.githubusercontent.com "$INSTALL_SOURCE_IMAGE"
/usr/bin/kyth-cosign download signature "$INSTALL_SOURCE_IMAGE" > /var/tmp/rowyn-signatures.json
jq -cs --arg digest "$digest" '{digest:$digest, signatures:.}' /var/tmp/rowyn-signatures.json \
    > /usr/share/kyth/image.sig.bundle.json
bundle_hash="$(sha256sum /usr/share/kyth/image.sig.bundle.json)"
jq -n --arg digest "$digest" --arg source "$INSTALL_SOURCE_IMAGE" \
    --arg target "$target_image" \
    --arg bundle "sha256:${bundle_hash%% *}" \
    '{schema_version:1,release_digest:$digest,digest:$digest,source_image:$source,target_image:$target,signature:"verified",signature_digest:$bundle}' \
    > /usr/share/kyth/image-source.json
printf 'KYTH_SOURCE_IMAGE=%s\nKYTH_TARGET_IMAGE=%s\nKYTH_SOURCE_DIGEST=%s\n' \
    "$INSTALL_SOURCE_IMAGE" "$target_image" "$digest" > /etc/kyth-installer.env
chmod 644 /etc/kyth-installer.env

# bootc install runs later from this live system. Install the same immutable
# image policy and verification key before the installer can invoke bootc.
install -Dm644 /rowyn/update.pub /usr/share/rowyn/update.pub
install -Dm755 /rowyn/update-policy.sh /usr/libexec/rowyn-update-policy
/usr/libexec/rowyn-update-policy --root /

# Match the official Kubuntu 26.10 desktop live package set, not only the
# small installer bootstrap set. Seed its Snap applications for first boot.
apt-get update
# systemd-boot is already installed from the bootstrap stage, so its dpkg
# file triggers (interest in /usr/lib/shim and /usr/lib/grub) fire when this
# install lands shim-signed and the grub packages. Its postinst then runs
# remove_shim, which does esp_path="$(bootctl --print-esp-path)" under
# set -e: with no EFI system partition in the build container, bootctl
# exits non-zero, the assignment inherits that status, and the postinst
# dies, failing the whole install (seen in CI runs 40403162528 and
# 40416004281). Divert bootctl for this step only so --print-esp-path
# reports "no ESP" cleanly (empty output, exit 0, which remove_shim
# treats as nothing to do); every other invocation delegates to the real
# binary. The diversion is removed immediately after the install, and a
# failed install discards the layer anyway.
dpkg-divert --rename --add /usr/bin/bootctl
cat > /usr/bin/bootctl <<'EOF'
#!/bin/sh
for arg in "$@"; do
    if [ "$arg" = "--print-esp-path" ]; then
        exit 0
    fi
done
exec /usr/bin/bootctl.distrib "$@"
EOF
chmod 755 /usr/bin/bootctl
/usr/bin/python3 /rowyn/ubuntu-live-packages.py install-deb-packages \
    --manifest /usr/share/rowyn/kubuntu-desktop-live-26.10-amd64.manifest
rm -f /usr/bin/bootctl
dpkg-divert --rename --remove /usr/bin/bootctl
/usr/bin/python3 /rowyn/ubuntu-live-packages.py seed-snaps \
    --manifest /usr/share/rowyn/kubuntu-desktop-live-26.10-amd64.manifest --root /
rm -rf /var/lib/apt/lists/*

# Containers commonly bind-mount /etc/resolv.conf, so do not try to replace
# that path during the image build. Persist the live-only link as a tmpfiles
# rule and let systemd create it after the container mount is gone. Override
# systemd-resolved's vendor rule by basename so the replacement is not treated
# as a duplicate path and ignored.
install -d -m 755 /etc/tmpfiles.d
cat > /etc/tmpfiles.d/systemd-resolve.conf <<'EOF'
# ! applies this at boot; + replaces a stale regular file captured by the ISO.
L!+ /etc/resolv.conf - - - - /run/systemd/resolve/stub-resolv.conf
EOF
systemctl enable systemd-resolved.service

# Casper's account and the launcher's authenticated socket group must agree.
# The official Ubuntu container can contain an unused uid-1000 ubuntu account;
# Casper requires its configured session user to own uid 1000.
if getent passwd ubuntu >/dev/null; then userdel ubuntu; fi
useradd --uid 1000 --create-home --shell /bin/bash --groups audio,video,render liveuser
/rowyn/kde-dark-defaults.sh --root / --user liveuser --home /home/liveuser
/rowyn/kde-wallpaper-defaults.sh --root /
# The live installer session must not lock itself while unattended. Keep this
# in the selected live user's home, not the shared payload defaults: the
# installed Rowyn session retains KDE's normal locking behavior.
install -d -m 700 -o liveuser -g liveuser /home/liveuser/.config
cat > /home/liveuser/.config/kscreenlockerrc <<'EOF'
[Daemon]
Autolock=false
LockOnResume=false
EOF
chown liveuser:liveuser /home/liveuser/.config/kscreenlockerrc
chmod 600 /home/liveuser/.config/kscreenlockerrc
cat > /etc/casper.conf <<'EOF'
export USERNAME="liveuser"
export USERFULLNAME="Rowyn Live Session"
export HOST="rowyn-live"
export BUILD_SYSTEM="Ubuntu"
EOF
# Ubuntu's packaged keyfile policy otherwise marks every Ethernet device
# unmanaged. Keep NetworkManager authoritative for live wired DHCP and Wi-Fi;
# netplan persists the NetworkManager renderer and the wired DHCP match.
/usr/bin/python3 /rowyn/ubuntu-live-network.py --root /
netplan generate --root-dir /
mkdir -p /etc/sddm.conf.d /etc/xdg/autostart /etc/sudoers.d
cat > /etc/sddm.conf.d/20-rowyn-live.conf <<'EOF'
[Autologin]
User=liveuser
Session=plasma.desktop
Relogin=false
EOF
# Session is selected from the actual Ubuntu package, not a Fedora session name.
if [[ -f /usr/share/xsessions/plasmax11.desktop ]]; then
    sed -i 's/Session=plasma.desktop/Session=plasmax11.desktop/' /etc/sddm.conf.d/20-rowyn-live.conf
elif [[ ! -f /usr/share/wayland-sessions/plasma.desktop ]]; then
    echo 'ERROR: Ubuntu Plasma session file is missing' >&2; exit 1
fi
cat > /etc/xdg/autostart/rowyn-installer.desktop <<'EOF'
[Desktop Entry]
Type=Application
Name=Install Rowyn
Comment=Ubuntu live session; changes are lost at reboot. UEFI, Secure Boot disabled, whole-disk plaintext only.
Exec=/usr/bin/kyth-launch-installer
Terminal=false
EOF
cat > /etc/sudoers.d/rowyn-live <<'EOF'
Defaults:liveuser env_keep += "DISPLAY WAYLAND_DISPLAY XAUTHORITY XDG_RUNTIME_DIR DBUS_SESSION_BUS_ADDRESS XDG_SESSION_TYPE LIBGL_ALWAYS_SOFTWARE GALLIUM_DRIVER MESA_LOADER_DRIVER_OVERRIDE QT_QUICK_BACKEND"
liveuser ALL=(root) NOPASSWD: /usr/bin/kyth-launch-installer ""
EOF
chmod 440 /etc/sudoers.d/rowyn-live
visudo -c -f /etc/sudoers.d/rowyn-live
# Casper's own 25adduser grants broad sudo. Revoke it inside the initramfs,
# before switching root or starting any console/desktop session.
cat > /usr/share/initramfs-tools/scripts/casper-bottom/99rowyn-permissions <<'EOF'
#!/bin/sh
case "${1:-}" in prereqs) exit 0;; esac
rm -f /root/etc/sudoers.d/casper
chroot /root /usr/sbin/usermod -G audio,video,render liveuser
EOF
chmod 755 /usr/share/initramfs-tools/scripts/casper-bottom/99rowyn-permissions
# Casper's 25adduser runs later and may recreate session-user state. Reassert
# the live-only KDE config after Casper so it cannot replace the chosen file.
cat > /usr/share/initramfs-tools/scripts/casper-bottom/99rowyn-screenlock <<'EOF'
#!/bin/sh
case "${1:-}" in prereqs) exit 0;; esac
mkdir -p /root/home/liveuser/.config
chroot /root chmod 700 /home/liveuser/.config
chroot /root chown liveuser:liveuser /home/liveuser/.config
cat > /root/home/liveuser/.config/kscreenlockerrc <<'CONFIG'
[Daemon]
Autolock=false
LockOnResume=false
CONFIG
chroot /root chown liveuser:liveuser /home/liveuser/.config/kscreenlockerrc
chmod 600 /root/home/liveuser/.config/kscreenlockerrc
EOF
chmod 755 /usr/share/initramfs-tools/scripts/casper-bottom/99rowyn-screenlock
# Casper does not own resolver policy, but its initramfs can expose the root
# before the first normal systemd tmpfiles run. Reapply and verify the live
# resolver link as the last Casper-bottom action; the installed payload is not
# involved in this live-only hook.
cat > /usr/share/initramfs-tools/scripts/casper-bottom/99rowyn-resolver <<'EOF'
#!/bin/sh
case "${1:-}" in prereqs) exit 0;; esac
chroot /root /usr/bin/systemd-tmpfiles --create --boot /etc/tmpfiles.d/systemd-resolve.conf
test -L /root/etc/resolv.conf
test "$(readlink /root/etc/resolv.conf)" = /run/systemd/resolve/stub-resolv.conf
EOF
chmod 755 /usr/share/initramfs-tools/scripts/casper-bottom/99rowyn-resolver
# Live-only differences: the installed signed payload is never modified here.
mkdir -p /etc/systemd/system/display-manager.service.d
cat > /etc/systemd/system/display-manager.service.d/rowyn-permissions.conf <<'EOF'
[Service]
ExecStartPre=/usr/bin/rm -f /etc/sudoers.d/casper
ExecStartPre=/usr/sbin/usermod -G audio,video,render liveuser
EOF
systemctl disable gdm3.service >/dev/null 2>&1 || true
systemctl enable sddm.service NetworkManager.service
systemctl set-default graphical.target
printf 'en_US.UTF-8 UTF-8\n' > /etc/locale.gen
locale-gen
printf 'LANG=en_US.UTF-8\n' > /etc/default/locale
mkdir -p /etc/systemd/journald.conf.d
printf '[Journal]\nRuntimeMaxUse=64M\nSystemMaxUse=128M\n' > /etc/systemd/journald.conf.d/rowyn-live.conf
# The live kernel's initramfs is Casper/initramfs-tools, never payload dracut.
update-initramfs -u -k all
kernel="$(find /lib/modules -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort -V | tail -n1)"
test -s "/boot/vmlinuz-${kernel}"
test -s "/boot/initrd.img-${kernel}"
lsinitramfs "/boot/initrd.img-${kernel}" > /var/tmp/rowyn-initrd-list
# Keep the listing in build evidence, but fail if Casper is absent.
grep -q 'scripts/casper' /var/tmp/rowyn-initrd-list
grep -q 'scripts/casper-bottom/99rowyn-permissions' /var/tmp/rowyn-initrd-list
grep -q 'scripts/casper-bottom/99rowyn-screenlock' /var/tmp/rowyn-initrd-list
grep -q 'scripts/casper-bottom/99rowyn-resolver' /var/tmp/rowyn-initrd-list
/usr/libexec/rowyn-live-preflight --runtime-only
# Record apt packages for diagnosis; these packages are deliberately not EVR-pinned.
dpkg-query -W > /usr/share/doc/rowyn-installer/live-packages.tsv
rm -rf /var/tmp/rowyn-build-tuf /var/tmp/rowyn-signatures.json
rm -f /usr/sbin/policy-rc.d
: > /etc/machine-id
