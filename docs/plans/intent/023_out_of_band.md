# 023: Out-of-band mode for plan version control

While keeping specs together with the source code is a desirable property for my own projects,
it is not always acceptable when collaborating.
Also, automatically added plan transition commits mixed with code changes can be considered noisy.

To support contributions to arbitrary projects,
we need a usage mode where plans are kept out-of-band, i.e. in a separate repository from the source code.

The mode should be selected during `workflow init` (flag `--oob`) and remembered for all subsequent commands.
There is another upcoming feature that requires persistent project-level metadata,
so the method for storing it should not be limited to a single value.
Let's use a single `.workflow.json` in the project root, with a locking mechanism for writes.
It can replace the existing `.workflow_root` marker as the indicator for the project root.


## Multiple code repos

For some projects, it is necessary to
- perform changes in lockstep, adding functionality or fixing bugs in a dependency in order to enable a change,
- or at least check details of and test against a feature branch of a dependency.
The current monorepo assumption doesn't fit this kind of work.


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

Note the following:
- Multiple git repos
    - `docs` is a separate git repo, instead of being a part of the package repo.
    - There are multiple code repos (`mypackage`, `dep1`, `dep2`).
    - `bare` and each agent become wrapper directories, having a copy of all repos.
- The main package moves deeper
  (e.g. from `project_root/canon/pyproject.toml` to `project_root/canon/mypackage/pyproject.toml`)
- The path to plans is not changed, e.g. `project_root/canon/docs/plans/intent/001_idea.md`


## Git branch model

The `workflow` tool only manages the branch lifecycle of `docs`.
The agents manage most of the git state of the code repos without script automation.
Plans must include a section detailing which branch of each code repo should be worked on,
and on which commit they should be based.

Pushing is still prohibited for the agents.
Planner should not add commits to the code repos.
The `reject` transition only affects plans: the code is intentionally left unmerged.
Thus the only transitions that push multiple repos are `check` and `accept`.
Remaining transitions (`todo`, `implement`, `reject`) only push docs.

All repos should be refreshed with fetch.
This is particulary important, as the agent forgetting to fetch could cause it to start work from stale code.

## Agent interaction

Planner needs copies of all repos for exploration.
Typically planner will check out branches in the code repos, but will not modify files or commit anything.
Planner writes and commits the plan document in the `docs` repo, which is pushed by `workflow`.

When builder receives the plan, it will check out (possibly creating) the branch(es) in the code repo(s).
It will then make changes in one or more of the repos, and commit each.
`check` pushes all repos to `bare`.

Reviewer receives the plan, pulls the changes, and writes its review in `docs`.
The review either gets handed to builder, or then the reviewer directly makes code changes, and commits.
`accept` pushes all repos to `bare`.
On acceptance, `docs` are automatically merged to main, but code branches are not:
typically the next step is pushing the branch upstream and opening a PR there.


## run_agent_in_docker.sh

The `run_agent_in_docker.sh` script will probably not need any major changes.
It already mounts the agent's subdirectory (called `repo`, but in oob mode a parent directory containing the repos),
and the entire `bare`.


## Risks

This is a major change, and a change of direction from the original intent, which was to keep plans as documentation in-repo.
As the change affects the expected shape of the project directories, and the number of involved repos,
it requires a thorough and comprehensive analysis during planning.


## Open questions

Should the in-band structure be modified as well, to minimize differences between the two modes?
This would mean adding redundant subdirs to bare:
- Moving the bare git repo from `project_root/bare` to `project_root/mypackage/bare`
But perhaps not moving the main package deeper, as that would change the path to plans.
