#!/usr/bin/env bash
exec >>/home/hatch/workspace/rowyn/build/to-filesystem.log 2>&1
echo "=== to-filesystem $(date) ==="
export https_proxy=http://hatch-egress-proxy:3128 HTTPS_PROXY=http://hatch-egress-proxy:3128
podman run --privileged --rm --network none \
  -v /mnt/rowyn-sysroot:/target \
  ghcr.io/kyth-os/rowyn:spike \
  bootc install to-filesystem --target-imgref ghcr.io/kyth-os/rowyn:spike \
    --bootloader systemd --composefs-backend /target
echo "bootc install rc=$?"
ls /mnt/rowyn-sysroot
echo "TOFILE_DONE"
