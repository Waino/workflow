# 026: Stacking Bugs

While on `feat/023.1_project_metadata_file`, I ran `workflow implement 023.2 --onto`
It printed the following plan:
```
Resume plan for `implement --onto`:
  [skip] transfer planner branch to main    plan already past planning
  [skip] transition to todo    plan already past planning
  [skip] rebase planner branch onto main    transfer already done or not needed
  [skip] checkout stack branch in builder    builder already on the branch and up to date
  [run]  write onto frontmatter
  [run]  transition to implementing
  [run]  push canon
  [skip] merge origin/main into stack branch    origin/main already merged
```

Then `workflow --dry-run ask 023.2`
errors with `No plan found for '023.2' in repos: [<RepoName.builder: 'builder'>]`.
The transition commit is on main, but not on `feat/023.1_project_metadata_file`.
The merge origin/main into stack branch was incorrectly skipped.

After manually merging `git merge main`, `workflow --dry-run ask 023.2` is still refusing with a different error:
```
023.2_layout_and_vocabulary's onto='023.1_project_metadata_file' does not resolve to a plan: Ambiguous reference
'023.1_project_metadata_file': 2 candidates: [Plan(sequence_number=[23, 1], slug='project_metadata_file',
state=<PlanState.IMPLEMENTING: 'implementing'>, path=PosixPath('canon/docs/plans/implementing/023.1_project_metadata_file.     md')),
Plan(sequence_number=[23, 1], slug='project_metadata_file', state=<PlanState.IMPLEMENTING: 'implementing'>,
path=PosixPath('builder/docs/plans/implementing/023.1_project_metadata_file.md'))]
```
There doesn't seem to be any repo bias in this plan resolution, even though `ask` cannot target `canon`.
