#!/usr/bin/env bash
# Install the Rowyn Plymouth boot splash into a payload root:
#
#   * the script-module theme (rowyn.plymouth + rowyn.script) under
#     /usr/share/plymouth/themes/rowyn/ — the logo PNG is copied there by
#     the Containerfile from docs/assets/rowyn-logo.png and must already
#     be present; this script fails closed without it, because a theme
#     whose logo is missing would boot to a blank splash
#   * /etc/plymouth/plymouthd.conf naming the theme as the daemon
#     default — this is what dracut's plymouth module reads (via
#     plymouth-populate-initrd) when it folds the theme into the
#     initramfs, and what plymouthd reads at boot
#   * the splash kernel arguments as a bootc kargs.d drop-in, so
#     installed and updated deployments boot with `quiet splash`
#
# Runs at image build, before shared/initramfs.sh generates the
# initramfs; that script verifies the theme actually landed inside the
# initramfs and fails the build otherwise.
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

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
theme_source="$script_dir/plymouth"
theme_dir=/usr/share/plymouth/themes/rowyn

logo="$(root_path "$theme_dir/rowyn-logo.png")"
if [[ ! -s "$logo" ]]; then
    echo "plymouth-theme: $logo is missing or empty; the Containerfile must copy docs/assets/rowyn-logo.png into the theme directory first" >&2
    exit 1
fi

install -Dm644 "$theme_source/rowyn.plymouth" "$(root_path "$theme_dir/rowyn.plymouth")"
install -Dm644 "$theme_source/rowyn.script" "$(root_path "$theme_dir/rowyn.script")"

install -d "$(root_path /etc/plymouth)"
cat > "$(root_path /etc/plymouth/plymouthd.conf)" <<'EOF'
# Rowyn boot splash: draw the Rowyn theme immediately, with the same
# device timeout Kyth's daemon configuration pins. (ShowDelay and
# DeviceTimeout also appear in the package's plymouthd.defaults; they
# are restated here so the splash contract lives in one visible place.)
[Daemon]
Theme=rowyn
ShowDelay=0
DeviceTimeout=8
EOF
chmod 0644 "$(root_path /etc/plymouth/plymouthd.conf)"

install -Dm644 "$theme_source/99-rowyn-splash.toml" \
    "$(root_path /usr/lib/bootc/kargs.d/99-rowyn-splash.toml)"
