#!/usr/bin/env python3
"""Static checks: generated locales (D7) and Hub tool packages (D8)."""
from __future__ import annotations

from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]
CONTAINERFILE = (ROOT / "Containerfile").read_text()
WORKFLOW = (ROOT / ".github/workflows/build.yml").read_text()


def smoke_block() -> str:
    m = re.search(r"-euxc '(.*?)\n\s*'", WORKFLOW, re.S)
    assert m, "smoke block not found"
    return m.group(1)


def kde_layer() -> str:
    return CONTAINERFILE.split("# --- KDE Plasma package layer", 1)[1].split("apt-get purge", 1)[0]


class LocaleTests(unittest.TestCase):
    def test_locales_in_kde_layer_not_locales_all(self):
        self.assertRegex(kde_layer(), r"\slocales\b")
        self.assertNotRegex(CONTAINERFILE, r"apt-get install[^\n]*locales-all|\n\s+locales-all")

    def test_locale_generated_after_install_and_asserted(self):
        self.assertIn("locale-gen", CONTAINERFILE)
        self.assertIn("en_US.UTF-8 UTF-8", CONTAINERFILE)
        self.assertIn("LANG=en_US.UTF-8", CONTAINERFILE)
        self.assertLess(CONTAINERFILE.index("apt-get install -y"), CONTAINERFILE.index("locale-gen"))
        self.assertLess(CONTAINERFILE.index("locale-gen"), CONTAINERFILE.index("bootc-rootfs.sh\n"))
        self.assertIn("locale -a | grep -qx en_US.utf8", CONTAINERFILE)

    def test_smoke_checks_locales(self):
        block = smoke_block()
        self.assertIn("locale -a | grep -qx en_US.utf8", block)
        self.assertIn("/etc/default/locale", block)


class ToolTests(unittest.TestCase):
    def test_packages_named_in_kde_layer(self):
        for pkg in ("pciutils", "fwupd"):
            self.assertRegex(kde_layer(), rf"\s{pkg}\b")

    def test_smoke_checks_tools(self):
        block = smoke_block()
        self.assertIn("test -x /usr/bin/lspci", block)
        self.assertIn("test -x /usr/bin/fwupdmgr", block)

    def test_no_single_quote_in_smoke_block(self):
        self.assertNotIn("'", smoke_block())


if __name__ == "__main__":
    unittest.main()
