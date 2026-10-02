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
