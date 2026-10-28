# Rowyn — a bootc-based Ubuntu (latest release, non-LTS) shipping KDE Plasma.
#
# Structure adapted from Bootcrew's ubuntu-bootc (Apache-2.0; see NOTICE and
# the vendored scripts under shared/). Differences from upstream Bootcrew:
#   - base is the latest Ubuntu release (26.10), not 25.10 "questing"
#   - Rowyn identity in /usr/lib/os-release
#   - a spike user provisioned the stateless way (passwd in /etc, home and
#     SSH key materialized under /var by tmpfiles at first boot)
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

# --- system: assemble the bootable image ------------------------------------
FROM base AS system
COPY --from=builder /output /

RUN --mount=type=tmpfs,dst=/tmp --mount=type=tmpfs,dst=/root --mount=type=tmpfs,dst=/boot apt update -y && \
  apt install -y btrfs-progs bubblewrap dosfstools e2fsprogs fdisk linux-firmware linux-image-generic netplan.io openssh-server systemd-resolved chrony skopeo podman crun sudo systemd systemd-boot* xfsprogs libostree-dev dracut && \
  cp /boot/vmlinuz-* "$(find /usr/lib/modules -maxdepth 1 -type d | tail -n 1)/vmlinuz" && \
  apt clean -y

# --- KDE Plasma package layer -------------------------------------------------
# Install packages that still need the distro's dpkg database before
# bootc-rootfs.sh removes /var. Desktop enablement remains below rootfs
# normalization because it only writes final system configuration.
RUN apt-get update && \
    apt-get install -y \
        plasma-desktop sddm xserver-xorg sddm-theme-breeze konsole dolphin kinfocenter \
        breeze breeze-gtk-theme breeze-icon-theme kde-style-breeze kde-cli-tools \
        plasma-nm plasma-pa xdg-desktop-portal-kde \
        network-manager pipewire wireplumber upower udisks2 \
        fonts-noto-core && \
    apt-get install -y --no-install-recommends \
        flatpak plasma-discover-backend-flatpak distrobox \
        slirp4netns uidmap fuse-overlayfs && \
    apt-get purge -y plasma-discover-backend-snap snapd plasma-welcome && \
    rm -rf /var/lib/apt/lists/* /var/cache/* /var/log/*

RUN systemctl enable systemd-resolved ssh && \
    mkdir -p /etc/netplan && \
    printf 'network:\n  version: 2\n  renderer: NetworkManager\n  ethernets:\n    all-en:\n      match:\n        name: en*\n      dhcp4: true\n    all-eth:\n      match:\n        name: eth*\n      dhcp4: true\n' > /etc/netplan/90-default.yaml && \
    chmod 0600 /etc/netplan/90-default.yaml

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

# --- Rowyn identity ----------------------------------------------------------
COPY os-release /usr/lib/os-release

# --- spike user --------------------------------------------------------------
# /etc/passwd is committed by ostree; /var starts empty on install, so the
# home directory and authorized_keys are created by tmpfiles on first boot.
COPY keys/spike.pub /usr/share/rowyn/spike.pub
RUN --mount=type=bind,from=ctx,source=/,target=/ctx \
    useradd --home-dir /var/home/rowyn --no-create-home --shell /bin/bash rowyn && \
    /ctx/shared/kde-dark-defaults.sh --root / --user rowyn && \
    usermod -aG sudo rowyn && \
    mkdir -p /etc/sudoers.d && \
    printf 'rowyn ALL=(ALL) NOPASSWD: ALL\n' > /etc/sudoers.d/90-rowyn-spike && \
    chmod 0440 /etc/sudoers.d/90-rowyn-spike && \
    printf 'd /var/home/rowyn 0700 rowyn rowyn -\nd /var/home/rowyn/.ssh 0700 rowyn rowyn -\nC /var/home/rowyn/.ssh/authorized_keys 0600 rowyn rowyn - /usr/share/rowyn/spike.pub\n' \
        > /usr/lib/tmpfiles.d/rowyn-spike.conf

# Desktop enablement:
# - spike user gets a throwaway password (rowyn) so SDDM/console login works
#   for local boot tests; SSH stays key-only. SPIKE ONLY — remove for release.
# - NetworkManager owns networking on the desktop (plasma-nm expects it):
#   a wildcard wired profile + an explicit "manage everything" drop-in,
#   and systemd-networkd masked so it cannot race NM for the interface.
RUN echo 'rowyn:rowyn' | chpasswd && \
    mkdir -p /etc/NetworkManager/system-connections /etc/NetworkManager/conf.d && \
    printf '[connection]\nid=rowyn-wired\ntype=ethernet\nautoconnect=true\n\n[ethernet]\n\n[ipv4]\nmethod=auto\n\n[ipv6]\nmethod=auto\n' \
        > /etc/NetworkManager/system-connections/rowyn-wired.nmconnection && \
    chmod 0600 /etc/NetworkManager/system-connections/rowyn-wired.nmconnection && \
    printf '[keyfile]\nunmanaged-devices=none\n' > /etc/NetworkManager/conf.d/99-rowyn-managed.conf && \
    systemctl enable sddm.service NetworkManager.service && \
    systemctl set-default graphical.target && \
    systemctl mask systemd-networkd.service systemd-networkd-wait-online.service && \
    netplan generate --root-dir /

LABEL containers.bootc 1

RUN bootc container lint
