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

rm -rf build-assets/bootc
git clone --depth 1 https://github.com/bootc-dev/bootc.git build-assets/bootc

# Vendor all crate dependencies into the source tree and pin cargo to them.
# `cargo vendor` prints the matching source-replacement config; capture it
# verbatim — it covers the crates.io deps AND bootc's one git dependency
# (bcvk-qemu, used by the manpage/xtask tooling) — and merge it under bootc's
# own cargo aliases.
(cd build-assets/bootc && cargo vendor vendor > ../vendor-config.txt)
mkdir -p build-assets/bootc/.cargo
{
    printf '[alias]\nxtask = "run --package xtask --"\n\n'
    cat build-assets/vendor-config.txt
} > build-assets/bootc/.cargo/config.toml
rm -f build-assets/vendor-config.txt

echo "assets ready:"
du -sh build-assets
