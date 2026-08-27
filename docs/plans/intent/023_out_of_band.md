# 023: Out-of-band mode for plan version control

While keeping specs together with the source code is a desirable property for my own projects,
it is not always acceptable when collaborating.
Also, automatically added plan transition commits can be considered noisy.

To support contributions to arbitrary projects,
we need a usage mode where plans are kept out-of-band, i.e. in a separate repository from the source code.

The mode should be selected during `workflow init` (flag `--oob`) and remembered for all subsequent commands.

## Example

Let's consider a project called `mypackage`. Here is a subset of files in the project directory:

Normal (in-band) mode.
```
+ bare/
| + HEAD
+ builder/
| + (same items as canon)
+ canon/
| + AGENTS.md
| + docs/
| | + plans/
| |   + intent/
| |     + 001_idea.md
| + .git/
| + pyproject.toml
| + mypackage/
| | + mypackage.py
| + .venv/
+ env.list
+ planner/
| + (same items as canon)
+ reviewer/
| + (same items as canon)
+ run_agent_in_docker.sh
+ workflow.log.jsonl
```

Out-of-band mode.
In addition to `mypackage`, the project includes the source of `dep1` and `dep2`.
```
+ bare/
| + docs/
|   + HEAD
| + mypackage/
|   + HEAD
| + dep1/
|   + HEAD
| + dep2/
|   + HEAD
+ builder/
| + (same items as canon)
+ canon/
| + AGENTS.md
| + docs/
| | + .git/
| | + plans/
| |   + intent/
| |     + 001_idea.md
| + mypackage/
| | + .git/
| | + pyproject.toml
| | + mypackage/
| |   + mypackage.py
| + dep1/
| | + .git/
| | + pyproject.toml
| | + dep1/
| |   + dep1.py
| + dep2/
| | + .git/
| | + pyproject.toml
| | + dep2/
| |   + dep2.py
| + .venv/
+ env.list
+ planner/
| + (same items as canon)
+ reviewer/
| + (same items as canon)
+ run_agent_in_docker.sh
+ workflow.log.jsonl
```

Note the following differences:
- Multiple git repos
    - `docs` is a separate git repo, instead of being a part of the package repo.
    - There are multiple code repos (`mypackage`, `dep1`, `dep2`).
    - `bare` and each agent has a copy of all repos.


## Branch model

The `workflow` tool only manages the branch lifecycle of `docs`.
The agents manage the git state of the code repos without script automation.
Plans must include a section detailing which branch of each code repo should be worked on,
and on which commit they should be based.


## Agent interaction

Planner needs copies of all repos for exploration.
Typically planner will check out branches in the code repos, but will not modify files or commit anything.
Planner writes and commits the plan document in the `docs` repo, which is pushed by `workflow`.

When builder receives the plan, it will check out (possibly creating) the branch(es) in the code repo(s).
It will then make changes in one or more of the repos, commit, and push to `bare`.
(We might like a script helper to push all repos, if leaving it to the agent works poorly.)

Reviewer receives the plan, pulls the changes, and writes its review in `docs`.
The review either gets handed to builder, or then the reviewer directly makes code changes, commits, and pushes.


## Multiple code repos

For some projects, it is necessary to
- perform changes in lockstep, adding functionality or fixing bugs in a dependency in order to enable a change,
- or at least check details of and test against a feature branch of a dependency.
The current monorepo assumption doesn't fit this kind of work.
