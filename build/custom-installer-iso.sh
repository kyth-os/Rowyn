#!/usr/bin/env bash
# Build Rowyn's ISO with the Kyth custom installer environment.
#
# The live shell/daemon and Titanoboa ISO assembly come from a pinned KythOS
# source snapshot. Rowyn remains the only installed payload: the exact,
# signed Rowyn image built by this workflow is handed to the installer as the
# install source.
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
cd "${repo_root}"

: "${IMAGE_REF:?IMAGE_REF is required}"
: "${IMAGE_DIGEST:?IMAGE_DIGEST is required}"
: "${SOURCE_TAG:?SOURCE_TAG is required}"
: "${KYTH_SOURCE_DIR:?KYTH_SOURCE_DIR is required}"
: "${KYTH_INSTALLER_COMMIT:?KYTH_INSTALLER_COMMIT is required}"
: "${KYTH_INSTALLER_BASE_IMAGE:?KYTH_INSTALLER_BASE_IMAGE is required}"

[[ "${IMAGE_REF}" =~ ^ghcr\.io/kyth-os/rowyn:[0-9a-f]{40}$ ]] \
    || { echo "ERROR: unexpected Rowyn image ref: ${IMAGE_REF}" >&2; exit 1; }
[[ "${IMAGE_DIGEST}" =~ ^sha256:[0-9a-f]{64}$ ]] \
    || { echo "ERROR: invalid Rowyn image digest: ${IMAGE_DIGEST}" >&2; exit 1; }
[[ "${SOURCE_TAG}" == "${IMAGE_REF##*:}" ]] \
    || { echo "ERROR: SOURCE_TAG must match the immutable Rowyn image tag" >&2; exit 1; }
[[ "${KYTH_INSTALLER_COMMIT}" =~ ^[0-9a-f]{40}$ ]] \
    || { echo "ERROR: invalid Kyth installer commit: ${KYTH_INSTALLER_COMMIT}" >&2; exit 1; }
[[ "${KYTH_INSTALLER_BASE_IMAGE}" =~ ^ghcr\.io/kyth-os/kyth@sha256:[0-9a-f]{64}$ ]] \
    || { echo "ERROR: Kyth installer base must be a pinned image digest" >&2; exit 1; }

source_repo="$(git -C "${KYTH_SOURCE_DIR}" rev-parse --show-toplevel)"
source_commit="$(git -C "${source_repo}" rev-parse HEAD)"
[[ "${source_commit}" == "${KYTH_INSTALLER_COMMIT}" ]] \
    || { echo "ERROR: Kyth installer source is ${source_commit}, expected ${KYTH_INSTALLER_COMMIT}" >&2; exit 1; }

for required in \
    build_files/build-live-iso.sh \
    installer/Containerfile \
    installer/build.sh \
    installer/iso.yaml \
    src/kyth-installer-web/src-tauri/src/installer_job_executor.rs; do
    test -f "${source_repo}/${required}" \
        || { echo "ERROR: Kyth installer source is missing ${required}" >&2; exit 1; }
done

# Adapt the payload identity, trust gate, and capability defaults. The
# installer implementation and ISO assembler remain the pinned custom
# environment; the source and target images are the immutable Rowyn image.
python3 - "${source_repo}/installer/build.sh" "${IMAGE_DIGEST}" <<'PY'
from pathlib import Path
import sys

build_path = Path(sys.argv[1])
source_root = build_path.parents[1]
release_digest = sys.argv[2]
lines = build_path.read_text(encoding="utf-8").splitlines()

replacements = {
    "target_image=": 'target_image="${INSTALL_SOURCE_IMAGE}"',
    "cosign_identity=": r'cosign_identity="^https://github.com/kyth-os/Rowyn/\.github/workflows/build\.yml@refs/heads/main$"',
    "cosign_bin=": 'cosign_bin="/usr/bin/kyth-cosign"',
    "cosign_transient=": 'cosign_transient=""',
}
for prefix, replacement in replacements.items():
    exact = [index for index, line in enumerate(lines) if line.strip() == replacement]
    matches = [
        index
        for index, line in enumerate(lines)
        if line.lstrip().startswith(prefix) and line.strip() != replacement
    ]
    if len(exact) == 1:
        if matches:
            raise SystemExit(f"found both original and patched {prefix!r} lines in {build_path}")
        continue
    if exact or len(matches) != 1:
        raise SystemExit(f"expected one unpatched {prefix!r} line in {build_path}, found {len(matches)}")
    index = matches[0]
    indent = lines[index][: len(lines[index]) - len(lines[index].lstrip())]
    lines[index] = indent + replacement

build_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

ui_path = source_root / "src/kyth-installer-web/src/InstallerApp.tsx"
ui_text = ui_path.read_text(encoding="utf-8")
ui_old = 'options={["fedora", "cachyos"]} hint="CachyOS kernel: tuned for gaming and latency. Fedora: the stock, most conservative kernel."'
ui_new = 'options={["fedora"]} hint="Rowyn uses the Ubuntu kernel included in the signed image."'
if ui_text.count(ui_old) == 1 and ui_text.count(ui_new) == 0:
    ui_text = ui_text.replace(ui_old, ui_new)
elif ui_text.count(ui_old) != 0 or ui_text.count(ui_new) != 1:
    raise SystemExit(f"expected one unpatched or one patched Rowyn kernel-choice UI anchor in {ui_path}")
ui_path.write_text(ui_text, encoding="utf-8")

slint_path = source_root / "src/kyth-installer-web/src-tauri/ui/installer.slint"
slint_text = slint_path.read_text(encoding="utf-8")
slint_old = '                                Button { text: "CachyOS"; clicked => { kernel = "cachyos"; select-kernel("cachyos"); } }\n'
if slint_text.count(slint_old) == 1:
    slint_text = slint_text.replace(slint_old, "")
elif 'Button { text: "CachyOS"' in slint_text:
    raise SystemExit(f"expected one Rowyn kernel-choice native UI anchor in {slint_path}")
slint_path.write_text(slint_text, encoding="utf-8")

executor_path = source_root / "src/kyth-installer-web/src-tauri/src/installer_job_executor.rs"
executor_text = executor_path.read_text(encoding="utf-8")
constant = f'const ROWYN_RELEASE_DIGEST: &str =\n    "{release_digest}";'
constant_anchor = 'const FILESYSTEM_STAGING_MOUNTPOINT: &str = "/run/kyth-installer/alongside-target";\n'
if constant not in executor_text:
    if "const ROWYN_RELEASE_DIGEST:" in executor_text:
        raise SystemExit(f"Rowyn release digest is already patched to a different value in {executor_path}")
    if executor_text.count(constant_anchor) != 1:
        raise SystemExit(f"expected one Rowyn digest constant anchor in {executor_path}")
    executor_text = executor_text.replace(constant_anchor, constant_anchor + constant + "\n")

runtime_cosign = '''        if digest != ROWYN_RELEASE_DIGEST {
            return Err(refuse(format!(
                "docker image digest does not match the Rowyn release digest: {digest}"
            )));
        }
        let cosign_ref = source.strip_prefix("docker://").unwrap_or(source);
        let tuf_root = "/run/kyth-installer/sigstore-tuf";
        if let Err(error) = std::fs::create_dir_all(tuf_root) {
            return Err(refuse(format!(
                "could not prepare cosign trust cache: {error}"
            )));
        }
        let mut cosign = Command::new("/usr/bin/kyth-cosign");
        cosign
            .args([
                "verify",
                "--certificate-identity-regexp",
                "^https://github.com/kyth-os/Rowyn/\\\\.github/workflows/build\\\\.yml@refs/heads/main$",
                "--certificate-oidc-issuer",
                "https://token.actions.githubusercontent.com",
                cosign_ref,
            ])
            .env("TUF_ROOT", tuf_root);
        super::installer_stream::run_command_timeout(
            &mut cosign,
            || false,
            std::time::Duration::from_secs(180),
        )
        .map_err(|error| refuse(format!("cosign verification failed: {error}")))?;
'''
runtime_anchor = '        Ok(())\n    }\n\n    fn check_storage_preflight('
if 'Command::new("/usr/bin/kyth-cosign")' not in executor_text:
    if executor_text.count(runtime_anchor) != 1:
        raise SystemExit(f"expected one Rowyn runtime verification anchor in {executor_path}")
    executor_text = executor_text.replace(runtime_anchor, runtime_cosign + runtime_anchor)
early_anchor = '            Phase::Prepare => {\n                let power = crate::installer_orchestration::power_check();\n'
early_verified = '            Phase::Prepare => {\n                self.verify_install_source(phase)?;\n                let power = crate::installer_orchestration::power_check();\n'
if executor_text.count(early_verified) == 1 and executor_text.count(early_anchor) == 0:
    pass
elif executor_text.count(early_anchor) == 1 and executor_text.count(early_verified) == 0:
    executor_text = executor_text.replace(early_anchor, early_verified)
else:
    raise SystemExit(f"expected one unpatched or one unconditional Prepare gate in {executor_path}")
executor_path.write_text(executor_text, encoding="utf-8")

service_path = source_root / "build_files/kyth-installerd.service"
service_text = service_path.read_text(encoding="utf-8")
service_old = "RestrictAddressFamilies=AF_UNIX\n"
service_new = "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6\n"
if service_text.count(service_new) == 1:
    if service_text.count(service_old) != 0:
        raise SystemExit(f"found both original and patched network-family restrictions in {service_path}")
elif service_text.count(service_old) == 1:
    service_text = service_text.replace(service_old, service_new)
else:
    raise SystemExit(f"expected one unpatched or one patched network-family restriction in {service_path}")
service_path.write_text(service_text, encoding="utf-8")

PY

# Adapt only the hosted-runner invocation; local/Distrobox upstream behavior is
# unchanged. Install on every invocation, including preflight, for idempotence.
if [[ "${GITHUB_ACTIONS:-}" == true && "${RUNNER_ENVIRONMENT:-}" == github-hosted ]]; then
    install -m 0755 "${repo_root}/build/rootful-podman.sh" \
        "${source_repo}/build_files/scripts/rootful-podman.sh"
fi

if [[ "${ROWYN_CUSTOM_INSTALLER_PREFLIGHT:-0}" == "1" ]]; then
    bash -n "${source_repo}/build_files/build-live-iso.sh"
    bash -n "${source_repo}/installer/build.sh"
    python3 - "${source_repo}/installer/build.sh" <<'PY'
from pathlib import Path
import sys

build_path = Path(sys.argv[1])
source_root = build_path.parents[1]
lines = [line.strip() for line in build_path.read_text(encoding="utf-8").splitlines()]
assert lines.count('target_image="${INSTALL_SOURCE_IMAGE}"') == 1
assert lines.count(r'cosign_identity="^https://github.com/kyth-os/Rowyn/\.github/workflows/build\.yml@refs/heads/main$"') == 1
assert lines.count('cosign_bin="/usr/bin/kyth-cosign"') == 1
assert lines.count('cosign_transient=""') == 1
ui = (source_root / "src/kyth-installer-web/src/InstallerApp.tsx").read_text(encoding="utf-8")
assert ui.count('options={["fedora"]} hint="Rowyn uses the Ubuntu kernel included in the signed image."') == 1
assert 'options={["fedora", "cachyos"]}' not in ui
slint = (source_root / "src/kyth-installer-web/src-tauri/ui/installer.slint").read_text(encoding="utf-8")
assert 'Button { text: "CachyOS"' not in slint
executor = (source_root / "src/kyth-installer-web/src-tauri/src/installer_job_executor.rs").read_text(encoding="utf-8")
assert 'const ROWYN_RELEASE_DIGEST: &str =\n    "sha256:' in executor
assert 'Command::new("/usr/bin/kyth-cosign")' in executor
assert 'KYTH_EARLY_SOURCE_VERIFY' not in executor
prepare = executor.index('            Phase::Prepare => {')
power_check = executor.index('                let power = crate::installer_orchestration::power_check();', prepare)
assert executor[prepare:power_check] == ('            Phase::Prepare => {\n'
    '                self.verify_install_source(phase)?;\n')
storage_phase = executor.index('            Phase::Storage =>')
assert prepare < power_check < storage_phase
image = executor[executor.index('    fn execute_image('):]
assert image.index('self.verify_install_source(phase)?;') < image.index('self.check_storage_preflight(phase, cancellation)?;')
service = (source_root / "build_files/kyth-installerd.service").read_text(encoding="utf-8")
assert service.count("RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6") == 1
build = build_path.read_text(encoding="utf-8")
assert 'KYTH_EARLY_SOURCE_VERIFY' not in build
PY
    printf 'Rowyn custom installer source preflight passed at %s\n' "${KYTH_INSTALLER_COMMIT}"
    exit 0
fi

output_dir="${ROWYN_ISO_OUTPUT:-${repo_root}/output}"
output_dir="$(realpath -m "${output_dir}")"
mkdir -p "${output_dir}"

image_name="${IMAGE_REF%:*}"
[[ "${image_name}" == "ghcr.io/kyth-os/rowyn" ]] \
    || { echo "ERROR: unexpected Rowyn image name: ${image_name}" >&2; exit 1; }
source_image="${image_name}@${IMAGE_DIGEST}"
export SOURCE_TAG
export INSTALLER_BASE_IMAGE="${KYTH_INSTALLER_BASE_IMAGE}"
export INSTALL_SOURCE_IMAGE="${source_image}"
export KYTH_LIVE_TAG="localhost/rowyn-live:${SOURCE_TAG}"
export KYTH_ISO_OUTPUT="${output_dir}"
export TITANOBOA_DIR="${TITANOBOA_DIR:-${RUNNER_TEMP:-${repo_root}/.cache}/rowyn-titanoboa}"

(
    cd "${source_repo}"
    bash build_files/build-live-iso.sh
)

kyth_iso="${output_dir}/kyth-live-${SOURCE_TAG}.iso"
rowyn_iso="${output_dir}/rowyn-custom-installer-${SOURCE_TAG}.iso"
test -s "${kyth_iso}"
mv "${kyth_iso}" "${rowyn_iso}"

python3 - "${rowyn_iso}" "${output_dir}/rowyn-custom-installer-${SOURCE_TAG}.json" <<'PY'
import hashlib
import json
import os
import sys
from pathlib import Path

iso = Path(sys.argv[1])
metadata = Path(sys.argv[2])
hasher = hashlib.sha256()
byte_count = 0
with iso.open("rb") as stream:
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        hasher.update(chunk)
        byte_count += len(chunk)
hash_hex = hasher.hexdigest()
metadata.write_text(
    json.dumps(
        {
            "schema_version": 1,
            "artifact": iso.name,
            "sha256": f"sha256:{hash_hex}",
            "bytes": byte_count,
            "source_image": f"ghcr.io/kyth-os/rowyn@{os.environ['IMAGE_DIGEST']}",
            "source_digest": os.environ["IMAGE_DIGEST"],
            "kyth_installer_commit": os.environ["KYTH_INSTALLER_COMMIT"],
            "kyth_base_image": os.environ["KYTH_INSTALLER_BASE_IMAGE"],
            "signing_identity": "https://github.com/kyth-os/Rowyn/.github/workflows/build.yml@refs/heads/main",
        },
        indent=2,
    )
    + "\n",
    encoding="utf-8",
)
PY

printf 'Rowyn custom installer ISO: %s\n' "${rowyn_iso}"
sha256sum "${rowyn_iso}"
