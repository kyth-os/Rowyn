#!/usr/bin/env python3
"""Flatten NVIDIA's supported-gpus.json into the ID list gpu-select reads.

The driver package ships /usr/share/doc/nvidia-kernel-common-<branch>/
supported-gpus.json. Chips supported by the *current* branch carry no
`legacybranch` key; chips only older legacy branches support carry one
(e.g. "580.xx"). The open kernel modules need GSP firmware (Turing and
newer) and NVIDIA marks exactly those chips with the `kernelopen`
feature, so a device is "supported" here when its entry has `kernelopen`
and no `legacybranch`.

Runs at image build time only (the runtime helper stays dependency-free):

    nvidia-open-devids.py supported-gpus.json /usr/lib/rowyn/nvidia-open-devids

Output: one lowercase 4-digit hex device ID per line, sorted, unique.
Exits non-zero if the JSON has no usable entry, so a format change in a
future driver branch fails the image build instead of silently sending
every NVIDIA GPU to nouveau.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

DEVID = re.compile(r"^0x[0-9a-fA-F]{4}$")


def supported_ids(data: dict) -> list[str]:
    chips = data["chips"]
    ids = set()
    for chip in chips:
        if chip.get("legacybranch"):
            continue
        if "kernelopen" not in chip.get("features", []):
            continue
        devid = chip["devid"]
        if not DEVID.match(devid):
            raise ValueError(f"unexpected device ID {devid!r}")
        ids.add(devid[2:].lower())
    return sorted(ids)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(f"usage: {argv[0]} SUPPORTED_GPUS_JSON OUTPUT", file=sys.stderr)
        return 2
    source, output = Path(argv[1]), Path(argv[2])
    ids = supported_ids(json.loads(source.read_text(encoding="utf-8")))
    if not ids:
        print(f"{source}: no kernelopen device IDs found", file=sys.stderr)
        return 1
    output.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# NVIDIA devices the open kernel modules support (Turing and newer).\n"
        "# Generated at image build time from the driver's supported-gpus.json\n"
        "# by shared/nvidia-open-devids.py; read by rowyn-gpu-select.\n"
    )
    output.write_text(header + "\n".join(ids) + "\n", encoding="utf-8")
    output.chmod(0o644)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
