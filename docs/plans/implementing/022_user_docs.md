# 022: User documentation

Input: None (user-written)

## Goal

Write user documentation in the `docs` directory.
Keep documentation brief.

The software is used on the command line, not as a library.
The audience is users, not developers.
Only document Python APIs if necessary for clarity, and omit any private non-user-facing parts.

There is a stub documentation in `docs/source`, which should be expanded.
Use the restructured text format.

## The workflow

Document the workflow: philosophy, participating agents, plan states, transitions.
Base the documentation on docs/plans/intent/001_idea.md and docs/default_config.yaml

## CLI

Document all CLI commands.
Note that prompt commands are configurable: document the pattern, rather than the commands in the default config.

## The docker script

Document run_agent_in_docker.sh (see docs/example_run_agent_in_docker.sh).
The documentation should cover usage and points where customization is possible (mainly `docker run` commands).

## Configurable vs requiring code changes

Separate changes that can be made purely through configuration
(e.g. modify prompts, add/remove prompt commands, add/remove manual steps, ...)
from changes that require a corresponding code change
(e.g. add/remove agents, add/remove plan states, add/remove script commands, ...).

Note that the software is designed for my workflow:
recommend that users wanting to modify the workflow fork the repo and instruct their agent(s) to implement the modifications.

## Caveats and limitations

- Plans are tracked in the same repo as the source code (an out-of-band mode is on the roadmap, but not implemented).
- The number of agents is limited on purpose. Tokenmaxxers are advised to look elsewhere.
- Collect other caveats and limitations from docs/plans/intent/001_idea.md, other plans, and the CLI help texts.
