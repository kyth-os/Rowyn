"""Overlay contracts: section anchors, bridge wiring, and CI coverage."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
HUB = ROOT / "build/hub"
PORT = ROOT / "build/ubuntu-hub-source.py"
WORKFLOW = ROOT / ".github/workflows/build.yml"
CONTAINER = ROOT / "Containerfile"
HELPER = ROOT / "shared/rowyn-boot-health"

# Manifest sections and the file that renders each `hub-section-*` anchor.
# Storage and Power live in the shared SystemCare component, not the page.
SECTION_ANCHORS = {
    "src/pages/RowynPlay.tsx": {
        "Gaming": "hub-section-gaming",
        "SteamLibrary": "hub-section-steam-library",
        "Compatibility": "hub-section-compatibility",
        "Controllers": "hub-section-controllers",
    },
    "src/pages/RowynDiagnostics.tsx": {"Diagnostics": "hub-section-diagnostics"},
    "src/components/SystemCare.tsx": {
        "Storage": "hub-section-storage",
        "Power": "hub-section-power",
    },
    "src/pages/Backups.tsx": {"Backups": "hub-section-backups"},
    "src/pages/RowynDevelopment.tsx": {"Development": "hub-section-development"},
}
ROUTES_WITH_SECTIONS = (
    "src/pages/RowynPlay.tsx",
    "src/pages/RowynDiagnostics.tsx",
    "src/pages/Backups.tsx",
    "src/pages/RowynDevelopment.tsx",
)

KCM_PACKAGES = ("bluedevil", "kscreen", "powerdevil", "plasma-nm", "plasma-pa",
                "kde-config-flatpak", "kup-backup")
SETTINGS_TARGETS = ("defaults", "file-associations", "flatpak-permissions",
                    "bluetooth", "wifi", "audio", "displays", "backups", "power")


class SectionReachability(unittest.TestCase):
    def test_every_declared_section_has_exactly_one_rendered_anchor(self):
        for relative, mapping in SECTION_ANCHORS.items():
            text = (HUB / relative).read_text(encoding="utf-8")
            for section, anchor in mapping.items():
                self.assertEqual(text.count(f'id="{anchor}"'), 1,
                                 f"{relative}: section {section} must render {anchor} once")

    def test_each_rowyn_route_follows_its_declared_sections(self):
        for relative in ROUTES_WITH_SECTIONS:
            text = (HUB / relative).read_text(encoding="utf-8")
            self.assertIn("useSectionFollow(SECTIONS)", text,
                          f"{relative} must follow its declared sections")

    def test_play_has_one_compatibility_section(self):
        play = (HUB / "src/pages/RowynPlay.tsx").read_text(encoding="utf-8")
        self.assertEqual(play.count("Compatibility &amp; anti-cheat"), 1)
        self.assertEqual(play.count('id="hub-section-compatibility"'), 1)

    def test_play_manifest_declares_the_steam_library_section(self):
        port = PORT.read_text(encoding="utf-8")
        for section in ("Gaming", "SteamLibrary", "Compatibility", "Controllers"):
            self.assertIn(f'{{"key": "{section}"', port)

    def test_rowyn_pages_never_poll(self):
        for relative in SECTION_ANCHORS:
            text = (HUB / relative).read_text(encoding="utf-8")
            self.assertNotIn("setInterval", text, relative)


class BootHealthWiring(unittest.TestCase):
    def test_model_validates_the_record_the_helper_writes(self):
        helper = HELPER.read_text(encoding="utf-8")
        model = (HUB / "src/services/rowynBootHealthModel.ts").read_text(encoding="utf-8")
        for key in ("schema_version", "known_good", "failures", "quarantined",
                    "rollback", "last_boot"):
            self.assertIn(f'"{key}"', helper, f"helper must write {key}")
            self.assertIn(key, model, f"model must read {key}")
        self.assertIn("BOOT_HEALTH_SCHEMA = 1", model)

    def test_bridge_is_bounded_read_only_and_registered(self):
        bridge = (HUB / "src-tauri/src/commands/rowyn_boot_health.rs").read_text(encoding="utf-8")
        for token in ("/var/lib/rowyn/boot-health.json", "O_NOFOLLOW", "LIMIT",
                      "spawn_blocking", "rowyn_boot_health_snapshot", "mod tests"):
            self.assertIn(token, bridge)
        for forbidden in ("sudo", "pkexec", "Command::new", "clear-quarantine",
                          "bootc rollback", "fs::write"):
            self.assertNotIn(forbidden, bridge)
        port = PORT.read_text(encoding="utf-8")
        self.assertIn("pub(crate) mod rowyn_boot_health;", port)
        self.assertIn("commands::rowyn_boot_health::rowyn_boot_health_snapshot", port)

    def test_panel_lands_on_updates_and_home(self):
        port = PORT.read_text(encoding="utf-8")
        self.assertIn("BootHealthPanel", port)
        self.assertIn("<BootHealthPanel />", port)
        dashboard = (HUB / "src/pages/Dashboard.tsx").read_text(encoding="utf-8")
        self.assertIn("<BootHealthPanel compact />", dashboard)

    def test_panel_exposes_no_write_path(self):
        panel = (HUB / "src/components/BootHealthPanel.tsx").read_text(encoding="utf-8")
        # The panel may describe rollback attempts; it must not offer to start
        # one, clear a quarantine, elevate, or write anything.
        for forbidden in ("clear-quarantine", "pkexec", "sudo", "systemctl",
                          "invoke(", "fs::", "writeFile"):
            self.assertNotIn(forbidden, panel)
        self.assertIn("terminal-only action", panel)
        self.assertIn("Read-only.", panel)


class SettingsCoverage(unittest.TestCase):
    def test_ui_targets_match_the_bridge_allowlist_and_power_is_the_power_page(self):
        ts = (HUB / "src/services/rowynSettings.ts").read_text(encoding="utf-8")
        rust = (HUB / "src-tauri/src/commands/rowyn_settings.rs").read_text(encoding="utf-8")
        for target in SETTINGS_TARGETS:
            self.assertIn(f'"{target}"', rust, f"bridge must map {target}")
            self.assertIn(target, ts, f"UI must know {target}")
        self.assertIn('"power" => Ok("kcm_powerdevil")', rust)
        self.assertNotIn("kcm_powerdevilprofilesconfig", rust)

    def test_every_module_provider_is_installed_explicitly(self):
        packages = CONTAINER.read_text(encoding="utf-8").split()
        for package in KCM_PACKAGES:
            self.assertIn(package, packages, f"{package} must be explicit, not a recommend")

    def test_image_smoke_checks_providers_and_probe_binaries(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn(
            "for package in bluedevil kscreen powerdevil plasma-nm plasma-pa "
            "kde-config-flatpak kup-backup; do", workflow)
        for binary in ("/usr/bin/nmcli", "/usr/bin/rfkill", "/usr/bin/kscreen-doctor",
                       "/usr/bin/df"):
            self.assertIn(f"test -x {binary}", workflow)


class HubTestJob(unittest.TestCase):
    def test_ci_runs_the_hub_suites_against_the_pinned_export(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("hub-tests:", workflow)
        for token in ("npm ci", "node --test", "ubuntu-hub-source.py"):
            self.assertIn(token, workflow)


if __name__ == "__main__":
    unittest.main()
