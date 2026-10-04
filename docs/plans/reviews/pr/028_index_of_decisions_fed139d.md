# PR review: 028_index_of_decisions (fed139d)

Plan: `docs/plans/intent/028_index_of_decisions.md` (special process: builder part only; the reviewer part —
creating the index, format proposals, final `index_decisions` prompt — follows as a separate step).

Branch commits: `002ceac` feat, `5de17a9` style, `fed139d` docs.

## Verification

- `pytest`: 784 passed.
- `mypy workflow tests`: clean. `black --check -l 120`: clean.
- `sphinx-build`: builds; the new `:ref:` resolves.
- `vulture workflow`: only pre-existing noise (click entry points, `model_config`); nothing new.

## Spec coverage (builder scope from "Special process")

| Item | Status |
|---|---|
| Generated AGENTS.md: read `intent/001` + index, phrased by activity, layout-derived path | Done |
| Generated AGENTS.md: decision conventions (marking + referring) | **Missing** (finding 1) |
| `index_decisions` command with placeholder prompt | Done |
| `followthrough` prunes deferred items | Done, wording issue (finding 3) |
| User docs: index file, periodic human review | Done, gaps (findings 4, 5) |

## Findings

### High

1. **Decision conventions are not in the generated AGENTS.md section.**
   The intent requires: "Both conventions (marking and referring) go into the generated part of AGENTS.md"
   (Decision 8), and the builder scope lists "generated AGENTS.md section (Agent instructions, decision conventions)".
   `agents_md_workflow.md.j2` only gained the two read-first lines. Missing content, at minimum:
   - Refer to decisions as `{identifier} Decision {n}`; identifier forms `intent/{number}_{slug}`,
     `{number}_{slug}` (no state), `reviews/{type}/{filename}`; `.md` dropped; dotted numbers kept.
   - One numbered `## Decisions` list per file (reviews of stacked plans may have several sections,
     numbering continuous); append-only, never renumber, withdrawn items stay marked withdrawn.
   - Every resolved open option produces a Decisions entry; the "would have" section is only the rationale record.
   - Legacy files are count-based and not edited; legacy references resolve only via the index.
   Without this, agents keep producing decisions in the old ad-hoc format, so every new file becomes "legacy"
   and needs locators, which defeats the index's cheap numbering. Note the system-prompt draft in the intent
   says "Refer to decisions as ... (see AGENTS.md)" — that reference currently dangles.

### Medium

2. **Placeholder prompt must not ship.** `index_decisions` prompt is
   "Placeholder (to be replaced in plan 028)". Correct per the special process, but this is the plan being merged,
   so the reviewer step must replace it on this branch before `accept`; otherwise the grep convention
   ("replaced in plan 028") leaves an orphan in `done`. Tracking only — not a builder defect.

3. **`followthrough` hash wording contradicts Decision 21.** The prompt says "record the current commit hash as
   the last followthrough pass". The reviewer is on a `review/*` branch at that point, so "current" is ambiguous
   (HEAD of the review branch after its own commits is not in `main` after a squash or rebase).
   Should be the same rule as the index pass: the `main` commit the branch was cut from.
   Note the `{{ commit }}` template variable is *not* a safe source for this: `_git_metadata` reads the reviewer
   worktree's HEAD before `git_pull`/`refresh_main` run, on whatever branch the reviewer happens to be on.

4. **Existing projects do not get the new command.** Project `config.yaml` files are copies, not overlays of
   `default_config.yaml`. The docs already carry this caveat for stack-aware prompts (workflow.rst, "Stacked plans");
   the new "Index of decisions" section should likewise say that existing projects need to copy the
   `index_decisions` command and the `followthrough` addition into their `config.yaml`. This includes the
   workflow project itself.

### Low

5. **Docs: oob wording for the manual cleanup.** workflow.rst says "Remove any static copy of the first instruction
   from your own `AGENTS.md` body". In oob mode the body lives in `docs/body_of_agents.md`
   (editing `AGENTS.md` directly is overwritten on the next `agents_md`). Mention both.

6. **Docs over-constrain the merge method.** "must be merged by fast-forward or merge commit, never squashed or
   rebased" — with the recorded hash being the `main` commit the branch was cut from (Decision 21), that hash is
   already in `main`'s history regardless of how the index branch is merged. The restriction (from the intent)
   only matters if the recorded hash were a branch commit. Either drop it or keep it as belt-and-braces; it is
   harmless but costs the human a rule to remember. Raised again in the format proposals of the reviewer step.

7. **Hardcoded `docs/plans/index_of_decisions.md` in prompts.** Both prompts hardcode the path while the template
   derives it from `layout`. Consistent with every other prompt in `default_config.yaml` (prompts have no
   `plans_dir` variable), and `plans_root` is identical in both modes, so not a bug. In oob mode the
   `git diff --name-only <hash>..HEAD -- <plans root>` has to run in the `docs` repo with path `plans/`;
   the final prompt text must say so (reviewer step).

8. **Command description omits intents.** "Update the index of decisions from plans and reviews not yet indexed"
   — intents are indexed too (Decision 4). Suggest "from plans, intents, and reviews ...". This string is also
   rendered into every AGENTS.md.

9. **Redundant / non-parametrized tests.**
   - `test_render_workflow_section_in_band_reads_idea_and_index` duplicates what the golden fixture already pins.
   - The in-band and oob tests assert identical strings; parametrize over the config loader (project guideline),
     or keep only the oob one, which is the case the golden fixture does not cover.
   - `test_default_config_has_index_decisions_for_reviewer` is covered by the golden fixture's command listing line.
   - No CLI dispatch test for `index-decisions`/`index_decisions` analogous to the `full-review` test in
     `test_cli.py`; the command name contains an underscore while `command_cli_name` only maps spaces, so the CLI
     name is `index_decisions`. Fine, but worth one dispatch test once the final prompt lands.

10. **Read-first instruction sits at the end of AGENTS.md.** The managed section is appended after the hand-written
    body, so "Always start by reading ..." appears at the bottom of the file. Agents read the whole file, so this
    works; noting it because the intent moved it out of the body's first line.

## Not issues (checked)

- `render_workflow_section` now calls `config.layout()`, which raises without metadata; its only production caller
  (`prepare_agents_md_update`) already requires a layout, and test fixtures create the project marker.
- `plans_dir` is a `Path`; renders with `/` on POSIX, which is the only supported platform.
- Sphinx label with spaces (`.. _Index of decisions:`) follows the existing `.. _Stacked plans:` pattern.

## Follow-ups for the human (manual, per intent)

- Remove the static "Always start by reading docs/plans/intent/001_idea.md" line from this repo's `AGENTS.md`
  body, then run `workflow agents_md` to regenerate the managed section.
- Paste the "Recording decisions" draft into the harness system prompts (Decision 23).

## Reviewer-step process (agreed with the human)

- All reviewer-step work (index file, format proposals, final `index_decisions` prompt) goes on
  `feat/028_index_of_decisions`, so the placeholder never reaches `main`. The recorded hash is `1c5aabf`,
  the `main` commit this branch was cut from.
- Order: fix findings 1, 3, 4, 5, 8, 9 first; then build the index; then stop for human review.
- Format proposals are discussed in chat first; agreed changes are written into the intent (spec + Decisions).
- The final prompt text is written only after the format proposals are decided.

## Reviewer step: index built — format proposals (pending human decision)

Status: findings 1, 3, 4, 5, 8, 9 fixed (`b7fadb4`); index created (`docs/plans/index_of_decisions.md`,
`e4ef809`): 69 files read, all indexable files appear in Plan summaries, 216 decision entries, 22 deferred items,
13 terms, ~44 KB. The final `index_decisions` prompt is not written yet (waits on the decisions below).

Learnings and proposals (to be decided, then written into `intent/028_index_of_decisions`):

1. **Legacy count-based numbering is a judgement call, not mechanical.** Decision-list headings vary
   (Decisions, Design decisions, Key design decisions, Clarified decisions, Clarifications, PR-review decisions,
   Post-review decisions; 024 has "Post-review fixes" whose text calls them decisions), some headings only
   *reference* a decision ("Dirty-tree preconditions (decision 17)"), and files carry their own labels that clash
   with counting (014: an unnumbered list, then D1–D9 and E1–E4; intent/023: two lists both starting at 1, with
   cross-references like "decision 6" that are themselves ambiguous). The counting rule applied is written in the
   index header. Proposal: adopt that rule in the intent; keep count + locator.
2. **Implicit legacy decisions (`D–`).** Plans 002–008, the 012 plan, 016 and parts of 017/021/023.x have no
   decision sections but hold the most load-bearing choices (relative `Plan.path`, no-verify transitions, trust
   model of `.agents_md_hash`, soft vs hard resolver constraints, migration-over-compat policy). "Explicit only"
   would drop them. ~45 entries are indexed as `D–` with a locator only. Proposal: allow `D–` for legacy files
   only; new files must use the Decisions list.
3. **Intent and plan restate the same decisions** (intent/020 D1–D10 ≈ 020 plan D1–D10; intent/024 vs plan 024).
   Proposal: index the earliest statement only; a later file is indexed only where it changes or adds something
   (e.g. 025 plan §4 supersedes intent/025 D10).
4. **Entry head for plan summaries.** The head is the top-level plan when one exists (`020_robustness`), else the
   intent (`intent/023_out_of_band`, whose plans are all subdivided). Proposal: write this rule down.
5. **Reviews contribute nothing so far.** No legacy review has a Decisions list; fix-time decisions were written
   into the *plan* ("PR-review decisions" in 023.4, "Post-review decisions" in 023.5/023.6, "Post-review fixes"
   in 024). Decision 16 says they belong in the review file. Proposal: say so explicitly in the generated
   AGENTS.md conventions ("decisions made while fixing go into the review's Decisions list") — otherwise
   reviewers will keep writing into the plan.
6. **Partial supersession.** Some later decisions narrow rather than replace an earlier one (intent/020 D9 narrows
   004.4 §6 on dry-run). Proposal: no extra status; keep `current` and say "narrowed by …" in the summary.
7. **Hash merge-method rule is redundant** (finding 6): with the recorded hash being a `main` commit, squash or
   rebase of the index branch cannot invalidate it. Proposal: drop "never squashed or rebased" from Decision 21
   and the docs.
8. **Stale 001 terminology.** intent/001 defines *repo* as one of canon/planner/builder/reviewer/bare; 023.2
   renamed that concept to *worktree*. Decision 15 excludes 001 terms from the index, so the stale definition is
   invisible here. Suggest updating 001 §Terminology (worktree vs git repo) and adding *ticket* and *handoff*.
9. **Size.** ~44 KB (~11k tokens) read at the start of every planning/review session, and it only grows.
   Proposal: keep entries to one summary line plus locator (done); revisit with Decision 22 (compression).
   Alternative: phrase the AGENTS.md instruction as "read the topics relevant to the task".
10. **"Mentioned in the index" needs a definition** for the candidate rule (Decision 20). Proposal: a file counts
    as indexed iff it appears in Plan summaries (as head or in Related). The index satisfies this today.
11. **Deferred work on first creation.** Out-of-scope items already completed by later plans were not added (e.g.
    the `go` command, `start` placeholders). Proposal: the prompt says the indexer skips items it can see are done;
    `followthrough` remains the authority for pruning.
12. **Selectivity.** 216 decisions is many. Candidates for pruning: trivial `D–` entries and 028's own decisions.
    Ask the human whether to prune before the prompt is written, since the prompt sets the bar for future passes.

## Round 2: human decisions applied, pruning

### Review issues

- **3 (`{{ commit }}` before pull)**: confirmed and fixed (`3c466dd`). `_setup_*` read `branch`/`commit` before
  `_dispatch_prompt` pulled the recipient. `pr` was mostly safe, since `check` already pulls the reviewer. But
  `full review` (and any stateless prompt) on a reviewer whose checked-out branch lagged `bare` (e.g. `main` after a
  planner `push`, which is not a handoff and refreshes no clone) named the report after the pre-pull commit.
  The sync now runs inside setup, before metadata is read. A regression test covers `pr` and `full-review`.
  Side effect: "Plan resolved to" is now printed after the sync instead of before it.
- **6**: merge-method restriction removed from docs and intent Decision 21.
- **7**: `Layout.plans_root_in_worktree` (`docs/plans` in both modes) is the template variable for all prompts and
  for AGENTS.md (replacing `plans_dir`). `git -C {{ plans_root_in_worktree }} diff --name-only --relative <hash> -- .`
  works in both modes, so no repo-root-relative variable is needed.

### Format proposals

Recorded as intent/028 Decisions 24–37 (Decision 16 withdrawn, 4/21/22 amended). intent/001 terminology updated
(worktree, git repo, ticket, handoff); those terms were removed from the index Terminology section (Decision 15).
Consequences applied to the index: reviews dropped from Plan summaries, subplans listed as ranges.
One loss: the only review-sourced deferred item ("fuzzy resolution of review paths", from
`reviews/followthrough/bf55b86`) was dropped with the reviews. If still wanted, it needs an intent.

### Pruning (applied: 202 → 152 decisions, 44 KB → 35 KB)

Numbers refer to the order of the first index version (`e4ef809`).

- **Obvious location, higher bar** (20): 028 D3 hash-not-timestamps, 028 D4 indexed files, 028 D7 reviewer owns,
  028 D12 legacy locators, 028 D13 append-only, 028 D18 resolved options, 028 D21 main hash (all restated by the
  index header or AGENTS.md); 012 D9 union on empty intersection; 012 D12 full path skips git; 012 tmux optional;
  004.1 unknown scripts fail at load; 004.2 kebab-case names; 027 D6 dispatch names plan/recipient;
  020 D6 shared `require()`; 005.2 hard prerequisite errors; 008 marker matrix; 023 D31 status matrix;
  023 D3 atomic write; 023 D23 `add-repo` exists; 023 D21 `sync` exists.
- **Structural "subclass/field"** (4): 002 sequence-number ints; 003 `Review` subclass; 004.4 template variables
  `plan`/`branch`/`commit`; 023 D20 five layout accessors.
- **Trivial** (15): 014 D23 same-second git ties; 024 D12 rebase in preview (follows from 020 D9); 020 D4 resumed
  transition skips (follows from 020 D2); 020 D5 `index.lock`; 027 D19 "Resume" vs "Execution plan" wording;
  027 D20 relative cwd; 023.6 `check-ref-format`; 023.6 D2 hash file `v2`; 027 D15 prompt prints reminder;
  027 D18 entries without `next_step`; 027.2 choice reminders omit plan name; 017 D2 `env.list` check;
  023 D9 at least one code repo; 023 D33 dirty check in all repos; 024 D11 doctor compares `main`.
- **Not relevant outside the plan** (5): 004.4 per-repo prompt loop (superseded); 005.3 doublecheck loop
  (superseded); 014 D4 hand-coded `check` mapping (superseded inside the same plan); 014 D18 non-transition
  commands; 023 D4 `.workflow_root` cutover (migration-only; policy covered by the migration-over-shim entry).
- **Scoping within a plan** (1): 027 D23 conservative parametrization (separate sub-plan, refactor-local policy).
- **Obvious definition** (1): 025 D2 `--onto` names what the work sits on.
- **Restated (format decision 28)** (4): intent/020 no automatic rollback (= 007 partial failures);
  023.4 AGENTS.md to all four roots (= 023 D17); 023 D27 config vs state (= Terminology); 023 D29 mode
  immutability (= 023 D2).
- "Never index" rules for accept/reject and adding tests pruned nothing: legacy files rarely record those as decisions.

### Ten lowest-value remaining decisions

1. `022` three-agent limit is deliberate: intent/001 already says three agents; a doc caveat, not a design choice.
2. `022` one workflow, fork to change it: product positioning, never consulted when planning a feature.
3. `014` D8 slug regex: visible in code and in the error message; kept only by the "unobvious location" rule.
4. `intent/017` D4 image hardcoded: duplicates the open deferred item "container image override".
5. `intent/017` D5 `-it` intentional: local to the shell script.
6. `017` script lookup (project dir, then `PATH`): one function, easily found by code search.
7. `020.1` D4 `new` is non-resumable: follows from `new` allocating a fresh number.
8. `intent/027` D21 `doctor` also uses exact names: applies 026 D2 to one more caller.
9. `intent/024` D3 refresh/park after the canon push: fully restated and extended by `024` D3 (every clone).
10. `023.6` D12 `.workflow.json` hash is advisory: a minor detail of the hash file.

Kept despite low value, because a rule protects them: 3 (unobvious location), 4–5 (accepted gaps).

### Proposed additional rules

1. Content already in `intent/001` is not indexed (mirrors Decision 15 for terms). Prunes 1.
2. An accepted limitation that is also a deferred-work item is listed only under Deferred work. Prunes 4.
3. A decision that only applies an indexed rule to one more caller or command is not indexed. Prunes 8,
   and in round 1 would have pruned 024 D12 and 020 D4.
4. Output wording and formatting are not indexed unless something parses them or they are a user-facing contract.
   Prunes 027 D19/D20, 027.2 (already pruned as trivial; makes the reason explicit).
5. A supersession inside the same file (decision withdrawn before the plan was done) is not indexed. Only
   cross-file supersessions are kept (014 D4).
6. Superseded decisions pass the same bar as current ones. A superseded decision that would be pruned if current
   is not kept (004.4/005.3 loops).

### Critique of the rule set

1. "Keep decisions in unobvious locations even if less impactful" collides with "non-trivial". Fix-up plans
   (014, 027.2) are full of small choices. Recommend: "a lower bar for impact, not for triviality".
2. "Something asked from the user is more likely to count" inflated legacy intents. intent/023's 36 clarified
   decisions include trivial answers (atomic write, at least one repo). Recommend weighting it only for choices
   between real alternatives, not confirmations.
3. "Keep never-X decisions": almost anything can be phrased negatively ("no confirmation dialog"). Recommend
   restricting it to prohibitions a later plan could plausibly violate (agents never push, no silent fallback).
4. "Superseded and obsolete decisions are kept" (Decision 5) without a bar keeps trivia alive. See proposed rule 6.
5. "Obvious location" is ambiguous for subsystems spread over several plans (the resolver: 003, 012, 014, 016).
   Acceptable as a heuristic; the definition is "the plan that introduced the subsystem".
6. No rule recommended against outright. "Never index decisions to add tests" is redundant with
   "relevant outside the plan" but costs nothing as an explicit example.

### 9.2 Reading only relevant topics: proposal

Options:
- A. **Markdown headings plus grep (recommended).** AGENTS.md: "When planning or reviewing, read its header,
  Plan summaries and Terminology, list the topics with `grep -n '^###' <file>`, then read the topic sections
  relevant to the task." No maintenance; line numbers come from grep, so they are always current. An Opus-level
  agent does this without further help.
- B. Topic list at the top, without line numbers. The reviewer maintains it on each pass. It duplicates the
  headings and drifts when a pass merges topics. Line numbers would drift on every edit.
- C. YAML plus a filter command. Needs a parser, and either a CLI agents can run (they can't run `workflow`) or a
  script. Conflicts with the rejected "Structured data" alternative and with readability for the human.

Recommendation: A. Keep topic headings at `###` and the fixed sections at `##`, so one grep lists them.
Deferred work only matters when planning; the instruction can say so.
