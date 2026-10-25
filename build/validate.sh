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

test -s Containerfile || fail "Containerfile is missing or empty"
test -s os-release || fail "os-release is missing or empty"
test -s keys/spike.pub || fail "spike public key is missing"

grep -Fq 'LABEL containers.bootc 1' Containerfile \
    || fail "Containerfile is missing containers.bootc label"
grep -Fq 'RUN bootc container lint' Containerfile \
    || fail "Containerfile is missing bootc container lint"
grep -Fq 'mkdir -p /var/lib/apt/lists/partial' Containerfile \
    || fail "KDE apt layer must recreate apt lists/partial after rootfs normalization"
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
grep -Fq 'IMAGE_DIGEST' .github/workflows/build.yml \
    || fail "ISO job does not consume the published image digest"

echo "[validate] Rowyn source and CI contracts passed"
