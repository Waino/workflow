#!/usr/bin/env bash
# Example run_agent_in_docker.sh
# Copy this file to the project root (or anywhere on PATH) and customise it.
#
# Usage: run_agent_in_docker.sh PROJECT AGENT --project-dir DIR [--model MODEL] [--reset] [--shell] [--venv]
#
# PROJECT       - project name (used in container name and volume path)
# AGENT         - agent name  (used in container name and volume path)
# --project-dir - absolute path to the project root directory (passed by `workflow start`)
# --model       - model id forwarded to the agent CLI (required for agent windows)
# --reset       - remove the existing container before starting a fresh one
# --shell       - open an interactive bash shell instead of running the agent CLI
# --venv        - create a virtual env instead of running the agent CLI

set -euo pipefail

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
usage() {
    echo "Usage: $(basename "$0") PROJECT AGENT --project-dir DIR [--model MODEL] [--reset] [--shell] [--venv]" >&2
    exit 1
}

[[ $# -ge 2 ]] || usage

PROJECT="$1"
AGENT="$2"
shift 2

PROJECT_DIR=""
MODEL=""
RESET=false
SHELL_MODE=false
VENV_MODE=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --project-dir)
            [[ $# -ge 2 ]] || { echo "Error: --project-dir requires an argument" >&2; exit 1; }
            PROJECT_DIR="$2"
            shift 2
            ;;
        --model)
            [[ $# -ge 2 ]] || { echo "Error: --model requires an argument" >&2; exit 1; }
            MODEL="$2"
            shift 2
            ;;
        --reset)
            RESET=true
            shift
            ;;
        --shell)
            SHELL_MODE=true
            shift
            ;;
        --venv)
            VENV_MODE=true
            shift
            ;;
        *)
            echo "Error: unknown option: $1" >&2
            usage
            ;;
    esac
done

# ---------------------------------------------------------------------------
# Validate inputs
# ---------------------------------------------------------------------------
[[ -n "$PROJECT" ]]     || { echo "Error: PROJECT must not be empty" >&2; exit 1; }
[[ -n "$AGENT" ]]       || { echo "Error: AGENT must not be empty" >&2; exit 1; }
[[ -n "$PROJECT_DIR" ]] || { echo "Error: --project-dir is required" >&2; exit 1; }
[[ -d "$PROJECT_DIR" ]] || { echo "Error: project dir does not exist: ${PROJECT_DIR}" >&2; exit 1; }

ENV_LIST="${PROJECT_DIR}/env.list"
if [[ ! -f "$ENV_LIST" ]]; then
    echo "Error: env.list not found at ${ENV_LIST}" >&2
    echo "       Create it and add the required environment variables (API keys, etc.)." >&2
    exit 1
fi

if [[ "$SHELL_MODE" == true && "$VENV_MODE" == true ]]; then
    echo "Error: Can not use both --shell and --venv"
    exit 1
fi

# ---------------------------------------------------------------------------
# Derived variables
# ---------------------------------------------------------------------------
CONTAINER="pi-${PROJECT}-${AGENT}"

# ---------------------------------------------------------------------------
# Reset: remove the existing container if requested
# ---------------------------------------------------------------------------
if [[ "$RESET" == true && "$SHELL_MODE" != true ]]; then
    if docker inspect "$CONTAINER" &>/dev/null; then
        echo "Removing container ${CONTAINER}..."
        docker rm -f "$CONTAINER"
    fi
fi

# ---------------------------------------------------------------------------
# Bring the container up (run / start / no-op depending on state)
# ---------------------------------------------------------------------------
if [[ "$SHELL_MODE" == true ]]; then
    # Wait for container to come up (brought up by starting agent)
    sleep 8
fi

if docker inspect "$CONTAINER" &>/dev/null; then
    CONTAINER_STATUS="$(docker inspect --format '{{.State.Status}}' "$CONTAINER")"
else
    CONTAINER_STATUS="absent"
fi

case "$CONTAINER_STATUS" in
    running)
        # Nothing to do; the exec below will attach to the running container.
        ;;
    exited|created|paused)
        echo "Starting stopped container ${CONTAINER}..."
        docker start "$CONTAINER"
        ;;
    absent)
        if [[ "$SHELL_MODE" == true ]]; then
            echo "Error: Shell mode expects container $CONTAINER to exist"
            exit 1
        else
            echo "Creating container ${CONTAINER}..."
            # NOTE: Add any additional --env flags here for secrets that should not
            # live in env.list (e.g. per-agent API keys):
            #   --env ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY}" \
            docker run \
                --name "$CONTAINER" \
                --env-file "$ENV_LIST" \
                --env PROJECT="$PROJECT" \
                --env AGENT="$AGENT" \
                -v "${PROJECT_DIR}/${AGENT}:/home/auggie/projects/${PROJECT}/${AGENT}" \
                --workdir "/home/auggie/projects/${PROJECT}" \
                --detach \
                gronsti-pi \
                sleep infinity
        fi
        ;;
    *)
        echo "Error: container ${CONTAINER} is in unexpected state: ${CONTAINER_STATUS}" >&2
        exit 1
        ;;
esac

# ---------------------------------------------------------------------------
# Exec into the container
# ---------------------------------------------------------------------------
if [[ "$SHELL_MODE" == true ]]; then
    exec docker exec -it \
        --user auggie \
        --workdir "/home/auggie/projects/${PROJECT}/${AGENT}" \
        "$CONTAINER" \
        bash
elif [[ "$VENV_MODE" == true ]]; then
    [[ -d "${PROJECT_DIR}/${AGENT}/.venv" ]] && rm -r "${PROJECT_DIR}/${AGENT}/.venv"
    exec docker exec -it \
        --user auggie \
        --workdir "/home/auggie/projects/${PROJECT}/${AGENT}" \
        --env UV_VENV_CLEAR=1 \
        --env UV_LINK_MODE=copy \
        "$CONTAINER" \
        bash -c "uv venv --seed && source .venv/bin/activate && uv pip install -e ."
else
    EXEC_CMD=(docker exec -it --user auggie --workdir "/home/auggie/projects/${PROJECT}/${AGENT}" "$CONTAINER" pi)
    [[ -n "$MODEL" ]] && EXEC_CMD+=(--model "$MODEL")
    exec "${EXEC_CMD[@]}"
fi
