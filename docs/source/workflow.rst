.. _Workflow:

The Workflow
============

``workflow`` orchestrates three AI coding agents (a planner, a builder, and a reviewer)
plus a human operator through a pipeline of small, reviewable changes.

Philosophy
----------

* **Quality over velocity.** The pipeline deliberately adds a few points of friction
  (clarifying questions, critiques, code review) where the human operator can inspect,
  adjust, or stop the work. This is a design choice, not an oversight.
* **Limited, pipelined parallelism.** Three agents is enough: one plans the next
  feature while a second implements the current one and a third reviews the previous
  one. This gives pipeline parallelism without the coordination overhead of a larger
  swarm. With this level of quality gates, human bandwidth precludes additional agents.
* **Forward-only flow.** Work only ever moves forward through the pipeline
  (planner → builder → reviewer). When a review turns up problems with an already
  merged feature, the fix re-enters the pipeline as a new plan rather than moving
  the plan backwards in the state sequence.
* **Git history protection.** Every agent works in its own sandboxed clone and can
  only read from the shared bare repository; only the human operator ever pushes to
  it. This keeps a quality gate in front of the project's real history.
* **Continuous improvement.** Lessons learned about the process itself are meant to
  be codified back into the prompts and configuration, not just remembered.

Agents and repos
-----------------

There are three **agents**, each an AI coding assistant running in its own sandbox:

* **planner** — turns rough ideas into detailed, reviewable plans.
* **builder** — implements a plan on a feature branch.
* **reviewer** — reviews and, if needed, fixes an implementation before it is merged.

The **human operator** drives the pipeline from outside the sandboxes using the
``workflow`` CLI, but is not itself an agent.

Each participant works out of its own git clone (a **repo**):

* ``canon`` — the human operator's working clone, and the only repo where transitions
  are committed.
* ``bare`` — a bare repository, readable by every sandbox but writable only from
  the host.
* ``planner``, ``builder``, ``reviewer`` — one clone per agent, living inside that
  agent's sandbox.

Only the ``main`` branch is long-lived; every other branch is short-lived and deleted after
merge. Plans move through the pipeline as commits on these branches, and the CLI
performs the push/pull/merge steps needed at each handoff.

Plans and the state machine
----------------------------

A **plan** is a Markdown file under ``docs/plans/``. Its state is determined purely
by which subdirectory it lives in, and its filename is a sequence number plus a slug
(e.g. ``docs/plans/implementing/022_user_docs.md``).

.. list-table:: Plan states
   :header-rows: 1
   :widths: 15 15 70

   * - State
     - Owning agent
     - Description
   * - ``intent``
     - planner
     - Rough, possibly incomplete ideas written by the human operator.
   * - ``planning``
     - planner
     - Detailed plans being drafted, before approval.
   * - ``todo``
     - *(human operator)*
     - Plans approved for implementation, waiting to be scheduled.
   * - ``implementing``
     - builder
     - Plans with an ongoing implementation.
   * - ``checking``
     - reviewer
     - Implementation complete; under review.
   * - ``done``
     - *(human operator)*
     - Completed and merged.
   * - ``rejected``
     - *(human operator)*
     - Closed out without merging. Reachable from any state.
   * - ``review``
     - reviewer
     - Not a plan state; a home for review/audit reports, including 
       codebase-wide ones not tied to a single plan.

Transitions between states are triggered either by a **script** command (a plain
Python action, e.g. ``workflow todo``) or a **prompt** command (a rendered prompt
sent to an agent's tmux window, e.g. ``workflow plan``). See :ref:`CLI` for the
full command reference, and ``AGENTS.md`` in a generated project for the
machine-generated listing of commands available in each state.

Reviews (under ``docs/plans/reviews/``) are a special case: they are never moved
between states. Instead, a review's findings are addressed by writing one or more
new plans.

.. _Stacked plans:

Stacked plans
-------------

Fine-grained plans (e.g. ``subdivide`` output, or a small follow-up fix) often build
directly on one another. Building them one at a time is right, but reviewing and
merging each one separately is wasteful. ``implement PLAN --onto BASE`` lets several
plans share one feature branch, so the whole stack is reviewed and merged once:

* ``implement B --onto A`` requires ``A`` to be ``implementing`` (or ``checking``
  with nothing unpushed from the reviewer), and to not already have another plan
  stacked on it. It records ``B``'s ``onto`` and the branch/anchor commit in ``B``'s
  plan file frontmatter, then merges current ``main`` into the shared branch.
  ``--onto`` with no value infers the base from the branch builder is already on.
* ``check TIP`` and ``accept TIP`` must be run on the stack's tip (the plan nothing
  else is stacked on); both cascade the transition to every member of the stack in
  one commit. ``accept`` still deletes only the one shared branch.
* ``reject`` never cascades — it only closes out the named plan. Rejecting a
  stacked plan prints the concrete ``git branch -f <branch> <anchor>`` that rewinds
  the shared branch past its commits (no working tree is ever discarded); this is
  safest done before the next ``check`` cascades the plan onto the branch for good.
* ``workflow status``, ``workflow log``, and ``workflow doctor`` all surface stack
  membership: ``status`` shows the base a plan is stacked onto, ``log`` annotates a
  stacked ``implement`` entry, and ``doctor`` flags a broken ``onto``, an orphaned
  stack branch, or a recorded branch that no longer exists anywhere.

A project's customized ``config.yaml`` does not automatically pick up stack-aware
prompt wording added to the default ``build``/``pr`` templates — copy the relevant
``{% if stack %}`` blocks over by hand if you want agents to see stack context.

Configurable vs. requiring code changes
----------------------------------------

The workflow graph is deliberately split across a YAML configuration file and the
Python codebase, so that day-to-day tuning does not require touching code:

**Configuration only** (edit ``config.yaml``, no code change needed):

* Prompt text for any prompt command (``ask``, ``critique``, ``plan``, ``build``, ...).
* Adding, removing, or reordering prompt and manual commands within existing states.
* Agent names, models, and tmux session settings.
* Which repo a command reads from (``repo_override``).

**Requires a code change** (fork the project and modify the Python source):

* Adding or removing plan states.
* Adding or removing agents.
* Adding a new *script* command, or changing what an existing one does — scripts are
  plain Python functions registered by name and referenced from the config.
* Changing the shape of the state machine itself (new transitions between states).

This tool is opinionated and built around one person's workflow. If you want a
substantially different pipeline (a different number of agents, extra states, a
different git strategy), the recommended path is to fork the repository and have
your own agent(s) implement the changes, rather than trying to bend the existing
configuration to fit.
