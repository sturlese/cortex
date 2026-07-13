---
name: bughunt
description: One autonomous bug-hunt iteration for this repo — find one real, provable bug, fix it on a branch with a regression test, open a PR, gate it with /code-review. Dry-run by default (PR stays open for a human); pass "full" to also merge after CI is green. Designed as the target of /loop.
---

# bughunt — one iteration of the autonomous bug-fix loop

One invocation = one iteration = at most ONE bug shipped end-to-end.

Modes:
- **dry-run** (default): stop after the PR is open and gated. A human merges.
- **full** (arg `full`): after a clean gate, wait for CI and merge.

## 0. Preconditions

- Working tree must be clean and on `main`. If not, STOP and report — never stash or discard user work.
- `git checkout main && git pull origin main` before anything.
- State lives in `bughunt-state.json` in the session scratchpad directory:
  `{"dry_sweeps": 0, "attempted": [], "shipped": [], "abandoned": []}`. Create it if missing.

## 1. Tend existing PRs before hunting

`gh pr list --author "@me" --state open --json number,title,body` and keep the ones whose body contains `bughunt`.

- If one has failing CI: this iteration fixes that branch instead of hunting a new bug (steps 4-6 on that branch).
- If 3 or more are open: do NOT hunt. Report the pile-up and, if running under /loop, stop the loop — human attention is needed.

## 2. Hunt — find ONE provable bug

Sweep the code with Explore agents (split by area: pipeline/fetch, pipeline/clean, pipeline/corpus, pipeline/graph, gbrain, evals). A candidate qualifies only if ALL of these hold:

- A genuine logic/contract/crash bug, demonstrable with a failing test — not style, not "could be cleaner", not a hypothetical.
- NOT in the deferred backlog: read
  `/Users/marc/.claude/projects/-Users-marc-dev-public-cortex/memory/deep-review-deferred-backlog.md`
  and exclude every item listed there (deliberately deferred — design- or migration-sensitive).
- NOT already covered by an open PR, and not in `attempted` in the state file.
- NOT in `.env*` files (human-managed in this repo), eval goldens, or tuned verifier thresholds.

Verify the winning candidate yourself by reading the code before accepting it; agents propose, you confirm.

If nothing qualifies: increment `dry_sweeps`; at 2 consecutive dry sweeps stop the loop and report. When a bug IS found, reset `dry_sweeps` to 0 and add it to `attempted`.

## 3. Fix on a branch

- Branch `fix/bughunt-<slug>` off up-to-date main.
- Minimal fix matching surrounding conventions. This repo is a portfolio piece: no refactors, no drive-by cleanups, keep the diff under ~200 lines.
- Add a regression test in the owning package's suite, following its existing test style. The test must fail without the fix (actually check this once).
- Stage files explicitly (`git add <paths>`); NEVER `git add -A` or `git add .`.

## 4. Validate locally

- Owning package, same gate as CI: `cd pipeline/<pkg> && python3 -m pytest -q --cov-fail-under=75` (for gbrain/evals changes run the nearest equivalent suite).
- `ruff check pipeline evals`
- If the change could affect the offline goldens: `make eval`.
- Still red after 3 fix attempts → abandon: record it in `abandoned` with the reason, switch to main, delete the branch, end the iteration. An abandoned bug is NOT a dry sweep.

## 5. Open the PR

- Conventional scoped commit like the existing history, e.g. `fix(corpus): …`, ending with:
  `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`
- `git push -u origin <branch>`, then `gh pr create` with title `fix(<scope>): <summary>` and body sections **Bug**, **Root cause**, **Fix**, **Test**. Include the word `bughunt` in the body (step 1 of later iterations searches for it) and end the body with:
  `🤖 Generated with [Claude Code](https://claude.com/claude-code)`
- Record it in `shipped`.

## 6. Gate — adversarial review

- Run the `/code-review` skill at high effort on this branch's diff vs main.
- Any confirmed correctness finding: fix it, re-run step 4, push. If the gate still fails after 2 rounds, post the finding as a PR comment and leave the PR open for a human. Never merge a PR that failed the gate.
- Dry-run mode ends here: report and leave the PR open.

## 7. Merge (full mode ONLY)

- `gh pr checks <n> --watch` — CI runs tests for the 4 packages, lint, offline evals, compose-validate.
- Green → `gh pr merge <n> --squash --delete-branch`. Not green → leave the PR open and report.
- Context: repo-level auto-merge is disabled and `main` is unprotected, so watch-then-merge is the mechanism. If branch protection with required reviews is ever enabled, this step needs a reviewer-bot token (`GH_TOKEN=<bot> gh pr review --approve`) before merging.

## Loop integration

Intended loop target (self-paced):
`/loop Run one bughunt iteration: Read .claude/skills/bughunt/SKILL.md and follow it exactly (dry-run mode).`

- Pacing: schedule the next wakeup ~10 min after shipping a PR, ~30 min after a dry sweep or an abandon.
- Stop conditions: 2 consecutive dry sweeps, 3+ open bughunt PRs, or the user says stop.
- Every iteration ends with a short report: what was found, fixed and shipped — or why nothing was.
