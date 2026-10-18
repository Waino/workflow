#!/usr/bin/env bash
# Example docs/bootstrap_venv.sh
#
# Copy this file to <project>/canon/docs/bootstrap_venv.sh (out-of-band: committed in the docs repo;
# in-band: committed in the project repo as docs/bootstrap_venv.sh), mark it executable
# (`chmod +x`, committed with `git add --chmod=+x`), and customise the repo list below.
# Out-of-band projects require it. In-band projects need it only without a pyproject.toml;
# a non-Python project commits its own, possibly no-op, script.
#
# run_agent_in_docker.sh's 'venv' subcommand runs this script inside the container instead
# of its own `uv venv --seed && uv sync` fallback whenever it exists,
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

# Dev tooling the repos' pre-commit hooks need (in-band gets this from `uv sync --group dev`):
uv pip install black mypy pytest ruff
