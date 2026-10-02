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

.. _Out-of-band mode:

Out-of-band mode
~~~~~~~~~~~~~~~~~

A project is either **in-band** (the default: plans live alongside the package
source in one repo) or **out-of-band** ("oob": plans live in a separate ``docs``
repo, alongside one or more independent code repos). The mode is fixed for good at
``workflow init`` (plain ``init``, or ``init --oob``) and cannot be changed afterwards.

In oob mode, ``canon``/``bare``/``planner``/``builder``/``reviewer`` become wrapper
directories, each holding one subdirectory per repo:

.. code-block::

    canon/
    +-- docs/          (plans; also holds body_of_agents.md and bootstrap_venv.sh)
    +-- mypackage/
    +-- dep1/

Before ``workflow init --oob``, prepare the project directory by hand:

* ``env.list`` exists in the project directory.
* ``canon/docs`` is a git repo with at least one commit and ``main`` checked out.
* ``canon/docs`` contains ``body_of_agents.md`` (the hand-written part of ``AGENTS.md``)
  and ``bootstrap_venv.sh``, committed as executable (``git add --chmod=+x``): agents
  clone the committed version.
* Every code repo is cloned into ``canon/NAME`` with at least one commit. Code repo
  names are single directory names (letters, digits, ``.``, ``_``, ``-``; not ``docs``).
  ``init --oob`` registers every git repo it finds under ``canon/`` besides ``docs``
  and prints the list; preview it first with ``workflow --dry-run init --oob``.

Code repos in ``canon`` keep their own upstream: ``init`` adds a ``bare`` remote and
pushes the checked-out branch to it, but leaves the branch's upstream and push settings
alone.

``workflow add-repo NAME`` registers one more code repo after ``init`` (it clones
``canon/NAME`` into ``bare`` and every agent worktree, and records it in
``.workflow.json``, and regenerates every ``AGENTS.md`` copy so agents learn about the new
repo); there is no command to remove one. ``AGENTS.md`` is generated
from a hand-written ``docs/body_of_agents.md`` into every worktree root as an
unversioned build artifact (in-band, the hand-written body and the generated file
are the same, version-controlled ``AGENTS.md``). A single ``.venv`` at each wrapper
root covers every code repo, bootstrapped by the docs repo's own
``bootstrap_venv.sh`` — see :ref:`Docker`.

.. _Multi-repo handoffs:

Multi-repo handoffs
'''''''''''''''''''

The tool does not manage a code repo's branch lifecycle at all (see "Git branch
model" in the intent): agents create, check out, and name code-repo branches
themselves; a plan's frontmatter records which branch/base to use, for the agents'
own reference. Only the following are automated:

* **``check`` and ``accept`` push every repo with commits bare doesn't have**, not
  just the docs repo. Before pushing, they fetch ``bare`` in every repo of the acting
  worktree (``builder`` for ``check``, ``reviewer`` for ``accept``) and print a
  (repo, branch, commits) table of what is about to move. The docs repo keeps its
  usual dedicated transition/merge steps; every code repo with commits ahead gets a
  plain ``git push -u bare <branch>``, under whatever branch that repo happens to be
  on.
* **``check`` fetches (never checks out) the reviewer's code repos.** The reviewer
  agent checks out whichever branches the plan's frontmatter names.
* **Every prompt dispatch fetches ``bare`` in every code repo** of the target
  worktree, in addition to the docs repo's existing pull/refresh -- the single most
  important defence against an agent starting work from stale code.
* **``check``/``accept`` refuse on a repo with uncommitted tracked changes**
  (anywhere in the acting worktree); an untracked file only warns, since third-party
  repos routinely carry untracked build output.
* **``check``/``accept`` refuse, before anything is transitioned or pushed, a code repo
  that can't be pushed cleanly:** a detached HEAD (commit on a named branch first), or
  a branch that diverged from its ``bare`` counterpart (e.g. rebased). ``accept
  --force-push`` pushes such a branch with ``--force-with-lease``; for ``check``, push it
  by hand.
* **``check`` refuses commits on a code repo's default branch** (``bare``'s ``HEAD``,
  usually ``main``): all code work belongs on feature branches. Commits pushed to the
  default branch would make ``workflow sync`` refuse that repo, and ``--force`` would
  discard them.
* **The pre-commit hook runs once per repo** in the push set (skipping any repo
  with ``run_hook: false``), passing ``--repo NAME`` to the docker script so it runs
  inside the right subdirectory. ``check`` runs it on the builder's repos, ``accept``
  on the code repos holding reviewer commits.

The push-set table and the tracked/untracked dirty-tree precondition apply in-band
too, where the push set is just the single repo.

``workflow sync [--repo NAME]... [--branch NAME]... [--force]`` (oob only) refreshes
``bare/<repo>`` from each repo's own ``origin`` (canon's upstream) -- the
counterpart to the above, since nothing else keeps ``bare`` from drifting behind
upstream between syncs. By default it covers every code repo; ``docs`` is synced only
when named with ``--repo docs``, since ``bare`` is the docs repo's source of truth and is
normally ahead of its ``origin`` (``--force`` then applies to docs too, and would
discard those commits). Per repo: fetches ``origin``, resolves the upstream default branch
(``origin/HEAD``, falling back to ``main``, then ``master``), and pushes it (and any
``--branch``) to ``bare``. The push is fast-forward-only by default -- refusing and
printing the old/new sha when ``bare`` holds commits ``origin`` doesn't -- unless
``--force`` is passed; it never mirrors *every* upstream branch, only the default
plus whatever ``--branch`` names, so it can never clobber an agent's in-flight
feature branch that happens to share a name with something on upstream. A branch
missing on ``origin`` is skipped with a warning. ``workflow
doctor`` separately notes (not an anomaly) when a code repo's ``bare`` has fallen
behind its ``origin``.

.. _Multi-repo validate, doctor, and status:

Multi-repo validate, doctor, and status
'''''''''''''''''''''''''''''''''''''''

A plan's frontmatter can now record a ``branches`` entry per registered code repo, not
just the docs repo; a value of ``null`` means the plan legitimately doesn't touch that
repo. Once a plan reaches ``implementing`` or ``checking``, ``workflow check``'s
``branches-frontmatter`` check requires an entry (name or ``null``) for every code repo,
rejects entries for repos that aren't registered, and validates any recorded branch name
with ``git check-ref-format``.

In oob mode, the ``validate`` step of ``workflow todo`` (planner) and ``workflow check``
(builder) runs one ``file-scope`` and one ``git-hooks`` result per repo. Code repos are
unrestricted for file scope. ``git-hooks`` expects the managed ``pre-commit`` symlink
only in a repo that ships ``.git-pre-commit-hook`` (the same repos ``init`` installs it
in); elsewhere an executable hook -- e.g. a code repo's own pre-commit-framework hook --
counts as unmanaged, which only warns in a code repo but fails in docs.
``noqa-nosec`` and ``agents-md`` stay single, worktree-level results,
but internally cover every repo/worktree: ``noqa-nosec`` scans each code repo's commits
not yet in ``bare`` the same way it scans the docs repo's diff, and ``agents-md`` checks
the AGENTS.md copy at every worktree root, not just the acting one. Two checks are new
and oob-only: ``planner-code-commits`` fails if the planner has committed to any code
repo (code changes belong to builder/reviewer), and ``branches-frontmatter`` enforces the
rule above.

A code repo has no stable baseline ref to diff against, so its ``file-scope`` and
``noqa-nosec`` inputs are *every* commit reachable from ``HEAD`` but from no ``bare`` ref
-- all unpushed work in that clone, not a diff of one branch against its base. A
``noqa`` added in one such commit and removed in a later one is still reported.

``workflow doctor`` mirrors the same per-repo shape: the table gains a ``repo`` column
(omitted in-band) with one ``bare`` row and one row per worktree for every registered
repo. Two anomalies are oob-only: a planner/builder/reviewer clone with anything other
than exactly one remote named ``bare`` (canon is exempt -- it legitimately keeps
``origin`` too, for ``workflow sync``), and a worktree-root ``AGENTS.md`` copy that
doesn't hash-match ``.agents_md_hash``. The branch rules (canon on ``main``, no stray
builder/reviewer branch, local ``main`` matching ``bare``) apply to the docs repo only:
the tool manages neither code-repo branches nor their local ``main``. The
tracked/untracked dirty-tree split (tracked changes are an anomaly, untracked-only is a
note) now applies uniformly to every repo, in-band and oob alike.

``workflow status`` replaces its linear per-worktree ``git status`` block with a matrix
in oob mode: one row per worktree, one column per repo, each cell showing the branch,
``+N`` commits not in any ``bare`` ref, and ``-M`` commits its own ``bare/<branch>`` has
that the checkout lacks (``-`` when there is no such branch yet, or on a detached HEAD),
with a ``*`` marker when the repo has tracked changes; a missing clone shows
``missing``. In-band output is unchanged.

The trusted-hash file (``.agents_md_hash``) is now a ``v2`` format that additionally
records a hash of ``.workflow.json`` in oob mode (in-band, where ``AGENTS.md`` doesn't
depend on it, the entry is the hash of empty input). It doesn't affect pass/fail -- that
is the ``AGENTS.md`` hash alone -- but when an ``AGENTS.md`` copy has drifted, the error
names ``.workflow.json`` if it changed since the last generation. Upgrading: an existing
project's ``v1`` hash file fails ``agents-md`` until ``workflow agents_md`` is rerun.

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

.. _Index of decisions:

Index of decisions
------------------

Decisions are made during planning and review and recorded only in the individual plan,
so a later plan can easily miss one. ``docs/plans/index_of_decisions.md`` collects them in one place:

* a summary of each change (intent, plan and reviews),
* the decisions, grouped by topic, each with a status (``current``, ``intended``, ``superseded``, ``obsolete``),
* deferred work (``open`` or ``dropped``) and architecturally important terms.

The file does not exist in a fresh project. Run ``workflow index_decisions`` to have the reviewer
create it, and again later to add what changed since. It records the commit hash of the last pass,
so it must be merged by fast-forward or merge commit, never squashed or rebased.
``workflow followthrough`` removes deferred items that have since been completed.

The generated part of ``AGENTS.md`` tells agents to read ``intent/001_idea.md`` and, if it exists,
the index. Remove any static copy of the first instruction from your own ``AGENTS.md`` body.

The index is also the one place to see the current set of decisions: review it periodically
and correct entries that are wrong or stale.

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
  An explicit base is a plan name or a sequence number (``006_bar``, ``006``, ``6``),
  looked up in canon -- unlike a plan argument, it is not fuzzily resolved.
* ``check TIP`` and ``accept TIP`` must be run on the stack's tip (the plan nothing
  else is stacked on); both cascade the transition to every member of the stack in
  one commit. ``accept`` still deletes only the one shared branch.
* ``reject`` never cascades — it only closes out the named plan. Rejecting a
  stacked plan prints the concrete ``git checkout <branch>`` and
  ``git reset --hard <anchor>`` that rewind the shared branch past its commits; this
  is safest done before the next ``check`` cascades the plan onto the branch for good.
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
* Which worktree a command reads from (``worktree_override``).

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

Config vs. state
----------------

A project directory holds two distinct kinds of files, and the two must not be
confused:

* ``config.yaml`` — hand-authored configuration. Edited freely by the operator;
  resolved from a CLI flag, the project directory, or ``~/.config/workflow/``.
* ``.workflow.json`` — tool-owned state. Written only by ``workflow`` commands
  (``init`` and, in oob mode, ``add-repo``); never hand-edited. Its presence marks
  the project root, the way ``.workflow_root`` used to.
