#!/usr/bin/env bash
# Example run_agent_in_docker.sh
# Copy this file to the project root (or anywhere on PATH) and customise it.
#
# Usage: run_agent_in_docker.sh SUBCOMMAND PROJECT REPO --project-dir DIR [--model MODEL] [--reset] [--repo NAME]
#
# SUBCOMMAND    - agent: run the agent CLI in a persistent sandbox container
#                 venv:  (re)create the repo's virtual env
#                        (agent -> persistent container; canon -> throwaway container)
#                 shell: open an interactive bash shell in an existing persistent container
#                 hook:  run the pre-commit hook in a throwaway container
# PROJECT       - project name (used in container name and volume path)
# REPO          - agent name (agent/shell); agent name or 'canon' (venv); builder/reviewer/canon (hook)
# --project-dir - absolute path to the project root directory (passed by `workflow start`)
# --model       - model id forwarded to the agent CLI (agent only)
# --reset       - remove the existing container before starting a fresh one (agent/venv only)
# --repo        - out-of-band repo to run the hook inside, e.g. 'docs' or a code repo name
#                 (hook only); default: the mount root itself (in-band only)

set -euo pipefail

# ---------------------------------------------------------------------------
# Project customization
# ---------------------------------------------------------------------------
IMAGE=gronsti-pi
# Docker port exports, e.g. "-p 127.0.0.1:8050:8050"
BUILDER_PORTS=""
CONTAINER_ENVS="--env UV_LINK_MODE=copy --env GIT_EDITOR=true"

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
usage() {
    echo "Usage: $(basename "$0") SUBCOMMAND PROJECT REPO --project-dir DIR [--model MODEL] [--reset] [--repo NAME]" >&2
    echo "       SUBCOMMAND is one of: agent, venv, shell, hook" >&2
    exit 1
}

[[ $# -ge 3 ]] || usage

SUBCOMMAND="$1"
PROJECT="$2"
REPO="$3"
shift 3

case "$SUBCOMMAND" in
    agent | venv | shell | hook) ;;
    *)
        echo "Error: unknown subcommand: $SUBCOMMAND" >&2
        usage
        ;;
esac

PROJECT_DIR=""
MODEL=""
RESET=false
CODE_REPO=""

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
        --repo)
            [[ $# -ge 2 ]] || { echo "Error: --repo requires an argument" >&2; exit 1; }
            CODE_REPO="$2"
            shift 2
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
[[ -n "$REPO" ]]        || { echo "Error: REPO must not be empty" >&2; exit 1; }
[[ -n "$PROJECT_DIR" ]] || { echo "Error: --project-dir is required" >&2; exit 1; }
[[ -d "$PROJECT_DIR" ]] || { echo "Error: project dir does not exist: ${PROJECT_DIR}" >&2; exit 1; }

if [[ -n "$MODEL" && "$SUBCOMMAND" != agent ]]; then
    echo "Error: --model is only valid for the 'agent' subcommand" >&2
    exit 1
fi
if [[ "$RESET" == true && "$SUBCOMMAND" != agent && "$SUBCOMMAND" != venv ]]; then
    echo "Error: --reset is only valid for the 'agent' and 'venv' subcommands" >&2
    exit 1
fi
if [[ -n "$CODE_REPO" && "$SUBCOMMAND" != hook ]]; then
    echo "Error: --repo is only valid for the 'hook' subcommand" >&2
    exit 1
fi

ENV_LIST="${PROJECT_DIR}/env.list"
if [[ ! -f "$ENV_LIST" ]]; then
    echo "Error: env.list not found at ${ENV_LIST}" >&2
    echo "       Create it and add the required environment variables (API keys, etc.)." >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Derived variables
# ---------------------------------------------------------------------------
CONTAINER="pi-${PROJECT}-${REPO}"
REPO_PATH="/home/auggie/projects/${PROJECT}/${REPO}"
# In-band, REPO_PATH is the repo itself. Out-of-band, it is a wrapper directory holding
# one subdirectory per repo (docs plus every code repo); --repo selects which one the
# 'hook' subcommand runs in.
HOOK_WORKDIR="$REPO_PATH"
[[ -n "$CODE_REPO" ]] && HOOK_WORKDIR="${REPO_PATH}/${CODE_REPO}"

# Throwaway-container subcommands need no persistent container bring-up at all.
THROWAWAY=false
[[ "$SUBCOMMAND" == hook ]] && THROWAWAY=true
[[ "$SUBCOMMAND" == venv && "$REPO" == canon ]] && THROWAWAY=true

if [[ "$THROWAWAY" != true ]]; then
    # -------------------------------------------------------------------
    # Reset: remove the existing container if requested
    # -------------------------------------------------------------------
    if [[ "$RESET" == true && "$SUBCOMMAND" != shell ]]; then
        if docker inspect "$CONTAINER" &>/dev/null; then
            echo "Removing container ${CONTAINER}..."
            docker rm -f "$CONTAINER"
        fi
    fi

    # -------------------------------------------------------------------
    # Bring the container up (run / start / no-op depending on state)
    # -------------------------------------------------------------------
    if [[ "$SUBCOMMAND" == shell ]]; then
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
        exited | created | paused)
            echo "Starting stopped container ${CONTAINER}..."
            docker start "$CONTAINER"
            ;;
        absent)
            if [[ "$SUBCOMMAND" == shell ]]; then
                echo "Error: Shell mode expects container $CONTAINER to exist"
                exit 1
            else
                echo "Creating container ${CONTAINER}..."
                # NOTE: Add any additional --env flags here for secrets that should not
                # live in env.list (e.g. per-agent API keys):
                #   --env ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY}" \
                PORTS=""
                [[ "$REPO" == builder ]] && PORTS="${BUILDER_PORTS}"
                docker run \
                    --name "$CONTAINER" \
                    --env-file "$ENV_LIST" \
                    --env PROJECT="$PROJECT" \
                    --env AGENT="$REPO" \
                    ${CONTAINER_ENVS} \
                    -v "/home/auggie/pi_agents/${REPO}:/home/auggie/.pi" \
                    -v /home/auggie/dot_cache:/home/auggie/.cache \
                    -v /home/auggie/dot_local/share/uv/python:/home/auggie/.local/share/uv/python \
                    -v "${PROJECT_DIR}/${REPO}:${REPO_PATH}" \
                    -v "${PROJECT_DIR}/bare:${PROJECT_DIR}/bare:ro" \
                    ${PORTS} \
                    --workdir "/home/auggie/projects/${PROJECT}" \
                    --detach \
                    "${IMAGE}"
                # NOTE: 'bare' is mounted at its host path so it matches the absolute
                # 'bare' remote URL recorded at clone time. Read-only by design: it makes
                # 'git fetch'/'git rebase' work (needed for the 'rebase' prompt) while
                # 'git push' fails by construction -- agents have no write access.
            fi
            ;;
        *)
            echo "Error: container ${CONTAINER} is in unexpected state: ${CONTAINER_STATUS}" >&2
            exit 1
            ;;
    esac
fi

# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
case "$SUBCOMMAND" in
    shell)
        exec docker exec -it --user auggie --workdir "$REPO_PATH" "$CONTAINER" bash
        ;;
    venv)
        [[ -d "${PROJECT_DIR}/${REPO}/.venv" ]] && rm -rf "${PROJECT_DIR}/${REPO}/.venv"
        # A bootstrap script (see docs/example_bootstrap_venv.sh) knows how to install the venv: it lives in
        # the docs repo (out-of-band) or at docs/bootstrap_venv.sh of the project repo (in-band).
        BOOTSTRAP_REL="docs/bootstrap_venv.sh"
        if [[ -f "${PROJECT_DIR}/${REPO}/${BOOTSTRAP_REL}" ]]; then
            VENV_CMD="source ~/.bashrc && ./${BOOTSTRAP_REL}"
        else
            VENV_CMD="source ~/.bashrc && uv venv --seed && source .venv/bin/activate && uv sync --group dev"
            # VENV_CMD="source ~/.bashrc && uv venv --seed && source .venv/bin/activate && uv pip install -e ."
        fi
        if [[ "$REPO" == canon ]]; then
            # Throwaway container: canon has no persistent sandbox.
            # NOTE: --env-file intentionally omitted; venv creation needs no secrets.
            exec docker run --rm --user auggie \
                -v "${PROJECT_DIR}/canon:${REPO_PATH}" --workdir "$REPO_PATH" \
                -v /home/auggie/dot_cache:/home/auggie/.cache \
                -v /home/auggie/dot_local/share/uv/python:/home/auggie/.local/share/uv/python \
                --env PROJECT="$PROJECT" --env AGENT="$REPO" \
                --env UV_VENV_CLEAR=1 --env UV_LINK_MODE=copy \
                "${IMAGE}" bash -c "$VENV_CMD"
        else
            exec docker exec --user auggie --workdir "$REPO_PATH" \
                --env UV_VENV_CLEAR=1 --env UV_LINK_MODE=copy "$CONTAINER" bash -c "$VENV_CMD"
        fi
        ;;
    hook)
        # The single .venv lives at the mount root; out-of-band the hook runs in a repo
        # subdirectory, so activate it explicitly rather than relying on the working directory.
        # Fall back to .git/hooks/pre-commit when there's no version-controlled
        # .git-pre-commit-hook (e.g. a third-party oob code repo); fail loudly if neither exists.
        HOOK_CMD="if [[ -f '${REPO_PATH}/.venv/bin/activate' ]]; then source '${REPO_PATH}/.venv/bin/activate'; fi; "
        HOOK_CMD+='if [[ -x ./.git-pre-commit-hook ]]; then ./.git-pre-commit-hook; \
            elif [[ -x .git/hooks/pre-commit ]]; then .git/hooks/pre-commit; \
            else echo "Error: no pre-commit hook found in $(pwd)" >&2; exit 1; fi'
        # Throwaway container; warm .venv comes from the mounted repo dir.
        # NOTE: --env-file intentionally omitted; the hook needs no secrets.
        exec docker run -t --rm --user auggie \
            -v "${PROJECT_DIR}/${REPO}:${REPO_PATH}" --workdir "$HOOK_WORKDIR" \
            -v /home/auggie/dot_cache:/home/auggie/.cache \
            -v /home/auggie/dot_local/share/uv/python:/home/auggie/.local/share/uv/python \
            --env PROJECT="$PROJECT" --env AGENT="$REPO" ${CONTAINER_ENVS} \
            "${IMAGE}" bash -lc "$HOOK_CMD"
        ;;
    agent)
        EXEC_CMD=(docker exec -it --user auggie --workdir "$REPO_PATH" "$CONTAINER" \
            bash -lc "pi${MODEL:+ --model $MODEL}")
        exec "${EXEC_CMD[@]}"
        ;;
esac
