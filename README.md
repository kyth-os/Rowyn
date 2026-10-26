# Rowyn

Rowyn is an Ubuntu 26.10 bootc image with KDE Plasma.

## Local image build

Fetch the pinned offline build inputs, then build the image with Podman:

```bash
./build/fetch-assets.sh
sudo podman build -t rowyn:spike .
```

## Custom installer ISO

The live system, compiler toolchains, and ISO assembly environment are all
Ubuntu 26.10; the installed system is the separately published Ubuntu bootc
payload. `build/ubuntu-live.Containerfile` starts its compiler and live stages
from the digest-pinned official Ubuntu image in `installer-iso.yml`. Apt supplies
Rust, Cargo, Node, GTK/WebKit, Plasma, Casper, initramfs-tools, GRUB, xorriso and
SquashFS tools. Neither an upstream OS Containerfile nor an upstream ISO wrapper
is executed. There is no Fedora live base, RPM package transaction, or Titanoboa
assembly step.

The Apache-2.0 Kyth installer Rust daemon, typed execution helper, Tauri
shell and web frontend, required shared crate and vendored glib dependency are
exported from commit
`2baf8496b5aa904c0784f4290df743ee17edae48`. Only that allowlisted source is
exported with `git archive`; the input checkout is not modified. Licenses and a
modification notice are shipped in `/usr/share/doc/rowyn-installer`. The live
system installs the Tauri shell (with the web frontend embedded, launched by
the upstream session launcher exactly as Kyth launches it), the daemon and the
typed helper; the crate's native Slint UI binary is not built or installed.

`build/ubuntu-installer-source.py` ports account groups (`sudo`, not `wheel`),
Ubuntu tool paths, locale/keyboard configuration, kernel/image selection, and
removes the Kyth-only installed-system account-repair call. A kernel selector
can never rewrite the immutable image digest. The live initramfs is built by
Casper/initramfs-tools; installed Rowyn keeps its original matching kernel and
bootc/dracut initramfs. `shared/initramfs.sh` is unchanged. The Ubuntu-built
`bootc` binary and its small `/usr/lib/bootc` helper directory come from the
exact signed payload, with Ubuntu apt libraries and helpers checked in the
live stage before ISO assembly. Bootc is explicitly instructed to use
`--bootloader=systemd`, matching the payload's systemd-boot EFI files.

### Scope and safety

This port is **not boot/installation-qualified yet**. Its enabled candidate
path is x86-64 UEFI, Secure Boot **disabled**, whole-disk Btrfs, **unencrypted**.
BIOS/CSM, Secure Boot/MOK enrollment, manual partitioning, Windows/NTFS resize,
partition replacement, free-space and TPM encryption are deliberately disabled,
not silently treated as working. The native UI marks these modes unavailable;
the daemon also rejects them, including the separate manual-disk mutation API
which otherwise bypasses the job's Prepare phase. A real UEFI VM boot, session
launch, installation to a disposable virtual disk and reboot of that disk are
required before claiming bootability or installation acceptance.

The workflow verifies the fixed `build.yml@refs/heads/main` payload signer
before privileged storage setup. The live daemon unconditionally verifies the
exact compiled-in Rowyn source/target and Cosign signer in **Prepare, before any
storage mutation**, and repeats verification in Image. Anonymous registry
access and network connectivity are required during installation; payload
layers are not embedded. Casper `/cdrom` and `/isodevice` mounts are protected,
including loop ISO backing files resolved to physical media. An unresolved
backing device fails closed. Runtime binary/library/CLI checks also fail before
storage changes. The privileged daemon retains its sandbox and authenticated
liveuser socket; only the argument-free session launcher is passwordless.

The assembler uses Ubuntu's `mksquashfs` and UEFI `grub-mkrescue`/xorriso inside
a container with no host devices or privileged flag. It emits the ISO, source
metadata, El Torito/system-area report and SquashFS report. CI signs and verifies
the portable ISO checksum manifest, then uploads the artifact and signature
bundle. Apt packages are resolved from current Ubuntu repositories, so the
package manifest is recorded but the build is not bit-for-bit reproducible.

### Local execution and validation

Requirements: Podman, Git, Python 3 with PyYAML, network access, ample disk space,
and a local Kyth repository containing the pinned commit. No host package
installation or host chroot is performed. Set `TMPDIR` to a writable scratch
filesystem (not a RAM-backed directory); all exported source/build scratch is
placed beneath it. For example, using the exact signed payload above:

```bash
export TMPDIR="$HOME/.hermes/cache/scratch"
export IMAGE_REF=ghcr.io/kyth-os/rowyn:009880f4e4a4e83ba490d879756911b47233e483
export IMAGE_DIGEST=sha256:e24caab597cd24970714f776538cb3eaa9578d7c4b58e0784faa843ab32a2d45
export SOURCE_TAG=009880f4e4a4e83ba490d879756911b47233e483
export KYTH_SOURCE_DIR=/path/to/kyth
export KYTH_INSTALLER_COMMIT=2baf8496b5aa904c0784f4290df743ee17edae48
export UBUNTU_LIVE_IMAGE=docker.io/library/ubuntu@sha256:93e29beff5257b2cdf1c024d831f6d2997e53655a653ff76a811fc9e6564a719
ROWYN_CUSTOM_INSTALLER_PREFLIGHT=1 ./build/custom-installer-iso.sh
./build/custom-installer-iso.sh
```

`./build/validate.sh` checks executable Ubuntu-only build guards, shell/YAML,
workflow trust and safe regression tests. Set `ROWYN_TEST_KYTH_REPO` to enable
real pinned-source export/adapter tests. The Ubuntu compiler additionally runs
Rust tests extracted from the actual adapted shared image-source functions,
checking digest preservation for every kernel-selector input. These tests and
structure reports do **not** substitute for VM acceptance.

References: Ubuntu's `casper(7)` manual and the Ubuntu Community Help
`LiveCDCustomization` / `LiveCDCustomizationFromScratch` guides describe the
Casper initramfs, `/casper` SquashFS/kernel layout and `.disk/info` requirement.
This port uses that layout rather than treating the bootc payload as a live
filesystem.

The uploaded checksum bundle can be verified with Cosign, then the checksum can
be checked against the ISO:

```bash
cosign verify-blob \
  --bundle rowyn-custom-installer-<commit>.iso.sigstore.json \
  --certificate-identity-regexp '^https://github.com/kyth-os/Rowyn/\.github/workflows/installer-iso\.yml@refs/heads/main$' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  rowyn-custom-installer-<commit>.iso.sha256
sha256sum -c rowyn-custom-installer-<commit>.iso.sha256
```

## Independent CI releases

`build.yml` (Rowyn CI) validates, builds, smoke-tests, publishes and signs the
payload on trusted main pushes affecting payload inputs, or manual main dispatch.
Its signing identity remains
`https://github.com/kyth-os/Rowyn/.github/workflows/build.yml@refs/heads/main`.
Pull requests validate only and cannot publish images or request ISO releases.

`installer-iso.yml` automatically runs after a successful trusted main image
release. It checks the upstream run through the GitHub API and reads only that
run's exact image metadata artifact (never its code), checks its SHA/run binding,
and verifies the fixed image signer before privileged work. It always executes
installer code checked out from trusted main, not from the triggering run.
The canonical installer source is `ghcr.io/kyth-os/rowyn@sha256:...`; tags are
used only as payload provenance labels, never to resolve the install source.
The ISO checksum signer is the separate `installer-iso.yml@refs/heads/main`
identity shown above; the payload signer does not change.

ISO-only main pushes validate the installer but do **not** rebuild the payload
or automatically select a mutable/latest image. Use a manual ISO-only retry with
an exact existing digest and its original source commit, for example:

```bash
gh workflow run installer-iso.yml --ref main \
  -f image_digest=sha256:e24caab597cd24970714f776538cb3eaa9578d7c4b58e0784faa843ab32a2d45 \
  -f source_sha=009880f4e4a4e83ba490d879756911b47233e483
```

This verifies the existing digest's original payload signature and builds only
the live installer/ISO, not the Rowyn payload. Automatic runs require the image
metadata artifact to remain available (currently seven days); manual retries do
not depend on that artifact. On GitHub-hosted runners the narrow Podman wrapper
uses rootful Podman in the host cgroup namespace; local builds use rootless
Podman. All OS compilation and filesystem/ISO tooling runs in Ubuntu containers.
Assembly receives writable output/scratch directories, not host block devices.

## Attribution

The installer UI is upstream Kyth's Tauri shell and web frontend. Upstream
installer source is Apache-2.0; Tauri is MIT/Apache-2.0; the vendored glib
Rust backport declares MIT. Dependency license texts are included in the live
installer documentation.
