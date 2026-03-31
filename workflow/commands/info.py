"""Informational script commands: status, log, claude.md.

These are placeholder implementations — full logic will be added in later plans.
Each function is registered in the script registry on module import.
"""

from __future__ import annotations

from workflow.registry import register_script


@register_script("status")
def status() -> int:
    """Show the current state of all plans across the pipeline."""
    return 0


@register_script("log")
def log() -> int:
    """Pretty-print the audit log."""
    return 0


@register_script("claude.md")
def claude_md() -> int:
    """Regenerate the auto-generated sections of CLAUDE.md from the config."""
    return 0
