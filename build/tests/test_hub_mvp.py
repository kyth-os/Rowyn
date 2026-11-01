"""Rowyn-owned Hub MVP: pin-export and safety contracts (no VM needed)."""
import json
import os
from pathlib import Path
import tempfile
import unittest

from test_hub_port import ROOT, hub, _pinned_repo


class MvpRoutes(unittest.TestCase):
    @unittest.skipUnless(_pinned_repo(), "set ROWYN_TEST_UPSTREAM_REPO")
    def test_native_pages_are_reachable_without_restoring_legacy_surfaces(self):
        with tempfile.TemporaryDirectory(prefix="rowyn-mvp-routes-") as temp:
            root = Path(temp) / "export"
            hub.export_source(_pinned_repo(), root)
            self.assertTrue(callable(getattr(hub, "apply_rowyn_mvp", None)),
                            "port must apply checked-in Rowyn MVP overlays")
            hub.apply_rowyn_mvp(root)
            web = root / "src/rowyn-hub-web"
            manifest = json.loads((web / "src/data/hubRoutes.json").read_text())
            destinations = {d["key"]: d for d in manifest["destinations"]}
            self.assertEqual(destinations["Diagnostics"]["route"], "/diagnostics")
            self.assertEqual([s["key"] for s in destinations["Play"]["sections"]],
                             ["Gaming", "Compatibility", "Controllers"])
            app = (web / "src/App.tsx").read_text()
            for name in ("Diagnostics", "Play"):
                self.assertIn(f'./pages/Rowyn{name}', app)
                self.assertIn(f'path="/{name.lower()}"', app)
            sidebar = (web / "src/components/Sidebar.tsx").read_text()
            self.assertIn('Diagnostics: { hint:', sidebar)
            self.assertIn('Play: { hint: "Launchers, compatibility, controllers"', sidebar)
            self.assertIn('Diagnostics: DIAGNOSTICS_SECTIONS',
                          (web / "src/data/destinations.ts").read_text())
            self.assertNotIn('Performance', json.dumps(destinations["Play"]))
            for name in ("Diagnostics", "Play"):
                page = (web / f"src/pages/Rowyn{name}.tsx").read_text()
                self.assertIn('Rowyn', page)
                self.assertNotIn('RecipeButton', page)
                self.assertNotIn('setInterval', page)


class MvpDiagnostics(unittest.TestCase):
    def test_read_only_bridge_has_fixed_bounded_scrubbed_probes_and_explicit_export(self):
        path = ROOT / "build/hub/src-tauri/src/commands/rowyn_diagnostics.rs"
        self.assertTrue(path.is_file(), "read-only native diagnostics bridge is missing")
        text = path.read_text()
        for needle in ('/usr/bin/bootc', '/usr/bin/lspci', '/usr/bin/systemctl',
                       '/usr/bin/rowyn-controller-check', '/usr/bin/df', '/proc/meminfo',
                       '/proc/sys/kernel/osrelease', '/usr/lib/os-release',
                       'run_bounded_command', 'Duration::from_secs(8)', 'scrub_logs',
                       'create_new(true)', 'rowyn_diagnostics_snapshot', 'rowyn_controller_scan',
                       'rowyn_export_report', 'parse_controller', 'mod tests'):
            self.assertIn(needle, text)
        for forbidden in ('sudo', 'pkexec', 'bash', 'sh -c', '.output()', 'Command::new(program)'):
            self.assertNotIn(forbidden, text)
        page = (ROOT / "build/hub/src/pages/RowynDiagnostics.tsx").read_text()
        for needle in ('Refresh checks', 'Copy report', 'Save report to Downloads',
                       'Report preview', 'rowynDiagnosticsSnapshot', 'exportRowynReport'):
            self.assertIn(needle, page)
        for forbidden in ('setInterval', 'useEffect', 'repair', 'installFlatpak'):
            self.assertNotIn(forbidden, page)


class MvpSteamGames(unittest.TestCase):
    def test_scan_is_manual_read_only_and_never_claims_compatibility(self):
        page = (ROOT / "build/hub/src/pages/RowynPlay.tsx").read_text()
        for needle in ('Scan Steam games (local only)', 'rowynSteamGamesScan',
                       'not mean Rowyn tested', 'ProtonDB community reports',
                       'No scan runs automatically', 'custom library location may not have been scanned'):
            self.assertIn(needle, page)
        service = (ROOT / "build/hub/src/services/rowynMvp.ts").read_text()
        self.assertIn("rowyn_steam_games_scan", service)
        scanner = ROOT / "build/hub/src-tauri/src/commands/rowyn_steam.rs"
        self.assertTrue(scanner.is_file(), "native Steam metadata scanner is missing")
        text = scanner.read_text()
        for needle in ('libraryfolders.vdf', 'appmanifest_', 'MAX_LIBRARIES',
                       'MAX_MANIFESTS', 'StateFlags', 'pub(crate) async fn rowyn_steam_games_scan'):
            self.assertIn(needle, text)
        for forbidden in ('Command::new', 'sudo', 'pkexec', 'rowyn-game-launch'):
            self.assertNotIn(forbidden, text)


class MvpPlay(unittest.TestCase):
    def test_play_uses_curated_flatpak_jobs_and_honest_host_boundary(self):
        page = (ROOT / "build/hub/src/pages/RowynPlay.tsx").read_text()
        for needle in ('installFlatpak', 'waitInstallJob', 'cancelInstall', 'fetchInstalledFlatpaks',
                       'rowynControllerScan', 'openRowynLauncher', 'Flatpak', 'not Rowyn-validated',
                       '2026-10-31', 'launchOptions'):
            self.assertIn(needle, page)
        bridge = ROOT / "build/hub/src-tauri/src/commands/rowyn_play.rs"
        self.assertTrue(bridge.is_file())
        text = bridge.read_text()
        for needle in ('launcher_id', 'installed_scope', 'rowyn_launcher_open', 'run_bounded_command', 'mod tests'):
            self.assertIn(needle, text)
        for forbidden in ('sudo', 'pkexec', 'bash', 'Command::new(program)', 'gaming::'):
            self.assertNotIn(forbidden, text)


class MvpExportTests(unittest.TestCase):
    @unittest.skipUnless(_pinned_repo(), "set ROWYN_TEST_UPSTREAM_REPO")
    def test_exported_frontend_tests_follow_supported_surface_without_dead_imports(self):
        with tempfile.TemporaryDirectory(prefix="rowyn-mvp-tests-") as temp:
            root = Path(temp) / "export"
            hub.export_source(_pinned_repo(), root)
            hub.apply_rowyn_mvp(root)
            web = root / "src/rowyn-hub-web"
            contract = (web / "tests/command-contract.test.mjs").read_text()
            self.assertNotIn('const gamingRust = await readFile', contract)
            self.assertIn('App Store install waits out slow mirrors', contract)
            self.assertIn('Updates page gives plain-language next steps', contract)
            self.assertIn('frontend stays behind the typed Tauri/Rust boundary', contract)
            self.assertIn('for (const file of await sourceFiles(resolve(root, "src")))', contract)
            self.assertIn('bug-hunt round 4: secrets stay out of strings, writes stay atomic', contract)
            self.assertIn('bug-hunt round 6: fail closed everywhere', contract)
            self.assertIn('bug-hunt round 2 closes races and verifier gaps', contract)
            smoke = (web / "tests/hub-shell-smoke.test.mjs").read_text()
            self.assertIn('RowynDiagnostics.tsx', smoke)
            self.assertIn('RowynPlay.tsx', smoke)
            self.assertNotIn('"ThisPc.tsx":', smoke)


if __name__ == "__main__":
    unittest.main()
