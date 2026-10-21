# 031 §6: `{number}_{slug}` fails to resolve in oob, bare number works

Status: **root cause NOT reproduced.** The clean-state oob flow resolves both forms. Below: what was ruled out,
the only slug-dependent code paths (the suspects), and a probe to run against the real project.

## Layout facts (workflow/layout.py)

- Oob: git repo is `<worktree>/docs`; plans tree is `<worktree>/docs/plans` (`plans_root`, layout.py:50-57).
  Inside the repo that is `plans/` (`plans_prefix`, layout.py:87-96). So the printed
  `planner/docs/plans/intent/002_foo_bar.md` is correct, and nothing is missing from it.
- Filesystem source uses `plans_root` (plan_resolver.py:139 `find_all_plans_in_worktree`); git source uses
  `git_repo_root` + `plans_prefix` (plan_resolver.py:716-719 `_from_git_history`, :671-682 `_git_newest_touch`).
  Both are consistent for oob.

## Fuzzy-path parsing ruled out

`_extract_components` (plan_resolver.py:401-433): `002_foo_bar` is not a `WorktreeName`, not `docs/plans`, not a state;
it goes to `parse_plan_filename` -> `seq=[2], slug="foo_bar"`. Probe output: `hints seq=[2] slug='foo_bar'`,
constraint repos/states `None`. Identical to bare `002` except `slug`.

## Reproduction attempts (probes in /tmp/workflow/031/test_probe.py, test_e2e.py; run with
`cd planner && PYTHONPATH=. uv run pytest /tmp/workflow/031/<file> -s -p no:cacheprovider`)

Real oob project via `tests/test_init_oob.py::_make_oob_project` + `_run_init`, then CLI: `new foo_bar`, commit in
canon/docs, `push 001_foo_bar`, `--dry-run critique 001`, `--dry-run critique 001_foo_bar [--edit]`:
all resolve to `planner/docs/plans/intent/001_foo_bar.md`. Also resolved with the intent (a) committed in canon and
planner, (b) only untracked in both. Git source: `states={INTENT}` for the slug form, silent/INTENT for bare. No failure.

Slug-dependent code is only:
1. `_git_newest_touch` glob (plan_resolver.py:671-674): bare -> `plans/*/002_*.md` **and**
   `plans/reviews/*/002_*.md`; slug -> `plans/*/002_foo_bar.md` and `plans/reviews/*/002_foo_bar.md`
   (the latter never matches review files, which carry a `_<timestamp>`/`_<hash>` suffix).
   With `--max-count=1` the bare form can have its newest commit be a **review** (or another plan with that number),
   which `_parse_path_to_state` maps to `None` (:640-641) so the git constraint goes *silent*. The slug form sees only
   the plan file's own newest commit and returns a hard `states={X}` constraint
   (`_from_git_history` :720-733, intersected at :937-938).
2. `_filesystem_matches` slug equality (:826-827).

Therefore the failure needs: newest git touch of `002_foo_bar.md` (across all `--all` refs of every worktree's docs
repo, incl. remote-tracking refs and other branches) says a state other than the one on disk in `planner/`
(e.g. `planning`/`done`, from a branch or from canon that planner has not pulled/merged),
while bare `002` is shielded by a newer review commit or never sees it. Result: `states` excludes `intent`
-> `_filesystem_matches` returns nothing -> `PlanNotFoundError(.., [planner])` (:940-942), repos shown
= `sorted(effective_repos)` = `{planner}` from the `critique` prompt's agent (:236-240). That matches the message.
I could not confirm this is what happened in the user's project; it is the only divergence the code allows.

Side observation (not the bug): in a fully synced oob project, `ask 001` is `AmbiguousPlanError` between
`builder/docs/...` and `planner/docs/...` (same intent in both clones), whereas the user's `ask 002` resolved.

## To confirm on the real project (read-only)

```python
from workflow.config import load_config
from workflow.plan_resolver import _from_git_history, _from_fuzzy_path, _from_audit_log
cfg = load_config(<config.yaml>, project_dir=<project>); lay = cfg.layout()
for ref in ("002", "002_foo_bar"):
    h = _from_fuzzy_path(ref); print(ref, _from_git_history(lay, h.seq, h.slug))
```
and `git -C <project>/planner/docs log --all --no-merges -3 --name-status -- ':(glob)plans/*/002_foo_bar.md'`
(and the same in canon/docs). If the slug form returns a state != `intent`, hypothesis 1 is confirmed;
if it returns `intent`, look at `workflow.log.jsonl` / tmux (`_from_tmux`, :796) instead.

## Suggested fix (if hypothesis confirmed)

Make git history consistent between both forms in `_git_newest_touch` / `_from_git_history`
(plan_resolver.py:671-733): drop the `reviews/*/` pathspec (reviews never carry plan state; they only silence the
bare form), and/or make git a soft hint -- e.g. if the git state has no matching file in the searched repos fall back
to filesystem match instead of intersecting to empty. Smallest change: remove the second pathspec so slug and bare
queries see the same plan-file touches.

## Regression test

`tests/test_plan_resolver.py`: extend `TestGitProvidesRepoFlag` (parametrized on `in_band`/`oob`, :905-960) with a case
committing `plans/intent/002_foo_bar.md` then a newer review commit, asserting `_from_git_history(layout, [2], None)`
equals `_from_git_history(layout, [2], "foo_bar")`; plus a `resolve_plan` oob test via `_make_config` with
`make_project(mode=oob)` (see `tests/test_plan_resolver.py:82`), `command_name="critique"`, both reference forms.
`tests/test_info_oob.py` / `test_oob_handoff.py` have no resolver coverage.
