"""Script command modules for the workflow CLI.

Each sub-module registers its commands with :func:`workflow.registry.register_script`
on import.  All modules in this package must be imported in ``workflow/cli.py``
before :func:`workflow.config.load_config` is called.
"""
