#!/usr/bin/env bash
# Install non-destructive Breeze Dark defaults for KDE Plasma and GTK.
#
# System files are defaults: a user's ~/.config files take precedence. User
# files are copied only when absent; installed /var/home accounts use tmpfiles
# so the stateless bootc rootfs gets the same defaults on first boot.
set -euo pipefail

root=/
user=
home=
while (($#)); do
    case "$1" in
        --root)
            root=${2:?--root requires a path}
            shift 2
            ;;
        --user)
            user=${2:?--user requires a name}
            shift 2
            ;;
        --home)
            home=${2:?--home requires a path}
            shift 2
            ;;
        *)
            echo "usage: $0 [--root PATH] [--user NAME [--home PATH]]" >&2
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

install -d "$(root_path /etc/xdg)" "$(root_path /etc/skel/.config)" \
    "$(root_path /usr/share/rowyn/kde-dark-defaults)"

write_template() {
    local name=$1
    local content=$2
    local path
    path=$(root_path "/usr/share/rowyn/kde-dark-defaults/$name")
    printf '%s\n' "$content" > "$path"
    chmod 0644 "$path"
}

write_template kdeglobals '[KDE]
LookAndFeelPackage=org.kde.breezedark.desktop
widgetStyle=Breeze

[General]
ColorScheme=BreezeDark

[Icons]
Theme=breeze-dark'
write_template plasmarc '[Theme]
name=default'
write_template kwinrc '[org.kde.kdecoration2]
library=org.kde.breeze
theme=Breeze'
write_template kcminputrc '[Mouse]
cursorTheme=breeze_cursors'
write_template ksplashrc '[KSplash]
Theme=org.kde.breeze.desktop'
write_template gtk-3.0-settings.ini '[Settings]
gtk-theme-name=Breeze-Dark
gtk-icon-theme-name=breeze-dark
gtk-application-prefer-dark-theme=true'
write_template gtk-4.0-settings.ini '[Settings]
gtk-theme-name=Breeze-Dark
gtk-icon-theme-name=breeze-dark
gtk-application-prefer-dark-theme=true'

# These are the exact keys emitted by the installed org.kde.breezedark.desktop
# look-and-feel package, plus the package's explicit look-and-feel selector.
install -Dm644 "$(root_path /usr/share/rowyn/kde-dark-defaults/kdeglobals)" \
    "$(root_path /etc/xdg/kdeglobals)"
install -Dm644 "$(root_path /usr/share/rowyn/kde-dark-defaults/plasmarc)" \
    "$(root_path /etc/xdg/plasmarc)"
install -Dm644 "$(root_path /usr/share/rowyn/kde-dark-defaults/kwinrc)" \
    "$(root_path /etc/xdg/kwinrc)"
install -Dm644 "$(root_path /usr/share/rowyn/kde-dark-defaults/kcminputrc)" \
    "$(root_path /etc/xdg/kcminputrc)"
install -Dm644 "$(root_path /usr/share/rowyn/kde-dark-defaults/ksplashrc)" \
    "$(root_path /etc/xdg/ksplashrc)"
install -Dm644 "$(root_path /usr/share/rowyn/kde-dark-defaults/gtk-3.0-settings.ini)" \
    "$(root_path /etc/gtk-3.0/settings.ini)"
install -Dm644 "$(root_path /usr/share/rowyn/kde-dark-defaults/gtk-4.0-settings.ini)" \
    "$(root_path /etc/gtk-4.0/settings.ini)"

copy_if_missing() {
    local source=$1
    local destination=$2
    if [[ ! -e "$destination" ]]; then
        install -Dm644 "$source" "$destination"
    fi
}

# /etc/skel is used by future adduser/useradd calls and never overwrites a
# user's existing config. Keep the source templates outside /var because bootc
# normalization removes /var from the image during composition.
for pair in \
    'kdeglobals:.config/kdeglobals' \
    'plasmarc:.config/plasmarc' \
    'kwinrc:.config/kwinrc' \
    'kcminputrc:.config/kcminputrc' \
    'ksplashrc:.config/ksplashrc' \
    'gtk-3.0-settings.ini:.config/gtk-3.0/settings.ini' \
    'gtk-4.0-settings.ini:.config/gtk-4.0/settings.ini'; do
    template=${pair%%:*}
    relative=${pair#*:}
    copy_if_missing "$(root_path "/usr/share/rowyn/kde-dark-defaults/$template")" \
        "$(root_path "/etc/skel/$relative")"
done

if [[ -n "$user" ]]; then
    if [[ -z "$home" ]]; then
        home=$(awk -F: -v account="$user" '$1 == account {print $6; exit}' "$(root_path /etc/passwd)")
        [[ -n "$home" ]] || { echo "user not found: $user" >&2; exit 1; }
    fi
    home=${home%/}
    user_root=$(root_path "$home")
    config_root="$user_root/.config"
    if [[ -d "$user_root" ]]; then
        install -d "$config_root"
        for pair in \
            'kdeglobals:.config/kdeglobals' \
            'plasmarc:.config/plasmarc' \
            'kwinrc:.config/kwinrc' \
            'kcminputrc:.config/kcminputrc' \
            'ksplashrc:.config/ksplashrc' \
            'gtk-3.0-settings.ini:.config/gtk-3.0/settings.ini' \
            'gtk-4.0-settings.ini:.config/gtk-4.0/settings.ini'; do
            template=${pair%%:*}
            relative=${pair#*:}
            copy_if_missing "$(root_path "/usr/share/rowyn/kde-dark-defaults/$template")" \
                "$user_root/$relative"
        done
        if [[ "$root" == / ]] && id "$user" >/dev/null 2>&1; then
            chown -R "$user:$user" "$config_root"
        fi
    else
        # Run after the tmpfiles entry that creates the user's home
        # (tmpfiles.d ordering: zz- sorts last).
        tmpfiles=$(root_path /usr/lib/tmpfiles.d/zz-rowyn-kde-dark.conf)
        install -d "$(dirname "$tmpfiles")"
        : > "$tmpfiles"
        printf 'd %s/.config 0700 %s %s -\n' "$home" "$user" "$user" >> "$tmpfiles"
        printf 'd %s/.config/gtk-3.0 0700 %s %s -\nd %s/.config/gtk-4.0 0700 %s %s -\n' \
            "$home" "$user" "$user" "$home" "$user" "$user" >> "$tmpfiles"
        for pair in \
            'kdeglobals:.config/kdeglobals' \
            'plasmarc:.config/plasmarc' \
            'kwinrc:.config/kwinrc' \
            'kcminputrc:.config/kcminputrc' \
            'ksplashrc:.config/ksplashrc' \
            'gtk-3.0-settings.ini:.config/gtk-3.0/settings.ini' \
            'gtk-4.0-settings.ini:.config/gtk-4.0/settings.ini'; do
            template=${pair%%:*}
            relative=${pair#*:}
            printf 'C %s/%s 0644 %s %s - /usr/share/rowyn/kde-dark-defaults/%s\n' \
                "$home" "$relative" "$user" "$user" "$template" >> "$tmpfiles"
        done
    fi
fi
