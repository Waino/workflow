# Index of decisions

Last indexing pass: `1c5aabf` (the `main` commit the indexing branch was cut from).
Last followthrough pass: (none)

Maintained by the `index_decisions` prompt; `followthrough` prunes completed deferred work.
Obsolete detection is best-effort: a `current` entry may describe code that has since been removed.

Identifiers: `intent/{number}_{slug}`, `{number}_{slug}` (plans, no state), `reviews/{type}/{filename}`.
`D{n}` is the decision number in that file. Legacy files (written before the decision conventions) are not
edited: their `D{n}` counts explicit decision items in file order across every decision-like list
(Decisions, Design decisions, Clarified decisions, Clarifications, PR-/post-review decisions) and
inline `Decision:` sentences, and each carries a locator (`§heading: "quote"`).
`D–` marks an implicit legacy design choice with no decision marker; it is indexed by locator only.
A file's own labels (e.g. 014's `D6`, 019's `D1`) appear in the locator, not in the number.

Statuses: decisions `current` / `intended` / `superseded` (→ superseding decision) / `obsolete`;
deferred work `open` / `dropped`; terms `current` / `superseded`.

## Plan summaries

- `intent/001_idea` — Project idea: three sandboxed agents plus a human operator, the `workflow` CLI, the plan
  state machine, git branch strategy, config-driven prompts.
  Related: (global; 002–008 derived from it)
- `002_models_and_config` — Pydantic models (Plan, PlanState, command union, audit entry) and YAML config loading.
  Related: reviews/pr/002_models_and_config_0e6baa9
- `003_path_resolution` — Loose fuzzy-path plan resolution, `Review` subclass, sequence-number collision detection.
  Related: reviews/pr/003_path_resolution_0f57880
- `004_cli_framework` — Click CLI with dry-run, script registry, `run_command`, prompt dispatch to tmux, JSONL
  audit log; review fixes (config-aware group, project-relative `Plan.path`, `plan`/`branch`/`commit` context).
  Related: 004.1_cli_and_registry, 004.2_prompt_dispatch, 004.3_audit_logging, 004.4_review_fixes,
  reviews/pr/004_cli_framework_1e84d3a
- `005_plan_state_and_git` — `transition_plan` via `git mv`, git helpers, `transfer_branch_to_main`, the
  doublecheck (later `validate`) suite; review fixes.
  Related: 005.1_plan_state_transitions_and_git_helpers, 005.2_doublecheck, 005.3_review_fixes,
  reviews/pr/005_plan_state_and_git_3b56c73
- `006_info_commands` — `new`, `status`, `log`, `cheat`.
  Related: reviews/pr/006_info_commands_29e01ca
- `007_workflow_commands` — `init`, `start`, `todo`, `implement`, `push` (later `check`), `accept`, `reject`.
  Related: reviews/pr/007_workflow_commands_29e01ca
- `008_claude_md_generation` — Generated workflow section of AGENTS.md between markers, plus the trusted
  `.agents_md_hash`.
  Related: reviews/pr/008_claude_md_generation_7e15d67
- `intent/010_pipeline_parallelism` — Timeline analysis of three dependent tickets: the human is the bottleneck;
  two parallel tickets are practical.
  Related: (analysis only, no plan)
- `intent/011_tmux_pane_rotation` — Verified libtmux proof-of-concept for control-pane rotation by recorded pane id.
  Related: 019_tmux_implementation_gaps
- `012_plan_resolution_respec` — Rename doublecheck→validate, split handoff `check` from content `push`, merged
  multi-source plan resolver (path, subcommand, git history, audit log, tmux).
  Related: intent/012_plan_resolution_respec, reviews/pr/012_plan_resolution_respec_29e01ca
- `intent/013_example_walkthrough` — Walkthrough of a project's first handoffs; pins the expected git and tmux
  behaviour of each command.
  Related: 019_tmux_implementation_gaps
- `014_fixes_to_006_007_012` — Fixes from the 006/007/012 reviews: `also_valid_in_states`, `pull_target`, main
  guards, config-derived audit mapping, git source gives state only, `check` owns implementing→checking, first
  end-to-end integration test.
  Related: reviews/pr/006_info_commands_29e01ca, reviews/pr/007_workflow_commands_29e01ca,
  reviews/pr/012_plan_resolution_respec_29e01ca
- `015_wide_resolver_test` — One parametrized resolver test over a wide (plan, command) matrix with
  sequence-number-only inputs.
- `016_soft_resolver_constraints` — Prompt-subcommand and audit repo hints are soft and dropped when the path
  names a repo.
- `017_docker_script` — `start` launches agents through a per-project `run_agent_in_docker.sh`; `--reset`;
  example script in docs.
  Related: intent/017_docker_script
- `intent/018_prompts` — Checklist for the `full review` prompt.
  Related: (no plan; folded into the default config prompt)
- `019_tmux_implementation_gaps` — Docked control pane, `go` rotation, `implement` creates the builder feature
  branch and rotates, `check` rotates to reviewer.
  Related: intent/011_tmux_pane_rotation, intent/013_example_walkthrough, reviews/followthrough/bf55b86
- `020_robustness` — Preconditions and idempotent resume (`ResumePlan`, `--continue`, resume preview) for the
  script commands; new `doctor`.
  Related: intent/020_robustness, 020.1_more_robustness, reviews/pr/020_robustness_8582f69
- `021_run_agent_in_docker_fixes` — Docker script takes a mandatory subcommand (`agent`/`venv`/`shell`/`hook`);
  hooks run in throwaway containers; canon venv and `canon_pre_commit`.
  Related: intent/017_docker_script, reviews/pr/021_run_agent_in_docker_fixes_ae03329
- `022_user_docs` — User documentation in `docs/source`.
- `intent/023_out_of_band` — Out-of-band mode: plans in a separate `docs` repo, multiple code repos,
  `.workflow.json`, layout/worktree vocabulary, `bare` remote, `init --oob`/`add-repo`, multi-repo handoffs and
  `sync`, per-repo validate/doctor/status.
  Related: 023.1_project_metadata_file, 023.2_layout_and_vocabulary, 023.3_bare_remote_rename,
  023.4_init_oob_and_bootstrap, 023.5_multi_repo_git_orchestration, 023.6_validate_doctor_and_reporting,
  reviews/pr/023.4_init_oob_and_bootstrap_20e6a4e, reviews/pr/023.6_validate_doctor_and_reporting_b21c7ca
- `024_avoiding_failed_merges` — No stale `main` after handoffs, rebase fallback in `transfer_branch_to_main`,
  `rebase` prompt, read-only `bare` mount, branch deletion on accept, `reject` never merges.
  Related: intent/024_avoiding_failed_merges, reviews/pr/024_avoiding_failed_merges_b929f03
- `025_stacked_plans` — Several plans share one feature branch (`implement --onto`), `onto` frontmatter,
  stack cascades, mid-stack guards, stack-aware prompts.
  Related: intent/025_stacked_plans, reviews/pr/025_stacked_plans_5a880d0
- `026_stacking_bugs` — Always merge `origin/main` into the stack branch after the canon push; exact-name stack
  reference lookup.
  Related: intent/026_stacking_bugs, reviews/pr/026_stacking_bugs_447250e
- `intent/027_various_fixes` — Prompt routing follows the plan's worktree (borrowing, blind-send warning,
  worktree-relative paths, builder `rebase`); next step recorded in the audit log; dry-run fixes; dotted-number
  parsing; conservative test parametrization.
  Related: 027.1_prompt_dispatch, 027.2_small_fixes, 027.3_test_parametrization,
  reviews/pr/027.3_test_parametrization_c58b06b
- `intent/028_index_of_decisions` — This index: read-first instructions and decision conventions in the generated
  AGENTS.md, `index_decisions` prompt, `followthrough` prunes deferred work.
  Related: reviews/pr/028_index_of_decisions_fed139d
- `reviews/followthrough/bf55b86` — Follow-through of intents and done plans (gaps later closed by 019 and others).

## Decisions

### Process and conventions

- current `intent/023_out_of_band` D– — Breaking changes ship with a single-use migration script plus a `doctor`
  check, never a compatibility shim. §Second round decisions: "prefer single-use migration scripts"
- current `023.2_layout_and_vocabulary` D– — Renamed config keys get no pydantic alias; a validator names the
  migration script instead. §Vocabulary rename: "Do **not** accept the old key"
- current `022_user_docs` D– — The tool encodes one workflow; changing it beyond config means forking.
  §Configurable vs requiring code changes: "fork the repo"
- current `022_user_docs` D– — The three-agent limit is deliberate. §Caveats: "limited on purpose"
- current `intent/027_various_fixes` D23 — Test parametrization is conservative: no coverage drop, no assertion
  dropped without an equivalent. §8: "Decision: conservative"
- current `014_fixes_to_006_007_012` D21 — The sandbox git rejects `--no-verify`; integration tests swap in a
  plain commit. §Phase 4d Decisions: "D7: The integration test bypasses `--no-verify`"
- current `intent/028_index_of_decisions` D3 — The index records the commit of the last pass, not timestamps.
- current `intent/028_index_of_decisions` D4 — Indexed: done plans, all intents, reviews; excluded: in-flight and
  rejected plans and their reviews.
- current `intent/028_index_of_decisions` D7 — The reviewer owns `index_decisions`; its branch is merged manually.
- current `intent/028_index_of_decisions` D8 — Read-first instructions and decision conventions live in the
  generated AGENTS.md section, phrased by activity.
- current `intent/028_index_of_decisions` D12 — Legacy decisions carry a locator and resolve only via this index.
- current `intent/028_index_of_decisions` D13 — Decisions lists are append-only; withdrawn entries stay, marked.
- current `intent/028_index_of_decisions` D14 — The indexer only adds deferred work; `followthrough` prunes it.
- current `intent/028_index_of_decisions` D18 — Every resolved option gets a Decisions entry; the "would have"
  summary is rationale only.
- current `intent/028_index_of_decisions` D21 — The recorded hash is the `main` commit the indexing branch was
  cut from.
- current `intent/028_index_of_decisions` D23 — How and when to record decisions goes into the harness system
  prompts (manual edit).

### Plan model and naming

- current `002_models_and_config` D– — Sequence numbers are int lists; the string form zero-pads only the first
  component (`012.1`). §Plan model: "sequence_number: list[int]"
- current `003_path_resolution` D– — Reviews are a `Plan` subclass carrying the review-only fields.
  §Review: "Add `Review` as a subclass of `Plan`"
- current `004.4_review_fixes` D– — `Plan.path` is relative to the project dir, so prompts are portable.
  §3: "from absolute path to project-relative path"
- current `intent/012_plan_resolution_respec` D10 — An intent may share its number with a ticket; two tickets with
  one number are an error. §Decisions: "Collision rule for same sequence number"
- current `014_fixes_to_006_007_012` D8 — Slugs match `[a-z0-9]+(_[a-z0-9]+)*`.
  §Decisions: "Slug validation for `new`"
- current `intent/027_various_fixes` D12 — Prompts use worktree-relative plan paths (`plan.path_in_worktree`).
  §2: "Decision: add a `path_in_worktree` helper"

### Plan resolution

- superseded (→ intent/012 D1) `003_path_resolution` D– — Single-repo resolver with `expected_states`.
  §Resolution algorithm: "always searches exactly one repo"
- superseded (→ intent/012 D1) `004.4_review_fixes` D– — Per-repo candidate loop for prompt resolution.
  §5: "maps prompt commands to candidate workflow targets"
- superseded (→ intent/012 D1) `005.3_review_fixes` D– — Per-command repo loop for doublecheck.
  §1: "_resolve_doublecheck_plan"
- current `003_path_resolution` D– — Cross-repo dedup keeps the most progressed state; rejected outranks all;
  reviews are never deduplicated. §Resolution algorithm: "`rejected` ranks highest"
- current `intent/012_plan_resolution_respec` D1 — One merged resolver entry point across all repos.
  §Decisions: "merged resolver that works across all repos"
- current `intent/012_plan_resolution_respec` D3 — Sources intersect per dimension (repos, states) independently;
  a silent source adds no constraint. §Decisions: "intersection of repo-sets AND"
- current `intent/012_plan_resolution_respec` D4 — Tmux contributes `{active agent, canon}`.
  §Decisions: "tmux's repo constraint is"
- current `intent/012_plan_resolution_respec` D5 — An explicit repo hint beats tmux; the resolution target is also
  the dispatch target. §Decisions: "always wins"
- current `intent/012_plan_resolution_respec` D6 — Strict-state script commands check the state after resolving.
  §Decisions: "`expected_states` / `also_valid_in_states` is removed"
- current `intent/012_plan_resolution_respec` D7 — The audit log is a resolver source.
  §Decisions: "consults the audit log"
- current `intent/012_plan_resolution_respec` D8 — Reviews resolve separately, detected by the `reviews/` prefix.
  §Decisions: "Reviews are resolved by a separate function"
- current `intent/012_plan_resolution_respec` D9 — An empty intersection prints the union with sources and exits
  non-zero. §Decisions: "prints the union with source attributions"
- current `intent/012_plan_resolution_respec` D11 — Git history across all refs replaces the working tree as a
  source. §Decisions: "Filesystem is intentionally replaced by git history"
- current `intent/012_plan_resolution_respec` D12 — A path fixing repo, state and number skips git.
  §Decisions: "we skip git entirely"
- current `intent/012_plan_resolution_respec` D– — A ticket's planner branch is found from git
  (`for-each-ref --contains`), not from its name. §Resolving the branch for a ticket
- current `012_plan_resolution_respec` D– — Tmux is optional; its absence makes the source silent.
  §The tmux source: "Must not raise on tmux absence"
- current `014_fixes_to_006_007_012` D1 — Extra valid states for a script live in config (`also_valid_in_states`),
  not in resolver code. §Decisions: "`also_valid_in_states` on `ScriptCommand`"
- superseded (→ 014 D17) `014_fixes_to_006_007_012` D4 — Hand-coded `check` audit mapping.
  §Decisions: "`check` audit mapping"
- current `014_fixes_to_006_007_012` D15 — Git history gives only the state, except for `push`, which needs the
  clone with unpushed edits. §Phase 4a–c Decisions: "D1: Git contributes state only"
- current `014_fixes_to_006_007_012` D17 — The audit command→(repo, state) mapping is derived from config
  transitions. §Phase 4a–c Decisions: "D3: Derive `AUDIT_COMMAND_REPO_STATE` from the config"
- current `014_fixes_to_006_007_012` D18 — Non-transition commands give no audit constraint.
  §Phase 4a–c Decisions: "D4: Keep behaviour for absent / non-transition commands"
- current `014_fixes_to_006_007_012` D20 — The audited state is a lower bound; later states are allowed (safety
  net only). §Phase 4d Decisions: "D6: Audit log contributes forward-progression states"
- current `014_fixes_to_006_007_012` D23 — Same-second git ties go to the more progressed state.
  §Phase 4d Decisions: "D9: Break `_from_git_history` timestamp ties"
- current `016_soft_resolver_constraints` D– — Path, script subcommand, git (push) and tmux are hard repo
  constraints; prompt subcommand and audit are soft, dropped when the path names a repo.
  §Root-cause classification
- current `016_soft_resolver_constraints` D– — Tmux stays hard; an unwatched agent needs a full path.
  §Risks: "Tmux: stays hard as requested"
- current `026_stacking_bugs` D2 — Stack references use an exact-name lookup (referring repo, then canon), not the
  fuzzy resolver. §Decisions: "Repo-scoped exact-name lookup"
- current `intent/027_various_fixes` D21 — `onto` is an exact name: `doctor` also uses `find_stack_member`.
  §6: "Decision: fix both name parsers"

### Prompt dispatch and CLI

- current `004.1_cli_and_registry` D– — Unknown script names fail at config load.
  §Script registry: "wired into the existing `WorkflowConfig.validate_script_references`"
- current `004.2_prompt_dispatch` D– — Multi-word command names become kebab-case CLI names.
  §Dynamic command registration: "exposed as kebab-case"
- current `004.4_review_fixes` D1 — A config-aware custom Click group loads config before subcommand lookup.
  §1: "Use Option A"
- current `004.4_review_fixes` D– — Prompt templates get `plan`, `branch`, `commit`.
  §4: "Stop passing `path_to_plan`"
- current `004.4_review_fixes` D– — Dry-run renders placeholders for prompt branch/commit instead of running git
  (narrowed by intent/020 D9 for resume guards). §6: "do **not** broaden dry-run"
- current `012_plan_resolution_respec` D– — The builder→reviewer handoff is `check`; `push` is a generic content
  push without transition. §Phase 2: "`push` / `check` split"
- current `012_plan_resolution_respec` D– — `repo_override` (now `worktree_override`) is a soft resolver
  constraint. §`repo_override` configuration field: "soft constraint"
- current `intent/027_various_fixes` D2 — No silent fallback to another agent's command definition.
  §1.3 Decisions: "The silent fallback to the first target"
- current `intent/027_various_fixes` D3 — A prompt goes to the agent owning the resolved worktree; definition from
  the plan's state, then the agent's other states, else borrowed; canon-resolved plans are an error.
  §1.3 Decisions: "Recipient rule"
- current `intent/027_various_fixes` D4 — Borrowing another agent's prompt needs `--edit`; transition prompts are
  never borrowed. §1.3 Decisions: "Borrowing requires `--edit`"
- current `intent/027_various_fixes` D5 — An agent needing another's prompt gets a plain second definition, not
  `also_valid_in_states`. §1.3 Decisions: "`also_valid_in_states` is not reused"
- current `intent/027_various_fixes` D6 — Every dispatch names its plan and recipient.
  §1.3 Decisions: "Every dispatch prints"
- current `intent/027_various_fixes` D7 — Sends to a non-active window warn loudly and wait an extra delay; no
  confirmation dialog. §1.3 Decisions: "Blind-send check"
- current `intent/027_various_fixes` D8 — Dispatch never rotates the control pane.
  §1.3 Decisions: "A blind send does not rotate"
- current `intent/027_various_fixes` D9 — Builder gets `rebase` via a YAML anchor; no config migration step.
  §1.3 Decisions: "`rebase` is added to the `implementing` state"
- current `027.1_prompt_dispatch` D3 — Routing assumes agent names equal worktree names.
  §Clarifications: "agent names equal worktree names"

### Handoffs and git sync

- current `007_workflow_commands` D– — Transition commits use `--no-verify` so a red tree cannot block them;
  `check` reruns the hook instead. §Note on git_commit and no_verify
- current `007_workflow_commands` D– — Partial failures are left to the human with clear errors, not rolled back.
  §Risks: "lean toward the latter"
- current `intent/013_example_walkthrough` D– — Handoffs only fetch into an agent that may be mid-task.
  §`workflow todo 004`: "Todo should fetch, not pull"
- current `014_fixes_to_006_007_012` D2 — `push` also pulls into `pull_target`.
  §Decisions: "`pull_target` on `ScriptCommand`"
- current `014_fixes_to_006_007_012` D3 — Agent repos must not push `main`; canon may.
  §Decisions: "`push` main-branch guard"
- current `014_fixes_to_006_007_012` D24 — Transitions are done by scripts, never agents, so the audit log stays
  complete (`check` owns implementing→checking). §Phase E Decision: "E1"
- current `019_tmux_implementation_gaps` D5 — `implement` creates `feat/{plan}` in the builder from fresh `main`
  (clean tree, no existing branch); prompts never create it. §D5
- current `intent/024_avoiding_failed_merges` D1 — `transition_plan` finds the file by name in the repo it
  operates on. §Decisions 1
- current `intent/024_avoiding_failed_merges` D2 — `reject` transitions on canon `main` only and never merges the
  feature branch. §Decisions 2
- current `intent/024_avoiding_failed_merges` D3 — `refresh_main`/`park_on_main` run after the canon push.
  §Decisions 3
- current `intent/024_avoiding_failed_merges` D4 — `transfer_branch_to_main`: ff-merge, else clean rebase, else
  fail fast, for todo/implement/accept alike. §Decisions 4
- current `intent/024_avoiding_failed_merges` D5 — The real `git rebase` is the only gate; `merge-tree` only names
  conflicts. §Decisions 5
- current `intent/024_avoiding_failed_merges` D6 — Rebase safeguards: clean tree, branch checked out only in the
  source repo, no dry-run mutation, `--force-with-lease`. §Decisions 6
- current `intent/024_avoiding_failed_merges` D7 — Rerun the pre-commit hook after an actual rebase, before canon
  merges. §Decisions 7
- current `intent/024_avoiding_failed_merges` D8 — The `rebase` prompt only rebases and runs the hook; agents never
  push. §Decisions 8
- current `intent/024_avoiding_failed_merges` D10 — Merged feature branches are deleted locally and on bare;
  failures only warn. §Decisions 10
- current `intent/024_avoiding_failed_merges` D12 — The rebase shows in the resume preview and dry-run.
  §Decisions 12
- current `intent/024_avoiding_failed_merges` D– — PR review filenames may cite pre-rebase hashes (accepted).
  §Audit notes: "Cosmetic; accepted"
- current `024_avoiding_failed_merges` D1 — Force-pushing a published branch needs `--force-push`, a passing
  verify, and no remote-only commits (`git cherry`). §Post-review fixes: "Force-pushing is now an explicit"
- current `024_avoiding_failed_merges` D2 — Three forcing flags: `--force-validation`, `--continue`,
  `--force-push`. §Post-review fixes: "Flag naming"
- current `024_avoiding_failed_merges` D3 — Every clone refreshes `main` after every handoff; only the handing-off
  clone is parked; failures only warn. §Post-review fixes: "Every clone's `main` is refreshed"

### Resume and robustness

- current `intent/020_robustness` D1 — Preconditions are a best-effort early guard; everything else relies on
  idempotent rerun. §Decisions: "Preconditions catch, they do not guarantee"
- current `intent/020_robustness` D2 — A step is skipped only when its postcondition holds, never on mere path
  existence. §Decisions: "Guards check postconditions"
- current `intent/020_robustness` D3 — Every git op in a handoff is its own guarded step.
  §Decisions: "Treat individual git commands as steps"
- current `intent/020_robustness` D4 — A resumed transition detects the already-moved file and skips.
  §Decisions: "Transition moves are a key precondition class"
- current `intent/020_robustness` D5 — Precondition: no `.git/index.lock` in touched repos.
  §Decisions: "No leftover git locks"
- current `intent/020_robustness` D6 — Shared `require()` and uniform `[skip]` logging (`workflow/steps.py`).
  §Decisions: "Shared vocabulary"
- current `intent/020_robustness` D8 — A separate read-only `doctor` command, not an extension of `status`.
  §Decisions: "`doctor` command"
- current `intent/020_robustness` D9 — Read-only guard queries run for real in dry-run; only mutations are
  simulated. §Decisions: "Dry-run previews resume"
- current `intent/020_robustness` D– — No automatic rollback; recovery is manual and made rare.
  §No automatic rollback
- current `intent/020_robustness` D– — `--continue` vouches that a rough partial state came from this same
  command; documented as dangerous. §The `--continue` contract
- current `020_robustness` D8 — Two-pass classify/preview/run; a rough step without `--continue` prints the
  preview and exits. §Design decisions: "Resume preview before mutating"
- current `020_robustness` D– — Cross-repo staleness between transitions is normal and not flagged by `doctor`.
  §Deviations: "`doctor` anomaly scope"
- current `020.1_more_robustness` D1 — Execution consults `resume.should_run`, never re-derives postconditions.
  §Design decisions: "Single source of truth for skip decisions"
- current `020.1_more_robustness` D2 — One idempotent guarded `transfer_branch_to_main` for todo/implement/accept.
  §Design decisions: "One guarded `transfer_branch_to_main`"
- current `020.1_more_robustness` D3 — push/new/go get preconditions and cheap idempotence only, no `ResumePlan`.
  §Design decisions: "Lightweight commands"
- current `020.1_more_robustness` D4 — `new` is non-resumable; it only refuses to overwrite.
  §Design decisions: "`new` always allocates the next number"
- current `026_stacking_bugs` D1 — A resume predicate that an earlier step of the same run invalidates is not
  classified; the idempotent step runs unconditionally. §Decisions: "Always run the `origin/main` merge"
- current `intent/027_various_fixes` D19 — "Resume plan" only when a step is satisfied or needs `--continue`;
  otherwise "Execution plan". §5.1: "Decision: \"resuming\" means"
- current `intent/027_various_fixes` D20 — Dry-run prints the cwd relative to the current directory.
  §5.2: "Decision: print `cwd` relative"

### Validation

- current `005.2_doublecheck` D– — Per-worktree file-scope rules (planner: plans only; builder: no intents or
  reviews; reviewer: no intents; canon: unrestricted). §Checks: "File scope verification"
- current `005.2_doublecheck` D– — Missing prerequisites fail the command instead of becoming check results.
  §Doublecheck function: "Missing prerequisites are hard command errors"
- current `012_plan_resolution_respec` D– — `doublecheck` is renamed `validate`. §Phase 1
- current `intent/023_out_of_band` D14 — Oob `validate` only checks that per-repo branch info exists; branch
  naming follows upstream. §Clarified decisions 14
- current `023.6_validate_doctor_and_reporting` D– — Builder/reviewer may not edit `body_of_agents.md` or
  `bootstrap_venv.sh`; the planner may not commit code. §`validate` becomes per-repo
- current `023.6_validate_doctor_and_reporting` D– — Branch-name validity is delegated to
  `git check-ref-format`. §Plan `branches` frontmatter
- current `025_stacked_plans` D– — A stacked branch is validated as a whole, not per plan.
  §Scope: "validated as a whole — accepted"

### AGENTS.md

- current `008_claude_md_generation` D– — `agents_md` does no git operations; only `init` commits AGENTS.md.
  §Command: "This is the deliberate choice"
- current `008_claude_md_generation` D– — `.agents_md_hash` lives outside every repo and is never agent-visible.
  §Trust model
- current `008_claude_md_generation` D– — Column-0 markers, strict marker matrix, append when no markers exist.
  §Marker handling rules
- current `008_claude_md_generation` D– — Runtime details (`worktree_override`, `pull_target`, `reminder`) stay out
  of AGENTS.md. §Risks: "are not surfaced in AGENTS.md"
- superseded (→ intent/023 D17) `intent/023_out_of_band` D5 — Oob AGENTS.md via a symlink from the docs repo.
  §Clarified decisions 5
- current `intent/023_out_of_band` D17 — Oob AGENTS.md body is `docs/body_of_agents.md`; the generated file is
  unversioned. §Second round decisions 1
- current `intent/023_out_of_band` D34 — The generated section says workflow rules override repo-local AGENTS.md.
  §Second round decisions 18
- current `023.4_init_oob_and_bootstrap` D– — In oob, AGENTS.md = body + managed section, written to all four
  worktree roots, never committed. §AGENTS.md in oob mode
- current `023.4_init_oob_and_bootstrap` D10 — A missing `body_of_agents.md` is an error, not an empty body.
  §PR-review decisions: "A missing `body_of_agents.md` fails `agents_md`"
- current `023.6_validate_doctor_and_reporting` D2 — `.agents_md_hash` format `v2` adds the `.workflow.json`
  hash; old files just mismatch. §Clarifications 2
- current `023.6_validate_doctor_and_reporting` D12 — The `.workflow.json` hash is advisory; `add-repo`
  regenerates AGENTS.md. §Post-review decisions: "`.workflow.json` stays a hint"

### Audit log and status

- current `004.3_audit_logging` D– — One JSONL log in the project dir, outside all repos; malformed lines are
  skipped with a warning. §Log file location
- superseded (→ intent/027 D17) `007_workflow_commands` D– — Next-step reminders derived implicitly from the
  config graph. §Audit logging and reminders: "Reminder lookup rules"
- current `intent/025_stacked_plans` D6 — The `implement` audit entry echoes the stack (`stacked_on`) for
  reporting only. §Decisions 6
- current `intent/027_various_fixes` D10 — `doctor` flags unknown commands and misattributed transition prompts
  in the audit log. §1.3 Decisions: "`doctor` reports audit entries that are impossible"
- current `intent/027_various_fixes` D13 — The audit log records the displayed next step; nothing parses it.
  §3 Decisions: "`next_step` holds the plain text"
- current `intent/027_various_fixes` D15 — Prompt dispatch prints and records its reminder.
  §3 Decisions: "Prompt dispatch also prints its next step"
- current `intent/027_various_fixes` D16 — `validate` writes an audit entry only on success; its next step is a
  configured free-text `reminder`. §3 Decisions: "`validate` writes an audit entry"
- current `intent/027_various_fixes` D17 — Every command with a next step has an explicit `reminder`; no implicit
  fallback. §3 Decisions: "Reminders are explicit"
- current `intent/027_various_fixes` D18 — Entries without `next_step` show nothing; no derivation.
  §3 Decisions: "Entries without `next_step`"
- current `027.2_small_fixes` D– — Free-text choice reminders (`accept or reject`) omit the plan name.
  §Risks: "accepted for a single mechanism"
- current `intent/023_out_of_band` D31 — Oob `status` shows a worktree × repo matrix.
  §Second round decisions 15

### Tmux layout

- current `019_tmux_implementation_gaps` D1 — All tmux layout ops go through libtmux in `tmux_dispatch.py`;
  dry-run renders the tmux command. §D1
- current `019_tmux_implementation_gaps` D2 — Exactly one control pane, located by the id stored in session
  option `@workflow_control_pane`. §D2
- current `019_tmux_implementation_gaps` D3 — The window stays `builder-shell`; `go manual` maps to it via
  `AgentConfig.manual_shell` (at most one). §D3
- current `019_tmux_implementation_gaps` D4 — Rotation failure is fatal in `go`, a loud warning in
  `implement`/`check`. §D4
- current `intent/020_robustness` D7 — `start` becomes the control pane; `--reset` refuses to kill its own
  session. §Decisions: "`start` and the control pane"

### Docker sandbox

- current `intent/017_docker_script` D– — Docker handling lives in a per-project shell script, not in `workflow`
  config. §Reasoning for separate shell script
- current `017_docker_script` D– — The script is looked up in the project dir first, then on `PATH`.
  §1: "_find_docker_script"
- current `intent/017_docker_script` D1 — `start` passes `--project-dir`; the script derives mounts from it.
  §Design decisions from critique: "Volume path"
- current `intent/017_docker_script` D2 — The script fails early without `env.list`.
  §Design decisions from critique: "`env.list` validation"
- current `intent/017_docker_script` D3 — The image needs a matching `auggie` UID (accepted limitation).
  §Design decisions from critique: "User ID"
- current `intent/017_docker_script` D4 — The image is hardcoded (accepted for now).
  §Design decisions from critique: "Hardcoded image"
- current `intent/017_docker_script` D5 — `-it` is intentional; the script is not for CI.
  §Design decisions from critique: "No TTY"
- current `021_run_agent_in_docker_fixes` D– — The script's first argument is a subcommand
  (`agent`/`venv`/`shell`/`hook`); inapplicable flags are usage errors. §Goal: "mandatory subcommand"
- current `021_run_agent_in_docker_fixes` D1 — The hook runs in a throwaway `docker run --rm` container for every
  repo, using the host-mounted venv. §Key design decisions: "Unified `hook REPO` mechanism"
- current `021_run_agent_in_docker_fixes` D3 — Canon gets a venv at init via a throwaway container.
  §Key design decisions: "canon gets its own venv"
- current `021_run_agent_in_docker_fixes` D5 — Throwaway containers get no API keys (`--env-file` dropped).
  §Key design decisions: "drop `--env-file`"
- current `021_run_agent_in_docker_fixes` D6 — `check` requires `run_agent_in_docker.sh`, like init/start.
  §Key design decisions: "`check` hard-depends on the external script"
- current `intent/024_avoiding_failed_merges` D9 — `bare` is mounted read-only into agent containers: fetch
  works, push fails by construction. §Decisions 9

### Out-of-band layout and metadata

- current `intent/023_out_of_band` D36 — Oob plans live in a separate docs repo, not an orphan branch.
  §Second round decisions 20
- current `intent/023_out_of_band` D1 — `.workflow.json` stores the code-repo list; no rediscovery by scanning.
  §Clarified decisions 1
- current `intent/023_out_of_band` D2 — `.workflow.json` is tool-owned JSON state; the mode is fixed at init.
  §Clarified decisions 2
- current `intent/023_out_of_band` D3 — Atomic write (tmp + `os.replace`) instead of a lock.
  §Clarified decisions 3
- current `intent/023_out_of_band` D4 — Hard cutover `.workflow_root` → `.workflow.json`, no fallback.
  §Clarified decisions 4
- current `intent/023_out_of_band` D6 — One `.venv` at the worktree root covering all code repos.
  §Clarified decisions 6
- current `intent/023_out_of_band` D7 — `bare` keeps its in-band shape; the modes are not unified.
  §Clarified decisions 7
- current `intent/023_out_of_band` D8 — The docs repo is always named `docs`. §Clarified decisions 8
- current `intent/023_out_of_band` D9 — An oob project has at least one code repo. §Clarified decisions 9
- current `intent/023_out_of_band` D10 — `init --oob` expects canon pre-populated by the operator.
  §Clarified decisions 10
- current `intent/023_out_of_band` D11 — The workflow remote is named `bare` in both modes; `origin`/`upstream`
  keep their usual meaning. §Clarified decisions 11
- current `intent/023_out_of_band` D18 — The oob venv is built by `docs/bootstrap_venv.sh` (option B).
  §Second round decisions 2
- current `intent/023_out_of_band` D20 — Five layout accessors: `worktree_root`, `git_repo_root`, `plans_root`,
  `venv_root`, `hook_root`. §Second round decisions 4
- current `intent/023_out_of_band` D22 — Per-repo branch info lives in plan frontmatter under `branches`
  (`branch` + `base`, or `null`). §Second round decisions 6
- current `intent/023_out_of_band` D23 — `workflow add-repo` registers a code repo after init.
  §Second round decisions 7
- current `intent/023_out_of_band` D24 — The docs repo's upstream is optional. §Second round decisions 8
- current `intent/023_out_of_band` D27 — Config (hand-authored YAML) vs state (tool-owned JSON with `version`) is
  documented. §Second round decisions 11
- superseded (→ intent/023 D28) `intent/023_out_of_band` D15 — Hook fallback to `.git/hooks/pre-commit` with an
  init-time opt-out. §Clarified decisions 15
- current `intent/023_out_of_band` D28 — `run_hook` is per repo; extra hooks in code repos only warn.
  §Second round decisions 12
- current `intent/023_out_of_band` D29 — `init` enforces mode immutability. §Second round decisions 13
- current `023.4_init_oob_and_bootstrap` D– — Project-setup commands (`add-repo`, `sync`, `doctor`) live outside
  the state machine. §`workflow add-repo`: "no state entry"
- current `023.4_init_oob_and_bootstrap` D5 — Canon code repos get a `bare` remote but keep tracking `origin`.
  §PR-review decisions: "Canon code repos keep their own upstream"
- current `023.4_init_oob_and_bootstrap` D6 — `--skip-hook` means no hook symlink and no hook run on `check`.
  §PR-review decisions: "`run_hook: false`"

### Out-of-band git operations

- current `intent/023_out_of_band` D12 — `check`/`accept` push every repo with commits ahead of `bare`.
  §Clarified decisions 12
- current `intent/023_out_of_band` D13 — On `check`, code repos are only fetched; the reviewer checks out
  branches. §Clarified decisions 13
- current `intent/023_out_of_band` D21 — `workflow sync` refreshes `bare/{repo}` from upstream; `doctor` reports
  divergence. §Second round decisions 5
- current `intent/023_out_of_band` D32 — Every agent-side code repo has exactly one remote, `bare`, so agents
  cannot reach upstream. §Second round decisions 16
- current `intent/023_out_of_band` D33 — `check` and `doctor` check dirty state in all repos.
  §Second round decisions 17
- current `023.5_multi_repo_git_orchestration` D– — The tool never checks out, refreshes, or parks a code repo;
  it only fetches. §Branch handling in code repos
- current `023.5_multi_repo_git_orchestration` D– — Every prompt dispatch fetches all code repos (warnings only).
  §Fetch-all
- current `023.5_multi_repo_git_orchestration` D– — `sync` pushes only the upstream default branch plus explicit
  `--branch`es. §`workflow sync`: "Deliberately **not** mirroring all upstream heads"
- current `023.5_multi_repo_git_orchestration` D1 — `sync` is fast-forward-only unless `--force`.
  §Clarifications 1: "`sync`'s force-push is gated"
- current `023.5_multi_repo_git_orchestration` D13 — `sync` is oob-only and skips `docs` unless `--repo docs`.
  §Post-review decisions: "`workflow sync` is rejected in-band"
- current `023.5_multi_repo_git_orchestration` D14 — Detached or diverged code branches are refused before any
  push; `accept --force-push` uses `--force-with-lease`. §Post-review decisions: "Push-set preconditions"
- current `023.5_multi_repo_git_orchestration` D15 — Code work must be on feature branches; `check` refuses
  commits on a code repo's default branch. §Post-review decisions: "`check` in oob refuses commits"
- current `023.5_multi_repo_git_orchestration` D16 — `accept` runs the hook on every code repo with reviewer
  commits. §Post-review decisions: "`accept` runs the per-repo hook"
- current `023.6_validate_doctor_and_reporting` D1 — The one-remote rule applies to agent worktrees, not canon.
  §Clarifications 1
- current `023.6_validate_doctor_and_reporting` D5 — Everywhere, tracked changes are an anomaly and untracked-only
  is a note. §Clarifications 5
- current `intent/024_avoiding_failed_merges` D11 — `doctor` compares each `main` with bare's and flags branches
  not based on it (informational). §Decisions 11

### Stacked plans

- current `intent/025_stacked_plans` D1 — A stack shares one branch; a branch per plan was rejected.
  §Decisions 1, §Rejected approach
- current `intent/025_stacked_plans` D2 — `--onto` names what the new work sits on. §Decisions 2
- current `intent/025_stacked_plans` D3 — Stacking onto a plan past `implementing` is refused. §Decisions 3
- current `intent/025_stacked_plans` D4 — Stacks are lists, not trees: `--onto` must name the tip. §Decisions 4
- current `intent/025_stacked_plans` D5 — `onto:` frontmatter is the source of truth; the branch is named after
  the stack root. §Decisions 5
- current `intent/025_stacked_plans` D7 — After the transition push, `origin/main` is merged into the stack
  branch. §Decisions 7
- current `intent/025_stacked_plans` D8 — `reject` does not cascade; it prints the manual branch rollback.
  §Decisions 8
- current `intent/025_stacked_plans` D9 — check/accept/pr/fix on a mid-stack plan error and name the tip.
  §Decisions 9
- superseded (→ 025_stacked_plans D– §4) `intent/025_stacked_plans` D10 — `accept` moving `implementing` members
  straight to `done`. §Decisions 10
- current `025_stacked_plans` D– — `check TIP` moves every member to checking and `accept` moves all to done, so
  every move is a declared edge. §4: "`check` cascades to the whole stack"
- current `026_stacking_bugs` D3 — A broken stack fails every command, including `ask`.
  §Decisions: "Stack-resolution failures keep failing hard"

## Deferred work

- open `intent/001_idea` — Fourth "freeform" agent.
- open `intent/001_idea` — More validate checks: bandit rules for tests (paths, network and sleep mocking);
  TDD file-scope checks.
- open `intent/001_idea` — MCP so agents can run selected git operations (e.g. `check`) outside the sandbox.
- open `006_info_commands` — Flesh out the intent template beyond title + Goal.
- open `intent/012_plan_resolution_respec` — Record the planner branch on `plan`/`subdivide` audit entries to
  skip the git branch lookup.
- open `intent/012_plan_resolution_respec` — Sweep remaining "plan" wording to "ticket" where appropriate.
- open `012_plan_resolution_respec` — Type-level `Ticket` class distinct from `Plan`.
- open `014_fixes_to_006_007_012` — Shared `conftest.py` consolidation across CLI, command and resolver tests.
- open `014_fixes_to_006_007_012` — Performance work on `_filesystem_matches`.
- dropped `014_fixes_to_006_007_012` — `_LogFilters` dataclass refactor.
- open `intent/017_docker_script` — Ensure `.venv/bin/activate` is sourced before the agent runs.
- open `intent/017_docker_script` — Container image override.
- open `019_tmux_implementation_gaps` — Automatic agent dispatch on every command (intent/001 stretch goal).
- open `reviews/followthrough/bf55b86` — Fuzzy resolution of review paths (`reviews/pr/007`).
- open `020_robustness` — `SourcesDisagreeError` when the same transition command is rerun with a bare
  identifier (workaround: fully qualified path).
- open `021_run_agent_in_docker_fixes` — Run `canon_pre_commit` as part of `accept`.
- open `021_run_agent_in_docker_fixes` — Automated tests for the docker shell script.
- open `intent/023_out_of_band` — Removing a code repo from an oob project.
- dropped `025_stacked_plans` — Rejecting a mid-stack plan automatically.
- dropped `025_stacked_plans` — Stack depth limit as a UX rule (only a runaway guard).
- open `intent/027_various_fixes` — Make `resolve_plan` always honour explicitly given fields of the fuzzy input.
- open `intent/028_index_of_decisions` — Index compression once the index has been used in practice.

## Terminology

- current **ticket** (`intent/012_plan_resolution_respec`) — a plan in planning, todo, implementing, checking,
  done or rejected; excludes intents and reviews.
- current **handoff** (`intent/012_plan_resolution_respec`) — a transition between agents, done by a script
  command with cross-repo git (`todo`, `implement`, `check`, `accept`).
- current **silent source** (`intent/012_plan_resolution_respec`) — a resolver source with no information on a
  dimension; it adds no constraint there.
- current **soft / hard constraint** (`016_soft_resolver_constraints`) — a resolver repo contribution that is a
  routing hint (dropped when the path names a repo) vs. a fact.
- current **control pane** (`019_tmux_implementation_gaps`) — the operator's shell pane that moves next to the
  active agent window.
- current **resume plan / rough state** (`020_robustness`) — the classify-then-run step list printed before
  mutating; a rough state can only be checked roughly and needs `--continue`.
- current **in-band / out-of-band (oob)** (`intent/023_out_of_band`) — plans in the code repo vs. in a separate
  `docs` repo; fixed at init.
- current **state vs config** (`intent/023_out_of_band`) — `.workflow.json` is tool-owned state; `config.yaml`
  is hand-authored config.
- current **worktree** (`023.2_layout_and_vocabulary`) — one of `canon`, `planner`, `builder`, `reviewer`
  (`WorktreeName`); in oob a wrapper directory holding several git repos.
- current **git repo / docs repo / code repo** (`023.2_layout_and_vocabulary`) — an actual git repository in a
  worktree; `docs` holds the plans (`Layout.repos()` includes it, `code_repos()` does not).
- current **push set** (`023.5_multi_repo_git_orchestration`) — repos in the acting worktree with commits in no
  `bare` ref; what `check`/`accept` push.
- current **stack / tip / root** (`intent/025_stacked_plans`) — plans sharing one feature branch via `onto`; the
  tip is the newest member, the root names the branch.
- current **borrowing** (`intent/027_various_fixes`) — sending a prompt command to an agent with no definition of
  it, which needs `--edit`.
