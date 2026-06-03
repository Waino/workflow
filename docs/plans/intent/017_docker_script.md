# Shell script to launch dockerized agents

The Docker commands are separated into a shell script `run_agent_in_docker.sh`.
The script is expected to be installed
- in the project root directory
- or if not there, then fall back to the $PATH.

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

1. Validate inputs
2. Determine the state of the container named `${CONTAINER}`.
3. If `--reset` and container exists: remove it.
4. Depending on the state:
    - Already running: do nothing.
    - Stopped: start the container.
    - Does not exist: run the container.
5. Docker exec the agent, with the specified model.

## Docker run

Basic implementation of the docker run command:

```bash
docker run \
    --name ${CONTAINER} \
    --env-file env.list \
    --env PROJECT=${PROJECT} \
    --env AGENT=${AGENT} \
    -v $(pwd)/projects/${PROJECT}/${AGENT}:/home/auggie/projects/${PROJECT}/${AGENT} \
    --detach \
    gronsti-auggie
```

## Docker exec

Basic implementation of the docker exec command:

```bash
docker exec -it \
    --user auggie \
    auggie \
    --model ${MODEL}
```

## Reasoning for separate shell script

The Docker commands should be customizable for each project.
I don't want to add complexity to `workflow` by incorporating docker settings into the config.
Instead of having multiple custom scripts (run and exec),
let's draw the boundary at bringing up the containerized agent regardless of docker status,
assigning responsibility for docker handling to the script.

## Open questions

- How best to ensure that `.venv/bin/activate` is sourced before running the agent?
