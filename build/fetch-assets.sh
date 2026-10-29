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

# Committed pin: both values are fixed in-tree (not env-overridable) so the
# toolchain is verified against a reviewed checksum, not one served alongside it.
# Keep RUST_VERSION in sync with ARG RUST_VERSION in the Containerfile.
RUST_VERSION="1.99.0"
RUST_SHA256="891c6366d7100feda0bca4c03ce63f3c9ac827cbebbc283e7433061d42c6a376"
BOOTC_REPO="https://github.com/containers/bootc.git"
BOOTC_COMMIT="6d7893f2dbdd289a4a04a5727e4df29433d43024"
BOOTC_DIR="build-assets/bootc"
cd "$(dirname "$0")/.."
mkdir -p build-assets
export PATH="$HOME/.cargo/bin:$PATH"

TARBALL="rust-${RUST_VERSION}-x86_64-unknown-linux-gnu.tar.xz"
verify_rust() {
    printf '%s  %s\n' "${RUST_SHA256}" "$1" | sha256sum -c -
}
if [ -f "build-assets/${TARBALL}" ] && ! verify_rust "build-assets/${TARBALL}"; then
    echo "cached ${TARBALL} does not match the pinned checksum; removing" >&2
    rm -f "build-assets/${TARBALL}"
    exit 1
fi
if [ ! -f "build-assets/${TARBALL}" ]; then
    tmp="build-assets/${TARBALL}.part"
    rm -f "${tmp}"
    curl -fsSL -o "${tmp}" "https://static.rust-lang.org/dist/${TARBALL}"
    if ! verify_rust "${tmp}"; then
        rm -f "${tmp}"
        echo "downloaded ${TARBALL} does not match the pinned checksum" >&2
        exit 1
    fi
    mv "${tmp}" "build-assets/${TARBALL}"
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
