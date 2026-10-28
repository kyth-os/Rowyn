#!/usr/bin/env python3
"""Export the pinned Kyth System Hub source, then port it to Rowyn on Ubuntu.

The port is deliberately scoped: only the Home/Dashboard, Apps, Updates,
and VPN destinations survive. Play, This PC, Move In, and the shell-only
Dev Tools route are cut from navigation, routing, search, and the sidebar;
their page files stay in the tree (unreferenced) so the cut stays a small,
anchor-checked diff instead of a fork. The Updates channel is repointed at
Rowyn's own releases and GHCR repository; branch switching stays registered
in Rust but its helper (/usr/bin/kyth-bootc-guard) is not shipped by Rowyn,
so the command fails cleanly through require_binary if ever invoked.

The VPN destination is Kyth's openconnect client (GlobalProtect "gp"
profile, SAML redirect/cookie/reconnect flow); it ports unchanged apart
from its on-disk paths, which take the Rowyn name. The payload image
provides its runtime stack — openconnect, vpnc-scripts, ksshaskpass (the
askpass the backend fails closed without), and firewalld (the lockdown
feature's one firewall-cmd call) — see the Containerfile.

Never executes upstream build scripts. The input checkout is read-only; git
archive exports exactly the pinned objects.
"""
import argparse
import io
import json
from pathlib import Path
import subprocess
import tarfile

PIN = "2baf8496b5aa904c0784f4290df743ee17edae48"
PATHS = ("src/kyth-hub-web", "src/kyth-shared-rs",
         "third_party/gtk-glib-0.18.5", "build_files/exe-handler-apps.json",
         "LICENSE")
KEPT_DESTINATIONS = ("Apps", "Updates", "VPN")
EXPECTED_DESTINATIONS = {"Play", "Apps", "This PC", "Move In", "VPN", "Updates"}


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f"expected exactly one source anchor: {old[:100]!r}")
    return text.replace(old, new)


def replace_all_counted(text, old, new):
    """Replace every occurrence, failing closed when the anchor is absent.

    Returns (text, count) so the port can report exactly what it rewrote in
    production code and in the upstream test fixtures that share the file.
    """
    count = text.count(old)
    if count == 0:
        raise ValueError(f"expected at least one source anchor: {old[:100]!r}")
    return text.replace(old, new), count


def export_source(repo, destination):
    actual = subprocess.check_output(["git", "-C", str(repo), "rev-parse", PIN + "^{commit}"], text=True).strip()
    if actual != PIN:
        raise ValueError("hub source commit mismatch")
    if destination.exists():
        raise ValueError("source destination must not exist (export fresh pinned objects)")
    archive = subprocess.check_output(["git", "-C", str(repo), "archive", PIN, *PATHS])
    destination.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(archive)) as stream:
        # node_modules is an upstream symlink to the developer's workspace.
        # Never export symlinks, submodules, or anything outside the allowlist.
        for member in stream.getmembers():
            if member.isdir():
                continue
            if not member.isfile() or Path(member.name).is_absolute() or ".." in Path(member.name).parts:
                continue
            path = destination / member.name
            path.parent.mkdir(parents=True, exist_ok=True)
            member_stream = stream.extractfile(member)
            assert member_stream is not None
            path.write_bytes(member_stream.read())
            path.chmod(member.mode & 0o777)


def port_source(root):
    changes = []
    web = root / "src/kyth-hub-web"
    crate = web / "src-tauri"

    # --- Tauri identity: Rowyn Hub, com.rowyn.hub -------------------------
    conf = crate / "tauri.conf.json"
    text = conf.read_text()
    text = replace_once(text, '"productName": "Kyth Hub"', '"productName": "Rowyn Hub"')
    text = replace_once(text, '"identifier": "com.kythos.hub"', '"identifier": "com.rowyn.hub"')
    text = replace_once(text, '"title": "Kyth Hub"', '"title": "Rowyn Hub"')
    conf.write_text(text)
    changes.append("tauri.conf.json: productName/identifier/title -> Rowyn Hub / com.rowyn.hub")

    # --- Crate identity: kyth-hub-shell -> rowyn-hub-shell ----------------
    cargo = crate / "Cargo.toml"
    text = cargo.read_text()
    if text.count("kyth-hub-shell") != 2:
        raise ValueError(
            f"expected exactly two kyth-hub-shell anchors in Cargo.toml, found {text.count('kyth-hub-shell')}")
    text = text.replace("kyth-hub-shell", "rowyn-hub-shell")
    text = replace_once(
        text,
        'description = "Native shell for the Kyth Hub web frontend"',
        'description = "Native shell for the Rowyn Hub web frontend"',
    )
    cargo.write_text(text)
    # --locked builds read the root package name from Cargo.lock too; keep
    # the lockfile's single root entry in step with the manifest rename.
    lock = crate / "Cargo.lock"
    text = lock.read_text()
    text = replace_once(text, 'name = "kyth-hub-shell"\nversion = "0.1.0"',
                        'name = "rowyn-hub-shell"\nversion = "0.1.0"')
    lock.write_text(text)
    changes.append("Cargo.toml/Cargo.lock: package + bin kyth-hub-shell -> rowyn-hub-shell")

    # --- Browser-chrome title ---------------------------------------------
    index = web / "index.html"
    text = index.read_text()
    text = replace_once(text, "<title>Kyth Hub</title>", "<title>Rowyn Hub</title>")
    index.write_text(text)

    # --- Destination cut: hubRoutes.json is the navigation source of truth.
    # destinations.ts, deepLink.ts, search.ts, and the sidebar all derive
    # from this manifest, so filtering it here removes the cut pages from
    # every derived surface at once. Fail closed if upstream's destination
    # set is not exactly the reviewed one.
    manifest_path = web / "src/data/hubRoutes.json"
    manifest = json.loads(manifest_path.read_text())
    keys = [entry["key"] for entry in manifest["destinations"]]
    if set(keys) != EXPECTED_DESTINATIONS:
        raise ValueError(f"hub destination set changed upstream: {sorted(keys)}")
    manifest["destinations"] = [
        entry for entry in manifest["destinations"] if entry["key"] in KEPT_DESTINATIONS
    ]
    text = json.dumps(manifest, indent=2) + "\n"
    text = replace_once(text, "one KythOS software center", "one Rowyn software center")
    manifest_path.write_text(text)
    changes.append(
        "hubRoutes.json: destinations cut to " + ", ".join(KEPT_DESTINATIONS))

    # --- App.tsx: lazy imports, routes, and crumbs for the cut pages ------
    app = web / "src/App.tsx"
    text = app.read_text()
    def only_line(haystack, needle):
        matches = [line for line in haystack.splitlines(keepends=True) if needle in line]
        if len(matches) != 1:
            raise ValueError(f"expected exactly one line containing {needle!r}, found {len(matches)}")
        return matches[0]

    for page, route in (("Play", "/play"), ("DevTools", "/dev-tools"),
                        ("ThisPc", "/this-pc"), ("MoveIn", "/move-in")):
        text = replace_once(text, only_line(text, f"const {page} = lazy("), "")
        text = replace_once(text, only_line(text, f'<Route path="{route}"'), "")
    for route, label in (("/play", "Play"), ("/dev-tools", "Dev Tools"),
                         ("/this-pc", "This PC"), ("/move-in", "Move In")):
        text = replace_once(text, f'  "{route}": "{label}",\n', "")
    # VPN is a kept destination: its lazy import, route, and crumb entry
    # must survive untouched. Fail closed if upstream renames any of them,
    # so the restore can never silently degrade to a missing page.
    for anchor in ('const Vpn = lazy(() => import("./pages/Vpn")',
                   '<Route path="/vpn" element={<Vpn />} />',
                   '  "/vpn": "VPN",\n'):
        if text.count(anchor) != 1:
            raise ValueError(f"expected exactly one kept VPN anchor in App.tsx: {anchor[:80]!r}")
    app.write_text(text)
    changes.append("App.tsx: cut /play, /dev-tools, /this-pc, /move-in routes; /vpn kept")

    # --- Sidebar: branding, presentation map, Dev Tools splice ------------
    sidebar = web / "src/components/Sidebar.tsx"
    text = sidebar.read_text()
    text = replace_once(
        text,
        'import { IconHome, IconPlay, IconGrid, IconLock, IconMonitor, IconRefresh, IconShield, IconTerminal, IconTransfer } from "./icons";',
        'import { IconHome, IconGrid, IconLock, IconRefresh, IconShield } from "./icons";')
    text = replace_once(
        text,
        "// in the rail automatically. Home and Dev Tools are shell-only routes\n"
        "// with no manifest destination, so they are spliced in at their\n"
        "// established positions around the derived entries. Presentation (hint +",
        "// in the rail automatically. Home is a shell-only route with no\n"
        "// manifest destination, so it is prepended to the derived entries.\n"
        "// Presentation (hint +")
    for entry in (
        '  Play: { hint: "Games, boost, controllers", Icon: IconPlay },\n',
        '  "This PC": { hint: "Health, hardware, repair", Icon: IconMonitor },\n',
        '  "Move In": { hint: "Files, saves, workflows", Icon: IconTransfer },\n',
    ):
        text = replace_once(text, entry, "")
    # The VPN presentation entry is kept with its destination; fail closed
    # if upstream renames it (the sidebar would fall back to a wrong icon).
    vpn_entry = '  VPN: { hint: "Secure work connections", Icon: IconLock },\n'
    if text.count(vpn_entry) != 1:
        raise ValueError("expected exactly one kept VPN presentation entry in Sidebar.tsx")
    text = replace_once(
        text,
        '  ...workspaceDestinations.slice(0, 2),\n'
        '  { to: "/dev-tools", label: "Dev Tools", hint: "AI dev box, editors, agents", Icon: IconTerminal },\n'
        '  ...workspaceDestinations.slice(2),\n',
        '  ...workspaceDestinations,\n')
    text = replace_once(text, 'aria-label="Kyth Hub navigation"', 'aria-label="Rowyn Hub navigation"')
    text = replace_once(text, '<div className="brand-name">Kyth Hub</div>',
                        '<div className="brand-name">Rowyn Hub</div>')
    text = replace_once(text, '<div className="brand-kicker">System protection</div>',
                        '<div className="brand-kicker">System tools</div>')
    text = replace_once(text, '<div className="sidebar-status-row">Kyth Hub</div>',
                        '<div className="sidebar-status-row">Rowyn Hub</div>')
    sidebar.write_text(text)
    changes.append("Sidebar.tsx: Rowyn Hub branding, cut presentation entries + Dev Tools splice; VPN entry kept")

    # --- Topbar breadcrumb branding ---------------------------------------
    topbar = web / "src/components/Topbar.tsx"
    text = topbar.read_text()
    text = replace_once(text, "Kyth Hub / {crumb}", "Rowyn Hub / {crumb}")
    topbar.write_text(text)

    # --- search.ts: drop the hardcoded Dev Tools destination entry --------
    search = web / "src/search.ts"
    text = search.read_text()
    text = replace_once(
        text,
        'import { IconGrid, IconHome, IconTerminal } from "./components/icons";',
        'import { IconGrid, IconHome } from "./components/icons";')
    text = replace_once(
        text,
        '  entries.push({\n'
        '    key: "dest:Dev Tools",\n'
        '    title: "Dev Tools",\n'
        '    description: "AI dev box, editors, agents",\n'
        '    destination: "Destination",\n'
        '    route: "/dev-tools",\n'
        '    Icon: IconTerminal,\n'
        '  });\n',
        "")
    search.write_text(text)
    changes.append("search.ts: cut hardcoded Dev Tools search entry")

    # --- Dashboard: branding, and cut the parts that navigate to cut pages.
    # The firstboot Play-onboarding card and the This PC action buttons
    # would land on removed routes; the firstboot fetch plumbing goes with
    # its only consumer (tsc rejects unused locals).
    dashboard = web / "src/pages/Dashboard.tsx"
    text = dashboard.read_text()
    text = replace_once(text, '<span className="home-eyebrow">KythOS command center</span>',
                        '<span className="home-eyebrow">Rowyn command center</span>')
    text = replace_once(text, "  fetchFirstbootAppsStatus,\n", "")
    text = replace_once(text, "  type FirstbootAppsStatus,\n", "")
    text = replace_once(
        text, "  const [firstboot, setFirstboot] = useState<FirstbootAppsStatus | null>(null);\n", "")
    text = replace_once(
        text,
        "const [nextGuardian, nextChannel, nextGpu, nextStorage, nextUser, nextBoot, nextRecovery, nextFirstboot] = await Promise.all([\n"
        "        fetchGuardianSnapshot(), fetchUpdateChannel(), fetchGpuName(), fetchStorageFree(),\n"
        "        fetchUserName(), fetchBootRuntimeChecks(), fetchRecoveryStatus(), fetchFirstbootAppsStatus(),\n"
        "      ]);",
        "const [nextGuardian, nextChannel, nextGpu, nextStorage, nextUser, nextBoot, nextRecovery] = await Promise.all([\n"
        "        fetchGuardianSnapshot(), fetchUpdateChannel(), fetchGpuName(), fetchStorageFree(),\n"
        "        fetchUserName(), fetchBootRuntimeChecks(), fetchRecoveryStatus(),\n"
        "      ]);")
    text = replace_once(text, "      setFirstboot(nextFirstboot);\n", "")
    text = replace_once(
        text,
        "        fetchUserName(), fetchBootRuntimeChecks(), fetchRecoveryStatus(), fetchFirstbootAppsStatus(),\n"
        "      ]).then(([nextGuardian, nextChannel, nextGpu, nextStorage, nextUser, nextBoot, nextRecovery, nextFirstboot]) => {",
        "        fetchUserName(), fetchBootRuntimeChecks(), fetchRecoveryStatus(),\n"
        "      ]).then(([nextGuardian, nextChannel, nextGpu, nextStorage, nextUser, nextBoot, nextRecovery]) => {")
    text = replace_once(text, "        setFirstboot((prev) => nextFirstboot ?? prev);\n", "")
    card_start = text.index('        {firstboot && firstboot.state !== "ready" && (')
    card_end = text.index('        <div className="home-card-grid">', card_start)
    text = text[:card_start] + text[card_end:]
    text = replace_once(
        text,
        '<div className="home-actions"><ActionButton label="Run health check" onClick={() => navigate("/this-pc?section=Guardian")} /><ActionButton label="Check updates" onClick={() => navigate("/updates")} /><ActionButton label="Open Repair" onClick={() => navigate("/this-pc?section=Repair")} /><ActionButton label="Open Hardware" onClick={() => navigate("/this-pc?section=Hardware")} /></div>',
        '<div className="home-actions"><ActionButton label="Check updates" onClick={() => navigate("/updates")} /></div>')
    text = replace_once(text, '"Guardian data is unavailable; open This PC to retry."',
                        '"Guardian data is unavailable."')
    text = replace_once(text, '"Boot health data is unavailable; open Diagnostics for recovery guidance."',
                        '"Boot health data is unavailable."')
    dashboard.write_text(text)
    changes.append("Dashboard.tsx: Rowyn branding; cut Play onboarding card + This PC actions")

    # --- Kept-surface display strings -------------------------------------
    hero = web / "src/components/HeroCard.tsx"
    text = hero.read_text()
    text = replace_once(text, "}}>KythOS</span>", "}}>Rowyn</span>")
    hero.write_text(text)

    store = web / "src/components/AppStoreSection.tsx"
    text = store.read_text()
    text = replace_once(text, '<span className="app-eyebrow">Kyth software center</span>',
                        '<span className="app-eyebrow">Rowyn software center</span>')
    text = replace_once(text, "Apps is the KythOS software center", "Apps is the Rowyn software center")
    text = replace_once(text, '"A trusted application for KythOS."', '"A trusted application for Rowyn."')
    text = replace_once(text, '<span className="app-eyebrow">Curated for KythOS</span>',
                        '<span className="app-eyebrow">Curated for Rowyn</span>')
    text = replace_once(text, "through the native Kyth bridge.", "through the native Rowyn bridge.")
    store.write_text(text)

    overview = web / "src/components/UpdatesOverview.tsx"
    text = overview.read_text()
    text, kythos_count = replace_all_counted(text, "KythOS", "Rowyn")
    overview.write_text(text)
    changes.append(f"kept surfaces: HeroCard/AppStoreSection branding; "
                   f"UpdatesOverview KythOS -> Rowyn ({kythos_count} strings)")

    # --- Updates channel: Rowyn releases + GHCR repository -----------------
    updates = crate / "src/commands/updates.rs"
    text = updates.read_text()
    text = replace_once(
        text,
        'const IMAGE_RELEASES_API: &str = "https://api.github.com/repos/kyth-os/kyth/releases?per_page=100";',
        'const IMAGE_RELEASES_API: &str = "https://api.github.com/repos/kyth-os/Rowyn/releases?per_page=100";')
    text, image_count = replace_all_counted(text, "ghcr.io/kyth-os/kyth@", "ghcr.io/kyth-os/rowyn@")
    text, release_count = replace_all_counted(
        text, "github.com/kyth-os/kyth/releases", "github.com/kyth-os/Rowyn/releases")
    updates.write_text(text)
    changes.append(
        f"updates.rs: releases API -> kyth-os/Rowyn; image refs -> ghcr.io/kyth-os/rowyn@ "
        f"({image_count} literals incl. fixtures); release URLs -> kyth-os/Rowyn ({release_count})")

    # --- Feedback issues go to the Rowyn repository ------------------------
    main_rs = crate / "src/main.rs"
    text = main_rs.read_text()
    text = replace_once(text, "/// Opens a prefilled `kyth-os/kyth` issue",
                        "/// Opens a prefilled `kyth-os/Rowyn` issue")
    text = replace_once(text, '        "https://github.com/kyth-os/kyth",',
                        '        "https://github.com/kyth-os/Rowyn",')
    main_rs.write_text(text)
    changes.append("main.rs: feedback issue URL -> kyth-os/Rowyn")

    # --- VPN: on-disk paths take the Rowyn name ---------------------------
    # The backend logic (openconnect argv, SAML flow, askpass gate, the one
    # firewall-cmd lockdown call) ports unchanged; only the paths a user or
    # admin could meet are renamed. Each replace_once asserts exactly one
    # anchor per file, so the saved-profile path is renamed in exactly the
    # two places it is constructed (vpn.rs writer, main.rs reader).
    vpn_rs = crate / "src/commands/vpn.rs"
    text = vpn_rs.read_text()
    text = replace_once(text, ".config/kyth-vpn-connect", ".config/rowyn-vpn-connect")
    text = replace_once(text, "/etc/kyth/network.toml", "/etc/rowyn/network.toml")
    vpn_rs.write_text(text)
    text = main_rs.read_text()
    text = replace_once(text, ".config/kyth-vpn-connect", ".config/rowyn-vpn-connect")
    main_rs.write_text(text)
    preset = root / "src/kyth-shared-rs/src/system/network_preset.rs"
    text = preset.read_text()
    text = replace_once(text, "/etc/kyth/network.toml", "/etc/rowyn/network.toml")
    preset.write_text(text)
    changes.append(
        "vpn.rs + main.rs: saved profile -> ~/.config/rowyn-vpn-connect (2 anchors); "
        "network_preset.rs + vpn.rs comment: preset -> /etc/rowyn/network.toml")

    # --- VPN surfaces: user-visible branding -------------------------------
    vpn_page = web / "src/pages/Vpn.tsx"
    text = vpn_page.read_text()
    text = replace_once(text, "complete sign-in securely inside Kyth Hub.",
                        "complete sign-in securely inside Rowyn Hub.")
    vpn_page.write_text(text)
    vpn_section = web / "src/components/VpnSection.tsx"
    text = vpn_section.read_text()
    text = replace_once(text, "available from the installed Kyth Hub.",
                        "available from the installed Rowyn Hub.")
    vpn_section.write_text(text)
    changes.append("Vpn.tsx + VpnSection.tsx: Kyth Hub -> Rowyn Hub (2 strings)")

    (root / "ROWYN-PORT-NOTICE").write_text(
        f"Kyth System Hub source {PIN}, Apache-2.0 (LICENSE).\n"
        "Modified by Rowyn: Rowyn Hub identity (com.rowyn.hub), destinations cut to\n"
        "Dashboard/Apps/Updates/VPN, updates channel repointed to kyth-os/Rowyn releases\n"
        "and ghcr.io/kyth-os/rowyn images, feedback issues filed against kyth-os/Rowyn,\n"
        "VPN saved-profile path renamed to ~/.config/rowyn-vpn-connect and the network\n"
        "preset path to /etc/rowyn/network.toml.\n"
        "Upstream OS/image/ISO assembly scripts are not included or invoked.\n")
    return changes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    export_source(args.repo, args.destination)
    changes = port_source(args.destination)
    for change in changes:
        print(f"  - {change}")
    print(f"Ported pinned hub {PIN} to {args.destination}")


if __name__ == "__main__":
    main()
