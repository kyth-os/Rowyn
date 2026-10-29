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
- Before pushing, run the full test suite (and `ROWYN_TEST_KYTH_REPO` tests for
  the Hub port) yourself and state plainly what could only be verified in CI
  or on the user's hardware.

## Current stage: move fast

The project is pre-release and the owner is the only daily user. Do not hold
pushes to wait for an in-flight CI build or ISO, and do not ask before pushing
routine fixes to `main`: commit as the owner, run the tests, push, and report.
Know the cost: a push that touches image files cancels the older in-flight
build, and any push to `main` makes an in-flight build refuse promotion to
`:stable` (the stale-main guard); if that happens, start a new run with
`workflow_dispatch` on `build.yml`. Destructive or hard-to-reverse actions
(force-pushing `main`, deleting images or tags) still need explicit approval.
