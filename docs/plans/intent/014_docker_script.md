# Shell script to launch dockerized agents

The docker commands are separated into a shell script `run_agent_in_docker.sh`.
The script is expected to be installed on the PATH.

The container runs detached.
Agents run in separate docker execs, which are attached.

## Mandatory arguments

- PROJECT: Project name
- AGENT: Agent name

## Options

`--model`: Model id to use in agent
    - Default: leave model choice to the agent CLI
`--reset`: Remove existing container instead of restarting

## Derived variables

- CONTAINER: `auggie-${PROJECT}-${AGENT}`

## Steps


1. Determine the state of the container named `${CONTAINER}`.
2. If `--reset` and container exists: remove it.
3. Depending on the state:
    - Already running: do nothing.
    - Stopped: start the container.
    - Does not exist: run the container.
4. Docker exec the agent, with the specified model.
