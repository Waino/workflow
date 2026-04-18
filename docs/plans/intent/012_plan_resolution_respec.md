This is a re-spec for the plan resolution.

## Terminology

To clear some confusion, we need a term for non-intent-non-review-plan: let's call it "ticket".
The new terminology:
- Plan: A plan in any state, including intent and review.
- Ticket: A plan in the states {planning, todo, implementing, checking, done, rejected}. Excludes intents and reviews.
          Tickets transition between states as work progresses.
- Intent: Intents are always in the state `intent`, they do not transition.
- Review: Reviews are always in the state `review`, they do not transition.

"Ticket" is a documentation/naming distinction only: all fields and methods of `Plan` apply to intents as well,
so a separate type-level `Ticket` class is not introduced up front. This stays DRY; introduce a type-level
distinction later only if it meaningfully simplifies calling code.

At the time of writing, only intent/001_idea has been updated to reflect the new terminology.
Neither the other plans nor the codebase has been updated.
TODO: Check each usage of "plan", and change to "ticket" when appropriate.

## Assumptions

1. Tickets only ever move forward through the workflow states, never backward.
2. There is only a single ticket with any given sequence number (tickets exclude intents).
   It is an error if two different tickets with the same number are found in a single repo.
3. Stale copies of a ticket can be found in other repos, if the transition hasn't been merged to that repo yet.
4. Prompt commands can do transitions that don't require a handoff between agents.
5. Transitions that do require a handoff are placed in script commands, because these can perform cross-repo git operations.
6. Script commands with a transition always involve a push to the bare repo.
7. Resolving the correct agent for a plan is important for agent routing, and checking the correct repo.
   Resolving the exact state within that agent/repo is secondary: we need it to construct the path, but not much else.
8. Script commands may have strict state requirements,
   but prompt commands only need the agent and the ticket visible to that agent (to instruct the agent what to read).

## UX goals

9. The user may want to do ad-hoc modifications to the workflow, e.g. use a prompt command in an untypical state.
   If it is possible to do so, we shouldn't prevent it just to enforce the workflow.
   For example, maybe there's an old subdivided ticket in state `planning`, that hasn't been critiqued yet.
   Before running `critique`, it is advisable to run `ask`.
   (However, this `ask` is not normal workflow for a plan that was just created in the same session).
10. When the user explicitly gives the state, (e.g. "planning/002"), that state should be used.
    If a plan in that state with that number doesn't exist, raise PlanNotFoundError.
    But if a plan in that state with that number does exist, never raise PlanNotFoundError.
11. When the user explicitly gives the repo, (e.g. `ask builder/docs/plans/intent/010` specifies the builder repo),
    that repo should be used.
    The command above might be useful to instruct builder to read and familiarize with a relevant intent file,
    before building another ticket.
12. The target agent should be predictable.
    Tmux state is highly informative for prompt commands:
    the user will be looking at the target agent, not sending prompts to an unseen agent.

## Observations and consequences

- Due to 2, the current state of a ticket can be easily found by globbing on the given hints across all repos,
  and taking the most progressed state (due to 1 and 3).
- Due to 4, 5, and 6, we know that the current state of a ticket is found in the bare repo after each handoff command.
- Shouldn't need an `also_valid_in_states` field: because of 9, `ask` is automatically valid in both planner states.

## Decisions

- The canonical resolver entry point is a new merged resolver that works across all repos.
  Internal helpers may search individual repos, but external callers use the merged entry point.
- The resolver reimplementation replaces the current single-repo `resolve_plan` in `workflow/plan_resolver.py`
  and the per-repo loop in `workflow/tmux_dispatch.py`. Surrounding tooling is updated accordingly.
- Evidence from information sources is combined as the intersection of repo-sets AND the intersection of
  state-sets, independently (not as intersection over (repo, state) tuples). Sources that are silent on a
  dimension contribute no constraint on that dimension.
- Tmux only specifies an agent (window name). To avoid unnecessary empty intersections, tmux's repo
  constraint is `{<active_agent>, "master"}` — the master repo has no pane of its own and is implicitly
  always reachable from the command pane.
- An explicit repo hint in the fuzzy path always wins, even if it conflicts with the active tmux pane.
  The resolution target also determines the dispatch target for prompt commands. Script commands may have
  hard constraints that prevent arbitrary dispatch.
- `expected_states` / `also_valid_in_states` is removed from the resolver signature if it can be done cleanly.
  Script commands with strict state requirements (e.g. `todo`, `check`, `accept`) validate the resolved
  plan's state themselves after resolution. `validate` (formerly `doublecheck`) is then called on an
  already-resolved plan without further resolution.
- The resolver consults the audit log as one of its information sources.
- Reviews are resolved by a separate function. Detection rule: the fuzzy path must contain the prefix
  `reviews/`. (Timestamp / commit-hash patterns are permissible in plan slugs, so pattern matching on the
  filename alone is unreliable.)
- When the intersection is empty, the resolver prints the union with source attributions and exits with a
  non-zero status. The output targets a human reader; programmatic callers are expected to pass
  unambiguous paths.
- Collision rule for same sequence number: intent-vs-ticket pairs are permitted (per 001); two tickets
  with the same sequence number are an error, regardless of slug.
- Filesystem is intentionally replaced by git history as a source when repo/state need to be inferred.
  Scripts that fetch (rather than pull) into an agent repo leave that repo's working tree on an older
  branch while the update is visible in remote-tracking refs — so the working tree cannot be trusted.
  Git history across all refs in each clone sidesteps that confounder.
- If the fuzzy path already unambiguously fixes repo, state, and sequence number, we skip git entirely
  and only consult the filesystem to fill in the slug. In every other case we query git history.

## Git history as an information source

Given a filename (sequence number + slug + `.md`, or just a sequence number when the slug is unknown),
we need the newest non-merge commit touching such a file, across all refs, in each clone. The command:

```
git -C <repo> log --all --no-merges --max-count=1 \
    --format='%H %ct' --name-status \
    -- ':(glob)docs/plans/*/<filename>.md' ':(glob)docs/plans/reviews/*/<filename>.md'
```

Flags:
- `--all` walks all refs (local branches, remote-tracking refs, tags, HEAD).
- `--no-merges` skips merge commits so a merge-to-main doesn't win over the real content commit.
- `--max-count=1` returns only the newest match; git short-circuits, so this is fast even on large
  histories.
- `--format='%H %ct'` prints commit hash and committer Unix timestamp.
- `--name-status` prints one line per changed path with status (`A`/`M`/`D`/`R…`), which tells us the
  resolved path (including renames across state dirs).
- `:(glob)…` magic pathspec lets `*` match any single state directory; two pathspecs cover both regular
  state dirs and `reviews/*`.

If the slug is unknown, widen the filename portion to `<seq>_*.md`.

Parsing per commit:
- If there is an `R…` line (rename), the path after the second tab is the resolved path.
- Otherwise the added/modified path is the resolved path.
- A pure `D` (delete) would mean the file was removed entirely, which doesn't occur in the workflow —
  treat as "no match" defensively.

Across repos: run the command in each of master, planner, builder, reviewer (bare is redundant as long
as the clones' remote-tracking refs are fresh — script commands keep them fresh). Pick the entry with
the highest `%ct`; the winning clone gives the repo, the parsed path gives the state.

Caveats to cover in the implementation plan:
1. `%ct` is committer date, which advances on rebase — this matches the "most recent touch" semantic we
   want. Swap to `%at` (author date) only if a different semantic is needed.
2. Rename detection is on by default at 50% similarity, which is ample for state-dir moves. If a human
   heavily rewrites while moving, git can emit paired `D old` + `A new` in the same commit instead of
   `R`; treat same-commit `D` + `A` with matching basenames as a rename.
3. `--all` includes stale tag refs. Normal operation is fine; if it becomes noisy, restrict to
   `refs/heads/*` and `refs/remotes/*`.

## Resolving the branch for a plan

Some script commands (e.g. `todo`, `implement`) need to push a planner branch to bare for merging.
The planner's branch-selection rule (see 001 §Planner) keeps related plans on a single `plan/*` branch
and does not nest, so a plan's sequence number does not in general match the name of the branch that
holds it (e.g. `subdivide 001` puts `002`, `003`, ... on `plan/001_idea`). The branch is therefore
resolved from git rather than from the plan name.

Two-step lookup in the planner clone:

1. Find the newest non-merge commit touching the plan file (same query as the resolver):
   ```
   git -C planner log --all --no-merges --max-count=1 --format='%H' \
       -- ':(glob)docs/plans/*/<filename>.md'
   ```
2. Find `plan/*` branches that contain that commit:
   ```
   git -C planner for-each-ref --contains <hash> --format='%(refname:short)' 'refs/heads/plan/*'
   ```

Disambiguation:
- **One match**: use it.
- **Zero matches**: the commit is only reachable from `main` (the plan branch was already merged and
  deleted). Operate on `main`.
- **Multiple matches**: ambiguous (e.g. cherry-pick or stranded merged-but-not-deleted branch).
  Prompt the user to pick.

This stays within the existing "git is the source of truth, no new metadata" design and reuses the
same git infrastructure as the resolver.

## Open questions

(none currently — Q12 resolved as above; see Future work for the audit-log optimization.)

## Future work

- Optimization: record the resulting branch name in the audit log entry for `plan` and `subdivide`
  (alternative D in the design discussion). The resolver can then short-circuit the
  `for-each-ref --contains` query when the audit log has a hit, and fall back to git for plans
  touched by manual operations the audit log doesn't capture. Defer until the git-based lookup
  proves to be a measurable bottleneck.

## Information sources

The plan resolution can use information from multiple sources.

### The command line

#### The (fuzzy) path

The command line may give varying levels of detail, from just a number to a fully determined path.
When hints are given (state, repo), the specified values take precedence, no inference is needed to resolve them.
Only when hints are missing do we need to infer the correct values.

- Pros
    - Unambiguous and top priority, if specified.
- Cons
    - May be omitted.

#### The subcommand

Some subcommands unambiguously resolve to a particular agent/state, e.g. `build` implies `builder`/`implementing`.
Other subcommands leave abiguity, e.g. `ask` could be directed at either planner or builder.
The states in which the subcommands are defined comes from the config, and is reflected on the cheatsheet.

- Pros
    - Never omitted.
    - Some subcommands have hard contraints: e.g. prompts can only be sent to agents, not to master.
- Cons
    - May leave ambiguity.
    - Workflow may be adjusted on the fly: the user should specify an unambiguous path when doing this.

### The filesystem (checked out work trees)

- Pros
    - Easy to check (can use simple globbing)
- Cons
    - Repo contents may be stale: must use the most progressed state if repos conflict
    - Repos may be checked out to unexpected branches
    - Mtimes are not useful: show when the last git operation touched the file, not the time of a logical edit
    - Redundant if git history is used

### Git history

- Pros
    - Complete information
- Cons
    - Difficult and expensive to query
    - Can get logical modification time information (when a modification to the file was last committed in any repo)
    - May be overkill to support commands acting on files not checked out anywhere

### Audit log

- Pros
    - Unified information across actors
- Cons
    - Incomplete: only captures workflow commands, but does not capture any manual operations

### Tmux state

The human operator will typically not want to send prompt commands blindly to unseen agents.
We can therefore assume that the target agent/repo is the one currently active in tmux.
For example, if builder is active in tmux and the command is `ask intent/010`, 
it is natural to assume the prompt should be sent to builder, not planner.

- Pros
    - Exact match with user expectation
- Cons
    - Difficult and potentially brittle to query
    - Transition commands with handoff (todo, implement, push) might start with either source or target agent active.
        - However, these commands unambiguously define the state and agents, so there is no ambiguity to resolve.

## Option 1: Combining the information sources

- The sequence number is mandatory for intents and tickets. If no sequence number is given, abort.
    - Reviews have their own resolution function.
- If the given path contains both the repo and the state, then those values should be used.
    - Absolute path: nothing to infer.
    - Relative path from project root: nothing to infer.
    - `planner/planning/002_pydantic_models`: fill in static components to `planner/docs/plans/planning/002_pydantic_models.md`
    - `planner/planning/002`: fill in static components and only infer the slug.
- If the given path contains the slug, use it. Else, scan the filesystem to determine the slug.
    - If more than one slug is found for the sequence number: abort with an error
    - If no file is found with that sequence number: abort with PlanNotFound. Quick exit for invalid sequence numbers.
    - Alternative: use the audit log to quickly find the slug. Avoids scanning the filesystem entirely.
- If either repo or state is omitted, infer using multiple data sources.
    - Each data source provides a set of possible values.
    - Filesystem superceded by git at this point.
- If any data source is silent, omit it from the comparison.
    - For example a newly created intent is not mentioned at all in the audit log:
      treat it as if the audit log allows any repo/state, rather than allowing none.
- Take the intersection of the sets.
    - If there is more than one element: print all matches as (partial) relative paths from the project root.
      The user can repeat the command with a more precise path.
    - If there are no elements in the intersection: print all matches in the union.
      For each element, print which data sources support it.
      The user can repeat the command with a more precise path to resolve.

### Worked example 1

The user wants to do an on-the-fly modification to workflow, running `ask` for a task in the `planning` state.
The sequence number originates from a `plan` command, so there is an intent with the same number.

- The original command is `workflow ask 005`.
- The path does not specify repo, state, or slug. The path is silent.
- The file system. The following files exist:
    - `master/docs/plans/intent/005_quux.md`
    - `master/docs/plans/planning/005_quux.md`
    - `planner/docs/plans/intent/005_quux.md`
    - `planner/docs/plans/planning/005_quux.md`
    - `builder/docs/plans/intent/005_quux.md`
    - The file system specifies the slug "quux".
- The subcommand `ask` is a prompt. Prompts can only be sent to agents, so the repo set is
  {"planner", "builder"} (hard constraint removing "master"). Prompts do not constrain state.
- The git history finds a last commit for the intent and for the ticket in state planning.
    - Git specifies repo in {"master", "planner", "builder"} and state in {"intent", "planning"}.
      Master immediately removed by the prompt hard constraint → {"planner", "builder"}.
- The audit log:
    - Human created intent 005.
    - Planner created ticket 005 by planning intent 005. No transitions for 005 after that.
    - Audit log specifies repo in {"master", "planner"} and state in {"intent", "planning"}.
      After the prompt hard constraint → repo {"planner"}.
- The tmux pane for planner is visible. Tmux specifies repo in {"planner", "master"}; after the
  prompt hard constraint → {"planner"}.
- Intersection on repo: {"planner", "builder"} ∩ {"planner", "builder"} ∩ {"planner"} ∩ {"planner"} = {"planner"}.
- Intersection on state: {"intent", "planning"} ∩ {"intent", "planning"} = {"intent", "planning"}.
  (Subcommand and tmux are silent on state.)
- Two matches remain: `planner/docs/plans/intent/005_quux.md` and `planner/docs/plans/planning/005_quux.md`.
- Print each element of the intersection (let's use a color scheme to highlight repos, states, and datasources):
planner/docs/plans/intent/005_quux.md               # fs, git, audit
planner/docs/plans/planning/005_quux.md             # fs, git, audit
- The user repeats as `workflow ask planner/docs/plans/planning/005_quux.md`
  (or the shorter `ask planning/005`).


### Worked example 2

The user wants to do an on-the-fly modification to workflow, running `ask` for a task in the `planning` state.
The sequence number originates from subdivide, so there is no intent with the same number.

- The original command is `workflow ask 002`.
- The path does not specify repo, state, or slug. The path is silent.
- The file system. The following files exist:
    - `master/docs/plans/planning/002_pydantic_models.md`
    - `planner/docs/plans/planning/002_pydantic_models.md`
    - The file system specifies the slug "pydantic_models".
- The subcommand `ask` is a prompt: repo in {"planner", "builder"} (hard constraint removing "master"),
  no state constraint.
- The git history finds the last commit to be the one in which 001 was subdivided into 002.
    - Git specifies repo in {"planner"} and state in {"planning"}.
- The audit log says planner created 002 by subdividing 001. No transitions for 002 after that.
  Audit log specifies repo in {"planner"} and state in {"planning"}.
- The tmux pane for planner is visible. Tmux specifies repo in {"planner", "master"};
  after the prompt hard constraint → {"planner"}.
- Intersection on repo: {"planner"}. Intersection on state: {"planning"}.
  The plan resolves to `planner/docs/plans/planning/002_pydantic_models.md`.


### Worked example 3

The user made a manual adjustment to a plan in the `todo` state, and wants to push the change.

- The original command is `workflow push 005_quux`
- The path does not specify repo or state, but specifies the slug. The path is silent.
- The file system. The following files exist:
    - `planner/docs/plans/planning/005_quux.md` (this is stale, but provides the slug)
    - (master is already checked out to another branch, and does not have 005)
- The subcommand `push` is a generic content-push. Soft constraint via `repo_override: master`:
  repo preferred in {"master"}, state in {"intent", "todo"}. No hard constraint.
- The git history:
    - The branch `plan/005_quux` in master has a new manual commit with this file, that the human wanted to push.
    - Git specifies repo in {"master"} and state in {"todo"}.
- The audit log says the human transitioned 005 to todo. Audit log specifies repo in {"master"} and state in {"todo"}.
- The tmux pane for builder is visible. Tmux specifies repo in {"builder", "master"}
  (master is implicitly included because the command pane is always active).
- Intersection on repo: {"master"}. Intersection on state: {"todo"}.
  The plan resolves to `master/docs/plans/todo/005_quux.md`.
