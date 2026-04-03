## Goals

- Ability to run (limited, 3 is enough) parallel agents:
  one plans the next feature while a second is working on the current feature and a third reviews the previous feature.
- Aiming for increased quality, not maximum velocity.
  There are multiple points where the human operator can review, adjust, and intervene.
  While it is ok that these add some friction to the workflow, the tool aims to streamline the process.
- Git history protection: the bare repo is readable from the sandboxes, but only the human operator pushes.
- Forward-only flow: work only flows forward through the pipeline (planner → builder → reviewer),
  allowing the three agents to act with pipeline parallelism.
  When reviews cause changes to existing features, the changes reenter the pipeline as new plans.
- Codifying lessons learned about how to optimize workflow, as continuous improvement.

## Components

- Python helper script `workflow` for the human operator
    - Steps marked "script: workflow <subcommand> [<args>]" are pure script commands.
    - Steps marked "prompt: workflow <subcommand> [<args>]" are also invoked through the CLI,
      but instead of only running Python logic they dispatch a rendered prompt to the agent:
      the CLI resolves the plan path, runs any mandatory pre-steps (e.g. `git pull`),
      renders the prompt template from the config, and sends it to the appropriate agent's tmux window via libtmux.
    - Always run outside the sandboxes, but may `docker exec` commands inside the sandboxes.
- CLAUDE.md: static instructions for agents (git branch strategy, coding standards, workflow orientation).
    - Auto-generated sections (plan state machine, command listing) are kept in sync by `workflow claude.md`.
    - Does not contain prompt command text; full prompts are rendered from the config and sent directly by the CLI.
- Changes to project cookiecutter

## Prompts modifying the shorthand

Adding `--edit` to any prompt command opens the rendered prompt in `$EDITOR`
(similar to `git commit`), pre-populated with the default prompt text.
The human can freely modify the text before saving and closing the editor.
Clearing the file entirely cancels the command without sending anything.
Without `--edit`, the default rendered prompt is sent as-is.
For example:
```
workflow pr 007_flux_capacitor --edit
```
Opens the pre-rendered `pr` prompt in `$EDITOR`.
The human can delete the deprecation-check instructions and add a security note,
then save and close; the modified text is sent to the reviewer.

## Git branch strategy

- `main` is the only long-lived branch. All other branches are short-lived and deleted after merge.
- All agents pull before starting any command.
- Agents have read access to the bare repo (can pull), but cannot push.
  Only the human operator pushes, enforcing a quality gate at each handoff.
- Transitions that don't need a handoff between agents can be done by the agent itself:
  e.g. prompts `subdivide` and `plan` transition the state but stay with `planner`.
- Transitions that do require a handoff are placed in script commands,
  because these can perform cross-repo git operations.
- For example, the `push` command was specifically introduced to avoid a handoff in `build`,
  which is a long-running prompt command.

### Planner
- Branches from `main`, named `plan/{input_plan}`
  (e.g. `plan/003_foo` could be either for `plan 003_foo` or `subdivide 003_foo`).
- For `critique`: if already on a branch for the plan being critiqued, commit there.
  Otherwise, create `plan/critique/{plan_name}`.
- Plan branches are merged to `main` via the `todo` or `implement` scripts.

### Builder
- Branches from `main`, named `feat/{plan_name}`.
- Works on a single plan at a time.

### Reviewer
- For `pr` and `fix`: works on the `feat/{plan_name}` branch (fetched from bare after `push`).
  Review documents and fixes are committed on the same branch. `accept` merges the whole branch to `main`.
- For `full review` and `followthrough`: branches from `main`, named `review/{output_name}`.

## Terminology

- **actor**: The participant on whose behalf a workflow action is recorded.
  Can be `"planner"`, `"builder"`, `"reviewer"`, or `"human"`.
  For script commands the actor is `"human"`; for prompt dispatch the actor is the agent the prompt was sent to.
  The CLI always writes the log; no agent writes log entries directly.
- **agent**: One of the three AI agents (planner, builder, reviewer) running in a sandbox.
  Does not include the human operator.
- **repo**: A git clone of the project used by a participant.
  The named repos are `master` (human operator), `planner`, `builder`, `reviewer`, and `bare`.
  `master` is a repo but not an agent; `human` is an actor but not a repo.

## Loose path parsing

- Humans are lazy writers. The path_to_plan parameters in both scripts and prompts should match loosely
- All of the following should work, as well as intermediate forms between them:
    - absolute path all the way to the plan file
    - planner/docs/plans/planning/002_pydantic_models.md        # relative path to md file
    - docs/plans/planning/002_pydantic_models.md                # omitting the agent when the command only allows one
    - 002_pydantic_models.md                                    # omitting agent and state, when clear from context
    - 002_pydantic_models                                       # omitting path and suffix (always .md)
    - intent/012_frobnicator                                    # distinguish state when necessary
    - 002                                                       # sequence number is enough, when clear from context
    - intent/012                                                # state and sequence number
- Collision validation: if two non-intent plans share the same sequence number, log a loud error.
  To refer to an intent when an agent-generated plan with the same number exists, use `intent/{number}` explicitly.

## Libraries

- Use click for the CLI
- Use rich for color and tabular layouts
- Use pydantic v2 for schemas and for validation of the yaml config
- Use jinja2 for templating (intent file creation, CLAUDE.md generation, prompt rendering)
- Use libtmux for sending rendered prompts to agent tmux windows
- Use pathlib for path handling and globs
- Can call out to shell tools rather than implementing heavy custom components or adding complex dependencies

## Plan contents

- Plans derived from another plan should start by a reference to the input plan
    - From intent through plan or subdivide
    - From another plan through subdivide
    - From a review
- Naming for the first content sections (omit if not applicable):
    - Goal
    - Scope
    - Problem summary
- The plan will be handed to another agent, without the full session context
    - Write in a self-contained way with regard to the discussion, but not necessarily the code base 
      (the receiving agent has full code search capabilities, so no need to cite the code extensively).
    - Make sure to document all decisions and the reasoning behind them.
    - Make sure to document any "wait, actually I need to..." and similar from the chain-of-thought,
      so that the receiving agent doesn't have to repeat the thought process.
    - Include excerpts of new code
        - When specifying schemas or core function signatures,
        - Core algorithms,
        - When exactness of expression is particularly important,
        - But avoid coding the whole solution in the plan excerpts.
    - Concicely cite existing code when
        - It was difficult to find (multi-step process to find the right location)
        - A before-and-after is the most concise way to describe the change
        - But avoid including large amounts of code just for context: the builder agent can easily search for it.
    - Marking options
        - If presenting open choices in the plan, clearly mark the section(s) containing the optional material.
        - Recommend one of the choices, giving reasons.
        - When the choice is made, the optional section will be marked as discarded, and its contents replaced with a summary
            - A few sentences describing the proposed implementation, phrased as "would have"
            - Motivation for the decision to discard
            - The previous agent recommendation is removed, if it recommended the discarded choice
        - Compacting and rephrasing the discarded sections avoids accidentally spilling them into the implementation,
          while still keeping a record of decisions.
- Dependencies: if a plan depends on another plan, note this in a "Dependencies" section.
  This is a lightweight convention; the tooling does not validate or enforce ordering.
- Include an analysis of potential risks towards the end of the plan
    - The revise discussion after the `critique` shorthand will add risks to this section,
      when the decision is "let's keep the plan, but document this as a risk".

## State machine for plans

- Each state has its own subdirectory under docs/plans/
- Typically plans transition between states by being moved between subdirectories.
    - Some exceptions, where instead of being moved, the input plan spawns one or more children in a different state.

### intent

- Written by the human operator.
- The agent is only allowed to change intent files if explicitly commanded, and changes should be minimal.
- May be loosely structured or incomplete.
- Special transition: intent files are never moved outside of the intent subdirectory.
  Instead, the agent writes one or more detailed plans based on the intent file and answers to questions.
- Id: sequence number + slug, e.g. 001_idea or 012_frobnicator

### planning

- Written by the agent.
- Plans being drafted, before approval.
- Id: sequence number + slug
    - The sequence number is based on the intent file used as input.
    - If the intent to plan mapping is 1:1, use the same number and slug: intent/012_frobnicator -> planning/012_frobnicator
    - If the intent spawns multiple plans, use a number hierarchy and a new slug, e.g.:
        intent/012_frobnicator -> planning/012.1_frobnicator_backend, planning/012.2_frobnicator_frontend
    - Exception from the above: intent/001_idea is global for the whole project.
      Instead of making everything children of 001, the initial subdividing of 001_idea results in other top-level plans
        intent/001_idea -> planning/002_pydantic_models, planning/003_core_algorithm, ...

### todo

- Plans accepted for implementation, before implementation starts.

### implementing

- Plans with ongoing implementation.

### checking

- Implementation done, but review in progress.

### done

- Completed plans, with an accepted and merged implementation.

### rejected

- Rejected plans.
- Plans can transition to rejected from any other state.

### reviews

- The output of critique or code review.
- Special transition: reviews are never moved outside the review subdirectory.
  Instead, one or more plans are formulated to address issues raised.

#### reviews/critique

- Critique for intents and plans
- Should always reference the plan being critiqued
- id: name of critiqued plan + timestamp

#### reviews/pr

- Review of the implementation of a plan
- Should always reference the plan being implemented
- id: `{plan_name}_{commit_hash}` — the commit hash allows multiple review rounds on the same branch

#### reviews/full

- Review of the complete current state of the codebase
- id: timestamp + hash of reviewed commit

## Steps in the workflow

In this walkthrough, the project is called `foo`.

### (Preconditions)

Directory structure:
    /home/auggie/projects/foo/              # project directory
        master/                             # git repo for merging and human work
            docs/plans/

### Cheat-sheet

- script: workflow cheat
- Prints a cheat sheet outlining the workflow
    - Use color: distinguish by markers, fade out table delimiters to gray.
- Markers, `-` for script, `@` for prompt shorthand, `( ... )` for other, `>` for transition
```
                                              Command cheatsheet
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━┓
┃ planner                   ┃               ┃ builder      ┃ reviewer      ┃                ┃ reviewer        ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━┩
│ intent         planning   │ todo          │ implementing │ checking      │ done  rejected │ review          │
│ ─────────────  ────────── │ ───────────── │ ──────────── │ ───────────── │ ────  ──────── │ ─────────────── │
│ @ ask          ( adjust ) │ - implement > │ @ ask        │ ( review )    │                │ @ full review   │
│ ( answer )     @ critique │               │ ( answer )   │ @ pr          │                │ @ followthrough │
│ @ critique     ( revise ) │               │ @ build      │ ( adjust )    │                │ @ refresh       │
│ ( revise )     - todo >   │               │ - push >     │ @ fix         │                │                 │
│ @ plan >                  │               │              │ - doublecheck │                │                 │
│ @ subdivide >             │               │              │ - accept >    │                │                 │
│                           │               │              │ - reject >    │                │                 │
└───────────────────────────┴───────────────┴──────────────┴───────────────┴────────────────┴─────────────────┘
```


### Init

- script: workflow init
- Call out to git to create clones of the master repo
- Default agents: planner, builder, reviewer. Can be modified in config.

Directory structure after:
    /home/auggie/projects/foo/              # project directory
        master/                             # repo for merging and human work
        bare/                               # bare git repo for push
        planner/                            # repo for planner agent
        builder/                            # repo for builder agent
        reviewer/                           # repo for reviewer agent
        .workflow_root                      # marker for the project directory

### Start

- script: workflow start
- Call out to libtmux to open one named window per agent in the configured tmux session
    - Window name matches the agent name (e.g. `planner`, `builder`, `reviewer`)
    - In each window, start the agent's sandbox container (run or start, depending on container existence)
    - Pass in the model that the agent should be using (configurable per agent)
- Also open a window named `builder-shell` with a docker exec bash in the builder sandbox

### @ Ask

- prompt: workflow ask {path_to_plan}
- agent: any
- plan can be in any state, but typically intent or implementing
- "Ask any questions until you have a clear understanding."

### @ Critique

- prompt: workflow critique {path_to_plan}
- agent: planner
- plan can be in any state, but typically intent or planning
"""
Critique the idea/plan.
Please be honest, even harsh if necessary.
Point out any flaws or weak points in the design.
Point out any decisions that have a high risk of becoming technical debt.
Propose additional features, to improve the effectiveness, robustness, and usability of the system.
"""

### @ Plan

- prompt: workflow plan {path_to_plan}
- agent: planner
- Input plan should be `intent`
- Agent creates a new plan in state `planning` in its own repo.
- Planning (plan & subdivide) may benefit from being a skill

### @ Subdivide

- prompt: workflow subdivide {path_to_plan}
- agent: planner
- input plan can be in any state, but typically intent or planning
- Agent creates several new plans in state `planning`

### Todo: Approve a plan

- script: workflow todo {path_to_plan}
- Plan must be in state `planning` in a branch on the `planner` repo.
- Run doublecheck.
- Push from `planner` to `bare`.
- Pull to `master`.
- Transition the plan to `todo`. Commit.
- Merge to `main` branch.
- Push from `master` to `bare`.
- Print workflow reminder: "next step is: workflow implement {new_path_to_plan}"

### Implement: Schedule a plan for implementation

- script: workflow implement {path_to_plan}
- Plan must be in state `planning` or `todo` on the `main` branch of the `master` repo.
- If plan is in `planning`, first perform the same push-pull-merge sequence as `todo`.
- Transition the plan to `implementing`. Commit. Push.
- Fetch in builder.
- Create a new branch `feat/{plan_name}` from main in builder.
- Print workflow reminder: "next step is: workflow ask {new_path_to_plan}". Pause briefly so it can be seen.
- Switch to the builder's tmux window via libtmux

### @ Build

- prompt: workflow build
- `workflow ask {path_to_plan}` should be visible in the session history. If not: start by asking.
- Plan must be in state `implementing`.
- Make one or more commits (depending on the feature complexity). Do not push.

### Push

- script: workflow push {path_to_plan}
- Plan must be in state `checking` on a branch in the `builder` repo.
- Rerun the pre-commit-hook inside the sandbox.
- Run doublecheck
- Transition the plan to `checking`. Commit.
- Push the branch
- Fetch in `reviewer`.

### @ PR Review

- prompt: workflow pr {path_to_plan}
- May benefit from being a skill
"""
Perform a thorough code review of the changes on the `feat/{plan_name}` branch.
Focus on aspects such as
software architecture,
logic errors,
missing or only partially implemented requirements,
fit with existing architecture and conventions in the codebase,
unit test quality and coverage,
security,
and any other relevant aspects.

Also consider integration-style tests:
are they worth the cost for this feature,
and is the codebase ready for them (or should they be postponed to a later phase).

Write a report on the results into `docs/plans/reviews/pr/{plan_name}_{commit}.md`.
Commit your changes. Do not modify the code.
"""

### @ Fix

- prompt: workflow fix [{path_to_review}]
- agent: reviewer
- Path to plan may be omitted, if the review is in the session history. In that case, fix what was most recently reviewed.
- Reviewer implements the fixes on the `feat/{plan_name}` branch.
- Outcomes after fix:
    1. Human runs `doublecheck` + `accept` if satisfied.
    2. Human runs `reject` if the branch is unrecoverably off-track, re-specs with lessons learned.
    3. Human continues iterating in chat, potentially with another `pr` + `fix` cycle.

### Doublecheck

- script: workflow doublecheck {path_to_plan}
- The checks are implemented as a reusable function (`doublecheck-func`),
  also called as part of `todo`, `push` and potentially other commands.
- checks
    - verify that planner only touches plans, and that builder does not touch intents
    - list all added noqa and nosec comments
    - make sure that the git hooks are in the correct state
        - pre-commit hook is a symlink to the version-controlled .git-pre-commit-hook
        - no other hooks are active
    - verify that CLAUDE.md has not been tampered with
- Print workflow reminder: `next step is: workflow [todo|accept] or reject`.
  Print todo or accept based on current state.

### Accept

- script: workflow accept {path_to_plan}
- Push the branch from `reviewer`
- Pull to `master`
- Transitions plan into `done`. Commit.
- Merge to main
- Push

### Reject

- script: workflow reject {path_to_plan}
- Push the branch from `reviewer`
- Pull to `master`
- Transitions plan into `reject`. Commit.
- Merge to main
- Push

### Full review

- prompt: workflow full review
- Most likely to benefit from being a skill
- Specialized substeps
    - Search for dead code: run vulture, analyze the results
    - Propose integration tests:
      considering the software as a whole, which critical paths should be covered by integration-style tests?

### Plan followthrough

- prompt: workflow followthrough
- Go through all plans in the states `intent`, `checking`, and `done`.
- Compare features against the codebase. List unimplemented and only partially implemented features.

### Plan refresh

- prompt: workflow refresh
- Go through all plans in the states `planning` and `todo`.
- For each target plan, check against any plans in the `done` state that have been modified after the target plan.
- When needed, check against the implementation of the features in the codebase.
- List all specifications that need updating due to decisions made after the target plan was written.
    - Decisions that have since been contradicted.
    - Decisions that were initially left open, but have since been made. Remove obsolete branches.
    - Features that can be specified in more detail as dependencies have taken shape.
    - Specifications that seem suboptimal in the new context.
    - Any other necessary updates.

### New: Create a new intent

- script: workflow new {slug}
- Finds the first free sequence number (checking all states and the audit log for plans on branches).
- Opens `docs/plans/intent/{num}_{slug}.md` using `${EDITOR}`.

### Status

- script: workflow status
- Use the subdirectories that plans are in, together with the audit log, to show how far along plans are
- Omit done and rejected plans from the listing
- In addition to current state, show next step(s) for each plan
- Print the lowest free number for plans (check existing plans across all states and the audit log)

### Log

- script: workflow log
- Pretty-prints the single JSONL audit log in chronological order, with rich formatting.
- Filtering options: by actor, by plan, by time range, by command.

### Generate CLAUDE.md

- script: workflow claude.md
- Regenerate the auto-generated sections of CLAUDE.md from the current configuration:
    - **Plan state machine**: which states exist, which agent works in each, available transitions.
      Agent-friendly prose/table format. Not the tabular cheat-sheet diagram.
    - **Command listing**: commands available in each state, with type markers (`-` script, `@` prompt, `( )` manual).
- Static sections (git branch strategy, coding standards) are outside the auto-generated markers and are not touched.
- Prompt command text is **not** included in CLAUDE.md; full prompts live in the config and are rendered at dispatch time.

## Audit log

The CLI writes a single JSONL audit log for all workflow actions.
Location: `{project_dir}/workflow.log.jsonl` (alongside `.workflow_root`).
Not under version control.
Each entry includes:
- `timestamp`: ISO 8601 timestamp
- `actor`: participant this action was performed for (e.g. "planner", "builder", "reviewer", "human")
- `command`: the command executed (e.g. "critique", "subdivide", "todo")
- `input_plan`: the input plan (if applicable)
- `output_plans`: list of output plans (if applicable, e.g. for subdivide)
Example row:
`{"timestamp": "2026-03-18T14-30-00Z", "actor": "planner", "command": "subdivide", "input_plan": "001_idea", "output_plans": ["002_pydantic_models", "003_core_algorithm"]}`


## Dry-run mode

- Global flag: `workflow --dry-run <command> [<args>]`
- Prints the shell commands (git, docker, tmux, mv, etc.) that would be executed, without running them.
- Propagates to all script commands.

## Configuration

### Config location

- During development: config file in the project root (`config.yaml`).
- Installed location: `~/.config/workflow/config.yaml`.
- CLI flag to override: `workflow --config <path> <command> [<args>]`.
- All settings (agent names, models, tmux session, workflow graph, prompt contents) are in a single config file.

### Configurability

Hardcoding the workflow steps in code and CLAUDE.md is too inflexible: making any changes would be tricky.
The opposite extreme of making all steps fully defined in configuration is not optimal either,
as the configuration would become quite complex due to needing to represent multi-step scripts.
Let's aim for a sweet spot where
- the transition graph, prompt texts, and some easily represented data is in a yaml config,
- scripts are hardcoded as python functions,
- the two are linked by a registry.
- Config validation: fail fast at startup if the config references a non-existent script name.

```yaml
tmux:
    session_name: workflow
    prompt_send_delay: 1.0   # seconds to wait after window switch before send-keys

states:
    intent:
        agent: planner
        commands:
            - name: ask
              command_type: prompt
              prompt: |
                Read the plan at {path_to_plan}.
                Ask any questions until you have a clear understanding.
            - name: critique
            ...
    todo:
        agent: null
        commands:
            - name: implement
              command_type: script
              script: implement
              transition: implementing
```

```python
@register_script("implement")
def implement(command: ScriptCommand, canonical_path_to_plan: Plan, ...) -> int:
    ...
```

- Command is a discriminated union of PromptCommand and ScriptCommand.
- Some work is done by the framework (e.g. turning fuzzy paths into canonical ones).
- Common operations extracted into helpers that scripts can call.


## TODO: future work
- additional doublecheck actions
    - in the future: custom bandit rules
        - check that tests only read from project dir or /tmp, and only write to /tmp
        - check that tests properly mock requests and other common networking
        - check that tests mock sleep, to avoid unnecessarily slow tests
    - maybe in the future: if doing TDD, check that test and build only touch appropriate files
- MCP to allow agents to perform some selected git operations outside the sandbox, e.g. `workflow push`
- **Stretch goal: advanced tmux layout management**
    - Side-by-side split: the human's workflow shell always on the left;
      the right pane rotates to show different agents on demand.
    - Dispatching any workflow command (e.g. `workflow implement 002`, `workflow ask 002`) automatically brings up
      the target agent in the right pane using `join-pane` / `break-pane`.
    - This improves ergonomics but adds significant complexity and fragility
      (pane IDs change on session restart; terminal dimensions matter).
      Implement only after the core libtmux dispatch is stable and proven.
    - See docs/plans/intent/011_tmux_pane_rotation.md
