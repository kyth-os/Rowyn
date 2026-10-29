#!/usr/bin/env bash
# Prepare /boot and finalize staged deployments before shutdown.
#
# Ported from Kyth's kyth-finalize-staged (same modes, same contract with
# the Hub's apply_staged launcher):
#   rowyn-finalize-staged              prepare /boot, then
#                                      `ostree admin finalize-staged`
#   rowyn-finalize-staged prepare-boot only prepare /boot
#   rowyn-finalize-staged reboot       queue a non-blocking restart;
#                                      systemd's ostree-finalize-staged
#                                      shutdown hook finalizes any
#                                      remaining deployment
#
# `mount -o remount,rw` is EINVAL on a bind-mounted /boot;
# `remount,bind,rw` works, so it is tried first.
set -uo pipefail

# Rowyn boots bootc's composefs backend: the kernel command line carries
# composefs.digest=v1-... (bootc's default EROFS V1 format) or composefs=...
# (V2). There, /boot is not an ostree boot mount and staged deployments are
# finalized at shutdown by bootc-finalize-staged.service, so the /boot
# preparation and `ostree admin finalize-staged` below apply only to ostree
# boots. ROWYN_PROC_CMDLINE exists for tests.
composefs_boot() {
    grep -qE '(^| )composefs(\.digest)?=' "${ROWYN_PROC_CMDLINE:-/proc/cmdline}" 2>/dev/null
}

prepare_boot() {
    if composefs_boot; then
        return 0
    fi
    if ! mount -o remount,bind,rw /boot 2>/dev/null && ! mount -o remount,rw /boot 2>/dev/null; then
        echo "rowyn-finalize-staged: could not remount /boot read-write" >&2
        return 1
    fi
    # ostree writes BLS entries through /sysroot/boot. If that path exists
    # and is not already a mount, bind the real /boot over it. Never remount
    # /sysroot/boot itself — it is often not a mount point.
    if [[ -d /sysroot/boot ]] && ! findmnt -n /sysroot/boot >/dev/null 2>&1; then
        if ! mount --bind /boot /sysroot/boot 2>/dev/null; then
            echo "rowyn-finalize-staged: could not bind /boot to /sysroot/boot" >&2
            return 1
        fi
    fi
    return 0
}

mode=${1:-}
if (( $# > 1 )); then
    echo "usage: rowyn-finalize-staged [prepare-boot|reboot]" >&2
    exit 2
fi

case "$mode" in
    prepare-boot)
        prepare_boot
        exit $?
        ;;
    reboot)
        # The ostree-finalize-staged shutdown hook performs any remaining
        # finalization during shutdown. Queue the reboot now rather than
        # trying to finalize an already-finalized deployment a second time
        # in the Hub path.
        exec /usr/bin/systemctl --no-block reboot
        ;;
    "")
        ;;
    *)
        echo "usage: rowyn-finalize-staged [prepare-boot|reboot]" >&2
        exit 2
        ;;
esac

if composefs_boot; then
    echo "rowyn-finalize-staged: bootc finalizes the staged deployment at shutdown"
    exit 0
fi

if ! prepare_boot; then
    echo "rowyn-finalize-staged: trying finalize anyway" >&2
fi

/usr/bin/ostree admin finalize-staged
status=$?
if (( status == 0 )); then
    # Close the read-write window prepare_boot opened: /boot otherwise
    # stays writable until the next reboot. Best-effort — a failed remount
    # must not fail an otherwise successful finalize.
    if ! mount -o remount,ro /boot 2>/dev/null; then
        echo "rowyn-finalize-staged: could not remount /boot read-only" >&2
    fi
fi
exit "$status"
