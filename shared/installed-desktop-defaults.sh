#!/usr/bin/env bash
# Install bounded first-boot policy for the installed KDE/SDDM payload.
#
# The payload is composed on a stateless /usr. Keep mutable overrides in /etc,
# mask only the confirmed Ubuntu Budgie cache units made unused by Breeze, and
# never make /usr writable.
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

# The package is selected explicitly in Containerfile. This late /etc drop-in
# overrides the vendor Budgie theme, QML import path, and display setup hook.
install -d "$(root_path /etc/sddm.conf.d)"
cat > "$(root_path /etc/sddm.conf.d/20-rowyn-desktop.conf)" <<'EOF'
[General]
DisplayServer=x11
InputMethod=qtvirtualkeyboard
GreeterEnvironment=QT_IM_MODULE=qtvirtualkeyboard

[Theme]
Current=breeze
ThemeDir=/usr/share/sddm/themes
CursorTheme=Breeze

[X11]
DisplayCommand=/usr/share/sddm/scripts/Xsetup
EOF
chmod 0644 "$(root_path /etc/sddm.conf.d/20-rowyn-desktop.conf)"

# Ubuntu's Budgie payload installs these three independent activation paths.
# Breeze does not consume the cache, and the updater writes both its cache and
# AccountsService QML under immutable /usr. Mask only these confirmed units.
for unit in \
    sddm-backgrounds-cache.service \
    sddm-backgrounds-cache.path \
    sddm-backgrounds-cache.timer; do
    vendor="$(root_path "/usr/lib/systemd/system/$unit")"
    [[ -f "$vendor" ]] || { echo "missing confirmed Budgie cache unit: $unit" >&2; exit 1; }
    target="$(root_path "/etc/systemd/system/$unit")"
    install -d "$(dirname "$target")"
    rm -f "$target"
    ln -s /dev/null "$target"
done

# Override the vendor basename in /etc so tmpfiles applies L!+ to an empty,
# regular, or stale resolver path after the stateless root has been mounted.
# The target remains on /run; systemd-resolved stays enabled by Containerfile.
install -d "$(root_path /etc/tmpfiles.d)"
cat > "$(root_path /etc/tmpfiles.d/systemd-resolve.conf)" <<'EOF'
# ! applies this at boot; + replaces a stale regular file captured by the image.
L!+ /etc/resolv.conf - - - - /run/systemd/resolve/stub-resolv.conf
EOF
chmod 0644 "$(root_path /etc/tmpfiles.d/systemd-resolve.conf)"
