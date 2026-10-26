#!/usr/bin/env bash
exec >>/home/hatch/workspace/rowyn/build/tofile-chroot.log 2>&1
echo "=== tofile-chroot $(date) ==="
ROOTFS=/home/hatch/containers-storage/vfs/dir/4b1b472c5da4662b09e79dae7be70da88708344779bc36370c216116c5b326b3
mount --bind /proc "$ROOTFS/proc" 2>/dev/null
mount --bind /dev "$ROOTFS/dev" 2>/dev/null
mount --bind /sys "$ROOTFS/sys" 2>/dev/null
mount --bind /home/hatch/workspace/rowyn/sysroot-work "$ROOTFS/target"
chroot "$ROOTFS" /usr/bin/bootc install to-filesystem --target-imgref ghcr.io/kyth-os/rowyn:spike --bootloader systemd --composefs-backend /target
echo "bootc install rc=$?"
ls /home/hatch/workspace/rowyn/sysroot-work
echo "CHROOT_INSTALL_DONE"
