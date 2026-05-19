"""Cheatsheet script command."""

from __future__ import annotations

from itertools import groupby, zip_longest

import click
from pydantic import BaseModel, ConfigDict
from rich.console import Console, RenderableType
from rich.rule import Rule
from rich.style import Style
from rich.table import Table
from rich.text import Text

from workflow.config import WorkflowConfig
from workflow.registry import register_script


class SubColumn(BaseModel):
    """A column in the inner tables of the cheatsheet."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    header: RenderableType
    cells: list[RenderableType]

    def render(self) -> list[RenderableType]:
        """Render the post-header rule and contents of this subcolumn."""
        return [Rule(style=Style(color="grey30")), *self.cells]


class Column(BaseModel):
    """A column in the outer table of the cheatsheet."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    header: Text | str
    subcolumns: list[SubColumn]

    def render_inner_table(self) -> Table:
        """Render the inner table for this column."""
        inner_table = Table(box=None)
        for subcolumn in self.subcolumns:
            inner_table.add_column(subcolumn.header)
        for row in zip_longest(*[subcolumn.render() for subcolumn in self.subcolumns]):
            inner_table.add_row(*row)
        return inner_table

    def render_header(self) -> Text:
        """Render the header for this column."""
        text = Text(" ")
        text.append(self.header)
        text.append(" ")
        return text


class CheatsheetTable(BaseModel):
    """The cheatsheet table."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    columns: list[Column]

    def render(self) -> Table:
        """Render the cheatsheet table."""
        outer_table = Table(
            title="Command cheatsheet",
            padding=0,
            border_style=Style(color="grey30"),
        )
        for column in self.columns:
            outer_table.add_column(column.render_header())
        outer_table.add_row(*[column.render_inner_table() for column in self.columns])
        return outer_table


def _format_state(state: str) -> Text:
    if state in {"intent", "review"}:
        return Text(state, style=Style(color="steel_blue3"))
    return Text(state)


@register_script("cheat")
@click.pass_context
def cheat(ctx: click.Context) -> int:
    """Print the workflow cheat sheet."""
    config: WorkflowConfig = ctx.obj["config"]
    columns: list[Column] = []
    for agent, group in groupby(config.states.items(), key=lambda x: x[1].agent):
        columns.append(
            Column(
                header=agent or "",
                subcolumns=[
                    SubColumn(
                        header=_format_state(state),
                        cells=[command.render() for command in state_config.commands],
                    )
                    for state, state_config in group
                ],
            ),
        )
    table = CheatsheetTable(columns=columns)
    console = Console()
    console.print(table.render())
    return 0
