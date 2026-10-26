# Both compilation and the live system are Ubuntu. No upstream OS build scripts.
ARG UBUNTU_IMAGE
ARG PAYLOAD_IMAGE
FROM ${PAYLOAD_IMAGE} AS payload
RUN test "$(. /usr/lib/os-release; echo "$ID_LIKE")" = 'ubuntu debian' && \
    test -x /usr/bin/bootc && \
    test -s /usr/lib/systemd/boot/efi/systemd-bootx64.efi && \
    find /usr/lib/modules -mindepth 2 -maxdepth 2 -name vmlinuz -size +0c | grep . && \
    find /usr/lib/modules -mindepth 2 -maxdepth 2 -name initramfs.img -size +0c | grep . && \
    for kernel in /usr/lib/modules/*/vmlinuz; do test -s "${kernel%/*}/initramfs.img"; done

FROM ${UBUNTU_IMAGE} AS compiler
ENV DEBIAN_FRONTEND=noninteractive CARGO_BUILD_JOBS=4
RUN . /etc/os-release && test "$ID" = ubuntu && test "$VERSION_ID" = 26.10 && \
    apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates build-essential cargo rustc pkg-config nodejs npm python3 \
      libwebkit2gtk-4.1-dev libxdo-dev libssl-dev librsvg2-dev \
      libfontconfig1-dev libxkbcommon-dev libwayland-dev libudev-dev libinput-dev \
      libgbm-dev libegl-dev libgl-dev && rm -rf /var/lib/apt/lists/*
COPY src/ /build/src/
COPY third_party/ /build/third_party/
COPY build_files/exe-handler-apps.json /build/build_files/exe-handler-apps.json
COPY rowyn/ubuntu-source-probes.py /source-probes.py
RUN python3 /source-probes.py /build /source-probes
WORKDIR /build/src/kyth-installer-web
RUN npm ci && npm run build
WORKDIR /build/src/kyth-installer-web/src-tauri
RUN cargo build --release --locked --bin kyth-installer-shell --bin kyth-installerd --bin kyth-installer-exec && \
    mkdir /binaries && cp target/release/kyth-installer-shell target/release/kyth-installerd target/release/kyth-installer-exec /binaries/
RUN mkdir /dependency-licenses && \
    find /root/.cargo/registry/src -type f \
      \( -iname '*license*' -o -iname 'copying*' -o -iname 'notice*' \) \
      -exec cp --parents -t /dependency-licenses {} + && \
    cp Cargo.lock /dependency-licenses/installer-Cargo.lock && \
    cp /build/third_party/gtk-glib-0.18.5/Cargo.toml /build/third_party/gtk-glib-0.18.5/README.md /dependency-licenses/ && \
    cp /root/.cargo/registry/src/*/glib-macros-0.18.5/LICENSE /dependency-licenses/gtk-rs-MIT-LICENSE

FROM ${UBUNTU_IMAGE} AS live
ENV DEBIAN_FRONTEND=noninteractive DRACUT_NO_XATTR=1
# casper/initramfs-tools are for the live OS only. Installed Rowyn retains its
# signed payload kernel and bootc/dracut initramfs; those are not regenerated.
RUN . /etc/os-release && test "$ID" = ubuntu && test "$VERSION_ID" = 26.10 && \
    printf '#!/bin/sh\nexit 101\n' > /usr/sbin/policy-rc.d && chmod 755 /usr/sbin/policy-rc.d && \
    apt-get update && apt-get install -y --no-install-recommends \
      linux-image-generic linux-firmware casper initramfs-tools systemd-sysv \
      plasma-desktop plasma-workspace sddm xserver-xorg xinit dbus-x11 \
      breeze breeze-gtk-theme breeze-icon-theme kde-style-breeze kde-cli-tools \
      network-manager netplan.io plasma-nm konsole kdialog fonts-noto-core \
      libwebkit2gtk-4.1-0 libgtk-3-0t64 libxdo3 libfontconfig1 libxkbcommon0 \
      libegl1 libgl1 libgl1-mesa-dri libinput10 libgbm1 libudev1 \
      ca-certificates curl python3 sudo openssl locales tzdata bubblewrap \
      ostree libostree-1-1 skopeo btrfs-progs dosfstools e2fsprogs xfsprogs \
      gdisk parted fdisk util-linux mount udev ntfs-3g cryptsetup-bin mokutil efibootmgr \
      systemd-boot systemd-boot-efi jq binutils \
      squashfs-tools xorriso grub-common grub-pc-bin grub-efi-amd64-bin mtools && \
    rm -rf /var/lib/apt/lists/*
COPY --from=payload /usr/bin/bootc /usr/bin/bootc
COPY --from=payload /usr/lib/bootc/ /usr/lib/bootc/
# bootc reads prepare-root policy from the running root even with --source-imgref.
# Reuse the exact policy from this digest-pinned payload in the Ubuntu live host.
COPY --from=payload /usr/lib/ostree/prepare-root.conf /usr/lib/ostree/prepare-root.conf
COPY --from=compiler /binaries/ /usr/bin/
COPY --from=compiler /dependency-licenses/ /usr/share/doc/rowyn-installer/dependency-licenses/
COPY build_files/kyth-launch-installer /usr/bin/kyth-launch-installer
COPY build_files/kyth-installerd.service /usr/lib/systemd/system/kyth-installerd.service
COPY LICENSE ROWYN-PORT-NOTICE /usr/share/doc/rowyn-installer/
COPY rowyn/ /rowyn/
ARG INSTALL_SOURCE_IMAGE
RUN bash /rowyn/configure-live.sh
# ISO assembly is a container entrypoint, never a host chroot or host install.
ENTRYPOINT ["bash", "/rowyn/assemble-iso.sh"]
