# Issue tracker: GitHub

Issues, plans and tickets for the whole monorepo (Python packages, apps and `apps/resona-pwa`) live as GitHub issues on [`ecsplico/resona`](https://github.com/ecsplico/resona/issues). Use the `gh` CLI for all operations. Nothing is tracked in the filesystem: no `.scratch/<effort>/issues/*.md`, no committed plan files.

`ecsplico/resona` is **public**. Never put credentials, host names, IP addresses, tailnet names, patient data or personal paths into an issue, comment or label; use placeholders (`<hub-host>`, `<gpu-host>`).

## Conventions

- **Create an issue**: `gh issue create --title "..." --body "..." --label "<triage>" --label "area:<component>"`. Use a heredoc for multi-line bodies.
- **Read an issue**: `gh issue view <number> --comments --json number,title,body,labels,state,assignees,comments`.
- **List issues**: `gh issue list --state open --json number,title,body,labels,comments --jq '[.[] | {number, title, body, labels: [.labels[].name], comments: [.comments[].body]}]'` with appropriate `--label` and `--state` filters.
- **Comment on an issue**: `gh issue comment <number> --body "..."`
- **Apply / remove labels**: `gh issue edit <number> --add-label "..."` / `--remove-label "..."`
- **Close**: `gh issue close <number> --comment "..."`

Every issue carries exactly one triage label (see `triage-labels.md`) and at least one `area:*` label naming the component it touches.

Infer the repo from `git remote -v`; `gh` does this automatically when run inside a clone. The `gh api` calls below spell it out as `repos/ecsplico/resona`.

## Pull requests as a triage surface

**PRs as a request surface: no.** _(Set to `yes` if this repo treats external PRs as feature requests; `/triage` reads this flag.)_

When set to `yes`, PRs run through the same labels and states as issues, using the `gh pr` equivalents:

- **Read a PR**: `gh pr view <number> --comments` and `gh pr diff <number>` for the diff.
- **List external PRs for triage**: `gh pr list --state open --json number,title,body,labels,author,authorAssociation,comments` then keep only `authorAssociation` of `CONTRIBUTOR`, `FIRST_TIME_CONTRIBUTOR`, or `NONE` (drop `OWNER`/`MEMBER`/`COLLABORATOR`).
- **Comment / label / close**: `gh pr comment`, `gh pr edit --add-label`/`--remove-label`, `gh pr close`.

GitHub shares one number space across issues and PRs, so a bare `#42` may be either: resolve with `gh pr view 42` and fall back to `gh issue view 42`.

## When a skill says "publish to the issue tracker"

Create a GitHub issue.

## When a skill says "fetch the relevant ticket"

Run `gh issue view <number> --comments`.

## Efforts, plans and tickets

An **effort** is a body of work that gets a three-letter code (`ADM`, `PWM`, `UIX`, ...). Its plan and its tickets are GitHub issues:

- **Plan = epic issue.** After plan approval, publish the plan as an issue titled `<XXX>: <Plan title>`, labelled `epic` plus the `area:*` labels it spans, with the full approved plan (including `## Ticket breakdown`) as its body. That issue is the durable plan: cite it (`#<n>`) wherever a skill asks for the "durable plan path". omp's plan-mode autosave (`.omp/plans/`) is a session-local draft and is gitignored.
- **Ticket = work issue.** One issue per `i-<xxx>-<description>` ticket, titled `<XXX>: <Ticket title>`, body from the ticket template (what to build, acceptance criteria, the `i-…` id on the first line so it stays greppable), labelled `ready-for-agent` (or `ready-for-human`) plus its `area:*` labels, and attached to the epic as a native sub-issue. Small work that needs no plan is a plain work issue without an effort code or epic.
- **Effort codes must be unique.** A code is taken when any issue title (open or closed) starts with it: `gh issue list --state all --search "<XXX> in:title" --json title --jq '.[].title'`. Retired codes from the pre-GitHub era: `ADM`, `PWM`, `UIX`. On a collision pick different letters; never add digits.

### Hierarchy: native sub-issues

Parent/child is expressed with GitHub's native sub-issues, never with a task list or a prose line. The endpoint takes the child's numeric **database id**, not its `#number`:

```sh
child_id=$(gh api "repos/ecsplico/resona/issues/<child>" --jq .id)
gh api --method POST "repos/ecsplico/resona/issues/<parent>/sub_issues" -F sub_issue_id="$child_id"
```

Read the children back with:

```sh
gh api "repos/ecsplico/resona/issues/<parent>/sub_issues" --jq '.[] | {number, title, state}'
```

`gh api repos/ecsplico/resona/issues/<n> --jq .sub_issues_summary` gives the `total` / `completed` counters for a parent.

### Blocking: native dependencies

Blocking is expressed with GitHub's native issue dependencies, the canonical, UI-visible representation. Publish blockers first so the edge can reference a real issue. The blocker is addressed by database id:

```sh
blocker_id=$(gh api "repos/ecsplico/resona/issues/<blocker>" --jq .id)
gh api --method POST "repos/ecsplico/resona/issues/<child>/dependencies/blocked_by" -F issue_id="$blocker_id"
```

Read the live gate with:

```sh
gh api "repos/ecsplico/resona/issues/<n>" --jq .issue_dependencies_summary
```

`blocked_by` counts **open** blockers only. An issue is takeable when `blocked_by` is 0.

### Frontier (ready work)

The frontier of an effort is its epic's open sub-issues that are unassigned, carry `ready-for-agent` or `ready-for-human`, and have `blocked_by == 0`. When several are free, the epic's `## Ticket breakdown` order wins.

```sh
gh api "repos/ecsplico/resona/issues/<epic>/sub_issues" \
  --jq '.[] | select(.state == "open" and (.assignees | length == 0)) | .number' |
while read -r n; do
  gh api "repos/ecsplico/resona/issues/$n" \
    --jq 'select(.issue_dependencies_summary.blocked_by == 0)
          | select([.labels[].name] | any(. == "ready-for-agent" or . == "ready-for-human"))
          | "#\(.number) \(.title)"'
done
```

### Claiming and closing

- **Claim**: `gh issue edit <n> --add-assignee @me`, before any work on the issue.
- **Close**: comment the outcome (commits, verification) first, then close: `gh issue comment <n> --body "<outcome>"` followed by `gh issue close <n>`. Commits that land a ticket reference it (`Closes #<n>` in the PR body or commit message).
- Close the epic when all its sub-issues are closed.
- A decision that is worth outliving its issue does not stay in the comments: record it as an ADR under `docs/adr/` and link the issue from it. Everything else lives and dies with the issue.

## Wayfinding operations

Used by `/wayfinder`. The **map** is a single issue with **child** issues as tickets.

- **Map**: a single issue labelled `wayfinder:map`, holding the Notes / Decisions-so-far / Fog body. `gh issue create --label wayfinder:map`.
- **Child ticket**: an issue titled `wf-<short-descriptive-slug>`, linked to the map as a native sub-issue (above). Labels: `wayfinder:<type>` (`research`/`prototype`/`grilling`/`task`) plus `area:*`. Once claimed, the ticket is assigned to the driving dev.
- **Blocking**: native issue dependencies (above). A ticket is unblocked when every blocker is closed.
- **Frontier query**: the map's open sub-issues without an open blocker or an assignee; first in map order wins.
- **Claim**: `gh issue edit <n> --add-assignee @me`, the session's first write.
- **Resolve**: `gh issue comment <n> --body "<answer>"`, then `gh issue close <n>`, then append a context pointer (gist + link) to the map's Decisions-so-far.
