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
`.github/workflows/build.yml`. Fedora is used only for the existing custom live
installer environment; Rowyn's installed payload remains the Ubuntu image. The
workflow requires anonymous access to the public immutable Rowyn registry image,
cryptographically verifies that image again from the live installer before
writing a disk, and publishes a signed ISO checksum bundle alongside the ISO.

The uploaded checksum bundle can be verified with Cosign, then the checksum can
be checked against the ISO:

```bash
cosign verify-blob \
  --bundle rowyn-custom-installer-<commit>.iso.sigstore.json \
  --certificate-identity-regexp '^https://github.com/kyth-os/Rowyn/\.github/workflows/build\.yml@refs/heads/main$' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  rowyn-custom-installer-<commit>.iso.sha256
sha256sum -c rowyn-custom-installer-<commit>.iso.sha256
```
