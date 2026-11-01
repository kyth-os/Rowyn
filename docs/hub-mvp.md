# Hub Diagnostics and Play MVP

Rowyn owns the small source overlays in `build/hub/`. The pin-export port
applies them **after** the legacy cuts; the 58-module shared closure, removed
helper binaries, gaming backend, just runner and root-daemon client stay cut.
Apps, Updates and VPN keep their existing backends.

## Diagnostics (`/diagnostics`)

Checks run only after **Refresh checks**; no mount-time scan or recurring poll.
Native reads inspect bounded OS/kernel/memory/module files and fixed absolute
`bootc status --json`, `lspci -nnk`, `systemctl --failed`, `df` and
`rowyn-controller-check --json` commands. Commands run as the signed-in user,
with an eight-second bound each and capped capture. A denied bootc read is
**unavailable**, not an invitation to elevate privileges. No repair, reboot,
firmware, package installation, journal upload or recovery actions exist here.

The bridge scrubs credentials, host/user/network identifiers and ANSI escapes
before display; the controller projection omits raw devices and mutation
advice. Scrubbing is best effort: the report preview explicitly asks the user
to review before sharing. Copy uses that preview; **Save report to Downloads**
creates a new `0600` text file only on request, refuses overwrite, and accepts
no caller-selected filesystem path. No files are created by collecting checks.

## Play (`/play`)

Steam, Heroic, Lutris and Bottles use the existing typed personal-Flatpak
install/status/cancel job layer. Explicit scans distinguish installed, not
installed and unknown/partial scopes. Opening accepts only the four curated
IDs, checks both scopes and requests the detected scope. It reports a launch
request, not that a detached application window or game is working.

The MVP detects launcher installations only. Game-library discovery (including
Flatpak Steam library paths), play history, game execution and per-game
compatibility verdicts are deferred; it does not present runner directories as
games or imply that libraries were scanned.

Controller rescan calls the same fixed read-only helper. Compatibility copy is
dated 2026-10-31, sourced to Valve's Proton anti-cheat developer documentation
and linked community reports; it is **not Rowyn game validation** or an online
lookup. Links use the Hub's system-browser bridge.

Launch options are per-game copy-only text, not saved config or an execution
bridge. Host wrappers are for an already-installed host-native launcher. They
are **not generally accessible inside Flatpak Steam**; the sandbox default is
`%command%`. The page neither installs a native launcher nor recommends a
sandbox escape. Mimalloc is experimental and opt-in per game; no custom
kernels, sched-ext, telemetry, Guardian, generic argv or privilege bridge is
introduced.

## Tests

`ROWYN_TEST_UPSTREAM_REPO=/path/to/pinned/checkout ./build/validate.sh` covers
source and actual export contracts. In a fresh exported Hub run
`npm ci && npm run build && node --test tests/*.test.mjs`, then in `src-tauri`
`cargo test --locked`. The Rowyn MVP tests execute real TS model functions,
SSR page construction, native parsers, launcher allowlists, bounded scrubbing,
single-flight checks and private explicit report export. No VM gate is added.
Live launcher downloads, desktop windows, clipboard and physical controllers
still require an installed-system check; container unit tests do not certify
that hardware or desktop integration.
