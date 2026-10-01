# 028: Index Of Decisions

## Goal

Improve the discoverability of architecture and design decisions.

Currently, decisions are made during planning or review, and noted only in individual plans.
There is a risk that a later planning step fails to discover the relevant decision,
as finding it is based on only the plan slug and keywords that planner searches for.

## Index file format

The index file is located at `docs/plans/index_of_decisions.md`.
In a fresh project, it does not exist. The file is created on the first run of the update prompt.

### Plan summary section

Each entry in this section represents one PR, containing the following fields
- One line summary of plan contents
- List of related files: intent, ticket, reviews
    - Also acts to track which plans have been indexed: a file not mentioned needs to be added in the next pass
    - Note that tickets should be listed without the state, as that would become stale quickly

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

## Agent instructions

All agents will receive a new instruction to read the index file if it exists.
The instruction comes via AGENTS.md, directly after the instruction to read `intent/001`.

## Update prompt

A prompt command `index_decisions` should be added to reviewer.
The gist of it is that the agent should read all plans not already mentioned in the index,
and update the index file with decisions collected from them.
If the index file doesn't already exist, it should be created.
The exact text TBD.

## Related change: how to unambiguously refer to decisions

Decisions should be referred to as `{identifier} Decision {number_in_file}`,
where identifier is the same as used in the decision summary section.
Instructions to do this should be added to the agent system prompts.

## Special process for this ticket

Planner will help to improve this file by critiqueing it.
There will be nothing for builder to to. (It could at most add a command with placeholder text.)
Reviewer will
- Read all plans in the current project.
- Create the index file for the current project.
- Based on learnings from doing that.
    - Propose changes to the format of the index file.
    - Formulate the final update prompt text and add the prompt command.

## Open questions

- Should superseded and obsolete decisions be listed in the index, or only current ones?
- Is there some other content in the plan files that would make sense to index?
- Is reviewer or planner a better fit?
- Timestamp-based detection of unprocessed files. Does it play well with in-flight plans?

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
