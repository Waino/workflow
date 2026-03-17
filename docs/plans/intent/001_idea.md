FIXME: pushes and pulls
FIXME: branch naming and lifespan

## Goals

- Ability to run (limited, 3 is enough) parallel agents:
  one plans the next feature while a second is working on the current feature and a third reviews the previous feature.
- Aiming for increased quality, not maximum velocity.
  There are multiple points where the human operator can review, adjust, and intervene.
  While it is ok that these add some friction to the workflow, the tool aims to streamline the process.
- Git history protection: the bare repo is readable from the sandboxes, but only the human operator pushes.
- Codifying lessons learned about how to optimize workflow, as continuous improvement. 

## Components

- Python helper script `agentic_workflow` for the human operator
    - Steps marked "script: agentic_workflow <subcommand> [<args>]"
    - Always run outside the sandboxes, but may docker exec commands inside the sandboxes
- CLAUDE.md and/or skills: instructions for the agent, referenced by shorthand for convenience
    - Steps marked "prompt: workflow <subcommand> [<args>]"
- Changes to project cookiecutter

## Prompts modifying the shorthand

Note that in addition to the shorthand, the complete prompt can contain other instructions,
that modify the instructions in the shorthand definition.
For example:
```
workflow pr 007_flux_capacitor 
This is a greenfield development, ignore the deprecation checks.
This is a security critical feature, pay extra attention to untrusted inputs.
```
The above means that any instructions in the `pr` workflow concerning how to review deprecating functionality should be ignored (override to subtract from the workflow),
and an extra instruction concerning security should be followed (override to add to the workflow).

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

## Libraries

- Use click for the CLI
- Use rich for color and tabular layouts
- Use pydantic v2 for schemas and for validation of the yaml config
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
- id: name of critiqued plan + hash of reviewed commit

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

- script: agentic_workflow cheat
- Prints a cheat sheet outlining the workflow
    - Use color: distinguish by markers, fade out table delimiters to gray.
- Markers, `-` for script, `@` for prompt shorthand, `( ... )` for other, `>` for transition
```
- init
- start

<------------------ planner -------------> |               | <- builder -> | <----------- reviewer --------------->
intent                     | planning      | todo          | implementing  | checking      | done | reviews
-------------------------- | ------------- | ------------- | ------------- | ------------- | ---- | ---------------
                           |               |               |               |               |      |                
@ ask         | @ ask      | ( adjust )    | - implement > | ( pull )      | ( review )    |      | @ full review
( answer )    | ( answer ) | @ critique    |               | ( branch )    | @ pr          |      | @ followthrough
@ critique    | @ plan >   | ( revise )    |               | @ ask         | ( adjust )    |      |
( revise )    |            | - doublecheck |               | ( answer )    | @ fix         |      |
@ subdivide > |            | - todo >      |               | @ build >     | - doublecheck |      |
                                                           | - push        | - accept >    |      |
```


### Init

- script: agentic_workflow init
- Call out to git to create clones of the master repo
- Default agents: planner, builder, reviewer. Can be modified in config.

Directory structure after:
    /home/auggie/projects/foo/              # project directory
        master/                             # repo for merging and human work
        bare/                               # bare git repo for push
        planner/                            # repo for planner agent
        builder/                            # repo for builder agent
        reviewer/                           # repo for reviewer agent

### Start

- script: agentic_workflow start
- Call out to tmux to open one window for each agent
    - In the tmux session, start a sandbox container for the agent (run or start, depending on container existence)
    - Pass in the model that the agent should be using (configurable per agent)
- Also open a tmux with a docker exec bash in the builder sandbox

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
Critique the idea.
Please be honest, even harsh if necessary.
Point out any flaws or weak points in the design.
Point out any decisions that have a high risk of becoming technical debt.
Propose additional features, to improve the effectiveness, robustness, and usability of the system.
"""

### @ Subdivide

- prompt: workflow subdivide {path_to_plan}
- agent: planner
- input plan can be in any state, but typically intent or planning
- Agent creates several new plans in state `planning`

### @ Plan

- prompt: workflow plan {path_to_plan}
- agent: planner
- Input plan should be `intent`
- Agent creates a new plan in state `planning` in its own repo.
- Planning (plan & subdivide) may benefit from being a skill

### Todo: Approve a plan

- script: agentic_workflow todo {path_to_plan}
- Plan must be in state `planning` in a branch on the `planner` repo.
- Push from `planner` to `bare`.
- Pull to `master`.
- Transition the plan to `todo`. Commit.
- Merge to `main` branch.
- Push from `master` to `bare`.
- Print workflow reminder: "next step is: agentic_workflow implement {new_path_to_plan}"

### Implement: Schedule a plan for implementation

- script: agentic_workflow implement {path_to_plan}
- Plan must be in state `planning` or `todo` on the `main` branch of the `master` repo.
- Transition the plan to `implementing`. Commit. Push.
- Print workflow reminder: "next step is: workflow ask {new_path_to_plan}". Pause briefly so it can be seen.
- tmux attach to the builder

### @ Build

- prompt: workflow build
- `workflow ask {path_to_plan}` should be visible in the session history. If not: start by asking.
- Pull.
- Plan must be in state `implementing`.
- Create a new branch `feat/{plan_name}`.
- When done, transition the state to `checking`.
- Make one or more commits (depending on the feature complexity). Do not push.

### Push

- script: agentic_workflow push {path_to_plan}
- Plan must be in state `checking` on a branch in the `builder` repo.
- Rerun the pre-commit-hook inside the sandbox.
- Run doublecheck
- Push the branch
- Fetch in `reviewer`.

### @ PR Review

- prompt: workflow pr {path_to_plan}
- May benefit from being a skill
"""
Derive `{plan_name}` from the workflow command.
Perform a thorough code review of the changes on the `feat/{plan_name}` branch.
Focus on aspects such as
software architecture,
logic errors,
missing or only partially implemented requirements,
fit with existing conventions in the codebase,
unit test quality and coverage,
security,
and any other relevant aspects.

Also consider integration-style tests:
are they worth the cost for this feature,
and is the codebase ready for them (or should they be postponed to a later phase).

Write a report on the results into `docs/plans/reviews/pr/{plan_name}.md`.
Commit your changes. Do not modify the code.
"""

### @ Fix

- prompt: workflow fix [{path_to_review}]
- Path to plan may be omitted, if the review is in the session history. In that case, fix what was most recently reviewed.

### Doublecheck

- script: agentic_workflow doublecheck {path_to_plan}
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

- script: agentic_workflow accept {path_to_plan}
- Push the branch from `reviewer`
- Pull to `master`
- Transitions plan into `done`
- Merge to main
- Push

### Reject

- script: agentic_workflow reject {path_to_plan}
- Transitions plan into `reject`

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


### Status

- script: agentic_workflow status
- Use the subdirectories that plans are in, together with the audit log, to show how far along plans are
- Omit done and rejected plans from the listing
- In addition to current sate, show next step(s) for each plan
- Print the lowest free number for plans (check existing plans across all states)


### Generate CLAUDE.md

- script: agentic_workflow claude.md
- Generate (the shorthand section of) the CLAUDE.md file based on the current configuration.

## Audit log

Both the script and the agent write logs (in jsonl format) of the executed workflow steps.
There is one log file per agent (e.g. /home/auggie/projects/foo/planner/workflow.log.jsonl),
plus one in master (/home/auggie/projects/foo/master/workflow.log.jsonl).
The logs should not be under version control.
Example row:
`{"actor": "agent", "step": "critique", "plan": "003_add_message_queue"}`


## Configurability

Hardcoding the workflow steps in code and CLAUDE.md is too inflexible: making any changes would be tricky.
The opposite extreme of making all steps fully defined in configuration is not optimal either,
as the configuration would become quite complex due to needing to represent multi-step scripts.
Let's aim for a sweet spot where
- the transition graph and some easily represented data is in a yaml config,
- scrips are hardcoded as python functions,
- the two are linked by a registry.

```yaml
intent:
    agent: planner
    commands:
        - name: ask
          command_type: prompt
          prompt: |
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
    - maybe in the future: if doing TDD, check that test and build only touch appropriate files
- MCP to allow agents to perform some selected git operations outside the sandbox, e.g. `agentic_workflow push`
