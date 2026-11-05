#!/usr/bin/python3
"""Write immutable image provenance during image construction only."""
import argparse
import json
from pathlib import Path
import re


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("/"))
    parser.add_argument("--source-revision", default="unknown")
    parser.add_argument("--image-version", default="0.1")
    args = parser.parse_args()
    revision = args.source_revision
    if revision != "unknown" and not re.fullmatch(r"[0-9a-f]{40}", revision):
        parser.error("source revision must be an exact lowercase Git commit or unknown")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.+_-]{0,63}", args.image_version):
        parser.error("invalid image version")
    destination = args.root / "usr/share/rowyn/build-info.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps({
        "source_revision": None if revision == "unknown" else revision,
        "image_version": args.image_version,
    }, indent=2) + "\n", encoding="utf-8")
    destination.chmod(0o644)


if __name__ == "__main__":
    main()
