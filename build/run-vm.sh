#!/usr/bin/env bash
# Boot the Rowyn disk image in QEMU for gate testing.
# Requires: qemu-system-x86_64 and OVMF UEFI firmware (apt: ovmf).
# No /dev/kvm on the build VM -> TCG emulation (slow but valid).
# SSH: ssh -p 2222 -i ~/.ssh/id_ed25519 rowyn@127.0.0.1 (spike user, key-only)
# Serial console is logged to build/serial.log. Kill with: pkill -f bootable.img
set -euo pipefail

cd "$(dirname "$0")/.."

OVMF_CODE="${OVMF_CODE:-/usr/share/OVMF/OVMF_CODE_4M.fd}"
OVMF_VARS="${OVMF_VARS:-/usr/share/OVMF/OVMF_VARS_4M.fd}"
[ -f "$OVMF_CODE" ] || { echo "OVMF firmware missing: $OVMF_CODE (apt install ovmf)" >&2; exit 1; }

# Per-boot writable copy of the UEFI vars
cp "$OVMF_VARS" build/ovmf-vars.fd

exec qemu-system-x86_64 \
    -machine q35 -accel tcg \
    -cpu max -smp 2 -m 4096 \
    -drive if=pflash,format=raw,unit=0,readonly=on,file="$OVMF_CODE" \
    -drive if=pflash,format=raw,unit=1,file=build/ovmf-vars.fd \
    -drive file=build/bootable.img,format=raw,if=virtio \
    -netdev user,id=net0,hostfwd=tcp::2222-:22 \
    -device virtio-net-pci,netdev=net0 \
    -display none -serial file:build/serial.log -monitor none \
    -pidfile build/qemu.pid -daemonize

echo "VM started (pid $(cat build/qemu.pid)); serial log: build/serial.log"
