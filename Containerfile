# Rowyn — a bootc-based Ubuntu (latest release, non-LTS) shipping KDE Plasma.
#
# Structure adapted from Bootcrew's ubuntu-bootc (Apache-2.0; see NOTICE and
# the vendored scripts under shared/). Differences from upstream Bootcrew:
#   - base is the latest Ubuntu release (26.10), not 25.10 "questing"
#   - Rowyn identity in /usr/lib/os-release
#   - no baked-in login users: the base image's `ubuntu` account is
#     removed, and the only interactive account on an installed system is
#     the one the installer creates
#
# Build:  build/fetch-assets.sh        (one-time, needs network)
#         sudo podman build -t rowyn:spike .   (fully offline)
#
# All network inputs (pinned Rust toolchain tarball, bootc source with
# vendored crate deps) are fetched on the host into build-assets/ first —
# the build itself never touches the network.
ARG UBUNTU_TAG=26.10
ARG RUST_VERSION=1.99.0

FROM scratch AS ctx
COPY shared/ /shared
COPY keys/ /keys
COPY build-assets/ /assets

FROM docker.io/library/ubuntu:${UBUNTU_TAG} AS base

# --- builder: compile bootc from source -------------------------------------
FROM base AS builder
ARG RUST_VERSION
RUN --mount=type=tmpfs,dst=/tmp --mount=type=tmpfs,dst=/root --mount=type=tmpfs,dst=/boot \
    apt update -y && \
    apt install -y python3 git curl make build-essential go-md2man libzstd-dev pkgconf libostree-dev ostree xz-utils libclang-dev

WORKDIR /home/build
RUN --mount=type=bind,from=ctx,source=/,target=/ctx \
    mkdir -p /tmp/rust-dist && \
    tar -xJf "/ctx/assets/rust-${RUST_VERSION}-x86_64-unknown-linux-gnu.tar.xz" -C /tmp/rust-dist --strip-components=1 && \
    /tmp/rust-dist/install.sh --prefix=/opt/rust --without=rust-docs && \
    cp -a /ctx/assets/bootc /home/build/bootc && \
    /usr/bin/python3 /ctx/shared/bootc-sigpolicy-patch.py /home/build/bootc && \
    PATH=/opt/rust/bin:$PATH /ctx/shared/build.sh /home/build/bootc

# --- hub builder: compile Rowyn Hub from the pinned, ported Kyth source -----
# The ported tree is produced outside the build (CI runs
# build/ubuntu-hub-source.py against the pinned Kyth commit into hub-source/;
# see .github/workflows/build.yml) so this stage only compiles. Unlike the
# offline bootc stage it needs network for npm/cargo registries, mirroring
# upstream Kyth's own hub-web-builder stage.
# Layout mirrors that upstream stage on purpose: kyth-hub-web and
# kyth-shared-rs sit one level shallower than in the Kyth repo, so the shared
# crate's repo-relative include of build_files/exe-handler-apps.json lands at
# /build_files and the glib patch path lands at /third_party. Copy those two
# inputs to match rather than editing the pinned crate.
FROM base AS hub-builder
RUN --mount=type=tmpfs,dst=/tmp --mount=type=tmpfs,dst=/root \
    apt update -y && \
    apt install -y --no-install-recommends \
        build-essential cargo rustc nodejs npm pkg-config \
        libwebkit2gtk-4.1-dev libgtk-3-dev libsoup-3.0-dev \
        libayatana-appindicator3-dev librsvg2-dev libdbus-1-dev \
        libssl-dev libxdo-dev && \
    rm -rf /var/lib/apt/lists/*
COPY hub-source/src/kyth-hub-web /build/kyth-hub-web
COPY hub-source/src/kyth-shared-rs /build/kyth-shared-rs
COPY hub-source/build_files/exe-handler-apps.json /build_files/exe-handler-apps.json
COPY hub-source/third_party/gtk-glib-0.18.5 /third_party/gtk-glib-0.18.5
WORKDIR /build/kyth-hub-web
RUN npm ci && npm run build
WORKDIR /build/kyth-hub-web/src-tauri
RUN cargo build --release --locked && \
    cp target/release/rowyn-hub-shell /rowyn-hub-shell

# --- system: assemble the bootable image ------------------------------------
FROM base AS system
COPY --from=builder /output /

RUN --mount=type=tmpfs,dst=/tmp --mount=type=tmpfs,dst=/root --mount=type=tmpfs,dst=/boot apt update -y && \
  apt install -y btrfs-progs bubblewrap dosfstools e2fsprogs fdisk linux-firmware linux-image-generic netplan.io openssh-server systemd-resolved chrony skopeo podman crun sudo systemd systemd-boot* xfsprogs libostree-dev dracut && \
  cp /boot/vmlinuz-* "$(find /usr/lib/modules -maxdepth 1 -type d | tail -n 1)/vmlinuz" && \
  apt clean -y

# The official Ubuntu base image ships an `ubuntu` login user (uid 1000).
# An installed Rowyn system must offer only the account the installer
# creates, so the baked-in account (and its home) goes away here, before
# any layer that could come to depend on it.
RUN userdel --remove ubuntu

# --- KDE Plasma package layer -------------------------------------------------
# Install packages that still need the distro's dpkg database before
# bootc-rootfs.sh removes /var. Desktop enablement remains below rootfs
# normalization because it only writes final system configuration.
RUN apt-get update && \
    apt-get install -y \
        plasma-desktop sddm xserver-xorg sddm-theme-breeze konsole dolphin kinfocenter \
        breeze breeze-gtk-theme breeze-icon-theme kde-style-breeze kde-cli-tools \
        plasma-nm plasma-pa xdg-desktop-portal-kde \
        network-manager pipewire wireplumber upower udisks2 plymouth \
        tzdata polkitd \
        fonts-noto-core \
        libwebkit2gtk-4.1-0 libayatana-appindicator3-1 libxdo3 \
        openconnect vpnc-scripts ksshaskpass firewalld && \
    apt-get install -y --no-install-recommends \
        flatpak plasma-discover-backend-flatpak distrobox \
        slirp4netns uidmap fuse-overlayfs && \
    apt-get purge -y plasma-discover-backend-snap snapd plasma-welcome && \
    rm -rf /var/lib/apt/lists/* /var/cache/* /var/log/*

# Canonical timezone state: the image ships exactly what
# systemd-timedated itself would write for UTC — an absolute
# /etc/localtime symlink into /usr/share/zoneinfo plus /etc/timezone —
# so the bootc /etc merge base is never a base-image default the
# installer's symlink swap or a later KDE/timedated change has to
# replace sight-unseen. tzdata and polkitd are named explicitly in the
# layer above: tzdata guarantees the zone files (America/Detroit and
# the US/Michigan alias included) and polkitd authorizes the KDE
# clock settings' timedated calls.
RUN printf 'Etc/UTC\n' > /etc/timezone && \
    ln -sf /usr/share/zoneinfo/Etc/UTC /etc/localtime

RUN systemctl enable systemd-resolved ssh && \
    mkdir -p /etc/netplan && \
    printf 'network:\n  version: 2\n  renderer: NetworkManager\n  ethernets:\n    all-en:\n      match:\n        name: en*\n      dhcp4: true\n    all-eth:\n      match:\n        name: eth*\n      dhcp4: true\n' > /etc/netplan/90-default.yaml && \
    chmod 0600 /etc/netplan/90-default.yaml

# Boot splash: the Rowyn Plymouth theme (pulsing berry mark on deep navy)
# must be installed before the initramfs is generated — dracut's plymouth
# module folds the theme named by the default.plymouth update-alternative
# into the initramfs, and initramfs.sh fails the build if the theme is
# not inside it.
COPY docs/assets/rowyn-logo.png /usr/share/plymouth/themes/rowyn/rowyn-logo.png
RUN --mount=type=bind,from=ctx,source=/,target=/ctx \
    /ctx/shared/plymouth-theme.sh --root /

RUN --mount=type=tmpfs,dst=/tmp --mount=type=tmpfs,dst=/root \
    --mount=type=bind,from=ctx,source=/,target=/ctx \
    /ctx/shared/initramfs.sh

RUN --mount=type=bind,from=ctx,source=/,target=/ctx \
    echo "HOME=/var/home" | tee -a "/etc/default/useradd" && \
    /ctx/shared/bootc-rootfs.sh

# Keep the update verifier and its public key in the immutable payload. The
# policy is generated after stateless normalization so it never depends on
# /var and is available to bootc update fetches from first boot onward.
COPY keys/update.pub /usr/share/rowyn/update.pub
RUN --mount=type=bind,from=ctx,source=/,target=/ctx \
    install -Dm755 /ctx/shared/update-policy.sh /usr/libexec/rowyn-update-policy && \
    /usr/libexec/rowyn-update-policy --root /

# First-boot policy must be generated after stateless normalization. It keeps
# mutable SDDM/resolver overrides in /etc and masks only the unused Budgie
# background cache activation paths.
RUN --mount=type=bind,from=ctx,source=/,target=/ctx \
    /ctx/shared/installed-desktop-defaults.sh --root / && \
    /ctx/shared/installed-apps.sh --root /

# The rootfs normalizer deletes /var. Keep KDE defaults in /etc so they
# survive stateless first boot.
RUN --mount=type=bind,from=ctx,source=/,target=/ctx \
    /ctx/shared/kde-dark-defaults.sh --root / && \
    /ctx/shared/kde-wallpaper-defaults.sh --root /

# --- Rowyn Hub ---------------------------------------------------------------
# System hub (Dashboard, Apps, Updates, VPN) compiled by the hub-builder
# stage from the pinned, ported Kyth source. Runtime libraries for the
# Tauri shell and the VPN stack (openconnect, vpnc-scripts, ksshaskpass,
# firewalld) are in the KDE package layer above.
COPY --from=hub-builder /rowyn-hub-shell /usr/bin/rowyn-hub-shell
COPY shared/com.rowyn.hub.desktop /usr/share/applications/com.rowyn.hub.desktop
COPY docs/assets/rowyn-logo.png /usr/share/icons/hicolor/512x512/apps/com.rowyn.hub.png

# The VPN backend pins the Fedora paths /usr/bin/openconnect and
# --script /etc/vpnc/vpnc-script (its own regression tests assert them);
# Ubuntu ships the binary in /usr/sbin and the script in
# /usr/share/vpnc-scripts. Bridge the layout with symlinks instead of
# forking the pinned argv construction.
RUN ln -s /usr/sbin/openconnect /usr/bin/openconnect && \
    mkdir -p /etc/vpnc && \
    ln -s /usr/share/vpnc-scripts/vpnc-script /etc/vpnc/vpnc-script

# --- Hub update helpers -------------------------------------------------------
# The Hub's Updates actions shell out to fixed helper binaries: the check
# and status reads go through the bootc guard (`sudo -n`, so the sudoers
# allowlist below is what lets them run without a password), staging goes
# through the safe-upgrade helper (`sudo -A`), and applying a staged
# update reboots through the finalizer. Rowyn's implementations are the
# shell scripts in shared/ (fixed-operation counterparts of Kyth's Rust
# helpers); the sudoers file grants exactly their fixed argv to %sudo.
RUN --mount=type=bind,from=ctx,source=/,target=/ctx \
    install -Dm755 /ctx/shared/rowyn-safe-upgrade.sh /usr/bin/rowyn-safe-upgrade && \
    install -Dm755 /ctx/shared/rowyn-bootc-guard.sh /usr/bin/rowyn-bootc-guard && \
    install -Dm755 /ctx/shared/rowyn-finalize-staged.sh /usr/libexec/rowyn-finalize-staged && \
    install -Dm440 /ctx/shared/rowyn-upgrade.sudoers /etc/sudoers.d/rowyn-upgrade && \
    visudo -cf /etc/sudoers.d/rowyn-upgrade

# --- Rowyn identity ----------------------------------------------------------
COPY os-release /usr/lib/os-release

# No login users are baked into the image. The KDE first-login defaults a
# new account needs are already system-wide: kde-dark-defaults.sh --root /
# above installs them into /etc/xdg (applies to every account) and
# /etc/skel (the installer copies /etc/skel into the account it creates),
# so the removed spike user's per-user tmpfiles bake has no equivalent
# here — there is deliberately no per-user seeding for an account that
# must not exist.

# Desktop enablement:
# - NetworkManager owns networking on the desktop (plasma-nm expects it):
#   a wildcard wired profile + an explicit "manage everything" drop-in,
#   and systemd-networkd masked so it cannot race NM for the interface.
RUN mkdir -p /etc/NetworkManager/system-connections /etc/NetworkManager/conf.d && \
    printf '[connection]\nid=rowyn-wired\ntype=ethernet\nautoconnect=true\n\n[ethernet]\n\n[ipv4]\nmethod=auto\n\n[ipv6]\nmethod=auto\n' \
        > /etc/NetworkManager/system-connections/rowyn-wired.nmconnection && \
    chmod 0600 /etc/NetworkManager/system-connections/rowyn-wired.nmconnection && \
    printf '[keyfile]\nunmanaged-devices=none\n' > /etc/NetworkManager/conf.d/99-rowyn-managed.conf && \
    systemctl enable sddm.service NetworkManager.service firewalld.service && \
    systemctl set-default graphical.target && \
    systemctl mask systemd-networkd.service systemd-networkd-wait-online.service && \
    netplan generate --root-dir /

# Keep host package guidance out of the Ubuntu live installer and install it
# only after all image-build APT transactions and stateless normalization.
RUN --mount=type=bind,from=ctx,source=/,target=/ctx \
    /ctx/shared/host-package-guidance.sh --root /

LABEL containers.bootc 1

RUN bootc container lint
