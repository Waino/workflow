#!/usr/bin/env bash
# Example docs/bootstrap_venv.sh
#
# Out-of-band only. Copy this file to <project>/canon/docs/bootstrap_venv.sh, commit it to the
# docs repo, mark it executable (`chmod +x`), and customise the repo list below.
#
# run_agent_in_docker.sh's 'venv' subcommand runs this script (via `uv run` inside the
# container) instead of its own `uv venv --seed && uv sync` fallback whenever it exists,
# because the wrapper root belongs to no repo and so has no pyproject.toml of its own for
# plain `uv sync` to act on. It runs with the wrapper root (mount root) as its working
# directory, so paths below are relative to that root, e.g. "mypackage", not "canon/mypackage".
#
# Extend this script for anything beyond `pip install -e`: other package managers, codegen,
# submodules, etc. It always runs inside the sandbox container, never on the host.

set -euo pipefail

uv venv --seed
source .venv/bin/activate

# One -e per code repo registered with `workflow add-repo` / detected at `init --oob`:
uv pip install -e mypackage -e dep1 -e dep2
