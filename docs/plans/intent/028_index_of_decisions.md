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

The index records the commit hash of the last indexing pass (Decision 3).

### Indexed files

- Plans in `done`, all intents, and reviews (Decision 4).
- Excluded: plans in-flight (`planning`, `todo`, `implementing`, `checking`) and `rejected`,
  and reviews of excluded plans. Codebase-wide reviews (full, followthrough) are always included.

### Plan summary section

Each entry in this section represents one PR, containing the following fields
- One line summary of plan contents
- List of related files: intent, ticket, reviews
    - Also acts to track which plans have been indexed: a file not mentioned needs to be added in the next pass
    - Note that tickets should be listed without the state, as that would become stale quickly

"PR" collapses an intent, its plan(s), and their reviews into one entry,
because their summaries would be (nearly) identical, and a review is best summarized by the reviewed change.
For subplans and stacked plans, the reviewer decides when they need an entry of their own.
Rule of thumb: entries with the same summary go together.

### Decision summary section

Each entry in this section represents one decision, containing the following fields
- Identifier for the file it is found in
    - `intent/{number}_{slug}`
    - `{number}_{slug}` (no state)
    - `reviews/{type}/{filename}`
- Status
    - current: Still relevant. Either currently reflected in code or intended to become that.
    - superseded: A later decision overrides this. A different path was taken.
    - obsolete: This decision pertains to some component that was removed. A different path was not taken: the path is gone.
- One line summary of decision

Only explicit decisions are indexed. They should be non-trivial and likely to be relevant outside the plan itself.
Logging every small decision the agent made by itself would be counterproductive.
Something that needed to be asked from the user very likely counts
(but beware of questions whose answer was already in the plan).

Superseded and obsolete decisions are kept in the index, so an agent reading an old plan can tell
whether a decision still holds. Superseded entries reference the superseding decision.

### Deferred work section

Deferred, out-of-scope, and future work items. Fields:
- Identifier of the file (same format as above)
- Status
    - open: still deferred.
    - dropped: decided not to do it.
- One line summary

Items that were deferred but have since been completed are removed: they no longer need tracking.
Carve-out: work deferred to a later part of the same subdivide chain is omitted.

### Terminology section

Only architecturally important terms: a term coined or given a specific meaning in a plan,
and used across several plans or in code identifiers (e.g. actor, repo vs agent, oob, canon, stack).
Avoid devolving into a list of all names in the project. Fields:
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
The exact text TBD.

## Related change: how to unambiguously refer to decisions

Decisions should be referred to as `{identifier} Decision {number_in_file}`,
where identifier is the same as used in the decision summary section.

Decisions are marked in plans as a single numbered `## Decisions` list per file.

Legacy files are not edited to conform. Their decisions are count-based: explicit decision items
(across all decision sections and inline `Decision:` sentences) are numbered in file order,
e.g. 014 gets one sequence across its four Decisions sections.

Both conventions (marking and referring) go into the generated part of AGENTS.md,
not into the agent system prompts (in the harness, outside this project) nor the per-command prompts in
`docs/default_config.yaml` (they would have to be repeated across many commands).

Identifier details: the `.md` suffix is dropped for all identifiers, including reviews.
Dotted plan numbers stay as they are (e.g. `023.4_init_oob_and_bootstrap`).

## Special process for this ticket

Planner will help to improve this file by critiqueing it.
Builder: generated AGENTS.md section (Agent instructions, decision conventions),
and the `index_decisions` command with placeholder prompt text.
Reviewer will
- Read all plans in the current project.
- Create the index file for the current project.
- Based on learnings from doing that.
    - Propose changes to the format of the index file.
    - Formulate the final update prompt text and add the prompt command.

## Decisions

1. Decisions are marked as a single numbered list per file; legacy files are count-based and not edited.
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
