#!/usr/bin/env bash
# Create a bootable disk image from a built Rowyn container image.
# Mirrors Bootcrew's `just disk-image`: runs the image privileged and lets
# bootc install itself onto a loopback file with the composefs backend.
set -euo pipefail

IMAGE="${1:-rowyn:spike}"
cd "$(dirname "$0")/.."

if [ ! -e build/bootable.img ]; then
    fallocate -l 20G build/bootable.img
fi

sudo podman run \
    --rm --privileged --pid=host \
    -v /var/lib/containers:/var/lib/containers \
    -v /etc/containers:/etc/containers \
    -v /dev:/dev \
    -v "$PWD/build:/data" \
    "$IMAGE" bootc install to-disk \
        --composefs-backend \
        --via-loopback /data/bootable.img \
        --filesystem ext4 \
        --wipe \
        --bootloader systemd

echo "disk image ready: build/bootable.img (from $IMAGE)"
