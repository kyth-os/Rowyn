#!/usr/bin/env bash
# Rowyn passwordless bootc gateway for the Hub.
#
# A fixed-operation wrapper in the shape of Kyth's kyth-bootc-guard: the
# Hub's unprivileged shell reaches bootc's root-only sysroot reads through
# `sudo -n` (allowlisted in /etc/sudoers.d/rowyn-upgrade), and every
# operation is a fixed argv — no caller-provided image reference or extra
# arguments are possible. Rowyn publishes a single channel, so the only
# switch operation is switch-latest, targeting the stable tag the image
# pipeline promotes; Kyth's testing/cachy channels do not exist here and
# are refused rather than mapped onto something they are not.
#
# Operations (exactly one argument; anything else is a usage error):
#   status        bootc status (human readable), 30s bound
#   status-json   bootc status --json, 30s bound
#   check         bootc upgrade --check, 90s bound; stdout/stderr pass
#                 through untouched — the Hub parses bootc's own
#                 "Update available for:" / "No changes in:" wording
#   switch-latest bootc switch ghcr.io/kyth-os/rowyn:stable under the
#                 bootc flock, then finalize the staged deployment
set -uo pipefail

readonly LOCK_FILE=/run/rowyn-bootc.lock
readonly STABLE_REF=ghcr.io/kyth-os/rowyn:stable

if (( $# != 1 )); then
    echo "usage: rowyn-bootc-guard <status|status-json|check|switch-latest>" >&2
    exit 2
fi

case "$1" in
    status)
        exec timeout 30 /usr/bin/bootc status
        ;;
    status-json)
        exec timeout 30 /usr/bin/bootc status --json
        ;;
    check)
        exec timeout 90 /usr/bin/bootc upgrade --check
        ;;
    switch-latest)
        ;;
    switch-testing|switch-latest-cachy|switch-testing-cachy)
        echo "rowyn-bootc-guard: channel '$1' does not exist on Rowyn; only the stable channel is published" >&2
        exit 2
        ;;
    *)
        echo "rowyn-bootc-guard: unsupported operation: $1" >&2
        exit 2
        ;;
esac

# switch-latest: serialize against rowyn-safe-upgrade (and any other
# holder of the same flock) before touching deployments. A busy lock is a
# retryable verdict, reported before any mount or deployment changes.
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    echo "Another bootc upgrade is in progress; will retry on the next run" >&2
    exit 1
fi

# Kyth prepares /boot (remount read-write) before a switch because its
# /boot is a read-only bind; Rowyn's bootc manages its boot partition
# itself, so preparation is best-effort here and never aborts the switch.
if [[ -x /usr/libexec/rowyn-finalize-staged ]]; then
    /usr/libexec/rowyn-finalize-staged prepare-boot >/dev/null 2>&1 || true
fi

timeout 3600 /usr/bin/bootc switch "$STABLE_REF"
status=$?
if (( status != 0 )); then
    exit "$status"
fi

if [[ -x /usr/libexec/rowyn-finalize-staged ]]; then
    exec /usr/libexec/rowyn-finalize-staged
fi
exec /usr/bin/ostree admin finalize-staged
