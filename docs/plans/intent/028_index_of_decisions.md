# 028: Index Of Decisions

## Goal

Improve the discoverability of architecture and design decisions.

Currently, decisions are made during planning or review, and noted only in individual plans.
There is a risk that a later planning step fails to discover the relevant decision,
as finding it is based on only the plan slug and keywords that planner searches for.

Goals of the index:
- Reduce the risk of the planner missing relevant decisions because it only reads a limited subset of prior plans.
- Reduce duplication and inconsistency.

Non-goals:
- Pinning down behavior against accidental change or deletion: unit tests serve that function.
- Removing the need to read prior plans: the index helps decide what to read.

## Index file format

The index file is located at `index_of_decisions.md` in the plans root
(`docs/plans/` in-band, `plans/` out-of-band; derived via `layout`).
In a fresh project, it does not exist. The file is created on the first run of the update prompt.

The index records the commit hash of the last indexing pass (Decision 3, 21),
and the commit hash of the last followthrough pass.

The file is also the single place for the human to review the current set of decisions;
the user docs recommend reviewing it periodically (Decision 11).

Decisions, deferred work, and terms are grouped under topic headings (e.g. git sync, plan resolution, oob layout)
chosen by the reviewer, with the file identifier as a field. Each pass may merge or rename headings (Decision 10).

### Indexed files

- Plans in `done` and all intents (Decision 4, 24).
- Excluded: plans in-flight (`planning`, `todo`, `implementing`, `checking`) and `rejected`.
- Reviews are not read: decisions made during `fix` are recorded in the plan (Decision 24).
- Intents (Decision 17):
    - all of whose plans were rejected: their decisions are marked obsolete.
    - with no plan yet: their decisions are marked `intended`.

### Plan summary section

Each entry in this section represents one change, containing the following fields
- One line summary of plan contents
- List of related files: intent, tickets
    - Also acts to track which plans have been indexed: a file not covered by any entry
      (as head, in the related files, or in a subplan range) needs to be added in the next pass (Decision 31).
    - Note that tickets should be listed without the state, as that would become stale quickly
    - Subplans may be listed as a range, e.g. "Subplans 023.1–023.6" (Decision 29).

An entry collapses an intent and its plan(s), because their summaries would be (nearly) identical.
The entry head is the top-level plan if one exists, else the intent (Decision 29).
For subplans and stacked plans, the reviewer decides when they need an entry of their own.
Rule of thumb: plans with the same summary go together.

### Decision summary section

Each entry in this section represents one decision, containing the following fields
- Identifier for the file it is found in
    - `intent/{number}_{slug}`
    - `{number}_{slug}` (no state)
- Decision number in the file
    - Legacy files additionally carry a locator: section heading plus a short quote.
      The index is the authority for legacy numbers (Decision 12).
    - Legacy numbering is count-based (Decision 26): explicit decision items are numbered in file order across
      every decision-like list (Decisions, Design decisions, Clarified decisions, Clarifications,
      PR-/post-review decisions) and inline `Decision:` sentences; a file's own labels go into the locator.
    - `D–` marks an implicit legacy design choice with no decision marker, indexed by locator only (Decision 27).
- Status
    - current: Still relevant. Either currently reflected in code or intended to become that.
    - intended: From an intent that has no plan yet.
    - superseded: A later decision overrides this. A different path was taken.
    - obsolete: This decision pertains to some component that was removed. A different path was not taken: the path is gone.
- One line summary of decision (Decision 33); a decision that only narrows an earlier one
  keeps `current` and says "narrowed by …" (Decision 30).

Extracting decisions is not a mechanical process; reviewer chooses which decisions are worth indexing.
The rules are heuristic and may be contradictory: the reviewer makes the call.
Not indexing a decision at all (pruning) is part of every pass; a pruned decision stays unindexed,
since later passes skip the file. Compressing already-indexed entries is a separate, deferred action (Decision 34).

Only explicit decisions are indexed. They should be non-trivial and likely to be relevant outside the plan itself.
Legacy files are a reviewer-discretionary exception: load-bearing implicit choices may be indexed as `D–` (Decision 27).
Logging every small decision the agent made by itself would be counterproductive.
Something that needed to be asked from the user is more likely to count
(but beware of questions whose answer was already in the plan).
Rejected proposals can also contain something to note
(e.g. rejecting an integration test to form an intentionally accepted test gap).

Never index the following:
- Decisions about sequencing and scoping within a plan or sequence of subdivided plans.
- The decision to accept or reject a proposal. The proposal itself may contain decisions worth indexing.
- Decisions to add particular tests. The existence of the test is enough.

Further rules (Decision 35):
- Decisions in the obvious location (e.g. the plan where a subsystem was added) must pass a higher bar for impact.
  Keep decisions in less obvious locations (follow-ups, bug fixes, arbitrary later plans) even if less impactful.
- Omit structural decisions like "Foo is a subclass of Bar" or "Foo carries the field baz",
  unless architecturally load-bearing.
- Keep surprising definitions, omit the obvious.
- Keep "never do X" decisions: negatives are hard to establish with code search.

When an intent and its plan restate the same decisions, only the earliest statement is indexed;
a later file is indexed only where it changes or adds something (Decision 28).

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
The indexer only adds items, skipping those it can see are already done;
the `followthrough` prompt prunes completed ones, as completion detection needs code inspection (Decision 14, 32).
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
plus those changed since the recorded hash
(`git -C {{ plans_root_in_worktree }} diff --name-only --relative <hash>..HEAD -- .`),
and update the index file with decisions collected from them.
If the index file doesn't already exist, it should be created.
Both halves of the candidate rule are needed: "not mentioned" catches new files, the hash catches modified ones
(Decision 20).

Hash rules (Decision 21):
- Record the `main` commit the review branch was cut from, so it is in `main`'s history.
- Prompts get the template variable `plans_root_in_worktree` (`docs/plans` in both modes, derived from `layout`);
  running git with `-C` on it works in both modes, as git finds the enclosing repo (Decision 36).

The exact text TBD.

## Related change: how to unambiguously refer to decisions

Decisions should be referred to as `{identifier} Decision {number_in_file}`,
where identifier is the same as used in the decision summary section.

Decisions are marked as a single numbered `## Decisions` list per file.
Decision-like sections (e.g. "Clarifications", "Post-review fixes") are not created; their items go under Decisions
(Decision 25). Decisions made while fixing review findings go into the plan's list, not the review (Decision 24).

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

Identifier details: the `.md` suffix is dropped for all identifiers.
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
   (The amendment by Decision 16 is withdrawn with it.)
2. Only explicit, non-trivial decisions likely relevant outside their own plan are indexed.
3. Detection of unprocessed files: record the commit hash of the last pass in the index (not timestamps),
   so files modified after being indexed are also picked up.
4. Indexed: `done` plans, all intents, reviews. Excluded: in-flight and `rejected` plans
   (the consequences of rejected decisions were rejected), and reviews of excluded plans.
   (Amended by Decision 24: reviews are no longer indexed.)
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
16. Withdrawn (replaced by Decision 24): Reviews: only explicit Decisions lists are indexed (decisions made
    during `fix`). The convention extends to reviews; multiple Decisions sections per file are allowed
    (stacked plans), numbering never resets within a file.
17. Intents whose plans were all rejected: decisions marked obsolete. Unplanned intents: decisions marked `intended`.
18. The "Marking options" convention is revised: every resolved option produces a Decisions list entry.
19. The plan grouping term is "entry", not "PR" (collides with `reviews/pr` and the `pr` command).
20. Candidates for indexing = indexable files not mentioned in the index + files changed since the recorded hash.
21. The recorded hash is the `main` commit the review branch was cut from; in oob the diff runs in the plans repo.
    (Amended: the merge-method restriction is dropped, since a `main` commit survives any merge method.)
22. Index compression (collapsing superseded/obsolete entries) is deferred until the index has been used in practice.
    (Clarified by Decision 34: this is compression, not pruning.)
23. How and when decisions are recorded goes into the agent system prompts (manual edit by the human);
    the plan carries the draft text.
24. Decisions made while fixing review findings are recorded in the plan, not the review. Reviews are therefore
    not read when indexing, not tracked in Plan summaries, and have no identifier format; the multi-section
    numbering rule is dropped. Reason: legacy practice already did this, and it keeps indexing to plans and intents.
25. No decision-like sections ("Clarifications", "Post-review fixes"): their items go under Decisions.
    Reason: legacy files used seven different headings, which made counting a judgement call.
26. Legacy numbering is count-based across all decision-like lists and inline `Decision:` sentences, in file order;
    a file's own labels go into the locator (the rule in the index header).
27. Legacy files get a reviewer-discretionary exception from "explicit only": load-bearing implicit choices are
    indexed as `D–` plus locator. Reason: plans 002–008 hold key choices but have no Decisions sections.
28. When an intent and its plan restate the same decisions, only the earliest statement is indexed;
    a later file only where it changes or adds something.
29. A Plan summaries entry is headed by the top-level plan if one exists, else the intent;
    subplans may be listed as a range ("Subplans 023.1–023.6").
30. A decision that narrows rather than replaces an earlier one gets no new status: the earlier stays `current`
    and its summary says "narrowed by …".
31. A file counts as indexed iff a Plan summaries entry covers it (head, related files, or subplan range).
32. The indexer skips deferred items it can see are already done; `followthrough` remains the authority for pruning.
33. Each index entry is one summary line plus its locator.
34. Pruning (choosing not to index a decision; it then stays unindexed) is part of every pass.
    Compression (rewriting indexed entries into a more compact, no longer 1:1 form) is postponed until needed.
35. Added pruning rules: higher bar in the obvious location, keep decisions in unobvious locations;
    omit structural "subclass of" / "carries field" decisions unless load-bearing;
    keep surprising definitions, omit obvious ones; keep "never do X" decisions.
36. Prompts get a `plans_root_in_worktree` template variable derived from `layout`, replacing hardcoded
    `docs/plans`; git runs with `-C {{ plans_root_in_worktree }}` so the same command works in both modes.
37. Prompt git metadata (`{{ branch }}`, `{{ commit }}`) is read after the recipient worktree is synced,
    so a review is named after the commit the agent actually sees (review fix; previously read before the pull).

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
  Decisions made while fixing review findings go into the plan, not the review.
- The list is append-only: never renumber. Amend in place and mark it amended;
  a withdrawn decision stays, marked withdrawn with the reason.
- When presenting open options, mark the optional sections and recommend one.
  Once chosen, add a Decisions entry, and collapse each discarded option into a short "would have" summary
  plus the reason it was discarded.
- Refer to decisions as `{identifier} Decision {n}` (see AGENTS.md); resolve legacy references through the index.
  Use this format in plans, code, and chat.
```

## Rejected alternatives

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
