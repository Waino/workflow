"""Script registry for the workflow CLI.

Maps named script identifiers (used in the YAML config) to Python functions.
All script command modules must be imported before load_config is called so
that the registry is fully populated when WorkflowConfig.validate_script_references
runs during model construction.
"""

from __future__ import annotations

from collections.abc import Callable


class ConfigError(Exception):
    """Raised when the config is invalid (e.g. references an unknown script name)."""


ScriptFunc = Callable[..., int | None]
"""Type alias for registered script functions.

The return value is an optional exit code.  Simple stub functions return ``int``
directly; richer commands that use ``@click.pass_context`` call ``ctx.exit()``
and return ``None``.
"""

_SCRIPT_REGISTRY: dict[str, ScriptFunc] = {}


def register_script(name: str) -> Callable[[ScriptFunc], ScriptFunc]:
    """Decorator that registers a Python function as a named script.

    Usage::

        @register_script("implement")
        def implement(...) -> int:
            ...

    The *name* must match the ``script`` field of a ``ScriptCommand`` entry in
    the YAML config.  Raises ``KeyError`` if the same name is registered twice.
    """

    def decorator(func: ScriptFunc) -> ScriptFunc:
        if name in _SCRIPT_REGISTRY:
            msg = f"Script name already registered: {name!r}"
            raise KeyError(msg)
        _SCRIPT_REGISTRY[name] = func
        return func

    return decorator
