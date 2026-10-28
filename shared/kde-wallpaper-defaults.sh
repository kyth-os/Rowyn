#!/usr/bin/env bash
# Shared live/installed Plasma wallpaper default; do not alter user preferences.
set -euo pipefail
root=/
while (($#)); do
    case "$1" in
        --root) root=${2:?--root requires a path}; shift 2 ;;
        *) echo "usage: $0 [--root PATH]" >&2; exit 2 ;;
    esac
done
script_dir=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
assets="$script_dir/wallpaper"
package="${root%/}/usr/share/wallpapers/RowynBlack"
test -s "$assets/metadata.json"
test -s "$assets/contents/images/3840x2160.png"
install -Dm644 "$assets/metadata.json" "$package/metadata.json"
install -Dm644 "$assets/contents/images/3840x2160.png" "$package/contents/images/3840x2160.png"

# Plasma gets a new desktop's wallpaper from its selected look-and-feel defaults.
# Keep Breeze/Breeze Dark, change only that default, and never touch a user's
# plasma-org.kde.plasma.desktop-appletsrc (including across image updates).
python3 - "$root" <<'PY'
from pathlib import Path
import re
import sys
root = Path(sys.argv[1])
for theme in ('org.kde.breeze.desktop', 'org.kde.breezedark.desktop'):
    path = root / 'usr/share/plasma/look-and-feel' / theme / 'contents/defaults'
    lines = path.read_text().splitlines(keepends=True)
    sections = [i for i, line in enumerate(lines) if line.strip() == '[Wallpaper]']
    if len(sections) > 1:
        raise RuntimeError('ambiguous Wallpaper sections: ' + str(path))
    if sections:
        start = sections[0] + 1
        end = next((i for i in range(start, len(lines)) if lines[i].lstrip().startswith('[')), len(lines))
        keys = [i for i in range(start, end) if re.match(r'^\s*Image\s*=', lines[i])]
        if len(keys) > 1:
            raise RuntimeError('ambiguous wallpaper Image keys: ' + str(path))
        if keys:
            lines[keys[0]] = 'Image=RowynBlack\n'
        else:
            lines.insert(start, 'Image=RowynBlack\n')
    else:
        if lines and not lines[-1].endswith('\n'):
            lines[-1] += '\n'
        lines += ['\n[Wallpaper]\n', 'Image=RowynBlack\n']
    path.write_text(''.join(lines))
PY
