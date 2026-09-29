# 027: Various Fixes

## 1) Bug: Explicit agent not respected

I ran the command `workflow rebase builder/docs/plans/implementing/023.1`.
The command was sent to `planner`, even though `builder` is expliclity named in the command.
This should never happen: an explitictly named agent must always be respected.

The `builder` agent doesn't have `rebase`, as typically it is either `planner` or `reviewer` that needs rebasing.
However, the decision to allow prompt commands to be sent to any agent by
explicitly naming the agent exists so that prompt commands don't need to be
defensively added to all agents for the rare cases where they are needed.

### 1.1) Make unexpected recipients easier to spot

Whenever sending a command blind (not the active tmux pane), there should be a loud warning
"would send to 'planner' [red](Not the active agent in tmux!)[/red]:"

### 1.2) Rebase specifically

`rebase` should be usable in all agents, preferably without needing to specify the agent explicitly.
Maybe `rebase` should be copied to `builder` as well.

## 2) Plan paths in prompts

Prompts unnecessarily include the agent in the plan path, e.g. "Read the plan at builder/docs/plans/implementing/022_user_docs.md."
The path is not relative to the agent work dir.
This doesn't appear to cause any confusion, but it's better to fix it.

## 3) Improvement: Next step in status

The next step column in `workflow status` is almost always empty.
It is quite hard to reason about the next step afterwards, based only on what is currently stored in the audit log.
Let's avoid the need to reason:
- each command showing a next step should record the text that was displayed in the audit log (a new next_step field)
- `status` can reshow the exact same text

Remove any now obsolete functions introduced only to support the old way of deducing next steps.

## 4) Bug: AGENTS.md dryrun

`workflow agents_md` does not respect `--dry-run`.

## 5) Resume plan

The resume plan is printed twice in `--dry-run`. Once is enough.

### 5.1) Improved plan naming

The naming of the plan is confusing when not resuming.
Let's have `--dry-run` print "Execution plan" if not resuming and "Resume plan" if resuming.

### 5.2) Improved command listing

`--dry-run` doesn't say which worktree is targetted (e.g says just "would run: git pull").
Add the worktree, e.g. "would run in reviewer: git pull".

## 6) Bug: Incorrect stack anomalies

Stack anomalies are incorrectly shown for plans that have been accepted.

```
Stack anomalies:
    - 023.2_layout_and_vocabulary: onto='023.1_project_metadata_file' does not resolve to a plan
    - 023.3_bare_remote_rename: onto='023.2_layout_and_vocabulary' does not resolve to a plan
    - 023.4_init_oob_and_bootstrap: onto='023.3_bare_remote_rename' does not resolve to a plan
```

There are no stale plans in non-done states:
```
find -iname "023.2_layout_and_vocabulary*"
./planner/docs/plans/done/023.2_layout_and_vocabulary.md
./builder/docs/plans/done/023.2_layout_and_vocabulary.md
./reviewer/docs/plans/done/023.2_layout_and_vocabulary.md
./canon/docs/plans/done/023.2_layout_and_vocabulary.md
```

`workflow status --all` also shows the plans as `done`


## 7) Trim stack column

`workflow status` easily becomes too wide due to `stack` column:
let's only show the sequence number in `stack`, omitting the slug.

## 8) Test refactoring

Check for repetitive tests that could be made DRY using `pytest.mark.parametrize`.

## Sequencing

Either everything in one plan, or break out a few larger tasks into separate subplans and group the rest.
