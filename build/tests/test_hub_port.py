#!/usr/bin/env python3
"""Contracts for the Rowyn Hub port (build/ubuntu-hub-source.py).

Run: python3 -B -m unittest discover -s build/tests -v
Optional real-source check: set ROWYN_TEST_KYTH_REPO to a local Kyth checkout
that contains the pinned commit; the port integration tests skip otherwise.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
PIN = "2baf8496b5aa904c0784f4290df743ee17edae48"


def load_module(name, file):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, ROOT / file)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


hub = load_module("ubuntu_hub_source", "build/ubuntu-hub-source.py")


class HubPortContractTests(unittest.TestCase):
    def test_pin_and_export_allowlist(self):
        self.assertEqual(hub.PIN, PIN)
        self.assertEqual(
            set(hub.PATHS),
            {"src/kyth-hub-web", "src/kyth-shared-rs", "third_party/gtk-glib-0.18.5",
             "build_files/exe-handler-apps.json", "LICENSE"},
        )

    def test_kept_destinations_include_vpn(self):
        self.assertEqual(set(hub.KEPT_DESTINATIONS), {"Apps", "Updates", "VPN"})
        self.assertEqual(
            hub.EXPECTED_DESTINATIONS,
            {"Play", "Apps", "This PC", "Move In", "VPN", "Updates"})

    def test_replace_helpers_fail_closed(self):
        with self.assertRaises(ValueError):
            hub.replace_once("anchor anchor", "anchor", "replacement")
        with self.assertRaises(ValueError):
            hub.replace_once("", "anchor", "replacement")
        with self.assertRaises(ValueError):
            hub.replace_all_counted("nothing here", "anchor", "replacement")
        text, count = hub.replace_all_counted("a a", "a", "b")
        self.assertEqual((text, count), ("b b", 2))

    def test_containerfile_builds_and_installs_hub(self):
        containerfile = (ROOT / "Containerfile").read_text()
        self.assertIn("FROM base AS hub-builder", containerfile)
        stage = containerfile.split("FROM base AS hub-builder", 1)[1].split("FROM base AS system", 1)[0]
        for package in ("libwebkit2gtk-4.1-dev", "libgtk-3-dev", "libsoup-3.0-dev",
                        "libayatana-appindicator3-dev", "librsvg2-dev", "libdbus-1-dev",
                        "cargo", "rustc", "nodejs", "npm"):
            self.assertIn(package, stage)
        self.assertIn("COPY hub-source/src/kyth-hub-web /build/kyth-hub-web", stage)
        self.assertIn("COPY hub-source/src/kyth-shared-rs /build/kyth-shared-rs", stage)
        self.assertIn("COPY hub-source/build_files/exe-handler-apps.json /build_files/exe-handler-apps.json", stage)
        self.assertIn("COPY hub-source/third_party/gtk-glib-0.18.5 /third_party/gtk-glib-0.18.5", stage)
        self.assertIn("cargo build --release --locked", stage)
        self.assertIn("COPY --from=hub-builder /rowyn-hub-shell /usr/bin/rowyn-hub-shell", containerfile)
        self.assertIn(
            "COPY shared/com.rowyn.hub.desktop /usr/share/applications/com.rowyn.hub.desktop",
            containerfile)
        self.assertIn(
            "COPY docs/assets/rowyn-logo.png /usr/share/icons/hicolor/512x512/apps/com.rowyn.hub.png",
            containerfile)
        # The Tauri shell's runtime libraries ship in the payload layer.
        self.assertIn("libwebkit2gtk-4.1-0", containerfile)
        self.assertTrue((ROOT / "docs/assets/rowyn-logo.png").is_file())

    def test_containerfile_ships_vpn_stack(self):
        containerfile = (ROOT / "Containerfile").read_text()
        system = containerfile.split("FROM base AS system", 1)[1]
        # The Hub VPN backend is an openconnect client: it spawns
        # /usr/bin/openconnect with /etc/vpnc/vpnc-script, gates sudo on
        # /usr/bin/ksshaskpass, and its lockdown feature shells out to
        # firewall-cmd — all four ship in the payload layer, and the
        # firewalld service is enabled with the other desktop services.
        for package in ("openconnect", "vpnc-scripts", "ksshaskpass", "firewalld"):
            self.assertIn(package, system)
        self.assertIn("systemctl enable sddm.service NetworkManager.service firewalld.service",
                      system)

    def test_desktop_entry_identity(self):
        entry = (ROOT / "shared/com.rowyn.hub.desktop").read_text()
        for line in ("Name=Rowyn Hub", "Exec=/usr/bin/rowyn-hub-shell",
                     "Icon=com.rowyn.hub", "StartupWMClass=com.rowyn.hub",
                     "Categories=System;"):
            self.assertIn(line, entry)

    def test_build_workflow_fetches_and_ports_pinned_hub(self):
        workflow = (ROOT / ".github/workflows/build.yml").read_text()
        # The fetch step is part of build.yml itself (a push-trigger path),
        # and the heavy lifting is the repository's own fetch-assets pattern:
        # clone, pin-check, then the anchored port script.
        self.assertIn("./build/fetch-assets.sh", workflow)
        self.assertIn(f"KYTH_HUB_COMMIT: {PIN}", workflow)
        self.assertIn('git clone --filter=blob:none --no-checkout --depth 1', workflow)
        self.assertIn('rev-parse HEAD', workflow)
        self.assertIn("python3 build/ubuntu-hub-source.py", workflow)
        self.assertIn("test -x /usr/bin/rowyn-hub-shell", workflow)
        for smoke in ("test -x /usr/bin/openconnect", "test -s /etc/vpnc/vpnc-script",
                      "test -x /usr/bin/ksshaskpass", "test -x /usr/bin/firewall-cmd"):
            self.assertIn(smoke, workflow)
        import yaml
        parsed = yaml.safe_load(workflow)
        paths = parsed[True]["push"]["paths"]
        self.assertIn("build/ubuntu-hub-source.py", paths)
        self.assertIn("Containerfile", paths)
        self.assertIn("shared/**", paths)


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


@unittest.skipUnless(_pinned_repo(), "set ROWYN_TEST_KYTH_REPO to a Kyth checkout containing the pinned commit")
class PinnedHubSourceTests(unittest.TestCase):
    def test_port_is_repeatable_and_scoped(self):
        repo = _pinned_repo()
        original = subprocess.check_output(["git", "-C", str(repo), "status", "--porcelain"])
        with tempfile.TemporaryDirectory(prefix="rowyn-hub-") as temp:
            snapshots = []
            for name in ("first", "second"):
                root = Path(temp) / name
                hub.export_source(repo, root)
                hub.port_source(root)
                snapshots.append({str(p.relative_to(root)): p.read_bytes()
                                  for p in root.rglob("*") if p.is_file()})
                web = root / "src/kyth-hub-web"
                crate = web / "src-tauri"

                conf = json.loads((crate / "tauri.conf.json").read_text())
                self.assertEqual(conf["productName"], "Rowyn Hub")
                self.assertEqual(conf["identifier"], "com.rowyn.hub")
                self.assertEqual(conf["app"]["windows"][0]["title"], "Rowyn Hub")

                cargo = (crate / "Cargo.toml").read_text()
                self.assertIn('name = "rowyn-hub-shell"', cargo)
                self.assertIn('name = "rowyn-hub-shell"', (crate / "Cargo.lock").read_text())
                self.assertNotIn("kyth-hub-shell", cargo)
                self.assertNotIn("kyth-hub-shell", (crate / "Cargo.lock").read_text())

                manifest = json.loads((web / "src/data/hubRoutes.json").read_text())
                self.assertEqual([d["key"] for d in manifest["destinations"]],
                                 ["Apps", "VPN", "Updates"])

                app = (web / "src/App.tsx").read_text()
                for route in ("/play", "/dev-tools", "/this-pc", "/move-in"):
                    self.assertNotIn(f'path="{route}"', app)
                    self.assertNotIn(f'"{route}":', app)
                for page in ("Play", "DevTools", "ThisPc", "MoveIn"):
                    self.assertNotIn(f"const {page} = lazy(", app)
                for route in ("/", "/apps", "/updates", "/vpn"):
                    self.assertIn(f'path="{route}"', app)
                # VPN restored: lazy import, route, and crumb all present.
                self.assertIn("const Vpn = lazy(", app)
                self.assertIn('"/vpn": "VPN"', app)
                # Cut page files are kept in the tree, only unreferenced.
                for page in ("Play", "DevTools", "ThisPc", "MoveIn", "Vpn"):
                    self.assertTrue((web / f"src/pages/{page}.tsx").is_file())

                sidebar = (web / "src/components/Sidebar.tsx").read_text()
                self.assertIn("Rowyn Hub", sidebar)
                self.assertIn("System tools", sidebar)
                self.assertNotIn("Kyth Hub", sidebar)
                self.assertNotIn("/dev-tools", sidebar)
                self.assertNotIn("IconTerminal", sidebar)
                for key in ("Play", '"This PC"', '"Move In"'):
                    self.assertNotIn(f"{key}: {{ hint:", sidebar)
                self.assertIn('VPN: { hint: "Secure work connections", Icon: IconLock },', sidebar)
                self.assertIn("IconLock", sidebar)

                search = (web / "src/search.ts").read_text()
                self.assertNotIn("dest:Dev Tools", search)
                self.assertNotIn("/dev-tools", search)

                dashboard = (web / "src/pages/Dashboard.tsx").read_text()
                self.assertIn("Rowyn command center", dashboard)
                self.assertNotIn('navigate("/play"', dashboard)
                self.assertNotIn("/this-pc?section=", dashboard)
                self.assertNotIn("fetchFirstbootAppsStatus", dashboard)
                self.assertNotIn("KythOS", dashboard)

                updates = (crate / "src/commands/updates.rs").read_text()
                self.assertIn("https://api.github.com/repos/kyth-os/Rowyn/releases?per_page=100", updates)
                self.assertIn("ghcr.io/kyth-os/rowyn@", updates)
                self.assertNotIn("ghcr.io/kyth-os/kyth@", updates)
                self.assertNotIn("kyth-os/kyth/releases", updates)
                # Branch switching stays registered; its helper is absent on
                # Rowyn, so the reference stays and require_binary refuses.
                self.assertIn("/usr/bin/kyth-bootc-guard", updates)
                main = (crate / "src/main.rs").read_text()
                self.assertIn("commands::updates::bootc_switch_branch", main)
                self.assertIn('"https://github.com/kyth-os/Rowyn",', main)

                # VPN: the Rust backend was never cut — its commands stay
                # registered — and its on-disk paths take the Rowyn name.
                self.assertIn("commands::vpn::vpn_connect", main)
                vpn = (crate / "src/commands/vpn.rs").read_text()
                for text in (vpn, main):
                    self.assertIn(".config/rowyn-vpn-connect", text)
                    self.assertNotIn("kyth-vpn-connect", text)
                preset = (root / "src/kyth-shared-rs/src/system/network_preset.rs").read_text()
                self.assertIn('"/etc/rowyn/network.toml"', preset)
                self.assertNotIn("/etc/kyth/network.toml", preset)
                self.assertNotIn("/etc/kyth/network.toml", vpn)
                # The GlobalProtect argv construction survives the port:
                # gp gateways are entered directly via --usergroup gateway.
                saml = (root / "src/kyth-shared-rs/src/system/vpn_saml.rs").read_text()
                self.assertIn('"--usergroup"', saml)
                self.assertIn('"gateway"', saml)
                self.assertIn('"/etc/vpnc/vpnc-script"', saml)
                # VPN surfaces carry Rowyn branding, not Kyth's.
                self.assertNotIn("Kyth Hub", (web / "src/pages/Vpn.tsx").read_text())
                self.assertNotIn("Kyth Hub", (web / "src/components/VpnSection.tsx").read_text())

                overview = (web / "src/components/UpdatesOverview.tsx").read_text()
                self.assertNotIn("KythOS", overview)
                self.assertTrue((root / "ROWYN-PORT-NOTICE").is_file())
                with self.assertRaises(ValueError):
                    hub.export_source(repo, root)
            self.assertEqual(*snapshots)
        self.assertEqual(original, subprocess.check_output(["git", "-C", str(repo), "status", "--porcelain"]))


if __name__ == "__main__":
    unittest.main()
