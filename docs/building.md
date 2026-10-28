# Building Rowyn and its installer

Rowyn has two build products: an Ubuntu bootc desktop image and an Ubuntu live ISO that installs a specific signed image. The live system, compiler toolchains, and ISO assembly environment use Ubuntu 26.10.

## Build the OS image

From the repository root, fetch the pinned offline inputs and build with Podman:

```bash
./build/fetch-assets.sh
sudo podman build -t rowyn:local .
```

The Containerfile builds bootc from the fetched source with offline Cargo dependencies. KDE Plasma and native OS dependencies are image content. Flatpak apps and rootless Ubuntu development containers are separate from the read-only host.

## Validate source changes

Requirements include Git, Python 3 with PyYAML, Bash, and ShellCheck. Run from the repository root:

```bash
./build/validate.sh
```

The gate checks shell syntax, ShellCheck errors, workflow YAML, Ubuntu-only build guards, image and installer trust contracts, and regression tests. Advisory ShellCheck warnings are reported separately.

Additional integration checks use a local Kyth checkout containing the pinned installer commit and the isolated Ubuntu runtime fixtures:

```bash
export ROWYN_TEST_KYTH_REPO=/path/to/kyth
./build/validate.sh
```

`ROWYN_TEST_UBUNTU_APP_POLICY=1` and `ROWYN_TEST_UBUNTU_NETWORK_POLICY=1` enable the Ubuntu application and network policy fixtures. `ROWYN_TEST_UBUNTU_PACKAGE_CLOSURE=1` enables the more expensive package-closure check. See the tests for fixture image names and prerequisites; these checks need Podman and are not all enabled by default.

Source validation does not replace booting the actual signed ISO, installing to a disposable disk, and booting that disk without the ISO.

## Build a custom installer ISO

Requirements: Podman, Git, Python 3 with PyYAML, network access, ample disk space, and a local Kyth checkout containing commit `2baf8496b5aa904c0784f4290df743ee17edae48`.

Choose an existing signed Rowyn payload. Obtain its digest and original source commit from that payload's build metadata; do not resolve a mutable tag as the installation source. Replace the example values below before running:

```bash
export TMPDIR="$HOME/.cache/rowyn"
mkdir -p "$TMPDIR"
export SOURCE_TAG='REPLACE_WITH_PAYLOAD_SOURCE_COMMIT'
export IMAGE_DIGEST='sha256:REPLACE_WITH_VERIFIED_PAYLOAD_DIGEST'
export IMAGE_REF="ghcr.io/kyth-os/rowyn:${SOURCE_TAG}"
export KYTH_SOURCE_DIR=/path/to/kyth
export KYTH_INSTALLER_COMMIT=2baf8496b5aa904c0784f4290df743ee17edae48
export UBUNTU_LIVE_IMAGE=docker.io/library/ubuntu@sha256:93e29beff5257b2cdf1c024d831f6d2997e53655a653ff76a811fc9e6564a719
ROWYN_CUSTOM_INSTALLER_PREFLIGHT=1 ./build/custom-installer-iso.sh
./build/custom-installer-iso.sh
```

Use a writable scratch filesystem rather than a RAM-backed directory. The build exports source and creates its working files beneath `TMPDIR`. OS compilation and filesystem tooling run in Ubuntu containers, without a host chroot or host package installation. ISO assembly receives output and scratch directories, not host block devices.

### Ubuntu live environment

`build/ubuntu-live.Containerfile` starts its compiler and live stages from the digest-pinned official Ubuntu image. Apt supplies Rust, Cargo, Node, GTK/WebKit, Plasma, Casper, initramfs-tools, GRUB, xorriso, and SquashFS tools. It does not execute an upstream OS Containerfile or ISO wrapper.

The Apache-2.0 Kyth installer daemon, typed execution helper, Tauri shell and web frontend, shared crate, and vendored glib dependency are exported from the pinned commit using an allowlist and `git archive`. The source checkout is not modified. The Slint UI binary is not built or installed. Licenses and the modification notice ship in `/usr/share/doc/rowyn-installer`.

`build/ubuntu-installer-source.py` adapts account groups (`sudo`, not `wheel`), tool paths, locale and keyboard configuration, and image selection for Ubuntu. Kernel selection cannot rewrite the immutable image digest.

Casper and initramfs-tools build the live initramfs. The installed system retains the payload's matching kernel and bootc/dracut initramfs. The Ubuntu-built bootc binary and `/usr/lib/bootc` helpers come from the exact signed payload; the live stage checks their Ubuntu runtime dependencies before ISO assembly. Installation uses the composefs backend and systemd-boot.

Ubuntu's `casper(7)` manual and the Ubuntu Community Help `LiveCDCustomization` and `LiveCDCustomizationFromScratch` guides describe the live layout: kernel and SquashFS under `/casper`, plus `.disk/info`. The payload is not used as the live root filesystem.

### Installation scope

The enabled path is x86-64 UEFI, Secure Boot disabled, whole-disk Btrfs, without encryption. BIOS/CSM, Secure Boot/MOK enrollment, manual partitioning, Windows/NTFS resize, partition replacement, free-space installation, and TPM encryption are disabled. The daemon rejects unsupported paths as well as the UI, including the separate manual-disk mutation API.

Recorded acceptance for ISO run `40594582203` covered installation to a disposable UEFI VM disk, disk-only desktop boot, networking, Flatpak apps, rootless Ubuntu containers, the read-only OS, and persistence across reboot. That result applies to that tested artifact. Cross-version bootc upgrade and rollback qualification remains outstanding, and VM testing does not establish physical-hardware compatibility.

### Storage and signature safeguards

The ISO workflow verifies the fixed payload signer before privileged storage setup. The live daemon verifies the exact compiled-in Rowyn source, target, and Cosign signer in Prepare, before storage mutation, and checks again in Image. Runtime binary, library, and CLI checks also run before storage changes.

The installation needs anonymous registry access and network connectivity because payload layers are not embedded in the ISO. Casper `/cdrom` and `/isodevice` mounts are protected, including loop ISO backing files resolved to physical media. Unresolved backing devices fail closed. The privileged daemon retains its sandbox and authenticated live-user socket; only the argument-free session launcher is passwordless.

Keep the live ISO attached until the live system has completely shut down. Removing media while Casper still uses its SquashFS root causes I/O failures. Detach it only before starting the installed system.

## Verify an ISO

Extract the ISO, checksum, and signature bundle into the same directory. Set `ISO` to the actual downloaded basename, then verify the signed checksum manifest before checking the ISO:

```bash
ISO='rowyn-custom-installer-REPLACE_WITH_SOURCE_COMMIT.iso'
cosign verify-blob \
  --bundle "${ISO}.sigstore.json" \
  --certificate-identity-regexp '^https://github.com/kyth-os/Rowyn/\.github/workflows/installer-iso\.yml@refs/heads/main$' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  "${ISO}.sha256"
sha256sum -c "${ISO}.sha256"
```

Both commands must succeed. A checksum alone does not authenticate its source.

The assembler emits the ISO, source metadata, El Torito/system-area report, and SquashFS report. CI signs and verifies the portable checksum manifest and uploads the signature bundle. Apt resolves packages from Ubuntu repositories, so the package manifest is recorded but the build is not bit-for-bit reproducible.

## CI and release flow

`build.yml` (Rowyn CI) validates, builds, smoke-tests, publishes, and signs the payload on trusted main pushes affecting payload inputs, or manual main dispatch. Pull requests validate only; they cannot publish images or request ISO releases.

The fixed payload signing identity is:

```text
https://github.com/kyth-os/Rowyn/.github/workflows/build.yml@refs/heads/main
```

`installer-iso.yml` runs after a successful trusted main image release. It checks the upstream run through the GitHub API, reads only that run's image metadata artifact, checks its SHA/run binding, and verifies the image signer before privileged work. Installer code comes from trusted main, not from the triggering run. The installation source is `ghcr.io/kyth-os/rowyn@sha256:…`; tags are provenance labels, not installation-source selectors.

ISO-only changes validate the installer without rebuilding the payload or selecting a mutable latest image. To retry against an existing signed payload, replace both values with its exact digest and original source commit:

```bash
gh workflow run installer-iso.yml --ref main \
  -f image_digest='sha256:REPLACE_WITH_VERIFIED_PAYLOAD_DIGEST' \
  -f source_sha='REPLACE_WITH_PAYLOAD_SOURCE_COMMIT'
```

Automatic runs require the upstream image metadata artifact, retained for seven days. Manual retries do not depend on that artifact. ISO artifacts are also retained for seven days. GitHub-hosted builds use the narrow rootful Podman wrapper in the host cgroup namespace; local builds use rootless Podman.

## Attribution

Rowyn uses an Ubuntu port of Kyth's custom installer. Upstream installer source is Apache-2.0; Tauri is MIT/Apache-2.0; the vendored glib Rust backport declares MIT. The live installer includes dependency license texts.
