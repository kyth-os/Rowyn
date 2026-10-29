#!/usr/bin/env bash
# Install Rowyn's performance defaults: zram swap, VM/network sysctls, MGLRU
# and THP policy, ntsync access, and generous file-descriptor limits.
#
# Everything lives in vendor directories under /usr/lib so an administrator's
# /etc drop-in of the same name or key always wins. Idempotent: each file is
# rewritten in full.
set -euo pipefail

root=/
while (($#)); do
    case "$1" in
        --root)
            root=${2:?--root requires a path}
            shift 2
            ;;
        *)
            echo "usage: $0 [--root PATH]" >&2
            exit 2
            ;;
    esac
done

if [[ "$root" != / ]]; then
    root="${root%/}"
fi
root_path() {
    if [[ "$1" == /* ]]; then
        printf '%s%s' "$root" "$1"
    else
        printf '%s/%s' "$root" "$1"
    fi
}

# write_file PATH MODE: stdin becomes the file.
write_file() {
    local path
    path=$(root_path "$1")
    install -d "$(dirname "$path")"
    cat > "$path"
    chmod "$2" "$path"
}

# systemd-zram-generator reads /usr/lib/systemd/zram-generator.conf.
write_file /usr/lib/systemd/zram-generator.conf 0644 <<'EOF2'
# Compressed RAM swap instead of a disk swap file.
[zram0]
# Half of RAM, capped at 8 GiB: enough headroom without starving the page cache.
zram-size = min(ram / 2, 8192)
# zstd compresses far better than lzo/lz4 at a cost modern CPUs hide.
compression-algorithm = zstd
# Above any disk swap so zram fills first.
swap-priority = 100
EOF2

write_file /usr/lib/sysctl.d/90-rowyn-performance.conf 0644 <<'EOF2'
# Rowyn performance defaults. Override in /etc/sysctl.d/.

# zram is cheaper than disk: prefer swapping anonymous pages over dropping cache.
vm.swappiness = 180
# Swap readahead is pointless on zram; read one page at a time.
vm.page-cluster = 0
# Do not boost reclaim watermarks after fragmentation events (less stalling).
vm.watermark_boost_factor = 0
# Wake kswapd earlier so allocations rarely enter direct reclaim.
vm.watermark_scale_factor = 125
# Games and Proton/Wine (and some JVMs) map many regions.
vm.max_map_count = 2147483642
# IDEs, file syncers and dev tools watch large trees.
fs.inotify.max_user_watches = 524288
# Many concurrent watchers (containers, IDEs) per user.
fs.inotify.max_user_instances = 1024
# fq pacing is required by BBR and gives fair queuing per flow.
net.core.default_qdisc = fq
# BBR sustains throughput on lossy or high-latency links (module: tcp_bbr).
net.ipv4.tcp_congestion_control = bbr
EOF2

write_file /usr/lib/modules-load.d/rowyn-bbr.conf 0644 <<'EOF2'
# Load BBR early so the tcp_congestion_control sysctl can select it.
tcp_bbr
EOF2

write_file /usr/lib/tmpfiles.d/rowyn-performance.conf 0644 <<'EOF2'
# Rowyn kernel memory-management policy.

# Multi-gen LRU: better page-reclaim decisions under memory pressure.
w /sys/kernel/mm/lru_gen/enabled - - - - y
# THP only on madvise: avoids latency spikes and bloat for apps that do not ask.
w /sys/kernel/mm/transparent_hugepage/enabled - - - - madvise
# Defrag stalls only for madvise regions; others fall back to small pages.
w /sys/kernel/mm/transparent_hugepage/defrag - - - - defer+madvise
EOF2

write_file /usr/lib/modules-load.d/rowyn-ntsync.conf 0644 <<'EOF2'
# NT synchronization primitives for Wine/Proton (kernel 6.14+).
ntsync
EOF2

write_file /usr/lib/udev/rules.d/70-rowyn-ntsync.rules 0644 <<'EOF2'
# Give the logged-in seat user access to /dev/ntsync for Wine/Proton.
KERNEL=="ntsync", MODE="0660", TAG+="uaccess"
EOF2

for scope in system user; do
    write_file "/usr/lib/systemd/$scope.conf.d/90-rowyn-limits.conf" 0644 <<'EOF2'
[Manager]
# Soft 1024 keeps select() legacy code safe; hard 1048576 satisfies games,
# Wine/Proton and servers that raise their own limit.
DefaultLimitNOFILE=1024:1048576
EOF2
done
