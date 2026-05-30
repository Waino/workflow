"""Pure generation functions for the CLAUDE.md workflow section.

All functions here are free of side effects except for I/O in
``render_workflow_section`` (reads the Jinja template from package
resources).  They are imported by ``workflow/commands/claude_md.py``
(orchestrator), ``workflow/validate.py`` (hash-file parsing), and
``workflow/commands/workflow.py`` (init bootstrap).
"""

from __future__ import annotations

import hashlib
import importlib.resources
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import jinja2
from pydantic import BaseModel

from workflow.exceptions import ClaudeMdGenerationError

if TYPE_CHECKING:
    from workflow.config import WorkflowConfig

MARKER_BEGIN = "<!-- WORKFLOW BEGIN -->"
MARKER_END = "<!-- WORKFLOW END -->"


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class ManagedSection:
    """Zero-based line indices of the BEGIN and END marker lines (inclusive)."""

    begin_line: int
    end_line: int


class HashFileV1(BaseModel):
    """Parsed representation of the v1 trusted hash file."""

    version: Literal["v1"]
    claude_md_sha: str
    config_yaml_sha: str


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


def normalise(text: str) -> str:
    """Normalise *text* to a canonical form.

    - CRLF / CR → LF line endings.
    - Strip trailing whitespace from every line.
    - Ensure exactly one trailing LF at end of file.
    - Preserve UTF-8 content (caller is responsible for encoding).
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    lines = [line.rstrip() for line in lines]
    # Drop trailing blank lines
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Marker handling
# ---------------------------------------------------------------------------


def find_managed_section(text: str) -> ManagedSection | None:
    """Locate the single managed marker pair in *text*.

    Markers are recognised **only** when the line equals exactly the marker
    string (column-0 rule, no surrounding whitespace).

    Returns ``None`` when no markers are present.
    Raises :class:`~workflow.exceptions.ClaudeMdGenerationError` for any
    other marker state (mismatched counts, wrong order, duplicates).
    """
    lines = text.split("\n")
    begins = [i for i, ln in enumerate(lines) if ln == MARKER_BEGIN]
    ends = [i for i, ln in enumerate(lines) if ln == MARKER_END]

    if not begins and not ends:
        return None

    if len(begins) != 1 or len(ends) != 1:
        msg = (
            f"Malformed markers: found {len(begins)} BEGIN and {len(ends)} END marker(s). "
            "Expected exactly one of each, or zero of both."
        )
        raise ClaudeMdGenerationError(msg)

    begin, end = begins[0], ends[0]
    if end <= begin:
        msg = f"Malformed markers: END marker (line {end}) must come after BEGIN marker (line {begin})."
        raise ClaudeMdGenerationError(msg)

    return ManagedSection(begin_line=begin, end_line=end)


def replace_managed_section(text: str, span: ManagedSection, managed: str) -> str:
    """Substitute the content between the marker lines, preserving the markers.

    *managed* is the new content (without markers).
    """
    lines = text.split("\n")
    before = lines[: span.begin_line + 1]  # up to and including BEGIN
    after = lines[span.end_line :]  # from END onward
    managed_lines = managed.rstrip("\n").split("\n") if managed.strip() else []
    return "\n".join(before + managed_lines + after)


def append_managed_section(text: str, managed: str) -> str:
    """Append a fresh marker pair plus *managed* at EOF.

    A single blank-line separator is inserted between existing content and
    the new managed block.
    """
    stripped = text.rstrip("\n")
    prefix = stripped + "\n\n" if stripped else ""
    managed_body = managed.rstrip("\n")
    return f"{prefix}{MARKER_BEGIN}\n{managed_body}\n{MARKER_END}\n"


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render_workflow_section(config: WorkflowConfig) -> str:
    """Render the managed workflow section from *config* using the Jinja2 template.

    Returns the rendered text (without markers, without trailing markers).
    The caller is responsible for splicing into the full CLAUDE.md.
    """
    template_text = (
        importlib.resources.files("workflow.templates").joinpath("claude_md_workflow.md.j2").read_text(encoding="utf-8")
    )

    # Build inbound transition index: target_state -> list of {name, source, type}
    inbound: dict[str, list[dict[str, str]]] = {str(s): [] for s in config.states}
    for source_state, state_cfg in config.states.items():
        for cmd in state_cfg.commands:
            transition = getattr(cmd, "transition", None)
            if transition is not None:
                target = str(transition)
                if target in inbound:
                    inbound[target].append({"name": cmd.name, "source": str(source_state), "type": cmd.command_type})

    states_data = []
    for state_name, state_cfg in config.states.items():
        agent_label = state_cfg.agent or "human operator"

        inbound_items = inbound.get(str(state_name), [])
        inbound_str = (
            ", ".join(f"{i['name']} from {i['source']} [{i['type']}]" for i in inbound_items)
            if inbound_items
            else "(none)"
        )

        outbound_items = [
            {"name": cmd.name, "target": str(getattr(cmd, "transition", None)), "type": cmd.command_type}
            for cmd in state_cfg.commands
            if getattr(cmd, "transition", None) is not None
        ]
        outbound_str = (
            ", ".join(f"{o['name']} -> {o['target']} [{o['type']}]" for o in outbound_items)
            if outbound_items
            else "(none)"
        )

        commands = [
            {
                "type": cmd.command_type,
                "name": cmd.name,
                "transition": str(getattr(cmd, "transition", None)) if getattr(cmd, "transition", None) else None,
                "description": cmd.description,
            }
            for cmd in state_cfg.commands
        ]

        states_data.append(
            {
                "name": str(state_name),
                "agent": agent_label,
                "description": state_cfg.description,
                "inbound_str": inbound_str,
                "outbound_str": outbound_str,
                "commands": commands,
            },
        )

    env = jinja2.Environment(
        loader=jinja2.BaseLoader(),
        autoescape=False,  # noqa: S701 — rendering Markdown, not HTML; XSS is not a concern
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    template = env.from_string(template_text)
    return normalise(template.render(states=states_data))


# ---------------------------------------------------------------------------
# Hash file
# ---------------------------------------------------------------------------


def compute_hash_file_contents(claude_md_text: str, config_yaml_bytes: bytes) -> str:
    r"""Return the ``v1 <sha> <sha>\n`` hash file contents.

    *claude_md_text* must already be normalised before calling.
    """
    claude_md_sha = hashlib.sha256(claude_md_text.encode("utf-8")).hexdigest()
    config_yaml_sha = hashlib.sha256(config_yaml_bytes).hexdigest()
    return f"v1 {claude_md_sha} {config_yaml_sha}\n"


def parse_hash_file(text: str) -> HashFileV1:
    """Parse the hash file *text* and return a :class:`HashFileV1`.

    Raises :class:`~workflow.exceptions.ClaudeMdGenerationError` on malformed
    input or an unsupported version tag.
    """
    tokens = text.strip().split()
    if len(tokens) != 3:  # noqa: PLR2004
        msg = f"Malformed hash file: expected 3 space-separated tokens, got {len(tokens)}."
        raise ClaudeMdGenerationError(msg)
    version, claude_md_sha, config_yaml_sha = tokens
    if version != "v1":
        msg = f"Unsupported hash file version: {version!r} (expected 'v1')."
        raise ClaudeMdGenerationError(msg)
    return HashFileV1(version="v1", claude_md_sha=claude_md_sha, config_yaml_sha=config_yaml_sha)
