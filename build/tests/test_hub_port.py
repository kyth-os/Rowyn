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
        # The backend pins /usr/bin/openconnect and /etc/vpnc/vpnc-script;
        # Ubuntu lays both out differently, so the image bridges them with
        # symlinks (the build.yml smoke checks assert the pinned paths).
        self.assertIn("ln -s /usr/sbin/openconnect /usr/bin/openconnect", system)
        self.assertIn("ln -s /usr/share/vpnc-scripts/vpnc-script /etc/vpnc/vpnc-script", system)

    def test_containerfile_ships_update_helpers(self):
        containerfile = (ROOT / "Containerfile").read_text()
        system = containerfile.split("FROM base AS system", 1)[1]
        # The Hub's Updates actions invoke three fixed helpers plus a
        # passwordless sudoers allowlist for their fixed argv (the check
        # path runs under `sudo -n`, so without the allowlist it could
        # never run). All four land in the payload, and the sudoers file
        # is syntax-checked at image build time.
        self.assertIn(
            "install -Dm755 /ctx/shared/rowyn-safe-upgrade.sh /usr/bin/rowyn-safe-upgrade",
            system)
        self.assertIn(
            "install -Dm755 /ctx/shared/rowyn-bootc-guard.sh /usr/bin/rowyn-bootc-guard",
            system)
        self.assertIn(
            "install -Dm755 /ctx/shared/rowyn-finalize-staged.sh /usr/libexec/rowyn-finalize-staged",
            system)
        self.assertIn(
            "install -Dm440 /ctx/shared/rowyn-upgrade.sudoers /etc/sudoers.d/rowyn-upgrade",
            system)
        self.assertIn("visudo -cf /etc/sudoers.d/rowyn-upgrade", system)
        for script in ("rowyn-safe-upgrade.sh", "rowyn-bootc-guard.sh",
                       "rowyn-finalize-staged.sh"):
            path = ROOT / "shared" / script
            self.assertTrue(path.is_file(), script)
            self.assertTrue(os.access(path, os.X_OK), script)
        sudoers = (ROOT / "shared/rowyn-upgrade.sudoers").read_text()
        for entry in (
            "%sudo ALL=(root) NOPASSWD: /usr/bin/rowyn-safe-upgrade",
            "%sudo ALL=(root) NOPASSWD: /usr/libexec/rowyn-finalize-staged",
            "%sudo ALL=(root) NOPASSWD: /usr/libexec/rowyn-finalize-staged reboot",
            "%sudo ALL=(root) NOPASSWD: /usr/bin/rowyn-bootc-guard status",
            "%sudo ALL=(root) NOPASSWD: /usr/bin/rowyn-bootc-guard status-json",
            "%sudo ALL=(root) NOPASSWD: /usr/bin/rowyn-bootc-guard check",
            "%sudo ALL=(root) NOPASSWD: /usr/bin/rowyn-bootc-guard switch-latest",
        ):
            self.assertIn(entry, sudoers)
        workflow = (ROOT / ".github/workflows/build.yml").read_text()
        for smoke in ("test -x /usr/bin/rowyn-safe-upgrade",
                      "test -x /usr/bin/rowyn-bootc-guard",
                      "test -x /usr/libexec/rowyn-finalize-staged",
                      "test -s /etc/sudoers.d/rowyn-upgrade"):
            self.assertIn(smoke, workflow)

    def test_port_script_carries_branding_sweep(self):
        script = (ROOT / "build/ubuntu-hub-source.py").read_text()
        # The sweep is what keeps upstream brand copy out of the shipped
        # Hub (an installed-system screenshot caught "KythOS is up to
        # date" on the Updates page). Its anchors must stay in the port:
        # the status-copy module, the service layer, the exe-handler
        # suggestion data, and the focus-session inhibit pair.
        for anchor in ("components/updateMessages.ts",
                       "services/liveData.ts",
                       "build_files/exe-handler-apps.json",
                       "KythOS Focus Session", "Rowyn Focus Session",
                       "Rowyn update quarantined after",
                       "Rowyn ships Plasma Wayland only"):
            self.assertIn(anchor, script)
        # The boot-health identity check is rekeyed to Rowyn's os-release
        # ID; keyed to upstream's, it flags every Rowyn system unhealthy.
        self.assertIn("ID=rowyn", script)
        self.assertIn("Rowyn identity: /usr/lib/os-release is not ID=rowyn", script)

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
                # The update actions shell out to helper binaries; Rowyn
                # ships its own under its own name, so every reference —
                # launch argv and require_binary prechecks alike — is
                # repointed and no Kyth helper name survives.
                self.assertIn("/usr/bin/rowyn-safe-upgrade", updates)
                self.assertIn("/usr/bin/rowyn-bootc-guard", updates)
                self.assertIn("/usr/libexec/rowyn-finalize-staged", updates)
                self.assertNotIn("kyth-safe-upgrade", updates)
                self.assertNotIn("kyth-bootc-guard", updates)
                self.assertNotIn("kyth-finalize-staged", updates)
                self.assertNotIn("KythOS update helper", updates)
                self.assertNotIn("KythOS bootc guard helper", updates)
                # Branch switching stays registered and now reaches the
                # Rowyn guard, which implements only the stable channel.
                self.assertIn("commands::updates::bootc_switch_branch",
                              (crate / "src/main.rs").read_text())
                # The check path lives in the shared crate: its guard
                # invocations and GHCR fallback take Rowyn's name too.
                bootc_query = (root / "src/kyth-shared-rs/src/system/bootc_query.rs").read_text()
                self.assertIn("/usr/bin/rowyn-bootc-guard", bootc_query)
                self.assertNotIn("kyth-bootc-guard", bootc_query)
                self.assertNotIn("ghcr.io/kyth-os/kyth", bootc_query)
                self.assertNotIn(r"ghcr\.io/kyth-os/kyth", bootc_query)
                self.assertIn("ghcr.io/kyth-os/rowyn", bootc_query)
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

    def test_check_classifier_accepts_composefs_manifest_diff(self):
        repo = _pinned_repo()
        with tempfile.TemporaryDirectory(prefix="rowyn-hub-") as temp:
            root = Path(temp) / "src"
            hub.export_source(repo, root)
            hub.port_source(root)
            query = (root / "src/kyth-shared-rs/src/system/bootc_query.rs").read_text()
            body = query.split("pub fn update_check_state(", 1)[1].split("\nfn ", 1)[0]
            self.assertIn('lower.contains("update available for:")', body)
            self.assertIn('lower.contains("total new layers:") && lower.contains("added layers:")', body)
            self.assertIn('lower.contains("no changes in:")', body)
            self.assertIn("fn update_check_state_accepts_composefs_manifest_diff_output", query)
            self.assertIn("/usr/bin/rowyn-bootc-guard", query)

    def test_no_upstream_branding_survives_port(self):
        repo = _pinned_repo()
        with tempfile.TemporaryDirectory(prefix="rowyn-hub-brand-") as temp:
            root = Path(temp) / "export"
            hub.export_source(repo, root)
            hub.port_source(root)
            web = root / "src/kyth-hub-web"
            crate = web / "src-tauri"

            # Frontend: no "KythOS" anywhere under src/ except the one
            # allowlisted provenance file. compat_games.json is data for
            # the cut Play surfaces whose notes record that a verdict
            # came from KythOS validation; rewriting it would falsify
            # the record, so it is the deliberate exception — and the
            # set equality below fails if any other file joins it or if
            # the exception itself disappears upstream.
            allowlist = {"src/data/compat_games.json"}
            survivors = {
                str(p.relative_to(web))
                for p in (web / "src").rglob("*")
                if p.is_file() and p.suffix in (".ts", ".tsx", ".css", ".json")
                and "KythOS" in p.read_text(errors="ignore")
            }
            self.assertEqual(survivors, allowlist)
            self.assertIn("KythOS", (web / "src/data/compat_games.json").read_text())

            # The exact sentence from Patrick's screenshot, rebranded.
            messages = (web / "src/components/updateMessages.ts").read_text()
            self.assertIn("Rowyn is up to date. No restart is needed.", messages)
            self.assertNotIn("KythOS", messages)
            # Service-layer prompts and the not-in-shell error family.
            live_data = (web / "src/services/liveData.ts").read_text()
            self.assertNotIn("KythOS", live_data)
            self.assertNotIn("Kyth Hub", live_data)
            # Exe-handler suggestion data ships in the shell.
            self.assertNotIn(
                "KythOS", (root / "build_files/exe-handler-apps.json").read_text())

            # Rust shell: no upstream brand string survives anywhere in
            # the crate, the identity check keys on Rowyn's os-release
            # ID, and the focus-session inhibit reason took the new name.
            for path in (crate / "src").rglob("*.rs"):
                self.assertNotIn("KythOS", path.read_text(),
                                 f"KythOS survived in {path.name}")
            updates = (crate / "src/commands/updates.rs").read_text()
            self.assertIn("ID=rowyn", updates)
            self.assertNotIn("ID=kythos", updates)
            main = (crate / "src/main.rs").read_text()
            self.assertIn('"--why=Rowyn Focus Session"', main)

            # Shared-crate modules whose display strings the shell
            # surfaces (and the inhibit reaper that must match the
            # reason main.rs sets) are clean and consistent.
            shared = root / "src/kyth-shared-rs/src/system"
            for name in ("process.rs", "boot_health.rs", "desktop_stack.rs"):
                self.assertNotIn("KythOS", (shared / name).read_text(), name)
            self.assertIn("Rowyn Focus Session", (shared / "process.rs").read_text())


if __name__ == "__main__":
    unittest.main()
