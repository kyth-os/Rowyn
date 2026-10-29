#!/usr/bin/env bash
# Seed the per-user containers/image policy for new accounts via /etc/skel.
#
# The system policy installed by update-policy.sh is deliberately
# fail-closed (default: reject) because bootc update fetches are verified
# against it — bootc's enforced ContainerPolicy rejects a default
# insecureAcceptAnything system policy. That strictness must not leak into
# interactive rootless use, where podman/distrobox pulls legitimately come
# from any registry (Distrobox's default images live on quay.io, for
# example). containers/image reads $HOME/.config/containers/policy.json in
# preference to /etc/containers/policy.json for a user (see
# containers-policy.json(5)), so seeding a permissive default policy
# through /etc/skel gives every installer-created account a working
# rootless default. Root operations never see it: root's home is /root,
# which account creation never seeds from /etc/skel, so bootc keeps
# resolving to the strict system policy.
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

policy=$(root_path /etc/skel/.config/containers/policy.json)

install -d "$(dirname "$policy")"

cat > "$policy" <<'EOF'
{
  "default": [
    { "type": "insecureAcceptAnything" }
  ]
}
EOF
chmod 0644 "$policy"
