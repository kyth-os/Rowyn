#!/usr/bin/env python3
"""Contracts for the payload's timezone stack.

The installer writes the chosen zone as an /etc/localtime symlink into
the deployment (Kyth configuration machinery, inherited untransformed),
and KDE System Settings changes it later through systemd-timedated.
Both need the payload to guarantee the pieces they rely on instead of
inheriting whatever the base image happens to ship:

- tzdata, named explicitly, so the zone files exist (America/Detroit
  included), and tzdata-legacy for the legacy aliases the zone tables
  still list (US/Michigan, which the installer picker can emit) —
  without it the installer's /etc/localtime symlink dangles and the
  clock silently falls back to UTC;
- polkitd, named explicitly, so the clock KCM's timedated calls are
  authorized (it also arrives transitively via polkit-kde-agent-1);
- a canonical image timezone state — the absolute /etc/localtime
  symlink and /etc/timezone systemd-timedated itself writes for UTC —
  so the bootc /etc merge base is never an opaque base-image default.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[2]
CONTAINERFILE = ROOT / "Containerfile"
BUILD_WORKFLOW = ROOT / ".github/workflows/build.yml"
INSTALLER_PORT = ROOT / "build/ubuntu-installer-source.py"
COMPOSEFS_PORT = ROOT / "build/ubuntu-composefs-source.py"
PIN = "2baf8496b5aa904c0784f4290df743ee17edae48"


def _kde_layer(text: str) -> str:
    start = text.index("# --- KDE Plasma package layer")
    end = text.index("apt-get purge -y plasma-discover-backend-snap", start)
    return text[start:end]


class PayloadTimezoneTests(unittest.TestCase):
    def test_timezone_packages_are_explicit(self):
        layer = _kde_layer(CONTAINERFILE.read_text(encoding="utf-8"))
        self.assertIn("tzdata", layer)
        self.assertIn("tzdata-legacy", layer)
        self.assertIn("polkitd", layer)

    def test_image_ships_canonical_utc_state(self):
        text = CONTAINERFILE.read_text(encoding="utf-8")
        self.assertIn("printf 'Etc/UTC\\n' > /etc/timezone", text)
        self.assertIn("ln -sf /usr/share/zoneinfo/Etc/UTC /etc/localtime", text)
        # The canonical state must be written after the package layer
        # installs tzdata, not before it.
        self.assertGreater(
            text.index("ln -sf /usr/share/zoneinfo/Etc/UTC /etc/localtime"),
            text.index("apt-get purge -y plasma-discover-backend-snap"),
        )

    def test_image_smokes_cover_the_timezone_stack(self):
        workflow = BUILD_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("test -s /usr/share/zoneinfo/America/Detroit", workflow)
        self.assertIn("test -s /usr/share/zoneinfo/US/Michigan", workflow)
        self.assertIn(
            'test "$(readlink /etc/localtime)" = /usr/share/zoneinfo/Etc/UTC',
            workflow,
        )
        self.assertIn('grep -Fx "Etc/UTC" /etc/timezone', workflow)
        self.assertIn(
            "test -s /usr/lib/systemd/system/systemd-timedated.service",
            workflow,
        )
        self.assertIn("test -x /usr/lib/polkit-1/polkitd", workflow)

    def test_installer_ports_leave_localtime_machinery_alone(self):
        # The timezone write is Kyth's symlink swap into the deployment
        # etc plus the assurance step's read-back check; neither Rowyn
        # port script may rewrite any of it.
        self.assertNotIn("localtime", INSTALLER_PORT.read_text(encoding="utf-8"))
        self.assertNotIn("localtime", COMPOSEFS_PORT.read_text(encoding="utf-8"))


def _pinned_repo():
    repo = os.environ.get("ROWYN_TEST_KYTH_REPO")
    if not repo:
        return None
    result = subprocess.run(
        ["git", "-C", repo, "rev-parse", PIN + "^{commit}"],
        text=True, capture_output=True)
    if result.returncode != 0 or result.stdout.strip() != PIN:
        return None
    return Path(repo)


@unittest.skipUnless(
    _pinned_repo(),
    "set ROWYN_TEST_KYTH_REPO to a Kyth checkout containing the pinned commit")
class PinnedInstallerTimezoneTests(unittest.TestCase):
    def _show(self, path: str) -> str:
        repo = _pinned_repo()
        return subprocess.check_output(
            ["git", "-C", str(repo), "show", f"{PIN}:{path}"],
            text=True)

    def test_pinned_configuration_writes_localtime_symlink(self):
        config = self._show(
            "src/kyth-installer-web/src-tauri/src/installer_configuration.rs")
        # The chosen zone becomes a symlink /etc/localtime ->
        # /usr/share/zoneinfo/<zone> inside the deployment's etc.
        self.assertIn('format!("/usr/share/zoneinfo/{timezone}")', config)
        self.assertIn(
            "std::os::unix::fs::symlink(&plan.localtime_target", config)
        self.assertIn("fs::rename(&temporary, &localtime)", config)
        # Zone identifiers are validated as single relative components:
        # no "..", no leading slash, so a bad zone cannot be written.
        self.assertIn("safe_component", config)

    def test_pinned_assurance_reads_the_symlink_back(self):
        assurance = self._show(
            "src/kyth-installer-web/src-tauri/src/installer_assurance.rs")
        self.assertIn('etc.join("localtime")', assurance)
        self.assertIn("read_link", assurance)
        self.assertIn("installed timezone verification failed", assurance)


if __name__ == "__main__":
    unittest.main()
