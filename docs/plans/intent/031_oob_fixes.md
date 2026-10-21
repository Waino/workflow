# 031: Issues encountered during first OOB use

## 1) New should print path

(Not limited to OOB)
`workflow new` should print the path to the newly created intent file.
The command opens the file in an editor.
However, when the editor is closed, it becomes tedious to reopen the file for further changes.

## 2) New should add frontmatter

In OOB mode, the plans are required to define the branches that should be worked on.
For tickets, `planner` creates suitable frontmatter based on instructions in the intent.
Nothing in the current intent template reminds to add this information.
The clearest and easiest way to do this may be for `workflow new` to add frontmatter to the intent template,
when in OOB mode.

Defaulting branches to `none` (skip) isn't user-friendly, as one then has to remember the non-skip format.
Let's default the first repo to `branch: feat/{{ plan.name }}` and `base: main`, and all others to `none`, 
under the convention that the first repo is the main package.

## 3) The branch rule is worded incorrectly for OOB

The planner directory isn't a git repository, so planner complains that the branch rule can not be applied.
The branch rule should be rephrased to clarify the usage in OOB mode.

OOB needs a map showing what the repos are, including the docs repo.
Mechanically add the map to AGENTS.md during generation.
The AGENTS.md hash already accounts for the contents of `.workflow.json`,
but the code for generating the map appears to be missing.

## 4) Check requires the pre-commit hook in docs

Docs generally don't strictly need a pre-commit hook.
There might be one e.g. for markdown linting, but this is not mandatory.
The docs repo should skip the pre-commit hook if none exists.

## 5) OOB hook pre-flight

With `run_hook=True`, the existence of the hook was never checked until it was actually called.
(I think I used `--continue` due to some other unmet condition that was checked only after running some steps).
We should make sure to validate, as a `workflow init` pre-flight check, that each repo either sets skip or has the hook.
There appears to exist such a check, but it either didn't trigger or was bypassed by `--continue`.

# 6) Next step didn't resolve

Next step gives a plan as `{number}_{slug}`.
This format failed to resolve an intent in an OOB project, even though only the number worked.
The `{number}_{slug}` format should resolve also in OOB mode.

```
$ wf ask 002
Plan resolved to: `planner/docs/plans/intent/002_foo_bar.md`
Sending to 'planner'
Next step: workflow critique 002_foo_bar

$ workflow critique 002_foo_bar --edit
No plan found for '002_foo_bar' in repos: [<WorktreeName.planner: 'planner'>]

$ workflow critique 002 --edit
# worked
```

## Decisions

1. One plan (`031_oob_fixes`) covers all items in scope.
2. §2: `new` renders the intent's own name into the frontmatter (`branch: feat/031_oob_fixes`); the generated
   AGENTS.md tells the planner to rewrite it to each ticket's name.
3. Amends §2: every code repo defaults to `{branch: feat/{intent name}, base: <default branch>}`, not only the first.
   Code repos are detected at `init` (alphabetical scan), so "first repo is the main package" would be arbitrary.
   Clearing unneeded repos to `null` is easier to remember than the non-skip format.
4. §2: "`none`" means YAML `null` (intent/023_out_of_band Decision 22); a literal `none` would parse as a string.
5. §2: `base` is each code repo's default branch as detected on `bare` (as `sync` does), not a hardcoded `main`.
6. §2: the intent frontmatter has no `docs` entry; `implement` writes that one.
7. §3: AGENTS.md (oob) gets a generic worktree map: worktree root is not a git repo, `AGENTS.md`, `.venv/`,
   `docs/` (plans, `body_of_agents.md`), and each code repo with whether its pre-commit hook runs.
   The planner branch rule is reworded for oob: it applies inside `docs/`; the planner never branches code repos.
8. §4: the docs repo runs the pre-commit hook iff `docs/.git-pre-commit-hook` exists; no `.workflow.json` change,
   matching how oob `validate` already treats hooks.
9. §5: `init --oob` and `add-repo` pre-flight require `.git-pre-commit-hook` committed at HEAD in every code repo
   with `run_hook: true`. Additionally, `doctor` flags existing violations (recovery: set `run_hook: false` in
   `.workflow.json`), and `check`/`accept` fail early in their preconditions, before any step runs.
10. §6 is postponed and out of scope: the failure could not be reproduced (see
    `docs/reports/research/031_oob_name_resolution.md`); the operator will run the diagnostics there later.
