========
workflow
========

.. image:: https://img.shields.io/pypi/v/workflow.svg
        :target: https://pypi.python.org/pypi/workflow

.. image:: https://readthedocs.org/projects/workflow/badge/?version=latest
        :target: https://workflow.readthedocs.io/en/latest/?version=latest
        :alt: Documentation Status


Agentic workflow

* Free software: MIT License
* Documentation: https://workflow.readthedocs.io.

Installation
------------

  .. code-block:: bash

    pip install workflow

Features
--------

* Orchestrates three AI coding agents (planner, builder, reviewer) plus a human
  operator through a pipeline of small, reviewable changes.
* Plans move through a fixed state machine (``intent`` -> ``planning`` -> ``todo``
  -> ``implementing`` -> ``checking`` -> ``done``/``rejected``), tracked as Markdown
  files under ``docs/plans/``.
* Script commands (``init``, ``todo``, ``implement``, ``check``, ``accept``, ...)
  perform the git operations needed at each handoff; prompt commands (``ask``,
  ``plan``, ``build``, ``pr``, ...) send rendered prompts to the responsible agent.
* Each agent runs in its own sandboxed git clone; only the human operator pushes to
  the shared repository.

See the full documentation for the workflow philosophy, the command reference, and
how to run agents in Docker.

Configuration
-------------

All settings (agent names, models, the tmux session, the state machine, and every
prompt's text) live in a single ``config.yaml``, resolved from an explicit
``--config`` path, ``{project_dir}/config.yaml``, or
``~/.config/workflow/config.yaml``. See ``docs/default_config.yaml`` for the
default configuration and the *Configurable vs. requiring code changes* section of
the documentation for what can be changed without a code change.
