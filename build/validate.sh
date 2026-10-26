#!/usr/bin/env bash
# Validate Rowyn's source and CI contracts before an image build starts.
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
cd "${repo_root}"

fail() {
    echo "[validate] ERROR: $*" >&2
    exit 1
}

scripts=(build/*.sh shared/*.sh)
(( ${#scripts[@]} > 0 )) || fail "no shell scripts found"

for script in "${scripts[@]}"; do
    test -x "${script}" || fail "script is not executable: ${script}"
    bash -n "${script}"
done

echo "[validate] shell syntax passed"

command -v shellcheck >/dev/null 2>&1 || fail "shellcheck is required"

echo "[validate] ShellCheck advisory diagnostics"
shellcheck --severity=warning "${scripts[@]}" || true

echo "[validate] ShellCheck errors"
shellcheck --severity=error "${scripts[@]}"

echo "[validate] ShellCheck passed"

python3 - <<'PY'
from pathlib import Path

import yaml

for workflow in sorted(Path('.github/workflows').glob('*.yml')):
    with workflow.open(encoding='utf-8') as handle:
        yaml.safe_load(handle)
PY
echo "[validate] workflow YAML parsed"

deprecated_tool_cache_count="$( { grep -Fh 'tool-cache:' .github/workflows/{build,installer-iso}.yml || true; } | wc -l)"
(( deprecated_tool_cache_count == 0 )) \
    || fail "workflow still uses deprecated free-disk-space tool-cache input"
preinstalled_runtimes_count="$(grep -Fc 'preinstalled-runtimes: true' .github/workflows/{build,installer-iso}.yml | awk -F: '{sum += $NF} END {print sum}')"
(( preinstalled_runtimes_count == 2 )) \
    || fail "both free-disk-space steps must use preinstalled-runtimes"
echo "[validate] free-disk-space inputs passed"

test -s Containerfile || fail "Containerfile is missing or empty"
test -s os-release || fail "os-release is missing or empty"
test -s keys/spike.pub || fail "spike public key is missing"
test -x build/custom-installer-iso.sh || fail "custom installer ISO script is missing or not executable"
test -s .github/actions/setup-compressed-storage/action.yml \
    || fail "compressed Podman storage action is missing or empty"

grep -Fq 'LABEL containers.bootc 1' Containerfile \
    || fail "Containerfile is missing containers.bootc label"
grep -Fq 'RUN bootc container lint' Containerfile \
    || fail "Containerfile is missing bootc container lint"
python3 - <<'PY'
from pathlib import Path

containerfile = Path('Containerfile').read_text(encoding='utf-8')
kde_packages = containerfile.find('plasma-desktop')
rootfs_normalization = containerfile.find('/ctx/shared/bootc-rootfs.sh')
if kde_packages < 0 or rootfs_normalization < 0 or kde_packages > rootfs_normalization:
    raise SystemExit(
        'KDE packages must be installed before bootc-rootfs.sh removes /var'
    )
PY
echo "[validate] KDE package ordering passed"
grep -Fq 'CARGO_NET_OFFLINE=true' shared/build.sh \
    || fail "bootc source build is not explicitly offline"
grep -Fq 'git clone --depth 1' build/fetch-assets.sh \
    || fail "bootc asset fetch is missing shallow clone"

grep -Fq 'NAME="Rowyn"' os-release || fail "os-release identity is not Rowyn"
grep -Fxq 'ID=rowyn' os-release || fail "os-release ID is not rowyn"
grep -Fxq 'ID_LIKE="ubuntu debian"' os-release \
    || fail "os-release does not identify the Ubuntu/Debian family"

if git ls-files | grep -q '^build-assets/'; then
    fail "host-fetched build-assets/ must never be committed"
fi
git check-ignore -q build-assets/.rowyn-ci-sentinel \
    || fail "build-assets/ is not ignored"

grep -Fq 'needs: validate' .github/workflows/build.yml \
    || fail "image build is not gated on validation"
grep -Fq 'grep -Fqx "NAME=\"Rowyn\"" /usr/lib/os-release' .github/workflows/build.yml \
    || fail "image smoke check has an invalid Rowyn os-release pattern"
grep -Fq 'grep -Fqx "ID_LIKE=\"ubuntu debian\"" /usr/lib/os-release' .github/workflows/build.yml \
    || fail "image smoke check has an invalid Ubuntu family pattern"
grep -Fq -- '--digestfile' .github/workflows/build.yml \
    || fail "image publication does not record an immutable digest"
grep -Fq 'steps.source.outputs.image_digest' .github/workflows/installer-iso.yml \
    || fail "ISO job does not consume the published image digest"
if grep -Fq -- '/var/lib/containers/storage:/var/lib/containers/storage' .github/workflows/installer-iso.yml; then
    fail "custom installer ISO path must not use the obsolete bootc-image-builder storage mount"
fi

if grep -Eq 'bootc-image-builder|anaconda-iso' .github/workflows/installer-iso.yml; then
    fail "Rowyn ISO path must use the custom installer environment, not anaconda-iso"
fi
grep -Fq 'workflows: [Rowyn CI]' .github/workflows/installer-iso.yml \
    || fail "custom installer ISO is not downstream of image workflow"
grep -Fq 'id-token: write' .github/workflows/build.yml \
    || fail "image signing job is missing OIDC permission"
grep -Fq 'cosign sign -y' .github/workflows/build.yml \
    || fail "image signing job does not sign the immutable image"
grep -Fq 'KYTH_INSTALLER_COMMIT' .github/workflows/installer-iso.yml \
    || fail "custom installer source commit is not pinned"
grep -Fq 'KYTH_INSTALLER_BASE_IMAGE' .github/workflows/installer-iso.yml \
    || fail "custom installer base image is not pinned"
grep -Fq './.github/actions/setup-compressed-storage' .github/workflows/installer-iso.yml \
    || fail "custom installer ISO does not provision compressed Podman storage"
grep -Fq 'size: 65G' .github/workflows/installer-iso.yml \
    || fail "custom installer Podman storage size is not explicit"
grep -Fq 'ROWYN_CUSTOM_INSTALLER_PREFLIGHT' .github/workflows/installer-iso.yml \
    || fail "custom installer adapter preflight is missing"
grep -Fq 'INSTALL_SOURCE_IMAGE' build/custom-installer-iso.sh \
    || fail "custom installer script does not pass the immutable Rowyn source"
grep -Fq 'bash build_files/build-live-iso.sh' build/custom-installer-iso.sh \
    || fail "custom installer script assumes the upstream entrypoint is executable"
grep -Fq 'source_image="${image_name}@${IMAGE_DIGEST}"' build/custom-installer-iso.sh \
    || fail "custom installer source is not canonical image@digest form"
grep -Fq 'cosign_identity="^https://github.com/kyth-os/Rowyn/' build/custom-installer-iso.sh \
    || fail "custom installer script does not pin the Rowyn signing identity"
grep -Fq 'cosign_bin="/usr/bin/kyth-cosign"' build/custom-installer-iso.sh \
    || fail "custom installer does not retain the verified cosign runtime"
grep -Fq 'ROWYN_RELEASE_DIGEST' build/custom-installer-iso.sh \
    || fail "custom installer does not bind runtime verification to this Rowyn digest"
grep -Fq 'Command::new("/usr/bin/kyth-cosign")' build/custom-installer-iso.sh \
    || fail "custom installer does not cryptographically verify the image at install time"
grep -Fq 'TUF_ROOT", tuf_root' build/custom-installer-iso.sh \
    || fail "custom installer does not isolate the runtime cosign trust cache"
grep -Fq 'early_verified =' build/custom-installer-iso.sh \
    || fail "custom installer does not require an unconditional Prepare gate"
if grep -Fq 'KYTH_EARLY_SOURCE_VERIFY=1' build/custom-installer-iso.sh; then
    fail "custom installer must not rely on an optional early-verification environment setting"
fi
grep -Fq 'self.verify_install_source(phase)?' build/custom-installer-iso.sh \
    || fail "custom installer does not verify the source during Prepare"
grep -Fq 'RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6' build/custom-installer-iso.sh \
    || fail "custom installer daemon cannot reach the registry for pre-storage verification"
grep -Fq 'sha256sum -- "$(basename "${iso}")"' .github/workflows/installer-iso.yml \
    || fail "ISO checksum does not use a portable basename"
grep -Fq 'runroot_source="${IMAGE_PATH}.runroot"' .github/actions/setup-compressed-storage/action.yml \
    || fail "compressed storage runroot source is not on the image's disk filesystem"
grep -Fq 'findmnt -n -o MAJ:MIN' .github/actions/setup-compressed-storage/action.yml \
    || fail "compressed storage action does not verify backing devices"
grep -Fq 'findmnt -n -o FSTYPE' .github/actions/setup-compressed-storage/action.yml \
    || fail "compressed storage action does not verify backing filesystems"
grep -Fq '[[ "${graphroot}" == "${MOUNT_POINT}" ]]' .github/actions/setup-compressed-storage/action.yml \
    || fail "compressed storage action does not verify the configured graphroot"
grep -Fq '[[ "${runroot}" == "${RUNROOT}" ]]' .github/actions/setup-compressed-storage/action.yml \
    || fail "compressed storage action does not verify the configured runroot"
grep -Fq 'cosign sign-blob --yes --bundle' .github/workflows/installer-iso.yml \
    || fail "ISO checksum is not signed"
grep -Fq 'cosign verify-blob' .github/workflows/installer-iso.yml \
    || fail "ISO checksum signature is not verified in CI"
grep -Fq 'rowyn-custom-installer-*.iso.sigstore.json' .github/workflows/installer-iso.yml \
    || fail "ISO signature bundle is not uploaded"
grep -Fq 'skopeo inspect --no-creds' .github/workflows/installer-iso.yml \
    || fail "custom installer does not assert anonymous source-image access"
grep -Fq 'Rowyn uses the Ubuntu kernel included in the signed image' build/custom-installer-iso.sh \
    || fail "custom installer script does not disable the unsupported Kyth kernel choice"
if grep -Fq 'KYTH_COSIGN_IDENTITY' build/custom-installer-iso.sh; then
    fail "custom installer signing identity must not be caller-overridable"
fi

python3 -B -m unittest discover -s build/tests -v

echo "[validate] Rowyn source and CI contracts passed"
