#!/usr/bin/env python3
"""Contracts for the payload's desktop package set.

The installed system carries the Kubuntu default applications and
integration — the set the live image gets from the Kubuntu manifest —
named explicitly in the Containerfile's KDE package layer rather than
pulled via the kubuntu-desktop metapackage, so the deliberate
exclusions stay excluded. Patrick's call (Oct 29, 2026): install the
essentials, skip LibreOffice. Firefox/Thunderbird stay out too (they
are snaps on Ubuntu and Rowyn is snap-free), as does plasma-welcome
(purged on purpose) and the legacy Oxygen/Air themes.
"""
from __future__ import annotations

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
CONTAINERFILE = ROOT / "Containerfile"
BUILD_WORKFLOW = ROOT / ".github/workflows/build.yml"

# The essentials set, grouped as in the Containerfile layer.
ESSENTIALS = [
    # Default KDE applications (kde-spectacle landed with the
    # Print Screen fix; it is part of the same set).
    "kde-spectacle",
    "gwenview", "okular", "okular-extra-backends", "ark", "kate",
    "kcalc", "kcharselect", "kdeconnect", "elisa", "haruna", "skanpage",
    # Archive backends for Ark.
    "unzip", "zip",
    # Printing, scanning, color, tablets.
    "cups", "cups-browsed", "cups-client", "cups-daemon", "cups-filters",
    "cups-ipp-utils", "cups-pk-helper", "print-manager",
    "system-config-printer-common", "system-config-printer-udev",
    "bluez-cups", "colord", "kde-config-tablet",
    "sane-airscan", "sane-utils",
    # Desktop integration: KCMs, KIO workers, Plasma/KWin add-ons.
    "kio-admin", "kio-audiocd", "kde-config-updates", "kde-config-plymouth",
    "plasma-runners-addons", "plasma-widgets-addons",
    "plasma-wallpapers-addons", "plasma-calendar-addons",
    "plasma-dataengines-addons", "kwin-addons",
    # Media plumbing.
    "ffmpeg", "gstreamer1.0-pipewire", "phonon4qt6-backend-vlc",
    # Document fonts and accessibility.
    "fonts-liberation", "fonts-dejavu-core", "fonts-dejavu-mono",
    "orca",
]

# Packages that must never be installed in the payload. Matched against
# install tokens, not raw text: the Containerfile comments name some of
# these while explaining why they are excluded.
FORBIDDEN_PREFIXES = ("libreoffice",)
FORBIDDEN = {"firefox", "thunderbird"}


def installed_tokens(text: str) -> set[str]:
    """All package tokens named by any apt-get install in the file."""
    tokens: set[str] = set()
    for match in re.finditer(r"apt-get install -y[^\n]*\\\n(.*?)\s&& \\", text, re.S):
        body = match.group(1).replace("\\", " ")
        tokens.update(body.split())
    return tokens


class PayloadPackageTests(unittest.TestCase):
    def test_essentials_are_installed(self):
        tokens = installed_tokens(CONTAINERFILE.read_text(encoding="utf-8"))
        for package in ESSENTIALS:
            self.assertIn(package, tokens)

    def test_essentials_are_in_the_kde_layer_not_the_recommends_free_one(self):
        # The essentials ride the main (recommends-enabled) KDE layer —
        # the same transaction as plasma-desktop — not the later
        # --no-install-recommends layer.
        text = CONTAINERFILE.read_text(encoding="utf-8")
        kde_layer = text.split("apt-get install -y --no-install-recommends", 1)[0]
        for package in ESSENTIALS:
            self.assertIn(package, installed_tokens(kde_layer))

    def test_libreoffice_and_snap_browsers_are_not_installed(self):
        tokens = installed_tokens(CONTAINERFILE.read_text(encoding="utf-8"))
        for token in tokens:
            for prefix in FORBIDDEN_PREFIXES:
                self.assertFalse(token.startswith(prefix),
                                 f"forbidden package installed: {token}")
            self.assertNotIn(token, FORBIDDEN)

    def test_cups_units_are_enabled(self):
        # Installing CUPS is not enough: the daemon must actually be
        # enabled the way Ubuntu does it, or printing stays dead.
        text = CONTAINERFILE.read_text(encoding="utf-8")
        enables = re.findall(r"systemctl enable ([^&]+)&&", text)
        desktop = [e for e in enables if "sddm.service" in e]
        self.assertTrue(desktop, "no systemctl enable line names sddm.service")
        units = desktop[0].replace("\\", " ").split()
        for unit in ("cups.service", "cups.socket", "cups-browsed.service"):
            self.assertIn(unit, units)

    def test_image_smoke_checks_cover_the_essentials(self):
        workflow = BUILD_WORKFLOW.read_text(encoding="utf-8")
        for assertion in (
            "test -x /usr/bin/gwenview",
            "test -x /usr/bin/okular",
            "test -x /usr/bin/ark",
            "test -x /usr/bin/kate",
            "test -x /usr/bin/kcalc",
            "test -x /usr/bin/kdeconnect-app",
            "test -x /usr/sbin/cupsd",
            "test -e /etc/systemd/system/multi-user.target.wants/cups.service",
            "test -e /etc/systemd/system/sockets.target.wants/cups.socket",
            "test -e /etc/systemd/system/multi-user.target.wants/cups-browsed.service",
        ):
            self.assertIn(assertion, workflow)


if __name__ == "__main__":
    unittest.main()
