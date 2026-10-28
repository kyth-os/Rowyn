#!/usr/bin/env bash
# Fetch every network input the image build needs into build-assets/, so the
# podman build itself runs fully offline. Run on the host (needs network);
# re-run to refresh. Everything downloaded is checksum-verified where the
# upstream publishes one.
#
# Produces:
#   build-assets/rust-<ver>-x86_64-unknown-linux-gnu.tar.xz  (pinned toolchain)
#   build-assets/bootc/                                       (bootc source, vendored deps)
set -euo pipefail

RUST_VERSION="${RUST_VERSION:-1.99.0}"
BOOTC_REPO="https://github.com/containers/bootc.git"
BOOTC_COMMIT="6d7893f2dbdd289a4a04a5727e4df29433d43024"
BOOTC_DIR="build-assets/bootc"
cd "$(dirname "$0")/.."
mkdir -p build-assets
export PATH="$HOME/.cargo/bin:$PATH"

TARBALL="rust-${RUST_VERSION}-x86_64-unknown-linux-gnu.tar.xz"
if [ ! -f "build-assets/${TARBALL}" ]; then
    curl -fsSL -o "build-assets/${TARBALL}" \
        "https://static.rust-lang.org/dist/${TARBALL}"
    curl -fsSL -o "build-assets/${TARBALL}.sha256" \
        "https://static.rust-lang.org/dist/${TARBALL}.sha256"
    (cd build-assets && sha256sum -c "${TARBALL}.sha256")
fi

rm -rf "${BOOTC_DIR}"
mkdir -p "${BOOTC_DIR}"
git -C "${BOOTC_DIR}" init
git -C "${BOOTC_DIR}" remote add origin "${BOOTC_REPO}"
git -C "${BOOTC_DIR}" fetch --no-tags --depth 1 origin "${BOOTC_COMMIT}"
git -C "${BOOTC_DIR}" checkout --detach --force "${BOOTC_COMMIT}"
actual_head="$(git -C "${BOOTC_DIR}" rev-parse --verify HEAD)"
if [[ "${actual_head}" != "${BOOTC_COMMIT}" ]]; then
    echo "bootc source HEAD mismatch: expected ${BOOTC_COMMIT}, got ${actual_head}" >&2
    exit 1
fi

cat > build-assets/bootc-source.provenance.json <<EOF
{
  "repository": "${BOOTC_REPO}",
  "commit": "${BOOTC_COMMIT}",
  "verified_head": "${actual_head}"
}
EOF

# Vendor all crate dependencies into the source tree and pin cargo to them.
# `cargo vendor` prints the matching source-replacement config; capture it
# verbatim — it covers the crates.io deps AND bootc's one git dependency
# (bcvk-qemu, used by the manpage/xtask tooling) — and merge it under bootc's
# own cargo aliases.
(cd "${BOOTC_DIR}" && cargo vendor vendor > ../vendor-config.txt)
mkdir -p "${BOOTC_DIR}/.cargo"
{
    printf '[alias]\nxtask = "run --package xtask --"\n\n'
    cat build-assets/vendor-config.txt
} > "${BOOTC_DIR}/.cargo/config.toml"
rm -f build-assets/vendor-config.txt

echo "assets ready:"
du -sh build-assets
