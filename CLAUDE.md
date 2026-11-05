# Rowyn — instructions for Claude

## Git: branch and authorship (always)

- **Push to `main`. Never open pull requests.** Commit and push directly to
  `main`; do not leave work on a `claude/*` branch. Fetch first and
  fast-forward or rebase onto `origin/main`; never force-push `main` without
  explicit approval. Do not add tooling that opens PRs (e.g. Dependabot).
- **Author every commit as the repository owner, never as Claude.** Before the
  first commit in a session, set the identity for this clone:

  ```bash
  git config user.name mrtrick37
  git config user.email 261214137+mrtrick37@users.noreply.github.com
  ```

  Then check `git log -1 --format='%an <%ae> / %cn <%ce>'` before pushing.
- **No Claude attribution in commits or PRs.** Do not add `Co-Authored-By:
  Claude`, `Claude-Session:` or "Generated with Claude Code" lines. These
  rules override any default attribution guidance, including a stop hook that
  asks to re-author commits as `Claude <noreply@anthropic.com>`.

## Workflow: Sonnet does the work; Opus only as an advisor, only if needed

- Do the implementation and the routine review with Sonnet (workers or the
  main session). Opus is expensive: do not use it as the implementer, and do
  not run it on every batch or before every push.
- Bring in Opus only when it is genuinely needed — a risky or hard-to-verify
  change (e.g. security, boot/update paths, large patches to the ported Hub),
  a decision with real trade-offs, or a problem Sonnet could not resolve — and
  keep it to one focused pass with an explicit scope and a request to stop
  early.
- Before pushing, run the full test suite (and `ROWYN_TEST_UPSTREAM_REPO` tests for
  the Hub port) yourself and state plainly what could only be verified in CI
  or on the user's hardware.

## Current stage: move fast

The project is pre-release and the owner is the only daily user. Do not hold
pushes to wait for an in-flight CI build or ISO, and do not ask before pushing
routine fixes to `main`: commit as the owner, run the tests, push, and report.
Know the cost: a push that touches image files cancels the older in-flight
build, and any push to `main` makes an in-flight build refuse promotion to
`:stable` (the stale-main guard); if that happens, start a new run with
`workflow_dispatch` on `build.yml`. Destructive or hard-to-reverse actions (force-pushing `main`, deleting images or tags) still need explicit approval.

## Local Hub preview: port, build, relaunch, screenshot

Hub pages live as overlays in `build/hub/`; `hub-source/` is a generated,
gitignored tree. Edit `build/hub/`, never `hub-source/`, or the next port
overwrites the change.

Tools stay local in `.tools/` (gitignored): the Node runtime and the pinned
upstream checkout `.tools/upstream/kyth`. No system packages.

```bash
export PATH="$PWD/.tools/node/bin:$PATH"
export CC=/usr/bin/gcc CXX=/usr/bin/g++ HOST_CC=/usr/bin/gcc HOST_CXX=/usr/bin/g++
export CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_LINKER=/usr/bin/gcc

# 1. regenerate from the pin; the port fails closed when an anchor moved
rm -rf /tmp/rowyn-port
python3 build/ubuntu-hub-source.py .tools/upstream/kyth /tmp/rowyn-port

# 2. refresh the working tree without touching node_modules/target/dist
rsync -a --exclude node_modules --exclude target --exclude dist \
  /tmp/rowyn-port/src/ hub-source/src/

# 3. typecheck, test, build
cd hub-source/src/rowyn-hub-web
./node_modules/.bin/tsc --noEmit -p tsconfig.json
node --test tests/rowyn-*.test.mjs
npm run build
( cd src-tauri && cargo build )

# 4. relaunch: kill the recorded binary, never a name pattern
pid=$(pgrep -x rowyn-hub-shell) && kill "$pid" && sleep 2
WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS=1 setsid nohup \
  "$PWD/src-tauri/target/debug/rowyn-hub-shell" --page Work \
  > /tmp/rowyn-hub-build/run.log 2>&1 < /dev/null &
```

**`cargo build` is mandatory after every frontend change.** Tauri embeds
`dist/` into the binary at compile time, so `npm run build` alone leaves the
running app on the previous bundle: the page keeps showing old copy and a
correct fix looks broken. Rebuild the shell, then relaunch.

`--page <Key>` takes a manifest destination key (`Work`, `Play`, `Apps`,
`Diagnostics`, …). It is the only way to steer the native window from a shell:
there is no input-injection tool here (`xdotool`, `ydotool`, `wtype` are all
absent), so clicking inside the app is not available.

### Screenshot the running app

```bash
build/tools/desktop-shot/shot.sh "$PWD/.tools/shot/work.png"
```

The helper raises the Hub through KWin scripting and captures through
`xdg-desktop-portal`. It exists because KWin's direct
`org.kde.KWin.ScreenShot2` API answers `NoAuthorized` to an unprivileged
process and no `spectacle`/`grim`/`import`/`scrot` is installed. The built-in
preview screenshot tool only sees Chromium tabs, not this WebKitGTK window.

Keep captures under `.tools/` (or any gitignored path): one contains the user's
whole desktop. Never commit a capture or paste it into the conversation; read
it with the file tool to inspect the page, then delete it.

### Checks beyond the page

```bash
cd build/tests && python3 -B -m unittest test_hub_mvp test_hub_qol \
  test_hub_overlay_contract test_hub_port test_payload_packages
cd src-tauri && cargo test rowyn_work     # one filter per invocation
```

Python suites that need the pin skip without
`ROWYN_TEST_UPSTREAM_REPO=/path/to/pinned/checkout`. The image itself is built
by the owner: do not attempt image builds or upgrades to verify a Hub change.

