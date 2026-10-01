# 028: Index Of Decisions

## Goal

Improve the discoverability of architecture and design decisions.

Currently, decisions are made during planning or review, and noted only in individual plans.
There is a risk that a later planning step fails to discover the relevant decision,
as finding it is based on only the plan slug and keywords that planner searches for.

## Index file format

The index file is located at `index_of_decisions.md` in the plans root
(`docs/plans/` in-band, `plans/` out-of-band; derived via `layout`).
In a fresh project, it does not exist. The file is created on the first run of the update prompt.

The index records the commit hash of the last indexing pass (Decision 3, 21).

The file is also the single place for the human to review the current set of decisions;
the user docs recommend reviewing it periodically (Decision 11).

Decisions, deferred work, and terms are grouped under topic headings (e.g. git sync, plan resolution, oob layout)
chosen by the reviewer, with the file identifier as a field. Each pass may merge or rename headings (Decision 10).

### Indexed files

- Plans in `done`, all intents, and reviews (Decision 4).
- Excluded: plans in-flight (`planning`, `todo`, `implementing`, `checking`) and `rejected`,
  and reviews of excluded plans. Codebase-wide reviews (full, followthrough) are always included.
- Reviews contribute only their explicit Decisions lists (decisions made during `fix` that exist only
  in the review file), never raw findings or proposals (Decision 16).
- Intents (Decision 17):
    - all of whose plans were rejected: their decisions are marked obsolete.
    - with no plan yet: their decisions are marked `intended`.

### Entry summary section

Each entry in this section represents one change ("entry"), containing the following fields
- One line summary of plan contents
- List of related files: intent, ticket, reviews
    - Also acts to track which plans have been indexed: a file not mentioned needs to be added in the next pass
    - Note that tickets should be listed without the state, as that would become stale quickly

An entry collapses an intent, its plan(s), and their reviews,
because their summaries would be (nearly) identical, and a review is best summarized by the reviewed change.
For subplans and stacked plans, the reviewer decides when they need an entry of their own.
Rule of thumb: entries with the same summary go together.

### Decision summary section

Each entry in this section represents one decision, containing the following fields
- Identifier for the file it is found in
    - `intent/{number}_{slug}`
    - `{number}_{slug}` (no state)
    - `reviews/{type}/{filename}`
- Decision number in the file
    - Legacy files additionally carry a locator: section heading plus a short quote.
      The index is the authority for legacy numbers (Decision 12).
- Status
    - current: Still relevant. Either currently reflected in code or intended to become that.
    - intended: From an intent that has no plan yet.
    - superseded: A later decision overrides this. A different path was taken.
    - obsolete: This decision pertains to some component that was removed. A different path was not taken: the path is gone.
- One line summary of decision

Only explicit decisions are indexed. They should be non-trivial and likely to be relevant outside the plan itself.
Logging every small decision the agent made by itself would be counterproductive.
Something that needed to be asked from the user very likely counts
(but beware of questions whose answer was already in the plan).

Superseded and obsolete decisions are kept in the index, so an agent reading an old plan can tell
whether a decision still holds. Superseded entries reference the superseding decision.

Supersede check: for each newly indexed decision, the reviewer searches the index for decisions it overrides
and marks them superseded (Decision 11). Obsolete detection is best-effort; the index file header says so.

### Deferred work section

Deferred, out-of-scope, and future work items. Fields:
- Identifier of the file (same format as above)
- Status
    - open: still deferred.
    - dropped: decided not to do it.
- One line summary

Items that were deferred but have since been completed are removed: they no longer need tracking.
The indexer only adds items; the `followthrough` prompt prunes completed ones,
as completion detection needs code inspection (Decision 14).
Carve-out: work deferred to a later part of the same subdivide chain is omitted.

### Terminology section

Only architecturally important terms: a term coined or given a specific meaning in a plan,
and used across several plans or in code identifiers (e.g. actor, repo vs agent, oob, canon, stack).
Avoid devolving into a list of all names in the project.
Terms already defined in `intent/001` are excluded; the reviewer may instead suggest adding a term there (Decision 15).
Fields:
- Term
- Identifier of the defining file
- One line definition
- Status (current / superseded, e.g. when renamed)

## Agent instructions

The instruction to read `intent/001` moves from the static part of AGENTS.md into the generated part
(`workflow/templates/agents_md_workflow.md.j2`), and the instruction to read the index is placed right after it.
The template uses `layout` to produce the correct paths, so it works for oob projects.
The index instruction is phrased by activity rather than agent name
(e.g. read when planning or reviewing; when implementing, consult it if the plan leaves something unclear),
because agent names are configurable.
Removing the old static instruction from existing projects' AGENTS.md / `body_of_agents.md` is a manual step for the human.

## Update prompt

A prompt command `index_decisions` should be added to reviewer.
The gist of it is that the agent should read all indexable files not already mentioned in the index,
plus those changed since the recorded hash (`git diff --name-only <hash>..HEAD -- <plans root>`),
and update the index file with decisions collected from them.
If the index file doesn't already exist, it should be created.
Both halves of the candidate rule are needed: "not mentioned" catches new files, the hash catches modified ones
(including reviews whose plan only later reached `done`) (Decision 20).

Hash rules (Decision 21):
- Record the `main` commit the review branch was cut from, so it is in `main`'s history.
- The index branch is merged fast-forward or with a merge commit, never squashed or rebased.
- In oob mode, the diff runs in the repo holding the plans.

The exact text TBD.

## Related change: how to unambiguously refer to decisions

Decisions should be referred to as `{identifier} Decision {number_in_file}`,
where identifier is the same as used in the decision summary section.

Decisions are marked as a single numbered `## Decisions` list per file.
This applies to reviews too. A review of stacked plans is organized by plan, so it may have more than one
Decisions section, but numbering continues across the file and never resets (Decision 16).

Numbering is append-only: never renumber. A withdrawn decision stays in place, marked withdrawn (Decision 13).

Resolved open options always produce an entry in the Decisions list;
the discarded-option section ("would have" summary) remains only as the rationale record (Decision 18).

Legacy files are not edited to conform. Their decisions are count-based: explicit decision items
(across all decision sections and inline `Decision:` sentences) are numbered in file order,
e.g. 014 gets one sequence across its four Decisions sections.
References to legacy decisions are valid only as resolved through the index (Decision 12).

Both conventions (marking and referring) go into the generated part of AGENTS.md,
not into the agent system prompts (in the harness, outside this project) nor the per-command prompts in
`docs/default_config.yaml` (they would have to be repeated across many commands).

Identifier details: the `.md` suffix is dropped for all identifiers, including reviews.
Dotted plan numbers stay as they are (e.g. `023.4_init_oob_and_bootstrap`).

## Special process for this ticket

Planner will help to improve this file by critiqueing it.
Builder: generated AGENTS.md section (Agent instructions, decision conventions),
the `index_decisions` command with placeholder prompt text, the `followthrough` prompt pruning deferred items,
and user docs (index file, periodic human review).
Reviewer will
- Read all plans in the current project.
- Create the index file for the current project.
- Based on learnings from doing that.
    - Propose changes to the format of the index file.
    - Formulate the final update prompt text and add the prompt command.

## Decisions

1. Decisions are marked as a single numbered list per file; legacy files are count-based and not edited.
   (Amended by Decision 16: multiple sections allowed, numbering continuous.)
2. Only explicit, non-trivial decisions likely relevant outside their own plan are indexed.
3. Detection of unprocessed files: record the commit hash of the last pass in the index (not timestamps),
   so files modified after being indexed are also picked up.
4. Indexed: `done` plans, all intents, reviews. Excluded: in-flight and `rejected` plans
   (the consequences of rejected decisions were rejected), and reviews of excluded plans.
5. Superseded and obsolete decisions are kept.
6. Also index deferred/out-of-scope/future work (statuses open/dropped; completed items removed;
   same-subdivide-chain deferrals omitted) and architecturally important terminology.
7. Reviewer owns `index_decisions`. The resulting branch is merged manually.
8. The instructions to read 001 and the index, and the decision conventions, live in the generated part of AGENTS.md.
   Index reading is phrased by activity, not agent name.
9. Identifiers drop the `.md` suffix; dotted plan numbers stay as they are.
10. Index entries are grouped by topic heading, not by file; the file identifier is a field.
11. The update prompt includes a supersede check against existing entries. Docs recommend the human
    periodically review the index, which doubles as the single place to review current decisions.
12. Legacy decisions carry a locator (heading + short quote); legacy references resolve only via the index.
13. Decisions lists are append-only; withdrawn decisions stay in place, marked withdrawn.
14. Deferred work: the indexer only adds; `followthrough` prunes completed items.
15. Terminology excludes terms defined in `intent/001`.
16. Reviews: only explicit Decisions lists are indexed (decisions made during `fix`).
    The convention extends to reviews; multiple Decisions sections per file are allowed (stacked plans),
    numbering never resets within a file.
17. Intents whose plans were all rejected: decisions marked obsolete. Unplanned intents: decisions marked `intended`.
18. The "Marking options" convention is revised: every resolved option produces a Decisions list entry.
19. The grouping term is "entry", not "PR" (collides with `reviews/pr` and the `pr` command).
20. Candidates for indexing = indexable files not mentioned in the index + files changed since the recorded hash.
21. The recorded hash is the `main` commit the review branch was cut from; the branch is merged ff or merge-commit;
    in oob the diff runs in the plans repo.
22. Index compression (collapsing superseded/obsolete entries) is deferred until the index has been used in practice.
23. How and when decisions are recorded goes into the agent system prompts (manual edit by the human);
    the plan carries the draft text.

## Draft: system prompt section on recording decisions

Portable text for the harness system prompts (manual edit). Format conventions also appear in the
generated AGENTS.md section; this text covers behavior, so it does not depend on a project's `intent/001`.

```markdown
## Recording decisions

- Record a decision when the user answers a question, picks between options, or resolves a critique point.
  Record it in the plan (or review) file right away, not only in chat.
- Record only non-trivial decisions that are likely to matter outside this file.
  Do not log every small choice you made yourself.
- Use one numbered `## Decisions` list per file. Each item: the decision and a one-line reason.
  Reviews organized per plan may have several Decisions sections; numbering continues across the file.
- The list is append-only: never renumber. Amend in place and mark it amended;
  a withdrawn decision stays, marked withdrawn with the reason.
- When presenting open options, mark the optional sections and recommend one.
  Once chosen, add a Decisions entry, and collapse each discarded option into a short "would have" summary
  plus the reason it was discarded.
- Refer to decisions as `{identifier} Decision {n}` (see AGENTS.md); resolve legacy references through the index.
```

## Considered alternatives

### Agent-managed section in `intent/001`

Instead of having a separate file, this content could be a section of the idea intent.
Cons: intents are supposed to be (primarily) human-written, and this would make a violation of that standard process.

### Structured data

Instead of a markdown file, store the data as json or yaml.
Con: json is less readable for humans.
The file should not be parsed, so there is little benefit of making it structured.

### Summary line convention in plans

The plan summary section could be omitted by introducing a different convention:
- either have a summary on a fixed line (title, blank, summary), or
- use a greppable prefix for the summary line

A list of plan summaries could then be dynamically created with head/grep of all files under docs/plans.
Con: Does not act as tracking of processed files.
