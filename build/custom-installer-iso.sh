#!/usr/bin/env bash
# Ubuntu-only custom live installer. Kyth supplies pinned Rust sources only.
set -euo pipefail
repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"
: "${IMAGE_REF:?IMAGE_REF is required}"
: "${IMAGE_DIGEST:?IMAGE_DIGEST is required}"
: "${SOURCE_TAG:?SOURCE_TAG is required}"
: "${KYTH_SOURCE_DIR:?KYTH_SOURCE_DIR is required}"
: "${KYTH_INSTALLER_COMMIT:?KYTH_INSTALLER_COMMIT is required}"
: "${UBUNTU_LIVE_IMAGE:?UBUNTU_LIVE_IMAGE is required}"
[[ "$IMAGE_REF" =~ ^ghcr\.io/kyth-os/rowyn:[0-9a-f]{40}$ ]] || { echo 'Invalid immutable Rowyn ref' >&2; exit 1; }
[[ "$IMAGE_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]] || { echo 'Invalid release digest' >&2; exit 1; }
[[ "$SOURCE_TAG" == "${IMAGE_REF##*:}" ]] || { echo 'Source tag mismatch' >&2; exit 1; }
[[ "$KYTH_INSTALLER_COMMIT" == 2baf8496b5aa904c0784f4290df743ee17edae48 ]] || { echo 'Unreviewed installer source' >&2; exit 1; }
# Fail before any container command if an OS other than digest-pinned Ubuntu is supplied.
[[ "$UBUNTU_LIVE_IMAGE" =~ ^docker\.io/library/ubuntu@sha256:[0-9a-f]{64}$ ]] || { echo 'Live and compiler base must be digest-pinned official Ubuntu' >&2; exit 1; }
[[ -z "${KYTH_INSTALLER_BASE_IMAGE:-}" ]] || { echo 'Legacy live-base override is forbidden' >&2; exit 1; }
source_image="ghcr.io/kyth-os/rowyn@${IMAGE_DIGEST}"
export INSTALL_SOURCE_IMAGE="$source_image"
python3 -B build/ubuntu-only-check.py
# Respect the caller's scratch filesystem, never a hard-coded /tmp host path.
: "${TMPDIR:?TMPDIR must name a scratch directory}"
work="$(mktemp -d "${TMPDIR%/}/rowyn-ubuntu-iso.XXXXXX")"
python3 build/ubuntu-installer-source.py "$KYTH_SOURCE_DIR" "$work/source" "$IMAGE_DIGEST"
mkdir -p "$work/source/rowyn" "$work/assembly"
cp build/{configure-live.sh,assemble-iso.sh,ubuntu-live-preflight.py,ubuntu-live-network.py,ubuntu-source-probes.py} "$work/source/rowyn/"
cp shared/kde-dark-defaults.sh "$work/source/rowyn/"
if [[ "${ROWYN_CUSTOM_INSTALLER_PREFLIGHT:-0}" == 1 ]]; then
    printf 'Ubuntu installer source preflight passed: %s\n' "$work/source"
    exit 0
fi
engine=(podman)
if [[ "${GITHUB_ACTIONS:-}" == true && "${RUNNER_ENVIRONMENT:-}" == github-hosted ]]; then
    engine=("$repo_root/build/rootful-podman.sh")
fi
image="localhost/rowyn-ubuntu-live:${SOURCE_TAG}"
"${engine[@]}" build --layers --network=host --security-opt label=disable \
    --build-arg "UBUNTU_IMAGE=${UBUNTU_LIVE_IMAGE}" \
    --build-arg "PAYLOAD_IMAGE=${source_image}" \
    --build-arg "INSTALL_SOURCE_IMAGE=${source_image}" \
    -f "$repo_root/build/ubuntu-live.Containerfile" -t "$image" "$work/source"
output_dir="$(realpath -m "${ROWYN_ISO_OUTPUT:-$repo_root/output}")"
mkdir -p "$output_dir"
# Assembly writes ordinary files only; no host devices/privileges are passed.
"${engine[@]}" run --rm --network=none --security-opt label=disable \
    -e "SOURCE_TAG=$SOURCE_TAG" -v "$output_dir:/output:rw" \
    -v "$work/assembly:/work:rw" "$image"
iso="$output_dir/rowyn-custom-installer-${SOURCE_TAG}.iso"
test -s "$iso"
python3 - "$iso" <<'PY'
import hashlib
import json
import os
from pathlib import Path
import sys
iso = Path(sys.argv[1])
hash_ = hashlib.sha256()
with iso.open('rb') as stream:
    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
        hash_.update(chunk)
iso.with_suffix('.json').write_text(json.dumps(dict(
    schema_version=2, artifact=iso.name, sha256='sha256:' + hash_.hexdigest(),
    bytes=iso.stat().st_size, source_image=os.environ['INSTALL_SOURCE_IMAGE'],
    source_digest=os.environ['IMAGE_DIGEST'], kyth_installer_commit=os.environ['KYTH_INSTALLER_COMMIT'],
    ubuntu_live_image=os.environ['UBUNTU_LIVE_IMAGE'], live_os='ubuntu', build_os='ubuntu',
    firmware='x86_64 UEFI; Secure Boot disabled', supported_modes=['wipe-plaintext'],
    signing_identity='https://github.com/kyth-os/Rowyn/.github/workflows/build.yml@refs/heads/main',
), indent=2) + '\n')
PY
printf 'Ubuntu live installer ISO: %s\nBuild workspace: %s\n' "$iso" "$work"
sha256sum "$iso"
