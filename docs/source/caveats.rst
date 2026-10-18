.. _Caveats:

Caveats and limitations
=========================

* **Plans live in the same repo as the source code.** Tracking plans out-of-band
  (e.g. in a separate repo or issue tracker) is on the roadmap but not implemented.
* **The number of agents is fixed at three by design** (planner, builder, reviewer).
  This tool intentionally does not scale up to larger agent swarms; if you want
  more parallelism, look elsewhere or fork the project (see :ref:`Workflow`).
* **This is one person's workflow, opinionated on purpose.** The git branch
  strategy, state machine, and pipeline shape reflect a specific way of working.
  Adding states, agents, or new script commands requires a code change and forking
  the project rather than a configuration tweak (see :ref:`Workflow`).
* **Only the human operator pushes to the bare repo.** Agents can pull but never
  push; every merge to ``main`` goes through a script command run by the human.
  This allows enforcing strict quality gates.
* **Validation is best-effort, not a full CI pipeline.** ``workflow validate`` checks
  things like plan/state boundaries, added lint/type-check suppression comments (``noqa``, ``type: ignore``, ...), git hook
  integrity, and that ``AGENTS.md`` has not been edited by the agent — it does not replace
  running your own test suite.
