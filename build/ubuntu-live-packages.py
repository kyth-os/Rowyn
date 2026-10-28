#!/usr/bin/env python3
"""Install and verify the package set from Kubuntu 26.10 desktop live media."""
import argparse
from dataclasses import dataclass
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REFERENCE_SHA256 = "fc344b44914d28b496d6f1431aa729a92fbdcc124f7d93266a1ef885adcc961e"
REFERENCE_RECORDS = 2183

# Packages in the official Kubuntu live set that Rowyn deliberately does
# not install. Most implement Kubuntu's own installer (Calamares, its
# settings packages, and the session installer prompt), which Rowyn
# replaces with its own bootc installer. plasma-welcome is excluded for
# a different reason: it is the KDE Welcome first-login popup, which
# Rowyn suppresses on the installed desktop as well (the payload image
# purges it); on the live media its fresh liveuser session would get
# the popup on every boot. It is only a Recommends of kubuntu-desktop
# and kubuntu-settings-desktop, and the install runs with
# --no-install-recommends, so nothing pulls it back in. The reference
# manifest stays byte-identical to the official file; the substitution
# lives here so parity verification can enforce both directions —
# everything else installed, these absent.
SUBSTITUTED_DEBS = frozenset({
    "calamares",
    "calamares-data",
    "calamares-settings-kubuntu",
    "calamares-settings-ubuntu-common",
    "calamares-settings-ubuntu-common-data",
    "kubuntu-installer-prompt",
    "libcalamares3.3",
    "libcalamaresui3.3",
    "plasma-welcome",
})


def expected_debs(package_set):
    unknown = sorted(SUBSTITUTED_DEBS - set(package_set.debs))
    if unknown:
        raise ValueError("Substituted packages absent from the reference manifest: " + ", ".join(unknown))
    return [name for name in package_set.debs if name not in SUBSTITUTED_DEBS]
ARCHITECTURE = "amd64"
PACKAGE_NAME = re.compile(r"[a-z0-9][a-z0-9+.-]*\Z")
SNAP_NAME = re.compile(r"[a-z0-9][a-z0-9-]*\Z")
CHANNEL = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9./-]*\Z")


@dataclass(frozen=True)
class Snap:
    name: str
    channel: str
    reference_revision: str


@dataclass(frozen=True)
class PackageSet:
    debs: tuple[str, ...]
    snaps: tuple[Snap, ...]


def parse_manifest(path):
    debs = set()
    snaps = []
    rows = Path(path).read_text(encoding="utf-8").splitlines()
    for line_number, line in enumerate(rows, 1):
        fields = line.split()
        if not fields:
            continue
        package = fields[0]
        if package.startswith("snap:"):
            if len(fields) != 3:
                raise ValueError(f"invalid snap manifest row at line {line_number}")
            name = package.removeprefix("snap:")
            channel, revision = fields[1:]
            if not SNAP_NAME.fullmatch(name) or not CHANNEL.fullmatch(channel) or not revision.isdigit():
                raise ValueError(f"invalid snap identity at line {line_number}")
            snaps.append(Snap(name, channel, revision))
            continue
        if len(fields) != 2:
            raise ValueError(f"invalid Debian package manifest row at line {line_number}")
        if ":" in package:
            name, architecture = package.rsplit(":", 1)
            if architecture != ARCHITECTURE:
                raise ValueError(f"unsupported package architecture at line {line_number}: {architecture}")
            package = name
        if not PACKAGE_NAME.fullmatch(package):
            raise ValueError(f"invalid Debian package name at line {line_number}")
        debs.add(package)
    if not debs or not snaps:
        raise ValueError("Ubuntu live package manifest is incomplete")
    if len(snaps) != len({snap.name for snap in snaps}):
        raise ValueError("Ubuntu live package manifest contains duplicate snap entries")
    return PackageSet(tuple(sorted(debs)), tuple(snaps))


def verify_reference_manifest(path):
    path = Path(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != REFERENCE_SHA256:
        raise ValueError("Kubuntu 26.10 desktop live manifest hash does not match the pinned official file")
    rows = path.read_text(encoding="utf-8").splitlines()
    if len(rows) != REFERENCE_RECORDS:
        raise ValueError("Kubuntu desktop live manifest record count changed")
    return parse_manifest(path)


def normalize_package_name(value):
    value = value.strip().split()[0] if value.strip() else ""
    if value.endswith(":" + ARCHITECTURE):
        return value[: -(len(ARCHITECTURE) + 1)]
    return value


def run_checked(arguments, *, cwd=None):
    result = subprocess.run(arguments, cwd=cwd, text=True, capture_output=True, check=False)
    if result.returncode:
        # apt-get reports only a summary line on stderr ("E: Sub-process
        # /usr/bin/dpkg returned an error code (1)"); the dpkg output that
        # names the failing package goes to stdout. Surface both, or the
        # CI log cannot say which package failed.
        stderr = (result.stderr or "").strip()
        stdout_lines = (result.stdout or "").splitlines()
        # dpkg reports a failed maintainer script as "dpkg: error processing
        # package <name> ..." on stdout, followed by the script's own error
        # output; that block can sit far above the end of a long apt run, so
        # surface every such block with context instead of only the tail.
        blocks = []
        for index, line in enumerate(stdout_lines):
            if "dpkg: error" in line.lower():
                start = max(0, index - 12)
                blocks.append("\n".join(stdout_lines[start:index + 13]))
        detail = stderr
        if blocks:
            excerpts = "\n...\n".join(blocks)
            detail = f"{detail}\n--- dpkg error excerpts from stdout ---\n{excerpts}" if detail else excerpts
        else:
            stdout_tail = "\n".join(stdout_lines[-40:]).strip()
            if stdout_tail:
                detail = f"{detail}\n--- stdout (last 40 lines) ---\n{stdout_tail}" if detail else stdout_tail
        raise RuntimeError(f"command failed ({result.returncode}): {arguments[0]}: {detail}")
    return result.stdout


def install_deb_packages(manifest, runner=run_checked):
    package_set = verify_reference_manifest(manifest)
    wanted = expected_debs(package_set)
    runner(["/usr/bin/apt-get", "install", "-y", "--no-install-recommends", *wanted])
    installed = runner([
        "/usr/bin/dpkg-query", "-W", "--showformat=${Package}\\n",
    ])
    installed_names = {normalize_package_name(line) for line in installed.splitlines() if line.strip()}
    missing = sorted(set(wanted) - installed_names)
    if missing:
        raise ValueError("Kubuntu desktop live packages were not installed: " + ", ".join(missing))


def _snap_metadata(snap_path, runner=run_checked):
    if Path(snap_path).name.startswith("bare_"):
        return "strict"
    metadata = runner(["/usr/bin/unsquashfs", "-cat", str(snap_path), "meta/snap.yaml"])
    match = re.search(r"(?m)^confinement:\s*(strict|classic|devmode)\s*$", metadata)
    if match is None:
        raise ValueError("downloaded snap has no recognized confinement: " + Path(snap_path).name)
    return match.group(1)


def seed_snaps(manifest, root=Path("/"), runner=run_checked, metadata_reader=_snap_metadata):
    package_set = verify_reference_manifest(manifest)
    root = Path(root)
    seed = root / "var/lib/snapd/seed"
    snaps_dir = seed / "snaps"
    assertions_dir = seed / "assertions"
    snaps_dir.mkdir(parents=True, exist_ok=True)
    assertions_dir.mkdir(parents=True, exist_ok=True)
    downloaded = []
    order = {"snapd": 0, "core24": 1, "bare": 2}
    for snap in sorted(package_set.snaps, key=lambda item: (order.get(item.name, 3), item.name)):
        with tempfile.TemporaryDirectory(prefix="rowyn-snap-download-") as temporary:
            runner([
                "/usr/bin/snap", "download", snap.name,
                f"--channel={snap.channel}", f"--target-directory={temporary}",
            ])
            candidates = sorted(Path(temporary).glob(f"{snap.name}_*.snap"))
            if len(candidates) != 1:
                raise ValueError(f"snap download did not return exactly one {snap.name} archive")
            archive = candidates[0]
            revision = archive.stem.rsplit("_", 1)[-1]
            if not revision.isdigit():
                raise ValueError("snap download returned an invalid revision: " + archive.name)
            assertions = sorted(Path(temporary).glob(f"{snap.name}_*.assert"))
            if len(assertions) != 1 or not assertions[0].is_file() or assertions[0].stat().st_size == 0:
                raise ValueError(f"snap download did not return assertions for {snap.name}")
            if not archive.is_file() or archive.stat().st_size == 0:
                raise ValueError(f"snap download returned an empty archive for {snap.name}")
            target_snap = snaps_dir / archive.name
            target_assertion = assertions_dir / assertions[0].name
            shutil.copyfile(archive, target_snap)
            shutil.copyfile(assertions[0], target_assertion)
            confinement = metadata_reader(target_snap, runner=runner)
            entry: dict[str, object] = {
                "name": snap.name,
                "channel": snap.channel,
                "file": archive.name,
            }
            if confinement == "classic":
                entry["classic"] = True
            downloaded.append(entry)
    if any(item["name"] == "snapd" for item in downloaded):
        (seed / ".snapd-explicit-install-stamp").touch()
    seed_payload = {"snaps": downloaded}
    (seed / "seed.yaml").write_text(json.dumps(seed_payload, indent=2) + "\n", encoding="utf-8")


def _seeded_snap_records(package_set, root):
    seed = Path(root) / "var/lib/snapd/seed"
    seed_yaml = seed / "seed.yaml"
    if not seed_yaml.is_file():
        raise ValueError("Kubuntu desktop snap seed is missing")
    if any(snap.name == "snapd" for snap in package_set.snaps) and not (seed / ".snapd-explicit-install-stamp").is_file():
        raise ValueError("Kubuntu desktop snapd explicit-install stamp is missing")
    try:
        payload = json.loads(seed_yaml.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as error:
        raise ValueError("Kubuntu desktop snap seed is unreadable") from error
    entries = payload.get("snaps") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        raise ValueError("Kubuntu desktop snap seed has no snap list")
    by_name = {entry.get("name"): entry for entry in entries if isinstance(entry, dict)}
    records = []
    for snap in package_set.snaps:
        entry = by_name.get(snap.name)
        if entry is None or entry.get("channel") != snap.channel:
            raise ValueError("Kubuntu desktop snap is not seeded on its reference channel: " + snap.name)
        filename = entry.get("file")
        if not isinstance(filename, str) or Path(filename).name != filename:
            raise ValueError("Kubuntu desktop snap seed has an unsafe archive path: " + snap.name)
        archive = seed / "snaps" / filename
        revision = archive.stem.rsplit("_", 1)[-1]
        assertion = seed / "assertions" / (archive.stem + ".assert")
        if (
            not revision.isdigit()
            or not archive.is_file()
            or archive.stat().st_size == 0
            or not assertion.is_file()
            or assertion.stat().st_size == 0
        ):
            raise ValueError("Kubuntu desktop snap archive/assertion is missing or empty: " + snap.name)
        if entry.get("classic") not in (None, True):
            raise ValueError("Kubuntu desktop snap has an invalid classic seed flag: " + snap.name)
        records.append((snap.name, snap.channel, revision))
    if len(entries) != len(package_set.snaps):
        raise ValueError("Kubuntu desktop snap seed contains unexpected entries")
    return records


def verify_package_parity(manifest, installed_manifest, root=Path("/")):
    package_set = verify_reference_manifest(manifest)
    installed_path = Path(installed_manifest)
    installed_names = {
        normalize_package_name(line)
        for line in installed_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    missing = sorted(set(expected_debs(package_set)) - installed_names)
    if missing:
        raise ValueError("Kubuntu desktop live packages missing from image: " + ", ".join(missing))
    substituted_present = sorted(SUBSTITUTED_DEBS & installed_names)
    if substituted_present:
        raise ValueError("Substituted Kubuntu installer packages present in image: " + ", ".join(substituted_present))
    records = _seeded_snap_records(package_set, root)
    existing = installed_path.read_text(encoding="utf-8").splitlines()
    present = {line.split()[0] for line in existing if line.split()}
    with installed_path.open("a", encoding="utf-8") as output:
        for name, channel, revision in records:
            key = f"snap:{name}"
            if key not in present:
                output.write(f"{key}\t{channel}\t{revision}\n")
    return len(expected_debs(package_set)), len(records)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    install = subparsers.add_parser("install-deb-packages")
    install.add_argument("--manifest", required=True)
    seed = subparsers.add_parser("seed-snaps")
    seed.add_argument("--manifest", required=True)
    seed.add_argument("--root", default="/")
    verify = subparsers.add_parser("verify-package-parity")
    verify.add_argument("--manifest", required=True)
    verify.add_argument("--installed-manifest", required=True)
    verify.add_argument("--root", default="/")
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "install-deb-packages":
            install_deb_packages(arguments.manifest)
            print("Installed all Debian packages from the Kubuntu desktop live manifest")
        elif arguments.command == "seed-snaps":
            seed_snaps(arguments.manifest, arguments.root)
            print("Seeded all Snap packages from the Kubuntu desktop live manifest")
        else:
            deb_count, snap_count = verify_package_parity(
                arguments.manifest, arguments.installed_manifest, arguments.root,
            )
            print(f"Verified Kubuntu live package parity: {deb_count} Debian packages, {snap_count} seeded snaps")
    except (OSError, RuntimeError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
