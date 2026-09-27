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
    Initialise the project directory: clone the canon repo(s) into a bare repository
    and one working clone per agent. Supports ``--continue`` to resume after a
    partially completed run. ``--oob`` initialises an out-of-band project instead
    (see :ref:`Out-of-band mode`, which lists what must exist beforehand);
    ``--skip-hook NAME`` (repeatable, oob only) skips installing the pre-commit hook
    symlink for a given code repo. The mode and the ``--skip-hook`` choices are fixed
    at the first successful ``init`` and cannot be changed by a later run.

``add-repo NAME``
    Out-of-band only: register one more code repo after ``init`` (clone into ``bare``
    and every agent worktree, record it in ``.workflow.json``). ``--skip-hook`` skips
    installing the pre-commit hook symlink for this repo. The venvs do not pick up the
    new repo until it is added to ``docs/bootstrap_venv.sh`` and they are recreated.
    There is no command to remove a registered repo.

``sync [--repo NAME]... [--branch NAME]... [--force]``
    Out-of-band only (state-independent, like ``doctor``): refresh ``bare/<repo>``
    from each code repo's own upstream (``origin``), plus ``docs`` if it has one.
    Fast-forward-only unless ``--force`` is passed (see :ref:`Out-of-band mode`).

``start``
    Create the tmux session (or attach to it if it already exists), one window per
    agent plus a ``builder-shell`` window for manual shell access. Supports
    ``--reset`` to tear down and recreate the session and containers.

``go TARGET``
    Rotate the control pane to the named tmux window (an agent name, or ``manual``
    for the builder shell) and print that repo's git status.

``doctor``
    Report the health of every clone (branch, dirty state, remotes, whether its
    ``main`` matches the bare repo) and flag plan-file anomalies (e.g. duplicate
    sequence numbers) and stack anomalies (a broken ``onto``, a stack branch whose
    plans are all ``done``/``rejected``, or a recorded branch that exists nowhere).
    Exits non-zero if any anomaly is found; the separate *notes* column carries
    observations that are normal during parallel work (e.g. a branch that still
    needs rebasing) and never affects the exit code. Out-of-band: one ``bare``/
    worktree row per registered code repo, plus two repo-scoped anomalies -- a
    planner/builder/reviewer clone with a remote other than ``bare``, and a
    worktree-root ``AGENTS.md`` that doesn't hash-match ``.agents_md_hash`` (see
    `Multi-repo validate, doctor, and status`_).

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
    canon, transition to ``todo``, merge to ``main``. ``--force-validation`` skips a
    failing validation (e.g. to accept edits outside the usual responsibility of the
    agent); ``--force-push`` is explained under `Landing a branch on main`_.

``implement PLAN_PATH``
    Schedule a ``planning``/``todo`` plan for implementation: transition to
    ``implementing``, push, create a ``feat/{plan_name}`` branch in the builder's
    clone, and switch the tmux focus to the builder. ``--continue`` resumes after a
    partially completed run; ``--force-push`` is explained under
    `Landing a branch on main`_. ``--onto`` builds a stack instead of a fresh branch —
    see `Stacked plans`_.

``check PLAN_PATH``
    Hand a finished implementation off from builder to reviewer: rerun the
    pre-commit hook, validate, transition to ``checking``, push the branch, and
    fetch it into the reviewer's clone. ``--target-repo`` overrides which repo is
    pushed (default ``builder``); ``--force-validation`` skips a failing validation.
    On a stacked plan this cascades: every stack member not already ``checking`` is
    transitioned in the same commit (see `Stacked plans`_); *PLAN_PATH* must be the
    tip of its stack. Out-of-band: also pushes every code repo with commits bare
    doesn't have, fetches (never checks out) the reviewer's code repos, runs the
    pre-commit hook once per pushed repo, and refuses on a repo with uncommitted
    tracked changes (see `Multi-repo handoffs`_).

``validate PLAN_PATH``
    Run the pre-merge verification checks (also run internally by ``todo``,
    ``check``, and ``accept``) without transitioning state. ``--target-repo``
    overrides the repo the checks run against. Out-of-band: ``file-scope`` and
    ``git-hooks`` run once per repo, and two repo-scoped checks are added --
    ``planner-code-commits`` and ``branches-frontmatter`` (see `Multi-repo
    validate, doctor, and status`_).

``accept PLAN_PATH``
    Accept a reviewed plan: push from reviewer, pull to canon, transition to
    ``done``, merge to ``main``, push, then delete the merged ``feat/{plan_name}``
    branch in the reviewer's and builder's clones and on the bare repo (all cleanup
    failures are warnings — the accept itself has already landed).
    ``--force-push`` is explained under `Landing a branch on main`_. On a stacked
    plan this cascades every member to ``done`` in one commit and deletes the one
    shared branch; *PLAN_PATH* must be the tip of its stack. Out-of-band: also
    pushes every code repo with commits bare doesn't have, and refuses on a repo
    with uncommitted tracked changes (see `Multi-repo handoffs`_).

``reject PLAN_PATH``
    Close out a plan without merging: transition to ``rejected`` and push that
    transition. The plan's branch is deliberately *not* merged and *not* deleted;
    the command prints a note naming the branch you may delete by hand.
    ``--target-repo`` selects which repo holds the plan being rejected (default
    ``canon``). On a stacked plan, the note instead gives the concrete checkout and
    ``git reset --hard`` that rewind the shared branch past this plan's commits (see
    `Stacked plans`_); rejecting a plan that itself has a child stacked on it prints
    an additional warning that the child's commits stay on the branch regardless.

``canon_pre_commit``
    Run the pre-commit hook against the canon repo in a throwaway container.
    Useful for validating manual edits, when you want the test suite to
    run containerized and not on the host.

.. _Landing a branch on main:

Landing a branch on main
-------------------------

``todo``, ``implement`` and ``accept`` land a branch on ``main`` with a
fast-forward merge. When ``main`` has moved on since the branch was cut — routine
under parallel work — the branch is first rebased onto ``bare/main``, the
pre-commit hook is re-run against the rebased tree, and the result is force-pushed
with ``--force-with-lease``. The command says so loudly: a rebase rewrites history,
which is your cue to check the outcome. A rebase is refused when the source clone is
dirty, is not on the branch, has the branch checked out in another clone, or when the
remote branch holds commits that exist nowhere locally.

If the rebase hits conflicts, the command fails without touching ``main`` and names
the conflicting paths; dispatch the ``rebase`` prompt to let the owning agent resolve
them. If a run rebased successfully but died before publishing, the rerun needs
``--force-push`` to replace the stale remote branch with the rebased one — the only
case in the whole pipeline where anything is force-pushed on purpose.

After every handoff each clone's ``main`` is brought up to date (and the handing-off
agent's clone is parked on it), so the next branch is cut from a current ``main``.

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

``rebase [PLAN_PATH]``
    Ask the agent owning the plan's branch (planner in ``planning``, reviewer in
    ``checking``) to rebase that branch onto current ``bare/main`` and resolve the
    conflicts the automatic rebase could not.

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
    Show every plan's current state and its likely next step(s), plus a ``stack``
    column naming the plan it is stacked onto (blank when unstacked). Omits ``done``
    and ``rejected`` plans unless ``--all`` is given. Followed by each worktree's git
    status; out-of-band, this is a worktree × repo matrix instead of a linear list
    (see `Multi-repo validate, doctor, and status`_).

``log``
    Pretty-print the JSONL audit log. Filterable by ``--agent``, ``--plan``,
    ``--command``, ``--since``, and ``--until``. A stacked ``implement`` entry's
    command cell reads ``implement (onto {base_plan})``.
