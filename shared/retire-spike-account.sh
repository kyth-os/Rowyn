#!/usr/bin/env bash
# Retire the development "spike" account on already-installed systems.
#
# Early Rowyn images shipped a built-in `rowyn` account (password `rowyn`,
# passwordless sudo, SSH key, kept as shared/retired-dev-key.pub). Installed systems keep that
# account across upgrades because /etc/passwd, /etc/shadow and /etc/group are
# locally modified and ostree's 3-way /etc merge preserves them, as does
# /var/home/rowyn/.ssh/authorized_keys.
#
# The spike account is identified ONLY by its SSH key fingerprint: a user that
# merely happens to be named `rowyn` (the installer creates the real user) is
# left untouched. The account and its home are locked, never deleted.
# Idempotent: once the key line is gone, later runs change nothing.
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

user=rowyn
spike_pub=$(root_path /usr/share/rowyn/retired-dev-key.pub)
passwd=$(root_path /etc/passwd)
authkeys=$(root_path /var/home/rowyn/.ssh/authorized_keys)
sudoers=$(root_path /etc/sudoers.d/90-rowyn-spike)

[[ -f "$passwd" ]] && grep -q "^${user}:" "$passwd" || exit 0
[[ -f "$authkeys" && -f "$spike_pub" ]] || exit 0

# Print "<type> <base64>" for every key in a file, skipping comments and any
# leading authorized_keys options. Options may contain quoted spaces, so find
# the first field that looks like a key type.
fingerprints() {
    awk '
        /^[[:space:]]*(#|$)/ { next }
        {
            for (i = 1; i <= NF; i++) {
                if ($i ~ /^(ssh-|ecdsa-|sk-)/ && (i + 1) <= NF) {
                    print $i, $(i + 1)
                    break
                }
            }
        }
    ' "$1"
}

# Print the line numbers of authorized_keys lines whose key matches.
matching_lines() {
    awk -v pubs="$(fingerprints "$spike_pub")" '
        BEGIN {
            n = split(pubs, a, "\n")
            for (i = 1; i <= n; i++) if (a[i] != "") want[a[i]] = 1
        }
        /^[[:space:]]*(#|$)/ { next }
        {
            for (i = 1; i <= NF; i++) {
                if ($i ~ /^(ssh-|ecdsa-|sk-)/ && (i + 1) <= NF) {
                    if (($i " " $(i + 1)) in want) print NR
                    break
                }
            }
        }
    ' "$authkeys"
}

mapfile -t hits < <(matching_lines)
((${#hits[@]})) || exit 0

# Lock first and drop the key last: the key is the only marker that identifies
# this account, so an interrupted run must still match on the next boot.
usermod --root "$root" --lock --expiredate 1 "$user"
echo "rowyn-retire-spike-account: locked and expired account ${user}"

if grep -Eq "^sudo:[^:]*:[^:]*:([^:]*,)?${user}(,[^:]*)?$" "$(root_path /etc/group)"; then
    if gpasswd --root "$root" -d "$user" sudo >/dev/null 2>&1; then
        echo "rowyn-retire-spike-account: removed ${user} from group sudo"
    fi
fi

if [[ -e "$sudoers" ]]; then
    rm -f "$sudoers"
    echo "rowyn-retire-spike-account: removed ${sudoers}"
fi

# Rewrite authorized_keys without the spike key, preserving owner and mode.
tmp=$(mktemp "$(dirname "$authkeys")/.authorized_keys.XXXXXX")
trap 'rm -f "$tmp"' EXIT
awk -v hits="${hits[*]}" '
    BEGIN { n = split(hits, a, " "); for (i = 1; i <= n; i++) drop[a[i]] = 1 }
    !(NR in drop)
' "$authkeys" > "$tmp"
chown --reference="$authkeys" "$tmp"
chmod --reference="$authkeys" "$tmp"
mv -f "$tmp" "$authkeys"
echo "rowyn-retire-spike-account: removed ${#hits[@]} spike key line(s) from ${authkeys}"
