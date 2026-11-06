#!/usr/bin/env bash
# Provision cups-browsed declaratively and default the image to journald-only.
# All writes are rooted under --root; no account or service is changed live.
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
    root=${root%/}
    [[ -n "$root" ]] || root=/
fi
root_path() {
    if [[ "$1" == /* ]]; then
        printf '%s%s' "$root" "$1"
    else
        printf '%s/%s' "$root" "$1"
    fi
}

sysusers=$(root_path /usr/lib/sysusers.d/rowyn-cups-browsed.conf)
install -d "$(dirname "$sysusers")"
cat > "$sysusers" <<'EOF'
# The package unit runs as cups-browsed. A dynamically allocated system UID
# avoids colliding with Ubuntu's package accounts; sysusers preserves an
# existing passwd/group entry on upgrades.
g cups-browsed -
u cups-browsed - "CUPS Browse Service" -
EOF
chmod 0644 "$sysusers"

# A locally merged /etc can lack a newly introduced service identity even when
# the distro's conditional sysusers job skips. Run this one fixed declaration
# before CUPS discovery, without resetting or rewriting any existing account.
unit_dir=$(root_path /usr/lib/systemd/system)
install -d "$unit_dir/cups-browsed.service.d"
cat > "$unit_dir/rowyn-cups-account.service" <<'EOF'
[Unit]
Description=Ensure the Rowyn CUPS discovery service identity exists
After=systemd-sysusers.service
Before=cups-browsed.service

[Service]
Type=oneshot
User=root
ExecStart=/usr/bin/systemd-sysusers rowyn-cups-browsed.conf
RemainAfterExit=yes
TimeoutStartSec=30
EOF
cat > "$unit_dir/cups-browsed.service.d/20-rowyn-account.conf" <<'EOF'
[Unit]
Requires=rowyn-cups-account.service
After=rowyn-cups-account.service
EOF
chmod 0644 "$unit_dir/rowyn-cups-account.service" "$unit_dir/cups-browsed.service.d/20-rowyn-account.conf"

# Preserve an explicit local unit or drop-in as a complete opt-in: masking its
# socket while preserving its service would still break that configuration.
logging_opt_in=0
for unit in rsyslog.service syslog.socket; do
    target=$(root_path "/etc/systemd/system/$unit")
    if [[ -e "$target" || -L "$target" ]]; then
        if [[ ! -L "$target" ]] || [[ "$(readlink -- "$target")" != /dev/null ]]; then
            logging_opt_in=1
        fi
    fi
    if [[ -d "$target.d" ]]; then logging_opt_in=1; fi
    for override in "$target.d/"*.conf; do
        if [[ -e "$override" || -L "$override" ]]; then logging_opt_in=1; fi
    done
done
# A dangling symlink is intentional: systemd recognizes /dev/null as a mask.
# Admins can opt back in by unmasking both units and supplying their service
# identity/configuration. Journald policy and retention are left untouched.
if (( logging_opt_in == 0 )); then
    for unit in rsyslog.service syslog.socket; do
        target=$(root_path "/etc/systemd/system/$unit")
        install -d "$(dirname "$target")"
        if [[ ! -e "$target" && ! -L "$target" ]]; then ln -s /dev/null "$target"; fi
    done
fi
