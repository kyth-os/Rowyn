#!/usr/bin/env bash
# Vendored from Bootcrew (Apache-2.0; see ../NOTICE), restructured for
# Rowyn: the bootc source tree (with vendored crate deps) is fetched on the
# host by build/fetch-assets.sh and passed in as $1, so this step — and the
# whole image build — needs no network.

set -xeuo pipefail

SRC="${1:?usage: build.sh <bootc source dir>}"
cd "$SRC"

CARGO_NET_OFFLINE=true make bin install-all DESTDIR=/output
