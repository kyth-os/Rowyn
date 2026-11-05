"""Fresh-export integration checks for Rowyn's daily-driver additions."""
import json
from pathlib import Path
import tempfile
import unittest
from test_hub_port import ROOT, hub, _pinned_repo


class QolPackagingTests(unittest.TestCase):
    def test_native_report_uses_trusted_provenance_and_one_sanitizer(self):
        diagnostics = (ROOT / "build/hub/src-tauri/src/commands/rowyn_diagnostics.rs").read_text()
        self.assertIn("rowyn_support::refresh_public_provenance()", diagnostics)
        self.assertIn("rowyn_support::sanitize_report", diagnostics)
        self.assertNotIn("let clean = scrub_logs(&strip_ansi(report));", diagnostics)

    def test_provenance_notification_and_dolphin_menu_are_packaged(self):
        container = (ROOT / "Containerfile").read_text()
        workflow = (ROOT / ".github/workflows/build.yml").read_text()
        for token in ("ARG ROWYN_SOURCE_REVISION=unknown", "write-build-info.py", "rowyn-update-notify.timer", "systemctl --global enable rowyn-update-notify.timer", "/usr/share/kio/servicemenus/rowyn-development-here.desktop"):
            self.assertIn(token, container)
        self.assertIn('--build-arg "ROWYN_SOURCE_REVISION=${GITHUB_SHA}"', workflow)
        for package in ("kup-backup", "bup", "rsync", "kde-config-flatpak", "libnotify-bin"):
            self.assertIn(package, container.split())


class QolIntegrationTests(unittest.TestCase):
    @unittest.skipUnless(_pinned_repo(), "set ROWYN_TEST_UPSTREAM_REPO")
    def test_backup_workspace_is_registered_once_with_native_handlers(self):
        with tempfile.TemporaryDirectory(prefix="rowyn-qol-export-") as temp:
            root = Path(temp) / "export"
            hub.export_source(_pinned_repo(), root)
            hub.port_source(root)
            web = root / "src/rowyn-hub-web"
            destinations = json.loads((web / "src/data/hubRoutes.json").read_text())["destinations"]
            backups = [d for d in destinations if d["key"] == "Backups"]
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0]["route"], "/backups")
            self.assertIn('path="/backups"', (web / "src/App.tsx").read_text())
            self.assertEqual((web / "src/components/Sidebar.tsx").read_text().count('Backups: { hint:'), 1)
            self.assertIn("Backups: BACKUPS_SECTIONS", (web / "src/data/destinations.ts").read_text())
            handlers = (web / "src-tauri/src/main.rs").read_text()
            for name in ("rowyn_backups_snapshot", "rowyn_open_settings", "rowyn_development_preset_preview"):
                self.assertIn(name, handlers)
            self.assertNotIn("commands::dev_tools::", handlers)
            self.assertIn("handle_development_cli", handlers)
            self.assertIn("SetupChecklist", (web / "src/pages/Dashboard.tsx").read_text())
            self.assertIn("AppsDefaultsAccess", (web / "src/pages/Apps.tsx").read_text())
            self.assertIn("UpdateStatusBanner", (web / "src/pages/Updates.tsx").read_text())
            self.assertIn("RecoveryClarity", (web / "src/pages/Updates.tsx").read_text())
            diagnostics_page = (web / "src/pages/RowynDiagnostics.tsx").read_text()
            self.assertIn("<StorageCare />", diagnostics_page)
            self.assertIn("<PowerCare />", diagnostics_page)
            section_source = (web / "src/data/hubSections.ts").read_text()
            self.assertIn("Storage: IconDatabase", section_source)
            self.assertIn("Power: IconChip", section_source)
            for name in ("rowyn_storage_snapshot", "rowyn_power_snapshot"):
                self.assertIn(f"commands::rowyn_care::{name}", handlers)
            self.assertIn("pub(crate) mod rowyn_care;", (web / "src-tauri/src/commands/mod.rs").read_text())
            app_store = (web / "src/components/AppStoreSection.tsx").read_text()
            self.assertIn("<AppRemovalConfirmation", app_store)
            self.assertIn("setRemoval(app)", app_store)
            self.assertIn("tests/rowyn-removal.test.mjs", json.loads((web / "package.json").read_text())["scripts"]["test:rowyn-mvp"])


if __name__ == "__main__":
    unittest.main()
