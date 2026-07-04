# Project Guidelines

Always start by reading docs/plans/intent/001_idea.md

## Code Style
- black
- Maximum line length 120.
- Always use type annotations.
- Follow the existing naming conventions in the codebase.
- Keep it simple and concise. Avoid intoducing unnecessary complexity. Instead favor short, simple, and readable solutions. Find a good balance between DRY (Don't repeat yourself) and avoidance of overengineering.
- Write testable code. If it is possible to extract logic into a pure function, prefer refactoring to use the pure function instead of mixing logic with side effects.

## Git usage
- Commit your changes, but don't push.
- Use semantic commit messages following conventional commits.
- Use feature branches, stop and let me know when a feature is ready to be merged.

## Python development
- Use uv for virtual environment and dependency management.
- Use flit for python packaging.
- There is a git pre commit hook in the file .git-pre-commit-hook. Install it by symlinking to `.git/hooks/pre-commit` and use it. It runs black, pytest, and mypy. These three should always run successfully for each commit. Note that initially running `pytest` has been temporarily disabled. Enable it once the first test has been written.
- DO NOT use `git commit --no-verify`. Stop and ask for instructions instead of omitting the pre-commit hook.

## Sandboxed environment
- Note that you run in a sandboxed and firewalled environment.
- Your Internet use is restricted: don't try to download or upload anything.
- You can install Python packages from pypi, but you can not download or install CLI tools.
- If it is necessary for completing the work, you can stop and propose other tools to install. Do not try to install anything directly.
- Note that you are not allowed to use Docker commands.

## Documentation
- Write user documentation in the `docs` subdirectory, in ReStructuredText format.
- Update documentation when adding new features.

<!-- WORKFLOW BEGIN -->
The workflow below is enacted by the human user. Agents do not invoke `workflow`
subcommands themselves; they receive rendered prompts in the chat via tmux, appearing as ordinary user messages.
Script subcommands are not visible to the agents.
Manual steps are free-form chat, possibly multiple turns, or omitted entirely.
This listing exists so an agent knows in advance which state a plan is in,
which actions are expected in that state, and which actions belong to other states.
For example, an agent reading a plan in the `planning` state must not start writing code;
an agent in the `implementing` state must not rewrite the plan.

The user may deviate from the workflow at any time.
A prompt command may therefore arrive in a state where it is not listed below
(for example, `ask` may be sent in any state).
When this happens, follow the prompt as given.

Plans reside in `docs/plans`.
The plan state is defined by location, e.g. `docs/plans/implementing/002_foo.md` is in the state `implementing`.
Plan names consist of an unique sequece number, a slug, and the `.md` suffix.
Reviews (in docs/plans/reviews) do not follow this naming scheme.

### intent (agent: planner)
Plans being explored.
Inbound: (none)
Outbound: plan -> planning [prompt], subdivide -> planning [prompt]
Commands:
- [script] new — Create a new intent file.
- [script] push — Push pending content to canon.
- [prompt] ask — Read the plan; ask clarifying questions.
- [manual] answer — Human answers questions in chat.
- [prompt] critique — Critique the plan.
- [manual] revise — Human revises the plan.
- [prompt] plan -> planning — Write a detailed plan from an intent.
- [prompt] subdivide -> planning — Break the plan into smaller planning documents.

Plans reside in `docs/plans`.
The plan state is defined by location, e.g. `docs/plans/implementing/002_foo.md` is in the state `implementing`.
Plan names consist of an unique sequece number, a slug, and the `.md` suffix.
Reviews (in docs/plans/reviews) do not follow this naming scheme.

### planning (agent: planner)
Plans being drafted.
Inbound: plan from intent [prompt], subdivide from intent [prompt]
Outbound: todo -> todo [script]
Commands:
- [manual] adjust — Human edits the plan directly.
- [prompt] critique — Critique the plan.
- [manual] revise — Human revises the plan.
- [script] todo -> todo — Approve for implementation.

Plans reside in `docs/plans`.
The plan state is defined by location, e.g. `docs/plans/implementing/002_foo.md` is in the state `implementing`.
Plan names consist of an unique sequece number, a slug, and the `.md` suffix.
Reviews (in docs/plans/reviews) do not follow this naming scheme.

### todo (agent: human operator)
Plans approved for implementation.
Inbound: todo from planning [script]
Outbound: implement -> implementing [script]
Commands:
- [script] implement -> implementing — Start implementation.
- [script] push — Push pending content to canon.

Plans reside in `docs/plans`.
The plan state is defined by location, e.g. `docs/plans/implementing/002_foo.md` is in the state `implementing`.
Plan names consist of an unique sequece number, a slug, and the `.md` suffix.
Reviews (in docs/plans/reviews) do not follow this naming scheme.

### implementing (agent: builder)
Plans under active implementation.
Inbound: implement from todo [script]
Outbound: check -> checking [script]
Commands:
- [prompt] ask — Read the plan; ask clarifying questions.
- [manual] answer — Human answers questions in chat.
- [prompt] build — Implement the plan.
- [script] check -> checking — Run verification checks and hand off.

Plans reside in `docs/plans`.
The plan state is defined by location, e.g. `docs/plans/implementing/002_foo.md` is in the state `implementing`.
Plan names consist of an unique sequece number, a slug, and the `.md` suffix.
Reviews (in docs/plans/reviews) do not follow this naming scheme.

### checking (agent: reviewer)
Implementation complete, review in progress.
Inbound: check from implementing [script]
Outbound: accept -> done [script], reject -> rejected [script]
Commands:
- [manual] review — Human reviews the proposed change.
- [prompt] pr — Write a thorough code review.
- [manual] adjust — Human edits the review or code directly.
- [prompt] fix — Fix issues from the most recent review.
- [script] validate — Run verification checks.
- [script] accept -> done — Accept and merge to main.
- [script] reject -> rejected — Close out without merging.

Plans reside in `docs/plans`.
The plan state is defined by location, e.g. `docs/plans/implementing/002_foo.md` is in the state `implementing`.
Plan names consist of an unique sequece number, a slug, and the `.md` suffix.
Reviews (in docs/plans/reviews) do not follow this naming scheme.

### done (agent: human operator)
Merged to main. Implementation and review complete.
Inbound: accept from checking [script]
Outbound: (none)
Commands: (none)

Plans reside in `docs/plans`.
The plan state is defined by location, e.g. `docs/plans/implementing/002_foo.md` is in the state `implementing`.
Plan names consist of an unique sequece number, a slug, and the `.md` suffix.
Reviews (in docs/plans/reviews) do not follow this naming scheme.

### rejected (agent: human operator)
Will not be implemented.
Inbound: reject from checking [script]
Outbound: (none)
Commands: (none)

Plans reside in `docs/plans`.
The plan state is defined by location, e.g. `docs/plans/implementing/002_foo.md` is in the state `implementing`.
Plan names consist of an unique sequece number, a slug, and the `.md` suffix.
Reviews (in docs/plans/reviews) do not follow this naming scheme.

### review (agent: reviewer)
Special state for all reviews. Commands are codebase-wide review and audit tasks unrelated to a specific plan.
Inbound: (none)
Outbound: (none)
Commands:
- [prompt] full review — Perform a full review of the current codebase.
- [prompt] followthrough — Compare done plans against the codebase for unimplemented features.
- [prompt] refresh — Update planning/todo plans against decisions made since they were written.
<!-- WORKFLOW END -->
