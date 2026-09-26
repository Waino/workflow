.. _Docker:

Running agents in Docker
==========================

Each agent (and canon, for a few one-off operations) runs inside its own Docker
container. ``workflow`` does not call Docker directly; instead it shells out to an
external script, conventionally named ``run_agent_in_docker.sh``, that you place on
``PATH`` (or in the project root). A template lives at
``docs/example_run_agent_in_docker.sh`` — copy it into your project and adjust it to
your environment before running ``workflow start``.

Usage
-----

::

    run_agent_in_docker.sh SUBCOMMAND PROJECT REPO --project-dir DIR [--model MODEL] [--reset] [--repo NAME]

``SUBCOMMAND``
    One of:

    * ``agent`` — run the agent CLI in a persistent, named sandbox container.
    * ``venv`` — (re)create the repo's Python virtual environment. Runs in the
      persistent container for an agent repo, or in a throwaway container for
      ``canon``.
    * ``shell`` — open an interactive bash shell in an already-running persistent
      container (used by the ``builder-shell`` tmux window).
    * ``hook`` — run the project's pre-commit hook in a throwaway container.

``PROJECT``
    Project name, used to name containers and derive mount paths.

``REPO``
    Which clone the command applies to: an agent name (``planner``, ``builder``,
    ``reviewer``) for ``agent``/``shell``/``hook``, or additionally ``canon`` for
    ``venv``/``hook``.

``--project-dir DIR``
    Absolute path to the project root (passed automatically by ``workflow start``).

``--model MODEL``
    Model id forwarded to the agent CLI. Only valid with ``agent``.

``--reset``
    Remove the existing container before starting a fresh one. Only valid with
    ``agent`` and ``venv``.

``--repo NAME``
    Out-of-band only: the repo to run the hook inside, e.g. ``docs`` or ``mypackage``.
    The mounted directory is a wrapper holding one subdirectory per repo (``docs`` plus
    every code repo) and is not a git repo itself, so ``hook`` needs to know which one
    to ``cd`` into. Only valid with ``hook``; omit it for in-band projects, where the
    mount root is the repo itself.

What ``workflow`` expects from the script
-------------------------------------------

``workflow start`` and ``workflow go`` invoke ``agent`` and ``shell`` respectively to
bring up and attach to each agent's tmux window. ``workflow check`` (and other
script commands that run the pre-commit hook) invoke ``hook``. The script must:

* Create the persistent container on first use, and reuse/restart it afterwards
  (``agent``/``shell`` should not throw away agent state between invocations).
* Mount the corresponding repo clone from ``{project-dir}/{REPO}`` into the
  container at a predictable path.
* Mount ``{project-dir}/bare`` **read-only**, at that same host path, into the
  persistent agent container. Agents have no write access to the shared repo, but
  they do need to ``git fetch`` it -- most importantly for the ``rebase`` prompt,
  which asks an agent to rebase its branch onto current ``bare/main`` after an
  automatic rebase hit conflicts. Mounting it read-write would let an agent push
  directly to bare, bypassing the human-operated CLI that performs every other
  push; mounting it read-only makes that fail by construction rather than by
  policy.
* Read secrets (API keys, etc.) from an ``env.list`` file in the project directory
  rather than embedding them in the script.
* Run ``.git-pre-commit-hook`` when present, falling back to ``.git/hooks/pre-commit``
  otherwise, and fail loudly if neither exists (out-of-band code repos are not
  guaranteed to ship the shared hook script). Activate the mount root's ``.venv``
  first: out-of-band, the hook runs in a repo subdirectory, away from the venv.
* For ``venv``, run a version-controlled ``docs/bootstrap_venv.sh`` from the mount
  root when it exists (the out-of-band wrapper root has no ``pyproject.toml`` for
  plain ``uv sync`` to act on), falling back to
  ``uv venv --seed && uv sync`` otherwise. A template lives at
  ``docs/example_bootstrap_venv.sh``.

Customization points
----------------------

The example script is a starting point, not a finished product — expect to edit the
``docker run`` invocation for your own setup. In particular:

* **Base image** — the example uses a fixed image name (``gronsti-pi``); change it
  to whatever image you build for your agents.
* **Volume mounts** — the example mounts the agent's home directory, package caches,
  and the repo clone. Add or remove ``-v`` mounts to match what your agent CLI and
  toolchain need.
* **Extra environment variables** — the ``docker run`` block for ``agent`` has a
  commented-out example of adding a per-agent secret with ``--env``; uncomment and
  adapt it instead of putting secrets in ``env.list`` if you need per-agent values.
* **Container naming/workdir** — ``CONTAINER`` and ``REPO_PATH`` are derived from
  ``PROJECT``/``REPO``; adjust if your directory layout differs.

Everything else (subcommand dispatch, container state detection, throwaway vs.
persistent container handling) is plumbing that most setups can leave as-is.
