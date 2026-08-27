.. _Introduction:

Introduction
============

``workflow`` orchestrates a small pipeline of AI coding agents (planner, builder,
reviewer) plus a human operator, moving plans through a fixed set of states from
rough idea to merged, reviewed code. It is a command-line tool for the human
operator; agents receive their instructions as prompts sent to their tmux windows,
not by invoking ``workflow`` themselves.

See :ref:`Workflow` for the philosophy and state machine, :ref:`CLI` for the full
command reference, :ref:`Docker` for running agents in sandboxed containers, and
:ref:`Caveats` for known limitations.

Alternatives
------------

TODO


Acknowledgements
----------------

TODO

Citing
------

TODO

  .. code-block:: bibtex

    @misc{ workflow,
        title={ workflow: Agentic workflow },
        author={Grönroos, Stig-Arne},
        year={ 2026 },
        note={\url{https://github.com/waino/workflow}},
    }
