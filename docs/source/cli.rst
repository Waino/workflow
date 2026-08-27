.. _CLI:

Command reference
==================

All functionality is exposed through the single ``workflow`` executable,
run by the human operator from outside the agent sandboxes::

    workflow [--dry-run] [--config PATH] COMMAND [ARGS]...

Global options
--------------

* ``-n``, ``--dry-run`` — print the shell commands (git, docker, tmux, ...) that a
  script command would run, without executing them.
* ``--config PATH`` — use an explicit config file instead of the default resolution
  order (explicit path → ``{project_dir}/config.yaml`` → ``~/.config/workflow/config.yaml``).

Most commands take a ``PLAN_PATH`` argument identifying a plan. Path matching is
loose: a full path, a path relative to a repo, a bare filename, or just a sequence
number all work, as long as the reference is unambiguous in context (see the
plan resolver in :ref:`Workflow` for the state machine that paths resolve against).

When plan resolution fails due to ambiguity, the solution is often to rerun with 
a path that fully pins down the agent and state (i.e. an absolute or relative path).

Two kinds of commands
----------------------

Commands come in two flavors, both dispatched through the same CLI:

* **Script commands** run local Python logic directly (git operations, file moves,
  validation, ...). They are never visible to the agents.
* **Prompt commands** render a prompt template from the configuration and send it to
  the responsible agent's tmux window via libtmux; the agent then does the actual
  work as an ordinary chat turn. Every prompt command accepts ``--edit``, which opens
  the rendered prompt in ``$EDITOR`` (like ``git commit``) before sending — clear the
  file entirely to cancel without sending anything.

Which prompt commands exist, and their exact wording, is entirely configuration
driven (see ``docs/default_config.yaml``). The set below reflects the default
configuration; a customized ``config.yaml`` may add, remove, or reword prompt
commands within a state without any code change.

Setup and session management
-----------------------------

``init``
    Initialise the project directory: clone the canon repo into a bare repository
    and one working clone per agent. Supports ``--continue`` to resume after a
    partially completed run.

``start``
    Create the tmux session (or attach to it if it already exists), one window per
    agent plus a ``builder-shell`` window for manual shell access. Supports
    ``--reset`` to tear down and recreate the session and containers.

``go TARGET``
    Rotate the control pane to the named tmux window (an agent name, or ``manual``
    for the builder shell) and print that repo's git status.

``doctor``
    Report the health of every clone (branch, dirty state, remotes) and flag
    plan-file anomalies (e.g. duplicate sequence numbers). Exits non-zero if any
    problems are found.

``agents_md``
    Regenerate the auto-generated section of ``AGENTS.md`` (plan state machine and
    per-state command listing) from the current configuration. Static sections of
    ``AGENTS.md`` are left untouched. ``--check`` reports whether a regeneration
    would change the file, without writing.

``cheat``
    Print a cheat sheet of the whole pipeline as a table. Works without a project
    directory.

Plan lifecycle
---------------

These script commands move a plan through the state machine and perform the git
operations (push/pull/merge) needed at each handoff.

``new SLUG``
    Create a new intent file at ``docs/plans/intent/{nnn}_{slug}.md`` using the next
    free sequence number ``nnn``, and open it in ``$EDITOR``.

``push PLAN_PATH``
    Push manually edited content for a plan to the bare repo (and pull it into the
    owning agent's clone, where applicable). Does not transition state. Used e.g.
    after hand-editing an intent or a ``todo`` plan.

``todo PLAN_PATH``
    Approve a ``planning`` plan for implementation: push from planner, pull to
    canon, transition to ``todo``, merge to ``main``. ``--force`` skips a failing
    validation (e.g. to accept edits outside the usual responsibility of the agent).

``implement PLAN_PATH``
    Schedule a ``planning``/``todo`` plan for implementation: transition to
    ``implementing``, push, create a ``feat/{plan_name}`` branch in the builder's
    clone, and switch the tmux focus to the builder. ``--continue`` resumes after a
    partially completed run.

``check PLAN_PATH``
    Hand a finished implementation off from builder to reviewer: rerun the
    pre-commit hook, validate, transition to ``checking``, push the branch, and
    fetch it into the reviewer's clone. ``--target-repo`` overrides which repo is
    pushed (default ``builder``); ``--force`` skips a failing validation.

``validate PLAN_PATH``
    Run the pre-merge verification checks (also run internally by ``todo``,
    ``check``, and ``accept``) without transitioning state. ``--target-repo``
    overrides the repo the checks run against.

``accept PLAN_PATH``
    Accept a reviewed plan: push from reviewer, pull to canon, transition to
    ``done``, merge to ``main``, push.

``reject PLAN_PATH``
    Close out a plan without merging: transition to ``rejected``, merge to
    ``main``, push. ``--target-repo`` selects which repo holds the plan being
    rejected (default ``canon``).

``canon_pre_commit``
    Run the pre-commit hook against the canon repo in a throwaway container.
    Useful for validating manual edits, when you want the test suite to
    run containerized and not on the host.

Prompt commands (agent dispatch)
----------------------------------

These send a rendered prompt to the agent responsible for the plan's current
state. All accept ``--edit`` to review/modify the prompt before sending.

``ask PLAN_PATH``
    Ask the agent to read the plan and raise clarifying questions.

``critique PLAN_PATH``
    Ask the planner to critique an intent or plan: flaws, technical-debt risks, and
    suggested improvements.

``plan PLAN_PATH``
    Ask the planner to turn an intent into a detailed plan in the ``planning`` state.

``subdivide PLAN_PATH``
    Ask the planner to break a plan into several smaller plans in the ``planning``
    state.

``build PLAN_PATH``
    Ask the builder to implement an ``implementing`` plan on its feature branch.

``pr PLAN_PATH``
    Ask the reviewer to write a thorough code review of the plan's branch.

``fix [PLAN_PATH]``
    Ask the reviewer to fix the issues from the most recent review. The path may
    be omitted if the review is already in the agent's session history.

``full-review``
    Ask the reviewer to review the whole current state of the codebase.

``followthrough``
    Ask the reviewer to compare done/checking/intent plans against the codebase
    and list unimplemented or partially implemented features.

``refresh``
    Ask the planner to update ``planning``/``todo`` plans against decisions made in
    plans that were completed after they were written.

Information
------------

``status``
    Show every plan's current state and its likely next step(s). Omits ``done`` and
    ``rejected`` plans unless ``--all`` is given.

``log``
    Pretty-print the JSONL audit log. Filterable by ``--agent``, ``--plan``,
    ``--command``, ``--since``, and ``--until``.
