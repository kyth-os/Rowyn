#!/usr/bin/env bash
# Hosted runners already have a valid host cgroup hierarchy. Do not unshare it:
# crun/Buildah otherwise resolves a systemd scope outside the cgroup mount.
# Keep upstream build/run flags (SYS_ADMIN, label=disable, host network) intact.
set -euo pipefail
[[ "${GITHUB_ACTIONS:-}" == true && "${RUNNER_ENVIRONMENT:-}" == github-hosted ]] \
    || { echo 'ERROR: this adapter is only for GitHub-hosted runners' >&2; exit 1; }
if [[ "$(id -u)" == 0 ]]; then
    exec podman "$@"
fi
exec sudo --preserve-env=MOK_KEY podman "$@"
