This is a re-spec for the plan resolution.

## Terminology

To clear some confusion, we need a term for non-intent-non-review-plan: let's call it "ticket".
The new terminology:
- Plan: A plan in any state, including intent and review.
- Ticket: A plan in the states {planning, todo, implementing, checking, done, rejected}. Excludes intents and reviews.
          Tickets transition between states as work progresses.
- Intent: Intents are always in the state `intent`, they do not transition.
- Review: Reviews are always in the state `review`, they do not transition.

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
- The subcommand `ask` specifies repo in {"planner", "builder"} and state in {"intent", "implementing"}.
  Because the subcommand is a prompt, there is a hard constraint removing "master".
- The git history finds a last commit for the intent and the plan in state planning.
    - Git specifies repo in {"master", "planner", "builder"} and state in {"intent", "planning"}.
      Master immediately removed by hard constraint.
    - Git history does not find any files in the states {"implementing"}.
      This adds a hard constraint, removing "implementing" from the subcommand results.
- The audit log:
    - Human created intent 005.
    - Planner created ticket 005 by planning intent 005. No transitions for 005 after that.
    - Audit log specifies repo in {"master", "planner"} and state in {"intent", "planning"}.
- The tmux pane for planner is visible. Tmux specifies repo in {"planner"}
- The intersection is empty.
- Print each element of the union (let's use a color scheme to highlight repos, states, and datasources):
planner/docs/plans/intent/005_quux.md               # command(ask), git, audit, tmux
planner/docs/plans/planning/005_quux.md             # git, audit, tmux
- The user repeats as `workflow ask planner/docs/plans/planning/005_quux.md`


### Worked example 2

The user wants to do an on-the-fly modification to workflow, running `ask` for a task in the `planning` state.
The sequence number originates from subdivide, so there is no intent with the same number.

- The original command is `workflow ask 002`.
- The path does not specify repo, state, or slug. The path is silent.
- The file system. The following files exist:
    - `master/docs/plans/planning/002_pydantic_models.md`
    - `planner/docs/plans/planning/002_pydantic_models.md`
    - The file system specifies the slug "pydantic_models".
- The subcommand `ask` specifies repo in {"planner", "builder"} and state in {"intent", "implementing"}.
  Because the subcommand is a prompt, there is a hard constraint removing "master".
- The git history finds the last commit to be the one in which 001 was subdivided into 002.
    - Git specifies repo in {"planner"} and state in {"planning"}.
- The audit log says planner created 002 by subdividing 001. No transitions for 002 after that.
  Audit log specifies repo in {"planner"} and state in {"planning"}.
- The tmux pane for planner is visible. Tmux specifies repo in {"planner"}
- The intersection is repo in {"planner"} and state in {"planning"}.
  The plan resolves to `planner/docs/plans/planning/002_pydantic_models.md`.


### Worked example 3

The user made a manual adjustment to a plan in the `todo` state, and wants to push the change.

- The original command is `workflow push 005_quux`
- The path does not specify repo or state, but specifies the slug. The path is silent.
- The file system. The following files exist:
    - `planner/docs/plans/planning/005_quux.md` (this is stale, but provides the slug)
    - (master is already checked out to another branch, and does not have 005)
- The subcommand `push` specifies repo in {"planner", "builder"} and state in {"intent", "implementing"}
- The git history:
    - The branch `plan/005_quux` in master has a new manual commit with this file, that the human wanted to push.
    - Git history does not find any files in the states {"implementing"}. This adds a hard constraint.
    - Git specifies repo in {"master"} and state in {"todo"}
- The audit log says the human transitioned 005 to todo. Audit log specifies repo in {"master"} and state in {"todo"}
- The tmux pane for builder is visible. Tmux specifies repo in {"builder"}
- The intersection is empty.
- Print each element of the union
master/docs/plans/todo/plan/005_quux.md             # git(plan/005_quux), audit
planner/docs/plans/intent/005_quux.md               # command(push)
- The user repeats as `workflow push master/docs/plans/todo/plan/005_quux.md`
