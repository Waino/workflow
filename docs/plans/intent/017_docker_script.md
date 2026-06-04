# Shell script to launch dockerized agents

The Docker commands are separated into a shell script `run_agent_in_docker.sh`.
The script is expected to be installed
- in the project root directory
- or if not there, then fall back to the $PATH.

The container runs detached.
Agents run in separate docker execs, which are attached.

## Script: `run_agent_in_docker.sh`

### Mandatory arguments

- PROJECT: Project name
- AGENT: Agent name

### Options

`--project-dir <path>`: Absolute path to the project root directory
    - Required; passed by `workflow start`
`--model <id>`: Model id to use in agent
    - Default: leave model choice to the agent CLI
`--reset`: Remove existing container instead of restarting
`--shell`: Launch a bash shell instead of the agent CLI

### Derived variables

- CONTAINER: `auggie-${PROJECT}-${AGENT}`

### Steps

1. Validate inputs
2. Determine the state of the container named `${CONTAINER}`.
3. If `--reset` and container exists: remove it.
4. Depending on the state:
    - Already running: do nothing.
    - Stopped: start the container.
    - Does not exist: run the container.
5. If `--shell`:
    - Docker exec bash:
    ```bash
    docker exec -it \
        --user auggie \
        ${CONTAINER} \
        bash
    ```
6. Otherwise:
    - Docker exec the agent:
    ```bash
    docker exec -it \
        --user auggie \
        ${CONTAINER} \
        auggie --model ${MODEL}
    ```

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

## Python Integration: `workflow start`

`workflow start` (outside the sandbox) will handle the tmux orchestration and call the script.

- New option: `--reset` (passed through to the script for agent windows only, not builder-shell)
- Finding the script:
  - Check `project_dir / "run_agent_in_docker.sh"` first.
  - If it exists, use that absolute path.
  - Else, use just `run_agent_in_docker.sh` (rely on `PATH`).
- For each configured agent:
  - `<script> ${PROJECT} ${AGENT} --project-dir <abs_project_dir> --model ${agent.model} [--reset]`
- For the `builder-shell` window:
  - `<script> ${PROJECT} builder --project-dir <abs_project_dir> --shell`
  - `--reset` is **not** passed here; the builder's regular agent window handles any reset.

## Reasoning for separate shell script

The Docker commands should be customizable for each project.
I don't want to add complexity to `workflow` by incorporating docker settings into the config.
Instead of having multiple custom scripts (run and exec),
let's draw the boundary at bringing up the containerized agent regardless of docker status,
assigning responsibility for docker handling to the script.

## Design decisions from critique

The following decisions were made when reviewing the initial design:

- **Volume path**: `workflow start` passes the project root as `--project-dir <abs_path>`
  to the script. The script uses `${PROJECT_DIR}/projects/${PROJECT}/${AGENT}` for the
  volume mount. This is correct whether the script is found in the project root or on
  `$PATH`.
- **`env.list` validation**: the script should fail early with a clear message if
  `env.list` is not found in the project root.
- **User ID**: accepted as a known limitation; the container image is expected to be built
  with a matching `auggie` UID. Out of scope for this plan.
- **Hardcoded image**: accepted as a known limitation for now. Image override is out of scope.
- **No TTY in non-interactive context**: accepted; the `-it` flags are intentional for
  tmux use and the script is not designed for CI.

## Open questions

- How best to ensure that `.venv/bin/activate` is sourced before running the agent?
  - Proposal: the container's entrypoint or the image's shell profile should handle this.
