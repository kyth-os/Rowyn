# Rowyn

Rowyn is an Ubuntu 26.10 bootc image with KDE Plasma.

## Local image build

Fetch the pinned offline build inputs, then build the image with Podman:

```bash
./build/fetch-assets.sh
sudo podman build -t rowyn:spike .
```

## Custom installer ISO

The GitHub Actions pipeline publishes the exact image digest, signs it with
GitHub OIDC, and then builds `rowyn-custom-installer-<commit>.iso` with the
pinned Kyth custom installer environment and Titanoboa assembly path. The ISO
installs the signed Rowyn image; it does not use the distro-specific
`bootc-image-builder`/Anaconda path.

The custom installer source and its Fedora-based live builder are pinned in
`.github/workflows/installer-iso.yml`. Fedora is used only for the existing custom live
installer environment; Rowyn's installed payload remains the Ubuntu image. The
workflow requires anonymous access to the public immutable Rowyn registry image,
cryptographically verifies that image again from the live installer before
writing a disk, and publishes a signed ISO checksum bundle alongside the ISO.

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
not depend on that artifact. On GitHub-hosted runners a narrow Podman wrapper
keeps the host cgroup namespace instead of upstream's desktop/Distrobox
`unshare --cgroup` workaround, while preserving rootful Podman and all upstream
privileged live-ISO flags. Local upstream invocation is unchanged.
