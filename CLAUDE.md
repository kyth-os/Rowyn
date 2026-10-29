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

## Workflow: Sonnet does the work, Opus reviews before any push

- Do the implementation with Sonnet (workers or the main session). Opus is
  expensive, so use it once per finished batch as a reviewer, not as the
  implementer.
- **Never push to `main` until an Opus review of the batch is done** and its
  findings are fixed or explicitly set aside with a reason. Give the reviewer
  the diff, the tests run, and what could not be verified (image builds and
  hardware tests only happen in CI or on the user's machines).
