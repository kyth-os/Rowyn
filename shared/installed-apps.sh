#!/usr/bin/env bash
# Install the bounded Ubuntu bootc application policy.
#
# The base image owns the immutable Ubuntu/Kubuntu desktop. GUI applications
# are discovered and installed by Flatpak/Discover into the persistent system
# installation at /var/lib/flatpak. CLI and development applications belong in
# rootless Podman/Distrobox. This helper deliberately performs no package
# installation, remote refresh, or application installation; it only writes
# local policy and Flatpak's supported static remote declaration.
set -euo pipefail

root=/
while (($#)); do
    case "$1" in
        --root)
            root=${2:?--root requires a path}
            shift 2
            ;;
        *)
            echo "usage: $0 [--root PATH]" >&2
            exit 2
            ;;
    esac
done

if [[ "$root" != / ]]; then
    root="${root%/}"
fi
root_path() {
    if [[ "$1" == /* ]]; then
        printf '%s%s' "$root" "$1"
    else
        printf '%s/%s' "$root" "$1"
    fi
}

script_dir=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_source="$script_dir/flathub-v1.flatpakrepo"
[[ -f "$repo_source" ]] || {
    echo "missing Flathub remote declaration: $repo_source" >&2
    exit 1
}

# Keep composed /var empty. Flatpak initializes /var/lib/flatpak on the
# installed system from the static remote declaration, including after an
# empty first-boot /var; never remove or replace existing application data.

# Flatpak officially consumes static .flatpakrepo files from /etc/flatpak/
# remotes.d. The checked-in artifact contains Flathub's GPGKey, so this path
# remains offline-safe and does not weaken signature verification.
install -d "$(root_path /etc/flatpak/remotes.d)"
install -m 0644 "$repo_source" \
    "$(root_path /etc/flatpak/remotes.d/flathub.flatpakrepo)"

# Ubuntu Discover bundles its APT backend and depends on the PackageKit DEB.
# Keep Discover and its Flatpak backend, but remove only the native app backends
# during image composition, never by making the installed /usr writable.
plugins_root="$(root_path /usr/lib)"
if [[ -d "$plugins_root" ]]; then
    find "$plugins_root" -path '*/plugins/discover/*' -type f \
        \( -name packagekit-backend.so -o -name snap-backend.so \) -delete
fi
install -d "$(root_path /etc/systemd/system)"
ln -sfn /dev/null "$(root_path /etc/systemd/system/packagekit.service)"

# Prefer an Ubuntu development container and rootless Podman; the Distrobox
# package's upstream default is Fedora Toolbox, which is not this OS's model.
install -d "$(root_path /etc/distrobox)"
cat > "$(root_path /etc/distrobox/distrobox.conf)" <<'EOF'
container_manager="podman"
container_image_default="docker.io/library/ubuntu:26.10"
EOF
chmod 0644 "$(root_path /etc/distrobox/distrobox.conf)"

# Keep the app split explicit and auditable without modifying the immutable
# desktop payload or installing a GUI application during image composition.
policy="$(root_path /usr/share/rowyn/installed-apps/policy.conf)"
install -d "$(dirname "$policy")"
cat > "$policy" <<'EOF'
# Rowyn Ubuntu/Kubuntu bootc application policy.
# This is policy only: no application payload is preloaded here.
GuiApplications=flatpak
CliApplications=podman+distrobox
PersistentInstallation=/var/lib/flatpak
NativeGuiPackages=disabled
SnapGuiPackages=disabled
PackageKitGuiPackages=disabled
FlatpakRemote=flathub
FlatpakRemoteConfig=/etc/flatpak/remotes.d/flathub.flatpakrepo
EOF
chmod 0644 "$policy"
